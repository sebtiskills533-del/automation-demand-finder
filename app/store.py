"""Every database query lives here (the "repository" layer).

Rules: always use `?` placeholders for values, and only ever put names from
fixed lists (never user text) into the SQL itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db import transaction
from app.domain import (
    DEFAULT_STATUS,
    DIFFICULTIES,
    MAX_BODY_CHARS,
    MAX_NOTES_CHARS,
    MAX_TITLE_CHARS,
    PATHS,
    STATUSES,
    AnalysisRecord,
    Post,
    to_iso,
    utc_now,
)

# ---------------------------------------------------------------- posts


def save_post(conn: sqlite3.Connection, post: Post) -> bool:
    """Save a post. Returns False (and changes nothing) if it already exists."""
    with transaction(conn):
        cursor = conn.execute(
            """INSERT OR IGNORE INTO posts
               (id, source, platform, url, title, body, author_handle, posted_at, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                post.id,
                post.source,
                post.platform,
                post.url,
                post.title[:MAX_TITLE_CHARS],
                post.body[:MAX_BODY_CHARS],
                post.author_handle,
                to_iso(post.posted_at),
                to_iso(utc_now()),
            ),
        )
    return cursor.rowcount == 1


def posts_pending_filter(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Posts the keyword filter hasn't looked at yet."""
    return conn.execute(
        "SELECT id, title, body FROM posts WHERE passed_filter IS NULL LIMIT ?", (limit,)
    ).fetchall()


def set_filter_results(conn: sqlite3.Connection, results: Iterable[tuple[str, bool]]) -> None:
    """Record pass (True) / fail (False) for many posts in one transaction."""
    with transaction(conn):
        conn.executemany(
            "UPDATE posts SET passed_filter = ? WHERE id = ?",
            [(int(passed), post_id) for post_id, passed in results],
        )


def posts_pending_analysis(
    conn: sqlite3.Connection, limit: int, max_attempts: int
) -> list[sqlite3.Row]:
    """Newest posts that passed the filter, have no analysis, and haven't failed too often."""
    return conn.execute(
        """SELECT p.id, p.source, p.platform, p.title, p.body
           FROM posts p
           LEFT JOIN analyses a ON a.post_id = p.id
           WHERE p.passed_filter = 1 AND a.post_id IS NULL AND p.analysis_attempts < ?
           ORDER BY p.posted_at DESC
           LIMIT ?""",
        (max_attempts, limit),
    ).fetchall()


def record_analysis_failure(conn: sqlite3.Connection, post_id: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE posts SET analysis_attempts = analysis_attempts + 1 WHERE id = ?", (post_id,)
        )


def count_posts(conn: sqlite3.Connection) -> dict[str, int]:
    """Totals for the status line: collected, passed filter, analysed."""
    row = conn.execute(
        """SELECT COUNT(*) AS collected,
                  COALESCE(SUM(passed_filter = 1), 0) AS passed,
                  (SELECT COUNT(*) FROM analyses) AS analyzed
           FROM posts"""
    ).fetchone()
    return dict(row)


# ------------------------------------------------------------- analyses


def save_analysis(conn: sqlite3.Connection, record: AnalysisRecord) -> None:
    """Insert or replace the analysis for one post."""
    with transaction(conn):
        conn.execute(
            """INSERT OR REPLACE INTO analyses
               (post_id, summary, category, difficulty, tool, est_hours, buildable,
                product_potential, seriousness, seriousness_reason, wants_to_hire,
                shares_own_website, region_hint, reason, delivery_min_days,
                delivery_max_days, contact_method, opportunity_score, model, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.post_id,
                record.summary,
                record.category,
                record.difficulty,
                record.tool,
                record.est_hours,
                int(record.buildable),
                record.product_potential,
                record.seriousness,
                record.seriousness_reason,
                int(record.wants_to_hire),
                int(record.shares_own_website),
                record.region_hint,
                record.reason,
                record.delivery_min_days,
                record.delivery_max_days,
                record.contact_method,
                record.opportunity_score,
                record.model,
                to_iso(utc_now()),
            ),
        )


def small_skipped_post_ids(conn: sqlite3.Connection, max_hours: float) -> list[str]:
    """Posts rated low/medium and small, yet not buildable (i.e. judged 'not a job')."""
    return [
        r[0]
        for r in conn.execute(
            """SELECT post_id FROM analyses
               WHERE buildable = 0 AND difficulty IN ('low', 'medium') AND est_hours <= ?""",
            (max_hours,),
        )
    ]


def delete_analyses(conn: sqlite3.Connection, post_ids: Iterable[str]) -> int:
    """Remove analyses so those posts are rated again on the next run.
    The posts themselves and the user's ticks/notes are kept."""
    ids = [(pid,) for pid in post_ids]
    with transaction(conn):
        conn.executemany("DELETE FROM analyses WHERE post_id = ?", ids)
        conn.executemany("UPDATE posts SET analysis_attempts = 0 WHERE id = ?", ids)
    return len(ids)


# -------------------------------------------------------------- actions

_ACTION_FIELDS = ("status", "path", "notes")


def update_action(conn: sqlite3.Connection, post_id: str, **changes: str | None) -> None:
    """Change status, path and/or notes for a post (creating its action row).

    Example: update_action(conn, "reddit_abc", status="Ticked", path="Product")
    """
    if not changes:
        return
    unknown = set(changes) - set(_ACTION_FIELDS)
    if unknown:
        raise ValueError(f"Unknown action field(s): {', '.join(sorted(unknown))}")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"Status must be one of {', '.join(STATUSES)}")
    if "path" in changes and changes["path"] not in (*PATHS, None):
        raise ValueError(f"Path must be one of {', '.join(PATHS)} or empty")
    if "notes" in changes:
        notes = changes["notes"] or ""
        if len(notes) > MAX_NOTES_CHARS:
            raise ValueError(f"Notes can be at most {MAX_NOTES_CHARS} characters")
        changes["notes"] = notes

    columns = [field for field in _ACTION_FIELDS if field in changes]  # fixed names only
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns)
    values = [changes[c] for c in columns]
    with transaction(conn):
        try:
            conn.execute(
                f"""INSERT INTO actions (post_id, {", ".join(columns)}, updated_at)
                    VALUES (?, {placeholders}, ?)
                    ON CONFLICT (post_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at""",
                (post_id, *values, to_iso(utc_now())),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"No post with id {post_id!r}") from exc


