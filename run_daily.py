"""Run the whole daily job: collect -> pre-filter -> analyse.

Usage (from the project folder):  .venv\\Scripts\\python run_daily.py
"""

import argparse
import logging
import sys
from datetime import datetime

from app.cli import open_runtime
from app.notify import fresh_gigs_message, send_toast
from app.pipeline import run_daily
from app.schedule import get_schedule, should_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_daily.py")
    parser.add_argument("--scheduled", action="store_true",
                        help="started by Task Scheduler: obey the on/off switch and hours on the Settings page")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    with open_runtime(need_llm=True) as runtime:
        if args.scheduled:
            ok, reason = should_run(get_schedule(runtime.conn), datetime.now())
            if not ok:
                logging.getLogger("run_daily").info("Scheduled run skipped: %s", reason)
                print(f"Skipped: {reason}")
                return 0
        report = run_daily(runtime)
        print("\n".join(report.summary_lines(runtime.settings.usd_to_inr)))
    if report.fresh_gigs:
        send_toast(*fresh_gigs_message(list(report.fresh_gigs)))
    # Exit code 1 tells Task Scheduler something needs attention.
    nothing_collected = report.collected.errors and not report.collected.new_per_source
    return 1 if nothing_collected or report.analyzed.stopped_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
