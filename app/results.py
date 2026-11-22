"""📊 Results: what happens after the user answers, so they can see what works.

Pure functions over store.results_rows (no Streamlit), so the numbers are easy to test.
"Got a reply" = the user marked replied / won / lost, or a reply to that post is in the inbox.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime

from app.domain import PLATFORM_NAMES, TECH_LABELS
from app.services import badge_for
from app.writing import STYLE_LABELS

TRUST_AFTER = 20  # below this many answers, patterns are only hints


def got_reply(row: Mapping) -> bool:
    return row.get("outcome") in ("replied", "won", "lost") or (row.get("replies") or 0) > 0


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def speed_bucket(row: Mapping) -> str:
    """How long after the post appeared the user answered (when they ticked it)."""
    posted, answered = _parse(row.get("posted_at")), _parse(row.get("answered_at"))
    if not posted or not answered:
        return "Unknown"
    hours = (answered - posted).total_seconds() / 3600
    if hours < 6:
        return "1. Under 6 hours"
    if hours < 24:
        return "2. 6-24 hours"
    if hours < 72:
        return "3. 1-3 days"
    return "4. Over 3 days"


def bids_bucket(row: Mapping) -> str:
    bids = row.get("bids")
    if bids is None:
        return "Not a gig"
    if bids <= 10:
        return "1. 0-10 bids"
    if bids <= 30:
        return "2. 11-30 bids"
    if bids <= 80:
        return "3. 31-80 bids"
    return "4. 80+ bids"


DIMENSIONS: dict[str, Callable[[Mapping], str]] = {
    "Platform": lambda r: PLATFORM_NAMES.get(r["platform"], r["platform"]),
    "Service": lambda r: badge_for(r.get("service")),
    "Writing style": lambda r: STYLE_LABELS.get(r.get("reply_style") or "", "Not recorded"),
    "Who they are": lambda r: TECH_LABELS.get(r.get("tech_level") or "", "Unknown"),
    "How fast you answered": speed_bucket,
    "Bids when you found it": bids_bucket,
}


def totals(rows: Iterable[Mapping]) -> dict:
    rows = list(rows)
    replied = sum(got_reply(r) for r in rows)
    won = sum(r.get("outcome") == "won" for r in rows)
    return {"answered": len(rows), "replied": replied, "won": won,
            "reply_rate": round(100 * replied / len(rows)) if rows else 0}


def breakdown(rows: Iterable[Mapping], dimension: str) -> list[dict]:
    """One line per group: answered, replied, won, reply rate; most answered first."""
    key = DIMENSIONS[dimension]
    groups: dict[str, list[Mapping]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    lines = [{dimension: name, "Answered": t["answered"], "Replied": t["replied"], "Won": t["won"],
              "Reply rate %": t["reply_rate"]} for name, t in ((n, totals(g)) for n, g in groups.items())]
    return sorted(lines, key=lambda line: (-line["Answered"], str(line[dimension])))
