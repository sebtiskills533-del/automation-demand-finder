from app.ui.table import EDITABLE_COLUMNS, build_table, edits_to_changes, stars

ROW = {
    "id": "reddit_1", "url": "https://www.reddit.com/r/x/comments/1/", "title": "T",
    "opportunity_score": 80, "seriousness": 4, "delivery_min_days": 1, "delivery_max_days": 3,
    "est_hours": 3.0, "difficulty": "low", "tool": "Zapier", "product_potential": 4,
    "contact_method": "Public reply", "region_hint": "Unknown", "summary": "S", "source": "r/x",
    "posted_at": "2026-11-18T09:30:00Z", "status": "New", "path": None, "notes": "",
}


def test_stars():
    assert stars(4) == "★★★★☆ 4/5"
    assert stars(9) == "★★★★★ 5/5"


def test_build_table():
    no_window = {"delivery_min_days": None, "delivery_max_days": None, "opportunity_score": 20}
    table = build_table([
        ROW,
        {**ROW, **no_window, "id": "reddit_2", "status": "Building", "difficulty": "high", "est_hours": 40},
        {**ROW, **no_window, "id": "reddit_3"},  # small, but judged "not a job"
    ])
    assert table.loc["reddit_3"]["Delivery"] == "Skip - not a job"
    assert list(table.index) == ["reddit_1", "reddit_2", "reddit_3"]
    first = table.loc["reddit_1"]
    assert first["Verdict"] == "Work on this"
    assert first["Delivery"] == "1-3 days"
    assert first["Posted"] == "2026-11-18"
    assert bool(first["Ticked"]) is False
    second = table.loc["reddit_2"]
    assert bool(second["Ticked"]) is True and second["Delivery"] == "Skip - too big"
    assert second["Verdict"] == "Skip"
    assert set(EDITABLE_COLUMNS) <= set(table.columns)


def test_build_table_empty():
    assert build_table([]).empty


IDS = ["a", "b", "c"]
STATUSES = ["New", "Ticked", "Sold"]


def test_tick_and_untick():
    assert edits_to_changes({0: {"Ticked": True}}, IDS, STATUSES) == [("a", {"status": "Ticked"})]
    assert edits_to_changes({1: {"Ticked": False}}, IDS, STATUSES) == [("b", {"status": "New"})]


def test_untick_never_loses_a_later_status():
    assert edits_to_changes({2: {"Ticked": False}}, IDS, STATUSES) == []


def test_status_column_wins_over_tick_box():
    changes = edits_to_changes({0: {"Ticked": True, "Status": "Building"}}, IDS, STATUSES)
    assert changes == [("a", {"status": "Building"})]


def test_path_and_notes():
    changes = edits_to_changes({0: {"Path": "Product", "Notes": "bundle"}, 1: {"Path": None}}, IDS, STATUSES)
    assert changes == [("a", {"path": "Product", "notes": "bundle"}), ("b", {"path": None})]


def test_invalid_values_and_stale_rows_are_ignored():
    assert edits_to_changes({0: {"Status": "Hacked"}}, IDS, STATUSES) == []
    assert edits_to_changes({7: {"Ticked": True}}, IDS, STATUSES) == []
    assert edits_to_changes({"1": {"Path": "Lottery"}}, IDS, STATUSES) == [("b", {"path": None})]
