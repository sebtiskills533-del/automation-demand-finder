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

from app import db  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.models_registry import ModelRegistry, load_registry  # noqa: E402
from app.ui import opportunities, settings_page  # noqa: E402
from app.ui.login import require_login  # noqa: E402


@st.cache_resource(show_spinner=False)
def _load_config() -> tuple[Settings, ModelRegistry]:
    """Read settings once per dashboard start (restart after editing .env)."""
    settings = load_settings()
    setup_logging(settings.log_dir, settings.log_level)
    return settings, load_registry(settings.models_file)


def main() -> None:
    st.set_page_config(page_title="Automation Demand Finder", page_icon="🔎", layout="wide")
    try:
        settings, registry = _load_config()
    except AppError as exc:
        st.error(f"Configuration problem: {exc}")
        st.stop()

    with db.open_db(settings.db_path) as conn:
        if not require_login(conn, settings):
            st.stop()
        pages = [
            st.Page(lambda: opportunities.render(conn, settings.db_path),
                    title="Opportunities", icon="🎯", url_path="opportunities", default=True),
            st.Page(lambda: settings_page.render(conn, settings, registry),
                    title="Settings & costs", icon="⚙️", url_path="settings"),
        ]
        st.navigation(pages).run()


main()
