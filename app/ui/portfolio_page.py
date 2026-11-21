"""🏆 Portfolio: Claude picks the projects that build a strong, credible portfolio."""

from __future__ import annotations

import logging
import sqlite3

import pandas as pd
import streamlit as st

from app import store
from app.cli import build_llm
from app.config import Settings
from app.domain import TECH_LABELS
from app.errors import AppError
from app.models_registry import ModelRegistry
from app.portfolio import MAX_PROJECT_HOURS, plan_portfolio
from app.ui.components import empty_state, md_escape, page_header

log = logging.getLogger(__name__)

KIND_COLORS = {"Client-style project": "blue", "Sellable template": "orange",
               "Free tool / lead magnet": "green", "Case study": "violet"}


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header(
        "🏆 Portfolio projects",
        "Claude picks projects from the real requests you collected: everyday problems of people with no "
        "IT background, that build trust with clients and stay useful for years.",
    )
    plan = store.latest_portfolio_plan(conn)
    model = registry.model_for_task(conn, "portfolio")
    label = "🔄 Pick again (with the newest posts)" if plan else "🏆 Pick my portfolio projects"
    if st.button(label, type="secondary" if plan else "primary", key="plan_portfolio"):
        try:
            with st.spinner("Claude is reading your best requests and planning a portfolio..."):
                plan_portfolio(conn, build_llm(settings, conn), registry, settings)
            st.rerun()
        except (AppError, ValueError) as exc:
            log.warning("Portfolio plan failed: %s", exc)
            st.error(str(exc))
    st.caption(f"Only runs when you click. Uses {model.name} (about Rs 3–6 a time). "
               f"Each project is under {MAX_PROJECT_HOURS} hours and linked to the real posts it answers.")
    if not plan:
        empty_state("No portfolio plan yet", "Click the button above to get your first picks.")
        return

    content = plan["content"]
    st.caption(f"Picked on {plan['created_at'][:10]}")
    st.info(f"🧭 {md_escape(content['strategy'])}")
    for number, project in enumerate(content["projects"], start=1):
        _project_card(number, project)


def _project_card(number: int, project: dict) -> None:
    # Claude wrote this from strangers' posts, so every piece is escaped or shown as plain text.
    with st.container(border=True):
        st.markdown(f"### {number}. {md_escape(project['title'])}")
        with st.container(horizontal=True, gap="small"):
            st.badge(project["kind"], color=KIND_COLORS.get(project["kind"], "gray"))
            st.badge(f"{project['est_hours']:g} h to build", icon=":material/schedule:", color="gray")
            non_tech = sum(p["tech_level"] == "non_technical" for p in project["posts"])
            st.badge(f"Answers {len(project['posts'])} real posts ({non_tech} non-tech)",
                     icon=":material/groups:", color="green")
        st.markdown(f"🇵🇰 *{md_escape(project['roman_urdu'])}*")
        left, right = st.columns(2)
        with left:
            st.markdown(f"**🧩 Problem it solves**  \n{md_escape(project['problem'])}")
            st.markdown(f"**🙋 Who it helps**  \n{md_escape(project['who_its_for'])}")
            st.markdown(f"**🤝 Why clients will trust you**  \n{md_escape(project['why_credible'])}")
        with right:
            st.markdown(f"**🔮 Why it's future-proof**  \n{md_escape(project['future_proof'])}")
            st.markdown("**🎓 Skills it shows**  \n" + ", ".join(md_escape(s) for s in project["skills_shown"]))
            st.markdown("**🧰 Tools**  \n" + ", ".join(md_escape(t) for t in project["tools"]))
        st.markdown(f"**🎬 How to show it**  \n{md_escape(project['how_to_show'])}")
        st.success(f"👉 First step: {md_escape(project['first_step'])}")
        with st.expander(f"📌 The real posts it answers ({len(project['posts'])})"):
            st.dataframe(
                pd.DataFrame([{"Post": p["title"], "Who": TECH_LABELS.get(p["tech_level"] or "", ""),
                               "Link": p["url"]} for p in project["posts"]]),
                hide_index=True, width="stretch",
                column_config={"Post": st.column_config.TextColumn("Post", width="large"),
                               "Link": st.column_config.LinkColumn("Link", display_text="Open ↗", width=75)},
            )
