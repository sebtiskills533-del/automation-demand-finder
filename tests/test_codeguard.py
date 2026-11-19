import os
from types import SimpleNamespace

from app.codeguard import code_fingerprint, refresh_if_code_changed


def make_app(tmp_path):
    app = tmp_path / "app"
    (app / "ui").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "ui" / "login.py").write_text("x = 1")
    return app


def test_fingerprint_is_newest_python_file(tmp_path):
    app = make_app(tmp_path)
    os.utime(app / "ui" / "login.py", (5000, 5000))
    os.utime(app / "__init__.py", (1000, 1000))
    assert code_fingerprint(app) == 5000
    assert code_fingerprint(tmp_path / "missing") == 0.0


def test_first_run_keeps_modules(tmp_path):
    app = make_app(tmp_path)
    modules = {"app": 1, "app.ui.login": 2, "streamlit": 3}
    state = SimpleNamespace()
    assert refresh_if_code_changed(app, modules, state) is False
    assert set(modules) == {"app", "app.ui.login", "streamlit"}


def test_unchanged_code_keeps_modules(tmp_path):
    app = make_app(tmp_path)
    state = SimpleNamespace()
    refresh_if_code_changed(app, {}, state)
    modules = {"app.ui.login": 2}
    assert refresh_if_code_changed(app, modules, state) is False
    assert modules == {"app.ui.login": 2}


def test_changed_code_drops_only_app_modules(tmp_path):
    # Regression: an updated dashboard.py imported a stale app.ui.login (ImportError).
    app = make_app(tmp_path)
    state = SimpleNamespace()
    refresh_if_code_changed(app, {}, state)
    newer = code_fingerprint(app) + 10
    os.utime(app / "ui" / "login.py", (newer, newer))
    modules = {"app": 1, "app.ui.login": 2, "application_other": 3, "streamlit": 4}
    assert refresh_if_code_changed(app, modules, state) is True
    assert set(modules) == {"application_other", "streamlit"}