# ------------------------------------------------------------ dashboard


@dataclass(frozen=True)
class DashboardFilters:
    """What the user picked in the dashboard sidebar. Empty tuple = no filter."""

    difficulties: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    min_score: int = 0
    min_seriousness: int = 1
    buildable_only: bool = False
    search: str = ""


_DASHBOARD_SELECT = """
    SELECT p.id, p.source, p.platform, p.url, p.title, p.body, p.author_handle, p.posted_at,
           a.summary, a.category, a.difficulty, a.tool, a.est_hours, a.buildable,
           a.product_potential, a.seriousness, a.seriousness_reason, a.wants_to_hire,
           a.region_hint, a.reason, a.delivery_min_days, a.delivery_max_days,
           a.contact_method, a.opportunity_score, a.model,
           COALESCE(ac.status, ?) AS status, ac.path, COALESCE(ac.notes, '') AS notes
    FROM posts p
    JOIN analyses a ON a.post_id = p.id
    LEFT JOIN actions ac ON ac.post_id = p.id
"""


def _dashboard_where(filters: DashboardFilters) -> tuple[str, list[object]]:
    clauses: list[str] = ["a.opportunity_score >= ?", "a.seriousness >= ?"]
    params: list[object] = [filters.min_score, filters.min_seriousness]

    def in_list(column: str, values: tuple[str, ...], allowed: Iterable[str] | None) -> None:
        if not values:
            return
        if allowed is not None and not set(values) <= set(allowed):
            raise ValueError(f"Invalid filter value for {column}")
        clauses.append(f"{column} IN ({', '.join('?' for _ in values)})")
        params.extend(values)

    in_list("a.difficulty", filters.difficulties, DIFFICULTIES)
    in_list("p.source", filters.sources, None)
    in_list("COALESCE(ac.status, 'New')", filters.statuses, STATUSES)
    if filters.buildable_only:
        clauses.append("a.buildable = 1")
    search = filters.search.strip()[:100]
    if search:
        # Escape LIKE wildcards so the search text is matched literally.
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(p.title LIKE ? ESCAPE '\\' OR a.summary LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])
    return " WHERE " + " AND ".join(clauses), params


