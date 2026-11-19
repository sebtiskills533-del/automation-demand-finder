"""The password screen shown before anything else."""

from __future__ import annotations

import sqlite3

import streamlit as st

from app.auth import LOCKOUT_WINDOW, LoginResult, attempt_login, session_expired
from app.config import Settings
from app.domain import utc_now

_SESSION_KEY = "logged_in_at"


def require_login(conn: sqlite3.Connection, settings: Settings) -> bool:
    """Show the login form unless this browser session is already logged in."""
    if not settings.dashboard_password_hash:
        st.error(
            "No dashboard password is set yet. In the project folder run:\n\n"
            "`.venv\\Scripts\\python -m app.set_password`\n\nthen reload this page."
        )
        return False

    logged_in_at = st.session_state.get(_SESSION_KEY)
    if logged_in_at is not None and not session_expired(logged_in_at, utc_now()):
        return True
    if logged_in_at is not None:
        st.session_state.pop(_SESSION_KEY, None)
        st.info("Your session expired. Please log in again.")

    _, middle, _ = st.columns([1, 1.3, 1])
    with middle:
        st.write("")
        st.markdown("## 🔎 Automation Demand Finder")
        st.caption("Find people who need small automations, then help them.")
        with st.form("login", clear_on_submit=True, border=True):
            password = st.text_input("Password", type="password", max_chars=1024)
            submitted = st.form_submit_button("Log in", type="primary", width="stretch")
    if not submitted:
        return False

    result = attempt_login(conn, password, settings.dashboard_password_hash, utc_now())
    if result is LoginResult.OK:
        st.session_state[_SESSION_KEY] = utc_now()
        st.rerun()
    with middle:
        if result is LoginResult.LOCKED_OUT:
            minutes = int(LOCKOUT_WINDOW.total_seconds() // 60)
            st.error(f"Too many wrong passwords. Try again in {minutes} minutes.")
        else:
            st.error("Wrong password.")
    return False


def logout_button() -> None:
    """At the bottom of the sidebar, under the page's own filters."""
    with st.sidebar:
        st.write("")
        if st.button("🚪 Log out", width="stretch"):
            st.session_state.pop(_SESSION_KEY, None)
            st.rerun()
