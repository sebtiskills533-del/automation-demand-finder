"""A Windows notification ("toast") when the scheduled run finds fresh gigs with few bids.

Uses Windows PowerShell's built-in toast support, so nothing extra is installed. The text
comes from strangers' post titles, so it is passed through environment variables (never put
into the command) and XML-escaped inside PowerShell. A notification problem is logged and
never fails the run. Elsewhere than Windows it does nothing.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
MAX_TEXT = 220
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$title = [Security.SecurityElement]::Escape($env:ADF_TOAST_TITLE)
$body = [Security.SecurityElement]::Escape($env:ADF_TOAST_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:ADF_TOAST_APP).Show($toast)
"""


def _clean(text: str) -> str:
    return " ".join(str(text).split())[:MAX_TEXT]


def send_toast(title: str, body: str, run=subprocess.run) -> bool:
    """Show a notification. True if it was shown (as far as Windows reported)."""
    if sys.platform != "win32":
        return False
    env = {**os.environ, "ADF_TOAST_TITLE": _clean(title), "ADF_TOAST_BODY": _clean(body), "ADF_TOAST_APP": _APP_ID}
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env, capture_output=True, timeout=TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not show a notification: %s", exc)
        return False
    if result.returncode != 0:
        log.warning("Notification failed: %s", (result.stderr or b"").decode(errors="replace")[:300])
        return False
    return True


def fresh_gigs_message(gigs: list[dict]) -> tuple[str, str]:
    """('🔥 3 fresh gigs for you', 'Boost Google Reviews (3 bids) · ...')."""
    title = f"🔥 {len(gigs)} fresh {'gig' if len(gigs) == 1 else 'gigs'} with few bids"
    parts = [f"{' '.join(g['title'].split())[:60]} ({g['bids']} bids)" if g.get("bids") is not None
             else " ".join(g["title"].split())[:60] for g in gigs[:3]]
    more = f" +{len(gigs) - 3} more" if len(gigs) > 3 else ""
    return title, " · ".join(parts) + more + ". Open 💼 Freelance jobs → 🔥 Fresh gigs."
