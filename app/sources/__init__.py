"""Post sources, built from config/sources.json."""

from __future__ import annotations

import json
from pathlib import Path

from app.errors import ConfigError
from app.sources.base import Source
from app.sources.hackernews import HackerNewsSource
from app.sources.reddit import RedditSource

__all__ = ["Source", "RedditSource", "HackerNewsSource", "load_sources"]


def load_sources(path: Path) -> list[Source]:
    """Read sources.json and return one Source per subreddit / search query."""
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
        lookback = _bounded_int(hn.get("lookback_hours", 48), "lookback_hours", 1, 720)
        hits = _bounded_int(hn.get("hits_per_query", 50), "hits_per_query", 1, 100)
        for query in hn.get("queries", []):
            sources.append(HackerNewsSource(query, lookback, hits))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError(f"{path.name}: {exc}") from None

    if not sources:
        raise ConfigError(f"{path.name} lists no sources.")
    return sources


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number between {low} and {high}")
    return value
