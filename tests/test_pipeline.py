import pytest

from app import cli, store
from app.cli import Runtime, open_runtime
from app.config import CONFIG_DIR
from app.errors import ConfigError, SourceFetchError
from app.llm import ClaudeClient
from app.models_registry import load_registry
from app.pipeline import run_daily
from tests.conftest import make_post
from tests.fakes import FakeAnthropic, FakeSource, fake_message
from tests.test_analyze import GOOD


def make_runtime(conn, settings, sources, *claude_results):
    fake = FakeAnthropic(*claude_results)
    llm = ClaudeClient("key", lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c), client=fake)
    return Runtime(
        settings=settings,
        conn=conn,
        sources=sources,
        http=None,
        registry=load_registry(CONFIG_DIR / "models.json"),
        llm=llm,
    )


def test_full_daily_run(conn, settings):
    posts = [
        make_post("reddit_1", title="How can I automate my invoices?"),
        make_post("reddit_2", title="Look at my dog", body="He is cute"),
    ]
    sources = [FakeSource("r/smallbusiness", posts), FakeSource("r/down", error=SourceFetchError("HTTP 503"))]
    runtime = make_runtime(conn, settings, sources, fake_message(GOOD))

    report = run_daily(runtime)

    assert report.collected.total_new == 2
    assert report.collected.errors == {"r/down": "HTTP 503"}
    assert (report.filtered.checked, report.filtered.passed) == (2, 1)
    assert report.analyzed.analyzed == 1
    lines = report.summary_lines(usd_to_inr=88)
    assert any("r/down: FAILED" in line for line in lines)
    assert any("about Rs" in line for line in lines)
    assert store.count_dashboard_rows(conn, store.DashboardFilters()) == 1


def test_second_run_does_no_duplicate_work(conn, settings):
    sources = [FakeSource("r/smallbusiness", [make_post("reddit_1", title="automate it")])]
    run_daily(make_runtime(conn, settings, sources, fake_message(GOOD)))
    report = run_daily(make_runtime(conn, settings, sources))  # no Claude replies queued
    assert report.collected.total_new == 0
    assert report.analyzed.analyzed == 0


def test_open_runtime_turns_config_errors_into_clean_exit(monkeypatch, capsys):
    def broken():
        raise ConfigError("MAX_POSTS_PER_RUN must be a whole number")

    monkeypatch.setattr(cli, "load_settings", broken)
    with pytest.raises(SystemExit) as exit_info:
        with open_runtime():
            pass
    assert exit_info.value.code == 2
    assert "MAX_POSTS_PER_RUN" in capsys.readouterr().err


def test_open_runtime_requires_api_key_for_analysis(monkeypatch, settings, capsys):
    from dataclasses import replace

    monkeypatch.setattr(cli, "load_settings", lambda: replace(settings, anthropic_api_key=""))
    with pytest.raises(SystemExit):
        with open_runtime(need_llm=True):
            pass
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_open_runtime_builds_everything(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    with open_runtime(need_llm=True) as runtime:
        assert runtime.llm is not None
        assert runtime.sources and runtime.registry.models
        assert runtime.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
