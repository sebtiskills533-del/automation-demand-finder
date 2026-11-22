"""Fresh gigs (bids, alerts, runs every 3 hours), outcomes and the Results page logic, proposals."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import db, notify, results, store
from app.config import CONFIG_DIR, PROJECT_ROOT
from app.domain import utc_now
from app.models_registry import load_registry
from app.pipeline import ALERT_MAX_BIDS, run_daily
from app.replies import draft_reply
from app.scoring import bid_adjustment, opportunity_score
from app.sources.jobs import FreelancerSource
from app.store import DashboardFilters
from app.writing import JOB_STYLES, PROPOSAL, REPLY_STYLES, get_style, set_style, style_rules, styles_for
from tests.conftest import make_analysis, make_post
from tests.fakes import FakeResponse, FakeSource, fake_message
from tests.test_analyze import GOOD
from tests.test_jobs import http_with
from tests.test_pipeline import make_runtime
from tests.test_v11 import llm_with


@pytest.fixture
def registry():
    return load_registry(CONFIG_DIR / "models.json")


def gig(post_id="freelancer_1", bids=5, posted_at=None, **analysis):
    post = make_post(post_id, platform="freelancer", source="Freelancer.com", posted_at=posted_at)
    return post.__class__(**{**post.__dict__, "bids": bids}), make_analysis(post_id, **analysis)


# ------------------------------------------------------------------ bids


def test_freelancer_gigs_keep_their_bid_count(conn):
    payload = {"result": {"projects": [
        {"id": 1, "title": "Few", "seo_url": "a/b", "bid_stats": {"bid_count": 3}},
        {"id": 2, "title": "Odd", "seo_url": "c/d", "bid_stats": {"bid_count": "lots"}},
    ]}}
    http, _ = http_with(FakeResponse(200, payload))
    posts = FreelancerSource("seo").fetch(http)
    assert [p.bids for p in posts] == [3, None]
    store.save_post(conn, posts[0])
    assert conn.execute("SELECT bids FROM posts").fetchone()[0] == 3


def test_bid_adjustment_and_score():
    assert [bid_adjustment(b) for b in (None, 0, 10, 11, 30, 31, 80, 81, 500)] == [0, 5, 5, 0, 0, -10, -10, -20, -20]
    base = opportunity_score(4, 3, "low", True, True)
    assert opportunity_score(4, 3, "low", True, True, bids=3) == min(100, base + 5)
    assert opportunity_score(4, 3, "low", True, True, bids=200) == base - 20
    assert opportunity_score(5, 5, "low", False, True, bids=1) == 30          # the cap still wins


def test_analysis_uses_the_bid_count(conn, settings, registry):
    post, _ = gig(bids=150)
    store.save_post(conn, post)
    store.set_filter_results(conn, [(post.id, True)])
    runtime = make_runtime(conn, settings, [], fake_message(GOOD))
    run_daily(runtime)
    crowded = store.get_post_details(conn, post.id)
    assert crowded["bids"] == 150
    expected = opportunity_score(GOOD["seriousness"], GOOD["product_potential"], GOOD["difficulty"], True,
                                 GOOD["wants_to_hire"], 150)
    assert crowded["opportunity_score"] == expected


def test_upgrade_fills_bids_and_rescores_old_gigs(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    for version, script in enumerate(db.MIGRATIONS[:11], start=1):  # a database from before V12
        for statement in db._split_statements(script):
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {version:d}")
    for pid, body in (("freelancer_1", "Job listing · Budget: 10 to 30 USD · Bids: 3\n\nText"),
                      ("freelancer_2", "Job listing · Budget: 10 to 30 USD · Bids: 229\n\nText"),
                      ("reddit_1", "Bids: 5 is just text here")):
        platform = pid.split("_")[0]
        conn.execute("INSERT INTO posts (id, source, platform, url, title, body, author_handle, posted_at, "
                     "collected_at, passed_filter) VALUES (?, 's', ?, 'https://x.com', 't', ?, 'a', "
                     "'2026-11-18T09:30:00Z', '2026-11-18T09:30:00Z', 1)", (pid, platform, body))
        store.save_analysis(conn, make_analysis(pid, opportunity_score=70))
    conn.commit()
    db.init_db(conn)
    rows = dict(conn.execute("SELECT p.id, p.bids FROM posts p").fetchall())
    scores = dict(conn.execute("SELECT post_id, opportunity_score FROM analyses").fetchall())
    assert rows == {"freelancer_1": 3, "freelancer_2": 229, "reddit_1": None}
    assert scores == {"freelancer_1": 75, "freelancer_2": 50, "reddit_1": 70}
    conn.close()


# ------------------------------------------------------------- fresh gigs


def test_fresh_gigs_and_filters(conn):
    start = utc_now() - timedelta(minutes=5)
    for post, analysis in (gig("freelancer_few", 4, opportunity_score=80),
                           gig("freelancer_crowd", 120, opportunity_score=80),
                           gig("freelancer_weak", 2, opportunity_score=20),
                           gig("freelancer_done", 1, opportunity_score=90)):
        store.save_post(conn, post)
        store.save_analysis(conn, analysis)
    store.save_post(conn, make_post("reddit_1"))
    store.save_analysis(conn, make_analysis("reddit_1", opportunity_score=95))
    store.set_answered(conn, "freelancer_done", True)

    fresh = store.fresh_gigs(conn, start, 45, ALERT_MAX_BIDS)
    assert [g["id"] for g in fresh] == ["freelancer_few"]                # crowded, weak, answered, not a gig: out
    assert store.fresh_gigs(conn, utc_now() + timedelta(minutes=1), 45, 20) == []   # rated before this run

    few = store.get_dashboard_rows(conn, DashboardFilters(max_bids=30))
    assert "freelancer_crowd" not in {r["id"] for r in few} and "reddit_1" in {r["id"] for r in few}
    old = store.get_dashboard_rows(conn, DashboardFilters(posted_since=datetime(2030, 1, 1, tzinfo=timezone.utc)))
    assert old == []


def test_daily_run_reports_fresh_gigs_and_groups_once_a_day(conn, settings):
    post, _ = gig("freelancer_9", bids=2)
    sources = [FakeSource("Freelancer.com: seo", [post])]
    grouping_answers = [fake_message({"groups": []})]
    clock = [utc_now()]  # the same clock the rating step writes with
    report = run_daily(make_runtime(conn, settings, sources, fake_message(GOOD), *grouping_answers),
                       now=lambda: clock[0])
    assert [g["id"] for g in report.fresh_gigs] == ["freelancer_9"]
    assert any("Fresh gigs with few bids: 1" in line for line in report.summary_lines(88))

    clock[0] += timedelta(hours=3)                                   # the next scheduled run
    second = run_daily(make_runtime(conn, settings, sources), now=lambda: clock[0])
    assert second.grouping == "already grouped today." and second.fresh_gigs == ()
    clock[0] += timedelta(hours=21)
    third = run_daily(make_runtime(conn, settings, sources), now=lambda: clock[0])
    assert third.grouping != "already grouped today."


def test_run_daily_script_shows_a_notification(monkeypatch, settings):
    import run_daily as script
    from app import cli

    shown = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(script, "run_daily", lambda runtime: _report([{"title": "Boost reviews", "bids": 3}]))
    monkeypatch.setattr(script, "send_toast", lambda title, body: shown.append((title, body)))
    monkeypatch.setattr(cli, "build_llm", lambda s, c: object())
    script.main([])
    assert shown and "1 fresh gig" in shown[0][0] and "Boost reviews (3 bids)" in shown[0][1]


def _report(gigs):
    from app.analyze import AnalyzeReport
    from app.collect import CollectReport
    from app.pipeline import DailyReport
    from app.prefilter import FilterReport

    return DailyReport(CollectReport({"x": 1}), FilterReport(0, 0), AnalyzeReport(), "", tuple(gigs))


# ---------------------------------------------------------------- notify


def test_toast_text_goes_through_the_environment_not_the_command(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "win32")
    seen = {}

    def run(args, env, **kwargs):
        seen["args"], seen["env"] = args, env
        return subprocess.CompletedProcess(args, 0, b"", b"")

    assert notify.send_toast("Title <b>", "evil'; Remove-Item C:\\ -Recurse #", run=run)
    assert "Remove-Item C:" not in " ".join(seen["args"][:-1]) and "Remove-Item" not in seen["args"][-1]
    assert seen["env"]["ADF_TOAST_BODY"].startswith("evil'") and "SecurityElement]::Escape" in seen["args"][-1]


def test_toast_failures_never_raise(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "win32")
    assert not notify.send_toast("t", "b", run=lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"no"))

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 20)

    assert not notify.send_toast("t", "b", run=boom)
    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert not notify.send_toast("t", "b", run=lambda *a, **k: pytest.fail("never on other systems"))


def test_fresh_gig_message():
    title, body = notify.fresh_gigs_message([{"title": f"Gig {n}", "bids": n} for n in range(5)])
    assert title == "🔥 5 fresh gigs with few bids" and "Gig 0 (0 bids)" in body and "+2 more" in body
    assert notify.fresh_gigs_message([{"title": "One", "bids": None}])[0] == "🔥 1 fresh gig with few bids"


def test_schedule_script_runs_every_3_hours_without_a_window():
    script = (PROJECT_ROOT / "scripts" / "schedule_every_3_hours.ps1").read_text(encoding="utf-8")
    assert "pythonw.exe" in script and "New-TimeSpan -Hours 3" in script and "run_daily.py --scheduled" in script
    assert "IgnoreNew" in script


# --------------------------------------------------------------- outcomes


def test_outcomes(conn):
    store.save_post(conn, make_post("reddit_1"))
    store.save_analysis(conn, make_analysis("reddit_1"))
    store.set_outcome(conn, "reddit_1", "won")
    post = store.get_post_details(conn, "reddit_1")
    assert post["outcome"] == "won" and post["answered_at"] and post["outcome_at"]
    store.mark_answered_if_unset(conn, "reddit_1", "2026-11-22T10:00:00Z")   # a reply later never downgrades
    assert store.get_post_details(conn, "reddit_1")["outcome"] == "won"
    store.set_outcome(conn, "reddit_1", None)
    post = store.get_post_details(conn, "reddit_1")
    assert post["outcome"] is None and post["answered_at"]                    # still answered, now waiting
    with pytest.raises(ValueError):
        store.set_outcome(conn, "reddit_1", "maybe")
    with pytest.raises(ValueError):
        store.set_outcome(conn, "nope", "won")


def test_a_reply_marks_the_post_replied(conn):
    store.save_post(conn, make_post("reddit_1"))
    store.mark_answered_if_unset(conn, "reddit_1", "2026-11-22T10:00:00Z")
    row = conn.execute("SELECT outcome, outcome_at FROM actions").fetchone()
    assert tuple(row) == ("replied", "2026-11-22T10:00:00Z")


# ------------------------------------------------------------ results logic


ROW = {"platform": "freelancer", "service": "seo", "tech_level": "non_technical", "reply_style": "proposal",
       "posted_at": "2026-11-20T08:00:00Z", "answered_at": "2026-11-20T10:00:00Z", "bids": 4,
       "outcome": None, "replies": 0}


def test_results_breakdowns():
    rows = [ROW | {"outcome": "won"}, ROW, ROW | {"platform": "reddit", "bids": None, "reply_style": None,
                                                    "answered_at": "2026-11-25T10:00:00Z", "replies": 1}]
    assert results.totals(rows) == {"answered": 3, "replied": 2, "won": 1, "reply_rate": 67}
    by_platform = {line["Platform"]: line for line in results.breakdown(rows, "Platform")}
    assert by_platform["Freelancer.com"]["Answered"] == 2 and by_platform["Freelancer.com"]["Reply rate %"] == 50
    assert by_platform["Reddit"]["Replied"] == 1
    speeds = {line["How fast you answered"] for line in results.breakdown(rows, "How fast you answered")}
    assert speeds == {"1. Under 6 hours", "4. Over 3 days"}
    styles = {line["Writing style"] for line in results.breakdown(rows, "Writing style")}
    assert styles == {"📝 Proposal (for gigs)", "Not recorded"}
    assert results.bids_bucket({"bids": 81}) == "4. 80+ bids" and results.bids_bucket({"bids": None}) == "Not a gig"
    assert results.speed_bucket({"posted_at": None, "answered_at": "x"}) == "Unknown"
    assert results.totals([]) == {"answered": 0, "replied": 0, "won": 0, "reply_rate": 0}


def test_results_rows_only_answered(conn):
    for pid in ("reddit_1", "reddit_2"):
        store.save_post(conn, make_post(pid))
        store.save_analysis(conn, make_analysis(pid))
    store.set_answered(conn, "reddit_1", True)
    assert [r["id"] for r in store.results_rows(conn)] == ["reddit_1"]


# ---------------------------------------------------------------- proposals


def test_gigs_get_the_proposal_style(conn):
    assert styles_for("freelancer") == JOB_STYLES and styles_for("remoteok") == JOB_STYLES
    assert styles_for("reddit") == REPLY_STYLES and PROPOSAL not in REPLY_STYLES
    assert get_style(conn, "freelancer") == PROPOSAL and get_style(conn, "reddit") == "convincing"
    with pytest.raises(ValueError):
        set_style(conn, PROPOSAL)              # the preferred reply style can't be the proposal
    rules = style_rules(PROPOSAL, "reply")
    assert "first two lines" in rules and "[link to my project]" in rules and "No price" in rules
    assert style_rules(PROPOSAL, "email") != rules   # emails keep their own rules


def test_draft_for_a_gig_is_a_proposal_and_remembers_the_style(conn, settings, registry):
    post, analysis = gig("freelancer_7", bids=8)
    store.save_post(conn, post)
    store.save_analysis(conn, analysis)
    llm, fake = llm_with(conn, fake_message({"reply": "You want your signups in Brevo. 1. Connect. 2. Map. 3. Test."}))
    draft_reply(conn, llm, registry, settings, "freelancer_7")
    assert "first two lines" in fake.calls[0]["system"]
    saved = store.get_post_details(conn, "freelancer_7")
    assert saved["reply_style"] == PROPOSAL and set(store.get_drafts(conn, "reply", "freelancer_7")) == {PROPOSAL}
    with pytest.raises(ValueError, match="Unknown writing style"):
        draft_reply(conn, llm, registry, settings, "freelancer_7", "shouting")
