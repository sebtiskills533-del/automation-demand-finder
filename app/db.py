"""SQLite connection and schema.

The schema is versioned with SQLite's `user_version`. To change it later,
append a new script to MIGRATIONS; never edit one that has already shipped.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_V1 = """
CREATE TABLE posts (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    platform          TEXT NOT NULL,
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    body              TEXT NOT NULL DEFAULT '',
    author_handle     TEXT NOT NULL DEFAULT '',
    posted_at         TEXT NOT NULL,
    collected_at      TEXT NOT NULL,
    passed_filter     INTEGER CHECK (passed_filter IN (0, 1)),
    analysis_attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_posts_filter_pending ON posts (passed_filter) WHERE passed_filter IS NULL;
CREATE INDEX idx_posts_passed ON posts (passed_filter, posted_at);
CREATE INDEX idx_posts_source ON posts (source);

CREATE TABLE analyses (
    post_id            TEXT PRIMARY KEY REFERENCES posts (id) ON DELETE CASCADE,
    summary            TEXT NOT NULL,
    category           TEXT NOT NULL,
    difficulty         TEXT NOT NULL CHECK (difficulty IN ('low', 'medium', 'high')),
    tool               TEXT NOT NULL,
    est_hours          REAL NOT NULL CHECK (est_hours >= 0),
    buildable          INTEGER NOT NULL CHECK (buildable IN (0, 1)),
    product_potential  INTEGER NOT NULL CHECK (product_potential BETWEEN 1 AND 5),
    seriousness        INTEGER NOT NULL CHECK (seriousness BETWEEN 1 AND 5),
    seriousness_reason TEXT NOT NULL,
    wants_to_hire      INTEGER NOT NULL CHECK (wants_to_hire IN (0, 1)),
    shares_own_website INTEGER NOT NULL CHECK (shares_own_website IN (0, 1)),
    region_hint        TEXT NOT NULL,
    reason             TEXT NOT NULL,
    delivery_min_days  INTEGER,
    delivery_max_days  INTEGER,
    contact_method     TEXT NOT NULL,
    opportunity_score  INTEGER NOT NULL CHECK (opportunity_score BETWEEN 0 AND 100),
    model              TEXT NOT NULL,
    analyzed_at        TEXT NOT NULL,
    cluster_id         TEXT
);
CREATE INDEX idx_analyses_score ON analyses (opportunity_score DESC);

CREATE TABLE actions (
    post_id     TEXT PRIMARY KEY REFERENCES posts (id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'New'
                CHECK (status IN ('New', 'Ticked', 'Building', 'Done', 'Sold', 'Skipped')),
    path        TEXT CHECK (path IN ('Product', 'Reply', 'Custom job')),
    notes       TEXT NOT NULL DEFAULT '',
    reply_draft TEXT,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_actions_status ON actions (status);

CREATE TABLE llm_usage (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    task          TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      REAL NOT NULL
);
CREATE INDEX idx_llm_usage_created ON llm_usage (created_at);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE login_attempts (
    id           INTEGER PRIMARY KEY,
    attempted_at TEXT NOT NULL,
    succeeded    INTEGER NOT NULL CHECK (succeeded IN (0, 1))
);
CREATE INDEX idx_login_attempts_time ON login_attempts (attempted_at);
"""

_SCHEMA_V2 = """
CREATE TABLE muse_runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    focus       TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    added       INTEGER NOT NULL DEFAULT 0,
    duplicates  INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    rated       INTEGER NOT NULL DEFAULT 0,
    chat_url    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_muse_runs_started ON muse_runs (started_at DESC);
"""

MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1, _SCHEMA_V2)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database (creating its folder), with safe defaults switched on."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # dashboard can read while a run writes
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema to the latest version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS, start=1):
        if version > current:
            with transaction(conn):
                # Python's sqlite3 doesn't open a transaction for CREATE
                # statements by itself; BEGIN makes the migration atomic.
                conn.execute("BEGIN")
                for statement in _split_statements(script):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {version:d}")


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open, migrate, use and always close the database."""
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit everything inside the block, or roll it all back on error."""
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _split_statements(script: str) -> list[str]:
    # executescript() would commit on its own; run statements one by one so
    # a migration is all-or-nothing. The schema has no ';' inside strings.
    return [s.strip() for s in script.split(";") if s.strip()]
