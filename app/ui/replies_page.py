"""🔔 Replies: answers people sent to the user's posts and comments."""

from __future__ import annotations

import hashlib
import logging
import sqlite3

import pandas as pd
import streamlit as st

from app import store
from app.config import Settings
from app.domain import PLATFORM_NAMES
from app.errors import AppError
from app.inbox import add_manual_reply, ingest_replies
from app.models_registry import ModelRegistry
from app.muse.job import DEFAULT_REPLY_DAYS
from app.muse.parse import MAX_REPLY_ROWS, REPLIES, parse_block
from app.ui.background import start_muse_process
from app.ui.components import empty_state, page_header
from app.ui.reply_widgets import reply_card

log = logging.getLogger(__name__)


VIEWS = {"🆕 Not seen yet": False, "📬 All": None, "✅ Seen": True}


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header(
        "🔔 Replies",
        "Every reply people sent you, from every post you answered. Tick the ones you've seen. "
        "An email is kept only when the person wrote it in their reply to you.",
    )
    _get_replies(conn, settings)
    all_tab, posts_tab = st.tabs(["💬 All replies", "📝 Posts I answered"], key="replies_tabs")
    with all_tab:
        _all_replies(conn, settings, registry)
    with posts_tab:
        _answered_posts(conn)


def _all_replies(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    c1, c2 = st.columns([1.3, 1])
    view = c1.segmented_control("Show", list(VIEWS), default="🆕 Not seen yet", key="replies_view") or "📬 All"
    platforms = c2.multiselect("Platform", sorted(PLATFORM_NAMES), format_func=PLATFORM_NAMES.get,
                               key="replies_platforms", placeholder="All platforms")
    replies = store.list_replies(conn, seen=VIEWS[view], platforms=tuple(platforms), limit=500)
    if not replies:
        empty_state(
            "All caught up 🎉" if VIEWS[view] is False else "No replies here yet",
            "Check for replies with Muse above, or add one you received by hand.",
        )
        return

    if VIEWS[view] is False and st.button(f"✅ Mark all {len(replies)} as seen", key="seen_all"):
        store.mark_replies_seen(conn, [r["id"] for r in replies])
        st.rerun()

    table = pd.DataFrame([
        {
            "Seen": bool(r["seen"]),
            "When": (r["replied_at"] or r["received_at"])[:10],
            "Platform": PLATFORM_NAMES.get(r["platform"], r["platform"]),
            "From": r["author"] or "Someone",
            "Reply": r["text"][:120],
            "Email": "✉️" if r["email"] else "",
            "About post": r["post_title"] or "",
            "Link": r["url"],
        }
        for r in replies
    ])
    # The key follows the rows and their ticks, so a saved tick never lands on another row.
    version = hashlib.sha1(repr([(r["id"], r["seen"]) for r in replies]).encode()).hexdigest()[:10]
    edited = st.data_editor(
        table, hide_index=True, width="stretch", key=f"replies_{version}",
        disabled=[c for c in table.columns if c != "Seen"],
        column_config={
            "Seen": st.column_config.CheckboxColumn("✅ Seen", width=70, help="Tick the replies you've read"),
            "Reply": st.column_config.TextColumn("Reply", width="large"),
            "Email": st.column_config.TextColumn("Email", width=60, help="✉️ = they shared an email"),
            "Link": st.column_config.LinkColumn("Link", display_text="Open ↗", width=75),
        },
    )
    changed = [(r["id"], bool(new)) for r, new in zip(replies, edited["Seen"]) if bool(new) != bool(r["seen"])]
    if changed:
        for seen in (True, False):
            store.mark_replies_seen(conn, [rid for rid, value in changed if value is seen], seen)
        st.rerun()

    labels = {r["id"]: f"{'🆕 ' if not r['seen'] else ''}{r['author'] or 'Someone'} · "
                       f"{(r['replied_at'] or r['received_at'])[:10]} · {r['text'][:60]}" for r in replies}
    chosen = st.selectbox("📖 Open a reply to read it and draft an email", list(labels),
                          format_func=labels.get, key=f"open_reply_{view}")
    reply = next(r for r in replies if r["id"] == chosen)
    reply_card(conn, settings, registry, reply)


def _answered_posts(conn: sqlite3.Connection) -> None:
    posts = store.answered_posts(conn)
    if not posts:
        empty_state("No answered posts yet",
                    "Tick ✅ 'Maine jawab bhej diya' on a post after you answer it. "
                    "Posts that get a reply are added automatically.")
        return
    st.caption(f"{len(posts)} posts you answered. Their replies are in 💬 All replies; "
               "open a post on 🎯 Opportunities to see its replies in the 💬 Replies tab.")
    st.dataframe(
        pd.DataFrame([
            {
                "Post": p["title"],
                "Platform": PLATFORM_NAMES.get(p["platform"], p["platform"]),
                "Answered": (p["answered_at"] or "")[:10],
                "Replies": p["replies"],
                "New": f"🆕 {p['unseen']}" if p["unseen"] else "",
                "Last reply": (p["last_reply"] or "")[:10],
                "Link": p["url"],
            }
            for p in posts
        ]),
        hide_index=True, width="stretch",
        column_config={
            "Post": st.column_config.TextColumn("Post", width="large"),
            "Replies": st.column_config.NumberColumn("Replies", width=70),
            "Link": st.column_config.LinkColumn("Link", display_text="Open ↗", width=75),
        },
    )


def _get_replies(conn: sqlite3.Connection, settings: Settings) -> None:
    with st.container(border=True):
        st.markdown("#### 📥 Get replies")
        if settings.muse.enabled:
            c1, c2, c3 = st.columns([1, 1.3, 1.3])
            days = c1.number_input("Days back", 1, 60, DEFAULT_REPLY_DAYS, key="reply_days")
            if c2.button("🔍 Check for replies with Muse", type="primary", width="stretch",
                         help="Muse reads your Facebook, Instagram and Quora notifications (read only)."):
                _start(["check-replies", "--days", str(int(days))],
                       "Muse is checking your notifications. Come back in a few minutes and refresh.")
            if c3.button("📥 Import Muse's last replies list", width="stretch",
                         help="Saves the newest REPLIES list from the Muse chat without asking again."):
                _start(["import-last-replies"], "Reading Muse's last list. Refresh in about 30 seconds.")
            if st.button("🔄 Refresh"):
                st.rerun()
        else:
            st.caption("Turn on Muse (MUSE_ENABLED=true in .env) to check your notifications automatically.")

        with st.expander("✍️ Add a reply you received (copy it in)"):
            with st.form("manual_reply", clear_on_submit=True):
                url = st.text_input("Link to the conversation (https://...)", max_chars=2000)
                name = st.text_input("Their name", max_chars=80)
                text = st.text_area("Their reply (an email they wrote here is saved)", max_chars=4000)
                if st.form_submit_button("Add reply", type="primary"):
                    _show(add_manual_reply(conn, url, name, text))
        with st.expander("📋 Paste a REPLIES list from Muse"):
            pasted = st.text_area("Muse's whole answer (REPLIES START ... REPLIES END)", height=150,
                                  max_chars=200_000, key="paste_replies")
            if st.button("Import replies", key="import_pasted_replies"):
                try:
                    _show(ingest_replies(conn, parse_block(pasted, REPLIES, MAX_REPLY_ROWS).rows))
                except AppError as exc:
                    st.error(str(exc))


def _start(muse_args: list[str], message: str) -> None:
    try:
        start_muse_process(muse_args)
        st.success(message)
    except OSError as exc:
        log.warning("Could not start Muse: %s", exc)
        st.error(f"Could not start: {exc}")


def _show(report) -> None:
    if report.errors:
        st.warning(f"{report.summary}.")
        st.text("\n".join(report.errors[:20]))
    elif report.added:
        st.success(f"{report.summary}.")
    else:
        st.info(f"Nothing new: {report.summary}.")
