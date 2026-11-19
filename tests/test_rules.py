"""Scoring, delivery-time and contact-method rules."""

import pytest

from app.contact import contact_method
from app.scoring import SKIP, WORK_ON_THIS, WORTH_A_LOOK, opportunity_label, opportunity_score
from app.timeline import DeliveryWindow, delivery_label, delivery_window

# ---------------------------------------------------------------- scoring


def test_best_and_worst_scores():
    assert opportunity_score(5, 5, "low", True, False) == 100
    assert opportunity_score(1, 1, "high", True, False) == 0


def test_seriousness_weighs_most():
    serious = opportunity_score(5, 2, "medium", True, False)
    popular = opportunity_score(2, 5, "medium", True, False)
    assert serious > popular


def test_hire_bonus_and_cap():
    base = opportunity_score(3, 3, "medium", True, False)
    assert opportunity_score(3, 3, "medium", True, True) == base + 10
    assert opportunity_score(5, 5, "low", True, True) == 100  # capped at 100


def test_unbuildable_jobs_are_capped():
    assert opportunity_score(5, 5, "low", False, True) == 30


@pytest.mark.parametrize(
    "args",
    [(0, 3, "low", True, False), (3, 6, "low", True, False), (3, 3, "extreme", True, False)],
)
def test_score_rejects_bad_input(args):
    with pytest.raises(ValueError):
        opportunity_score(*args)


@pytest.mark.parametrize(
    ("score", "label"), [(100, WORK_ON_THIS), (70, WORK_ON_THIS), (69, WORTH_A_LOOK), (45, WORTH_A_LOOK), (44, SKIP), (0, SKIP)]
)
def test_labels(score, label):
    assert opportunity_label(score) == label


# --------------------------------------------------------------- timeline


@pytest.mark.parametrize(
    ("hours", "difficulty", "buildable", "expected"),
    [
        (2, "low", True, DeliveryWindow(1, 3)),
        (5, "low", True, DeliveryWindow(1, 3)),
        (5.5, "medium", True, DeliveryWindow(3, 7)),
        (10, "medium", True, DeliveryWindow(3, 7)),
        (10.5, "medium", True, None),
        (3, "high", True, None),
        (3, "low", False, None),
    ],
)
def test_delivery_window(hours, difficulty, buildable, expected):
    assert delivery_window(hours, difficulty, buildable) == expected


def test_delivery_window_rejects_negative_hours():
    with pytest.raises(ValueError):
        delivery_window(-1, "low", True)


def test_delivery_label():
    assert delivery_label(1, 3) == "1-3 days"
    assert delivery_label(None, None) == "Skip - too big"


# ---------------------------------------------------------------- contact


def test_contact_default_is_public_reply():
    assert contact_method("reddit", False, False).startswith("Public reply under the post on Reddit")


def test_contact_hiring_means_dm_on_same_platform():
    assert contact_method("reddit", True, False).startswith("Direct message on Reddit")
    # HN has no DMs, so never suggest one there.
    assert "no private messages" in contact_method("hackernews", True, False)


def test_contact_shared_website_means_contact_form():
    assert "contact form" in contact_method("reddit", False, True)


def test_contact_on_forums_warns_about_rules():
    assert "rules on offering paid help" in contact_method("forum", False, False)
    assert contact_method("forum", True, False).startswith("Direct message on the community forum")
    assert "rules" in contact_method("forum", True, False)


def test_contact_never_suggests_email_or_phone():
    for platform in ("reddit", "hackernews", "forum"):
        for hire in (True, False):
            for site in (True, False):
                text = contact_method(platform, hire, site).lower()
                assert "email" not in text and "phone" not in text


def test_contact_unknown_platform():
    with pytest.raises(ValueError):
        contact_method("myspace", False, False)
