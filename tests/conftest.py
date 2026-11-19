"""Shared test fixtures. Tests never use the network or the real .env file."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from app import db
from app.config import Settings, load_settings
from app.domain import AnalysisRecord, Post


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test tries to make a real HTTP request."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("Tests must not use the network; inject a fake session.")

    monkeypatch.setattr(requests.Session, "request", refuse)


_APP_ENV_VARS = (
    "ANTHROPIC_API_KEY", "DASHBOARD_PASSWORD_HASH", "MAX_POSTS_PER_RUN", "MONTHLY_BUDGET_USD",
    "USD_TO_INR", "REDDIT_USERNAME", "LOG_LEVEL", "DB_PATH", "LOG_DIR", "MUSE_ENABLED", "MUSE_CHAT",
    "MUSE_MAX_POSTS", "MUSE_BROWSER_PORT", "MUSE_PROFILE_DIR", "CHROME_PATH", "MUSE_SURFACE",
    "MUSE_EXTENSION_ID",
)


@pytest.fixture(autouse=True)
def _isolate_from_real_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the user's real .env (API key, password, Muse switch...)."""
    from app import config

    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "isolated.env")  # does not exist
    for name in _APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        env={
            "ANTHROPIC_API_KEY": "test-key",
            "DB_PATH": str(tmp_path / "test.db"),
            "LOG_DIR": str(tmp_path / "logs"),
            "MAX_POSTS_PER_RUN": "5",
            "MONTHLY_BUDGET_USD": "1",
        },
        env_file=None,
    )


@pytest.fixture
def conn(settings: Settings):
    connection = db.connect(settings.db_path)
    db.init_db(connection)
    yield connection
    connection.close()


def make_post(
    post_id: str = "reddit_abc123",
    title: str = "Is there a way to automate my invoices?",
    body: str = "Every day I copy and paste orders into a spreadsheet.",
    source: str = "r/smallbusiness",
    platform: str = "reddit",
    posted_at: datetime | None = None,
) -> Post:
    """Build a valid Post with sensible defaults; override what a test needs."""
    return Post(
        id=post_id,
        source=source,
        platform=platform,
        url=f"https://www.reddit.com/r/smallbusiness/comments/{post_id}/",
        title=title,
        body=body,
        author_handle="someone",
        posted_at=posted_at or datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc),
    )


def make_analysis(post_id: str = "reddit_abc123", **overrides) -> AnalysisRecord:
    """Build a valid AnalysisRecord; pass keyword overrides for what a test needs."""
    values = dict(
        post_id=post_id,
        summary="Wants invoices emailed when a form is submitted",
        category="invoicing",
        difficulty="low",
        tool="Google Sheets + Apps Script",
        est_hours=3.0,
        buildable=True,
        product_potential=4,
        seriousness=4,
        seriousness_reason="Specific details and a clear business need",
        wants_to_hire=False,
        shares_own_website=False,
        region_hint="Unknown",
        reason="Simple form trigger",
        delivery_min_days=1,
        delivery_max_days=3,
        contact_method="Public reply under the post on Reddit",
        opportunity_score=75,
        model="claude-haiku-5-5",
    )
    values.update(overrides)
    return AnalysisRecord(**values)
