"""The app's own Chrome for Muse: launch it, connect to it, one job at a time."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import MuseSettings
from app.errors import MuseBrowserError, MuseBusyError
from app.muse import selectors

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 20          # one per second after launching Chrome
STALE_LOCK_SECONDS = 45 * 60   # longer than the 30-minute reply limit


def chrome_args(muse: MuseSettings, open_url: str | None = None) -> list[str]:
    """Command line for the app's Chrome. Debugging listens on 127.0.0.1 only."""
    args = [
        str(muse.chrome_path),
        f"--user-data-dir={muse.profile_dir}",
        f"--remote-debugging-port={muse.browser_port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
    ]
    if open_url:
        args.append(open_url)
    return args


def launch_chrome(muse: MuseSettings, open_url: str | None = None) -> None:
    """Start the app's Chrome, detached so it keeps running after this process ends."""
    if not muse.chrome_path.exists():
        raise MuseBrowserError(
            f"Chrome was not found at {muse.chrome_path}. Set CHROME_PATH in .env."
        )
    muse.profile_dir.mkdir(parents=True, exist_ok=True)
    options: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    log.info("Launching the Muse Chrome on port %d", muse.browser_port)
    subprocess.Popen(chrome_args(muse, open_url), **options)  # noqa: S603 (fixed argument list)


def open_for_setup(muse: MuseSettings) -> None:
    """Open the app's Chrome on muse.ai so the user can log in / set up the chat."""
    launch_chrome(muse, open_url=f"{selectors.BASE_URL}/")


@contextmanager
def connected_context(
    muse: MuseSettings,
    *,
    playwright_factory: Callable[[], object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[object]:
    """Yield the logged-in browser context of the app's Chrome.

    Tries to connect; if nothing answers, launches Chrome and retries every
    second. Leaving the block only DISCONNECTS; Chrome keeps running.
    """
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright

        playwright_factory = lambda: sync_playwright().start()  # noqa: E731

    playwright = playwright_factory()
    endpoint = f"http://127.0.0.1:{muse.browser_port}"
    browser = None
    try:
        for attempt in range(CONNECT_ATTEMPTS + 1):
            try:
                browser = playwright.chromium.connect_over_cdp(endpoint)
                break
            except Exception as exc:  # Playwright raises its own Error type for "not running"
                if attempt == 0:
                    log.info("Muse Chrome not running (%s); launching it", type(exc).__name__)
                    launch_chrome(muse)
                elif attempt == CONNECT_ATTEMPTS:
                    raise MuseBrowserError(
                        "The app's Chrome could not be reached. Close that Chrome window and try again."
                    ) from exc
                sleep(1)
        if not browser.contexts:
            raise MuseBrowserError("The app's Chrome has no open profile window.")
        yield browser.contexts[0]
    finally:
        if browser is not None:
            browser.close()  # for a CDP connection this only disconnects
        playwright.stop()


class BrowserLock:
    """Only one Muse job may use the app's Chrome at a time.

    A lock file is created atomically; a lock older than STALE_LOCK_SECONDS
    (a crashed job) is taken over.
    """

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._held = False

    def __enter__(self) -> BrowserLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    log.warning("Removing a stale Muse lock: %s", self.path)
                    self.path.unlink(missing_ok=True)
                    continue
                raise MuseBusyError(
                    "Another Muse job is still running. Wait for it to finish (up to 30 minutes)."
                ) from None
            with os.fdopen(fd, "w") as handle:
                handle.write(f"{os.getpid()} {self._clock():.0f}")
            self._held = True
            return self
        raise MuseBusyError("Could not take the Muse lock; try again.")

    def __exit__(self, *_exc: object) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _is_stale(self) -> bool:
        try:
            age = self._clock() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True
        return age > STALE_LOCK_SECONDS
