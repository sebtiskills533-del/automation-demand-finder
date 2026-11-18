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
    if not buildable or difficulty == "high" or est_hours > MEDIUM_MAX_HOURS:
        return None
    if est_hours <= EASY_MAX_HOURS:
        return DeliveryWindow(1, 3)
    return DeliveryWindow(3, 7)


def delivery_label(min_days: int | None, max_days: int | None) -> str:
    """Text for stored values; None means the job is too big to promise."""
    if min_days is None or max_days is None:
        return "Skip - too big"
    return DeliveryWindow(min_days, max_days).label
