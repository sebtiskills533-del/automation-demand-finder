"""Muse integration: settings, Chrome connection, chat driving, reply parsing and runs.
No real Chrome or muse.ai is used: the browser is replaced by small fakes."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app import store
from app.config import MuseSettings, load_settings
from app.domain import utc_now
from app.errors import (
    ConfigError,
    LLMAuthError,
    MuseBrowserError,
    MuseBusyError,
    MuseChatNotFoundError,
    MuseLoggedOutError,
    MuseReplyError,
    MuseTimeoutError,
)
from app.muse import browser, client, selectors
from app.muse.client import MuseReplyText, ask_muse, extract_reply, wait_for_stable_text
from app.muse.job import build_job_message, clean_focus, run_muse_collection
from app.muse.parse import parse_muse_reply

GOOD_REPLY = """POSTS START
```csv
url,title,text,source,author,posted_at
https://www.quora.com/How-to-automate-Etsy-orders,How to automate Etsy orders?,"I copy 40 orders a week by hand. Call 555-123-4567",Quora,Maria L,2026-11-17
https://www.facebook.com/groups/1/posts/2,Form that emails me and adds a row,"Will pay someone to set it up.",Facebook: Small Biz,Jake P,2026-11-18
```
Copy
POSTS END"""


# ------------------------------------------------------------- settings


def test_muse_is_off_by_default_and_uses_its_own_port():
    muse = load_settings(env={}, env_file=None).muse
    assert muse.enabled is False
    assert muse.browser_port == 9223  # never the SavorFable Chrome on 9222
    assert muse.chat_name == "AUTOMATION DEMAND FINDER"
    assert muse.max_posts == 20


def test_muse_settings_are_read():
    muse = load_settings(
        env={"MUSE_ENABLED": "yes", "MUSE_CHAT": "My finder", "MUSE_MAX_POSTS": "5", "MUSE_BROWSER_PORT": "9300"},
        env_file=None,
    ).muse
    assert (muse.enabled, muse.chat_name, muse.max_posts, muse.browser_port) == (True, "My finder", 5, 9300)


@pytest.mark.parametrize(
    "env",
    [{"MUSE_ENABLED": "maybe"}, {"MUSE_MAX_POSTS": "500"}, {"MUSE_BROWSER_PORT": "80"},
     {"MUSE_CHAT": "x" * 61}, {"MUSE_CHAT": "bad\tname"}],
)
def test_invalid_muse_settings(env):
    with pytest.raises(ConfigError):
        load_settings(env=env, env_file=None)


# --------------------------------------------------------------- chrome


def test_chrome_args_are_loopback_only_with_own_profile(tmp_path):
    muse = MuseSettings(profile_dir=tmp_path / "profile", browser_port=9223)
    args = browser.chrome_args(muse, open_url="https://muse.ai/")
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=9223" in args
    assert f"--user-data-dir={tmp_path / 'profile'}" in args
    assert args[-1] == "https://muse.ai/"


def test_launch_chrome_missing_chrome(tmp_path):
    with pytest.raises(MuseBrowserError, match="CHROME_PATH"):
        browser.launch_chrome(MuseSettings(chrome_path=tmp_path / "nope.exe"))


def test_launch_chrome_starts_detached_process(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("")
    calls = []
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    browser.open_for_setup(MuseSettings(chrome_path=chrome, profile_dir=tmp_path / "p"))
    args, options = calls[0]
    assert args[0] == str(chrome) and args[-1] == "https://muse.ai/"
    assert (tmp_path / "p").is_dir()
    assert "shell" not in options


class FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts
        self.closed = False

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, fail_times, contexts=("ctx",)):
        self.fail_times = fail_times
        self.browser = FakeBrowser(list(contexts))
        self.stopped = False
        self.attempts = 0
        self.chromium = self

    def connect_over_cdp(self, endpoint):
        assert endpoint == "http://127.0.0.1:9223"
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("connect ECONNREFUSED")
        return self.browser

    def stop(self):
        self.stopped = True


def test_connect_launches_chrome_when_not_running(monkeypatch):
    launched = []
    monkeypatch.setattr(browser, "launch_chrome", lambda muse, open_url=None: launched.append(1))
    fake = FakePlaywright(fail_times=3)
    with browser.connected_context(MuseSettings(), playwright_factory=lambda: fake, sleep=lambda s: None) as ctx:
        assert ctx == "ctx"
    assert launched == [1] and fake.attempts == 4
    assert fake.browser.closed and fake.stopped  # disconnects, never leaves Playwright running


def test_connect_gives_up_with_clear_message(monkeypatch):
    monkeypatch.setattr(browser, "launch_chrome", lambda muse, open_url=None: None)
    fake = FakePlaywright(fail_times=999)
    with pytest.raises(MuseBrowserError, match="could not be reached"):
        with browser.connected_context(MuseSettings(), playwright_factory=lambda: fake, sleep=lambda s: None):
            pass
    assert fake.stopped


def test_connect_without_profile_window():
    fake = FakePlaywright(fail_times=0, contexts=())
    with pytest.raises(MuseBrowserError, match="no open profile"):
        with browser.connected_context(MuseSettings(), playwright_factory=lambda: fake, sleep=lambda s: None):
            pass


def test_browser_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "muse.lock"
    with browser.BrowserLock(path):
        assert path.exists()
        with pytest.raises(MuseBusyError):
            with browser.BrowserLock(path):
                pass
    assert not path.exists()
    with browser.BrowserLock(path):  # can be taken again
        pass


def test_browser_lock_takes_over_stale_lock(tmp_path):
    path = tmp_path / "muse.lock"
    path.write_text("123 0")
    future = lambda: path.stat().st_mtime + browser.STALE_LOCK_SECONDS + 1  # noqa: E731
    with browser.BrowserLock(path, clock=future):
        assert path.exists()


# --------------------------------------------------------- waiting/reply


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_returns_after_text_is_quiet():
    clock = Clock()
    texts = iter(["", "Working", "Working on it", "POSTS START"])
    last = {"t": ""}

    def read():
        last["t"] = next(texts, last["t"])
        return last["t"]

    result = wait_for_stable_text(read, quiet_seconds=60, timeout_seconds=600, clock=clock.time, sleep=clock.sleep)
    assert result == "POSTS START"
    assert 60 <= clock.now < 70


def test_wait_times_out_while_still_changing():
    clock = Clock()
    counter = iter(range(10_000))
    with pytest.raises(MuseTimeoutError):
        wait_for_stable_text(lambda: str(next(counter)), quiet_seconds=5, timeout_seconds=30,
                             clock=clock.time, sleep=clock.sleep)


def test_wait_never_accepts_empty_text():
    clock = Clock()
    with pytest.raises(MuseTimeoutError):
        wait_for_stable_text(lambda: "", quiet_seconds=5, timeout_seconds=30, clock=clock.time, sleep=clock.sleep)


def test_extract_reply():
    msg = "FIND POSTS. Focus: x."
    assert extract_reply("old chat", "old chat" + msg + "\nPOSTS START", msg) == "POSTS START"
    # Long chat whose start scrolled away: use the LAST copy of our message.
    assert extract_reply("not a prefix", msg + "\nold reply\n" + msg + "\nnew reply", msg) == "new reply"
    assert extract_reply("a", "a fresh text", msg) == "fresh text"


# ------------------------------------------------------- chat driving


class FakeLocator:
    """Like Playwright: names may be regexes, and an element shown twice can only
    be checked through `.first`."""

    def __init__(self, page, role, name, first=False):
        self.page, self.role, self.name, self._first = page, role, name, first

    @property
    def first(self):
        return FakeLocator(self.page, self.role, self.name, first=True)

    @property
    def key(self):
        """The visible element this locator resolves to (or its own name if none)."""
        for role, label in self.page.visible:
            if role != self.role:
                continue
            if (self.name.search(label) if isinstance(self.name, re.Pattern) else label == self.name):
                return (role, label)
        return (self.role, self.name)

    def is_visible(self):
        if self.key in self.page.duplicates and not self._first:
            raise RuntimeError(f"strict mode violation: {self.key} resolved to 2 elements")
        return self.key in self.page.visible

    def wait_for(self, state, timeout):
        if self.key not in self.page.visible:
            raise TimeoutError("not visible")

    def click(self):
        self.page.clicks.append(self.key)
        if self.key == CHATS:  # opening the collapsed chat list shows the chats
            self.page.visible |= self.page.behind_chats_button

    def fill(self, text):
        self.page.typed = text

    def press(self, key):
        if key == "Enter":
            self.page.log_text += self.page.typed + "\n" + self.page.reply

    def inner_text(self):
        return self.page.log_text


class FakePage:
    url = "https://muse.ai/chat/abc"

    def __init__(self, visible, reply="POSTS START", log_text="earlier messages\n", duplicates=(),
                 behind_chats_button=()):
        self.visible, self.reply, self.log_text = set(visible), reply, log_text
        self.duplicates = set(duplicates)
        self.behind_chats_button = set(behind_chats_button)
        self.clicks, self.typed, self.closed, self.pressed = [], "", False, []

    @property
    def keyboard(self):
        return self

    def press(self, key):
        self.pressed.append(key)

    def goto(self, url, wait_until=None):
        assert url == "https://muse.ai/"

    def get_by_role(self, role, name, exact=False):
        return FakeLocator(self, role, name)

    def locator(self, selector):
        assert selector == selectors.MESSAGE_LOG
        return FakeLocator(self, "log", "log")

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        self.closed = True


COMPOSER = ("textbox", "Message")
LOGIN = ("button", "Log in")
CHATS = ("button", "Chats")
CHAT = ("button", "AUTOMATION DEMAND FINDER")


class FakeContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


def no_wait(read):
    return read()


def test_ask_muse_with_chat_list_already_open():
    # The real wide-window layout (2026-11-19): side chats are listed, no "Chats" button.
    page = FakePage({COMPOSER, CHAT}, reply="POSTS START\n...")
    result = ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "FIND POSTS.", wait=no_wait)
    assert result == MuseReplyText("POSTS START\n...", "https://muse.ai/chat/abc")
    assert page.clicks[0] == CHAT and CHATS not in page.clicks
    assert "Escape" not in page.pressed  # nothing to close
    assert page.typed == "FIND POSTS." and page.closed


def test_ask_muse_opens_collapsed_chat_list():
    page = FakePage({COMPOSER, CHATS}, behind_chats_button={CHAT})
    ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "FIND POSTS.", wait=no_wait)
    assert page.clicks[:2] == [CHATS, CHAT]
    assert "Escape" in page.pressed  # closes the panel it opened


def test_chat_with_unread_updates_is_found():
    unread = ("button", "AUTOMATION DEMAND FINDER Unread updates")
    page = FakePage({COMPOSER, unread})
    ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "FIND POSTS.", wait=no_wait)
    assert page.clicks[0] == unread


def test_similar_chat_names_are_not_confused():
    page = FakePage({COMPOSER, ("button", "AUTOMATION DEMAND FINDER OLD")})
    with pytest.raises(MuseChatNotFoundError):
        ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "x", wait=no_wait)


def test_ask_muse_logged_out():
    page = FakePage({LOGIN})
    with pytest.raises(MuseLoggedOutError, match="log in"):
        ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "x", wait=no_wait)
    assert page.closed


def test_logged_out_page_with_two_login_buttons_is_detected():
    # Regression: the real muse.ai logged-out page shows "Log in" twice.
    page = FakePage({LOGIN}, duplicates={LOGIN})
    with pytest.raises(MuseLoggedOutError):
        ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "x", wait=no_wait)


def test_page_that_never_loads():
    with pytest.raises(MuseBrowserError, match="did not finish loading"):
        ask_muse(FakeContext(FakePage(set())), "AUTOMATION DEMAND FINDER", "x", wait=no_wait)


def test_ask_muse_missing_chat():
    page = FakePage({COMPOSER})
    with pytest.raises(MuseChatNotFoundError, match="AUTOMATION DEMAND FINDER"):
        ask_muse(FakeContext(page), "AUTOMATION DEMAND FINDER", "x", wait=no_wait)


# --------------------------------------------------------------- parsing


def test_parse_good_reply_ignores_code_fences_and_copy_button():
    reply = parse_muse_reply(GOOD_REPLY, max_posts=20)
    assert [r["source"] for r in reply.rows] == ["Quora", "Facebook: Small Biz"]
    assert reply.note == ""


def test_parse_without_markers_stops_at_sign_off():
    text = ("Here you go!\nurl,title,text,source,author,posted_at\n"
            "https://www.quora.com/q1,Q1,\"t\",Quora,a,\n2 minutes ago\nWant me to find more?")
    assert len(parse_muse_reply(text, 20).rows) == 1


def test_parse_strips_invisible_characters():
    text = GOOD_REPLY.replace("POSTS START", "​POSTS START")
    assert len(parse_muse_reply(text, 20).rows) == 2


def test_parse_limits_number_of_posts():
    reply = parse_muse_reply(GOOD_REPLY, max_posts=1)
    assert len(reply.rows) == 1 and "only the first 1" in reply.note


def test_parse_no_posts_found():
    reply = parse_muse_reply("NO POSTS FOUND", 20)
    assert reply.rows == [] and "no matching posts" in reply.note


def test_parse_no_posts_found_with_reason():
    reply = parse_muse_reply("NO POSTS FOUND: searched Quora and 3 Facebook groups, only ads", 20)
    assert reply.rows == [] and reply.note.endswith("Reason: searched Quora and 3 Facebook groups, only ads")


def test_parse_problem_line():
    with pytest.raises(MuseReplyError, match="logged out of Facebook"):
        parse_muse_reply("PROBLEM: logged out of Facebook", 20)


def test_parse_unusable_reply():
    with pytest.raises(MuseReplyError, match="expected format"):
        parse_muse_reply("Sure! I found some great posts for you.", 20)


# ------------------------------------------------------------------ jobs


@pytest.fixture
def muse_settings(settings):
    return replace(settings, muse=replace(settings.muse, enabled=True))


def test_job_message_and_focus():
    assert "Focus: Etsy sellers." in build_job_message("Etsy sellers", 20)
    message = build_job_message("", 20)
    assert "Maximum 20 posts" in message
    assert "browser" in message and "PROBLEM:" in message and "NO POSTS FOUND:" in message
    assert "\n" not in message  # typed as one line; Enter would send it early
    assert clean_focus("  Etsy \n sellers ") == "Etsy sellers"
    with pytest.raises(ConfigError):
        clean_focus("x" * 101)


def test_job_refuses_when_disabled(conn, settings):
    with pytest.raises(ConfigError, match="MUSE_ENABLED"):
        run_muse_collection(conn, settings, ask=lambda m: MuseReplyText(GOOD_REPLY, ""))


def test_job_imports_rates_and_records_run(conn, muse_settings):
    sent = []

    def ask(message):
        sent.append(message)
        return MuseReplyText(GOOD_REPLY, "https://muse.ai/chat/abc")

    result = run_muse_collection(conn, muse_settings, "Etsy sellers", ask=ask, rate=lambda: 2)
    assert result.ok and (result.added, result.rated) == (2, 2)
    assert "Focus: Etsy sellers" in sent[0]
    body = conn.execute("SELECT body FROM posts WHERE platform = 'quora'").fetchone()[0]
    assert "555-123-4567" not in body
    run = store.recent_muse_runs(conn)[0]
    assert (run["status"], run["added"], run["chat_url"]) == ("ok", 2, "https://muse.ai/chat/abc")
    assert not (muse_settings.db_path.parent / "muse.lock").exists()  # lock released


def test_job_second_run_counts_duplicates(conn, muse_settings):
    ask = lambda m: MuseReplyText(GOOD_REPLY, "")  # noqa: E731
    run_muse_collection(conn, muse_settings, ask=ask)
    result = run_muse_collection(conn, muse_settings, ask=ask, rate=lambda: pytest.fail("nothing new to rate"))
    assert (result.added, result.duplicates) == (0, 2)


@pytest.mark.parametrize(
    ("ask", "match"),
    [
        (lambda m: MuseReplyText("PROBLEM: Instagram blocked me", ""), "Instagram blocked me"),
        (lambda m: (_ for _ in ()).throw(MuseLoggedOutError("muse.ai is logged out")), "logged out"),
        (lambda m: (_ for _ in ()).throw(MuseTimeoutError("did not finish")), "did not finish"),
    ],
)
def test_job_failures_are_recorded(conn, muse_settings, ask, match):
    result = run_muse_collection(conn, muse_settings, ask=ask)
    assert not result.ok and re.search(match, result.message)
    assert store.recent_muse_runs(conn)[0]["status"] == "failed"
    assert not (muse_settings.db_path.parent / "muse.lock").exists()


def test_job_rating_failure_keeps_successful_import(conn, muse_settings):
    def rate():
        raise LLMAuthError("Claude rejected the API key")

    result = run_muse_collection(conn, muse_settings, ask=lambda m: MuseReplyText(GOOD_REPLY, ""), rate=rate)
    assert result.ok and result.added == 2
    assert "Rating failed" in result.message


def test_job_refuses_while_another_run_holds_the_lock(conn, muse_settings):
    with browser.BrowserLock(muse_settings.db_path.parent / "muse.lock"):
        result = run_muse_collection(conn, muse_settings, ask=lambda m: MuseReplyText(GOOD_REPLY, ""))
    assert not result.ok and "still running" in result.message


def test_stale_running_rows_are_failed(conn):
    run_id = store.start_muse_run(conn, "")
    assert store.fail_stale_muse_runs(conn, utc_now() - timedelta(minutes=45)) == 0
    assert store.fail_stale_muse_runs(conn, utc_now() + timedelta(minutes=1)) == 1
    run = store.recent_muse_runs(conn)[0]
    assert run["id"] == run_id and run["status"] == "failed" and "Interrupted" in run["message"]


# ------------------------------------------------------------------- CLI


def test_cli_open_chrome_and_run(monkeypatch, settings, capsys):
    from app import cli
    from app.muse import __main__ as muse_cli
    from app.muse.job import MuseRunResult

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    opened = []
    monkeypatch.setattr(muse_cli, "open_for_setup", lambda muse: opened.append(muse))
    assert muse_cli.main(["open-chrome"]) == 0 and opened

    seen = {}

    def fake_run(conn, settings_, focus, rate=None):
        seen["focus"], seen["rate"] = focus, rate
        return MuseRunResult(1, True, "Import: 2 added.", added=2, chat_url="https://muse.ai/c")

    monkeypatch.setattr(muse_cli, "run_muse_collection", fake_run)
    assert muse_cli.main(["run", "--focus", "Etsy"]) == 0
    assert seen["focus"] == "Etsy" and seen["rate"] is not None  # API key present -> rates
    assert muse_cli.main(["run", "--no-rate"]) == 0 and seen["rate"] is None
    assert "Done: Import: 2 added." in capsys.readouterr().out


def test_procedure_document_matches_parser():
    """The example reply in the procedure Muse is given must parse."""
    doc = (Path(__file__).parent.parent / "docs" / "muse-finder-procedure.md").read_text(encoding="utf-8")
    example = doc.split("POSTS START", 2)[1].split("POSTS END")[0]  # first example after section 4
    reply = parse_muse_reply("POSTS START" + example + "POSTS END", 20)
    assert len(reply.rows) == 2
    assert all(r["url"].startswith("https://") for r in reply.rows)
    assert client.QUIET_SECONDS == 60 and client.REPLY_TIMEOUT_SECONDS == 1800
