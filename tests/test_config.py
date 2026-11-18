from pathlib import Path

import pytest

from app.config import load_settings, require_api_key
from app.errors import ConfigError


def test_defaults_when_env_is_empty():
    s = load_settings(env={}, env_file=None)
    assert s.max_posts_per_run == 30
    assert s.monthly_budget_usd == 5.0
    assert s.log_level == "INFO"
    assert s.anthropic_api_key == ""


def test_values_are_read_and_trimmed():
    s = load_settings(
        env={"ANTHROPIC_API_KEY": "  sk-test  ", "MAX_POSTS_PER_RUN": "10", "LOG_LEVEL": "debug"},
        env_file=None,
    )
    assert s.anthropic_api_key == "sk-test"
    assert s.max_posts_per_run == 10
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize(
    "env",
    [
        {"MAX_POSTS_PER_RUN": "lots"},
        {"MAX_POSTS_PER_RUN": "0"},
        {"MAX_POSTS_PER_RUN": "100000"},
        {"MONTHLY_BUDGET_USD": "-1"},
        {"MONTHLY_BUDGET_USD": "abc"},
        {"LOG_LEVEL": "LOUD"},
        {"REDDIT_USERNAME": "bad name\r\nX-Header: 1"},
    ],
)
def test_invalid_values_raise_clear_errors(env):
    with pytest.raises(ConfigError):
        load_settings(env=env, env_file=None)


def test_reddit_username_prefix_is_removed():
    assert load_settings(env={"REDDIT_USERNAME": "u/safi_dev"}, env_file=None).reddit_username == "safi_dev"


def test_env_file_is_read_and_environment_wins(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("MAX_POSTS_PER_RUN=12\nUSD_TO_INR=80\n", encoding="utf-8")
    s = load_settings(env={"USD_TO_INR": "90"}, env_file=env_file)
    assert s.max_posts_per_run == 12
    assert s.usd_to_inr == 90


def test_require_api_key():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        require_api_key(load_settings(env={}, env_file=None))
    assert require_api_key(load_settings(env={"ANTHROPIC_API_KEY": "k"}, env_file=None)) == "k"