def get_dashboard_rows(
    conn: sqlite3.Connection, filters: DashboardFilters, limit: int = 50, offset: int = 0
) -> list[dict]:
    """One page of analysed posts, best opportunities first, then newest."""
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("limit must be 1-500 and offset >= 0")
    where, params = _dashboard_where(filters)
    rows = conn.execute(
        _DASHBOARD_SELECT + where
        + " ORDER BY a.opportunity_score DESC, p.posted_at DESC LIMIT ? OFFSET ?",
        (DEFAULT_STATUS, *params, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def count_dashboard_rows(conn: sqlite3.Connection, filters: DashboardFilters) -> int:
    where, params = _dashboard_where(filters)
    return conn.execute(
        """SELECT COUNT(*) FROM posts p
           JOIN analyses a ON a.post_id = p.id
           LEFT JOIN actions ac ON ac.post_id = p.id""" + where,
        params,
    ).fetchone()[0]


def distinct_sources(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT source FROM posts ORDER BY source")]


# ---------------------------------------------------------------- usage


def log_usage(
    conn: sqlite3.Connection,
    task: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO llm_usage (created_at, task, model, input_tokens, output_tokens, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (to_iso(utc_now()), task, model, input_tokens, output_tokens, cost_usd),
        )


def spend_since(conn: sqlite3.Connection, since: datetime) -> float:
    """Total AI cost in USD since `since`."""
    return conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE created_at >= ?", (to_iso(since),)
    ).fetchone()[0]


def usage_totals_since(conn: sqlite3.Connection, since: datetime, task: str) -> dict[str, float]:
    """Tokens and cost for one task since `since` (used for a run's summary)."""
    row = conn.execute(
        """SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cost_usd), 0) AS cost_usd
           FROM llm_usage WHERE created_at >= ? AND task = ?""",
        (to_iso(since), task),
    ).fetchone()
    return dict(row)


def usage_by_model_since(conn: sqlite3.Connection, since: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,
                  SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd
           FROM llm_usage WHERE created_at >= ? GROUP BY model ORDER BY cost_usd DESC""",
        (to_iso(since),),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------- settings


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# --------------------------------------------------------------- logins


def record_login_attempt(conn: sqlite3.Connection, succeeded: bool, at: datetime) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO login_attempts (attempted_at, succeeded) VALUES (?, ?)",
            (to_iso(at), int(succeeded)),
        )
        # Keep the table small: attempts older than 30 days are useless.
        conn.execute(
            "DELETE FROM login_attempts WHERE attempted_at < ?",
            (to_iso(at - timedelta(days=30)),),
        )


def failed_logins_since(conn: sqlite3.Connection, since: datetime) -> int:
    """Failed attempts after `since` that came after the last successful login."""
    return conn.execute(
        """SELECT COUNT(*) FROM login_attempts
           WHERE succeeded = 0 AND attempted_at >= ?
             AND attempted_at > COALESCE(
                 (SELECT MAX(attempted_at) FROM login_attempts WHERE succeeded = 1), '')""",
        (to_iso(since),),
    ).fetchone()[0]
