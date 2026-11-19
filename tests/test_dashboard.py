"""Run the real Streamlit dashboard headlessly with streamlit.testing."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app import db, store
from app.auth import LOCKOUT_THRESHOLD, hash_password
from app.config import PROJECT_ROOT
from tests.conftest import make_analysis, make_post

DASHBOARD = str(PROJECT_ROOT / "app" / "dashboard.py")
PASSWORD = "dashboard test pass"
TIMEOUT = 30


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "dash.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", hash_password(PASSWORD, n=2**10))
    st.cache_resource.clear()
    st.cache_data.clear()
    yield db_path
    st.cache_resource.clear()


def seed(db_path: Path, count: int = 3) -> None:
    with db.open_db(db_path) as conn:
        for i in range(count):
            store.save_post(conn, make_post(f"reddit_{i}", title=f"Automate thing {i}"))
            store.save_analysis(conn, make_analysis(f"reddit_{i}", opportunity_score=90 - i * 30))


def login(password: str = PASSWORD) -> AppTest:
    at = AppTest.from_file(DASHBOARD, default_timeout=TIMEOUT).run()
    at.text_input[0].input(password)
    at.button[0].click()
    return at.run()


def all_text(at: AppTest) -> str:
    parts = [e.value for e in (*at.markdown, *at.error, *at.info, *at.title, *at.caption, *at.text)]
    return "\n".join(str(p) for p in parts)


def test_missing_password_hash_blocks_dashboard(env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "")
    at = AppTest.from_file(DASHBOARD, default_timeout=TIMEOUT).run()
    assert "set_password" in all_text(at)
    assert not at.text_input  # no login form, no data


def test_login_form_shown_first_and_no_data_leaks(env):
    seed(env)
    at = AppTest.from_file(DASHBOARD, default_timeout=TIMEOUT).run()
    assert at.text_input[0].label == "Password"
    assert "Automate thing" not in all_text(at)
    assert len(at.dataframe) == 0


def test_new_password_works_without_restarting_dashboard(env, tmp_path, monkeypatch):
    """Regression: changing the password in .env must take effect in a running dashboard."""
    import os

    from app import config

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH")  # read it from the .env file instead

    env_file.write_text(f"DASHBOARD_PASSWORD_HASH={hash_password('old password 123', n=2**10)}\n")
    assert any(t.value == "Opportunities" for t in login("old password 123").title)

    env_file.write_text(f"DASHBOARD_PASSWORD_HASH={hash_password('new password 456', n=2**10)}\n")
    later = env_file.stat().st_mtime + 5
    os.utime(env_file, (later, later))  # make sure the change is visible on any file system

    assert "Wrong password" in all_text(login("old password 123"))
    assert any(t.value == "Opportunities" for t in login("new password 456").title)


def test_wrong_password(env):
    at = login("not the password")
    assert "Wrong password" in all_text(at)
    assert not any(t.value == "Opportunities" for t in at.title)


def test_lockout_after_repeated_failures(env):
    for _ in range(LOCKOUT_THRESHOLD):
        login("bad guess here")
    at = login(PASSWORD)
    assert "Too many wrong passwords" in all_text(at)


def test_login_shows_empty_state(env):
    at = login()
    assert not at.exception
    assert any(t.value == "Opportunities" for t in at.title)
    assert "No analysed posts match" in all_text(at)


def test_login_shows_ranked_table_and_details(env):
    seed(env)
    at = login()
    assert not at.exception
    assert any(m.label == "Analysed by Claude" and m.value == "3" for m in at.metric)
    assert "Automate thing 0" in all_text(at)  # details panel shows the top post
    assert any(b.label.startswith("Open the original post") for b in at.get("link_button"))


def test_filters_narrow_results(env):
    seed(env)
    at = login()
    at.slider[0].set_value(50).run()  # minimum opportunity score
    assert not at.exception
    assert "2 matching posts" in all_text(at)


def _settings_page_app():
    # Runs inside AppTest, so it imports what it needs itself.
    from app import db
    from app.config import load_settings
    from app.models_registry import load_registry
    from app.ui import settings_page

    settings = load_settings()
    with db.open_db(settings.db_path) as conn:
        settings_page.render(conn, settings, load_registry(settings.models_file))


def _import_page_app():
    from app import db
    from app.config import load_settings
    from app.models_registry import load_registry
    from app.ui import import_page

    settings = load_settings()
    with db.open_db(settings.db_path) as conn:
        import_page.render(conn, settings, load_registry(settings.models_file))


def _by_label(widgets, prefix):
    return next(w for w in widgets if w.label.startswith(prefix))


def _click(at, label):
    _by_label(at.button, label).click()
    return at.run()


def test_import_page_add_one_post(env):
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    assert not at.exception
    _by_label(at.text_input, "Link to the post").input("https://www.facebook.com/groups/x/posts/9")
    _by_label(at.text_input, "Title").input("Need orders copied to Google Sheets")
    _by_label(at.text_area, "Post text").input("Call me on 555-123-4567")
    at = _click(at, "Add post")
    assert not at.exception
    assert any("1 added" in s.value for s in at.success)
    with db.open_db(env) as conn:
        row = conn.execute("SELECT platform, body, passed_filter FROM posts").fetchone()
    assert tuple(row) == ("facebook", "Call me on [phone removed]", 1)


def test_import_page_paste_reports_bad_rows(env):
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    _by_label(at.text_area, "Paste CSV").input(
        "url,title\nhttps://www.quora.com/q1,How to automate invoices\nnot-a-link,Bad row\n"
    )
    at = _click(at, "Import")
    assert any("1 added" in w.value and "1 skipped" in w.value for w in at.warning)
    assert any("Row 2: url must be a full https:// link" in t.value for t in at.text)


def test_import_page_rate_now_uses_claude(env, monkeypatch):
    from app import store as store_module
    from app.llm import ClaudeClient
    from app.ui import import_page
    from tests.fakes import FakeAnthropic, fake_message
    from tests.test_analyze import GOOD

    def fake_llm(settings, conn):
        sink = lambda t, m, i, o, c: store_module.log_usage(conn, t, m, i, o, c)  # noqa: E731
        return ClaudeClient("key", sink, client=FakeAnthropic(fake_message(GOOD)))

    monkeypatch.setattr(import_page, "build_llm", fake_llm)
    with db.open_db(env) as conn:
        from app.importer import import_rows

        import_rows(conn, [{"url": "https://www.quora.com/q1", "title": "How to automate invoices"}])
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    at = _click(at, "Rate new posts now")
    assert not at.exception
    assert any("Rated 1 posts" in s.value for s in at.success)
    with db.open_db(env) as conn:
        assert store.count_posts(conn)["analyzed"] == 1


def test_import_page_rate_now_shows_missing_key(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    at = _click(at, "Rate new posts now")
    assert any("ANTHROPIC_API_KEY" in e.value for e in at.error)


def test_import_page_muse_section_off_by_default(env):
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    assert any("Muse collection is switched off" in i.value for i in at.info)
    assert not any(b.label == "Start Muse collection" for b in at.button)


def test_import_page_muse_start_runs_in_background(env, monkeypatch):
    from app.ui import import_page

    monkeypatch.setenv("MUSE_ENABLED", "true")
    started = []
    monkeypatch.setattr(import_page, "start_background_run", lambda focus: started.append(focus))
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    _by_label(at.text_input, "What should Muse look for").input("Etsy sellers")
    at = _click(at, "Start Muse collection")
    assert not at.exception
    assert started == ["Etsy sellers"]
    assert any("Started" in s.value for s in at.success)


def test_import_page_shows_muse_runs_and_blocks_double_start(env, monkeypatch):
    monkeypatch.setenv("MUSE_ENABLED", "true")
    with db.open_db(env) as conn:
        done = store.start_muse_run(conn, "")
        store.finish_muse_run(conn, done, ok=True, message="Import: 3 added.", added=3)
        store.start_muse_run(conn, "")  # still running
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert _by_label(at.button, "Start Muse collection").disabled
    assert any("in progress" in i.value for i in at.info)
    assert len(at.dataframe) == 1


def test_start_background_run_uses_argument_list(monkeypatch):
    from app.ui import import_page

    calls = []
    monkeypatch.setattr(import_page.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    import_page.start_background_run('Etsy"; rm -rf /')
    args, options = calls[0]
    assert args[1:4] == ["-m", "app.muse", "run"]
    assert args[-1] == 'Etsy"; rm -rf /'  # passed as one argument, never through a shell
    assert "shell" not in options


def test_settings_page_lists_all_models_and_saves_choice(env):
    # AppTest can't switch to function-based pages, so render the page directly.
    with db.open_db(env) as conn:
        store.log_usage(conn, "analyze", "claude-haiku-5-5", 1000, 200, 0.0002)
    at = AppTest.from_function(_settings_page_app, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert any(t.value == "Settings & costs" for t in at.title)
    assert len(at.dataframe) == 2  # usage this month + the model list
    models_table = at.dataframe[1].value
    assert list(models_table["Level"]) == [1, 2, 3, 4]
    at.selectbox(key="model_analyze").set_value("claude-sonnet-5-5").run()
    assert not at.exception
    with db.open_db(env) as conn:
        assert store.get_setting(conn, "model.analyze") == "claude-sonnet-5-5"
