"""Import page: add posts you found anywhere, then have Claude rate them."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from datetime import timedelta

import streamlit as st

from app import store
from app.analyze import analyze_pending
from app.cli import build_llm
from app.config import PROJECT_ROOT, Settings
from app.errors import AppError
from app.importer import TEMPLATE_CSV, ImportReport, decode_upload, import_rows, import_text
from app.models_registry import ModelRegistry
from app.ui.components import page_header
from app.domain import utc_now
from app.muse.browser import STALE_LOCK_SECONDS, open_for_setup
from app.muse.job import MAX_FOCUS_CHARS, clean_focus

log = logging.getLogger(__name__)

AGENT_PROMPT = """Look at the posts I have open on this page. For each post where someone asks
for help automating a task (spreadsheets, emails, forms, Zapier, Make, scripts),
write one CSV line with these columns:
url,title,text,author,posted_at
- url: the full link to that post
- title: the post's title or its first sentence
- text: the post's text (put it in double quotes)
- author: the public username only
- posted_at: the date as YYYY-MM-DD if shown
Do NOT include email addresses or phone numbers.
Reply with only the CSV, starting with the header line."""


def render(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    page_header(
        "📥 Import posts",
        "Found a good request on Facebook, Instagram, Quora or anywhere else? Add it here and Claude "
        "rates it like every other post. Emails and phone numbers are removed automatically.",
    )

    with st.container(border=True):
        st.markdown("#### ➕ Add posts yourself")
        one, upload, paste = st.tabs(["Add one post", "Upload a file", "Paste CSV / JSON"])
        with one:
            _single_post_form(conn)
        with upload:
            _upload(conn)
        with paste:
            _paste(conn)

    with st.container(border=True):
        _rate_now(conn, settings, registry)

    with st.container(border=True):
        _muse_section(conn, settings)
    _agent_help()


def _single_post_form(conn: sqlite3.Connection) -> None:
    with st.form("add_one", clear_on_submit=True):
        url = st.text_input("Link to the post (https://...)", max_chars=2000)
        title = st.text_input("Title or first sentence", max_chars=500)
        text = st.text_area("Post text (optional but helps the rating)", max_chars=20_000)
        author = st.text_input("Public username (optional)", max_chars=50)
        submitted = st.form_submit_button("Add post", type="primary")
    if submitted:
        _show(import_rows(conn, [{"url": url, "title": title, "text": text, "author": author}]))


def _upload(conn: sqlite3.Connection) -> None:
    st.download_button("Download an empty CSV template", TEMPLATE_CSV, "posts_template.csv", "text/csv")
    file = st.file_uploader("CSV or JSON file", type=["csv", "json"], max_upload_size=2)
    if file is not None and st.button("Import this file"):
        try:
            _show(import_text(conn, decode_upload(file.getvalue())))
        except ValueError as exc:
            st.error(str(exc))


def _paste(conn: sqlite3.Connection) -> None:
    with st.form("paste", clear_on_submit=False):
        text = st.text_area(
            "Paste CSV (with header line) or JSON", height=200, max_chars=2_000_000,
            placeholder=TEMPLATE_CSV + "https://www.facebook.com/groups/x/posts/1,Need my orders in a sheet,...",
        )
        submitted = st.form_submit_button("Import")
    if submitted:
        try:
            _show(import_text(conn, text))
        except ValueError as exc:
            st.error(str(exc))


def _show(report: ImportReport) -> None:
    next_step = " Click 'Rate new posts now' below." if report.added else ""
    if report.errors:
        # Some rows were skipped: make sure that's noticed, even if others worked.
        st.warning(f"Import finished: {report.summary}.{next_step}")
    elif report.added:
        st.success(f"Import finished: {report.summary}.{next_step}")
    else:
        st.info(f"Nothing new: {report.summary}.")
    if report.errors:
        st.text("\n".join(report.errors[:50]))  # plain text: rows may contain anything


def _rate_now(conn: sqlite3.Connection, settings: Settings, registry: ModelRegistry) -> None:
    st.markdown("#### ⭐ Rate new posts")
    model = registry.model_for_task(conn, "analyze")
    st.caption(
        f"Uses {model.name}. Rates up to {settings.max_posts_per_run} waiting posts "
        "(imported and collected), within your monthly budget."
    )
    if not st.button("Rate new posts now", type="primary"):
        return
    try:
        with st.spinner("Claude is reading the posts..."):
            report = analyze_pending(conn, build_llm(settings, conn), registry, settings)
    except AppError as exc:
        log.exception("Rating from the import page failed")
        st.error(str(exc))
        return
    st.success(
        f"Rated {report.analyzed} posts (skipped {report.failed}). "
        f"Cost ${report.cost_usd:.4f} (about Rs {report.cost_usd * settings.usd_to_inr:.2f}). "
        "See them on the Opportunities page."
    )
    if report.stopped_reason:
        st.warning(report.stopped_reason)


def _muse_section(conn: sqlite3.Connection, settings: Settings) -> None:
    st.markdown("#### 🤖 Collect with Muse")
    muse = settings.muse
    if not muse.enabled:
        st.info(
            "Muse collection is switched off. To use it, set MUSE_ENABLED=true in .env "
            "and follow the 'Muse' section of the README."
        )
        return

    st.caption(
        f'The app opens its own Chrome (port {muse.browser_port}), types a job into your Muse side chat '
        f'"{muse.chat_name}", waits for the answer (up to 30 minutes), imports up to {muse.max_posts} '
        "posts and rates them. You can watch it work in that Chrome window."
    )
    c1, c2 = st.columns(2)
    if c1.button("Open the Muse Chrome (log in / set up)"):
        try:
            open_for_setup(muse)
            st.success("The Muse Chrome is opening. Log in to muse.ai there; it remembers you.")
        except AppError as exc:
            st.error(str(exc))

    focus = st.text_input("What should Muse look for? (optional)", max_chars=MAX_FOCUS_CHARS,
                          placeholder="e.g. Etsy sellers and Google Sheets")
    store.fail_stale_muse_runs(conn, utc_now() - timedelta(seconds=STALE_LOCK_SECONDS))
    running = any(r["status"] == "running" for r in store.recent_muse_runs(conn, 3))
    if c2.button("Start Muse collection", type="primary", disabled=running):
        try:
            start_background_run(clean_focus(focus))
            st.success("Started. It can take up to 30 minutes. Click 'Refresh' below to see progress.")
        except (AppError, OSError) as exc:
            st.error(f"Could not start: {exc}")
    if running:
        st.info("A Muse run is in progress.")

    runs = store.recent_muse_runs(conn, 5)
    r1, r2 = st.columns([1, 3])
    if r1.button("🔄 Refresh"):
        st.rerun()
    if r2.button("📥 Import Muse's last reply", disabled=running,
                 help="Muse finished after the app stopped waiting? This reads its last reply from the chat "
                      "and imports the posts, without sending a new job. Safe to repeat."):
        try:
            start_background_import_last()
            st.success("Reading Muse's last reply. Click 'Refresh' in about 30 seconds.")
        except OSError as exc:
            st.error(f"Could not start: {exc}")
    if runs:
        st.dataframe(
            [
                {
                    "Started": r["started_at"].replace("T", " ").rstrip("Z"),
                    "Status": r["status"],
                    "Added": r["added"],
                    "Rated": r["rated"],
                    "Result": r["message"],
                    "Muse chat": r["chat_url"] or None,
                }
                for r in runs
            ],
            hide_index=True,
            column_config={"Muse chat": st.column_config.LinkColumn(display_text="Open ↗")},
        )


def start_background_run(focus: str) -> None:
    """Run `python -m app.muse run` as its own process so the dashboard stays responsive."""
    _start_muse_process(["run", "--focus", focus] if focus else ["run"])


def start_background_import_last() -> None:
    _start_muse_process(["import-last"])


def _start_muse_process(muse_args: list[str]) -> None:
    # An argument list, never a shell, so nothing typed can be run as a command.
    args = [sys.executable, "-m", "app.muse", *muse_args]
    options: dict = {"cwd": PROJECT_ROOT, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(args, **options)  # noqa: S603


def _agent_help() -> None:
    with st.expander("Using an AI browser assistant (such as Muse) to prepare the CSV"):
        st.write(
            "Open the posts in your browser yourself, then give your assistant this instruction. "
            "Paste its answer into the 'Paste CSV / JSON' tab."
        )
        st.code(AGENT_PROMPT, language=None)
        st.warning(
            "Facebook, Instagram, Quora and Reddit forbid automated collection. Letting an AI agent "
            "browse and copy posts with your logged-in accounts can get those accounts banned. "
            "Keep it to a few posts you are actually reading, and the decision is yours."
        )
