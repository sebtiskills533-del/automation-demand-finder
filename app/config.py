"""Load and validate settings from environment variables / the .env file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from app.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "finder.db"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class Settings:
    """Every setting the app reads. Built once by `load_settings`."""

    anthropic_api_key: str
    dashboard_password_hash: str
    max_posts_per_run: int
    monthly_budget_usd: float
    usd_to_inr: float
    reddit_username: str
    log_level: str
    db_path: Path
    log_dir: Path
    sources_file: Path
    phrases_file: Path
    models_file: Path


def load_settings(
    env: Mapping[str, str] | None = None,
    env_file: Path | None = ENV_FILE,
) -> Settings:
    """Read settings, with real environment variables overriding the .env file.

    Pass `env` (and `env_file=None`) in tests to avoid touching the real files.
    """
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update(os.environ if env is None else env)

    return Settings(
        anthropic_api_key=values.get("ANTHROPIC_API_KEY", "").strip(),
        dashboard_password_hash=values.get("DASHBOARD_PASSWORD_HASH", "").strip(),
        max_posts_per_run=_int_setting(values, "MAX_POSTS_PER_RUN", 30, 1, 500),
        monthly_budget_usd=_float_setting(values, "MONTHLY_BUDGET_USD", 5.0, 0.01, 10_000),
        usd_to_inr=_float_setting(values, "USD_TO_INR", 88.0, 1, 1_000),
        reddit_username=_reddit_username(values.get("REDDIT_USERNAME", "")),
        log_level=_log_level(values.get("LOG_LEVEL", "INFO")),
        db_path=Path(values.get("DB_PATH") or DEFAULT_DB_PATH),
        log_dir=Path(values.get("LOG_DIR") or DEFAULT_LOG_DIR),
        sources_file=CONFIG_DIR / "sources.json",
        phrases_file=CONFIG_DIR / "phrases.txt",
        models_file=CONFIG_DIR / "models.json",
    )


def require_api_key(settings: Settings) -> str:
    """Return the API key, or explain how to add one."""
    if not settings.anthropic_api_key:
        raise ConfigError(
            "ANTHROPIC_API_KEY is empty. Open the .env file and paste your key "
            "after ANTHROPIC_API_KEY= (copy .env.example to .env first if needed)."
        )
    return settings.anthropic_api_key


def _int_setting(values: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = (values.get(name) or "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}.") from None
    if not low <= number <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {number}.")
    return number


def _float_setting(
    values: Mapping[str, str], name: str, default: float, low: float, high: float
) -> float:
    raw = (values.get(name) or "").strip()
    if not raw:
        return default
    try:
        number = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from None
    if not low <= number <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {number}.")
    return number


def _log_level(raw: str) -> str:
    level = (raw or "INFO").strip().upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, got {raw!r}.")
    return level


def _reddit_username(raw: str) -> str:
    name = (raw or "").strip().removeprefix("u/").removeprefix("/u/")
    # Reddit usernames: 3-20 letters, digits, '_' or '-'. Anything else could
    # inject text into the User-Agent header, so reject it.
    if name and not (3 <= len(name) <= 20 and all(c.isalnum() or c in "_-" for c in name)):
        raise ConfigError("REDDIT_USERNAME may only contain letters, digits, '_' and '-'.")
    return name
