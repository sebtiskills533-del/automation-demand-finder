"""Main page: ranked opportunities with filters, tick boxes and status tracking."""

from __future__ import annotations

import logging
import math
import sqlite3
from pathlib import Path

import streamlit as st

from app import db, store
from app.domain import DIFFICULTIES, MAX_NOTES_CHARS, PATHS, PLATFORM_NAMES, STATUSES
from app.scoring import opportunity_label
from app.store import DashboardFilters
from app.timeline import delivery_label
from app.ui.table import EDITABLE_COLUMNS, build_table, edits_to_changes, stars

log = logging.getLogger(__name__)

PAGE_SIZE = 50
_EDITOR_VERSION = "editor_version"


def render(conn: sqlite3.Connection, db_path: Path) -> None:
    st.title("Opportunities")
    _show_totals(conn)

    filters = _sidebar_filters(db_path)
    total = store.count_dashboard_rows(conn, filters)
    if total == 0:
        st.info(
            "No analysed posts match. Run the daily job "
            "(`.venv\\Scripts\\python run_daily.py`) or loosen the filters in the sidebar."
        )
        return

    pages = math.ceil(total / PAGE_SIZE)
    page = st.number_input(f"Page (of {pages})", 1, pages, 1) if pages > 1 else 1
    rows = store.get_dashboard_rows(conn, filters, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    st.caption(f"{total} matching posts, best first. Tick the ones worth acting on; changes save instantly.")

    table = build_table(rows)
    editor_key = f"opportunities_{st.session_state.get(_EDITOR_VERSION, 0)}"
    st.data_editor(
        table,
        key=editor_key,
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(table), 700),
        disabled=[c for c in table.columns if c not in EDITABLE_COLUMNS],
        column_config=_column_config(),
        on_change=_save_edits,
        args=(db_path, editor_key, list(table.index), list(table["Status"])),
    )
    _details(rows)


def _show_totals(conn: sqlite3.Connection) -> None:
    totals = store.count_posts(conn)
    c1, c2, c3 = st.columns(3)
    c1.metric("Posts collected", totals["collected"])
    c2.metric("Looked like requests", totals["passed"])
    c3.metric("Analysed by Claude", totals["analyzed"])


@st.cache_data(ttl=300, show_spinner=False)
def _sources(db_path: str) -> list[str]:
    """Source names change rarely, so cache them for 5 minutes."""
    with db.open_db(Path(db_path)) as conn:
        return store.distinct_sources(conn)


def _sidebar_filters(db_path: Path) -> DashboardFilters:
    with st.sidebar:
        st.header("Filters")
        search = st.text_input("Search title or summary", max_chars=100)
        min_score = st.slider("Minimum opportunity score", 0, 100, 0, step=5)
        min_seriousness = st.slider("Minimum seriousness", 1, 5, 1)
        difficulties = st.multiselect("Difficulty", DIFFICULTIES)
        statuses = st.multiselect("Status", STATUSES)
        sources = st.multiselect("Source", _sources(str(db_path)))
        buildable_only = st.checkbox("Buildable only")
    return DashboardFilters(
        difficulties=tuple(difficulties),
        sources=tuple(sources),
        statuses=tuple(statuses),
        min_score=min_score,
        min_seriousness=min_seriousness,
        buildable_only=buildable_only,
        search=search,
    )


def _column_config() -> dict:
    cc = st.column_config
    return {
        "Post": cc.LinkColumn("Post", display_text="Open ↗", help="Open the original post", width="small"),
        "Title": cc.TextColumn("Title", width="large"),
        "Verdict": cc.TextColumn("Verdict", help="Work on this ≥ 70, Worth a look ≥ 45, else Skip"),
        "Score": cc.ProgressColumn("Score", min_value=0, max_value=100, format="%d",
                                   help="Opportunity score: seriousness, product potential and ease"),
        "Seriousness": cc.TextColumn("Seriousness", help="How likely this is a real, committed person (1-5)"),
        "Delivery": cc.TextColumn("Delivery", help="Days to promise the client (includes a safety margin)"),
        "Hours": cc.NumberColumn("Hours", format="%.1f", help="Estimated build + test hours"),
        "Potential": cc.NumberColumn("Potential", help="Product potential 1-5"),
        "Contact": cc.TextColumn("Contact", width="medium", help="The allowed way to reach this person"),
        "Summary": cc.TextColumn("Summary", width="large"),
        "Ticked": cc.CheckboxColumn("Ticked"),
        "Status": cc.SelectboxColumn("Status", options=list(STATUSES), required=True),
        "Path": cc.SelectboxColumn("Path", options=list(PATHS)),
        "Notes": cc.TextColumn("Notes", max_chars=MAX_NOTES_CHARS),
    }


def _save_edits(db_path: Path, editor_key: str, post_ids: list[str], statuses: list[str]) -> None:
    """Runs when a cell changes: save to the database, then reset the editor."""
    edited_rows = st.session_state.get(editor_key, {}).get("edited_rows", {})
    changes = edits_to_changes(edited_rows, post_ids, statuses)
    try:
        with db.open_db(db_path) as conn:
            for post_id, fields in changes:
                store.update_action(conn, post_id, **fields)
    except (ValueError, sqlite3.Error) as exc:
        log.exception("Saving dashboard edits failed")
        st.toast(f"Could not save: {exc}", icon="⚠️")
    else:
        if changes:
            st.toast("Saved", icon="✅")
    # A new key gives a fresh editor that shows exactly what's in the database.
    st.session_state[_EDITOR_VERSION] = st.session_state.get(_EDITOR_VERSION, 0) + 1


def _details(rows: list[dict]) -> None:
    """Full details for one post. Post text is shown as plain text, never as HTML."""
    st.subheader("Post details")
    by_id = {r["id"]: r for r in rows}
    chosen = st.selectbox(
        "Pick a post from this page",
        list(by_id),
        format_func=lambda pid: f"[{by_id[pid]['opportunity_score']}] {by_id[pid]['title'][:100]}",
    )
    if chosen is None:
        return
    r = by_id[chosen]

    st.link_button(f"Open the original post on {PLATFORM_NAMES[r['platform']]} ↗", r["url"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Verdict", opportunity_label(r["opportunity_score"]), f"score {r['opportunity_score']}",
              delta_color="off")
    c2.metric("Seriousness", stars(r["seriousness"]))
    c3.metric("Delivery", delivery_label(r["delivery_min_days"], r["delivery_max_days"]),
              f"{r['est_hours']:g} build hours", delta_color="off")
    c4.metric("Difficulty", r["difficulty"].capitalize(), r["tool"], delta_color="off")

    # Everything below came from strangers or from AI reading their text:
    # st.text shows it literally, so links, images or scripts in it can't run.
    for label, key in (
        ("Summary", "summary"),
        ("Why this seriousness rating", "seriousness_reason"),
        ("Difficulty reasoning", "reason"),
        ("How to contact", "contact_method"),
        ("Region (only if stated in the post)", "region_hint"),
    ):
        st.caption(label)
        st.text(r[key])
    st.caption("Full post")
    st.text(
        f"{r['source']} · posted {r['posted_at'][:10]} · by {r['author_handle'] or 'unknown'}\n\n"
        f"{r['title']}\n\n{r['body'] or '(no text)'}"
    )
