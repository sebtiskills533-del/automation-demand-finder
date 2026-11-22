"""🧭 Platforms: where to find clients, sorted for the user's services (config/platforms.json)."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import streamlit as st

from app import store
from app.config import CONFIG_DIR
from app.domain import JOB_BOARD_PLATFORMS
from app.errors import ConfigError
from app.services import service_names
from app.ui.components import page_header

PLATFORMS_FILE = CONFIG_DIR / "platforms.json"


def load_platform_groups(path=PLATFORMS_FILE) -> list[dict]:
    """[{id, title, help, platforms: [(name, note), ...]}] from config/platforms.json."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        groups = [
            {"id": str(g["id"]), "title": str(g["title"]), "help": str(g.get("help", "")),
             "platforms": [(str(p[0]), str(p[1]) if len(p) > 1 else "") for p in g["platforms"]]}
            for g in raw["groups"]
        ]
    except FileNotFoundError:
        raise ConfigError(f"Platforms file not found: {path}") from None
    except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
        raise ConfigError(f"{path.name} could not be read: {exc}") from None
    return groups


def render(conn: sqlite3.Connection) -> None:
    page_header(
        "🧭 Platforms",
        f"Where to find clients for your services ({service_names()}). Connected platforms feed jobs "
        "into Opportunities automatically; the rest you use by hand.",
    )
    try:
        groups = load_platform_groups()
    except ConfigError as exc:
        st.error(str(exc))
        return

    jobs = store.count_dashboard_rows(conn, store.DashboardFilters(platforms=JOB_BOARD_PLATFORMS))
    total = sum(len(g["platforms"]) for g in groups)
    c1, c2, c3 = st.columns(3)
    c1.metric("Platforms in your list", total)
    c2.metric("Connected automatically", len(next((g["platforms"] for g in groups if g["id"] == "connected"), [])))
    c3.metric("Rated jobs from job boards", jobs, help="See them on the 💼 Freelance jobs page")

    search = st.text_input("Find a platform", max_chars=60, placeholder="e.g. Fiverr").strip().lower()
    for group in groups:
        rows = [(name, note) for name, note in group["platforms"]
                if not search or search in name.lower() or search in note.lower()]
        if not rows:
            continue
        with st.expander(f"{group['title']} ({len(rows)})", expanded=group["id"] in ("connected", "good_fit")):
            st.caption(group["help"])
            st.dataframe(pd.DataFrame(rows, columns=["Platform", "Why / how to use it"]), hide_index=True,
                         width="stretch", column_config={"Why / how to use it": st.column_config.TextColumn(width="large")})
    st.caption("Edit config/platforms.json to move a platform to another group or change its note.")
