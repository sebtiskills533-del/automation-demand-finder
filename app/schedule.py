"""The on/off switch and the hours for the automatic runs.

Windows Task Scheduler starts `run_daily.py --scheduled` every 3 hours
(scripts/schedule_every_3_hours.ps1). Each scheduled run first asks should_run():
when the switch is off, or the hour is outside the chosen window, it stops at once:
nothing is collected and nothing is spent. Running the job by hand (desktop shortcut)
ignores the switch. The choice is saved in the settings table and set on the Settings page.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app import store

_ENABLED_KEY = "schedule.enabled"
_HOURS_KEY = "schedule.hours"
DEFAULT_START, DEFAULT_END = 7, 23   # runs at 7, 10, 13, 16, 19, 22; not at night


@dataclass(frozen=True)
class Schedule:
    enabled: bool = True
    start_hour: int = DEFAULT_START   # first hour a run may start (0-23)
    end_hour: int = DEFAULT_END       # runs must start before this hour (1-24)

    def label(self) -> str:
        if not self.enabled:
            return "Off"
        if (self.start_hour, self.end_hour) == (0, 24):
            return "On, all day"
        return f"On, between {self.start_hour:02d}:00 and {self.end_hour:02d}:00"


def _check(start: int, end: int) -> None:
    if not (0 <= start <= 23 and 1 <= end <= 24 and start < end):
        raise ValueError("Pick a start hour before the end hour (0-24).")


def get_schedule(conn: sqlite3.Connection) -> Schedule:
    enabled = store.get_setting(conn, _ENABLED_KEY) != "0"
    try:
        start, end = (int(part) for part in (store.get_setting(conn, _HOURS_KEY) or "").split("-"))
        _check(start, end)
    except ValueError:
        start, end = DEFAULT_START, DEFAULT_END
    return Schedule(enabled, start, end)


def set_schedule(conn: sqlite3.Connection, enabled: bool, start_hour: int, end_hour: int) -> Schedule:
    _check(start_hour, end_hour)
    store.set_setting(conn, _ENABLED_KEY, "1" if enabled else "0")
    store.set_setting(conn, _HOURS_KEY, f"{start_hour}-{end_hour}")
    return Schedule(enabled, start_hour, end_hour)


def should_run(schedule: Schedule, local_now: datetime) -> tuple[bool, str]:
    """(run?, reason). `local_now` is the computer's own clock (the hours the user sees)."""
    if not schedule.enabled:
        return False, "Automatic runs are switched off (Settings page)."
    if not schedule.start_hour <= local_now.hour < schedule.end_hour:
        return False, f"Outside the chosen hours ({schedule.start_hour:02d}:00-{schedule.end_hour:02d}:00)."
    return True, ""


def run_times(schedule: Schedule, first_hour: int = 7, every: int = 3) -> list[str]:
    """The hours a 3-hourly task (starting at 7:00) will actually do work, e.g. ['07:00', '10:00', ...]."""
    if not schedule.enabled:
        return []
    hours = sorted({(first_hour + every * n) % 24 for n in range(24 // every)})
    return [f"{h:02d}:00" for h in hours if schedule.start_hour <= h < schedule.end_hour]
