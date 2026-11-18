"""The daily run: collect -> pre-filter -> analyse."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.analyze import AnalyzeReport, analyze_pending
from app.cli import Runtime
from app.collect import CollectReport, collect
from app.prefilter import FilterReport, load_phrases, run_prefilter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyReport:
    collected: CollectReport
    filtered: FilterReport
    analyzed: AnalyzeReport

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
        return lines


def run_daily(runtime: Runtime) -> DailyReport:
    """Each step saves its work before the next starts, so nothing is lost."""
    assert runtime.llm is not None, "run_daily needs a Claude client"
    collected = collect(runtime.conn, runtime.sources, runtime.http)
    filtered = run_prefilter(runtime.conn, load_phrases(runtime.settings.phrases_file))
    analyzed = analyze_pending(runtime.conn, runtime.llm, runtime.registry, runtime.settings)
    report = DailyReport(collected, filtered, analyzed)
    log.info("Daily run finished: %s", " | ".join(report.summary_lines(runtime.settings.usd_to_inr)))
    return report
