"""Drafts side by side (one per writing style) so the user can compare and choose.
Used for reply drafts and email drafts."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

import streamlit as st

from app import store
from app.errors import AppError
from app.writing import STYLE_HELP, STYLE_LABELS, get_style

log = logging.getLogger(__name__)

# Oldest style on the left, newest on the right (the order of STYLE_LABELS).
STYLES_IN_ORDER = tuple(STYLE_LABELS)


def compare_drafts(
    conn: sqlite3.Connection,
    *,
    kind: str,
    owner_id: str,
    chosen_body: str,
    make_draft: Callable[[str], object],
    choose: Callable[[dict], None],
    key: str,
    with_subject: bool = False,
) -> None:
    """All styles next to each other, each with draft / redraft / "use this one"."""
    drafts = store.get_drafts(conn, kind, owner_id)
    preferred = get_style(conn)
    if st.button("✨ Draft all styles" if not drafts else "🔄 Redraft all styles",
                 type="secondary" if drafts else "primary", key=f"all_{key}",
                 help=f"Writes one draft in each of the {len(STYLES_IN_ORDER)} styles (about Rs 1 each). "
                      f"Your preferred style ({STYLE_LABELS[preferred]}, set on the Settings page) is picked "
                      "automatically; switch with 'Use this one'."):
        # The preferred style is drafted last, so it ends up as the chosen one.
        order = sorted(STYLES_IN_ORDER, key=lambda s: s == preferred)
        if _run(lambda: [make_draft(style) for style in order], "Claude is writing a draft in each style..."):
            st.rerun()

    columns = st.columns(len(STYLES_IN_ORDER))
    for column, style in zip(columns, STYLES_IN_ORDER):
        with column, st.container(border=True):
            st.markdown(f"**{STYLE_LABELS[style]}**")
            st.caption(STYLE_HELP[style])
            draft = drafts.get(style)
            if draft:
                if with_subject:
                    st.caption("Subject")
                    st.text(draft["subject"])
                st.code(draft["body"], language=None, wrap_lines=True)
                if draft["body"] == chosen_body:
                    st.badge("Chosen", icon=":material/check_circle:", color="green")
                elif st.button("✅ Use this one", key=f"use_{key}_{style}"):
                    choose(draft)
                    st.toast(f"Using the {STYLE_LABELS[style]} draft", icon="✅")
                    st.rerun()
            if st.button("🔄 Redraft" if draft else "✨ Draft this style", key=f"one_{key}_{style}"):
                if _run(lambda s=style: make_draft(s), "Claude is writing..."):
                    st.rerun()


def _run(action: Callable[[], object], spinner: str) -> bool:
    try:
        with st.spinner(spinner):
            action()
        return True
    except (AppError, ValueError) as exc:
        log.warning("Drafting failed: %s", exc)
        st.error(str(exc))
        return False
