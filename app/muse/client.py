"""Talk to a Muse chat: open it, send one message, wait for the full reply."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.errors import MuseBrowserError, MuseChatNotFoundError, MuseLoggedOutError, MuseTimeoutError
from app.muse import selectors

log = logging.getLogger(__name__)

# Muse is an agent: it works in steps and can go quiet between them, so a
# reply only counts as finished after this long without any new text.
QUIET_SECONDS = 60
REPLY_TIMEOUT_SECONDS = 30 * 60
POLL_SECONDS = 1.0
PAGE_LOAD_SECONDS = 30


@dataclass(frozen=True)
class MuseReplyText:
    text: str
    chat_url: str


def wait_for_stable_text(
    read: Callable[[], str],
    *,
    quiet_seconds: float = QUIET_SECONDS,
    timeout_seconds: float = REPLY_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Poll `read` until its (non-empty) text stops changing for `quiet_seconds`."""
    deadline = clock() + timeout_seconds
    last = ""
    quiet_since = clock()
    while clock() < deadline:
        text = read()
        if text != last:
            last = text
            quiet_since = clock()
        elif text and clock() - quiet_since >= quiet_seconds:
            return text
        sleep(poll_seconds)
    raise MuseTimeoutError("Muse's reply did not finish in time. Open the chat in the app's Chrome and look.")


def extract_reply(before: str, now: str, message: str) -> str:
    """The text Muse added after our message (the chat log also contains old messages)."""
    added = now[len(before):] if now.startswith(before) else now
    # The LAST copy of our message: earlier runs may have sent the same text,
    # and a long chat may not show its oldest messages (so `before` won't match).
    own = added.rfind(message)
    return (added if own == -1 else added[own + len(message):]).strip()


def open_chat(page, chat_name: str) -> None:
    """Go to muse.ai and open the named side chat (or 'Main chat')."""
    page.goto(f"{selectors.BASE_URL}/", wait_until="domcontentloaded")
    # .first everywhere: muse.ai shows the same button more than once (the logged-out
    # page has two "Log in" buttons), and Playwright refuses ambiguous checks.
    composer = page.get_by_role("textbox", name=selectors.COMPOSER_LABEL).first
    login = page.get_by_role("button", name=selectors.LOGIN_BUTTON, exact=True).first

    # Whichever appears first: the message box (logged in) or "Log in" (logged out).
    for _ in range(PAGE_LOAD_SECONDS):
        if _visible(login):
            raise MuseLoggedOutError(
                "muse.ai is logged out. Open the Muse Chrome from the Import page and log in once."
            )
        if _visible(composer):
            break
        page.wait_for_timeout(1000)
    else:
        raise MuseBrowserError(
            "muse.ai did not finish loading (no message box). Check the internet connection "
            "and look at the Muse Chrome window."
        )

    page.get_by_role("button", name=selectors.CHATS_BUTTON).first.click()
    chat = page.get_by_role("button", name=chat_name or selectors.MAIN_CHAT, exact=True).first
    try:
        chat.wait_for(state="visible", timeout=15_000)
    except Exception as exc:  # Playwright TimeoutError
        raise MuseChatNotFoundError(
            f'No Muse side chat named "{chat_name}". Create it in Muse (see the procedure) '
            "or fix MUSE_CHAT in .env."
        ) from exc
    chat.click()
    try:
        page.keyboard.press("Escape")  # close the chats panel
    except Exception:  # noqa: BLE001 - harmless if the panel is already closed
        pass


def ask_muse(
    context,
    chat_name: str,
    message: str,
    *,
    wait: Callable[..., str] = wait_for_stable_text,
) -> MuseReplyText:
    """Send `message` to the named Muse chat and return Muse's full reply text."""
    page = context.new_page()
    try:
        open_chat(page, chat_name)
        log_box = page.locator(selectors.MESSAGE_LOG).first
        composer = page.get_by_role("textbox", name=selectors.COMPOSER_LABEL).first

        def read_log() -> str:
            try:
                return log_box.inner_text() or ""
            except Exception:  # noqa: BLE001 - the log can re-render while Muse writes
                return ""

        before = read_log()
        composer.click()
        composer.fill(message)
        composer.press("Enter")
        log.info("Sent the job to Muse chat %r; waiting for the reply", chat_name)
        text = wait(lambda: extract_reply(before, read_log(), message))
        return MuseReplyText(text=text, chat_url=page.url)
    finally:
        page.close()


def _visible(locator) -> bool:
    try:
        return bool(locator.is_visible())
    except Exception:  # noqa: BLE001
        return False
