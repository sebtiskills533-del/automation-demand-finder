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
from app.inbox import ingest_replies
from app.muse.browser import STALE_LOCK_SECONDS, BrowserLock, connected_context
from app.muse.client import MuseReplyText, ask_muse, read_latest_reply
from app.services import service_names
from app.muse.parse import MAX_REPLY_ROWS, POSTS, REPLIES, BlockFormat, is_complete, parse_block

log = logging.getLogger(__name__)

MAX_FOCUS_CHARS = 100
JOB_PREFIX = "FIND POSTS."  # every job line starts with this (see build_job_message)
REPLIES_PREFIX = "CHECK REPLIES."
RECOVERED_FOCUS = "(imported Muse's last reply)"
REPLY_CHECK_FOCUS = "(checked replies to me)"
RECOVERED_REPLIES_FOCUS = "(imported Muse's last replies list)"
DEFAULT_REPLY_DAYS = 14
DEFAULT_FOCUS = "any small business owner or seller who needs one of the services below"


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
    """The single line typed into the Muse side chat (the procedure is already there).
    It names the user's services (config/services.json) so Muse looks beyond automation."""
    return (
        f"{JOB_PREFIX} Focus: {focus or DEFAULT_FOCUS}. Maximum {max_posts} posts. "
        f"A match is a person asking for help with, or wanting to hire someone for, any of: {service_names()}. "
        "Use your browser (the Muse Chrome extension) to actually open and search: Quora; public Facebook "
        "groups for small businesses, local businesses and Etsy/Shopify sellers; Instagram; and the Etsy "
        "Community forums (community.etsy.com: Shop Critiques, Optimizing Your Shop, Marketing). "
        "If you cannot open them, reply PROBLEM: <what happened>. If nothing matches, "
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

    ask = ask or _asker(settings, POSTS)
    return _run(conn, settings, focus, lambda: ask(message), _posts_handler(conn, settings, rate))


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
    read = read or _latest_reader(settings, POSTS, JOB_PREFIX)
    return _run(conn, settings, RECOVERED_FOCUS, read, _posts_handler(conn, settings, rate))


def build_replies_message(days: int) -> str:
    """The single line asking Muse to check for replies to the user (format included,
    so the side chat needs no extra procedure)."""
    return (
        f"{REPLIES_PREFIX} Use your browser (the Muse Chrome extension) to open my Facebook, Instagram "
        f"and Quora notifications and find replies to MY posts and comments from the last {days} days. "
        "Read only: never reply, like, react or send messages. For each reply note: url (link to the "
        "thread), their_name, their_reply (their exact words on ONE line, in double quotes), "
        "replied_at (YYYY-MM-DD if shown). Never look up anyone's email or phone number. "
        "Reply only with the line REPLIES START, then the header line url,their_name,their_reply,replied_at, "
        "one line per reply, then REPLIES END. If there are none: NO REPLIES FOUND: <where you looked>. "
        "If something stops you: PROBLEM: <what happened>."
    )


def run_reply_check(
    conn: sqlite3.Connection,
    settings: Settings,
    days: int = DEFAULT_REPLY_DAYS,
    *,
    ask: Callable[[str], MuseReplyText] | None = None,
) -> MuseRunResult:
    """Ask Muse for replies people sent the user, and save them to the inbox."""
    _require_enabled(settings)
    if not 1 <= days <= 60:
        raise ConfigError("Check between 1 and 60 days back.")
    ask = ask or _asker(settings, REPLIES)
    message = build_replies_message(days)
    return _run(conn, settings, REPLY_CHECK_FOCUS, lambda: ask(message), _replies_handler(conn))


def import_latest_replies(
    conn: sqlite3.Connection, settings: Settings, *, read: Callable[[], MuseReplyText] | None = None
) -> MuseRunResult:
    """Save Muse's newest reply list from the chat without sending a new job."""
    _require_enabled(settings)
    read = read or _latest_reader(settings, REPLIES, REPLIES_PREFIX)
    return _run(conn, settings, RECOVERED_REPLIES_FOCUS, read, _replies_handler(conn))


# ------------------------------------------------------------- plumbing


@dataclass(frozen=True)
class _Outcome:
    summary: str
    added: int = 0
    duplicates: int = 0
    skipped: int = 0
    rated: int = 0


def _require_enabled(settings: Settings):
    if not settings.muse.enabled:
        raise ConfigError("Muse is switched off. Set MUSE_ENABLED=true in .env to use it.")
    return settings.muse


def _asker(settings: Settings, fmt: BlockFormat) -> Callable[[str], MuseReplyText]:
    """Send a job to the Muse chat in the app's Chrome and wait for this format's end signal."""
    muse = settings.muse

    def ask(text: str) -> MuseReplyText:
        with connected_context(muse) as context:
            return ask_muse(context, muse.chat_name, text, extension_id=muse.extension_id,
                            is_complete=lambda reply: is_complete(reply, fmt))

    return ask


def _latest_reader(settings: Settings, fmt: BlockFormat, job_prefix: str) -> Callable[[], MuseReplyText]:
    muse = settings.muse

    def read() -> MuseReplyText:
        with connected_context(muse) as context:
            return read_latest_reply(context, muse.chat_name, job_prefix,
                                     start=fmt.start, extension_id=muse.extension_id)

    return read


def _posts_handler(
    conn: sqlite3.Connection, settings: Settings, rate: Callable[[], int] | None
) -> Callable[[str], _Outcome]:
    def handle(text: str) -> _Outcome:
        parsed = parse_block(text, POSTS, settings.muse.max_posts)
        report = import_rows(conn, parsed.rows)
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
                log.warning("Rating after a Muse run failed: %s", exc)
                summary += f" Rating failed ({exc}); the posts will be rated on the next run."
        if report.errors:
            summary += " Skipped rows: " + "; ".join(report.errors[:5])
        return _Outcome(summary, report.added, report.duplicates, len(report.errors), rated)

    return handle


def _replies_handler(conn: sqlite3.Connection) -> Callable[[str], _Outcome]:
    def handle(text: str) -> _Outcome:
        parsed = parse_block(text, REPLIES, MAX_REPLY_ROWS)
        report = ingest_replies(conn, parsed.rows)
        summary = f"Replies: {report.summary}."
        if parsed.note:
            summary = f"{parsed.note} {summary}"
        if report.errors:
            summary += " Skipped: " + "; ".join(report.errors[:5])
        return _Outcome(summary, report.added, report.duplicates, len(report.errors))

    return handle


def _run(
    conn: sqlite3.Connection,
    settings: Settings,
    focus: str,
    get_reply: Callable[[], MuseReplyText],
    handle: Callable[[str], _Outcome],
) -> MuseRunResult:
    """Shared steps: get Muse's answer (holding the browser lock), handle it, record the run."""
    store.fail_stale_muse_runs(conn, utc_now() - timedelta(seconds=STALE_LOCK_SECONDS))
    run_id = store.start_muse_run(conn, focus)
    try:
        with BrowserLock(settings.db_path.parent / "muse.lock"):
            reply = get_reply()
        outcome = handle(reply.text)
    except AppError as exc:
        log.warning("Muse run %d failed: %s", run_id, exc)
        store.finish_muse_run(conn, run_id, ok=False, message=str(exc))
        return MuseRunResult(run_id, False, str(exc))

    store.finish_muse_run(
        conn, run_id, ok=True, message=outcome.summary, added=outcome.added,
        duplicates=outcome.duplicates, skipped=outcome.skipped, rated=outcome.rated, chat_url=reply.chat_url,
    )
    log.info("Muse run %d finished: %s", run_id, outcome.summary)
    return MuseRunResult(run_id, True, outcome.summary, outcome.added, outcome.duplicates,
                         outcome.skipped, outcome.rated, reply.chat_url)
