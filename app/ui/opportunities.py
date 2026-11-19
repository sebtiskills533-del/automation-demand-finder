"""Main page: ranked opportunities. Click a row to see it, reply, report and track it."""

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from app import db, store
from app.budget import month_start
from app.cli import build_llm
from app.config import Settings
from app.domain import (
    DIFFICULTIES,
    MAX_NOTES_CHARS,
    MAX_REPLY_CHARS,
    PATHS,
    PLATFORM_NAMES,
    STATUSES,
    TICKED_STATUSES,
    utc_now,
)
from app.errors import AppError
from app.models_registry import ModelRegistry
from app.replies import draft_reply
from app.reports import make_report
from app.store import DashboardFilters
from app.timeline import delivery_label
from app.ui.components import (
    REPORT_STYLE,
    STATUS_ICONS,
    empty_state,
    labelled_text,
    md_escape,
    page_header,
    verdict_badge,
)
from app.ui.draft_compare import compare_drafts
from app.ui.reply_widgets import reply_card
from app.ui.table import build_table, stars

log = logging.getLogger(__name__)

PAGE_SIZE = 50
BEST_SCORE = 45

# Quick views: (statuses to show, minimum score)
QUICK_VIEWS = {
    "⭐ Best leads": (("New", "Ticked"), BEST_SCORE),
    "🛠️ In progress": (("Ticked", "Building"), 0),
    "📋 All": ((), 0),
    "💰 Done & sold": (("Done", "Sold"), 0),
    "🚫 Skipped": (("Skipped",), 0),
}


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header("🎯 Opportunities", "Best posts first. Click a row to read it, draft a reply and track it.")
    _summary(conn, settings)

    view = st.segmented_control("Show", list(QUICK_VIEWS), default="⭐ Best leads", key="quick_view")
    view = view or "⭐ Best leads"
    statuses, min_score = QUICK_VIEWS[view]
    filters = _sidebar_filters(settings.db_path, statuses, min_score)

    total = store.count_dashboard_rows(conn, filters)
    if total == 0:
        empty_state(
            "Nothing here yet",
            "Try another view above, loosen the sidebar filters, or run the daily job "
            "(desktop icon) to collect new posts.",
        )
        return

    pages = math.ceil(total / PAGE_SIZE)
    page = 1
    if pages > 1:
        page = st.number_input(f"Page (1-{pages})", 1, pages, 1, key="page")
    rows = store.get_dashboard_rows(conn, filters, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    st.caption(f"{total} posts · page {page} of {pages}")

    selected = _table(rows, key=f"table_{hash((view, filters, page))}")
    st.write("")
    _details(conn, settings, registry, selected["id"])


# ------------------------------------------------------------------ top


def _summary(conn: sqlite3.Connection, settings: Settings) -> None:
    counts = store.dashboard_counts(conn, BEST_SCORE, utc_now() - timedelta(days=1))
    spent = store.spend_since(conn, month_start(utc_now()))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⭐ Best leads (score 45+)", counts["best"])
    c2.metric("🛠️ In progress", counts["active"])
    c3.metric("🆕 Rated in last 24 h", counts["new_today"])
    limit = settings.monthly_budget_usd * settings.usd_to_inr
    c4.metric("💳 AI spend this month", f"Rs {spent * settings.usd_to_inr:.0f} / {limit:.0f}",
              help="Spent so far this month / your monthly limit (MONTHLY_BUDGET_USD in .env).")


@st.cache_data(ttl=300, show_spinner=False)
def _sources(db_path: str) -> list[str]:
    """Source names change rarely, so cache them for 5 minutes."""
    with db.open_db(Path(db_path)) as conn:
        return store.distinct_sources(conn)


def _sidebar_filters(db_path: Path, statuses: tuple[str, ...], min_score: int) -> DashboardFilters:
    with st.sidebar:
        st.markdown("### 🔍 Filters")
        search = st.text_input("Search", max_chars=100, placeholder="e.g. Google Sheets, Etsy")
        with st.expander("More filters"):
            min_score = st.slider("Minimum score", 0, 100, min_score, step=5, key=f"min_score_{min_score}")
            min_seriousness = st.slider("Minimum seriousness", 1, 5, 1)
            difficulties = st.multiselect("Difficulty", DIFFICULTIES)
            sources = st.multiselect("Source", _sources(str(db_path)))
            buildable_only = st.checkbox("Only jobs I can build")
    return DashboardFilters(
        difficulties=tuple(difficulties),
        sources=tuple(sources),
        statuses=statuses,
        min_score=min_score,
        min_seriousness=min_seriousness,
        buildable_only=buildable_only,
        search=search,
    )


def _table(rows: list[dict], key: str) -> dict:
    """The ranked table; returns the selected row (the first one if none is selected)."""
    table = build_table(rows)
    cc = st.column_config
    event = st.dataframe(
        table,
        key=key,
        hide_index=True,
        width="stretch",
        height=min(40 + 35 * len(table), 460),
        on_select="rerun",
        selection_mode="single-row",
        column_order=["Verdict", "Score", "Title", "Delivery", "Source", "Status", "Post"],
        column_config={
            "Verdict": cc.TextColumn("Verdict", width=135),
            "Score": cc.ProgressColumn("Score", min_value=0, max_value=100, format="%d", width=85),
            "Title": cc.TextColumn("Title", width=330),
            "Delivery": cc.TextColumn("Delivery", width=95, help="Days to promise (includes a safety margin)"),
            "Seriousness": cc.TextColumn("Serious", width=85, help="How real and committed the person seems"),
            "Source": cc.TextColumn("Source", width=135),
            "Status": cc.TextColumn("Status", width=100),
            "Post": cc.LinkColumn("Link", display_text="Open ↗", width=75),
        },
    )
    chosen = event.selection.rows if event and event.selection else []
    index = chosen[0] if chosen and chosen[0] < len(rows) else 0
    if not chosen:
        st.caption("👆 Click any row to open it. Showing the top post below.")
    return rows[index]


# -------------------------------------------------------------- details


def _details(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, post_id: str) -> None:
    post = store.get_post_details(conn, post_id)
    if post is None:
        return
    with st.container(border=True):
        # Titles come from strangers: escaped so links/images/formatting can't appear.
        st.markdown(f"### {md_escape(post['title'])}")
        # Source names can come from imports/Muse, so they are escaped too.
        st.caption(f"{md_escape(post['source'])} · posted {post['posted_at'][:10]} · "
                   f"by {md_escape(post['author_handle'] or 'unknown')}")
        _badges(post)
        st.link_button(f"Open the original post on {PLATFORM_NAMES[post['platform']]} ↗", post["url"])

        new_replies = store.unseen_reply_count(conn, post_id)
        replies_label = f"💬 Replies ({new_replies} new)" if new_replies else "💬 Replies"
        overview, reply, report, track, answers = st.tabs(
            ["📄 Overview", "✍️ Reply", "📋 Decision report", "🗂️ Track", replies_label], key=f"tabs_{post_id}"
        )
        with overview:
            _overview(post)
        with reply:
            _reply_tab(conn, settings, registry, post)
        with report:
            _report_tab(conn, settings, registry, post)
        with track:
            _track_tab(conn, post)
        with answers:
            _answers_tab(conn, settings, registry, post_id)


def _badges(post: dict) -> None:
    with st.container(horizontal=True, gap="small"):
        verdict_badge(post["opportunity_score"])
        st.badge(delivery_label(post["delivery_min_days"], post["delivery_max_days"],
                                post["difficulty"], post["est_hours"]),
                 icon=":material/schedule:", color="blue")
        st.badge(f"{post['difficulty'].capitalize()} · {post['est_hours']:g} h",
                 icon=":material/build:", color="gray")
        st.badge(f"Serious {stars(post['seriousness'])}", icon=":material/person:", color="violet")
        st.badge(f"{STATUS_ICONS.get(post['status'], '')} {post['status']}", color="primary")


def _overview(post: dict) -> None:
    left, right = st.columns(2)
    with left:
        labelled_text("What they need", post["summary"])
        labelled_text("Why this seriousness rating", post["seriousness_reason"])
        labelled_text("Difficulty reasoning", post["reason"])
    with right:
        labelled_text("Best tool", post["tool"])
        labelled_text("How to contact them (the allowed way)", post["contact_method"])
        labelled_text("Region (only if stated in the post)", post["region_hint"])
        labelled_text("Product potential", f"{stars(post['product_potential'])}  ({post['product_potential']}/5)")
    with st.expander("Read the full post"):
        st.text(f"{post['title']}\n\n{post['body'] or '(no text)'}")


def _reply_tab(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, post: dict) -> None:
    model = registry.model_for_task(conn, "reply")
    st.caption(f"Claude writes the reply in each style so you can compare and pick one. You edit it and "
               f"post it yourself — nothing is ever posted for you. Uses {model.name}.")
    compare_drafts(
        conn, kind="reply", owner_id=post["id"], chosen_body=post["reply_draft"], key=f"reply_{post['id']}",
        make_draft=lambda style: draft_reply(conn, build_llm(settings, conn), registry, settings, post["id"], style),
        choose=lambda draft: store.update_action(conn, post["id"], reply_draft=draft["body"]),
    )
    if not post["reply_draft"]:
        return

    st.markdown("**✏️ Your chosen reply** (edit freely, then copy)")
    # The key follows the chosen text, so choosing another draft refreshes this box.
    edited = st.text_area("Your reply (edit freely)", post["reply_draft"], height=170, label_visibility="collapsed",
                          max_chars=MAX_REPLY_CHARS, key=f"reply_{post['id']}_{_short_hash(post['reply_draft'])}")
    c1, c2 = st.columns([1, 3])
    if c1.button("💾 Save edits", key=f"save_reply_{post['id']}") and edited != post["reply_draft"]:
        store.update_action(conn, post["id"], reply_draft=edited)
        st.toast("Reply saved", icon="✅")
    c2.caption(f"{len(edited.split())} words. Copy it with the button in the box below.")
    st.code(edited, language=None, wrap_lines=True)


def _report_tab(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, post: dict) -> None:
    if post["status"] not in TICKED_STATUSES:
        st.info("Tick this post first (in the 🗂️ Track tab) to make a Decision Report. "
                "Reports are only made for posts you chose, which keeps costs low.")
        return
    report = store.get_decision_report(conn, post["id"])
    model = registry.model_for_task(conn, "report")
    label = "🔄 Make a new report" if report else "📋 Make Decision Report"
    if st.button(label, type="secondary" if report else "primary", key=f"report_{post['id']}"):
        if _run_ai(lambda: make_report(conn, build_llm(settings, conn), registry, settings, post["id"]),
                   "Claude is preparing the report..."):
            st.rerun()
    st.caption(f"Should you do it, the steps, the delivery time and how to reach them. Uses {model.name}.")
    if report:
        _report_card(post, report)


def _report_card(post: dict, report: dict) -> None:
    color, emoji = REPORT_STYLE[report["verdict"]]
    st.badge(f"{emoji} {report['verdict']}", color=color)
    c1, c2, c3 = st.columns(3)
    c1.metric("Build + test", f"{report['build_hours']:g} h")
    c2.metric("Promise to deliver", delivery_label(report["delivery_min_days"], report["delivery_max_days"],
                                                   post["difficulty"], report["build_hours"]))
    c3.metric("Steps", len(report["plan"]))
    st.markdown("**Why**")
    st.markdown("\n".join(f"- {md_escape(reason)}" for reason in report["reasons"]))
    labelled_text("How to contact them", report["contact_method"])
    with st.expander("🧭 A-to-Z plan"):
        st.dataframe(
            pd.DataFrame([{"#": n, "Step": s["title"], "Hours": s["hours"]}
                          for n, s in enumerate(report["plan"], start=1)]),
            hide_index=True, width="stretch",
        )
    st.caption("Ready draft (copy with the button in the box)")
    st.code(report["draft"], language=None, wrap_lines=True)


def _track_tab(conn: sqlite3.Connection, post: dict) -> None:
    status = st.segmented_control(
        "Status", list(STATUSES), default=post["status"], key=f"status_{post['id']}",
        format_func=lambda s: f"{STATUS_ICONS[s]} {s}",
    )
    if status and status != post["status"]:
        store.update_action(conn, post["id"], status=status)
        st.toast(f"Status: {status}", icon="✅")
        st.rerun()

    path = st.segmented_control("What will you do with it?", list(PATHS),
                                default=post["path"], key=f"path_{post['id']}")
    if path != post["path"]:
        store.update_action(conn, post["id"], path=path)
        st.toast("Saved", icon="✅")

    notes = st.text_area("Notes", post["notes"], max_chars=MAX_NOTES_CHARS, key=f"notes_{post['id']}",
                         placeholder="e.g. Replied on 20 Nov, waiting for answer")
    if st.button("💾 Save notes", key=f"save_notes_{post['id']}") and notes != post["notes"]:
        store.update_action(conn, post["id"], notes=notes)
        st.toast("Notes saved", icon="✅")


def _answers_tab(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, post_id: str) -> None:
    replies = store.list_replies(conn, post_id=post_id)
    if not replies:
        st.info("No replies to this post yet. Check for replies on the 🔔 Replies page.")
        return
    for reply in replies:
        reply_card(conn, settings, registry, reply)


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _run_ai(action, spinner: str) -> bool:
    """Run a Claude action with a spinner; show a friendly error instead of crashing."""
    try:
        with st.spinner(spinner):
            action()
        return True
    except (AppError, ValueError) as exc:
        log.warning("Dashboard AI action failed: %s", exc)
        st.error(str(exc))
        return False
