"""Top problems: requests that keep repeating, and product specs for templates."""

from __future__ import annotations

import logging
import sqlite3

import pandas as pd
import streamlit as st

from app import store
from app.cli import build_llm
from app.clusters import run_clustering
from app.config import Settings
from app.errors import AppError
from app.models_registry import ModelRegistry
from app.specs import spec_to_markdown, write_spec
from app.ui.components import empty_state, labelled_text, md_escape, page_header
from app.ui.table import stars

log = logging.getLogger(__name__)


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header(
        "🧩 Top problems",
        "Requests that keep coming back. A problem seen many times is a template you can build once "
        "and sell many times.",
    )
    c1, c2 = st.columns([3, 1])
    show_all = c1.toggle("Include problems seen only once", value=False)
    if c2.button("🔄 Regroup now", width="stretch",
                 help=f"Claude groups the last 30 days of posts ({registry.model_for_task(conn, 'cluster').name})"):
        _regroup(conn, settings, registry)

    clusters = store.list_clusters(conn, min_posts=1 if show_all else 2)
    if not clusters:
        empty_state(
            "No repeated problems yet",
            "Groups appear after the daily job has rated more posts. Click 'Regroup now' to try, "
            "or switch on 'Include problems seen only once'.",
        )
        return

    table = pd.DataFrame(
        [
            {
                "Problem": c["name"],
                "Seen": c["posts"],
                "Potential": stars(round(c["avg_potential"] or 0)),
                "Avg score": c["avg_score"] or 0,
                "Avg hours": c["avg_hours"] or 0,
                "Last seen": (c["last_seen"] or "")[:10],
                "Spec": "📝" if c["has_spec"] else "",
            }
            for c in clusters
        ]
    )
    event = st.dataframe(
        table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        key=f"problems_{show_all}",
        column_config={
            "Problem": st.column_config.TextColumn("Problem", width="large"),
            "Seen": st.column_config.NumberColumn("Seen", help="How many posts asked for this", width="small"),
            "Avg score": st.column_config.ProgressColumn("Avg score", min_value=0, max_value=100, format="%d"),
            "Spec": st.column_config.TextColumn("Spec", width="small", help="📝 = product spec written"),
        },
    )
    rows = event.selection.rows if event and event.selection else []
    if not rows:
        st.caption("👆 Click a problem to see its posts and write a product spec. Showing the top one.")
    cluster = clusters[rows[0] if rows and rows[0] < len(clusters) else 0]
    st.write("")
    _cluster_detail(conn, settings, registry, cluster)


def _regroup(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    try:
        with st.spinner("Claude is grouping similar requests..."):
            result = run_clustering(conn, build_llm(settings, conn), registry, settings, force=True)
    except AppError as exc:
        log.warning("Regroup failed: %s", exc)
        st.error(str(exc))
        return
    if result.skipped_reason:
        st.info(result.skipped_reason)
    else:
        st.success(f"Grouped {result.grouped_posts} posts into {result.groups} problems.")


def _cluster_detail(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, cluster: dict) -> None:
    with st.container(border=True):
        st.markdown(f"### {md_escape(cluster['name'])}")
        st.caption(f"Seen {cluster['posts']} times · average potential "
                   f"{stars(round(cluster['avg_potential'] or 0))} · about {cluster['avg_hours'] or 0:g} h to build")
        st.text(cluster["description"] or "")

        posts_tab, spec_tab = st.tabs(["📰 Posts asking for this", "📝 Product spec"], key=f"ct_{cluster['id']}")
        with posts_tab:
            posts = store.cluster_posts(conn, cluster["id"])
            st.dataframe(
                pd.DataFrame([{"Score": p["opportunity_score"], "What they need": p["summary"],
                               "Source": p["source"], "Posted": p["posted_at"][:10], "Link": p["url"]}
                              for p in posts]),
                hide_index=True, width="stretch",
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                    "What they need": st.column_config.TextColumn(width="large"),
                    "Link": st.column_config.LinkColumn(display_text="Open ↗"),
                },
            )
        with spec_tab:
            _spec(conn, settings, registry, cluster)


def _spec(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry, cluster: dict) -> None:
    spec = store.get_product_spec(conn, cluster["id"])
    model = registry.model_for_task(conn, "spec")
    st.caption(f"A build plan for a sellable Google Sheets + Apps Script template — hand it to Claude Code "
               f"to build. Uses {model.name}.")
    label = "🔄 Write a new spec" if spec else "📝 Write product spec"
    if st.button(label, type="secondary" if spec else "primary", key=f"spec_{cluster['id']}"):
        try:
            with st.spinner("Claude is writing the spec..."):
                write_spec(conn, build_llm(settings, conn), registry, settings, cluster["id"])
            st.rerun()
        except (AppError, ValueError) as exc:
            log.warning("Spec failed: %s", exc)
            st.error(str(exc))
    if not spec:
        return

    st.markdown(f"#### {md_escape(spec['product_name'])}")
    left, right = st.columns(2)
    with left:
        labelled_text("Who it's for", spec["who_its_for"])
        labelled_text("Problem it solves", spec["problem_solved"])
        st.caption("Features")
        st.markdown("\n".join(f"- {md_escape(f)}" for f in spec["features"]))
    with right:
        st.caption("Sheets / tabs")
        st.dataframe(pd.DataFrame(spec["sheets_tabs"]).rename(columns={"name": "Tab", "purpose": "Purpose"}),
                     hide_index=True, width="stretch")
        st.caption("Script triggers")
        st.markdown("\n".join(f"- {md_escape(t)}" for t in spec["script_triggers"]) or "-")
    st.caption("Buyer setup steps")
    st.markdown("\n".join(f"{n}. {md_escape(s)}" for n, s in enumerate(spec["buyer_setup_steps"], start=1)))
    st.caption("Etsy listing title (copy with the button)")
    st.code(spec["etsy_listing_title"], language=None, wrap_lines=True)
    st.download_button("⬇️ Download spec (Markdown, for Claude Code)", spec_to_markdown(spec),
                       file_name=f"{cluster['id']}-spec.md", mime="text/markdown", key=f"dl_{cluster['id']}")
