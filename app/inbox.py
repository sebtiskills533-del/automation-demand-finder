"""Replies people sent to the user (on Facebook, Instagram, Quora...), from Muse
or typed in by hand.

Privacy rule: an email address is kept ONLY when the person wrote it in their
reply to the user (they chose to share it so the user can contact them). It is
never searched for anywhere else. Phone numbers are always removed.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from app import store
from app.domain import platform_for_host, to_iso, utc_now
from app.importer import link_key, parse_date
from app.privacy import extract_email, strip_contact_details

MAX_REPLY_TEXT = 2_000
_FB_PATH_ID = re.compile(r"/(?:posts|permalink)/(\w+)")
_FB_QUERY_IDS = ("multi_permalinks", "story_fbid", "fbid")


@dataclass
class InboxReport:
    added: int = 0
    duplicates: int = 0
    with_email: int = 0
    linked: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.added} new replies", f"{self.linked} matched to your posts",
                 f"{self.with_email} with an email they shared"]
        if self.duplicates:
            parts.append(f"{self.duplicates} already seen")
        if self.errors:
            parts.append(f"{len(self.errors)} skipped")
        return ", ".join(parts)


def post_anchor(url: str) -> str:
    """What identifies the post a link belongs to. Facebook shows one post under
    many link shapes (group ?multi_permalinks=, /posts/<id>, comment links), so
    there the post number is used; elsewhere the normalised link without query."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("facebook.com"):
        query = parse_qs(parsed.query)
        for name in _FB_QUERY_IDS:
            if query.get(name):
                return f"fb:{query[name][0]}"
        match = _FB_PATH_ID.search(parsed.path)
        if match:
            return f"fb:{match.group(1)}"
    return link_key(parsed._replace(query="")) if parsed.hostname else url


def ingest_replies(
    conn: sqlite3.Connection, rows: Iterable[Mapping[str, object]], now: Callable[[], datetime] = utc_now
) -> InboxReport:
    """Save replies (rows with url, their_name, their_reply, optional replied_at)."""
    report = InboxReport()
    anchors = {post_anchor(url): post_id for post_id, url in store.post_urls(conn)}
    moment = now()
    for number, row in enumerate(rows, start=1):
        try:
            reply = _to_reply(row, anchors, moment)
        except ValueError as exc:
            report.errors.append(f"Reply {number}: {exc}")
            continue
        if store.save_reply(conn, reply):
            report.added += 1
            report.with_email += reply["email"] is not None
            report.linked += reply["post_id"] is not None
            if reply["post_id"]:
                store.mark_answered_if_unset(conn, reply["post_id"], to_iso(moment))
        else:
            report.duplicates += 1
    return report


def add_manual_reply(conn: sqlite3.Connection, url: str, their_name: str, their_reply: str) -> InboxReport:
    """A reply the user copied in by hand."""
    return ingest_replies(conn, [{"url": url, "their_name": their_name, "their_reply": their_reply}])


def _to_reply(row: Mapping[str, object], anchors: dict[str, str], now: datetime) -> dict:
    url = str(row.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or len(url) > 2_000:
        raise ValueError("url must be a full https:// link")
    raw_text = str(row.get("their_reply") or "").strip()
    if not raw_text:
        raise ValueError("the reply text is empty")
    author = strip_contact_details(str(row.get("their_name") or "").strip())[:80]

    # Only an address the person wrote in this reply (never the separate column alone).
    email = extract_email(raw_text)
    text = strip_contact_details(raw_text)[:MAX_REPLY_TEXT]
    replied_raw = str(row.get("replied_at") or "").strip()
    replied_at = to_iso(parse_date(replied_raw, now)) if replied_raw else None

    anchor = post_anchor(url)
    key = hashlib.sha256(f"{anchor}|{author.lower()}|{text[:300].lower()}".encode()).hexdigest()
    return {
        "reply_key": key,
        "post_id": anchors.get(anchor),
        "platform": platform_for_host(parsed.hostname),
        "url": url,
        "author": author,
        "text": text,
        "email": email,
        "replied_at": replied_at,
    }
