from datetime import datetime, timedelta, timezone

import pytest

from app import db, store
from app.store import DashboardFilters
from tests.conftest import make_analysis, make_post


def test_schema_is_created_and_versioned(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"posts", "analyses", "actions", "llm_usage", "settings", "login_attempts"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_init_db_twice_is_harmless(conn):
    db.init_db(conn)
    db.init_db(conn)


def test_open_db_creates_the_file_and_folder(tmp_path):
    path = tmp_path / "nested" / "finder.db"
    with db.open_db(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
    assert path.exists()


def test_save_post_skips_duplicates(conn):
    assert store.save_post(conn, make_post()) is True
    assert store.save_post(conn, make_post()) is False
    assert store.count_posts(conn)["collected"] == 1


def test_save_post_truncates_huge_text(conn):
    store.save_post(conn, make_post(title="t" * 5000, body="b" * 100_000))
    row = conn.execute("SELECT title, body FROM posts").fetchone()
    assert len(row["title"]) == 500 and len(row["body"]) == 20_000


def test_filter_flow(conn):
    store.save_post(conn, make_post("reddit_1"))
    store.save_post(conn, make_post("reddit_2"))
    assert len(store.posts_pending_filter(conn, 10)) == 2
    store.set_filter_results(conn, [("reddit_1", True), ("reddit_2", False)])
    assert store.posts_pending_filter(conn, 10) == []
    assert store.count_posts(conn)["passed"] == 1


def test_pending_analysis_respects_attempts_and_existing_analyses(conn):
    for pid in ("reddit_1", "reddit_2", "reddit_3"):
        store.save_post(conn, make_post(pid))
    store.set_filter_results(conn, [("reddit_1", True), ("reddit_2", True), ("reddit_3", True)])
    store.save_analysis(conn, make_analysis("reddit_1"))
    for _ in range(3):
        store.record_analysis_failure(conn, "reddit_2")
    pending = store.posts_pending_analysis(conn, limit=10, max_attempts=3)
    assert [r["id"] for r in pending] == ["reddit_3"]


def test_pending_analysis_newest_first_and_limited(conn):
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for i in range(5):
        store.save_post(conn, make_post(f"reddit_{i}", posted_at=base + timedelta(days=i)))
    store.set_filter_results(conn, [(f"reddit_{i}", True) for i in range(5)])
    pending = store.posts_pending_analysis(conn, limit=2, max_attempts=3)
    assert [r["id"] for r in pending] == ["reddit_4", "reddit_3"]


def test_analysis_rejects_out_of_range_values(conn):
    import sqlite3

    store.save_post(conn, make_post())
    with pytest.raises(sqlite3.IntegrityError):
        store.save_analysis(conn, make_analysis(product_potential=9))


def test_update_action_creates_then_updates(conn):
    store.save_post(conn, make_post())
    store.update_action(conn, "reddit_abc123", status="Ticked")
    store.update_action(conn, "reddit_abc123", path="Product", notes="Bundle it")
    row = conn.execute("SELECT status, path, notes FROM actions").fetchone()
    assert tuple(row) == ("Ticked", "Product", "Bundle it")
    store.update_action(conn, "reddit_abc123", path=None)
    assert conn.execute("SELECT path FROM actions").fetchone()[0] is None


@pytest.mark.parametrize(
    "changes",
    [{"status": "Maybe"}, {"path": "Lottery"}, {"notes": "x" * 2001}, {"evil": "1"}],
)
def test_update_action_validates(conn, changes):
    store.save_post(conn, make_post())
    with pytest.raises(ValueError):
        store.update_action(conn, "reddit_abc123", **changes)


def test_update_action_unknown_post(conn):
    with pytest.raises(ValueError, match="No post"):
        store.update_action(conn, "nope", status="Ticked")


def _seed_dashboard(conn):
    rows = [
        ("reddit_a", "low", 90, 5, True, "r/excel"),
        ("reddit_b", "medium", 60, 3, True, "r/zapier"),
        ("reddit_c", "high", 20, 2, False, "r/excel"),
    ]
    for pid, diff, score, serious, buildable, source in rows:
        store.save_post(conn, make_post(pid, source=source, title=f"Title {pid} 100%_off"))
        store.save_analysis(
            conn,
            make_analysis(pid, difficulty=diff, opportunity_score=score, seriousness=serious,
                          buildable=buildable),
        )


def test_dashboard_rows_sorted_and_defaulted(conn):
    _seed_dashboard(conn)
    rows = store.get_dashboard_rows(conn, DashboardFilters())
    assert [r["id"] for r in rows] == ["reddit_a", "reddit_b", "reddit_c"]
    assert all(r["status"] == "New" for r in rows)
    assert store.count_dashboard_rows(conn, DashboardFilters()) == 3


def test_dashboard_filters(conn):
    _seed_dashboard(conn)
    store.update_action(conn, "reddit_b", status="Ticked")

    def ids(**kw):
        return [r["id"] for r in store.get_dashboard_rows(conn, DashboardFilters(**kw))]

    assert ids(difficulties=("low", "medium")) == ["reddit_a", "reddit_b"]
    assert ids(min_score=50) == ["reddit_a", "reddit_b"]
    assert ids(min_seriousness=4) == ["reddit_a"]
    assert ids(sources=("r/excel",)) == ["reddit_a", "reddit_c"]
    assert ids(statuses=("Ticked",)) == ["reddit_b"]
    assert ids(statuses=("New",)) == ["reddit_a", "reddit_c"]
    assert ids(buildable_only=True) == ["reddit_a", "reddit_b"]
    assert ids(search="reddit_c") == ["reddit_c"]


def test_dashboard_search_is_literal_and_injection_safe(conn):
    _seed_dashboard(conn)
    # "%" and "_" are matched literally, not as wildcards.
    assert store.count_dashboard_rows(conn, DashboardFilters(search="100%_off")) == 3
    assert store.count_dashboard_rows(conn, DashboardFilters(search="1%0")) == 0
    # SQL in the search box is just text.
    evil = "'; DROP TABLE posts; --"
    assert store.count_dashboard_rows(conn, DashboardFilters(search=evil)) == 0
    assert store.count_posts(conn)["collected"] == 3


def test_dashboard_rejects_bad_filter_values(conn):
    with pytest.raises(ValueError):
        store.get_dashboard_rows(conn, DashboardFilters(difficulties=("low') OR 1=1 --",)))
    with pytest.raises(ValueError):
        store.get_dashboard_rows(conn, DashboardFilters(), limit=0)


def test_dashboard_pagination(conn):
    _seed_dashboard(conn)
    page = store.get_dashboard_rows(conn, DashboardFilters(), limit=2, offset=2)
    assert [r["id"] for r in page] == ["reddit_c"]


def test_usage_and_spend(conn):
    store.log_usage(conn, "analyze", "claude-haiku-5-5", 1000, 200, 0.0002)
    store.log_usage(conn, "analyze", "claude-haiku-5-5", 1000, 200, 0.0002)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    assert store.spend_since(conn, since) == pytest.approx(0.0004)
    by_model = store.usage_by_model_since(conn, since)
    assert by_model[0]["calls"] == 2
    assert store.spend_since(conn, datetime.now(timezone.utc) + timedelta(days=1)) == 0


def test_settings_roundtrip(conn):
    assert store.get_setting(conn, "model.analyze") is None
    store.set_setting(conn, "model.analyze", "a")
    store.set_setting(conn, "model.analyze", "b")
    assert store.get_setting(conn, "model.analyze") == "b"


def test_failed_logins_reset_after_success(conn):
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    store.record_login_attempt(conn, False, now - timedelta(minutes=3))
    store.record_login_attempt(conn, False, now - timedelta(minutes=2))
    assert store.failed_logins_since(conn, now - timedelta(minutes=15)) == 2
    store.record_login_attempt(conn, True, now - timedelta(minutes=1))
    assert store.failed_logins_since(conn, now - timedelta(minutes=15)) == 0


def test_old_login_attempts_are_pruned(conn):
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    store.record_login_attempt(conn, False, now - timedelta(days=40))
    store.record_login_attempt(conn, False, now)
    assert conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 1


def test_answered_tick_and_filter(conn):
    for pid in ("reddit_1", "reddit_2"):
        store.save_post(conn, make_post(pid))
        store.save_analysis(conn, make_analysis(pid))
    store.set_answered(conn, "reddit_1", True)
    answered = store.get_dashboard_rows(conn, DashboardFilters(answered=True))
    assert [r["id"] for r in answered] == ["reddit_1"] and answered[0]["answered_at"].endswith("Z")
    assert [r["id"] for r in store.get_dashboard_rows(conn, DashboardFilters(answered=False))] == ["reddit_2"]
    assert store.count_dashboard_rows(conn, DashboardFilters()) == 2
    store.set_answered(conn, "reddit_1", False)
    assert store.count_dashboard_rows(conn, DashboardFilters(answered=True)) == 0
    # status and notes are untouched by the tick
    assert store.get_post_details(conn, "reddit_1")["status"] == "New"


def test_answered_at_must_be_a_real_time(conn):
    store.save_post(conn, make_post("reddit_1"))
    with pytest.raises(ValueError):
        store.update_action(conn, "reddit_1", answered_at="yesterday; DROP TABLE posts")
    with pytest.raises(ValueError):
        store.set_answered(conn, "nope", True)


def test_upgrade_ticks_posts_that_already_have_replies(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    for version, script in enumerate(db.MIGRATIONS[:8], start=1):  # a database from before V9
        for statement in db._split_statements(script):
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {version:d}")
    conn.commit()
    for pid in ("facebook_1", "facebook_2"):  # the old schema: no bids column yet
        conn.execute("INSERT INTO posts (id, source, platform, url, title, body, author_handle, posted_at, "
                     "collected_at) VALUES (?, 'Facebook', 'facebook', 'https://facebook.com/x', 't', 'b', 'a', "
                     "'2026-11-18T09:30:00Z', '2026-11-18T09:30:00Z')", (pid,))
    conn.commit()
    store.save_reply(conn, {"reply_key": "k", "post_id": "facebook_1", "platform": "facebook",
                            "url": "https://facebook.com/x", "author": "A", "text": "hi",
                            "email": None, "replied_at": None})
    db.init_db(conn)
    answered = dict(conn.execute("SELECT post_id, answered_at FROM actions").fetchall())
    assert set(answered) == {"facebook_1"} and answered["facebook_1"]
    conn.close()
