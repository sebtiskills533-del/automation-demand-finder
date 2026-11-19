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


def on_page(at: AppTest, title: str) -> bool:
    return any(title in t.value for t in at.title)


def metric(at: AppTest, label_part: str):
    return next(m for m in at.metric if label_part in m.label)


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
    assert on_page(login("old password 123"), "Opportunities")

    env_file.write_text(f"DASHBOARD_PASSWORD_HASH={hash_password('new password 456', n=2**10)}\n")
    later = env_file.stat().st_mtime + 5
    os.utime(env_file, (later, later))  # make sure the change is visible on any file system

    assert "Wrong password" in all_text(login("old password 123"))
    assert on_page(login("new password 456"), "Opportunities")


def test_wrong_password(env):
    at = login("not the password")
    assert "Wrong password" in all_text(at)
    assert not on_page(at, "Opportunities")


def test_lockout_after_repeated_failures(env):
    for _ in range(LOCKOUT_THRESHOLD):
        login("bad guess here")
    at = login(PASSWORD)
    assert "Too many wrong passwords" in all_text(at)


def test_login_shows_empty_state(env):
    at = login()
    assert not at.exception
    assert on_page(at, "Opportunities")
    assert "Nothing here yet" in all_text(at)


def test_login_shows_summary_table_and_details(env):
    seed(env)  # scores 90, 60, 30
    at = login()
    assert not at.exception
    assert metric(at, "Best leads").value == "2"
    assert "Automate thing 0" in all_text(at)  # details panel shows the top post
    assert any(b.label.startswith("Open the original post") for b in at.get("link_button"))
    assert len(at.dataframe) == 1


def test_stranger_text_cannot_become_links_or_images(env):
    from app.importer import import_rows

    with db.open_db(env) as conn:
        import_rows(conn, [{"url": "https://www.quora.com/q1",
                            "title": "Help ![pixel](https://tracker.example/p.png)",
                            "source": "[click me](https://evil.example)"}])
        post_id = conn.execute("SELECT id FROM posts").fetchone()[0]
        store.save_analysis(conn, make_analysis(post_id, opportunity_score=90))
    at = login()
    assert not at.exception
    rendered = " ".join(m.value for m in (*at.markdown, *at.caption))
    assert "](https://tracker.example" not in rendered and "](https://evil.example" not in rendered
    assert "\\[click me\\]" in rendered  # shown literally, escaped


def test_quick_views_and_filters(env):
    seed(env)
    at = login()
    assert "2 posts" in all_text(at)  # default view: best leads (score 45+)
    at.segmented_control(key="quick_view").set_value("📋 All").run()
    assert not at.exception and "3 posts" in all_text(at)
    _by_label(at.slider, "Minimum score").set_value(50).run()
    assert "2 posts" in all_text(at)
    at.text_input[0].input("thing 2").run()  # search box
    assert "Nothing here yet" in all_text(at)


def test_track_tab_changes_status(env):
    seed(env)
    at = login()
    at.segmented_control(key="status_reddit_0").set_value("Ticked").run()
    assert not at.exception
    with db.open_db(env) as conn:
        assert store.get_post_details(conn, "reddit_0")["status"] == "Ticked"


def test_reply_tab_drafts_with_claude_and_saves_edits(env, monkeypatch):
    from app.llm import ClaudeClient
    from app.ui import opportunities
    from tests.fakes import FakeAnthropic, fake_message

    seed(env)
    reply = {"reply": "Use an onFormSubmit trigger to append the row. Happy to help if you get stuck."}

    def fake_llm(settings, conn):
        sink = lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c)  # noqa: E731
        return ClaudeClient("key", sink, client=FakeAnthropic(fake_message(reply)))

    monkeypatch.setattr(opportunities, "build_llm", fake_llm)
    at = login()
    at = _click(at, "✨ Draft a reply with Claude")
    assert not at.exception
    box = at.text_area(key="reply_reddit_0")
    assert box.value.startswith("Use an onFormSubmit trigger")
    box.input("My own edited reply.")
    at = _click(at, "💾 Save edits")
    with db.open_db(env) as conn:
        assert store.get_post_details(conn, "reddit_0")["reply_draft"] == "My own edited reply."


