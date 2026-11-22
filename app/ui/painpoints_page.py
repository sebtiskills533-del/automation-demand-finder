"""💡 Pain points: problems people have that could become an app or automation product.

Every AI step can be run by Claude or by Muse; the user picks.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta

import pandas as pd
import streamlit as st

from app import painpoints, store
from app.cli import build_llm
from app.config import Settings
from app.domain import TECH_LABELS, utc_now
from app.errors import AppError
from app.models_registry import ModelRegistry
from app.muse.analysis import import_pasted_answer
from app.muse.browser import STALE_LOCK_SECONDS
from app.muse.job import LINKEDIN_MAX_POSTS, PLACE_LABELS
from app.ui.background import start_muse_process
from app.ui.components import empty_state, md_escape, page_header

log = logging.getLogger(__name__)

CLAUDE, MUSE = "🤖 Claude", "🪄 Muse"
COLLECT_PLACES = ("reddit", "linkedin_trends", "linkedin")
VERDICT_STYLE = {"Build it": ("green", "✅"), "Test first": ("orange", "🧪"), "Skip": ("gray", "⏭️")}
EVIDENCE_COLOR = {"Strong": "green", "Medium": "orange", "Thin": "red"}


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header(
        "💡 Pain points",
        "Real problems people have, why they hurt, whether it is worth building an app or automation for "
        "each one, and how it could earn (subscription or AdSense). Built only from the posts you collected.",
    )
    store.fail_stale_muse_runs(conn, utc_now() - timedelta(seconds=STALE_LOCK_SECONDS))
    running = any(r["status"] == "running" for r in store.recent_muse_runs(conn, 3))

    with st.container(border=True):
        _collect_step(settings, running)
    with st.container(border=True):
        _analyse_step(conn, settings, registry, running)

    plan = store.latest_pain_point_plan(conn)
    if not plan:
        empty_state("No analysis yet", "Collect some posts, then click one of the analyse buttons above.")
        return
    _results(plan)


# ------------------------------------------------------------------ steps


def _collect_step(settings: Settings, running: bool) -> None:
    st.markdown("#### 1️⃣ Collect more evidence (optional)")
    muse = settings.muse
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🪄 With Muse** (Reddit and LinkedIn)")
        if not muse.enabled:
            st.caption("Muse is switched off. Set MUSE_ENABLED=true in .env to use it.")
        else:
            place = st.selectbox("Where should Muse look?", COLLECT_PLACES, format_func=PLACE_LABELS.get,
                                 key="pp_place")
            if place.startswith("linkedin"):
                st.warning("LinkedIn forbids automated access, so your account could be restricted. Use it "
                           f"sparingly (a couple of times a week). Limited to {LINKEDIN_MAX_POSTS} posts, read "
                           "only, no names copied.")
            if st.button("🔍 Start Muse collection", key="pp_collect", disabled=running):
                _start(["run", "--where", place],
                       "Started. It can take up to 30 minutes; the posts are rated when it finishes. "
                       "Progress is on the Import posts page.")
            if running:
                st.info("A Muse job is running right now.")
    with c2:
        st.markdown("**🤖 With the daily job** (Hacker News, forums, job boards)")
        st.caption("Run the daily job from your desktop shortcut. Claude rates every new post, including the "
                   "LinkedIn and Reddit ones Muse collected (Import posts → Rate new posts now).")


def _analyse_step(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, running: bool) -> None:
    st.markdown("#### 2️⃣ Find the pain points")
    engine = st.segmented_control("Who should analyse your posts?", [CLAUDE, MUSE], default=CLAUDE,
                                  key="pp_engine") or CLAUDE
    model = registry.model_for_task(conn, painpoints.TASK)
    if engine == CLAUDE:
        st.caption(f"One click. Uses {model.name} (about Rs 8–15 a time, within your monthly budget).")
        if st.button("💡 Find pain points with Claude", type="primary", key="pp_claude"):
            try:
                with st.spinner("Claude is reading your posts and thinking it through... (about a minute)"):
                    painpoints.analyze_with_claude(conn, build_llm(settings, conn), registry, settings)
                st.rerun()
            except (AppError, ValueError) as exc:
                log.warning("Pain point analysis failed: %s", exc)
                st.error(str(exc))
    else:
        _muse_analysis(conn, settings, running)


def _muse_analysis(conn: sqlite3.Connection, settings: Settings, running: bool) -> None:
    st.caption("Free, but slower (a few minutes). The app types the same question into your Muse chat "
               f'"{settings.muse.chat_name}" and reads back its answer. Muse is told not to browse.')
    if settings.muse.enabled:
        c1, c2 = st.columns(2)
        if c1.button("🪄 Ask Muse to find pain points", type="primary", key="pp_muse", disabled=running):
            _start(["pain-points"], "Started. Click 'Refresh' in a few minutes.")
        if c2.button("📥 Import Muse's last answer", key="pp_import_last", disabled=running,
                     help="Reads Muse's newest analysis from the chat without asking again."):
            _start(["import-last-painpoints"], "Reading Muse's last answer. Click 'Refresh' in about 30 seconds.")
        if st.button("🔄 Refresh", key="pp_refresh"):
            st.rerun()
        runs = [r for r in store.recent_muse_runs(conn, 5) if "pain points" in r["focus"]]
        if runs:
            st.caption(f"Last Muse analysis: {runs[0]['status']}. {runs[0]['message'] or ''}")
    else:
        st.info("Muse is switched off, but you can still use it by hand: prepare the question below, ask Muse "
                "yourself, then paste its answer.")

    with st.expander("📋 Ask Muse yourself and paste the answer"):
        if st.button("Prepare the question", key="pp_prepare"):
            try:
                items = painpoints.select_items(conn, jobs=painpoints.MUSE_JOBS, market=painpoints.MUSE_MARKET)
            except ValueError as exc:
                st.error(str(exc))
            else:
                painpoints.remember_items(conn, items)
                st.session_state["pp_question"] = painpoints.muse_message(items)
        if st.session_state.get("pp_question"):
            st.caption("Copy this (button in the box), paste it into your Muse chat, then paste Muse's answer below.")
            st.code(st.session_state["pp_question"], language=None, wrap_lines=True)
        answer = st.text_area("Muse's answer (ANALYSIS START ... ANALYSIS END)", height=160, max_chars=300_000,
                              key="pp_answer")
        if st.button("Import this answer", key="pp_import_pasted"):
            try:
                content = import_pasted_answer(conn, answer)
                st.success(f"Saved {len(content['pain_points'])} pain points.")
                st.rerun()
            except (AppError, ValueError) as exc:
                st.error(str(exc))


def _start(muse_args: list[str], message: str) -> None:
    try:
        start_muse_process(muse_args)
        st.success(message)
    except OSError as exc:
        log.warning("Could not start Muse: %s", exc)
        st.error(f"Could not start: {exc}")


# ---------------------------------------------------------------- results


def _results(plan: dict) -> None:
    content = plan["content"]
    engine = {"claude": "Claude", "muse": "Muse", "pasted": "Muse (pasted)"}.get(plan["engine"], plan["engine"])
    st.caption(f"Analysed on {plan['created_at'][:10]} by {engine}")
    st.info(f"🧭 {md_escape(content['summary'])}")
    for number, point in enumerate(content["pain_points"], start=1):
        _pain_point_card(number, point)
    if content["market_trends"]:
        st.markdown("### 📈 What's new in the market")
        for trend in content["market_trends"]:
            with st.container(border=True):
                st.markdown(f"**{md_escape(trend['title'])}**")
                st.markdown(f"{md_escape(trend['whats_new'])}")
                st.success(f"💰 Opportunity: {md_escape(trend['opportunity'])}")
                if trend["posts"]:
                    _posts_table(trend["posts"], f"📌 The posts behind it ({len(trend['posts'])})")


def _pain_point_card(number: int, point: dict) -> None:
    # Written by an AI from strangers' posts: everything is escaped or shown as plain text.
    color, icon = VERDICT_STYLE.get(point["verdict"], ("gray", "•"))
    with st.container(border=True):
        st.markdown(f"### {number}. {md_escape(point['title'])}")
        with st.container(horizontal=True, gap="small"):
            st.badge(f"{icon} {point['verdict']}", color=color)
            st.badge(f"Evidence: {point['evidence']} ({len(point['posts'])} real posts)",
                     icon=":material/fact_check:", color=EVIDENCE_COLOR.get(point["evidence"], "gray"))
            if point["non_tech"]:
                st.badge(f"🙋 {point['non_tech']} non-tech", color="green")
            st.badge(f"💰 {point['best_model']}", color="orange")
            st.badge(f"{point['est_hours']:g} h for a first version", icon=":material/schedule:", color="gray")
        if point.get("roman_urdu"):
            st.markdown(f"🇵🇰 *{md_escape(point['roman_urdu'])}*")

        st.markdown(f"**😣 The pain**  \n{md_escape(point['pain'])}")
        st.markdown(f"**🔍 Why it is a real pain point**  \n{md_escape(point['why_painful'])}")
        st.markdown(f"**🙋 Who has it**  \n{md_escape(point['who'])}")
        st.markdown(f"**🛠️ What to build** ({md_escape(point['kind'])})  \n{md_escape(point['solution'])}")

        st.markdown("#### 🗣️ Should we build it? The discussion")
        left, right = st.columns(2)
        with left:
            st.markdown("**✅ Why we should**")
            st.markdown("\n".join(f"- {md_escape(a)}" for a in point["case_for"]))
        with right:
            st.markdown("**⚠️ The risks, and the answer to each**")
            st.markdown("\n".join(f"- {md_escape(a)}" for a in point["case_against"]))
        message = f"{point['verdict']}: {md_escape(point['verdict_reason'])}"
        {"green": st.success, "orange": st.warning}.get(color, st.info)(message)

        st.markdown("#### 💰 How it could earn")
        sub, ads = st.columns(2)
        sub.markdown(f"**🔁 Subscription**  \n{md_escape(point['subscription_case'])}")
        ads.markdown(f"**📰 AdSense (ads)**  \n{md_escape(point['adsense_case'])}")
        st.markdown(f"**🚀 Cheapest first test**  \n{md_escape(point['first_revenue_step'])}")
        _posts_table(point["posts"], f"📌 The real posts behind it ({len(point['posts'])})")


def _posts_table(posts: list[dict], title: str) -> None:
    with st.expander(title):
        st.dataframe(
            pd.DataFrame([{"Post": p["title"], "Who": TECH_LABELS.get(p["tech_level"] or "", ""), "Link": p["url"]}
                          for p in posts]),
            hide_index=True, width="stretch",
            column_config={"Post": st.column_config.TextColumn("Post", width="large"),
                           "Link": st.column_config.LinkColumn("Link", display_text="Open ↗", width=75)},
        )
