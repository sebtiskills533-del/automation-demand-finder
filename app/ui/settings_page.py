"""Settings page: pick a Claude model per task and watch AI spending."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app import store
from app.analyze import month_start
from app.config import Settings
from app.domain import utc_now
from app.errors import ConfigError
from app.models_registry import TASKS, ModelRegistry


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    st.title("Settings & costs")
    _spending(conn, settings)
    _model_choice(conn, registry)
    _model_table(registry)


def _spending(conn: sqlite3.Connection, settings: Settings) -> None:
    st.subheader("AI spending this month")
    since = month_start(utc_now())
    spent = store.spend_since(conn, since)
    budget = settings.monthly_budget_usd
    c1, c2, c3 = st.columns(3)
    c1.metric("Spent", f"${spent:.4f}", f"≈ Rs {spent * settings.usd_to_inr:.2f}", delta_color="off")
    c2.metric("Monthly limit", f"${budget:.2f}", f"≈ Rs {budget * settings.usd_to_inr:.0f}", delta_color="off")
    c3.metric("Posts per run (max)", settings.max_posts_per_run)
    st.progress(min(spent / budget, 1.0), text=f"{min(spent / budget, 1.0):.0%} of the monthly limit")
    st.caption("Change the limit with MONTHLY_BUDGET_USD in .env. Rupee amounts use USD_TO_INR and are approximate.")

    usage = store.usage_by_model_since(conn, since)
    if usage:
        st.dataframe(
            pd.DataFrame(usage).rename(columns={
                "model": "Model", "calls": "Calls", "input_tokens": "Input tokens",
                "output_tokens": "Output tokens", "cost_usd": "Cost (USD)",
            }),
            hide_index=True,
            column_config={"Cost (USD)": st.column_config.NumberColumn(format="$%.4f")},
        )


def _model_choice(conn: sqlite3.Connection, registry: ModelRegistry) -> None:
    st.subheader("Which Claude model to use")
    ids = [m.id for m in registry.models]
    for task, task_label in TASKS.items():
        current = registry.model_for_task(conn, task)
        chosen = st.selectbox(
            task_label,
            ids,
            index=ids.index(current.id),
            format_func=lambda mid: (
                f"{registry.get(mid).display} · ${registry.get(mid).input_usd_per_mtok:g} in / "
                f"${registry.get(mid).output_usd_per_mtok:g} out per 1M tokens"
            ),
            key=f"model_{task}",
        )
        if chosen != current.id:
            try:
                registry.set_model_for_task(conn, task, chosen)
            except ConfigError as exc:
                st.error(str(exc))
            else:
                st.success(f"{task_label} will now use {registry.get(chosen).name}.")
    st.caption("Higher levels are smarter but cost more. Level 1 (Haiku) is plenty for daily analysis.")


def _model_table(registry: ModelRegistry) -> None:
    st.subheader("All available models")
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
        hide_index=True,
    )
    st.caption("Edit config/models.json to update prices or add a new model.")
