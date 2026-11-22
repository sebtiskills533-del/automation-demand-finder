"""Muse as a thinking engine: send it a question, read back a JSON answer.

Used by the pain-points analysis (app/painpoints.py) so the user can pick Muse instead of Claude.
Same safety as the collection jobs: started by the user, one job at a time (BrowserLock), recorded in
muse_runs, never retried automatically. Muse is told NOT to browse for these jobs.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from app import painpoints
from app.config import Settings
from app.errors import ConfigError, MuseReplyError
from app.muse.browser import connected_context
from app.muse.client import MuseReplyText, ask_muse, read_latest_reply
from app.muse.job import MuseRunResult, _Outcome, _require_enabled, _run
from app.muse.parse import ANALYSIS_START, analysis_is_complete, parse_json_block

log = logging.getLogger(__name__)

PAINPOINTS_FOCUS = "(pain points analysis)"
RECOVERED_PAINPOINTS_FOCUS = "(imported Muse's last pain points answer)"


def _handler(conn: sqlite3.Connection, items: list[dict]) -> Callable[[str], _Outcome]:
    def handle(text: str) -> _Outcome:
        try:
            content = painpoints.content_from_data(parse_json_block(text), items)
        except ValueError as exc:  # shape problems are shown like any other Muse problem
            raise MuseReplyError(str(exc)) from None
        painpoints.save_answer(conn, content, "muse")
        return _Outcome(f"Pain points: {len(content['pain_points'])} found, "
                        f"{len(content['market_trends'])} market trends.")

    return handle


def run_muse_painpoints(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    ask: Callable[[str], MuseReplyText] | None = None,
) -> MuseRunResult:
    """Ask Muse to analyse the collected requests into pain points; save its answer."""
    muse = _require_enabled(settings)
    try:
        items = painpoints.select_items(conn, jobs=painpoints.MUSE_JOBS, market=painpoints.MUSE_MARKET)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    painpoints.remember_items(conn, items)
    message = painpoints.muse_message(items)

    def default_ask(text: str) -> MuseReplyText:
        with connected_context(muse) as context:
            return ask_muse(context, muse.chat_name, text, extension_id=muse.extension_id,
                            is_complete=analysis_is_complete)

    ask = ask or default_ask
    return _run(conn, settings, PAINPOINTS_FOCUS, lambda: ask(message), _handler(conn, items))


def import_latest_painpoints(
    conn: sqlite3.Connection, settings: Settings, *, read: Callable[[], MuseReplyText] | None = None
) -> MuseRunResult:
    """Save Muse's newest analysis from the chat without sending a new question
    (the numbers are matched to the posts of the last question the app prepared)."""
    muse = _require_enabled(settings)
    try:
        items = painpoints.remembered_items(conn)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None

    def default_read() -> MuseReplyText:
        with connected_context(muse) as context:
            return read_latest_reply(context, muse.chat_name, painpoints.PROMPT_PREFIX,
                                     start=ANALYSIS_START, extension_id=muse.extension_id)

    return _run(conn, settings, RECOVERED_PAINPOINTS_FOCUS, read or default_read, _handler(conn, items))


def import_pasted_answer(conn: sqlite3.Connection, text: str) -> dict:
    """The user pasted Muse's answer: tie it to the posts of the last prepared question and save it."""
    content = painpoints.content_from_data(parse_json_block(text), painpoints.remembered_items(conn))
    painpoints.save_answer(conn, content, "pasted")
    return content

