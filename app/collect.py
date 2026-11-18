"""Step 1: collect new posts from every source and save them.

Run on its own with:  python -m app.collect
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from app import store
from app.errors import SourceFetchError
from app.http_client import PoliteHttpClient
from app.sources import Source

log = logging.getLogger(__name__)


@dataclass
class CollectReport:
    new_per_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_new(self) -> int:
        return sum(self.new_per_source.values())


def collect(conn: sqlite3.Connection, sources: list[Source], http: PoliteHttpClient) -> CollectReport:
    """Fetch every source. One broken source never stops the others."""
    report = CollectReport()
    for source in sources:
        try:
            posts = source.fetch(http)
        except SourceFetchError as exc:
            log.error("Collect failed for %s: %s", source.name, exc)
            report.errors[source.name] = str(exc)
            continue
        new = sum(store.save_post(conn, post) for post in posts)
        report.new_per_source[source.name] = new
        log.info("%s: %d fetched, %d new", source.name, len(posts), new)
    return report


def main() -> int:
    from app.cli import open_runtime

    with open_runtime() as runtime:
        report = collect(runtime.conn, runtime.sources, runtime.http)
    for name, count in report.new_per_source.items():
        print(f"{name}: {count} new")
    for name, problem in report.errors.items():
        print(f"{name}: FAILED ({problem})")
    return 1 if report.errors and not report.new_per_source else 0


if __name__ == "__main__":
    raise SystemExit(main())
