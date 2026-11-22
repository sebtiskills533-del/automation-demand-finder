"""The daily run: collect -> pre-filter -> analyse -> group into top problems.

It is scheduled every 3 hours (scripts/schedule_every_3_hours.ps1) so gigs are seen before the
crowd: grouping runs at most once a day, and good fresh gigs with few bids are reported so the
run can show a notification.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.analyze import AnalyzeReport, analyze_pending
from app.cli import Runtime
from app.clusters import run_clustering
from app.collect import CollectReport, collect
from app import store
from app.backup import daily_backup
from app.domain import to_iso, utc_now
from app.errors import AppError
from app.prefilter import FilterReport, load_phrases, run_prefilter

log = logging.getLogger(__name__)

GROUPING_EVERY = timedelta(hours=20)  # about once a day, even with runs every 3 hours
_LAST_GROUPING_KEY = "clusters.last_attempt_at"
ALERT_MIN_SCORE = 45
ALERT_MAX_BIDS = 20


@dataclass(frozen=True)
class DailyReport:
    collected: CollectReport
    filtered: FilterReport
    analyzed: AnalyzeReport
    grouping: str = ""
    fresh_gigs: tuple[dict, ...] = ()
    backup: str = ""

    def summary_lines(self, usd_to_inr: float) -> list[str]:
        lines = [f"Collected {self.collected.total_new} new posts:"]
        lines += [f"  {name}: {count} new" for name, count in self.collected.new_per_source.items()]
        lines += [f"  {name}: FAILED ({problem})" for name, problem in self.collected.errors.items()]
        lines.append(f"Pre-filter: {self.filtered.passed} of {self.filtered.checked} look like requests.")
        a = self.analyzed
        cost_inr = a.cost_usd * usd_to_inr
        lines.append(
            f"Analysed {a.analyzed} (skipped {a.failed}). Tokens {a.input_tokens} in / "
            f"{a.output_tokens} out. Cost ${a.cost_usd:.4f} (about Rs {cost_inr:.2f})."
        )
        if a.stopped_reason:
            lines.append(f"Stopped early: {a.stopped_reason}")
        if self.grouping:
            lines.append(f"Top problems: {self.grouping}")
        if self.backup:
            lines.append(f"Backup: {self.backup}")
        if self.fresh_gigs:
            lines.append(f"Fresh gigs with few bids: {len(self.fresh_gigs)}")
            lines += [f"  {g['title'][:70]} ({g['bids'] if g['bids'] is not None else '?'} bids)"
                      for g in self.fresh_gigs[:5]]
        return lines


def run_daily(runtime: Runtime, now: Callable[[], datetime] = utc_now) -> DailyReport:
    """Each step saves its work before the next starts, so nothing is lost."""
    assert runtime.llm is not None, "run_daily needs a Claude client"
    started = now()
    collected = collect(runtime.conn, runtime.sources, runtime.http)
    filtered = run_prefilter(runtime.conn, load_phrases(runtime.settings.phrases_file))
    analyzed = analyze_pending(runtime.conn, runtime.llm, runtime.registry, runtime.settings)
    fresh = tuple(store.fresh_gigs(runtime.conn, started - timedelta(seconds=1), ALERT_MIN_SCORE, ALERT_MAX_BIDS))
    grouping = _group(runtime, now())
    backup = daily_backup(runtime.conn, runtime.settings.db_path.parent / "backups", now().astimezone().date())
    report = DailyReport(collected, filtered, analyzed, grouping, fresh, backup)
    log.info("Daily run finished: %s", " | ".join(report.summary_lines(runtime.settings.usd_to_inr)))
    return report


def _group(runtime: Runtime, moment: datetime) -> str:
    """Grouping is a bonus: a problem here must never fail the daily run. At most once a day."""
    last = store.get_setting(runtime.conn, _LAST_GROUPING_KEY)
    try:
        if last and moment - datetime.fromisoformat(last.replace("Z", "+00:00")) < GROUPING_EVERY:
            return "already grouped today."
    except ValueError:
        pass  # an unreadable time: just group again
    store.set_setting(runtime.conn, _LAST_GROUPING_KEY, to_iso(moment))
    try:
        result = run_clustering(runtime.conn, runtime.llm, runtime.registry, runtime.settings)
    except AppError as exc:
        log.warning("Grouping skipped: %s", exc)
        return f"skipped ({exc})"
    if result.skipped_reason:
        return result.skipped_reason
    return f"{result.grouped_posts} posts in {result.groups} groups."
