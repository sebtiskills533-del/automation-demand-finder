"""Every database query lives here (the "repository" layer).

Rules: always use `?` placeholders for values, and only ever put names from
fixed lists (never user text) into the SQL itself.
"""

from __future__ import annotations

import json
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
    MAX_REPLY_CHARS,
    JOB_BOARD_PLATFORMS,
    MAX_TITLE_CHARS,
    OUTCOMES,
    PATHS,
    PLATFORM_NAMES,
    REPORT_VERDICTS,
    STATUSES,
    TECH_LEVELS,
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
               (id, source, platform, url, title, body, author_handle, posted_at, collected_at, bids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                post.bids,
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
        """SELECT p.id, p.source, p.platform, p.title, p.body, p.bids
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
                delivery_max_days, contact_method, opportunity_score, model, analyzed_at, service,
                tech_level, tech_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                record.service,
                record.tech_level,
                record.tech_reason,
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

_ACTION_FIELDS = ("status", "path", "notes", "reply_draft", "answered_at", "reply_style")


def update_action(conn: sqlite3.Connection, post_id: str, **changes: str | None) -> None:
    """Change status, path, notes and/or the reply draft for a post (creating its action row).

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
    if "reply_draft" in changes:
        draft = changes["reply_draft"] or ""
        if len(draft) > MAX_REPLY_CHARS:
            raise ValueError(f"A reply can be at most {MAX_REPLY_CHARS} characters")
        changes["reply_draft"] = draft
    if changes.get("answered_at") is not None:
        try:  # must be a real time
            changes["answered_at"] = to_iso(datetime.fromisoformat(str(changes["answered_at"]).replace("Z", "+00:00")))
        except ValueError:
            raise ValueError("answered_at must be an ISO date-time") from None

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


def set_answered(conn: sqlite3.Connection, post_id: str, answered: bool) -> None:
    """Tick (or untick) "I answered this post"."""
    update_action(conn, post_id, answered_at=to_iso(utc_now()) if answered else None)


def mark_answered_if_unset(conn: sqlite3.Connection, post_id: str, when: str) -> None:
    """A reply came in for this post, so the user must have answered it, and the person replied.
    Never unticks, and never overwrites an outcome the user set (won / lost)."""
    with transaction(conn):
        conn.execute(
            """INSERT INTO actions (post_id, answered_at, outcome, outcome_at, updated_at) VALUES (?, ?, 'replied', ?, ?)
               ON CONFLICT (post_id) DO UPDATE SET
                   answered_at = COALESCE(actions.answered_at, excluded.answered_at),
                   outcome_at = CASE WHEN actions.outcome IS NULL THEN excluded.outcome_at ELSE actions.outcome_at END,
                   outcome = COALESCE(actions.outcome, 'replied')""",
            (post_id, when, when, to_iso(utc_now())),
        )


def set_outcome(conn: sqlite3.Connection, post_id: str, outcome: str | None) -> None:
    """What happened after the user answered: 'replied', 'won', 'lost', or None (still waiting).
    Setting an outcome also ticks the post as answered."""
    if outcome not in (*OUTCOMES, None):
        raise ValueError(f"Outcome must be one of {', '.join(OUTCOMES)} or empty")
    now = to_iso(utc_now())
    with transaction(conn):
        try:
            conn.execute(
                """INSERT INTO actions (post_id, outcome, outcome_at, answered_at, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (post_id) DO UPDATE SET outcome = excluded.outcome, outcome_at = excluded.outcome_at,
                       answered_at = COALESCE(actions.answered_at, excluded.answered_at),
                       updated_at = excluded.updated_at""",
                (post_id, outcome, now if outcome else None, now if outcome else None, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"No post with id {post_id!r}") from exc


def results_rows(conn: sqlite3.Connection) -> list[dict]:
    """Every post the user answered, with what is needed to see what works (Results page)."""
    rows = conn.execute(
        """SELECT p.id, p.platform, p.source, p.posted_at, p.bids, a.service, a.tech_level,
                  a.opportunity_score, ac.answered_at, ac.outcome, ac.reply_style,
                  (SELECT COUNT(*) FROM replies r WHERE r.post_id = p.id) AS replies
           FROM actions ac JOIN posts p ON p.id = ac.post_id
           LEFT JOIN analyses a ON a.post_id = p.id
           WHERE ac.answered_at IS NOT NULL
           ORDER BY ac.answered_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def fresh_gigs(conn: sqlite3.Connection, rated_since: datetime, min_score: int, max_bids: int) -> list[dict]:
    """Good job-board posts rated since `rated_since` that few people have bid on yet (for the alert)."""
    rows = conn.execute(
        f"""SELECT p.id, p.title, p.platform, p.bids, a.opportunity_score FROM analyses a
            JOIN posts p ON p.id = a.post_id LEFT JOIN actions ac ON ac.post_id = p.id
            WHERE a.analyzed_at >= ? AND a.opportunity_score >= ? AND (p.bids IS NULL OR p.bids <= ?)
              AND p.platform IN ({', '.join('?' for _ in JOB_BOARD_PLATFORMS)}) AND ac.answered_at IS NULL
            ORDER BY a.opportunity_score DESC""",
        (to_iso(rated_since), min_score, max_bids, *JOB_BOARD_PLATFORMS),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------ dashboard


@dataclass(frozen=True)
class DashboardFilters:
    """What the user picked in the dashboard sidebar. Empty tuple = no filter."""

    difficulties: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    min_score: int = 0
    min_seriousness: int = 1
    buildable_only: bool = False
    search: str = ""
    answered: bool | None = None  # True = only answered, False = only not answered
    tech_levels: tuple[str, ...] = ()
    exclude_platforms: tuple[str, ...] = ()
    max_bids: int | None = None      # gigs with at most this many bids (posts without bids still show)
    posted_since: datetime | None = None


_DASHBOARD_SELECT = """
    SELECT p.id, p.source, p.platform, p.url, p.title, p.body, p.author_handle, p.posted_at,
           a.summary, a.category, a.difficulty, a.tool, a.est_hours, a.buildable,
           a.product_potential, a.seriousness, a.seriousness_reason, a.wants_to_hire,
           a.region_hint, a.reason, a.delivery_min_days, a.delivery_max_days,
           a.contact_method, a.opportunity_score, a.model, a.service,
           COALESCE(ac.status, ?) AS status, ac.path, COALESCE(ac.notes, '') AS notes,
           COALESCE(ac.reply_draft, '') AS reply_draft, a.cluster_id, ac.answered_at,
           a.tech_level, a.tech_reason, p.bids, ac.outcome, ac.outcome_at, ac.reply_style
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
    in_list("a.service", filters.services, None)
    in_list("p.platform", filters.platforms, PLATFORM_NAMES)
    if filters.exclude_platforms:
        if not set(filters.exclude_platforms) <= set(PLATFORM_NAMES):
            raise ValueError("Invalid filter value for p.platform")
        clauses.append(f"p.platform NOT IN ({', '.join('?' for _ in filters.exclude_platforms)})")
        params.extend(filters.exclude_platforms)
    in_list("COALESCE(ac.status, 'New')", filters.statuses, STATUSES)
    in_list("a.tech_level", filters.tech_levels, TECH_LEVELS)
    if filters.buildable_only:
        clauses.append("a.buildable = 1")
    if filters.max_bids is not None:
        clauses.append("(p.bids IS NULL OR p.bids <= ?)")
        params.append(int(filters.max_bids))
    if filters.posted_since is not None:
        clauses.append("p.posted_at >= ?")
        params.append(to_iso(filters.posted_since))
    if filters.answered is not None:
        clauses.append("ac.answered_at IS NOT NULL" if filters.answered else "ac.answered_at IS NULL")
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


def get_post_details(conn: sqlite3.Connection, post_id: str) -> dict | None:
    """One analysed post with everything the detail panel shows."""
    row = conn.execute(_DASHBOARD_SELECT + " WHERE p.id = ?", (DEFAULT_STATUS, post_id)).fetchone()
    return dict(row) if row else None


def get_post_basic(conn: sqlite3.Connection, post_id: str) -> dict | None:
    """Title and text of any post, rated or not."""
    row = conn.execute("SELECT id, title, body, url, source FROM posts WHERE id = ?", (post_id,)).fetchone()
    return dict(row) if row else None


def dashboard_counts(conn: sqlite3.Connection, best_score: int, since: datetime) -> dict[str, int]:
    """Numbers for the summary row at the top of the dashboard."""
    row = conn.execute(
        """SELECT COUNT(*) AS analyzed,
                  COALESCE(SUM(a.opportunity_score >= ?), 0) AS best,
                  COALESCE(SUM(COALESCE(ac.status, 'New') IN ('Ticked', 'Building')), 0) AS active,
                  COALESCE(SUM(a.analyzed_at >= ?), 0) AS new_today
           FROM analyses a LEFT JOIN actions ac ON ac.post_id = a.post_id""",
        (best_score, to_iso(since)),
    ).fetchone()
    return dict(row)


# -------------------------------------------------------- decision reports


def save_decision_report(conn: sqlite3.Connection, report: dict) -> None:
    """`report` keys: post_id, verdict, reasons (list), plan (list of dicts), build_hours,
    delivery_min_days, delivery_max_days, contact_method, draft, model."""
    if report["verdict"] not in REPORT_VERDICTS:
        raise ValueError("Unknown verdict")
    with transaction(conn):
        conn.execute(
            """INSERT OR REPLACE INTO decision_reports
               (post_id, verdict, reasons, plan, build_hours, delivery_min_days, delivery_max_days,
                contact_method, draft, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report["post_id"], report["verdict"], json.dumps(report["reasons"]),
                json.dumps(report["plan"]), report["build_hours"], report["delivery_min_days"],
                report["delivery_max_days"], report["contact_method"], report["draft"],
                report["model"], to_iso(utc_now()),
            ),
        )


def get_decision_report(conn: sqlite3.Connection, post_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM decision_reports WHERE post_id = ?", (post_id,)).fetchone()
    if row is None:
        return None
    report = dict(row)
    report["reasons"] = json.loads(report["reasons"])
    report["plan"] = json.loads(report["plan"])
    return report


# ------------------------------------------------------------- tech level


def posts_missing_tech_level(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Rated posts whose tech level was never judged (rated before the feature), best first."""
    rows = conn.execute(
        """SELECT p.id, p.platform, p.title, a.summary FROM analyses a JOIN posts p ON p.id = a.post_id
           WHERE a.tech_level IS NULL ORDER BY a.opportunity_score DESC, p.posted_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_missing_tech_level(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM analyses WHERE tech_level IS NULL").fetchone()[0]


def set_tech_levels(conn: sqlite3.Connection, levels: Iterable[tuple[str, str, str]]) -> None:
    """(post_id, tech_level, reason) for posts judged after their analysis."""
    rows = []
    for post_id, level, reason in levels:
        if level not in TECH_LEVELS:
            raise ValueError(f"Unknown tech level {level!r}")
        rows.append((level, reason[:300], post_id))
    with transaction(conn):
        conn.executemany("UPDATE analyses SET tech_level = ?, tech_reason = ? WHERE post_id = ?", rows)


# -------------------------------------------------------------- portfolio


_CANDIDATE_SELECT = """
    SELECT p.id, p.title, p.url, p.platform, p.source, a.summary, a.service, a.category, a.tool,
           a.est_hours, a.difficulty, a.product_potential, a.seriousness, a.tech_level,
           c.name AS problem, (SELECT COUNT(*) FROM analyses x WHERE x.cluster_id = a.cluster_id) AS problem_posts
    FROM analyses a JOIN posts p ON p.id = a.post_id
    LEFT JOIN clusters c ON c.id = a.cluster_id
"""


def portfolio_candidates(conn: sqlite3.Connection, since: datetime, limit: int) -> list[dict]:
    """Recent real jobs to pick portfolio projects from: non-tech people first, then the
    problems many people share (product potential), then the most serious."""
    rows = conn.execute(
        _CANDIDATE_SELECT
        + """WHERE a.est_hours > 0 AND a.analyzed_at >= ?
           ORDER BY (a.tech_level = 'non_technical') DESC, a.product_potential DESC,
                    a.seriousness DESC, a.opportunity_score DESC
           LIMIT ?""",
        (to_iso(since), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def candidates_by_ids(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    """Rated posts for these ids, in the same order (posts that no longer exist are left out)."""
    if not ids:
        return []
    rows = conn.execute(_CANDIDATE_SELECT + f"WHERE p.id IN ({', '.join('?' for _ in ids)})", ids).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def market_posts(conn: sqlite3.Connection, since: datetime, limit: int) -> list[dict]:
    """Recent LinkedIn posts (any kind: problems people talk about, new things), newest first.
    They are not always 'jobs', so they are kept apart from portfolio_candidates."""
    rows = conn.execute(
        _CANDIDATE_SELECT
        + """WHERE p.platform = 'linkedin' AND a.analyzed_at >= ?
           ORDER BY p.posted_at DESC LIMIT ?""",
        (to_iso(since), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def save_pain_point_plan(conn: sqlite3.Connection, content: dict, engine: str, model: str) -> int:
    if engine not in ("claude", "muse", "pasted"):
        raise ValueError(f"Unknown engine {engine!r}")
    data = json.dumps(content, ensure_ascii=False)
    if len(data) > 120_000:
        raise ValueError("The analysis is too long")
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO pain_point_plans (content, engine, model, created_at) VALUES (?, ?, ?, ?)",
            (data, engine, model, to_iso(utc_now())))
    return int(cursor.lastrowid)


def latest_pain_point_plan(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT id, content, engine, model, created_at FROM pain_point_plans "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {"id": row["id"], "content": json.loads(row["content"]), "engine": row["engine"],
            "model": row["model"], "created_at": row["created_at"]}


def save_portfolio_plan(conn: sqlite3.Connection, content: dict, model: str) -> int:
    data = json.dumps(content, ensure_ascii=False)
    if len(data) > 60_000:
        raise ValueError("The portfolio plan is too long")
    with transaction(conn):
        cursor = conn.execute("INSERT INTO portfolio_plans (content, model, created_at) VALUES (?, ?, ?)",
                              (data, model, to_iso(utc_now())))
    return int(cursor.lastrowid)


def latest_portfolio_plan(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id, content, model, created_at FROM portfolio_plans ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {"id": row["id"], "content": json.loads(row["content"]), "model": row["model"],
            "created_at": row["created_at"]}


# ----------------------------------------------------------- explanations

MAX_EXPLANATION_CHARS = 20_000


def save_explanation(conn: sqlite3.Connection, post_id: str, content: dict, model: str) -> None:
    """Keep (or replace) the Roman Urdu explanation of one post."""
    data = json.dumps(content, ensure_ascii=False)
    if len(data) > MAX_EXPLANATION_CHARS:
        raise ValueError("The explanation is too long")
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO explanations (post_id, content, model, created_at) VALUES (?, ?, ?, ?)",
            (post_id, data, model, to_iso(utc_now())),
        )


def get_explanation(conn: sqlite3.Connection, post_id: str) -> dict | None:
    """{"content": {...}, "model", "created_at"} or None if never explained."""
    row = conn.execute("SELECT content, model, created_at FROM explanations WHERE post_id = ?",
                       (post_id,)).fetchone()
    if row is None:
        return None
    return {"content": json.loads(row["content"]), "model": row["model"], "created_at": row["created_at"]}


# --------------------------------------------------------------- clusters


def job_summaries_since(conn: sqlite3.Connection, since: datetime, limit: int) -> list[dict]:
    """Recent analysed posts that are real jobs (not news or showcases), newest first."""
    rows = conn.execute(
        """SELECT a.post_id, a.summary, a.category FROM analyses a
           WHERE a.analyzed_at >= ? AND (a.buildable = 1 OR a.est_hours > 0)
           ORDER BY a.analyzed_at DESC LIMIT ?""",
        (to_iso(since), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_analysis_time(conn: sqlite3.Connection) -> str | None:
    return conn.execute("SELECT MAX(analyzed_at) FROM analyses").fetchone()[0]


def replace_clusters(
    conn: sqlite3.Connection,
    clusters: Iterable[tuple[str, str, str, list[str]]],
    window_post_ids: Iterable[str],
) -> None:
    """Save a fresh grouping: (id, name, description, post_ids) per cluster.

    Posts in this window are reassigned; clusters left without posts are
    removed (with their product specs). Clusters that keep their id keep
    their spec.
    """
    window = [(pid,) for pid in window_post_ids]
    now = to_iso(utc_now())
    with transaction(conn):
        conn.executemany("UPDATE analyses SET cluster_id = NULL WHERE post_id = ?", window)
        for cluster_id, name, description, post_ids in clusters:
            conn.execute(
                """INSERT INTO clusters (id, name, description, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET name = excluded.name,
                       description = excluded.description, updated_at = excluded.updated_at""",
                (cluster_id, name, description, now),
            )
            conn.executemany(
                "UPDATE analyses SET cluster_id = ? WHERE post_id = ?",
                [(cluster_id, pid) for pid in post_ids],
            )
        conn.execute(
            "DELETE FROM clusters WHERE id NOT IN (SELECT DISTINCT cluster_id FROM analyses "
            "WHERE cluster_id IS NOT NULL)"
        )


def list_clusters(conn: sqlite3.Connection, min_posts: int = 1) -> list[dict]:
    """Problem groups, most repeated first, with averages for ranking."""
    rows = conn.execute(
        """SELECT c.id, c.name, c.description, COUNT(a.post_id) AS posts,
                  ROUND(AVG(a.product_potential), 1) AS avg_potential,
                  ROUND(AVG(a.opportunity_score)) AS avg_score,
                  ROUND(AVG(a.est_hours), 1) AS avg_hours,
                  MAX(p.posted_at) AS last_seen,
                  (SELECT COUNT(*) FROM product_specs s WHERE s.cluster_id = c.id) AS has_spec
           FROM clusters c
           JOIN analyses a ON a.cluster_id = c.id
           JOIN posts p ON p.id = a.post_id
           GROUP BY c.id HAVING COUNT(a.post_id) >= ?
           ORDER BY posts DESC, avg_potential DESC""",
        (min_posts,),
    ).fetchall()
    return [dict(r) for r in rows]


def cluster_posts(conn: sqlite3.Connection, cluster_id: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id, p.title, p.url, p.source, p.posted_at, a.summary, a.opportunity_score,
                  a.product_potential
           FROM analyses a JOIN posts p ON p.id = a.post_id
           WHERE a.cluster_id = ? ORDER BY a.opportunity_score DESC LIMIT ?""",
        (cluster_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def save_product_spec(conn: sqlite3.Connection, cluster_id: str, spec: dict, model: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO product_specs (cluster_id, spec, model, created_at) VALUES (?, ?, ?, ?)",
            (cluster_id, json.dumps(spec), model, to_iso(utc_now())),
        )


def get_product_spec(conn: sqlite3.Connection, cluster_id: str) -> dict | None:
    row = conn.execute("SELECT spec FROM product_specs WHERE cluster_id = ?", (cluster_id,)).fetchone()
    return json.loads(row[0]) if row else None


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


# --------------------------------------------------------------- replies


def save_reply(conn: sqlite3.Connection, reply: dict) -> bool:
    """Save one reply someone sent the user. False if it was already saved.
    `reply` keys: reply_key, post_id, platform, url, author, text, email, replied_at."""
    with transaction(conn):
        cursor = conn.execute(
            """INSERT OR IGNORE INTO replies
               (reply_key, post_id, platform, url, author, text, email, replied_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (reply["reply_key"], reply["post_id"], reply["platform"], reply["url"], reply["author"],
             reply["text"], reply["email"], reply["replied_at"], to_iso(utc_now())),
        )
    return cursor.rowcount == 1


def list_replies(
    conn: sqlite3.Connection,
    *,
    post_id: str | None = None,
    unseen_only: bool = False,
    seen: bool | None = None,
    platforms: tuple[str, ...] = (),
    limit: int = 200,
) -> list[dict]:
    """Replies, newest first, with the title of the post they answer (if known).

    seen: True = only ones the user ticked as seen, False = only not seen yet.
    """
    clauses, params = [], []
    if post_id is not None:
        clauses.append("r.post_id = ?")
        params.append(post_id)
    if unseen_only:
        seen = False
    if seen is not None:
        clauses.append("r.seen = ?")
        params.append(int(seen))
    if platforms:
        if not set(platforms) <= set(PLATFORM_NAMES):
            raise ValueError("Invalid platform filter")
        clauses.append(f"r.platform IN ({', '.join('?' for _ in platforms)})")
        params.extend(platforms)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        """SELECT r.*, p.title AS post_title FROM replies r
           LEFT JOIN posts p ON p.id = r.post_id""" + where
        + " ORDER BY r.received_at DESC, r.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_reply(conn: sqlite3.Connection, reply_id: int) -> dict | None:
    row = conn.execute(
        "SELECT r.*, p.title AS post_title, p.id AS linked_post FROM replies r "
        "LEFT JOIN posts p ON p.id = r.post_id WHERE r.id = ?", (reply_id,),
    ).fetchone()
    return dict(row) if row else None


def unseen_reply_count(conn: sqlite3.Connection, post_id: str | None = None) -> int:
    if post_id is None:
        return conn.execute("SELECT COUNT(*) FROM replies WHERE seen = 0").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM replies WHERE seen = 0 AND post_id = ?", (post_id,)).fetchone()[0]


def mark_replies_seen(conn: sqlite3.Connection, reply_ids: Iterable[int], seen: bool = True) -> None:
    """Tick (or untick) replies as seen. Only the user does this; opening a reply doesn't."""
    with transaction(conn):
        conn.executemany("UPDATE replies SET seen = ? WHERE id = ?", [(int(seen), int(i)) for i in reply_ids])


def answered_posts(conn: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """Posts the user answered (ticked, or that got a reply), with their reply counts.

    Posts with the newest replies come first, then the most recently answered.
    """
    rows = conn.execute(
        """SELECT p.id, p.title, p.platform, p.url, ac.answered_at,
                  COUNT(r.id) AS replies, COALESCE(SUM(r.seen = 0), 0) AS unseen,
                  MAX(r.received_at) AS last_reply
           FROM posts p
           LEFT JOIN actions ac ON ac.post_id = p.id
           LEFT JOIN replies r ON r.post_id = p.id
           WHERE ac.answered_at IS NOT NULL OR r.id IS NOT NULL
           GROUP BY p.id
           ORDER BY last_reply IS NULL, last_reply DESC, ac.answered_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def save_email_draft(conn: sqlite3.Connection, reply_id: int, subject: str, body: str) -> None:
    if len(subject) > 200 or len(body) > MAX_REPLY_CHARS:
        raise ValueError("The email is too long")
    with transaction(conn):
        conn.execute("UPDATE replies SET email_subject = ?, email_body = ? WHERE id = ?",
                     (subject, body, int(reply_id)))


DRAFT_KINDS = ("reply", "email")


def save_draft(conn: sqlite3.Connection, kind: str, owner_id: str, style: str, body: str, subject: str = "") -> None:
    """Keep one draft per writing style, so the user can compare and choose."""
    if kind not in DRAFT_KINDS:
        raise ValueError(f"Unknown draft kind {kind!r}")
    if len(body) > MAX_REPLY_CHARS or len(subject) > 200:
        raise ValueError("The draft is too long")
    with transaction(conn):
        conn.execute(
            """INSERT INTO drafts (kind, owner_id, style, subject, body, created_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (kind, owner_id, style) DO UPDATE SET subject = excluded.subject,
                   body = excluded.body, created_at = excluded.created_at""",
            (kind, str(owner_id), style, subject, body, to_iso(utc_now())),
        )


def get_drafts(conn: sqlite3.Connection, kind: str, owner_id: str) -> dict[str, dict]:
    """{style: {"subject", "body", "created_at"}} for one post's replies or one reply's emails."""
    rows = conn.execute(
        "SELECT style, subject, body, created_at FROM drafts WHERE kind = ? AND owner_id = ?",
        (kind, str(owner_id)),
    ).fetchall()
    return {r["style"]: {"subject": r["subject"], "body": r["body"], "created_at": r["created_at"]} for r in rows}


def post_urls(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(id, url) of every post, for linking replies to the post they answer."""
    return [(r[0], r[1]) for r in conn.execute("SELECT id, url FROM posts")]


# ------------------------------------------------------------ muse runs


def start_muse_run(conn: sqlite3.Connection, focus: str) -> int:
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO muse_runs (started_at, status, focus) VALUES (?, 'running', ?)",
            (to_iso(utc_now()), focus),
        )
    return int(cursor.lastrowid)


def finish_muse_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    ok: bool,
    message: str,
    added: int = 0,
    duplicates: int = 0,
    skipped: int = 0,
    rated: int = 0,
    chat_url: str = "",
) -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE muse_runs SET finished_at = ?, status = ?, message = ?, added = ?,
                   duplicates = ?, skipped = ?, rated = ?, chat_url = ?
               WHERE id = ?""",
            (to_iso(utc_now()), "ok" if ok else "failed", message[:1000], added, duplicates,
             skipped, rated, chat_url[:500], run_id),
        )


def fail_stale_muse_runs(conn: sqlite3.Connection, older_than: datetime) -> int:
    """Runs still 'running' after the time limit were interrupted (closed window, crash)."""
    with transaction(conn):
        cursor = conn.execute(
            """UPDATE muse_runs SET status = 'failed', finished_at = ?,
                   message = 'Interrupted: the run stopped without finishing.'
               WHERE status = 'running' AND started_at < ?""",
            (to_iso(utc_now()), to_iso(older_than)),
        )
    return cursor.rowcount


def recent_muse_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM muse_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


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


def save_login_session(
    conn: sqlite3.Connection, token_hash: str, password_tag: str, now: datetime, expires: datetime
) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM login_sessions WHERE expires_at < ?", (to_iso(now),))  # tidy up
        conn.execute(
            "INSERT INTO login_sessions (token_hash, password_tag, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, password_tag, to_iso(now), to_iso(expires)),
        )


def find_login_session(conn: sqlite3.Connection, token_hash: str, now: datetime) -> dict | None:
    row = conn.execute(
        "SELECT * FROM login_sessions WHERE token_hash = ? AND expires_at > ?", (token_hash, to_iso(now))
    ).fetchone()
    return dict(row) if row else None


def delete_login_session(conn: sqlite3.Connection, token_hash: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM login_sessions WHERE token_hash = ?", (token_hash,))


def failed_logins_since(conn: sqlite3.Connection, since: datetime) -> int:
    """Failed attempts after `since` that came after the last successful login."""
    return conn.execute(
        """SELECT COUNT(*) FROM login_attempts
           WHERE succeeded = 0 AND attempted_at >= ?
             AND attempted_at > COALESCE(
                 (SELECT MAX(attempted_at) FROM login_attempts WHERE succeeded = 1), '')""",
        (to_iso(since),),
    ).fetchone()[0]
