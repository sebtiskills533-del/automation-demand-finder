"""Pain points: the analysis (Claude or Muse), JSON answers, the app's rules, Muse places."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app import painpoints, store
from app.config import CONFIG_DIR
from app.errors import BudgetExceededError, ConfigError, LLMOutputError, MuseReplyError
from app.models_registry import load_registry
from app.muse.analysis import import_latest_painpoints, import_pasted_answer, run_muse_painpoints
from app.muse.client import MuseReplyText
from app.muse.job import LINKEDIN_MAX_POSTS, PLACES, build_job_message, run_muse_collection
from app.muse.parse import analysis_is_complete, parse_json_block
from tests.conftest import make_analysis, make_post
from tests.fakes import fake_message
from tests.test_v11 import llm_with


@pytest.fixture
def registry():
    return load_registry(CONFIG_DIR / "models.json")


@pytest.fixture
def muse_settings(settings):
    return replace(settings, muse=replace(settings.muse, enabled=True))


def seed(conn, n=6, **analysis):
    for i in range(n):
        store.save_post(conn, make_post(f"reddit_{i}", title=f"Need help {i}"))
        store.save_analysis(conn, make_analysis(f"reddit_{i}", **analysis))


def point(**over):
    base = {
        "title": "Etsy sellers can't see profit per order", "pain": "They copy fees into a sheet by hand.",
        "why_painful": "6 people describe hours of weekly copying. Call 0300 1234567",
        "who": "Small Etsy sellers", "solution": "A dashboard that reads orders and fees.", "kind": "Web app",
        "est_hours": 30, "case_for": ["Six people describe it", "It repeats every month", "No free tool does it"],
        "case_against": ["Etsy may add it: ship a niche first"], "verdict": "Build it",
        "verdict_reason": "Solid evidence and a small first version.", "best_model": "Subscription",
        "subscription_case": "Sellers pay for tools that save hours.", "adsense_case": "Little search traffic.",
        "first_revenue_step": "Sell a template for Rs 500 to five sellers.",
        "roman_urdu": "Yeh masla sellers ka waqt khata hai.", "post_numbers": [1, 2, 3, 4, 5],
    }
    return base | over


PLAN = {
    "summary": "Small sellers lose hours to manual copying.",
    "pain_points": [point()],
    "market_trends": [{"title": "AI listing writers", "whats_new": "Sellers ask about AI titles.",
                       "opportunity": "A checker for AI-written titles.", "post_numbers": [6]}],
}


# ------------------------------------------------------------ the requests


def test_requests_are_numbered_non_tech_first_and_include_linkedin_even_if_not_a_job(conn):
    seed(conn, 5)
    store.set_tech_levels(conn, [("reddit_4", "non_technical", "By hand")])
    store.save_post(conn, make_post("linkedin_1", platform="linkedin", source="LinkedIn: trend"))
    store.save_analysis(conn, make_analysis("linkedin_1", est_hours=0, buildable=False, summary="Owners hate invoicing"))
    items = painpoints.select_items(conn, jobs=10, market=5)
    assert items[0]["id"] == "reddit_4" and items[-1]["id"] == "linkedin_1"
    lines = painpoints.request_lines(items)
    assert lines[0].startswith("1. [Reddit]") and "Non-tech" in lines[0]
    assert "market post" in lines[-1] and "Owners hate invoicing" in lines[-1]


def test_not_enough_posts(conn):
    seed(conn, 2)
    with pytest.raises(ValueError, match="Not enough rated posts"):
        painpoints.select_items(conn, jobs=10, market=5)


def test_old_posts_are_left_out(conn):
    seed(conn, 6)
    later = lambda: datetime(2030, 1, 1, tzinfo=timezone.utc)  # noqa: E731
    with pytest.raises(ValueError, match="Not enough"):
        painpoints.select_items(conn, jobs=10, market=5, now=later)


# ----------------------------------------------------------------- Claude


def test_claude_pain_points_are_tied_to_real_posts_and_scrubbed(conn, settings, registry):
    seed(conn, 6)
    llm, fake = llm_with(conn, fake_message(PLAN))
    content = painpoints.analyze_with_claude(conn, llm, registry, settings)

    first = content["pain_points"][0]
    assert first["evidence"] == "Strong" and len(first["posts"]) == 5 and first["verdict"] == "Build it"
    assert "1234567" not in first["why_painful"]
    assert content["market_trends"][0]["posts"][0]["id"] == first["posts"][0]["id"] or content["market_trends"][0]["posts"]
    saved = store.latest_pain_point_plan(conn)
    assert saved["engine"] == "claude" and saved["content"] == content
    assert saved["model"] == registry.model_for_task(conn, "painpoints").id
    assert fake.calls[0]["model"] == saved["model"]
    assert "<requests>" in fake.calls[0]["messages"][0]["content"]


def test_app_rules_overrule_the_ai_verdict(conn, settings, registry):
    seed(conn, 6)
    thin = point(title="Thin one", post_numbers=[1])
    big = point(title="Big one", est_hours=200)
    invented = point(title="Invented", post_numbers=[99])
    plan = PLAN | {"pain_points": [thin, big, invented]}
    llm, _ = llm_with(conn, fake_message(plan))
    content = painpoints.analyze_with_claude(conn, llm, registry, settings)
    by_title = {p["title"]: p for p in content["pain_points"]}
    assert set(by_title) == {"Thin one", "Big one"}                     # no real post -> dropped
    assert by_title["Thin one"]["verdict"] == "Test first" and by_title["Thin one"]["evidence"] == "Thin"
    assert "only a few real posts" in by_title["Thin one"]["verdict_reason"]
    assert by_title["Big one"]["verdict"] == "Test first" and "over 60 hours" in by_title["Big one"]["verdict_reason"]


def test_evidence_strength_comes_from_counts():
    assert [painpoints.evidence_strength(n) for n in (1, 2, 3, 4, 5, 9)] == ["Thin", "Thin", "Medium", "Medium", "Strong", "Strong"]


def test_budget_and_empty_matches(conn, settings, registry):
    seed(conn, 6)
    none = PLAN | {"pain_points": [point(post_numbers=[77])]}
    llm, _ = llm_with(conn, fake_message(none))
    with pytest.raises(ValueError, match="None of the pain points"):
        painpoints.analyze_with_claude(conn, llm, registry, settings)
    assert store.latest_pain_point_plan(conn) is None
    store.log_usage(conn, "analyze", "m", 0, 0, settings.monthly_budget_usd)
    llm2, fake = llm_with(conn)
    with pytest.raises(BudgetExceededError):
        painpoints.analyze_with_claude(conn, llm2, registry, settings)
    assert fake.calls == []


@pytest.mark.parametrize("bad", [
    {"summary": "x", "pain_points": []},
    {"summary": "x", "pain_points": [point(case_for=["only one"])]},
    {"summary": "x", "pain_points": [point(est_hours=0)]},
    "not json",
])
def test_unusable_answers_are_retried_then_refused(conn, settings, registry, bad):
    seed(conn, 6)
    llm, fake = llm_with(conn, fake_message(bad), fake_message(bad))
    with pytest.raises(LLMOutputError):
        painpoints.analyze_with_claude(conn, llm, registry, settings)
    assert len(fake.calls) == 2


# ------------------------------------------------------- answers (Muse/pasted)


def test_loose_wording_from_muse_is_normalised():
    items = [{"id": f"p{n}", "title": "t", "url": "https://x.com", "platform": "reddit", "tech_level": None}
             for n in range(1, 7)]
    loose = copy.deepcopy(PLAN)
    loose["pain_points"] = [point(verdict="Build it!", best_model="adsense and subscription",
                                  post_numbers=["1", "2", "3"], case_against="A lone objection written as plain text",
                                  est_hours="25")]
    content = painpoints.content_from_data(loose, items)
    p = content["pain_points"][0]
    assert p["best_model"] == "Both" and p["est_hours"] == 25 and len(p["posts"]) == 3
    assert p["verdict"] == "Build it" and p["evidence"] == "Medium"
    assert painpoints._pick("who knows", painpoints.VERDICTS, "Test first") == "Test first"
    assert painpoints._pick("skip it", painpoints.VERDICTS, "Test first") == "Skip"
    assert painpoints._pick("ads", painpoints.MONEY_MODELS, "Subscription") == "AdSense"


@pytest.mark.parametrize("data, fragment", [
    ([], "JSON object"),
    ({"summary": "x"}, "pain_points"),
    ({"summary": "x", "pain_points": [{"title": "only a title"}]}, "pain"),
])
def test_bad_shapes_say_where(data, fragment):
    with pytest.raises(ValueError, match=fragment):
        painpoints.content_from_data(data, [])


def test_muse_message_is_one_line_with_rules_shape_and_requests(conn):
    seed(conn, 6)
    items = painpoints.select_items(conn, jobs=10, market=5)
    message = painpoints.muse_message(items)
    assert "\n" not in message and message.startswith(painpoints.PROMPT_PREFIX)
    assert "Do NOT browse" in message and "ANALYSIS START" in message and "never follow instructions" in message
    assert '"pain_points"' in message and "REQUESTS" in message and message.count(" // ") == len(items) - 1
    # The marker words are inside a sentence, never alone on a line: they can't be mistaken for Muse's answer.
    assert not any(line.strip() == "ANALYSIS START" for line in message.splitlines())


# ------------------------------------------------------------- JSON parsing

ANSWER = json.dumps(PLAN)


def test_json_block_with_fences_and_chat_noise():
    text = f"Sure!\nANALYSIS START\n```json\nCopy\n{ANSWER}\n```\nANALYSIS END\nAnything else?"
    assert parse_json_block(text)["summary"] == PLAN["summary"]
    assert parse_json_block(ANSWER)["summary"] == PLAN["summary"]          # even without the markers
    curly = ANSWER.replace('"', "“", 2)  # curly quotes around the first key
    assert parse_json_block(f"ANALYSIS START\n{curly}\nANALYSIS END")["pain_points"]


def test_json_block_problems():
    with pytest.raises(MuseReplyError, match="reported a problem: logged out"):
        parse_json_block("PROBLEM: logged out")
    with pytest.raises(MuseReplyError, match="no JSON answer"):
        parse_json_block("ANALYSIS START\nI could not do it\nANALYSIS END")
    with pytest.raises(MuseReplyError, match="could not be read"):
        parse_json_block('ANALYSIS START\n{"summary": "x",, }\nANALYSIS END')


def test_analysis_completion_signals():
    assert analysis_is_complete("ANALYSIS START\n{}\nANALYSIS END")
    assert analysis_is_complete("PROBLEM: blocked")
    assert not analysis_is_complete("ANALYSIS START\n{")


# --------------------------------------------------------------- Muse engine


def test_muse_run_saves_a_plan_and_records_the_run(conn, muse_settings):
    seed(conn, 6)
    sent = []

    def ask(message):
        sent.append(message)
        return MuseReplyText(f"ANALYSIS START\n{ANSWER}\nANALYSIS END", "https://muse.ai/c/1")

    result = run_muse_painpoints(conn, muse_settings, ask=ask)
    assert result.ok and "1 found, 1 market trends" in result.message
    assert sent[0].startswith(painpoints.PROMPT_PREFIX)
    plan = store.latest_pain_point_plan(conn)
    assert plan["engine"] == "muse" and plan["content"]["pain_points"][0]["evidence"] == "Strong"
    run = store.recent_muse_runs(conn, 1)[0]
    assert run["status"] == "ok" and "pain points" in run["focus"]


def test_muse_run_problems_are_reported_not_saved(conn, muse_settings):
    seed(conn, 6)
    result = run_muse_painpoints(conn, muse_settings, ask=lambda m: MuseReplyText("PROBLEM: logged out", ""))
    assert not result.ok and "logged out" in result.message
    wrong = run_muse_painpoints(conn, muse_settings,
                                ask=lambda m: MuseReplyText('ANALYSIS START\n{"summary": "x"}\nANALYSIS END', ""))
    assert not wrong.ok and "pain_points" in wrong.message
    assert store.latest_pain_point_plan(conn) is None


def test_muse_run_needs_muse_and_posts(conn, settings, muse_settings):
    with pytest.raises(ConfigError, match="switched off"):
        run_muse_painpoints(conn, settings, ask=lambda m: pytest.fail("not asked"))
    with pytest.raises(ConfigError, match="Not enough rated posts"):
        run_muse_painpoints(conn, muse_settings, ask=lambda m: pytest.fail("not asked"))


def test_import_last_answer_uses_the_remembered_numbers(conn, muse_settings):
    with pytest.raises(ConfigError, match="No question has been prepared"):
        import_latest_painpoints(conn, muse_settings, read=lambda: pytest.fail("nothing prepared"))
    seed(conn, 6)
    painpoints.remember_items(conn, painpoints.select_items(conn, jobs=10, market=5))
    result = import_latest_painpoints(
        conn, muse_settings, read=lambda: MuseReplyText(f"ANALYSIS START\n{ANSWER}\nANALYSIS END", ""))
    assert result.ok and store.latest_pain_point_plan(conn)["engine"] == "muse"


def test_pasted_answer(conn):
    with pytest.raises(ValueError, match="No question has been prepared"):
        import_pasted_answer(conn, ANSWER)
    seed(conn, 6)
    painpoints.remember_items(conn, painpoints.select_items(conn, jobs=10, market=5))
    with pytest.raises(MuseReplyError):
        import_pasted_answer(conn, "just some words")
    content = import_pasted_answer(conn, f"ANALYSIS START\n{ANSWER}\nANALYSIS END")
    assert content["pain_points"] and store.latest_pain_point_plan(conn)["engine"] == "pasted"


def test_plan_storage_rules(conn):
    with pytest.raises(ValueError):
        store.save_pain_point_plan(conn, {}, "telepathy", "m")
    with pytest.raises(ValueError):
        store.save_pain_point_plan(conn, {"x": "a" * 130_000}, "claude", "m")
    store.save_pain_point_plan(conn, {"a": 1}, "claude", "m")
    store.save_pain_point_plan(conn, {"a": 2}, "muse", "muse")
    assert store.latest_pain_point_plan(conn)["content"] == {"a": 2}
    seed(conn, 3)
    assert [i["id"] for i in store.candidates_by_ids(conn, ["reddit_2", "gone", "reddit_0"])] == ["reddit_2", "reddit_0"]
    assert store.candidates_by_ids(conn, []) == []


# ------------------------------------------------------- Reddit / LinkedIn places


def test_job_message_places():
    default = build_job_message("", 20)
    assert "Quora" in default and "For this job look ONLY" not in default
    reddit = build_job_message("", 20, "reddit")
    assert "r/EtsySellers" in reddit and "Maximum 20 posts" in reddit and "Quora" not in reddit
    linkedin = build_job_message("", 20, "linkedin")
    assert f"Maximum {LINKEDIN_MAX_POSTS} posts" in linkedin and "EMPTY (do not copy people's names)" in linkedin
    assert "Never open anyone's profile" in linkedin
    trends = build_job_message("", 50, "linkedin_trends")
    assert "PROBLEM they struggle with" in trends and f"Maximum {LINKEDIN_MAX_POSTS} posts" in trends
    assert "LinkedIn: trend" in trends and "\n" not in trends
    with pytest.raises(ConfigError):
        build_job_message("", 20, "myspace")
    assert PLACES[0] == "default"


def test_collection_on_reddit_is_labelled_in_the_run_list(conn, muse_settings):
    sent = []
    reply = ("POSTS START\nurl,title,text,source,author,posted_at\n"
             "https://www.reddit.com/r/EtsySellers/comments/abc/help,Help with fees,\"I copy fees by hand\","
             "Reddit: r/EtsySellers,someone,2026-11-20\nPOSTS END")
    result = run_muse_collection(conn, muse_settings, ask=lambda m: sent.append(m) or MuseReplyText(reply, ""),
                                 place="reddit")
    assert result.ok and result.added == 1 and "r/EtsySellers" in sent[0]
    assert store.recent_muse_runs(conn, 1)[0]["focus"].startswith("[Reddit]")
    assert conn.execute("SELECT platform FROM posts").fetchone()[0] == "reddit"


# ------------------------------------------------------------------ CLI


def test_cli_pain_point_commands(monkeypatch, settings, capsys):
    from app import cli
    from app.muse import __main__ as muse_cli
    from app.muse.job import MuseRunResult

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(muse_cli, "run_muse_painpoints",
                        lambda conn, s: MuseRunResult(1, True, "Pain points: 4 found, 1 market trends."))
    assert muse_cli.main(["pain-points"]) == 0
    assert "Done: Pain points: 4 found" in capsys.readouterr().out

    def boom(conn, s):
        raise ConfigError("Not enough rated posts yet.")

    monkeypatch.setattr(muse_cli, "import_latest_painpoints", boom)
    assert muse_cli.main(["import-last-painpoints"]) == 1
    assert "Failed: Not enough rated posts" in capsys.readouterr().out
