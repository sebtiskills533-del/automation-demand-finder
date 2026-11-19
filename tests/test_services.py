"""Services the user offers (automation, Etsy, SEO, local SEO) and the sources for them."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app import store
from app.analyze import SYSTEM_PROMPT, PostAnalysis, to_record
from app.config import CONFIG_DIR
from app.errors import ConfigError
from app.http_client import PoliteHttpClient
from app.muse.job import build_job_message
from app.prefilter import build_matcher, load_phrases
from app.services import OTHER, badge_for, load_services, offer_summary, service_ids
from app.sources import RssForumSource, load_sources
from app.store import DashboardFilters
from app.ui.table import build_table
from tests.conftest import make_analysis, make_post
from tests.fakes import FakeClock, FakeResponse, FakeSession
from tests.test_analyze import GOOD


def test_real_services_file():
    services = load_services()
    assert [s.id for s in services] == ["automation", "etsy", "seo", "local_seo"]
    assert service_ids()[-1] == OTHER
    assert "Google Business Profile" in offer_summary()
    assert badge_for("etsy") == "🛍️ Etsy" and badge_for("nope") == "• Other"


@pytest.mark.parametrize(
    "data",
    ["{bad", {"services": []}, {"services": [{"id": "a b", "name": "x"}]},
     {"services": [{"id": "other", "name": "x"}]}, {"services": [{"name": "no id"}]},
     {"services": [{"id": "seo", "name": "a"}, {"id": "seo", "name": "b"}]}],
)
def test_invalid_services_file(tmp_path, data):
    path = tmp_path / "services.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_services(path)


def test_analysis_prompt_and_answer_cover_the_services():
    assert "Etsy shop help" in SYSTEM_PROMPT and "Local SEO" in SYSTEM_PROMPT
    assert "how do I rank higher on Google Maps" in SYSTEM_PROMPT
    record = to_record("reddit_1", "reddit", PostAnalysis(**{**GOOD, "service": "local_seo"}), "m")
    assert record.service == "local_seo"
    with pytest.raises(ValueError):
        PostAnalysis(**{**GOOD, "service": "plumbing"})


def test_keyword_filter_lets_seo_and_etsy_requests_through():
    matcher = build_matcher(load_phrases(CONFIG_DIR / "phrases.txt"))
    for text in ("My Etsy shop gets no sales", "How do I rank on Google Maps?",
                 "Need help with local SEO", "Google Business Profile not showing"):
        assert matcher.search(text), text


def test_service_is_saved_shown_and_filterable(conn):
    store.save_post(conn, make_post("reddit_1"))
    store.save_post(conn, make_post("reddit_2"))
    store.save_analysis(conn, make_analysis("reddit_1", service="etsy"))
    store.save_analysis(conn, make_analysis("reddit_2"))  # default: automation
    rows = store.get_dashboard_rows(conn, DashboardFilters(services=("etsy",)))
    assert [r["id"] for r in rows] == ["reddit_1"]
    assert build_table(rows).loc["reddit_1"]["Service"] == "🛍️ Etsy"


def test_muse_job_names_the_services_and_etsy_forums():
    message = build_job_message("", 20)
    assert "Etsy shop help, SEO, Local SEO" in message
    assert "community.etsy.com" in message and "\n" not in message


def test_new_sources_are_configured():
    names = [s.name for s in load_sources(CONFIG_DIR / "sources.json")]
    assert "Digital Point forums" in names
    assert "Stack Exchange webmasters: seo" in names
    assert "Ask HN: SEO" in names
    assert "r/localseo" in names and "r/EtsySellers" in names


FORUM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Digital Point</title>
<item><title>Need local SEO help for my dental clinic</title>
  <link>https://forums.digitalpoint.com/threads/need-local-seo-help.2885802/</link>
  <pubDate>Wed, 18 Nov 2026 10:00:00 +0000</pubDate><dc:creator>clinicowner</dc:creator>
  <description><![CDATA[<p>We don't show on Google Maps. Email me: owner@clinic.com</p>]]></description></item>
<item><title>Spam elsewhere</title><link>https://evil.example.com/x</link></item>
</channel></rss>"""


def test_rss_forum_source_reads_threads_and_cleans_them():
    clock = FakeClock()
    session = FakeSession(FakeResponse(200, FORUM_FEED))
    http = PoliteHttpClient("ua", session=session, sleep=clock.sleep, clock=clock.time)
    posts = RssForumSource("Digital Point forums", "https://forums.digitalpoint.com/forums/-/index.rss").fetch(http)
    assert len(posts) == 1  # the off-site link is skipped
    post = posts[0]
    assert post.id == "forum_forums_digitalpoint_com_2885802"
    assert post.platform == "forum" and "owner@clinic.com" not in post.body
    assert post.posted_at == datetime(2026, 11, 18, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize(("name", "url"), [("<x>", "https://a.com/rss"), ("Forum", "http://a.com/rss")])
def test_rss_forum_source_validation(name, url):
    with pytest.raises(ValueError):
        RssForumSource(name, url)
