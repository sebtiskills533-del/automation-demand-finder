"""Run the whole daily job: collect -> pre-filter -> analyse.

Usage (from the project folder):  .venv\\Scripts\\python run_daily.py
"""

from app.cli import open_runtime
from app.notify import fresh_gigs_message, send_toast
from app.pipeline import run_daily


def main() -> int:
    with open_runtime(need_llm=True) as runtime:
        report = run_daily(runtime)
        print("\n".join(report.summary_lines(runtime.settings.usd_to_inr)))
    if report.fresh_gigs:
        send_toast(*fresh_gigs_message(list(report.fresh_gigs)))
    # Exit code 1 tells Task Scheduler something needs attention.
    nothing_collected = report.collected.errors and not report.collected.new_per_source
    return 1 if nothing_collected or report.analyzed.stopped_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
