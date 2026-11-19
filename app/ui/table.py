"""Pure helpers behind the opportunities table (no Streamlit here, so they're testable)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from app.domain import DEFAULT_STATUS, PATHS, STATUSES, TICKED_STATUS
from app.scoring import opportunity_label
from app.timeline import delivery_label

# Columns the user may edit; everything else is read-only.
EDITABLE_COLUMNS = ("Ticked", "Status", "Path", "Notes")

# Statuses that count as "not ticked" for the tick box.
_UNTICKED = {DEFAULT_STATUS, "Skipped"}


def stars(rating: int) -> str:
    """4 -> '★★★★☆ 4/5'"""
    rating = max(0, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating) + f" {rating}/5"


def build_table(rows: Sequence[Mapping]) -> pd.DataFrame:
    """Dashboard rows -> the DataFrame shown in the editor, indexed by post id."""
    records = [
        {
            "id": r["id"],
            "Post": r["url"],
            "Title": r["title"],
            "Verdict": opportunity_label(r["opportunity_score"]),
            "Score": r["opportunity_score"],
            "Seriousness": stars(r["seriousness"]),
            "Delivery": delivery_label(
                r["delivery_min_days"], r["delivery_max_days"], r["difficulty"], r["est_hours"]
            ),
            "Hours": r["est_hours"],
            "Difficulty": r["difficulty"],
            "Tool": r["tool"],
            "Potential": r["product_potential"],
            "Contact": r["contact_method"],
            "Region": r["region_hint"],
            "Summary": r["summary"],
            "Source": r["source"],
            "Posted": r["posted_at"][:10],
            "Ticked": r["status"] not in _UNTICKED,
            "Status": r["status"],
            "Path": r["path"],
            "Notes": r["notes"],
        }
        for r in rows
    ]
    columns = ["id", "Post", "Title", "Verdict", "Score", "Seriousness", "Delivery", "Hours",
               "Difficulty", "Tool", "Potential", "Contact", "Region", "Summary", "Source",
               "Posted", "Ticked", "Status", "Path", "Notes"]
    return pd.DataFrame.from_records(records, columns=columns).set_index("id")


def edits_to_changes(
    edited_rows: Mapping[int, Mapping[str, object]],
    post_ids: Sequence[str],
    current_statuses: Sequence[str],
) -> list[tuple[str, dict[str, str | None]]]:
    """Turn the editor's {row position: {column: value}} into store.update_action calls.

    The tick box is a shortcut: ticking a New/Skipped post sets it to Ticked,
    unticking a Ticked post sets it back to New. Other statuses are only
    changed through the Status column, so nothing is lost by accident.
    """
    changes_list: list[tuple[str, dict[str, str | None]]] = []
    for position, edits in edited_rows.items():
        position = int(position)
        if not 0 <= position < len(post_ids):
            continue  # stale edit from a previous page; ignore safely
        current = current_statuses[position]
        changes: dict[str, str | None] = {}

        if "Status" in edits and edits["Status"] in STATUSES:
            changes["status"] = str(edits["Status"])
        elif "Ticked" in edits:
            if edits["Ticked"] and current in _UNTICKED:
                changes["status"] = TICKED_STATUS
            elif not edits["Ticked"] and current == TICKED_STATUS:
                changes["status"] = DEFAULT_STATUS

        if "Path" in edits:
            path = edits["Path"]
            changes["path"] = path if path in PATHS else None
        if "Notes" in edits:
            changes["notes"] = str(edits["Notes"] or "")

        if changes:
            changes_list.append((post_ids[position], changes))
    return changes_list
