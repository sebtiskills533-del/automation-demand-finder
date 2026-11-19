from datetime import datetime, timezone

import anthropic
import pytest

from app import store
from app.analyze import (
    MAX_ATTEMPTS_PER_POST,
    MAX_BODY_CHARS_FOR_AI,
    PostAnalysis,
    analyze_pending,
    build_user_text,
    to_record,
)
from app.budget import month_start
from app.config import CONFIG_DIR
from app.errors import LLMAuthError
from app.llm import ClaudeClient
from app.models_registry import load_registry
from tests.conftest import make_post
from tests.fakes import FakeAnthropic, claude_connection_error, claude_status_error, fake_message

GOOD = {
    "summary": "Wants Etsy orders copied into Google Sheets",
    "service": "automation",
    "category": "Orders",
    "difficulty": "low",
    "tool": "Google Sheets + Apps Script",
    "est_hours": 3,
    "buildable": True,
    "product_potential": 5,
    "seriousness": 5,
    "seriousness_reason": "Runs a shop, gives volumes, offers to pay",
    "wants_to_hire": True,
    "shares_own_website": False,
    "region_hint": "United Kingdom",
    "reason": "Simple trigger and append",
}


@pytest.fixture
def registry():
    return load_registry(CONFIG_DIR / "models.json")


def setup_posts(conn, count=1):
    for i in range(count):
        store.save_post(conn, make_post(f"reddit_{i}"))
    store.set_filter_results(conn, [(f"reddit_{i}", True) for i in range(count)])


def make_llm(conn, *results):
    fake = FakeAnthropic(*results)
    llm = ClaudeClient(
        "key", lambda task, model, i, o, c: store.log_usage(conn, task, model, i, o, c), client=fake
    )
    return llm, fake


def test_to_record_applies_app_rules():
    record = to_record("reddit_1", "reddit", PostAnalysis(**GOOD), "claude-haiku-5-5")
    assert record.category == "orders"
    assert (record.delivery_min_days, record.delivery_max_days) == (1, 3)
    assert record.contact_method.startswith("Direct message on Reddit")
    assert record.opportunity_score == 100
    assert record.region_hint == "United Kingdom"


def test_to_record_overrides_ai_when_job_is_too_big():
    data = PostAnalysis(**{**GOOD, "difficulty": "medium", "est_hours": 25, "buildable": True})
    record = to_record("reddit_1", "reddit", data, "m")
    assert record.buildable is False
    assert record.delivery_min_days is None
    assert record.opportunity_score <= 30


def test_to_record_blank_region_becomes_unknown():
    record = to_record("reddit_1", "reddit", PostAnalysis(**{**GOOD, "region_hint": "  "}), "m")
    assert record.region_hint == "Unknown"


@pytest.mark.parametrize(
    "bad", [{"seriousness": 9}, {"difficulty": "extreme"}, {"tool": "Excel macro"}, {"extra": 1}]
)
def test_output_model_rejects_invalid_answers(bad):
    with pytest.raises(ValueError):
        PostAnalysis(**{**GOOD, **bad})


def test_user_text_wraps_and_shortens_post():
    text = build_user_text("reddit", "r/Etsy", "Title", "x" * (MAX_BODY_CHARS_FOR_AI + 50))
    assert text.startswith("Platform: Reddit (r/Etsy)")
    assert "<post>" in text and "[post shortened]" in text
    assert "(no text)" in build_user_text("hackernews", "Hacker News", "T", "")


def test_month_start():
    assert month_start(datetime(2026, 11, 18, 15, 4, tzinfo=timezone.utc)) == datetime(
        2026, 11, 1, tzinfo=timezone.utc
    )


def test_analyze_saves_results_and_reports_tokens(conn, settings, registry):
    setup_posts(conn, 2)
    llm, fake = make_llm(conn, fake_message(GOOD), fake_message(GOOD))
    report = analyze_pending(conn, llm, registry, settings)
    assert (report.analyzed, report.failed) == (2, 0)
    assert report.input_tokens == 2000 and report.output_tokens == 400
    assert report.cost_usd > 0
    assert store.count_posts(conn)["analyzed"] == 2
    assert fake.calls[0]["model"] == "claude-haiku-5-5"


def test_analyze_uses_model_chosen_in_settings(conn, settings, registry):
    setup_posts(conn)
    registry.set_model_for_task(conn, "analyze", "claude-sonnet-5-5")
    llm, fake = make_llm(conn, fake_message(GOOD))
    analyze_pending(conn, llm, registry, settings)
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"


