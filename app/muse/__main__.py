"""Muse commands.

    .venv\\Scripts\\python -m app.muse open-chrome          open the app's Chrome on muse.ai (log in / set up)
    .venv\\Scripts\\python -m app.muse run [--focus TEXT]   collect posts with Muse, import and rate them
    .venv\\Scripts\\python -m app.muse run --no-rate        ...without rating
"""

from __future__ import annotations

import argparse
import sys

from app.analyze import analyze_pending
from app.cli import build_llm, open_runtime
from app.muse.browser import open_for_setup
from app.muse.job import run_muse_collection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.muse")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("open-chrome", help="open the app's Chrome on muse.ai")
    run = commands.add_parser("run", help="collect posts with Muse")
    run.add_argument("--focus", default="", help="what to look for, e.g. 'Etsy sellers and spreadsheets'")
    run.add_argument("--no-rate", action="store_true", help="import only; rate later")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    with open_runtime() as runtime:
        if args.command == "open-chrome":
            open_for_setup(runtime.settings.muse)
            print("The Muse Chrome is opening. Log in to muse.ai there (only needed once).")
            return 0

        def rate_waiting_posts() -> int:
            llm = build_llm(runtime.settings, runtime.conn)
            return analyze_pending(runtime.conn, llm, runtime.registry, runtime.settings).analyzed

        use_claude = not args.no_rate and bool(runtime.settings.anthropic_api_key)
        rate = rate_waiting_posts if use_claude else None

        print("Asking Muse. This can take up to 30 minutes; you can watch it in the Muse Chrome.")
        result = run_muse_collection(runtime.conn, runtime.settings, args.focus, rate=rate)
    print(("Done: " if result.ok else "Failed: ") + result.message)
    if result.chat_url:
        print(f"Muse chat: {result.chat_url}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
