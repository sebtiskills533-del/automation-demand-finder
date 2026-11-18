"""Which contact method is allowed, following the PRD's contact rules.

Never suggests finding emails or phone numbers: only the platform itself,
or a business website's contact form the person shared publicly.
"""

from __future__ import annotations

from app.domain import PLATFORM_NAMES


def contact_method(platform: str, wants_to_hire: bool, shares_own_website: bool) -> str:
    name = PLATFORM_NAMES.get(platform)
    if name is None:
        raise ValueError(f"Unknown platform {platform!r}")

    if wants_to_hire:
        if platform == "hackernews":
            # Hacker News has no private messages.
            return "Reply on Hacker News (it has no private messages)"
        return f"Direct message on {name} (they asked to hire someone)"
    if shares_own_website:
        return "One short, polite message via their website's contact form"
    return f"Public reply under the post on {name}: a useful tip first, then one line offering help"
