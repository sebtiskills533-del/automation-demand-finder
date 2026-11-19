"""Optional collection through Muse (Meta's AI agent at muse.ai).

Muse has no API, so the app drives the muse.ai website in a dedicated,
logged-in Chrome over the Chrome DevTools Protocol, the same way the
SavorFable Autopilot app does (see docs/muse-finder-procedure.md).
It only runs when the user starts it, one job at a time.
"""
