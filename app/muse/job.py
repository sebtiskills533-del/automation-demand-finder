"""One Muse collection run: ask Muse -> parse -> import -> (optionally) rate.

Every run is recorded in the muse_runs table so the dashboard can show it.
Runs are started by the user only, never retried automatically, so a
failure can't create duplicate work in Muse.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from app import store
from app.config import Settings
from app.domain import utc_now
from app.errors import AppError, ConfigError
from app.importer import import_rows
from app.muse.browser import STALE_LOCK_SECONDS, BrowserLock, connected_context
from app.muse.client import MuseReplyText, ask_muse
from app.muse.parse import parse_muse_reply

log = logging.getLogger(__name__)

MAX_FOCUS_CHARS = 100
DEFAULT_FOCUS = "any small business or freelancer asking for help automating a repetitive task"


@dataclass(frozen=True)
class MuseRunResult:
    run_id: int
    ok: bool
    message: str
    added: int = 0
    duplicates: int = 0
    skipped: int = 0
    rated: int = 0
    chat_url: str = ""


def clean_focus(focus: str) -> str:
    """One short line: it is typed into Muse as part of the job message."""
    focus = " ".join((focus or "").split())
    if len(focus) > MAX_FOCUS_CHARS:
        raise ConfigError(f"Keep the focus under {MAX_FOCUS_CHARS} characters.")
    return focus


def build_job_message(focus: str, max_posts: int) -> str:
    """The single line typed into the Muse side chat (the procedure is already there)."""
    return (
        f"FIND POSTS. Focus: {focus or DEFAULT_FOCUS}. Maximum {max_posts} posts. "
        "Use your browser (the Muse Chrome extension) to actually open Quora, Facebook and Instagram "
        "and search them. If you cannot open them, reply PROBLEM: <what happened>. If nothing matches, "
        "reply NO POSTS FOUND: <where you looked and why>. Otherwise reply only in the exact "
        "POSTS START / POSTS END format from your procedure."
    )


def run_muse_collection(
    conn: sqlite3.Connection,
    settings: Settings,
    focus: str = "",
    *,
    ask: Callable[[str], MuseReplyText] | None = None,
    rate: Callable[[], int] | None = None,
) -> MuseRunResult:
    """Run one collection. `ask` / `rate` are injectable for tests.

    `ask(message)` returns Muse's reply; by default it drives the app's Chrome.
    `rate()` rates waiting posts and returns how many; None = don't rate.
    """
    muse = settings.muse
    if not muse.enabled:
        raise ConfigError("Muse is switched off. Set MUSE_ENABLED=true in .env to use it.")
    focus = clean_focus(focus)
    store.fail_stale_muse_runs(conn, utc_now() - timedelta(seconds=STALE_LOCK_SECONDS))
    run_id = store.start_muse_run(conn, focus)
    message = build_job_message(focus, muse.max_posts)

    if ask is None:
        def ask(text: str) -> MuseReplyText:
            with connected_context(muse) as context:
                return ask_muse(context, muse.chat_name, text)

    try:
        with BrowserLock(settings.db_path.parent / "muse.lock"):
            reply = ask(message)
        parsed = parse_muse_reply(reply.text, muse.max_posts)
        report = import_rows(conn, parsed.rows)
    except AppError as exc:
        log.warning("Muse run %d failed: %s", run_id, exc)
        store.finish_muse_run(conn, run_id, ok=False, message=str(exc))
        return MuseRunResult(run_id, False, str(exc))

    summary = f"Import: {report.summary}."
    if parsed.note:
        summary = f"{parsed.note} {summary}"

    # The import already succeeded; a rating problem must not hide that.
    rated = 0
    if rate is not None and report.added:
        try:
            rated = rate()
            summary += f" Rated {rated} posts."
        except AppError as exc:
            log.warning("Rating after Muse run %d failed: %s", run_id, exc)
            summary += f" Rating failed ({exc}); the posts will be rated on the next run."
    if report.errors:
        summary += " Skipped rows: " + "; ".join(report.errors[:5])
    store.finish_muse_run(
        conn, run_id, ok=True, message=summary, added=report.added, duplicates=report.duplicates,
        skipped=len(report.errors), rated=rated, chat_url=reply.chat_url,
    )
    log.info("Muse run %d finished: %s", run_id, summary)
    return MuseRunResult(
        run_id, True, summary, report.added, report.duplicates, len(report.errors), rated, reply.chat_url
    )
