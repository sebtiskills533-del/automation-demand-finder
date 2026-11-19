"""Delivery-time rules from the PRD (they already include a beginner safety margin).

| Job                        | Build + test | Promise to client |
| Easy                       | up to 5 h    | 1-3 days          |
| Medium                     | 5-10 h       | 3-7 days          |
| Hard (>10 h, high, unbuildable) | -       | Skip              |
"""

from __future__ import annotations

from dataclasses import dataclass

EASY_MAX_HOURS = 5
MEDIUM_MAX_HOURS = 10


@dataclass(frozen=True)
class DeliveryWindow:
    min_days: int
    max_days: int

    @property
    def label(self) -> str:
        return f"{self.min_days}-{self.max_days} days"


def delivery_window(est_hours: float, difficulty: str, buildable: bool) -> DeliveryWindow | None:
    """How many days to promise, or None when the job should be skipped."""
    if est_hours < 0:
        raise ValueError("est_hours cannot be negative")
    if not buildable or is_too_big(difficulty, est_hours):
        return None
    if est_hours <= EASY_MAX_HOURS:
        return DeliveryWindow(1, 3)
    return DeliveryWindow(3, 7)


TOO_BIG = "Skip - too big"
NOT_A_JOB = "Skip - not a job"


def is_too_big(difficulty: str, est_hours: float) -> bool:
    return difficulty == "high" or est_hours > MEDIUM_MAX_HOURS


def delivery_label(
    min_days: int | None, max_days: int | None, difficulty: str, est_hours: float
) -> str:
    """Days to promise, or why there is no promise.

    A small job with no delivery window was skipped because it isn't a job
    for a freelancer (news, a showcase, a vendor-only problem), not because
    it is big, so it must not say "too big".
    """
    if min_days is not None and max_days is not None:
        return DeliveryWindow(min_days, max_days).label
    return TOO_BIG if is_too_big(difficulty, est_hours) else NOT_A_JOB
