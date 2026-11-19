"""Read any forum's official public RSS feed (XenForo, phpBB, vBulletin...).

Used for forums that publish a feed and allow reading it in robots.txt
(e.g. Digital Point: forums.digitalpoint.com/forums/-/index.rss). Discourse
forums have their own reader (discourse.py) because they expose topic ids.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser

from app.domain import Post
from app.errors import SourceFetchError
from app.html_text import html_to_text
from app.http_client import PoliteHttpClient
from app.sources.base import clean_text, convert_entries

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[\w .&()-]{2,40}$")
_THREAD_ID_RE = re.compile(r"\.(\d{1,12})/?$|[?&]t=(\d{1,12})|/(\d{1,12})/?$")


class RssForumSource:
    def __init__(self, name: str, feed_url: str) -> None:
        if not _NAME_RE.match(name):
            raise ValueError(f"Invalid forum name: {name!r}")
        parsed = urlparse(feed_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"Forum feed_url must be an https link, got {feed_url!r}")
        self.name = name
        self.feed_url = feed_url
        self.host = parsed.hostname.lower()
        self._id_prefix = "forum_" + re.sub(r"[^a-z0-9]+", "_", self.host.removeprefix("www."))

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        feed = feedparser.parse(http.get(self.feed_url).content)
        if feed.bozo and not feed.entries:
            raise SourceFetchError(f"{self.name}: the feed could not be read")
        return convert_entries(self.name, feed.entries, self._to_post)

    def _to_post(self, entry: feedparser.FeedParserDict) -> Post | None:
        link = entry.get("link", "")
        parsed = urlparse(link)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == self.host or host.endswith("." + self.host.removeprefix("www."))):
            log.info("%s: skipped an entry linking outside the forum", self.name)
            return None
        match = _THREAD_ID_RE.search(parsed.path + ("?" + parsed.query if parsed.query else ""))
        thread_id = next((g for g in match.groups() if g), None) if match else None
        thread_id = thread_id or hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]

        published = entry.get("published_parsed") or entry.get("updated_parsed")
        posted_at = datetime(*published[:6], tzinfo=timezone.utc) if published else datetime.now(timezone.utc)
        return Post(
            id=f"{self._id_prefix}_{thread_id}",
            source=self.name,
            platform="forum",
            url=link,
            title=clean_text(entry.get("title", "")),
            body=clean_text(html_to_text(entry.get("summary", ""))),
            author_handle=clean_text(entry.get("author", ""))[:50],
            posted_at=posted_at,
        )
