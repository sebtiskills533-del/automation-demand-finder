"""Streamlit dashboard entry point.

Start it from the project folder with:
    .venv\\Scripts\\python -m streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit runs this file as a script, so make the project importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402

from app.codeguard import code_fingerprint, refresh_if_code_changed  # noqa: E402

# After an update, re-import every app module instead of mixing old and new code.
refresh_if_code_changed(_PROJECT_ROOT / "app")

from app import config, db, store  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.models_registry import ModelRegistry, load_registry  # noqa: E402
from app.ui import import_page, opportunities, problems_page, replies_page, settings_page  # noqa: E402
from app.ui.components import apply_page_style  # noqa: E402
from app.ui.login import logout_button, require_login  # noqa: E402


def _config_version() -> tuple[float, float, float]:
    """Last-changed times of .env, models.json and the app code. When any of them
    changes (for example a new password or an update), cached settings are reloaded."""
    def mtime(path) -> float:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    return (mtime(config.ENV_FILE), mtime(config.CONFIG_DIR / "models.json"),
            code_fingerprint(_PROJECT_ROOT / "app"))


@st.cache_resource(show_spinner=False, max_entries=1)
def _load_config(version: tuple[float, float, float]) -> tuple[Settings, ModelRegistry]:
    """Read settings; `version` changes whenever .env, models.json or the code changes."""
    settings = load_settings(env_file=config.ENV_FILE)
    setup_logging(settings.log_dir, settings.log_level)
    return settings, load_registry(settings.models_file)


def _notify_new_replies(count: int) -> None:
    """A pop-up when new replies arrived since the last one we announced."""
    if count and st.session_state.get("announced_replies") != count:
        st.toast(f"🔔 You have {count} new {'reply' if count == 1 else 'replies'}. See the Replies page.")
    st.session_state["announced_replies"] = count


def main() -> None:
    st.set_page_config(page_title="Automation Demand Finder", page_icon="🔎", layout="wide")
    apply_page_style()
    try:
        settings, registry = _load_config(_config_version())
    except AppError as exc:
        st.error(f"Configuration problem: {exc}")
        st.stop()

    with db.open_db(settings.db_path) as conn:
        if not require_login(conn, settings):
            st.stop()
        assets = Path(__file__).parent / "ui" / "assets"
        st.logo(str(assets / "logo.svg"), icon_image=str(assets / "icon.svg"), size="large")
        new_replies = store.unseen_reply_count(conn)
        _notify_new_replies(new_replies)
        pages = [
            st.Page(lambda: opportunities.render(conn, settings, registry),
                    title="Opportunities", icon="🎯", url_path="opportunities", default=True),
            st.Page(lambda: replies_page.render(conn, settings, registry),
                    title=f"Replies ({new_replies} new)" if new_replies else "Replies",
                    icon="🔔", url_path="replies"),
            st.Page(lambda: problems_page.render(conn, settings, registry),
                    title="Top problems", icon="🧩", url_path="problems"),
            st.Page(lambda: import_page.render(conn, settings, registry),
                    title="Import posts", icon="📥", url_path="import"),
            st.Page(lambda: settings_page.render(conn, settings, registry),
                    title="Settings & costs", icon="⚙️", url_path="settings"),
        ]
        st.navigation(pages).run()
        logout_button(conn)


main()
