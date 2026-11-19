"""Search recent questions on Stack Exchange sites through the official API.

No key is needed for low volume (300 requests per day per IP; this app uses
a handful). The API's "backoff" field is always obeyed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urlparse

from app.domain import Post
from app.errors import SourceFetchError
from app.html_text import html_to_text
from app.http_client import PoliteHttpClient
from app.sources.base import clean_text, convert_entries

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.stackexchange.com/2.3/search/advanced"
_SITE_RE = re.compile(r"^[a-z][a-z0-9.]{1,40}$")
_ALLOWED_HOST_SUFFIXES = (
    "stackexchange.com", "stackoverflow.com", "superuser.com", "serverfault.com", "askubuntu.com",
)


class StackExchangeSource:
    def __init__(
        self,
        site: str,
        query: str,
        lookback_hours: int = 168,
        page_size: int = 50,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not _SITE_RE.match(site):
            raise ValueError(f"Invalid Stack Exchange site: {site!r}")
        query = query.strip()
        if not 1 <= len(query) <= 100:
            raise ValueError("A Stack Exchange query must be 1-100 characters")
        self.site = site
        self.query = query
        self.lookback_hours = lookback_hours
        self.page_size = page_size
        self._now = now
        self.name = f"Stack Exchange {site}: {query}"

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        since = int((self._now() - timedelta(hours=self.lookback_hours)).timestamp())
        response = http.get(
            SEARCH_URL,
            params={
                "order": "desc",
                "sort": "creation",
                "q": self.query,
                "site": self.site,
                "fromdate": since,
                "pagesize": self.page_size,
                "filter": "withbody",
            },
        )
        try:
            data = response.json()
            items = data["items"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceFetchError(f"{self.name}: unexpected response from Stack Exchange") from exc
        if not isinstance(items, list):
            raise SourceFetchError(f"{self.name}: unexpected response from Stack Exchange")

        # The API asks clients to wait this many seconds before the next call.
        backoff = data.get("backoff")
        if isinstance(backoff, int) and backoff > 0:
            http.pause(backoff)
        if data.get("quota_remaining") == 0:
            log.warning("Stack Exchange daily quota used up; it resets at midnight UTC")

        return convert_entries(self.name, items, self._to_post)

    def _to_post(self, item: dict) -> Post | None:
        if item.get("closed_date"):
            return None  # closed questions can't be answered
        question_id = item.get("question_id")
        link = str(item.get("link", ""))
        parsed = urlparse(link)
        host = parsed.hostname or ""
        if (
            not isinstance(question_id, int)
            or parsed.scheme != "https"
            or not any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES)
        ):
            log.info("%s: skipped an item with an unexpected id or link", self.name)
            return None
        created = item.get("creation_date")
        posted_at = (
            datetime.fromtimestamp(created, tz=timezone.utc) if isinstance(created, int) else self._now()
        )
        owner = item.get("owner") or {}
        return Post(
            id=f"se_{self.site}_{question_id}",
            source=f"Stack Exchange: {self.site}",
            platform="stackexchange",
            url=link,
            title=clean_text(unescape(str(item.get("title", "")))),
            body=clean_text(html_to_text(str(item.get("body", "")))),
            author_handle=clean_text(unescape(str(owner.get("display_name", ""))))[:50],
            posted_at=posted_at,
        )
