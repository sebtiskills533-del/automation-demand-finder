"""The on/off switch and hours for the automatic (Task Scheduler) runs."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.schedule import DEFAULT_END, DEFAULT_START, Schedule, get_schedule, run_times, set_schedule, should_run


def at(hour: int) -> datetime:
    return datetime(2026, 11, 22, hour, 5)


def test_defaults_skip_the_night(conn):
    schedule = get_schedule(conn)
    assert schedule == Schedule(True, DEFAULT_START, DEFAULT_END)
    assert run_times(schedule) == ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]
    assert should_run(schedule, at(10)) == (True, "")
    ok, reason = should_run(schedule, at(1))
    assert not ok and "Outside the chosen hours (07:00-23:00)" in reason


def test_switch_off_and_on(conn):
    set_schedule(conn, False, 9, 18)
    off = get_schedule(conn)
    assert off.label() == "Off" and run_times(off) == []
    ok, reason = should_run(off, at(12))
    assert not ok and "switched off" in reason
    on = set_schedule(conn, True, 9, 18)
    assert get_schedule(conn) == on and on.label() == "On, between 09:00 and 18:00"
    assert run_times(on) == ["10:00", "13:00", "16:00"]
    assert set_schedule(conn, True, 0, 24).label() == "On, all day"


@pytest.mark.parametrize("start, end", [(10, 10), (12, 9), (-1, 5), (5, 25)])
def test_bad_hours_are_refused(conn, start, end):
    with pytest.raises(ValueError):
        set_schedule(conn, True, start, end)


def test_damaged_setting_falls_back_to_defaults(conn):
    from app import store

    store.set_setting(conn, "schedule.hours", "lunch-time")
    assert get_schedule(conn).start_hour == DEFAULT_START


def test_scheduled_run_obeys_the_switch_but_a_manual_run_does_not(monkeypatch, settings, conn):
    import run_daily as script
    from app import cli

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_llm", lambda s, c: object())
    ran = []
    monkeypatch.setattr(script, "run_daily", lambda runtime: ran.append(1) or _empty_report())
    set_schedule(conn, False, 7, 23)

    assert script.main(["--scheduled"]) == 0 and ran == []          # off: nothing collected, nothing spent
    script.main([])
    assert ran == [1]                                                # the desktop icon always works
    set_schedule(conn, True, 0, 24)
    script.main(["--scheduled"])
    assert ran == [1, 1]


def _empty_report():
    from app.analyze import AnalyzeReport
    from app.collect import CollectReport
    from app.pipeline import DailyReport
    from app.prefilter import FilterReport

    return DailyReport(CollectReport({"x": 0}), FilterReport(0, 0), AnalyzeReport())
