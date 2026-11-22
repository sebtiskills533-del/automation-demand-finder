"""Job boards and freelance marketplaces with official public feeds or APIs.

Only boards whose terms allow it (checked 2026-11-20):
- Freelancer.com   public projects API (freelance gigs with budgets)
- Remote OK        public API; terms: link back to the job and name Remote OK (the dashboard does)
- Remotive         public API; personal use, never republished
- Himalayas        public jobs API (search)
- We Work Remotely category RSS feeds
- Working Nomads   public jobs feed
Upwork closed its public feeds and blocks bots; Fiverr, PeoplePerHour, Guru and
others have no public feed, so they are not read automatically.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser

from app.domain import Post
from app.errors import SourceFetchError
from app.html_text import html_to_text
from app.http_client import PoliteHttpClient
from app.sources.base import clean_text, convert_entries

log = logging.getLogger(__name__)

_QUERY_RE = re.compile(r"^[\w .&+-]{2,60}$")
_SLUG_RE = re.compile(r"^[a-z0-9-]{3,80}$")


def _check_query(query: str) -> str:
    query = query.strip()
    if not _QUERY_RE.match(query):
        raise ValueError(f"Invalid job search term: {query!r}")
    return query


def _on_host(url: str, *hosts: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)


def _when(value: object) -> datetime:
    """Epoch seconds or an ISO date -> aware UTC datetime (now if missing/odd)."""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        if isinstance(value, str) and value:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return datetime.now(timezone.utc)


def _join(value: object) -> str:
    """A list (or a single string) of labels -> 'a, b'."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return ""


def _facts(**facts: object) -> str:
    """'Job listing · Company: X · Type: Full Time' — gives Claude the key details."""
    parts = [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in facts.items() if v not in (None, "", [], 0)]
    return "Job listing · " + " · ".join(parts)


def money_range(low: object, high: object, currency: str = "") -> str:
    """'600 to 1,500 INR'. Written with 'to' and thousands separators so a budget can
    never look like a phone number to the privacy filter (e.g. '10000.0-20000.0')."""
    def fmt(value: object) -> str:
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return "?"

    return f"{fmt(low)} to {fmt(high)} {currency}".strip()


def _post(platform: str, source: str, key: str, url: str, title: str, facts: str, body_html: str,
          when: object, author: str = "", bids: int | None = None) -> Post:
    return Post(
        id=f"{platform}_{key}",
        source=source,
        platform=platform,
        url=url,
        title=clean_text(title)[:300],
        body=clean_text(f"{facts}\n\n{html_to_text(body_html or '')}"),
        author_handle=clean_text(author)[:50],
        posted_at=_when(when),
        bids=bids,
    )


def _bid_count(value: object) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if 0 <= count < 100_000 else None


class _JsonJobSource:
    """Shared fetch for JSON boards: get -> list of items -> Posts."""

    name: str

    def _get_items(self, http: PoliteHttpClient) -> list:  # pragma: no cover - overridden
        raise NotImplementedError

    def _to_post(self, item: dict) -> Post | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        try:
            items = self._get_items(http)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SourceFetchError(f"{self.name}: unexpected response") from exc
        if not isinstance(items, list):
            raise SourceFetchError(f"{self.name}: unexpected response")
        return convert_entries(self.name, [i for i in items if isinstance(i, dict)], self._to_post)


class FreelancerSource(_JsonJobSource):
    URL = "https://www.freelancer.com/api/projects/0.1/projects/active/"

    def __init__(self, query: str, per_query: int = 20) -> None:
        self.query = _check_query(query)
        self.per_query = per_query
        self.name = f"Freelancer.com: {self.query}"

    def _get_items(self, http):
        data = http.get(self.URL, params={"query": self.query, "limit": self.per_query,
                                          "full_description": "true"}).json()
        return data["result"]["projects"]

    def _to_post(self, item: dict) -> Post | None:
        seo_url = str(item.get("seo_url") or "")
        if not item.get("id") or not seo_url or item.get("nonpublic"):
            return None
        budget = item.get("budget") or {}
        currency = (item.get("currency") or {}).get("code", "")
        amount = money_range(budget.get("minimum"), budget.get("maximum"), currency)
        bids = _bid_count((item.get("bid_stats") or {}).get("bid_count"))
        facts = _facts(budget=amount, type=item.get("type"), bids=bids)
        return _post("freelancer", "Freelancer.com", str(item["id"]), f"https://www.freelancer.com/projects/{seo_url}",
                     item.get("title", ""), facts, item.get("description") or item.get("preview_description", ""),
                     item.get("time_submitted") or item.get("submitdate"), bids=bids)


class RemoteOkSource(_JsonJobSource):
    URL = "https://remoteok.com/api"

    def __init__(self, tag: str) -> None:
        self.tag = _check_query(tag)
        self.name = f"Remote OK: {self.tag}"

    def _get_items(self, http):
        data = http.get(self.URL, params={"tag": self.tag}).json()
        return data[1:]  # the first item is Remote OK's legal notice

    def _to_post(self, item: dict) -> Post | None:
        url = str(item.get("url") or "")
        if not item.get("id") or not _on_host(url, "remoteok.com"):
            return None
        facts = _facts(company=item.get("company"), location=item.get("location"),
                       salary=money_range(item.get("salary_min"), item.get("salary_max"), "USD") if item.get("salary_max") else "")
        return _post("remoteok", "Remote OK", str(item["id"]), url, item.get("position", ""), facts,
                     item.get("description", ""), item.get("epoch") or item.get("date"))


