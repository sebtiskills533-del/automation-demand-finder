"""Post sources, built from config/sources.json."""

from __future__ import annotations

import json
from pathlib import Path

from app.errors import ConfigError
from app.sources.base import Source
from app.sources.discourse import DiscourseSource
from app.sources.hackernews import HackerNewsSource
from app.sources.reddit import RedditSource
from app.sources.rss_forum import RssForumSource
from app.sources.stackexchange import StackExchangeSource

__all__ = [
    "Source", "RedditSource", "HackerNewsSource", "DiscourseSource", "StackExchangeSource",
    "RssForumSource", "load_sources",
]


def load_sources(path: Path) -> list[Source]:
    """Read sources.json and return one Source per subreddit, search query and forum."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Sources file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid JSON (line {exc.lineno}): {exc.msg}") from None

    sources: list[Source] = []
    try:
        reddit = config.get("reddit", {})
        per_feed = _bounded_int(reddit.get("posts_per_feed", 25), "posts_per_feed", 1, 100)
        for subreddit in reddit.get("subreddits", []):
            sources.append(RedditSource(subreddit, per_feed))

        hn = config.get("hackernews", {})
        lookback = _bounded_int(hn.get("lookback_hours", 168), "lookback_hours", 1, 720)
        hits = _bounded_int(hn.get("hits_per_query", 50), "hits_per_query", 1, 100)
        tags = hn.get("tags", "ask_hn")
        for query in hn.get("queries", []):
            sources.append(HackerNewsSource(query, lookback, hits, tags))

        se = config.get("stackexchange", {})
        se_lookback = _bounded_int(se.get("lookback_hours", 168), "lookback_hours", 1, 720)
        se_page = _bounded_int(se.get("page_size", 50), "page_size", 1, 100)
        for site in se.get("sites", []):
            for query in se.get("queries", []):
                sources.append(StackExchangeSource(site, query, se_lookback, se_page))

        for forum in config.get("rss_forums", []):
            sources.append(RssForumSource(forum["name"], forum["feed_url"]))

        for forum in config.get("forums", []):
            skip = forum.get("skip_categories", [])
            if not isinstance(skip, list) or not all(isinstance(c, str) for c in skip):
                raise ValueError("skip_categories must be a list of names")
            sources.append(DiscourseSource(forum["name"], forum["base_url"], tuple(skip)))
    except KeyError as exc:
        raise ConfigError(f"{path.name}: a forum entry is missing {exc}") from None
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError(f"{path.name}: {exc}") from None

    if not sources:
        raise ConfigError(f"{path.name} lists no sources.")
    names = [s.name for s in sources]
    if len(set(names)) != len(names):
        raise ConfigError(f"{path.name} lists the same source twice.")
    return sources


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number between {low} and {high}")
    return value
