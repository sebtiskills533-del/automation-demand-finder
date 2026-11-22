"""Daily database backups: one a day, kept for 7 days, never break the run."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app import store
from app.backup import daily_backup
from tests.conftest import make_post


def test_one_copy_a_day_with_the_data(conn, tmp_path):
    store.save_post(conn, make_post("reddit_1"))
    folder = tmp_path / "backups"
    assert daily_backup(conn, folder, date(2026, 11, 22)).startswith("saved")
    copy = folder / "finder-2026-11-22.db"
    with sqlite3.connect(copy) as check:
        assert check.execute("SELECT id FROM posts").fetchall() == [("reddit_1",)]
    store.save_post(conn, make_post("reddit_2"))
    assert daily_backup(conn, folder, date(2026, 11, 22)).startswith("already saved today")
    with sqlite3.connect(copy) as check:
        assert check.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1   # the day's first copy stays
    assert not list(folder.glob("*.partial"))


def test_only_the_last_seven_days_are_kept(conn, tmp_path):
    folder = tmp_path / "backups"
    start = date(2026, 11, 1)
    for n in range(10):
        message = daily_backup(conn, folder, start + timedelta(days=n))
    names = sorted(p.name for p in folder.glob("finder-*.db"))
    assert names == [f"finder-2026-11-{d:02d}.db" for d in range(4, 11)]
    assert "7 daily copies kept" in message


def test_a_backup_problem_never_breaks_the_run(conn, tmp_path):
    blocker = tmp_path / "backups"
    blocker.write_text("not a folder", encoding="utf-8")
    assert daily_backup(conn, blocker, date(2026, 11, 22)).startswith("FAILED")


def test_the_run_reports_its_backup(conn, settings):
    from app.pipeline import run_daily
    from tests.test_pipeline import make_runtime

    report = run_daily(make_runtime(conn, settings, []))
    assert report.backup.startswith("saved")
    assert any(line.startswith("Backup: saved") for line in report.summary_lines(88))
    assert list((settings.db_path.parent / "backups").glob("finder-*.db"))
