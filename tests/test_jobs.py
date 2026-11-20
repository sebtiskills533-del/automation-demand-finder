"""Job boards / freelance marketplaces with official feeds, and the Platforms guide."""

from __future__ import annotations

import json

import pytest

from app import store
from app.config import CONFIG_DIR
from app.contact import contact_method
from app.domain import JOB_BOARD_PLATFORMS
from app.errors import ConfigError, SourceFetchError
from app.http_client import PoliteHttpClient
from app.sources import load_sources
from app.sources.jobs import (
    FreelancerSource,
    HimalayasSource,
    RemoteOkSource,
    RemotiveSource,
    WeWorkRemotelySource,
    WorkingNomadsSource,
)
from app.store import DashboardFilters
from tests.conftest import make_analysis
from tests.fakes import FakeClock, FakeResponse, FakeSession


def http_with(*responses):
    clock = FakeClock()
    session = FakeSession(*responses)
    return PoliteHttpClient("ua", session=session, sleep=clock.sleep, clock=clock.time), session


def test_freelancer_projects_become_posts_with_budget():
    payload = {"status": "success", "result": {"projects": [
        {"id": 40826492, "title": "Google Business Profile setup", "seo_url": "seo/Google-Business-Profile-setup",
         "description": "Need GBP optimised. Call 0300 1234567", "budget": {"minimum": 50, "maximum": 120},
         "currency": {"code": "USD"}, "type": "fixed", "bid_stats": {"bid_count": 7},
         "time_submitted": 1795111982},
        {"id": 2, "title": "Private", "seo_url": "x/y", "nonpublic": True},
        {"id": 3, "title": "No url"},
    ]}}
    http, session = http_with(FakeResponse(200, payload))
    posts = FreelancerSource("google business profile").fetch(http)
    assert len(posts) == 1
    post = posts[0]
    assert post.id == "freelancer_40826492" and post.platform == "freelancer"
    assert post.url == "https://www.freelancer.com/projects/seo/Google-Business-Profile-setup"
    assert post.body.startswith("Job listing · Budget: 50 to 120 USD · Type: fixed · Bids: 7")
    assert "1234567" not in post.body
    assert session.calls[0][1]["query"] == "google business profile"


def test_big_budgets_are_not_mistaken_for_phone_numbers():
    # Regression: "10000.0-20000.0 INR" was replaced by "[phone removed]".
    from app.sources.jobs import money_range

    assert money_range(10000.0, 20000.0, "INR") == "10,000 to 20,000 INR"
    assert money_range(None, "x") == "? to ?"
    payload = {"result": {"projects": [{"id": 7, "title": "Boost Google reviews", "seo_url": "a/b",
                                        "budget": {"minimum": 10000.0, "maximum": 20000.0},
                                        "currency": {"code": "INR"}, "type": "fixed"}]}}
    http, _ = http_with(FakeResponse(200, payload))
    body = FreelancerSource("seo").fetch(http)[0].body
    assert "Budget: 10,000 to 20,000 INR" in body and "phone removed" not in body


def test_remoteok_skips_the_legal_notice_and_foreign_links():
    payload = [{"legal": "API Terms of Service..."},
               {"id": "1137805", "position": "SEO Specialist (part-time)", "company": "Acme",
                "url": "https://remoteOK.com/remote-jobs/seo-1137805", "description": "<p>Help our SEO</p>",
                "epoch": 1795017631},
               {"id": "9", "position": "Elsewhere", "url": "https://evil.example.com/x"}]
    http, _ = http_with(FakeResponse(200, payload))
    posts = RemoteOkSource("seo").fetch(http)
    assert [p.id for p in posts] == ["remoteok_1137805"]
    assert "Company: Acme" in posts[0].body


