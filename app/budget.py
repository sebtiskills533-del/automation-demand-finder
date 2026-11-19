"""The monthly AI budget, shared by every feature that calls Claude."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from app import store
from app.config import Settings
from app.domain import utc_now
from app.errors import BudgetExceededError


def month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def over_budget_message(
    conn: sqlite3.Connection, settings: Settings, now: Callable[[], datetime] = utc_now
) -> str | None:
    """A plain-English message if this month's limit is reached, else None."""
    if store.spend_since(conn, month_start(now())) < settings.monthly_budget_usd:
        return None
    return (
        f"Monthly AI budget of ${settings.monthly_budget_usd:.2f} reached. "
        "Raise MONTHLY_BUDGET_USD in .env or wait until next month."
    )


def check_budget(conn: sqlite3.Connection, settings: Settings, now: Callable[[], datetime] = utc_now) -> None:
    message = over_budget_message(conn, settings, now)
    if message:
        raise BudgetExceededError(message)
