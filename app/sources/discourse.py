"""Read a Discourse community forum's official public "latest topics" RSS feed.

Many tool communities (n8n, Make, ...) run on Discourse, which publishes
/latest.rss for everyone. Their robots.txt allows /latest.rss; per-topic and
per-category feeds are disallowed, so only /latest.rss is used.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser

from app.domain import Post
from app.errors import SourceFetchError
from app.html_text import html_to_text
from app.http_client import PoliteHttpClient
from app.sources.base import clean_text, convert_entries

log = logging.getLogger(__name__)

_TOPIC_ID_RE = re.compile(r"(?:-topic-|/)(\d{1,12})/?$")
_NAME_RE = re.compile(r"^[\w .&-]{2,40}$")


class DiscourseSource:
    def __init__(self, name: str, base_url: str, skip_categories: tuple[str, ...] = ()) -> None:
        if not _NAME_RE.match(name):
            raise ValueError(f"Invalid forum name: {name!r}")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
            raise ValueError(f"Forum base_url must look like https://community.example.com, got {base_url!r}")
        self.name = name
        self.host = parsed.hostname
        self.feed_url = f"https://{self.host}/latest.rss"
        self.skip_categories = {c.strip().lower() for c in skip_categories}
        self._id_prefix = "forum_" + re.sub(r"[^a-z0-9]+", "_", self.host.lower())

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        response = http.get(self.feed_url)
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise SourceFetchError(f"{self.name}: the feed could not be read")
        return convert_entries(self.name, feed.entries, self._to_post)

    def _to_post(self, entry: feedparser.FeedParserDict) -> Post | None:
        categories = {t.get("term", "").strip().lower() for t in entry.get("tags", [])}
        if categories & self.skip_categories:
            return None  # showcases, news, freelancers offering help, ...

        link = entry.get("link", "")
        parsed = urlparse(link)
        id_match = _TOPIC_ID_RE.search(entry.get("id", "")) or _TOPIC_ID_RE.search(parsed.path)
        if parsed.scheme != "https" or parsed.hostname != self.host or not id_match:
            log.info("%s: skipped an entry with an unexpected id or link", self.name)
            return None

        published = entry.get("published")
        posted_at = parsedate_to_datetime(published) if published else datetime.now(timezone.utc)
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        return Post(
            id=f"{self._id_prefix}_{id_match.group(1)}",
            source=self.name,
            platform="forum",
            url=link,
            title=clean_text(entry.get("title", "")),
            body=clean_text(html_to_text(entry.get("summary", ""))),
            author_handle=clean_text(entry.get("author", ""))[:50],
            posted_at=posted_at,
        )
