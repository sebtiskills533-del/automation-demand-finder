"""Turn Claude's ratings into one 0-100 "should I work on this?" score.

The formula is fixed code (not AI) so the ranking is predictable and testable.
"""

from __future__ import annotations

_EASE = {"low": 5, "medium": 3, "high": 1}

# Weights add up to 1. Seriousness matters most: a real, committed person.
_W_SERIOUSNESS = 0.45
_W_POTENTIAL = 0.30
_W_EASE = 0.25

HIRE_BONUS = 10
NOT_BUILDABLE_CAP = 30

WORK_ON_THIS = "Work on this"
WORTH_A_LOOK = "Worth a look"
SKIP = "Skip"


def bid_adjustment(bids: int | None) -> int:
    """Freelancer.com gigs: few bids = a real chance to be noticed; 80+ bids = very hard to win.
    (Kept in step with the one-off update in db._SCHEMA_V12.)"""
    if bids is None:
        return 0
    if bids <= 10:
        return 5
    if bids <= 30:
        return 0
    if bids <= 80:
        return -10
    return -20


def opportunity_score(
    seriousness: int, product_potential: int, difficulty: str, buildable: bool, wants_to_hire: bool,
    bids: int | None = None,
) -> int:
    """0-100. Each 1-5 rating is weighted; easy jobs score higher.

    - Someone openly offering to pay gets a bonus.
    - Gigs with few bids go up, crowded ones down (bid_adjustment).
    - Jobs we can't build are capped low, however attractive.
    """
    for name, value in (("seriousness", seriousness), ("product_potential", product_potential)):
        if not 1 <= value <= 5:
            raise ValueError(f"{name} must be 1-5, got {value}")
    if difficulty not in _EASE:
        raise ValueError(f"Unknown difficulty {difficulty!r}")

    weighted = (
        _W_SERIOUSNESS * seriousness + _W_POTENTIAL * product_potential + _W_EASE * _EASE[difficulty]
    )
    score = round((weighted - 1) / 4 * 100)  # map 1..5 onto 0..100
    if wants_to_hire:
        score += HIRE_BONUS
    score += bid_adjustment(bids)
    if not buildable:
        score = min(score, NOT_BUILDABLE_CAP)
    return max(0, min(100, score))


def opportunity_label(score: int) -> str:
    """Plain-English verdict for the dashboard."""
    if score >= 70:
        return WORK_ON_THIS
    if score >= 45:
        return WORTH_A_LOOK
    return SKIP
