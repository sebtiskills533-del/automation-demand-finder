"""Every draft written for a stranger follows the same clear-and-convincing rules."""

from __future__ import annotations

import pytest

from app import emails, replies, reports, store
from app.config import CONFIG_DIR
from app.llm import ClaudeClient
from app.models_registry import load_registry
from app.writing import CLEAR_AND_CONVINCING, CONVINCING, TIP_FIRST, delivery_hint, get_style, set_style
from tests.conftest import make_analysis, make_post
from tests.fakes import FakeAnthropic, fake_message


@pytest.mark.parametrize("prompt", [replies.SYSTEM_PROMPT, reports.SYSTEM_PROMPT, emails.SYSTEM_PROMPT])
def test_all_drafts_share_the_writing_rules(prompt):
    assert CLEAR_AND_CONVINCING in prompt


def test_writing_rules_cover_understanding_plain_words_and_honesty():
    rules = CLEAR_AND_CONVINCING.lower()
    for idea in ("their own words", "plain words", "no jargon", "one practical tip",
                 "never invent numbers", "next step", "no pressure"):
        assert idea in rules, idea


def test_delivery_hint():
    assert delivery_hint(None) == ""
    assert delivery_hint({"delivery_min_days": None}) == ""
    hint = delivery_hint({"delivery_min_days": 1, "delivery_max_days": 3, "difficulty": "low", "est_hours": 2})
    assert "1-3 days" in hint


def test_reply_prompt_gets_the_delivery_time(conn, settings):
    store.save_post(conn, make_post("reddit_1"))
    store.save_analysis(conn, make_analysis("reddit_1", delivery_min_days=3, delivery_max_days=7,
                                            difficulty="medium", est_hours=6))
    fake = FakeAnthropic(fake_message({"reply": "Copying orders by hand eats your evenings. Here's a fix."}))
    llm = ClaudeClient("key", lambda *a: None, client=fake)
    replies.draft_reply(conn, llm, load_registry(CONFIG_DIR / "models.json"), settings, "reddit_1")
    assert "Realistic delivery time you may mention: 3-7 days" in fake.calls[0]["messages"][0]["content"]


def test_stack_exchange_rule_survives_both_styles():
    for style in (CONVINCING, TIP_FIRST):
        assert "On Stack Exchange never offer help or services" in replies.system_prompt(style)


def test_tip_first_style_brings_back_the_earlier_wording():
    reply_prompt = replies.system_prompt(TIP_FIRST)
    email_prompt = emails.system_prompt(TIP_FIRST)
    assert "Start with a genuinely useful, specific tip" in reply_prompt
    assert CLEAR_AND_CONVINCING not in reply_prompt
    assert "Refer to their actual problem" in email_prompt and CLEAR_AND_CONVINCING not in email_prompt


def test_style_is_remembered_and_validated(conn):
    assert get_style(conn) == CONVINCING  # default
    set_style(conn, TIP_FIRST)
    assert get_style(conn) == TIP_FIRST
    with pytest.raises(ValueError):
        set_style(conn, "shouty")
    store.set_setting(conn, "writing.style", "removed-style")
    assert get_style(conn) == CONVINCING  # unknown saved value falls back safely


def test_drafts_use_the_saved_style_unless_one_is_given(conn, settings):
    store.save_post(conn, make_post("reddit_1"))
    store.save_analysis(conn, make_analysis("reddit_1"))
    registry = load_registry(CONFIG_DIR / "models.json")
    answer = {"reply": "Use an onFormSubmit trigger so each form row lands in your sheet."}
    fake = FakeAnthropic(fake_message(answer), fake_message(answer))
    llm = ClaudeClient("key", lambda *a: None, client=fake)

    set_style(conn, TIP_FIRST)
    replies.draft_reply(conn, llm, registry, settings, "reddit_1")
    assert "Start with a genuinely useful, specific tip" in fake.calls[0]["system"]
    replies.draft_reply(conn, llm, registry, settings, "reddit_1", style=CONVINCING)
    assert CLEAR_AND_CONVINCING in fake.calls[1]["system"]
