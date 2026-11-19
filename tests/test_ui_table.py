from app.ui.components import md_escape, status_text, verdict_text
from app.ui.table import TABLE_COLUMNS, build_table, stars

ROW = {
    "id": "reddit_1", "url": "https://www.reddit.com/r/x/comments/1/", "title": "T",
    "opportunity_score": 80, "seriousness": 4, "delivery_min_days": 1, "delivery_max_days": 3,
    "est_hours": 3.0, "difficulty": "low", "source": "r/x",
    "posted_at": "2026-11-18T09:30:00Z", "status": "New",
}


def test_stars():
    assert stars(4) == "★★★★☆"
    assert stars(9) == "★★★★★"
    assert stars(-1) == "☆☆☆☆☆"


def test_build_table():
    no_window = {"delivery_min_days": None, "delivery_max_days": None, "opportunity_score": 20}
    table = build_table([
        ROW,
        {**ROW, **no_window, "id": "reddit_2", "status": "Building", "difficulty": "high", "est_hours": 40},
        {**ROW, **no_window, "id": "reddit_3"},  # small, but judged "not a job"
    ])
    assert list(table.columns) == TABLE_COLUMNS
    assert list(table.index) == ["reddit_1", "reddit_2", "reddit_3"]
    first = table.loc["reddit_1"]
    assert first["Verdict"] == "🟢 Work on this"
    assert first["Delivery"] == "1-3 days"
    assert first["Posted"] == "2026-11-18"
    assert first["Status"] == "🆕 New"
    assert table.loc["reddit_2"]["Delivery"] == "Skip - too big"
    assert table.loc["reddit_3"]["Delivery"] == "Skip - not a job"
    assert table.loc["reddit_2"]["Verdict"] == "⚪ Skip"


def test_build_table_empty():
    assert build_table([]).empty


def test_verdict_and_status_text():
    assert verdict_text(55) == "🟠 Worth a look"
    assert status_text("Sold") == "💰 Sold"
    assert status_text("Unknown") == "Unknown"


def test_md_escape_neutralises_links_images_and_formatting():
    evil = "![x](http://tracker.example/p.png) [click](javascript:alert(1)) **bold** <b>hi</b>"
    escaped = md_escape(evil)
    assert "](" not in escaped.replace("\\]\\(", "")  # every bracket/paren is escaped
    assert "\\*\\*bold\\*\\*" in escaped and "\\<b\\>" in escaped
    assert md_escape("line1\n\n# heading") == "line1 \\# heading"
    assert md_escape(None) == ""
