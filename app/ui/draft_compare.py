"""Two drafts side by side (one per writing style) so the user can compare and choose.
Used for reply drafts and email drafts."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

import streamlit as st

from app import store
from app.errors import AppError
from app.writing import CONVINCING, STYLE_HELP, STYLE_LABELS, TIP_FIRST, get_style

log = logging.getLogger(__name__)

# The earlier style on the left, the new one on the right.
STYLES_IN_ORDER = (TIP_FIRST, CONVINCING)


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
    """Both styles next to each other, each with draft / redraft / "use this one"."""
    drafts = store.get_drafts(conn, kind, owner_id)
    preferred = get_style(conn)
    if st.button("✨ Draft both styles" if not drafts else "🔄 Redraft both styles",
                 type="secondary" if drafts else "primary", key=f"both_{key}",
                 help=f"Writes one draft in each style. Your preferred style ({STYLE_LABELS[preferred]}, "
                      "set on the Settings page) is picked automatically; switch with 'Use this one'."):
        # The preferred style is drafted last, so it ends up as the chosen one.
        order = sorted(STYLES_IN_ORDER, key=lambda s: s == preferred)
        if _run(lambda: [make_draft(style) for style in order], "Claude is writing both drafts..."):
            st.rerun()

    columns = st.columns(2)
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