def test_remotive_himalayas_workingnomads_and_wwr():
    http, _ = http_with(FakeResponse(200, {"jobs": [
        {"id": 5, "title": "Automation Specialist", "url": "https://remotive.com/remote-jobs/x-5",
         "company_name": "Beta", "job_type": "contract", "publication_date": "2026-11-14T20:33:53",
         "description": "<p>Zapier</p>"}]}))
    assert RemotiveSource("automation").fetch(http)[0].id == "remotive_5"

    http, _ = http_with(FakeResponse(200, {"jobs": [
        {"title": "SEO Internship", "guid": "https://himalayas.app/companies/x/jobs/seo-intern",
         "companyName": "X", "employmentType": "Intern", "seniority": ["Entry-level"],
         "locationRestrictions": "India", "pubDate": 1795133359, "excerpt": "Learn SEO"}]}))
    post = HimalayasSource("seo").fetch(http)[0]
    assert post.platform == "himalayas" and "Seniority: Entry-level" in post.body and "Location: India" in post.body

    http, _ = http_with(FakeResponse(200, [
        {"url": "https://www.workingnomads.com/job/go/2034803/", "title": "SEO Analyst",
         "company_name": "Y", "pub_date": "2026-11-19T03:05:06-05:00", "description": "SEO"}]))
    assert WorkingNomadsSource().fetch(http)[0].id == "workingnomads_2034803"

    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><item><title>Acme: SEO Manager</title>'
           "<link>https://weworkremotely.com/remote-jobs/acme-seo-manager</link>"
           "<pubDate>Wed, 18 Nov 2026 10:00:00 +0000</pubDate><description>SEO</description></item>"
           "</channel></rss>")
    http, session = http_with(FakeResponse(200, rss))
    posts = WeWorkRemotelySource("remote-sales-and-marketing-jobs").fetch(http)
    assert posts[0].platform == "weworkremotely"
    assert session.calls[0][0] == "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss"


@pytest.mark.parametrize("payload", [{"unexpected": True}, {"jobs": "nope"}])
def test_bad_board_responses_raise(payload):
    http, _ = http_with(FakeResponse(200, payload))
    with pytest.raises(SourceFetchError):
        RemotiveSource("seo").fetch(http)


@pytest.mark.parametrize("bad", ["", "x", "seo; DROP TABLE", "a" * 80])
def test_job_search_terms_are_validated(bad):
    with pytest.raises(ValueError):
        FreelancerSource(bad)


def test_wwr_category_is_validated():
    with pytest.raises(ValueError):
        WeWorkRemotelySource("../etc/passwd")


def test_job_boards_are_configured():
    names = [s.name for s in load_sources(CONFIG_DIR / "sources.json")]
    for expected in ("Freelancer.com: etsy", "Remote OK: seo", "Remotive: seo", "Himalayas: seo",
                     "We Work Remotely: remote-sales-and-marketing-jobs", "Working Nomads"):
        assert expected in names, expected


def test_job_boards_contact_rules():
    assert contact_method("freelancer", True, False).startswith("Send a proposal (bid) on Freelancer.com")
    assert contact_method("remoteok", True, False).startswith("Apply through the listing on Remote OK")
    for platform in JOB_BOARD_PLATFORMS:
        text = contact_method(platform, True, True).lower()
        assert "email" not in text and "phone" not in text


def test_jobs_filter_by_platform(conn):
    from tests.conftest import make_post

    store.save_post(conn, make_post("reddit_1"))
    job = make_post("freelancer_1", platform="freelancer", source="Freelancer.com")
    store.save_post(conn, job)
    store.save_analysis(conn, make_analysis("reddit_1"))
    store.save_analysis(conn, make_analysis("freelancer_1"))
    rows = store.get_dashboard_rows(conn, DashboardFilters(platforms=JOB_BOARD_PLATFORMS))
    assert [r["id"] for r in rows] == ["freelancer_1"]
    with pytest.raises(ValueError):
        store.get_dashboard_rows(conn, DashboardFilters(platforms=("myspace",)))


def test_analysis_prompt_judges_jobs_for_a_beginner():
    from app.analyze import SYSTEM_PROMPT

    assert "Job listing ·" in SYSTEM_PROMPT and "BEGINNER" in SYSTEM_PROMPT
    assert "internships and junior roles" in SYSTEM_PROMPT


# --------------------------------------------------------------- guide


def test_platform_guide_covers_every_group():
    from app.ui.platforms_page import load_platform_groups

    groups = {g["id"]: g for g in load_platform_groups()}
    assert set(groups) == {"connected", "good_fit", "later", "not_your_skills"}
    connected = {name for name, _ in groups["connected"]["platforms"]}
    assert connected == {"Freelancer.com", "Remote OK", "Remotive", "Himalayas", "We Work Remotely", "Working Nomads"}
    assert ("Fiverr" in {n for n, _ in groups["good_fit"]["platforms"]})


def test_platform_guide_bad_file(tmp_path):
    from app.ui.platforms_page import load_platform_groups

    bad = tmp_path / "platforms.json"
    bad.write_text(json.dumps({"groups": [{"title": "no id"}]}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_platform_groups(bad)
