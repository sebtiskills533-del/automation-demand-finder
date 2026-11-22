"""Version 1.1: draft replies, decision reports, top problems and product specs."""

from __future__ import annotations

import pytest

from app import db, store
from app.clusters import WINDOW_DAYS, run_clustering, slugify
from app.config import CONFIG_DIR
from app.errors import BudgetExceededError, LLMOutputError
from app.llm import ClaudeClient
from app.models_registry import TASKS, load_registry
from app.replies import draft_reply
from app.reports import SKIP_REASON, make_report
from app.specs import spec_to_markdown, write_spec
from tests.conftest import make_analysis, make_post
from tests.fakes import FakeAnthropic, fake_message


@pytest.fixture
def registry():
    return load_registry(CONFIG_DIR / "models.json")


def llm_with(conn, *results):
    fake = FakeAnthropic(*results)
    sink = lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c)  # noqa: E731
    return ClaudeClient("key", sink, client=fake), fake


def seed(conn, post_id="reddit_1", status=None, **analysis):
    store.save_post(conn, make_post(post_id))
    store.save_analysis(conn, make_analysis(post_id, **analysis))
    if status:
        store.update_action(conn, post_id, status=status)


# ------------------------------------------------------------- registry


def test_every_task_has_a_default_model(registry, conn):
    assert set(TASKS) == {"analyze", "reply", "report", "cluster", "spec", "email", "explain", "portfolio", "painpoints"}
    assert registry.model_for_task(conn, "reply").id == "claude-sonnet-5-5"
    assert registry.model_for_task(conn, "cluster").id == "claude-haiku-5-5"


