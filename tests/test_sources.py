import json
from datetime import datetime, timezone

import pytest

from app.errors import ConfigError, SourceFetchError
from app.http_client import PoliteHttpClient
from app.sources import HackerNewsSource, RedditSource, load_sources
from tests.fakes import FakeClock, FakeResponse, FakeSession

REDDIT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <author><name>/u/shopowner</name></author>
    <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Every day I copy orders. Email me: owner@shop.com or 555-123-4567&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; submitted by /u/shopowner</content>
    <id>t3_abc123</id>
    <link href="https://www.reddit.com/r/Etsy/comments/abc123/help/" />
    <published>2026-11-18T09:30:00+00:00</published>
    <title>Is there a way to automate Etsy orders?</title>
  </entry>
  <entry>
    <id>t3_evil1</id>
    <link href="https://evil.example.com/phish" />
    <title>Bad link</title>
  </entry>
  <entry>
    <id>not-a-post-id</id>
    <link href="https://www.reddit.com/r/Etsy/comments/x/" />
    <title>Bad id</title>
  </entry>
</feed>"""


def http_with(*responses):
    clock = FakeClock()
    session = FakeSession(*responses)
    return PoliteHttpClient("ua", session=session, sleep=clock.sleep, clock=clock.time), session


def test_reddit_parses_and_cleans_posts():
    http, session = http_with(FakeResponse(200, REDDIT_FEED))
    posts = RedditSource("Etsy").fetch(http)
    assert session.calls[0][0] == "https://www.reddit.com/r/Etsy/new/.rss"
    assert len(posts) == 1  # the bad link and bad id are skipped
    post = posts[0]
    assert post.id == "reddit_abc123"
    assert post.source == "r/Etsy"
    assert post.author_handle == "shopowner"
    assert post.posted_at == datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
    assert "owner@shop.com" not in post.body and "555-123-4567" not in post.body
    assert "submitted by" not in post.body
    assert post.body.startswith("Every day I copy orders")


def test_reddit_empty_feed_is_fine():
    http, _ = http_with(FakeResponse(200, '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'))
    assert RedditSource("Etsy").fetch(http) == []


def test_reddit_garbage_response_raises():
    http, _ = http_with(FakeResponse(200, "<html><body>blocked"))
    with pytest.raises(SourceFetchError):
        RedditSource("Etsy").fetch(http)


@pytest.mark.parametrize("name", ["", "a", "bad name", "../etc", "x" * 22])
def test_reddit_rejects_invalid_subreddit(name):
    with pytest.raises(ValueError):
        RedditSource(name)


def hn_response(hits):
    return FakeResponse(200, {"hits": hits})


def test_hackernews_parses_hits():
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    http, session = http_with(
        hn_response(
            [
                {
                    "objectID": "4242",
                    "title": "Ask HN: How do I automate invoices?",
                    "story_text": "<p>Call me at +1 555 123 4567</p>",
                    "author": "pg_fan",
                    "created_at_i": 1763460000,
                },
                {"objectID": "abc", "title": "bad id"},
                {"objectID": "77", "title": ""},
            ]
        )
    )
    posts = HackerNewsSource("automate", lookback_hours=24, now=lambda: now).fetch(http)
    assert len(posts) == 1
    post = posts[0]
    assert post.id == "hn_4242"
    assert post.url == "https://news.ycombinator.com/item?id=4242"
    assert "555" not in post.body
    params = session.calls[0][1]
    assert params["query"] == "automate"
    assert params["numericFilters"] == f"created_at_i>{int(now.timestamp()) - 24 * 3600}"


def test_hackernews_bad_json_raises():
    http, _ = http_with(FakeResponse(200, b"not json"))
    with pytest.raises(SourceFetchError):
        HackerNewsSource("automate").fetch(http)


def test_hackernews_query_validation():
    with pytest.raises(ValueError):
        HackerNewsSource("   ")


def test_load_sources_from_real_config():
    from app.config import CONFIG_DIR

    sources = load_sources(CONFIG_DIR / "sources.json")
    names = [s.name for s in sources]
    assert "r/smallbusiness" in names and "Hacker News: automate" in names


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"reddit": {"subreddits": ["bad name"]}}),
        json.dumps({"reddit": {"subreddits": ["ok"], "posts_per_feed": 1000}}),
        json.dumps({"reddit": {"subreddits": []}}),
        json.dumps({"reddit": "oops"}),
    ],
)
def test_load_sources_invalid_config(tmp_path, content):
    path = tmp_path / "sources.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_sources(path)


def test_load_sources_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_sources(tmp_path / "missing.json")
