"""Every draft written for a stranger follows the same clear-and-convincing rules."""

from __future__ import annotations

import pytest

from app import emails, replies, reports, store
from app.config import CONFIG_DIR
from app.llm import ClaudeClient
from app.models_registry import load_registry
from app.writing import CLEAR_AND_CONVINCING, delivery_hint
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


def test_stack_exchange_rule_survives_the_new_style():
    assert "On Stack Exchange never offer help or services" in replies.SYSTEM_PROMPT
