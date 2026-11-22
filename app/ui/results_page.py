"""📊 Results: which sources, services, styles and timings get replies."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app import results, store
from app.ui.components import empty_state, page_header


def render(conn: sqlite3.Connection) -> None:
    page_header(
        "📊 Results",
        "What happens after you answer: which platforms, services, writing styles and timings get replies. "
        "Mark 'What happened?' on each answered post so this page can learn.",
    )
    rows = store.results_rows(conn)
    if not rows:
        empty_state("No answered posts yet",
                    "Tick ✅ 'Maine jawab bhej diya' on posts you answer, then set 'What happened?' later.")
        return

    total = results.totals(rows)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ Answered", total["answered"])
    c2.metric("💬 Got a reply", total["replied"])
    c3.metric("🏆 Won", total["won"])
    c4.metric("Reply rate", f"{total['reply_rate']}%")
    waiting = sum(1 for r in rows if r.get("outcome") is None and not results.got_reply(r))
    if waiting:
        st.caption(f"⏳ {waiting} answers are still 'Waiting'. Update them on Opportunities → ✅ Answered.")
    if total["answered"] < results.TRUST_AFTER:
        st.info(f"With {total['answered']} answers these are early hints, not proof. Patterns become "
                f"trustworthy after about {results.TRUST_AFTER} answers.")

    left, right = st.columns(2)
    for n, dimension in enumerate(results.DIMENSIONS):
        with (left if n % 2 == 0 else right), st.container(border=True):
            st.markdown(f"**{dimension}**")
            st.dataframe(
                pd.DataFrame(results.breakdown(rows, dimension)), hide_index=True, width="stretch",
                column_config={"Reply rate %": st.column_config.ProgressColumn(
                    "Reply rate", min_value=0, max_value=100, format="%d%%")},
            )
