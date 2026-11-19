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
CHAT_LIST_SECONDS = 10


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


def open_surface(page, extension_id: str | None):
    """Load Muse and return what holds its chat: the page itself (muse.ai tab), or
    the muse.ai frame inside the extension panel (which may use the device browser)."""
    if not extension_id:
        page.goto(f"{selectors.BASE_URL}/", wait_until="domcontentloaded")
        return page
    try:
        page.goto(selectors.extension_panel_url(extension_id), wait_until="domcontentloaded")
    except Exception as exc:  # net::ERR_BLOCKED_BY_CLIENT etc. when it isn't installed
        raise MuseBrowserError(
            "The 'Muse from Meta' Chrome extension is not installed (or is switched off) in the "
            "app's Chrome. Open the Muse Chrome and add it from the Chrome Web Store."
        ) from exc
    for _ in range(PAGE_LOAD_SECONDS):
        frame = next((f for f in page.frames if f.url.startswith(selectors.BASE_URL)), None)
        if frame is not None:
            return frame
        page.wait_for_timeout(1000)
    raise MuseBrowserError("The Muse extension panel did not load. Look at the Muse Chrome window.")


def open_chat(page, chat_name: str, root=None) -> None:
    """Open the named side chat (or 'Main chat') in an already loaded Muse surface.

    `root` is where the chat lives (the page, or the extension's muse.ai frame);
    `page` is the tab, used for waiting and the keyboard.
    """
    root = page if root is None else root
    # .first everywhere: muse.ai shows the same button more than once (the logged-out
    # page has two "Log in" buttons), and Playwright refuses ambiguous checks.
    composer = root.get_by_role("textbox", name=selectors.COMPOSER_LABEL).first
    login = root.get_by_role("button", name=selectors.LOGIN_BUTTON, exact=True).first

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

    chat = root.get_by_role("button", name=selectors.chat_button_name(chat_name or selectors.MAIN_CHAT)).first
    chats = root.get_by_role("button", name=selectors.CHATS_BUTTON).first
    # In a wide window the chat list is already open; otherwise open it via "Chats".
    # Either can appear a moment after the message box (about 1 s in the extension
    # panel, seen 2026-11-19), so wait for whichever shows up first.
    opened_panel = False
    for _ in range(CHAT_LIST_SECONDS):
        if _visible(chat):
            break
        if _visible(chats):
            chats.click()
            opened_panel = True
            break
        page.wait_for_timeout(1000)
    try:
        chat.wait_for(state="visible", timeout=15_000)
    except Exception as exc:  # Playwright TimeoutError
        raise MuseChatNotFoundError(
            f'No Muse side chat named "{chat_name}". Create it in Muse (see the procedure) '
            "or fix MUSE_CHAT in .env."
        ) from exc
    chat.click()
    if opened_panel:
        try:
            page.keyboard.press("Escape")  # close the chats panel we opened
        except Exception:  # noqa: BLE001 - harmless if it closed by itself
            pass


def ask_muse(
    context,
    chat_name: str,
    message: str,
    *,
    extension_id: str | None = None,
    wait: Callable[..., str] = wait_for_stable_text,
) -> MuseReplyText:
    """Send `message` to the named Muse chat and return Muse's full reply text.

    With `extension_id`, the chat is driven inside the Muse extension panel so Muse
    can use the device browser; without it, on the plain muse.ai page.
    """
    page = context.new_page()
    try:
        root = open_surface(page, extension_id)
        open_chat(page, chat_name, root)
        log_box = root.locator(selectors.MESSAGE_LOG).first
        composer = root.get_by_role("textbox", name=selectors.COMPOSER_LABEL).first

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
        chat_url = root.url if root.url.startswith(selectors.BASE_URL) else page.url
        return MuseReplyText(text=text, chat_url=chat_url)
    finally:
        page.close()


def _visible(locator) -> bool:
    try:
        return bool(locator.is_visible())
    except Exception:  # noqa: BLE001
        return False
