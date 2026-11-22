"""A daily copy of the database, kept for 7 days (data/backups/finder-YYYY-MM-DD.db).

Made at the end of each run (the first run of the day makes it; later runs that day skip).
Uses SQLite's own backup, which is safe while the dashboard has the database open, and
writes to a temporary file first so a half-written copy never replaces a good one.
A backup problem is logged and never fails the run. data/ is never committed to git.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_DAYS = 7
_PREFIX = "finder-"


def daily_backup(conn: sqlite3.Connection, backup_dir: Path, today: date, keep: int = KEEP_DAYS) -> str:
    """Make today's copy if it doesn't exist yet, remove the oldest beyond `keep`; return a summary."""
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{_PREFIX}{today.isoformat()}.db"
        made = False
        if not target.exists():
            partial = target.with_suffix(".partial")
            with sqlite3.connect(partial) as copy:
                conn.backup(copy)
            copy.close()
            partial.replace(target)
            made = True
        backups = sorted(backup_dir.glob(f"{_PREFIX}*.db"))
        for old in backups[:-keep]:
            old.unlink()
        kept = min(len(backups), keep)
        return f"{'saved' if made else 'already saved today'} ({kept} daily copies kept in {backup_dir.name}/)"
    except (OSError, sqlite3.Error) as exc:
        log.warning("Backup failed: %s", exc)
        return f"FAILED ({exc})"
