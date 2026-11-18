"""Logging: short messages on screen, full detail in logs/app.log."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_NAME = "automation-finder-file"


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Send logs to a rotating file (max 5 x 1 MB). Safe to call more than once."""
    root = logging.getLogger()
    root.setLevel(level)
    if any(h.get_name() == _HANDLER_NAME for h in root.handlers):
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.set_name(_HANDLER_NAME)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    root.addHandler(file_handler)

    # Third-party libraries are noisy at INFO; keep them to warnings.
    for noisy in ("httpx", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
