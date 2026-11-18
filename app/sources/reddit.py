"""Read a subreddit's public RSS feed (no login or API key needed)."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser

from app.domain import Post
from app.errors import SourceFetchError
from app.html_text import html_to_text
from app.http_client import PoliteHttpClient
from app.sources.base import clean_text

log = logging.getLogger(__name__)

SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_POST_ID_RE = re.compile(r"^t3_([a-z0-9]{1,16})$")


class RedditSource:
    def __init__(self, subreddit: str, posts_per_feed: int = 25) -> None:
        if not SUBREDDIT_RE.match(subreddit):
            raise ValueError(f"Invalid subreddit name: {subreddit!r}")
        self.subreddit = subreddit
        self.posts_per_feed = posts_per_feed
        self.name = f"r/{subreddit}"

    @property
    def feed_url(self) -> str:
        return f"https://www.reddit.com/r/{self.subreddit}/new/.rss"

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        response = http.get(self.feed_url, params={"limit": self.posts_per_feed})
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise SourceFetchError(f"{self.name}: the feed could not be read")

        posts: list[Post] = []
        for entry in feed.entries:
            post = self._to_post(entry)
            if post is not None:
                posts.append(post)
        return posts

    def _to_post(self, entry: feedparser.FeedParserDict) -> Post | None:
        id_match = _POST_ID_RE.match(entry.get("id", ""))
        link = entry.get("link", "")
        if not id_match or not _is_reddit_https_url(link):
            log.info("%s: skipped an entry with an unexpected id or link", self.name)
            return None

        html = entry.content[0].value if entry.get("content") else entry.get("summary", "")
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        posted_at = (
            datetime.fromtimestamp(calendar.timegm(published), tz=timezone.utc)
            if published
            else datetime.now(timezone.utc)
        )
        author = entry.get("author", "").removeprefix("/u/").removeprefix("u/")
        return Post(
            id=f"reddit_{id_match.group(1)}",
            source=self.name,
            platform="reddit",
            url=link,
            title=clean_text(entry.get("title", "")),
            body=clean_text(html_to_text(html)),
            author_handle=author[:50],
            posted_at=posted_at,
        )


def _is_reddit_https_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "reddit.com" or host.endswith(".reddit.com"))