class RemotiveSource(_JsonJobSource):
    URL = "https://remotive.com/api/remote-jobs"

    def __init__(self, search: str, limit: int = 30) -> None:
        self.search = _check_query(search)
        self.limit = limit
        self.name = f"Remotive: {self.search}"

    def _get_items(self, http):
        return http.get(self.URL, params={"search": self.search, "limit": self.limit}).json()["jobs"]

    def _to_post(self, item: dict) -> Post | None:
        url = str(item.get("url") or "")
        if not item.get("id") or not _on_host(url, "remotive.com"):
            return None
        facts = _facts(company=item.get("company_name"), type=item.get("job_type"), salary=item.get("salary"),
                       location=item.get("candidate_required_location"))
        return _post("remotive", "Remotive", str(item["id"]), url, item.get("title", ""), facts,
                     item.get("description", ""), item.get("publication_date"))


class HimalayasSource(_JsonJobSource):
    URL = "https://himalayas.app/jobs/api/search"

    def __init__(self, search: str, limit: int = 20) -> None:
        self.search = _check_query(search)
        self.limit = limit
        self.name = f"Himalayas: {self.search}"

    def _get_items(self, http):
        return http.get(self.URL, params={"q": self.search, "limit": self.limit}).json()["jobs"]

    def _to_post(self, item: dict) -> Post | None:
        url = next((str(u) for u in (item.get("guid"), item.get("applicationLink"))
                    if isinstance(u, str) and u.startswith("https://")), "")
        if not url:
            return None
        facts = _facts(company=item.get("companyName"), type=item.get("employmentType"),
                       seniority=_join(item.get("seniority")),
                       location=_join(item.get("locationRestrictions")) or "Anywhere")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return _post("himalayas", "Himalayas", key, url, item.get("title", ""), facts,
                     item.get("description") or item.get("excerpt", ""), item.get("pubDate"))


class WorkingNomadsSource(_JsonJobSource):
    URL = "https://www.workingnomads.com/api/exposed_jobs/"
    name = "Working Nomads"

    def _get_items(self, http):
        return http.get(self.URL).json()

    def _to_post(self, item: dict) -> Post | None:
        url = str(item.get("url") or "")
        if not _on_host(url, "workingnomads.com"):
            return None
        match = re.search(r"/(\d+)/?$", urlparse(url).path)
        key = match.group(1) if match else hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        facts = _facts(company=item.get("company_name"), category=item.get("category_name"),
                       location=item.get("location"))
        return _post("workingnomads", "Working Nomads", key, url, item.get("title", ""), facts,
                     item.get("description", ""), item.get("pub_date"))


class WeWorkRemotelySource:
    """A We Work Remotely category RSS feed."""

    def __init__(self, category: str) -> None:
        if not _SLUG_RE.match(category):
            raise ValueError(f"Invalid We Work Remotely category: {category!r}")
        self.category = category
        self.name = f"We Work Remotely: {category}"

    @property
    def feed_url(self) -> str:
        return f"https://weworkremotely.com/categories/{self.category}.rss"

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        feed = feedparser.parse(http.get(self.feed_url).content)
        if feed.bozo and not feed.entries:
            raise SourceFetchError(f"{self.name}: the feed could not be read")
        return convert_entries(self.name, feed.entries, self._to_post)

    def _to_post(self, entry: feedparser.FeedParserDict) -> Post | None:
        url = entry.get("link", "")
        if not _on_host(url, "weworkremotely.com"):
            return None
        published = entry.get("published_parsed")
        when = datetime(*published[:6], tzinfo=timezone.utc).isoformat() if published else None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        facts = _facts(region=entry.get("region"), type=entry.get("type"))
        return _post("weworkremotely", "We Work Remotely", key, url, entry.get("title", ""), facts,
                     entry.get("summary", ""), when)


def build_job_sources(config: dict, bounded_int: Callable[[object, str, int, int], int]) -> list:
    """Sources from the "job_boards" section of sources.json."""
    boards = config.get("job_boards") or {}
    sources: list = []
    fl = boards.get("freelancer") or {}
    per_query = bounded_int(fl.get("per_query", 20), "per_query", 1, 50)
    sources += [FreelancerSource(q, per_query) for q in fl.get("queries", [])]
    sources += [RemoteOkSource(t) for t in (boards.get("remoteok") or {}).get("tags", [])]
    sources += [RemotiveSource(s) for s in (boards.get("remotive") or {}).get("searches", [])]
    sources += [HimalayasSource(s) for s in (boards.get("himalayas") or {}).get("searches", [])]
    sources += [WeWorkRemotelySource(c) for c in (boards.get("weworkremotely") or {}).get("categories", [])]
    if (boards.get("workingnomads") or {}).get("enabled"):
        sources.append(WorkingNomadsSource())
    return sources
