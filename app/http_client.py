"""A polite HTTP client: one request at a time, slow, with a clear User-Agent.

Every source goes through this, so rate limits and retries live in one place.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping

import requests

from app import __version__
from app.errors import SourceFetchError

log = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 60


def build_user_agent(reddit_username: str = "") -> str:
    """User-Agent in the format Reddit asks for: platform:app:version (by /u/name)."""
    owner = f" (by /u/{reddit_username})" if reddit_username else " (personal research tool)"
    return f"python:automation-demand-finder:{__version__}{owner}"


class PoliteHttpClient:
    """GET requests with a minimum gap between them and limited retries."""

    def __init__(
        self,
        user_agent: str,
        *,
        min_interval_seconds: float = 2.0,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._min_interval = min_interval_seconds
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    def get(self, url: str, params: Mapping[str, str | int] | None = None) -> requests.Response:
        """GET `url`, retrying temporary failures. Raises SourceFetchError on failure."""
        if not url.startswith("https://"):
            raise SourceFetchError(f"Refusing non-HTTPS URL: {url}")

        last_problem = ""
        for attempt in range(self._max_retries + 1):
            self._wait_for_turn()
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                last_problem = f"network error ({type(exc).__name__})"
                log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
                self._backoff(attempt, None)
                continue

            if response.status_code in _RETRY_STATUSES:
                last_problem = f"HTTP {response.status_code}"
                log.warning("GET %s returned %d (attempt %d)", url, response.status_code, attempt + 1)
                self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code >= 400:
                raise SourceFetchError(f"{url} returned HTTP {response.status_code}")
            return response

        raise SourceFetchError(f"{url} failed after {self._max_retries + 1} tries: {last_problem}")

    def _wait_for_turn(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            remaining = self._min_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self._max_retries:
            return
        delay = 2.0 * (2**attempt)  # 2 s, 4 s, ...
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        self._sleep(min(delay, _MAX_RETRY_AFTER_SECONDS))
