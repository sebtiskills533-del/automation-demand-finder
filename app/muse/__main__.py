"""Muse commands.

    .venv\\Scripts\\python -m app.muse open-chrome          open the app's Chrome on muse.ai (log in / set up)
    .venv\\Scripts\\python -m app.muse run [--focus TEXT]   collect posts with Muse, import and rate them
    .venv\\Scripts\\python -m app.muse run --no-rate        ...without rating
    .venv\\Scripts\\python -m app.muse import-last          import Muse's reply to the last job (no new job)
    .venv\\Scripts\\python -m app.muse check-replies        ask Muse for replies people sent you
    .venv\\Scripts\\python -m app.muse import-last-replies  save Muse's last replies list (no new job)
"""

from __future__ import annotations

import argparse
import sys

from app.analyze import analyze_pending
from app.cli import build_llm, open_runtime
from app.muse.browser import open_for_setup
from app.muse.job import (
    DEFAULT_REPLY_DAYS,
    import_latest_replies,
    import_latest_reply,
    run_muse_collection,
    run_reply_check,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.muse")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("open-chrome", help="open the app's Chrome on muse.ai")
    run = commands.add_parser("run", help="collect posts with Muse")
    run.add_argument("--focus", default="", help="what to look for, e.g. 'Etsy sellers and spreadsheets'")
    run.add_argument("--no-rate", action="store_true", help="import only; rate later")
    last = commands.add_parser("import-last", help="import Muse's reply to the last job")
    last.add_argument("--no-rate", action="store_true", help="import only; rate later")
    check = commands.add_parser("check-replies", help="ask Muse for replies people sent you")
    check.add_argument("--days", type=int, default=DEFAULT_REPLY_DAYS, help="how many days back (1-60)")
    commands.add_parser("import-last-replies", help="save Muse's last replies list (no new job)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    with open_runtime() as runtime:
        if args.command == "open-chrome":
            open_for_setup(runtime.settings.muse)
            print("The Muse Chrome is opening. Log in to muse.ai there (only needed once).")
            return 0
        if args.command in ("check-replies", "import-last-replies"):
            if args.command == "check-replies":
                print("Asking Muse for replies to you. This can take a few minutes.")
                result = run_reply_check(runtime.conn, runtime.settings, args.days)
            else:
                result = import_latest_replies(runtime.conn, runtime.settings)
            print(("Done: " if result.ok else "Failed: ") + result.message)
            return 0 if result.ok else 1

        def rate_waiting_posts() -> int:
            llm = build_llm(runtime.settings, runtime.conn)
            return analyze_pending(runtime.conn, llm, runtime.registry, runtime.settings).analyzed

        use_claude = not args.no_rate and bool(runtime.settings.anthropic_api_key)
        rate = rate_waiting_posts if use_claude else None

        if args.command == "import-last":
            print("Reading Muse's last reply (no new job is sent).")
            result = import_latest_reply(runtime.conn, runtime.settings, rate=rate)
        else:
            print("Asking Muse. This can take up to 30 minutes; you can watch it in the Muse Chrome.")
            result = run_muse_collection(runtime.conn, runtime.settings, args.focus, rate=rate)
    print(("Done: " if result.ok else "Failed: ") + result.message)
    if result.chat_url:
        print(f"Muse chat: {result.chat_url}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
