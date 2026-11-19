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
from app.muse.client import MuseReplyText, ask_muse, read_latest_reply
from app.muse.parse import parse_muse_reply, reply_is_complete

log = logging.getLogger(__name__)

MAX_FOCUS_CHARS = 100
JOB_PREFIX = "FIND POSTS."  # every job line starts with this (see build_job_message)
RECOVERED_FOCUS = "(imported Muse's last reply)"
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
        f"{JOB_PREFIX} Focus: {focus or DEFAULT_FOCUS}. Maximum {max_posts} posts. "
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
    muse = _require_enabled(settings)
    focus = clean_focus(focus)
    message = build_job_message(focus, muse.max_posts)

    if ask is None:
        def ask(text: str) -> MuseReplyText:
            with connected_context(muse) as context:
                return ask_muse(context, muse.chat_name, text, extension_id=muse.extension_id,
                                is_complete=reply_is_complete)

    return _run(conn, settings, focus, lambda: ask(message), rate)


def import_latest_reply(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    read: Callable[[], MuseReplyText] | None = None,
    rate: Callable[[], int] | None = None,
) -> MuseRunResult:
    """Import Muse's reply to the most recent job WITHOUT sending a new one.

    For when Muse finished after a run had stopped reading (it is safe to repeat:
    posts already in the app are counted as duplicates, never added twice).
    """
    muse = _require_enabled(settings)
    if read is None:
        def read() -> MuseReplyText:
            with connected_context(muse) as context:
                return read_latest_reply(context, muse.chat_name, JOB_PREFIX, extension_id=muse.extension_id)

    return _run(conn, settings, RECOVERED_FOCUS, read, rate)


def _require_enabled(settings: Settings):
    if not settings.muse.enabled:
        raise ConfigError("Muse is switched off. Set MUSE_ENABLED=true in .env to use it.")
    return settings.muse


def _run(
    conn: sqlite3.Connection,
    settings: Settings,
    focus: str,
    get_reply: Callable[[], MuseReplyText],
    rate: Callable[[], int] | None,
) -> MuseRunResult:
    """Shared steps: get the reply (holding the browser lock), parse, import, rate, record."""
    muse = settings.muse
    store.fail_stale_muse_runs(conn, utc_now() - timedelta(seconds=STALE_LOCK_SECONDS))
    run_id = store.start_muse_run(conn, focus)
    try:
        with BrowserLock(settings.db_path.parent / "muse.lock"):
            reply = get_reply()
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
