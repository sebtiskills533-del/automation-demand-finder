"""Keep a long-running dashboard from mixing old and new code after an update.

Streamlit re-runs dashboard.py on every interaction but keeps already-imported
modules in memory. After an update this caused an ImportError (new page code,
old login module). This guard notices that app/*.py changed since the last run
and drops all cached app.* modules, so everything is imported fresh.
"""

from __future__ import annotations

import sys
from collections.abc import MutableMapping
from pathlib import Path

_STATE_ATTR = "_automation_finder_code_fingerprint"  # kept on `sys`, which is never reloaded


def code_fingerprint(app_dir: Path) -> float:
    """Newest modification time of any Python file in the app."""
    return max((p.stat().st_mtime for p in app_dir.rglob("*.py")), default=0.0)


def refresh_if_code_changed(
    app_dir: Path,
    modules: MutableMapping[str, object] | None = None,
    state: object = sys,
) -> bool:
    """Forget cached app.* modules if the code changed. Returns True if it did."""
    modules = sys.modules if modules is None else modules
    current = code_fingerprint(app_dir)
    previous = getattr(state, _STATE_ATTR, None)
    setattr(state, _STATE_ATTR, current)
    if previous is None or previous == current:
        return False
    for name in [n for n in modules if n == "app" or n.startswith("app.")]:
        del modules[name]
    return True
