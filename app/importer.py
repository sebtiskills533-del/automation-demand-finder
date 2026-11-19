"""Bring in posts you found yourself (any platform) so Claude can rate them.

Accepts CSV or JSON with these fields (only url and title are required):
    url, title, text, source, author, posted_at
"text" may also be called "body" or "content". Imported posts skip the
keyword filter (you already picked them) and wait for the next analysis.

Command line:  .venv\\Scripts\\python -m app.importer my_posts.csv
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from app import store
from app.errors import AppError
from app.domain import MAX_BODY_CHARS, MAX_TITLE_CHARS, PLATFORM_NAMES, Post, platform_for_host, utc_now
from app.privacy import strip_contact_details

MAX_IMPORT_ROWS = 500
MAX_IMPORT_BYTES = 2_000_000
MAX_URL_CHARS = 2_000
TEMPLATE_CSV = "url,title,text,source,author,posted_at\n"

_TEXT_KEYS = ("text", "body", "content")


@dataclass
class ImportReport:
    added: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.added} added", f"{self.duplicates} already in the app"]
        if self.errors:
            parts.append(f"{len(self.errors)} skipped")
        return ", ".join(parts)


def parse_import(text: str) -> list[dict]:
    """CSV or JSON text -> list of row dicts. Raises ValueError with a clear reason."""
    text = text.lstrip("﻿").strip()
    if not text:
        raise ValueError("The file or text is empty.")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Not valid JSON (line {exc.lineno}): {exc.msg}") from None
        if isinstance(data, dict):
            data = data.get("posts")
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError('JSON must be a list of posts, e.g. [{"url": "...", "title": "..."}]')
        rows = data
    else:
        reader = csv.DictReader(io.StringIO(text))
        headers = {(h or "").strip().lower() for h in reader.fieldnames or []}
        if not {"url", "title"} <= headers:
            raise ValueError("The CSV needs a header line with at least: url,title")
        rows = [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"Too many posts ({len(rows)}); import at most {MAX_IMPORT_ROWS} at a time.")
    return rows


def row_to_post(row: Mapping[str, object], now: datetime) -> Post:
    """One imported row -> a cleaned Post. Raises ValueError with the reason."""
    url = _text(row.get("url"))
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("url must be a full https:// link")
    if len(url) > MAX_URL_CHARS:
        raise ValueError("url is too long")
    title = strip_contact_details(_text(row.get("title")))[:MAX_TITLE_CHARS]
    if not title:
        raise ValueError("title is empty")
    body = next((_text(row.get(k)) for k in _TEXT_KEYS if _text(row.get(k))), "")

    platform = platform_for_host(parsed.hostname)
    source = strip_contact_details(_text(row.get("source")))[:60] or f"Imported: {PLATFORM_NAMES[platform]}"
    return Post(
        id=_post_id(parsed),
        source=source,
        platform=platform,
        url=urlunparse(parsed._replace(fragment="")),
        title=title,
        body=strip_contact_details(body)[:MAX_BODY_CHARS],
        author_handle=strip_contact_details(_text(row.get("author")))[:50],
        posted_at=_parse_date(_text(row.get("posted_at")), now),
    )


def import_rows(
    conn: sqlite3.Connection, rows: Iterable[Mapping[str, object]], now: Callable[[], datetime] = utc_now
) -> ImportReport:
    """Save valid rows; collect a reason for every row that was skipped."""
    report = ImportReport()
    moment = now()
    for number, row in enumerate(rows, start=1):
        try:
            post = row_to_post(row, moment)
        except ValueError as exc:
            report.errors.append(f"Row {number}: {exc}")
            continue
        if store.save_post(conn, post):
            # You chose these posts yourself, so they skip the keyword filter.
            store.set_filter_results(conn, [(post.id, True)])
            report.added += 1
        else:
            report.duplicates += 1
    return report


def import_text(conn: sqlite3.Connection, text: str, now: Callable[[], datetime] = utc_now) -> ImportReport:
    """Parse pasted/uploaded CSV, JSON, or a copied Muse answer (POSTS START ... POSTS END)."""
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError(f"That's too much at once (limit {MAX_IMPORT_BYTES // 1_000_000} MB).")
    # Imported here, not at the top: app.muse.parse itself builds on this module.
    from app.muse.parse import POSTS_START_RE, parse_muse_reply

    if POSTS_START_RE.search(text):
        try:
            rows = parse_muse_reply(text, MAX_IMPORT_ROWS).rows
        except AppError as exc:
            raise ValueError(str(exc)) from None
    else:
        rows = parse_import(text)
    return import_rows(conn, rows, now)


def decode_upload(data: bytes) -> str:
    """Uploaded file bytes -> text, with a clear message for non-UTF-8 files."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError(f"The file is too big (limit {MAX_IMPORT_BYTES // 1_000_000} MB).")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Save the file as UTF-8 (in Excel: Save As -> 'CSV UTF-8').") from None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _post_id(parsed) -> str:
    """Same link (ignoring www., letter case of the host and #fragment) -> same id."""
    host = parsed.hostname.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if host.endswith("quora.com"):
        # Quora shows one question at /Question and at /unanswered/Question.
        path = path.removeprefix("/unanswered")
    key = f"{host}{path}?{parsed.query}"
    return "import_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _parse_date(raw: str, now: datetime) -> datetime:
    if not raw:
        return now
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("posted_at must look like 2026-11-18 or 2026-11-18T09:30") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return min(moment, now)  # a date in the future is a typo


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python -m app.importer path\\to\\posts.csv")
        return 2
    from app.cli import open_runtime

    path = Path(args[0])
    try:
        text = decode_upload(path.read_bytes())
    except FileNotFoundError:
        print(f"File not found: {path}")
        return 1
    except ValueError as exc:
        print(f"Problem: {exc}")
        return 1
    with open_runtime() as runtime:
        try:
            report = import_text(runtime.conn, text)
        except ValueError as exc:
            print(f"Problem: {exc}")
            return 1
    print(f"Import finished: {report.summary}.")
    for error in report.errors:
        print(f"  {error}")
    if report.added:
        print("Run  .venv\\Scripts\\python -m app.analyze  (or run_daily.py) to rate them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
