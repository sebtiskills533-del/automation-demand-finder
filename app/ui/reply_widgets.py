"""One reply someone sent the user, and the email draft for it. Used on the
Replies page and in each post's Replies tab."""

from __future__ import annotations

import logging
import sqlite3

import streamlit as st

from app import store
from app.cli import build_llm
from app.config import Settings
from app.domain import MAX_REPLY_CHARS, PLATFORM_NAMES
from app.emails import draft_email, mailto_link
from app.errors import AppError
from app.models_registry import ModelRegistry
from app.ui.components import md_escape, writing_style_picker

log = logging.getLogger(__name__)


def reply_card(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, reply: dict) -> None:
    """Show a reply (marking it as seen) with its email tools."""
    if not reply["seen"]:
        store.mark_replies_seen(conn, [reply["id"]])
    with st.container(border=True):
        when = (reply["replied_at"] or reply["received_at"])[:10]
        platform = PLATFORM_NAMES.get(reply["platform"], reply["platform"])
        st.markdown(f"#### 💬 {md_escape(reply['author'] or 'Someone')} replied on {platform}")
        st.caption(f"{when}" + (f" · about: {md_escape(reply['post_title'])}" if reply.get("post_title") else ""))
        st.text(reply["text"])  # their words, shown literally
        st.link_button(f"Open the conversation on {platform} ↗", reply["url"])
        st.divider()
        _email_section(conn, settings, registry, reply)


def _email_section(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, reply: dict) -> None:
    if not reply["email"]:
        st.caption("✉️ They didn't share an email in this reply — answer them on the platform instead.")
        return
    st.caption("✉️ Email they shared with you")
    st.code(reply["email"], language=None)
    model = registry.model_for_task(conn, "email")
    style = writing_style_picker(conn, f"email_{reply['id']}")
    has_draft = bool(reply["email_body"])
    if st.button("✨ Write a new email draft" if has_draft else "✨ Draft an email with Claude",
                 type="secondary" if has_draft else "primary", key=f"email_draft_{reply['id']}",
                 help=f"Uses {model.name}. You review it and send it yourself."):
        try:
            with st.spinner("Claude is writing the email..."):
                draft_email(conn, build_llm(settings, conn), registry, settings, reply["id"], style)
            st.rerun()
        except (AppError, ValueError) as exc:
            log.warning("Email draft failed: %s", exc)
            st.error(str(exc))
    if not has_draft:
        return

    subject = st.text_input("Subject", reply["email_subject"], max_chars=200, key=f"subj_{reply['id']}")
    body = st.text_area("Email", reply["email_body"], height=220, max_chars=MAX_REPLY_CHARS,
                        key=f"body_{reply['id']}")
    c1, c2 = st.columns([1, 2])
    if c1.button("💾 Save edits", key=f"save_email_{reply['id']}"):
        try:
            store.save_email_draft(conn, reply["id"], subject, body)
            st.toast("Email saved", icon="✅")
        except ValueError as exc:
            st.error(str(exc))
    try:
        c2.link_button("📧 Open in my email app", mailto_link(reply["email"], subject, body),
                       help="Opens your email app with this draft. Nothing is sent until you press Send there.")
    except ValueError as exc:
        c2.error(str(exc))
    st.caption("Or copy the email text:")
    st.code(body, language=None, wrap_lines=True)
