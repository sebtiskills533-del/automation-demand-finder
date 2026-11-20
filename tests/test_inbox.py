"""Replies people sent the user: privacy rules, linking, Muse jobs, email drafts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app import store
from app.config import CONFIG_DIR
from app.emails import MAILTO_MAX_CHARS, draft_email, mailto_link
from app.errors import ConfigError, LLMOutputError
from app.importer import import_rows
from app.inbox import add_manual_reply, ingest_replies, post_anchor
from app.llm import ClaudeClient
from app.models_registry import load_registry
from app.muse.client import MuseReplyText, latest_reply_in
from app.muse.job import (
    REPLIES_PREFIX,
    build_replies_message,
    import_latest_replies,
    run_reply_check,
)
from app.muse.parse import REPLIES, is_complete, parse_block
from app.privacy import extract_email, is_valid_email
from tests.conftest import make_analysis
from tests.fakes import FakeAnthropic, fake_message

FB_POST = "https://www.facebook.com/groups/1129871225741454/?multi_permalinks=1471413221587251&hoisted=x"
FB_COMMENT = "https://www.facebook.com/groups/1129871225741454/posts/1471413221587251/?comment_id=99"

REPLIES_ANSWER = "\n".join([
    "Checking your notifications...",
    "REPLIES START",
    "url,their_name,their_reply,replied_at",
    f'{FB_COMMENT},Sara K,"Yes please! Email me at Sara.K@example.com or call 0300 1234567",2026-11-18',
    'https://www.quora.com/How-do-I-automate-invoices,Tom,"Thanks, that formula worked",2026-11-17',
    "REPLIES END",
])


@pytest.fixture
def registry():
    return load_registry(CONFIG_DIR / "models.json")


@pytest.fixture
def muse_settings(settings):
    return replace(settings, muse=replace(settings.muse, enabled=True))


def seed_fb_post(conn):
    import_rows(conn, [{"url": FB_POST, "title": "Looking for an AI automation expert"}])
    return conn.execute("SELECT id FROM posts").fetchone()[0]


# ----------------------------------------------------------- privacy


def test_extract_email_only_from_the_reply_text():
    assert extract_email("Email me at Sara.K@example.com please") == "sara.k@example.com"
    assert extract_email("no address here") is None
    assert extract_email(None) is None
    assert is_valid_email("a@b.co") and not is_valid_email("a@b") and not is_valid_email("x" * 250 + "@b.co")


def test_reply_keeps_their_email_but_never_phone_numbers(conn):
    report = add_manual_reply(conn, FB_COMMENT, "Sara", "Email me at sara@example.com or call 0300 1234567")
    assert report.added == 1 and report.with_email == 1
    reply = store.list_replies(conn)[0]
    assert reply["email"] == "sara@example.com"
    assert "sara@example.com" not in reply["text"] and "1234567" not in reply["text"]
    assert reply["seen"] == 0


def test_email_column_alone_is_ignored(conn):
    # Muse is told never to look up emails; an address not written in the reply is not kept.
    ingest_replies(conn, [{"url": FB_COMMENT, "their_name": "X", "their_reply": "Interested!",
                           "email": "looked-up@example.com"}])
    assert store.list_replies(conn)[0]["email"] is None


# ----------------------------------------------------------- linking


def test_facebook_link_shapes_point_to_the_same_post():
    assert post_anchor(FB_POST) == post_anchor(FB_COMMENT) == "fb:1471413221587251"
    assert post_anchor("https://www.quora.com/unanswered/How-x?share=1") == post_anchor("https://quora.com/How-x")


def test_replies_are_linked_to_posts_and_counted(conn):
    post_id = seed_fb_post(conn)
    report = ingest_replies(conn, parse_block(REPLIES_ANSWER, REPLIES, 100).rows)
    assert (report.added, report.linked, report.with_email) == (2, 1, 1)
    assert [r["author"] for r in store.list_replies(conn, post_id=post_id)] == ["Sara K"]
    assert store.unseen_reply_count(conn) == 2 and store.unseen_reply_count(conn, post_id) == 1
    store.mark_replies_seen(conn, [r["id"] for r in store.list_replies(conn)])
    assert store.unseen_reply_count(conn) == 0
    assert store.list_replies(conn, unseen_only=True) == []


def test_a_linked_reply_ticks_the_post_as_answered(conn):
    post_id = seed_fb_post(conn)
    store.save_analysis(conn, make_analysis(post_id))
    assert store.get_post_details(conn, post_id)["answered_at"] is None
    ingest_replies(conn, parse_block(REPLIES_ANSWER, REPLIES, 100).rows)
    first = store.get_post_details(conn, post_id)["answered_at"]
    assert first
    assert [p["id"] for p in store.answered_posts(conn)] == [post_id]
    # A later reply never moves the date, and an untick by the user is respected until a new reply.
    store.set_answered(conn, post_id, False)
    assert store.get_post_details(conn, post_id)["answered_at"] is None


def test_replies_can_be_ticked_and_unticked_and_filtered(conn):
    seed_fb_post(conn)
    ingest_replies(conn, parse_block(REPLIES_ANSWER, REPLIES, 100).rows)
    ids = [r["id"] for r in store.list_replies(conn)]
    store.mark_replies_seen(conn, ids[:1])
    assert [r["id"] for r in store.list_replies(conn, seen=True)] == ids[:1]
    assert [r["id"] for r in store.list_replies(conn, seen=False)] == ids[1:]
    store.mark_replies_seen(conn, ids[:1], seen=False)
    assert store.unseen_reply_count(conn) == 2
    platforms = {r["platform"] for r in store.list_replies(conn)}
    assert len(store.list_replies(conn, platforms=tuple(platforms))) == 2
    assert store.list_replies(conn, platforms=("hackernews",)) == []
    with pytest.raises(ValueError):
        store.list_replies(conn, platforms=("myspace",))


def test_same_reply_twice_is_saved_once(conn):
    rows = parse_block(REPLIES_ANSWER, REPLIES, 100).rows
    ingest_replies(conn, rows)
    again = ingest_replies(conn, rows)
    assert (again.added, again.duplicates) == (0, 2)


@pytest.mark.parametrize(
    ("row", "error"),
    [({"url": "http://x.com/a", "their_reply": "hi"}, "https"),
     ({"url": "https://x.com/a", "their_reply": "  "}, "empty")],
)
def test_bad_replies_are_skipped_with_a_reason(conn, row, error):
    report = ingest_replies(conn, [row])
    assert report.added == 0 and error in report.errors[0]


def test_deleting_a_post_keeps_its_replies(conn):
    post_id = seed_fb_post(conn)
    add_manual_reply(conn, FB_COMMENT, "Sara", "Interested")
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    assert store.list_replies(conn)[0]["post_id"] is None


# -------------------------------------------------------------- Muse


def test_replies_format_parses_and_completes():
    assert is_complete(REPLIES_ANSWER, REPLIES)
    assert is_complete("NO REPLIES FOUND: nothing new on Quora", REPLIES)
    assert not is_complete("REPLIES START\nurl,their_name,their_reply", REPLIES)
    assert parse_block("NO REPLIES FOUND: quiet", REPLIES, 100).note.endswith("Reason: quiet")


def test_reply_job_message_is_read_only_and_one_line():
    message = build_replies_message(7)
    assert message.startswith(REPLIES_PREFIX) and "last 7 days" in message
    assert "Read only" in message and "Never look up anyone's email" in message
    assert "\n" not in message


def test_run_reply_check_saves_replies(conn, muse_settings):
    sent = []

    def ask(message):
        sent.append(message)
        return MuseReplyText(REPLIES_ANSWER, "https://muse.ai/t")

    result = run_reply_check(conn, muse_settings, 7, ask=ask)
    assert result.ok and result.added == 2 and "with an email" in result.message
    assert "last 7 days" in sent[0]
    assert store.recent_muse_runs(conn)[0]["focus"] == "(checked replies to me)"


def test_reply_check_validation_and_switch(conn, settings, muse_settings):
    with pytest.raises(ConfigError, match="MUSE_ENABLED"):
        run_reply_check(conn, settings, ask=lambda m: MuseReplyText("", ""))
    with pytest.raises(ConfigError):
        run_reply_check(conn, muse_settings, 0, ask=lambda m: MuseReplyText("", ""))


def test_reply_check_problem_is_recorded(conn, muse_settings):
    result = run_reply_check(conn, muse_settings, ask=lambda m: MuseReplyText("PROBLEM: logged out of Instagram", ""))
    assert not result.ok and "logged out of Instagram" in result.message


def test_import_latest_replies(conn, muse_settings):
    result = import_latest_replies(conn, muse_settings, read=lambda: MuseReplyText(REPLIES_ANSWER, ""))
    assert result.ok and result.added == 2


def test_latest_reply_in_finds_the_newest_replies_list():
    chat = "FIND POSTS. x\nPOSTS START\n...\nPOSTS END\n" + REPLIES_ANSWER
    assert latest_reply_in(chat, REPLIES_PREFIX, REPLIES.start).startswith("REPLIES START")


# ------------------------------------------------------------- email


EMAIL = {"subject": "Your Facebook automation question",
         "body": "Hi Sara,\n\nThanks for replying. A simple first step is ...\n\nBest regards,\n[Your name]"}


def llm_with(conn, *results):
    fake = FakeAnthropic(*results)
    sink = lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c)  # noqa: E731
    return ClaudeClient("key", sink, client=fake), fake


def test_draft_email_for_a_reply_with_an_email(conn, settings, registry):
    seed_fb_post(conn)
    add_manual_reply(conn, FB_COMMENT, "Sara", "Yes! sara@example.com")
    reply_id = store.list_replies(conn)[0]["id"]
    llm, fake = llm_with(conn, fake_message(EMAIL))
    draft = draft_email(conn, llm, registry, settings, reply_id)
    assert draft["subject"] == EMAIL["subject"]
    saved = store.get_reply(conn, reply_id)
    assert saved["email_body"].startswith("Hi Sara")
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    assert "<original_post>" in fake.calls[0]["messages"][0]["content"]


def test_no_email_draft_without_a_shared_email(conn, settings, registry):
    add_manual_reply(conn, FB_COMMENT, "Tom", "Thanks!")
    llm, fake = llm_with(conn)
    with pytest.raises(ValueError, match="didn't share an email"):
        draft_email(conn, llm, registry, settings, store.list_replies(conn)[0]["id"])
    assert fake.calls == []


def test_draft_email_rejects_overlong_answers(conn, settings, registry):
    add_manual_reply(conn, FB_COMMENT, "Sara", "sara@example.com")
    bad = {"subject": "x" * 300, "body": "hi"}
    llm, _ = llm_with(conn, fake_message(bad), fake_message(bad))
    with pytest.raises(LLMOutputError):
        draft_email(conn, llm, registry, settings, store.list_replies(conn)[0]["id"])


def test_cli_check_replies_and_import_last_replies(monkeypatch, settings, capsys):
    from app import cli
    from app.muse import __main__ as muse_cli
    from app.muse.job import MuseRunResult

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    seen = {}

    def fake_check(conn, settings_, days):
        seen["days"] = days
        return MuseRunResult(1, True, "Replies: 2 new replies.", added=2)

    monkeypatch.setattr(muse_cli, "run_reply_check", fake_check)
    monkeypatch.setattr(muse_cli, "import_latest_replies",
                        lambda conn, settings_: MuseRunResult(2, False, "Muse reported a problem: x"))
    assert muse_cli.main(["check-replies", "--days", "5"]) == 0 and seen["days"] == 5
    assert muse_cli.main(["import-last-replies"]) == 1
    out = capsys.readouterr().out
    assert "Done: Replies: 2 new replies." in out and "Failed: Muse reported a problem" in out


def test_mailto_link_is_encoded_and_safe():
    link = mailto_link("sara@example.com", "Hi & welcome", "Line 1\nLine 2")
    assert link == "mailto:sara@example.com?subject=Hi%20%26%20welcome&body=Line%201%0ALine%202"
    long_link = mailto_link("sara@example.com", "S", "word " * 2000)
    assert len(long_link) <= MAILTO_MAX_CHARS and "body=" not in long_link
    with pytest.raises(ValueError):
        mailto_link("sara@example.com?bcc=everyone@x.com", "S", "B")
