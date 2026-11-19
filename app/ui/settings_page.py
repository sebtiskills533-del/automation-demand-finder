"""Settings page: watch AI spending and pick a Claude model for each task."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app import store
from app.budget import month_start
from app.config import Settings
from app.domain import utc_now
from app.errors import ConfigError
from app.models_registry import TASKS, ModelRegistry
from app.ui.components import page_header, writing_style_picker

TASK_HELP = {
    "analyze": "Rates every new post. Runs the most, so the cheapest model is best.",
    "reply": "Writes reply drafts. A better writer reads more naturally.",
    "report": "Writes Decision Reports for posts you ticked.",
    "cluster": "Groups similar requests once a day. Cheap is fine.",
    "spec": "Writes product specs for templates. Only when you click.",
}


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header("⚙️ Settings & costs", "See what the AI costs and choose which Claude model does each job.")
    _spending(conn, settings)
    st.write("")
    _model_choice(conn, registry)
    st.write("")
    with st.container(border=True):
        st.markdown("#### ✍️ How replies and emails are written")
        st.caption("Switch any time; it is remembered and also shown next to every draft button.")
        writing_style_picker(conn, "settings")
    st.write("")
    _model_table(registry)


def _spending(conn: sqlite3.Connection, settings: Settings) -> None:
    since = month_start(utc_now())
    spent = store.spend_since(conn, since)
    budget = settings.monthly_budget_usd
    rate = settings.usd_to_inr
    with st.container(border=True):
        st.markdown("#### 💳 AI spending this month")
        c1, c2, c3 = st.columns(3)
        c1.metric("Spent", f"Rs {spent * rate:.2f}", help=f"${spent:.4f}")
        c2.metric("Monthly limit", f"Rs {budget * rate:.0f}", help=f"${budget:.2f} (MONTHLY_BUDGET_USD)")
        c3.metric("Posts rated per run (max)", settings.max_posts_per_run)
        share = min(spent / budget, 1.0)
        st.progress(share, text=f"{share:.0%} of your monthly limit used")
        st.caption("All AI work stops if the limit is reached. Change it with MONTHLY_BUDGET_USD in .env. "
                   "Rupee amounts are approximate (USD_TO_INR).")
        usage = store.usage_by_model_since(conn, since)
        if usage:
            with st.expander("Spending by model"):
                st.dataframe(
                    pd.DataFrame(usage).rename(columns={
                        "model": "Model", "calls": "Calls", "input_tokens": "Input tokens",
                        "output_tokens": "Output tokens", "cost_usd": "Cost (USD)",
                    }),
                    hide_index=True, width="stretch",
                    column_config={"Cost (USD)": st.column_config.NumberColumn(format="$%.4f")},
                )


def _model_choice(conn: sqlite3.Connection, registry: ModelRegistry) -> None:
    ids = [m.id for m in registry.models]
    with st.container(border=True):
        st.markdown("#### 🤖 Which Claude model does each job")
        st.caption("Level 1 is the cheapest; level 4 the smartest and most expensive.")
        columns = st.columns(2)
        for n, (task, task_label) in enumerate(TASKS.items()):
            with columns[n % 2]:
                current = registry.model_for_task(conn, task)
                chosen = st.selectbox(
                    task_label,
                    ids,
                    index=ids.index(current.id),
                    format_func=lambda mid: (
                        f"{registry.get(mid).display} · ${registry.get(mid).input_usd_per_mtok:g} / "
                        f"${registry.get(mid).output_usd_per_mtok:g} per 1M tokens"
                    ),
                    help=TASK_HELP.get(task),
                    key=f"model_{task}",
                )
                if chosen != current.id:
                    try:
                        registry.set_model_for_task(conn, task, chosen)
                    except ConfigError as exc:
                        st.error(str(exc))
                    else:
                        st.success(f"{task_label} will now use {registry.get(chosen).name}.")


def _model_table(registry: ModelRegistry) -> None:
    with st.expander("📚 All available models and prices"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Level": m.level,
                        "Model": m.name,
                        "What it's for": m.level_label,
                        "Input $ / 1M tokens": m.input_usd_per_mtok,
                        "Output $ / 1M tokens": m.output_usd_per_mtok,
                        "Notes": m.description,
                        "Model id": m.id,
                    }
                    for m in registry.models
                ]
            ),
            hide_index=True, width="stretch",
        )
        st.caption("Edit config/models.json to update prices or add a new model.")