def test_schema_v3_tables_exist(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"decision_reports", "clusters", "product_specs"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


# --------------------------------------------------------------- replies

REPLY = {"reply": "Add a Filter node before the AI Agent that drops messages under 3 words. "
                  "Email me at x@y.com. Happy to help set this up."}


def test_draft_reply_saves_clean_text_and_uses_reply_model(conn, settings, registry):
    seed(conn)
    llm, fake = llm_with(conn, fake_message(REPLY))
    text = draft_reply(conn, llm, registry, settings, "reddit_1")
    assert "x@y.com" not in text and text.startswith("Add a Filter node")
    assert store.get_post_details(conn, "reddit_1")["reply_draft"] == text
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    assert "Allowed contact method" in fake.calls[0]["messages"][0]["content"]


def test_draft_reply_retries_when_too_long(conn, settings, registry):
    seed(conn)
    too_long = {"reply": "word " * 400}
    llm, fake = llm_with(conn, fake_message(too_long), fake_message(REPLY))
    draft_reply(conn, llm, registry, settings, "reddit_1")
    assert len(fake.calls) == 2


def test_draft_reply_gives_up_after_two_bad_answers(conn, settings, registry):
    seed(conn)
    llm, _ = llm_with(conn, fake_message({"reply": ""}), fake_message({"reply": ""}))
    with pytest.raises(LLMOutputError):
        draft_reply(conn, llm, registry, settings, "reddit_1")


def test_draft_reply_unknown_post_and_budget(conn, settings, registry):
    llm, fake = llm_with(conn)
    with pytest.raises(ValueError):
        draft_reply(conn, llm, registry, settings, "nope")
    seed(conn)
    store.log_usage(conn, "analyze", "m", 0, 0, settings.monthly_budget_usd)
    with pytest.raises(BudgetExceededError):
        draft_reply(conn, llm, registry, settings, "reddit_1")
    assert fake.calls == []


def test_drafts_are_kept_per_style(conn, settings, registry):
    seed(conn)
    llm, _ = llm_with(conn, fake_message({"reply": "Tip first version."}),
                      fake_message({"reply": "Convincing version."}))
    draft_reply(conn, llm, registry, settings, "reddit_1", style="tip_first")
    draft_reply(conn, llm, registry, settings, "reddit_1", style="convincing")
    drafts = store.get_drafts(conn, "reply", "reddit_1")
    assert drafts["tip_first"]["body"] == "Tip first version."
    assert drafts["convincing"]["body"] == "Convincing version."
    assert store.get_post_details(conn, "reddit_1")["reply_draft"] == "Convincing version."  # newest chosen
    with pytest.raises(ValueError):
        store.save_draft(conn, "tweet", "reddit_1", "x", "body")


def test_reply_draft_length_is_limited_in_store(conn):
    seed(conn)
    with pytest.raises(ValueError):
        store.update_action(conn, "reddit_1", reply_draft="x" * 3001)


# --------------------------------------------------------------- reports

REPORT = {
    "verdict": "Do it",
    "reasons": ["Low difficulty", "Beginner can build it with Claude Code", "Low support risk",
                "Could become a template"],
    "plan": [{"title": "Get access to their sheet", "hours": 0.5},
             {"title": "Build the Apps Script trigger", "hours": 2},
             {"title": "Test and write handover note", "hours": 1}],
    "draft": "Tip: use an onFormSubmit trigger. Happy to set it up for you.",
}


def test_report_needs_a_ticked_post(conn, settings, registry):
    seed(conn)  # status New
    llm, fake = llm_with(conn)
    with pytest.raises(ValueError, match="Tick this post first"):
        make_report(conn, llm, registry, settings, "reddit_1")
    assert fake.calls == []


def test_report_uses_app_rules_for_delivery_and_contact(conn, settings, registry):
    seed(conn, status="Ticked", contact_method="Public reply under the post on Reddit")
    llm, _ = llm_with(conn, fake_message(REPORT))
    report = make_report(conn, llm, registry, settings, "reddit_1")
    assert report["verdict"] == "Do it"
    assert report["build_hours"] == 3.5
    assert (report["delivery_min_days"], report["delivery_max_days"]) == (1, 3)
    assert report["contact_method"] == "Public reply under the post on Reddit"
    saved = store.get_decision_report(conn, "reddit_1")
    assert saved["plan"][1]["title"] == "Build the Apps Script trigger"
    assert len(saved["reasons"]) == 4


def test_report_forces_skip_when_plan_is_too_big(conn, settings, registry):
    seed(conn, status="Building")
    big = {**REPORT, "plan": [{"title": "Build a full app", "hours": 30}, {"title": "Test", "hours": 5}]}
    llm, _ = llm_with(conn, fake_message(big))
    report = make_report(conn, llm, registry, settings, "reddit_1")
    assert report["verdict"] == "Skip" and report["reasons"][0] == SKIP_REASON
    assert report["delivery_min_days"] is None


@pytest.mark.parametrize(
    "bad",
    [{"reasons": ["only one"]}, {"verdict": "Maybe"}, {"plan": [{"title": "x", "hours": 1}]},
     {"draft": "word " * 300}],
)
def test_report_rejects_invalid_answers(conn, settings, registry, bad):
    seed(conn, status="Ticked")
    llm, _ = llm_with(conn, fake_message({**REPORT, **bad}), fake_message({**REPORT, **bad}))
    with pytest.raises(LLMOutputError):
        make_report(conn, llm, registry, settings, "reddit_1")
    assert store.get_decision_report(conn, "reddit_1") is None


# -------------------------------------------------------------- clusters


def seed_jobs(conn, count):
    for i in range(count):
        seed(conn, f"reddit_{i}", summary=f"Wants orders copied to a sheet #{i}", category="orders")


GROUPING = {"groups": [
    {"id": "Orders -> Sheet!", "name": "Orders to Google Sheet", "description": "Copy orders",
     "post_numbers": [1, 2, 3, 99, 2]},
    {"id": "orders-sheet", "name": "Duplicate id", "description": "", "post_numbers": [4]},
    {"id": "empty", "name": "Nothing valid", "description": "", "post_numbers": [0, 50]},
]}


def test_slugify():
    assert slugify("Orders -> Sheet!") == "orders-sheet"
    assert slugify("!!!") == ""


def test_clustering_validates_and_saves(conn, settings, registry):
    seed_jobs(conn, 4)
    llm, fake = llm_with(conn, fake_message(GROUPING))
    result = run_clustering(conn, llm, registry, settings)
    assert (result.groups, result.grouped_posts) == (2, 4)
    clusters = {c["id"]: c for c in store.list_clusters(conn)}
    assert clusters["orders-sheet"]["posts"] == 3  # duplicates and bad numbers ignored
    assert clusters["orders-sheet-2"]["posts"] == 1  # duplicate id made unique
    assert "empty" not in clusters
    assert "<requests>" in fake.calls[0]["messages"][0]["content"]


def test_clustering_is_skipped_when_nothing_new(conn, settings, registry):
    seed_jobs(conn, 3)
    llm, fake = llm_with(conn, fake_message(GROUPING))
    run_clustering(conn, llm, registry, settings)
    again = run_clustering(conn, llm, registry, settings)
    assert again.skipped_reason.startswith("No new posts") and len(fake.calls) == 1


def test_clustering_needs_at_least_two_job_posts(conn, settings, registry):
    seed_jobs(conn, 1)
    seed(conn, "reddit_news", buildable=False, est_hours=0)  # 'not a job' is excluded
    llm, fake = llm_with(conn)
    assert "Not enough" in run_clustering(conn, llm, registry, settings).skipped_reason
    assert fake.calls == []


def test_regrouping_removes_empty_groups_but_keeps_reused_specs(conn, settings, registry):
    seed_jobs(conn, 3)
    first = {"groups": [{"id": "keep", "name": "Keep", "description": "", "post_numbers": [1, 2]},
                        {"id": "gone", "name": "Gone", "description": "", "post_numbers": [3]}]}
    llm, _ = llm_with(conn, fake_message(first))
    run_clustering(conn, llm, registry, settings)
    store.save_product_spec(conn, "keep", {"x": 1}, "m")
    store.save_product_spec(conn, "gone", {"x": 2}, "m")

    second = {"groups": [{"id": "keep", "name": "Keep", "description": "", "post_numbers": [1, 2, 3]}]}
    llm, _ = llm_with(conn, fake_message(second))
    run_clustering(conn, llm, registry, settings, force=True)
    assert [c["id"] for c in store.list_clusters(conn)] == ["keep"]
    assert store.get_product_spec(conn, "keep") == {"x": 1}
    assert store.get_product_spec(conn, "gone") is None


def test_clustering_window_is_30_days():
    assert WINDOW_DAYS == 30


# ----------------------------------------------------------------- specs

SPEC = {
    "product_name": "Etsy Order Tracker",
    "who_its_for": "Etsy sellers with 20-200 orders a month",
    "problem_solved": "Stops copying orders by hand",
    "features": ["Auto-import orders", "Monthly totals", "Low-stock alerts"],
    "sheets_tabs": [{"name": "Orders", "purpose": "One row per order"}],
    "script_triggers": ["Time-driven: every hour, import new orders"],
    "buyer_setup_steps": ["Make a copy", "Authorize the script", "Paste your shop name"],
    "etsy_listing_title": "Etsy Order Tracker Google Sheets Template | Automatic Order Log",
}


def test_write_spec_saves_and_renders_markdown(conn, settings, registry):
    seed_jobs(conn, 2)
    store.replace_clusters(conn, [("etsy-orders", "Etsy orders", "d", ["reddit_0", "reddit_1"])],
                           ["reddit_0", "reddit_1"])
    llm, fake = llm_with(conn, fake_message(SPEC))
    spec = write_spec(conn, llm, registry, settings, "etsy-orders")
    assert store.get_product_spec(conn, "etsy-orders") == spec
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    md = spec_to_markdown(spec)
    assert md.startswith("# Etsy Order Tracker") and "1. Make a copy" in md
    assert store.list_clusters(conn)[0]["has_spec"] == 1


def test_write_spec_rejects_long_etsy_title_and_unknown_group(conn, settings, registry):
    seed_jobs(conn, 2)
    store.replace_clusters(conn, [("g", "G", "", ["reddit_0"])], ["reddit_0"])
    bad = {**SPEC, "etsy_listing_title": "x" * 200}
    llm, _ = llm_with(conn, fake_message(bad), fake_message(bad))
    with pytest.raises(LLMOutputError):
        write_spec(conn, llm, registry, settings, "g")
    with pytest.raises(ValueError):
        write_spec(conn, llm, registry, settings, "missing")


# ------------------------------------------------------------- dashboard


def test_dashboard_counts(conn):
    from datetime import timedelta

    from app.domain import utc_now

    seed(conn, "reddit_a", opportunity_score=80, status="Ticked")
    seed(conn, "reddit_b", opportunity_score=30)
    counts = store.dashboard_counts(conn, 45, utc_now() - timedelta(days=1))
    assert counts == {"analyzed": 2, "best": 1, "active": 1, "new_today": 2}


def test_get_post_details(conn):
    seed(conn)
    details = store.get_post_details(conn, "reddit_1")
    assert details["status"] == "New" and details["reply_draft"] == ""
    assert store.get_post_details(conn, "missing") is None
