"""Search recent Hacker News stories through the official Algolia API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.domain import Post
from app.errors import SourceFetchError
from app.html_text import html_to_text
from app.http_client import PoliteHttpClient
from app.sources.base import clean_text, convert_entries

log = logging.getLogger(__name__)

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://news.ycombinator.com/item?id={}"


# "ask_hn" = people asking the community something (where requests live);
# "story" = every submission, which is mostly news and project launches.
ALLOWED_TAGS = ("ask_hn", "story")


class HackerNewsSource:
    def __init__(
        self,
        query: str,
        lookback_hours: int = 168,
        hits_per_query: int = 50,
        tags: str = "ask_hn",
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        query = query.strip()
        if not 1 <= len(query) <= 100:
            raise ValueError("A Hacker News query must be 1-100 characters")
        if tags not in ALLOWED_TAGS:
            raise ValueError(f"Hacker News tags must be one of {', '.join(ALLOWED_TAGS)}")
        self.query = query
        self.lookback_hours = lookback_hours
        self.hits_per_query = hits_per_query
        self.tags = tags
        self._now = now
        label = "Ask HN" if tags == "ask_hn" else "Hacker News"
        self.name = f"{label}: {query}"

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        since = int((self._now() - timedelta(hours=self.lookback_hours)).timestamp())
        response = http.get(
            SEARCH_URL,
            params={
                "query": self.query,
                "tags": self.tags,
                "numericFilters": f"created_at_i>{since}",
                "hitsPerPage": self.hits_per_query,
            },
        )
        try:
            hits = response.json()["hits"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceFetchError(f"{self.name}: unexpected response from Hacker News") from exc

        if not isinstance(hits, list):
            raise SourceFetchError(f"{self.name}: unexpected response from Hacker News")
        return convert_entries(self.name, hits, self._to_post)

    def _to_post(self, hit: dict) -> Post | None:
        object_id = str(hit.get("objectID", ""))
        if not object_id.isdigit() or not hit.get("title"):
            log.info("%s: skipped a hit with no id or title", self.name)
            return None
        created = hit.get("created_at_i")
        posted_at = (
            datetime.fromtimestamp(int(created), tz=timezone.utc)
            if isinstance(created, int)
            else self._now()
        )
        return Post(
            id=f"hn_{object_id}",
            source="Hacker News",
            platform="hackernews",
            url=ITEM_URL.format(object_id),
            title=clean_text(hit["title"]),
            body=clean_text(html_to_text(hit.get("story_text") or "")),
            author_handle=clean_text(str(hit.get("author") or ""))[:50],
            posted_at=posted_at,
        )
