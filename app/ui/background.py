"""Start Muse jobs as separate processes, so the dashboard stays responsive."""

from __future__ import annotations

import subprocess
import sys

from app.config import PROJECT_ROOT


def start_muse_process(muse_args: list[str]) -> None:
    """Run `python -m app.muse <muse_args>` in the background.

    An argument list, never a shell, so nothing the user typed can run as a command.
    """
    args = [sys.executable, "-m", "app.muse", *muse_args]
    options: dict = {"cwd": PROJECT_ROOT, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(args, **options)  # noqa: S603
