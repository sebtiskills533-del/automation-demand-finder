"""Step 2: a cheap keyword check that drops posts which aren't requests.

Only posts that pass are sent to Claude, which keeps AI costs down.
Run on its own with:  python -m app.prefilter
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import store
from app.errors import ConfigError

log = logging.getLogger(__name__)

_BATCH_SIZE = 500


@dataclass(frozen=True)
class FilterReport:
    checked: int
    passed: int


def load_phrases(path: Path) -> list[str]:
    """Read one phrase per line; blank lines and lines starting with # are ignored."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise ConfigError(f"Phrase file not found: {path}") from None
    phrases = [line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#")]
    if not phrases:
        raise ConfigError(f"{path.name} contains no phrases.")
    return phrases


def build_matcher(phrases: list[str]) -> re.Pattern[str]:
    """One regex for all phrases. Whole words only: 'script' won't match 'description'."""
    alternatives = "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def run_prefilter(conn: sqlite3.Connection, phrases: list[str]) -> FilterReport:
    """Mark every unchecked post as passed or not."""
    matcher = build_matcher(phrases)
    checked = passed = 0
    while True:
        batch = store.posts_pending_filter(conn, _BATCH_SIZE)
        if not batch:
            break
        results = [(row["id"], bool(matcher.search(f"{row['title']}\n{row['body']}"))) for row in batch]
        store.set_filter_results(conn, results)
        checked += len(results)
        passed += sum(ok for _, ok in results)
    log.info("Pre-filter: %d checked, %d passed", checked, passed)
    return FilterReport(checked, passed)


def main() -> int:
    from app.cli import open_runtime

    with open_runtime() as runtime:
        report = run_prefilter(runtime.conn, load_phrases(runtime.settings.phrases_file))
    print(f"Checked {report.checked} posts, {report.passed} look like requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