def test_report_tab_asks_to_tick_first_then_shows_report(env, monkeypatch):
    from app.llm import ClaudeClient
    from app.ui import opportunities
    from tests.fakes import FakeAnthropic, fake_message
    from tests.test_v11 import REPORT

    seed(env)
    at = login()
    assert any("Tick this post first" in i.value for i in at.info)

    with db.open_db(env) as conn:
        store.update_action(conn, "reddit_0", status="Ticked")

    def fake_llm(settings, conn):
        sink = lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c)  # noqa: E731
        return ClaudeClient("key", sink, client=FakeAnthropic(fake_message(REPORT)))

    monkeypatch.setattr(opportunities, "build_llm", fake_llm)
    at = login()
    at = _click(at, "📋 Make Decision Report")
    assert not at.exception
    assert metric(at, "Build + test").value == "3.5 h"
    assert any("Tip: use an onFormSubmit trigger" in c.value for c in at.code)


def test_ai_errors_are_shown_not_crashed(env):
    seed(env)
    at = login()  # no ANTHROPIC_API_KEY in the test environment
    at = _click(at, "✨ Draft a reply with Claude")
    assert not at.exception
    assert any("ANTHROPIC_API_KEY" in e.value for e in at.error)


def _problems_page_app():
    from app import db
    from app.config import load_settings
    from app.models_registry import load_registry
    from app.ui import problems_page

    settings = load_settings()
    with db.open_db(settings.db_path) as conn:
        problems_page.render(conn, settings, load_registry(settings.models_file))


