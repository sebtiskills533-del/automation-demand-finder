import json
from datetime import datetime, timezone

import pytest

from app import importer, store
from app.domain import platform_for_host
from app.importer import (
    MAX_IMPORT_ROWS,
    decode_upload,
    import_rows,
    import_text,
    parse_import,
    row_to_post,
)

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)

CSV = """url,title,text,author,posted_at
https://www.facebook.com/groups/shops/posts/123,Need my Shopify orders in a sheet,"Every day I copy orders. Call 555-123-4567",jane_shop,2026-11-18
https://www.quora.com/How-do-I-automate-invoices,How do I automate invoices?,,,
"""


@pytest.mark.parametrize(
    ("host", "platform"),
    [
        ("www.facebook.com", "facebook"),
        ("m.facebook.com", "facebook"),
        ("instagram.com", "instagram"),
        ("www.quora.com", "quora"),
        ("old.reddit.com", "reddit"),
        ("x.com", "x"),
        ("webapps.stackexchange.com", "stackexchange"),
        ("notfacebook.com", "web"),
        ("example.org", "web"),
    ],
)
def test_platform_for_host(host, platform):
    assert platform_for_host(host) == platform


def test_parse_csv_and_json_give_same_rows():
    rows = parse_import(CSV)
    assert len(rows) == 2 and rows[0]["title"] == "Need my Shopify orders in a sheet"
    as_json = json.dumps([{"url": r["url"], "title": r["title"]} for r in rows])
    assert [r["url"] for r in parse_import(as_json)] == [r["url"] for r in rows]
    assert len(parse_import(json.dumps({"posts": [{"url": "u", "title": "t"}]}))) == 1


def test_parse_handles_bom_and_header_case():
    rows = parse_import("﻿URL, Title\nhttps://x.com/a/status/1,Hi\n")
    assert rows[0]["url"] == "https://x.com/a/status/1"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "empty"),
        ("name,link\nx,y\n", "header"),
        ("[1, 2]", "list of posts"),
        ("{broken", "JSON"),
        ('{"posts": "nope"}', "list of posts"),
    ],
)
def test_parse_errors(text, match):
    with pytest.raises(ValueError, match=match):
        parse_import(text)


def test_parse_row_limit():
    text = "url,title\n" + "https://x.com/a,t\n" * (MAX_IMPORT_ROWS + 1)
    with pytest.raises(ValueError, match="at most"):
        parse_import(text)


def test_row_to_post_cleans_and_detects_platform():
    post = row_to_post(parse_import(CSV)[0], NOW)
    assert post.platform == "facebook"
    assert post.source == "Imported: Facebook"
    assert "555-123-4567" not in post.body
    assert post.author_handle == "jane_shop"
    assert post.posted_at == datetime(2026, 11, 18, tzinfo=timezone.utc)
    assert post.id.startswith("import_")


def test_row_to_post_text_aliases_and_custom_source():
    post = row_to_post({"url": "https://a.org/p", "title": "T", "body": "B", "source": "My group"}, NOW)
    assert post.body == "B" and post.source == "My group" and post.platform == "web"


def test_future_dates_become_now():
    post = row_to_post({"url": "https://a.org/p", "title": "T", "posted_at": "2030-01-01"}, NOW)
    assert post.posted_at == NOW


@pytest.mark.parametrize(
    ("row", "match"),
    [
        ({"url": "http://facebook.com/x", "title": "T"}, "https"),
        ({"url": "javascript:alert(1)", "title": "T"}, "https"),
        ({"url": "https://a.org/" + "x" * 3000, "title": "T"}, "too long"),
        ({"url": "https://a.org/p", "title": "  "}, "title"),
        ({"url": "https://a.org/p", "title": "T", "posted_at": "yesterday"}, "posted_at"),
    ],
)
def test_row_validation(row, match):
    with pytest.raises(ValueError, match=match):
        row_to_post(row, NOW)


def test_same_link_variants_are_one_post():
    a = row_to_post({"url": "https://www.Facebook.com/groups/x/posts/1/#comments", "title": "T"}, NOW)
    b = row_to_post({"url": "https://facebook.com/groups/x/posts/1", "title": "T"}, NOW)
    assert a.id == b.id


def test_quora_unanswered_link_is_the_same_question():
    # Regression: Muse returned the same Quora question with and without /unanswered/.
    a = row_to_post({"url": "https://www.quora.com/unanswered/How-do-I-automate-WhatsApp", "title": "T"}, NOW)
    b = row_to_post({"url": "https://www.quora.com/How-do-I-automate-WhatsApp", "title": "T"}, NOW)
    assert a.id == b.id
    other = row_to_post({"url": "https://www.example.com/unanswered/How-do-I-automate-WhatsApp", "title": "T"}, NOW)
    assert other.id != row_to_post({"url": "https://www.example.com/How-do-I-automate-WhatsApp", "title": "T"}, NOW).id


def test_import_saves_skips_filter_and_dedupes(conn):
    report = import_text(conn, CSV, now=lambda: NOW)
    assert (report.added, report.duplicates, report.errors) == (2, 0, [])
    assert store.posts_pending_filter(conn, 10) == []  # straight to analysis
    assert len(store.posts_pending_analysis(conn, 10, 3)) == 2
    again = import_text(conn, CSV, now=lambda: NOW)
    assert (again.added, again.duplicates) == (0, 2)
    assert "2 already in the app" in again.summary


def test_import_reports_bad_rows_and_keeps_good_ones(conn):
    report = import_rows(conn, [{"url": "nope", "title": "T"}, {"url": "https://a.org/1", "title": "T"}])
    assert report.added == 1
    assert report.errors == ["Row 1: url must be a full https:// link"]
    assert "1 skipped" in report.summary


def test_paste_accepts_a_copied_muse_answer(conn):
    pasted = (
        "Here is what I found:\nPOSTS START\nurl,title,text,source,author,posted_at\n"
        'https://www.facebook.com/groups/1/posts/9,Need a sheet automation,"Will pay",Facebook,Ann,2026-11-18\n'
        "POSTS END\nWant me to look for more?"
    )
    report = import_text(conn, pasted, now=lambda: NOW)
    assert report.added == 1
    assert conn.execute("SELECT platform FROM posts").fetchone()[0] == "facebook"


def test_paste_of_a_muse_problem_line_explains_itself(conn):
    with pytest.raises(ValueError, match="no posts in the expected format"):
        import_text(conn, "POSTS START\nnothing useful here\nPOSTS END", now=lambda: NOW)


def test_import_text_size_limit(conn, monkeypatch):
    monkeypatch.setattr(importer, "MAX_IMPORT_BYTES", 10)
    with pytest.raises(ValueError, match="too much"):
        import_text(conn, CSV)


def test_decode_upload():
    assert decode_upload("﻿url,title".encode("utf-8")) == "url,title"
    with pytest.raises(ValueError, match="UTF-8"):
        decode_upload("café".encode("cp1252"))


def test_cli_import(tmp_path, monkeypatch, settings, capsys):
    from app import cli

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    path = tmp_path / "posts.csv"
    path.write_text(CSV, encoding="utf-8")
    assert importer.main([str(path)]) == 0
    assert "2 added" in capsys.readouterr().out
    assert importer.main([str(tmp_path / "missing.csv")]) == 1
    assert importer.main([]) == 2
