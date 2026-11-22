"""Pure helpers behind the opportunities table (no Streamlit here, so they're testable)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from app.domain import TECH_LABELS
from app.services import badge_for
from app.timeline import delivery_label
from app.ui.components import status_text, verdict_text

TABLE_COLUMNS = ["✅", "Verdict", "Score", "Service", "Who", "Title", "Bids", "Delivery", "Seriousness", "Source", "Status", "Posted", "Post"]


def stars(rating: int) -> str:
    """4 -> '★★★★☆'"""
    rating = max(0, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating)


_OUTCOME_MARKS = {"replied": "💬", "won": "🏆", "lost": "❌"}


def _answered_mark(row: Mapping) -> str:
    """✅ answered, or what happened next."""
    if row.get("outcome") in _OUTCOME_MARKS:
        return _OUTCOME_MARKS[row["outcome"]]
    return "✅" if row.get("answered_at") else ""


def build_table(rows: Sequence[Mapping]) -> pd.DataFrame:
    """Dashboard rows -> the read-only table, indexed by post id."""
    records = [
        {
            "id": r["id"],
            "✅": _answered_mark(r),
            "Verdict": verdict_text(r["opportunity_score"]),
            "Score": r["opportunity_score"],
            "Service": badge_for(r.get("service")),
            "Who": TECH_LABELS.get(r.get("tech_level") or "", ""),
            "Title": r["title"],
            "Bids": r.get("bids"),
            "Delivery": delivery_label(
                r["delivery_min_days"], r["delivery_max_days"], r["difficulty"], r["est_hours"]
            ),
            "Seriousness": stars(r["seriousness"]),
            "Source": r["source"],
            "Status": status_text(r["status"]),
            "Posted": r["posted_at"][:10],
            "Post": r["url"],
        }
        for r in rows
    ]
    return pd.DataFrame.from_records(records, columns=["id", *TABLE_COLUMNS]).set_index("id")