def test_analyze_retries_once_then_succeeds(conn, settings, registry):
    setup_posts(conn)
    llm, fake = make_llm(conn, fake_message("garbage"), fake_message(GOOD))
    report = analyze_pending(conn, llm, registry, settings)
    assert report.analyzed == 1 and len(fake.calls) == 2
    assert report.input_tokens == 2000  # the failed attempt is counted too


def test_analyze_skips_after_two_bad_answers_and_gives_up_eventually(conn, settings, registry):
    setup_posts(conn)
    for _ in range(MAX_ATTEMPTS_PER_POST):
        llm, _ = make_llm(conn, fake_message("bad"), fake_message("bad"))
        report = analyze_pending(conn, llm, registry, settings)
        assert report.failed == 1
    # After MAX_ATTEMPTS the post is no longer retried (no Claude call at all).
    llm, fake = make_llm(conn)
    assert analyze_pending(conn, llm, registry, settings).failed == 0
    assert fake.calls == []


def test_analyze_respects_max_posts_per_run(conn, settings, registry):
    setup_posts(conn, settings.max_posts_per_run + 3)
    llm, fake = make_llm(conn, *[fake_message(GOOD)] * settings.max_posts_per_run)
    assert analyze_pending(conn, llm, registry, settings).analyzed == settings.max_posts_per_run


def test_analyze_stops_at_monthly_budget(conn, settings, registry):
    setup_posts(conn, 2)
    store.log_usage(conn, "analyze", "claude-haiku-5-5", 0, 0, settings.monthly_budget_usd)
    llm, fake = make_llm(conn)
    report = analyze_pending(conn, llm, registry, settings)
    assert report.analyzed == 0 and "budget" in report.stopped_reason
    assert fake.calls == []


def test_analyze_stops_on_network_trouble_without_burning_attempts(conn, settings, registry):
    setup_posts(conn, 2)
    llm, _ = make_llm(conn, claude_connection_error())
    report = analyze_pending(conn, llm, registry, settings)
    assert report.analyzed == 0 and "internet" in report.stopped_reason
    attempts = conn.execute("SELECT MAX(analysis_attempts) FROM posts").fetchone()[0]
    assert attempts == 0


def test_analyze_bad_api_key_stops_the_run(conn, settings, registry):
    setup_posts(conn)
    llm, _ = make_llm(conn, claude_status_error(anthropic.AuthenticationError, 401))
    with pytest.raises(LLMAuthError):
        analyze_pending(conn, llm, registry, settings)


def test_prompt_counts_fixes_as_jobs():
    from app.analyze import SYSTEM_PROMPT

    assert "fixing, debugging" in SYSTEM_PROMPT
    assert "problems only the platform can solve" in SYSTEM_PROMPT


def test_reset_small_skipped_requeues_only_small_not_a_job_posts(conn, settings, registry):
    from app.analyze import reset_small_skipped
    from tests.conftest import make_analysis

    for pid in ("reddit_small", "reddit_big", "reddit_good"):
        store.save_post(conn, make_post(pid))
    store.set_filter_results(conn, [(p, True) for p in ("reddit_small", "reddit_big", "reddit_good")])
    store.save_analysis(conn, make_analysis("reddit_small", buildable=False, difficulty="low", est_hours=1))
    store.save_analysis(conn, make_analysis("reddit_big", buildable=False, difficulty="high", est_hours=40))
    store.save_analysis(conn, make_analysis("reddit_good"))
    store.update_action(conn, "reddit_small", notes="keep me")
    store.record_analysis_failure(conn, "reddit_small")

    assert reset_small_skipped(conn) == 1
    pending = store.posts_pending_analysis(conn, 10, MAX_ATTEMPTS_PER_POST)
    assert [r["id"] for r in pending] == ["reddit_small"]
    assert conn.execute("SELECT notes FROM actions").fetchone()[0] == "keep me"  # user data kept

    llm, _ = make_llm(conn, fake_message(GOOD))
    assert analyze_pending(conn, llm, registry, settings).analyzed == 1


def test_analyze_cli_rejects_unknown_options(capsys):
    from app.analyze import main

    assert main(["--everything"]) == 2


def test_analyze_with_nothing_pending(conn, settings, registry):
    llm, fake = make_llm(conn)
    report = analyze_pending(conn, llm, registry, settings)
    assert report.analyzed == 0 and fake.calls == []
