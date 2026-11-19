"""Set (or change) the dashboard password.

Usage:  .venv\\Scripts\\python -m app.set_password
        .venv\\Scripts\\python -m app.set_password --visible   (if hidden typing doesn't work)

You type the password twice (hidden by default). Only its hash is saved,
into the DASHBOARD_PASSWORD_HASH line of your .env file.
"""

from __future__ import annotations

import getpass
import os
import sys
import re
import shutil
import tempfile
from pathlib import Path

from app.auth import hash_password, password_problems
from app.config import ENV_FILE, PROJECT_ROOT

ENV_KEY = "DASHBOARD_PASSWORD_HASH"
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9:_\-]*$")


def write_env_value(env_path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, keeping every other line as it is.

    Creates the file from .env.example if it doesn't exist yet. The file is
    replaced in one step, so a crash can't leave it half-written.
    """
    if not _KEY_RE.match(key):
        raise ValueError(f"Invalid setting name: {key!r}")
    if not _SAFE_VALUE_RE.match(value):
        raise ValueError("Value contains characters that are unsafe in a .env file")

    if not env_path.exists():
        example = env_path.with_name(".env.example")
        if example.exists():
            shutil.copyfile(example, env_path)
        else:
            env_path.touch()

    lines = env_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)

    fd, tmp_name = tempfile.mkstemp(dir=env_path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp:
            tmp.write("\n".join(lines) + "\n")
        os.replace(tmp_name, env_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def set_password(env_path: Path, password: str) -> None:
    problems = password_problems(password)
    if problems:
        raise ValueError(" ".join(problems))
    write_env_value(env_path, ENV_KEY, hash_password(password))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    visible = "--visible" in args
    if visible:
        # For terminals where hidden typing doesn't work. Clear the screen afterwards.
        print("Choose a dashboard password (at least 10 characters). Typing WILL be visible.")
        ask = input
    else:
        print("Choose a dashboard password (at least 10 characters).")
        print("Typing is hidden on purpose: nothing appears, just type and press Enter.")
        print("If your keyboard does nothing, run again with:  -m app.set_password --visible")
        ask = getpass.getpass
    first = ask("New password: ")
    second = ask("Type it again: ")
    if first != second:
        print("The two passwords don't match. Nothing was changed.")
        return 1
    try:
        set_password(ENV_FILE, first)
    except ValueError as exc:
        print(f"Password not accepted: {exc}")
        return 1
    print(f"Saved. The hash is in {ENV_FILE.relative_to(PROJECT_ROOT)}; restart the dashboard to use it.")
    if visible:
        print("Tip: type  cls  and press Enter to clear the password from the screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
