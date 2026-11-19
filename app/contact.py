"""Which contact method is allowed, following the PRD's contact rules.

Never suggests finding emails or phone numbers: only the platform itself,
or a business website's contact form the person shared publicly.
"""

from __future__ import annotations

from app.domain import PLATFORM_NAMES


# Tool-vendor forums often forbid advertising services in replies.
_FORUM_NOTE = " (check the forum's rules on offering paid help first)"


def contact_method(platform: str, wants_to_hire: bool, shares_own_website: bool) -> str:
    name = PLATFORM_NAMES.get(platform)
    if name is None:
        raise ValueError(f"Unknown platform {platform!r}")
    note = _FORUM_NOTE if platform == "forum" else ""

    if platform == "stackexchange":
        # No private messages, and answers that advertise services get deleted.
        return "Post a genuinely helpful answer on Stack Exchange (no self-promotion allowed there)"
    if platform == "web":
        return "Only if the page itself offers a public reply or contact form; otherwise skip"
    if wants_to_hire:
        if platform == "hackernews":
            # Hacker News has no private messages.
            return "Reply on Hacker News (it has no private messages)"
        return f"Direct message on {name} (they asked to hire someone){note}"
    if shares_own_website:
        return "One short, polite message via their website's contact form"
    if platform == "forum":
        return f"Public reply under the post on {name}: answer the question helpfully{note}"
    return f"Public reply under the post on {name}: a useful tip first, then one line offering help"
