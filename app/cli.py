"""Shared start-up for the command-line scripts: settings, logs, database,
HTTP client and (when needed) the Claude client. Errors are shown in plain
English instead of a traceback."""

from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import NoReturn

from app import db, store
from app.config import Settings, load_settings, require_api_key
from app.errors import AppError
from app.http_client import PoliteHttpClient, build_user_agent
from app.llm import ClaudeClient
from app.logging_setup import setup_logging
from app.models_registry import ModelRegistry, load_registry
from app.sources import Source, load_sources

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    conn: sqlite3.Connection
    sources: list[Source]
    http: PoliteHttpClient
    registry: ModelRegistry
    llm: ClaudeClient | None


def build_llm(settings: Settings, conn: sqlite3.Connection) -> ClaudeClient:
    """A Claude client that records every call's tokens and cost in the database."""

    def record(task: str, model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        store.log_usage(conn, task, model, input_tokens, output_tokens, cost)
        log.info("Claude %s/%s: %d in, %d out, $%.5f", task, model, input_tokens, output_tokens, cost)

    return ClaudeClient(require_api_key(settings), record)


@contextmanager
def open_runtime(need_llm: bool = False) -> Iterator[Runtime]:
    """Set everything up; on a known problem print it and exit with code 2."""
    try:
        settings = load_settings()
        setup_logging(settings.log_dir, settings.log_level)
        registry = load_registry(settings.models_file)
        sources = load_sources(settings.sources_file)
    except AppError as exc:
        _fail(exc)

    with db.open_db(settings.db_path) as conn:
        try:
            llm = build_llm(settings, conn) if need_llm else None
            runtime = Runtime(
                settings=settings,
                conn=conn,
                sources=sources,
                http=PoliteHttpClient(build_user_agent(settings.reddit_username)),
                registry=registry,
                llm=llm,
            )
            yield runtime
        except AppError as exc:
            log.exception("Run stopped")
            _fail(exc)
        except Exception:
            # A bug, not a known problem: keep the traceback in logs/app.log.
            log.exception("Unexpected error")
            raise


def _fail(exc: AppError) -> NoReturn:
    print(f"Problem: {exc}", file=sys.stderr)
    raise SystemExit(2)