def test_problems_page_empty_then_groups_and_spec(env, monkeypatch):
    from app.llm import ClaudeClient
    from app.ui import problems_page
    from tests.fakes import FakeAnthropic, fake_message
    from tests.test_v11 import SPEC

    at = AppTest.from_function(_problems_page_app, default_timeout=TIMEOUT).run()
    assert not at.exception and "No repeated problems yet" in all_text(at)

    seed(env)
    with db.open_db(env) as conn:
        store.replace_clusters(conn, [("etsy-orders", "Etsy orders to sheet", "Copy orders",
                                       ["reddit_0", "reddit_1"])], ["reddit_0", "reddit_1"])

    def fake_llm(settings, conn):
        sink = lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c)  # noqa: E731
        return ClaudeClient("key", sink, client=FakeAnthropic(fake_message(SPEC)))

    monkeypatch.setattr(problems_page, "build_llm", fake_llm)
    at = AppTest.from_function(_problems_page_app, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert "Etsy orders to sheet" in all_text(at)
    at = _click(at, "📝 Write product spec")
    assert not at.exception
    assert any("Etsy Order Tracker Google Sheets Template" in c.value for c in at.code)
    with db.open_db(env) as conn:
        assert store.get_product_spec(conn, "etsy-orders")["product_name"] == "Etsy Order Tracker"


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


def test_import_last_reply_button_starts_background_import(env, monkeypatch):
    from app.ui import import_page

    monkeypatch.setenv("MUSE_ENABLED", "true")
    started = []
    monkeypatch.setattr(import_page, "start_background_import_last", lambda: started.append(True))
    at = AppTest.from_function(_import_page_app, default_timeout=TIMEOUT).run()
    at = _click(at, "📥 Import Muse's last reply")
    assert not at.exception and started == [True]


def test_start_background_run_uses_argument_list(monkeypatch):
    from app.ui import background, import_page

    calls = []
    monkeypatch.setattr(background.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
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
    assert on_page(at, "Settings & costs")
    assert len(at.dataframe) == 2  # usage this month + the model list
    models_table = at.dataframe[1].value
    assert list(models_table["Level"]) == [1, 2, 3, 4]
    at.selectbox(key="model_analyze").set_value("claude-sonnet-5-5").run()
    assert not at.exception
    with db.open_db(env) as conn:
        assert store.get_setting(conn, "model.analyze") == "claude-sonnet-5-5"


def test_writing_style_switch_on_settings_page_is_saved(env):
    at = AppTest.from_function(_settings_page_app, default_timeout=TIMEOUT).run()
    at.segmented_control(key="style_settings_convincing").set_value("tip_first").run()
    assert not at.exception
    with db.open_db(env) as conn:
        assert store.get_setting(conn, "writing.style") == "tip_first"
    # Every picker now shows the saved choice.
    at = AppTest.from_function(_settings_page_app, default_timeout=TIMEOUT).run()
    assert at.segmented_control(key="style_settings_tip_first").value == "tip_first"


def test_reply_tab_shows_the_style_switch(env):
    seed(env)
    at = login()
    assert not at.exception
    assert at.segmented_control(key="style_reply_reddit_0_convincing").value == "convincing"


# ---------------------------------------------------------------- replies

FB_REPLY_URL = "https://www.facebook.com/groups/1/posts/2/?comment_id=3"


def _replies_page_app():
    from app import db
    from app.config import load_settings
    from app.models_registry import load_registry
    from app.ui import replies_page

    settings = load_settings()
    with db.open_db(settings.db_path) as conn:
        replies_page.render(conn, settings, load_registry(settings.models_file))


def test_replies_page_empty_then_manual_reply_with_email(env):
    at = AppTest.from_function(_replies_page_app, default_timeout=TIMEOUT).run()
    assert not at.exception and "No replies yet" in all_text(at)
    _by_label(at.text_input, "Link to the conversation").input(FB_REPLY_URL)
    _by_label(at.text_input, "Their name").input("Sara")
    _by_label(at.text_area, "Their reply").input("Yes please, email me: sara@example.com")
    at = _click(at, "Add reply")
    assert not at.exception
    assert any("1 new replies" in s.value for s in at.success)
    at = AppTest.from_function(_replies_page_app, default_timeout=TIMEOUT).run()
    assert any(c.value == "sara@example.com" for c in at.code)  # shown, copyable
    assert any(b.label.startswith("Open the conversation") for b in at.get("link_button"))
    with db.open_db(env) as conn:
        assert store.unseen_reply_count(conn) == 0  # opening it marked it read


def test_replies_page_drafts_email_and_offers_mailto(env, monkeypatch):
    from app.inbox import add_manual_reply
    from app.llm import ClaudeClient
    from app.ui import reply_widgets
    from tests.fakes import FakeAnthropic, fake_message
    from tests.test_inbox import EMAIL

    with db.open_db(env) as conn:
        add_manual_reply(conn, FB_REPLY_URL, "Sara", "Interested! sara@example.com")

    def fake_llm(settings, conn):
        sink = lambda t, m, i, o, c: store.log_usage(conn, t, m, i, o, c)  # noqa: E731
        return ClaudeClient("key", sink, client=FakeAnthropic(fake_message(EMAIL)))

    monkeypatch.setattr(reply_widgets, "build_llm", fake_llm)
    at = AppTest.from_function(_replies_page_app, default_timeout=TIMEOUT).run()
    at = _click(at, "✨ Draft an email with Claude")
    assert not at.exception
    assert _by_label(at.text_input, "Subject").value == EMAIL["subject"]
    mail = next(b for b in at.get("link_button") if b.label.startswith("📧 Open in my email app"))
    assert mail.proto.url.startswith("mailto:sara@example.com?subject=")


def test_reply_without_email_suggests_answering_on_the_platform(env):
    from app.inbox import add_manual_reply

    with db.open_db(env) as conn:
        add_manual_reply(conn, FB_REPLY_URL, "Tom", "Thanks, that worked")
    at = AppTest.from_function(_replies_page_app, default_timeout=TIMEOUT).run()
    assert "didn't share an email" in all_text(at)
    assert not any("Draft an email" in b.label for b in at.button)


def test_replies_page_starts_muse_check_in_background(env, monkeypatch):
    from app.ui import replies_page

    monkeypatch.setenv("MUSE_ENABLED", "true")
    started = []
    monkeypatch.setattr(replies_page, "start_muse_process", lambda args: started.append(args))
    at = AppTest.from_function(_replies_page_app, default_timeout=TIMEOUT).run()
    at = _click(at, "🔍 Check for replies with Muse")
    assert not at.exception and started == [["check-replies", "--days", "14"]]


def test_new_replies_pop_up_and_show_on_the_post(env):
    from app.inbox import add_manual_reply
    from app.importer import import_rows

    with db.open_db(env) as conn:
        import_rows(conn, [{"url": "https://www.facebook.com/groups/1/?multi_permalinks=2", "title": "Need a bot"}])
        post_id = conn.execute("SELECT id FROM posts").fetchone()[0]
        store.save_analysis(conn, make_analysis(post_id, opportunity_score=90))
        add_manual_reply(conn, FB_REPLY_URL, "Sara", "I'm interested")
    at = login()
    assert not at.exception
    assert any("1 new reply" in t.value for t in at.toast)
    assert any(t.label == "💬 Replies (1 new)" for t in at.tabs)
    assert "I'm interested" in all_text(at)
