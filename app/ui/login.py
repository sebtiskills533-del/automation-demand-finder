"""The password screen, plus "remember me" so a page refresh doesn't ask again."""

from __future__ import annotations

import sqlite3

import streamlit as st

from app.auth import (
    COOKIE_NAME,
    LOCKOUT_WINDOW,
    REMEMBER_FOR,
    LoginResult,
    attempt_login,
    check_remembered_session,
    create_remembered_session,
    end_remembered_session,
    session_expired,
)
from app.config import Settings
from app.domain import utc_now

_SESSION_KEY = "logged_in_at"
_PENDING_COOKIE = "set_session_cookie"  # token to write into the browser on the next run
_CONN_KEY = "login_conn_token"          # the token this browser session uses


def require_login(conn: sqlite3.Connection, settings: Settings) -> bool:
    """True if this browser is logged in (this session, or a remembered cookie)."""
    if not settings.dashboard_password_hash:
        st.error(
            "No dashboard password is set yet. In the project folder run:\n\n"
            "`.venv\\Scripts\\python -m app.set_password`\n\nthen reload this page."
        )
        return False

    now = utc_now()
    logged_in_at = st.session_state.get(_SESSION_KEY)
    if logged_in_at is not None and not session_expired(logged_in_at, now):
        _write_pending_cookie()
        return True

    # A refresh starts a new session: accept a valid "remember me" cookie instead of the form.
    token = st.context.cookies.get(COOKIE_NAME)
    if check_remembered_session(conn, token, settings.dashboard_password_hash, now):
        st.session_state[_SESSION_KEY] = now
        st.session_state[_CONN_KEY] = token
        return True
    if logged_in_at is not None:
        st.session_state.pop(_SESSION_KEY, None)
        st.info("Your session expired. Please log in again.")

    _, middle, _ = st.columns([1, 1.3, 1])
    with middle:
        st.write("")
        st.markdown("## 🔎 Automation Demand Finder")
        st.caption("Find people who need help, then help them.")
        with st.form("login", clear_on_submit=True, border=True):
            password = st.text_input("Password", type="password", max_chars=1024)
            remember = st.checkbox(f"Keep me logged in on this computer ({REMEMBER_FOR.days} days)", value=True)
            submitted = st.form_submit_button("Log in", type="primary", width="stretch")
    if not submitted:
        return False

    result = attempt_login(conn, password, settings.dashboard_password_hash, now)
    if result is LoginResult.OK:
        st.session_state[_SESSION_KEY] = now
        if remember:
            token = create_remembered_session(conn, settings.dashboard_password_hash, now)
            st.session_state[_PENDING_COOKIE] = token
            st.session_state[_CONN_KEY] = token
        st.rerun()
    with middle:
        if result is LoginResult.LOCKED_OUT:
            minutes = int(LOCKOUT_WINDOW.total_seconds() // 60)
            st.error(f"Too many wrong passwords. Try again in {minutes} minutes.")
        else:
            st.error("Wrong password.")
    return False


def logout_button(conn: sqlite3.Connection) -> None:
    """At the bottom of the sidebar, under the page's own filters."""
    with st.sidebar:
        st.write("")
        if st.button("🚪 Log out", width="stretch"):
            end_remembered_session(conn, st.session_state.pop(_CONN_KEY, None) or st.context.cookies.get(COOKIE_NAME))
            st.session_state.pop(_SESSION_KEY, None)
            _set_cookie("", max_age=0)  # forget the cookie in this browser
            st.rerun()


def _write_pending_cookie() -> None:
    token = st.session_state.pop(_PENDING_COOKIE, None)
    if token:
        _set_cookie(token, max_age=int(REMEMBER_FOR.total_seconds()))


def _set_cookie(token: str, max_age: int) -> None:
    """Our own fixed script; the token is url-safe base64 only (checked when read back)."""
    if token and not all(c.isalnum() or c in "-_" for c in token):
        return
    st.html(
        f"<script>document.cookie = '{COOKIE_NAME}={token}; Max-Age={max_age}; Path=/; SameSite=Strict';</script>",
        unsafe_allow_javascript=True,
    )
