"""One reply someone sent the user, and the email draft for it. Used on the
Replies page and in each post's Replies tab."""

from __future__ import annotations

import hashlib
import logging
import sqlite3

import streamlit as st

from app import store
from app.cli import build_llm
from app.config import Settings
from app.domain import MAX_REPLY_CHARS, PLATFORM_NAMES
from app.emails import draft_email, mailto_link
from app.models_registry import ModelRegistry
from app.ui.components import md_escape
from app.ui.draft_compare import compare_drafts

log = logging.getLogger(__name__)


def reply_card(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, reply: dict) -> None:
    """Show a reply with a "seen" tick (only the user ticks it) and its email tools."""
    with st.container(border=True):
        when = (reply["replied_at"] or reply["received_at"])[:10]
        platform = PLATFORM_NAMES.get(reply["platform"], reply["platform"])
        st.markdown(f"#### 💬 {md_escape(reply['author'] or 'Someone')} replied on {platform}")
        st.caption(f"{when}" + (f" · about: {md_escape(reply['post_title'])}" if reply.get("post_title") else ""))
        st.text(reply["text"])  # their words, shown literally
        c1, c2 = st.columns([1.4, 1])
        c1.link_button(f"Open the conversation on {platform} ↗", reply["url"])
        seen_tick(conn, reply, c2)
        st.divider()
        _email_section(conn, settings, registry, reply)


def seen_tick(conn: sqlite3.Connection, reply: dict, where) -> None:
    """"I've seen this reply" tick. Unticked replies count as new."""
    seen = bool(reply["seen"])
    ticked = where.checkbox("✅ Dekh liya (seen)", value=seen, key=f"seen_{reply['id']}",
                            help="Tick when you've read it. Unticked replies stay in 🆕 Not seen yet.")
    if ticked != seen:
        store.mark_replies_seen(conn, [reply["id"]], ticked)
        st.rerun()


def _email_section(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, reply: dict) -> None:
    if not reply["email"]:
        st.caption("✉️ They didn't share an email in this reply — answer them on the platform instead.")
        return
    st.caption("✉️ Email they shared with you")
    st.code(reply["email"], language=None)
    model = registry.model_for_task(conn, "email")
    st.caption(f"Claude writes the email in each style so you can compare and pick one. Uses {model.name}. "
               "You review it and send it yourself.")
    compare_drafts(
        conn, kind="email", owner_id=str(reply["id"]), chosen_body=reply["email_body"],
        key=f"email_{reply['id']}", with_subject=True,
        make_draft=lambda style: draft_email(conn, build_llm(settings, conn), registry, settings, reply["id"], style),
        choose=lambda draft: store.save_email_draft(conn, reply["id"], draft["subject"], draft["body"]),
    )
    if not reply["email_body"]:
        return

    st.markdown("**✏️ Your chosen email** (edit freely, then open it in your email app)")
    # The keys follow the chosen text, so choosing another draft refreshes these boxes.
    version = hashlib.sha1(reply["email_body"].encode("utf-8")).hexdigest()[:8]
    subject = st.text_input("Subject", reply["email_subject"], max_chars=200, key=f"subj_{reply['id']}_{version}")
    body = st.text_area("Email", reply["email_body"], height=220, max_chars=MAX_REPLY_CHARS,
                        key=f"body_{reply['id']}_{version}")
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
