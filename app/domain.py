"""Shared types and allowed values, kept in one place so every module agrees."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Where a post came from. The keys are stored in the database.
PLATFORM_NAMES = {
    "reddit": "Reddit",
    "hackernews": "Hacker News",
    "forum": "the community forum",
    "stackexchange": "Stack Exchange",
    # Platforms that only arrive through the import feature:
    "facebook": "Facebook",
    "instagram": "Instagram",
    "quora": "Quora",
    "linkedin": "LinkedIn",
    "x": "X (Twitter)",
    "web": "the website",
    # Job boards and freelance marketplaces (app/sources/jobs.py):
    "freelancer": "Freelancer.com",
    "remoteok": "Remote OK",
    "remotive": "Remotive",
    "himalayas": "Himalayas",
    "weworkremotely": "We Work Remotely",
    "workingnomads": "Working Nomads",
}
JOB_BOARD_PLATFORMS = ("freelancer", "remoteok", "remotive", "himalayas", "weworkremotely", "workingnomads")

# Website address -> platform, used for imported posts.
_HOST_PLATFORMS = {
    "reddit.com": "reddit",
    "news.ycombinator.com": "hackernews",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "quora.com": "quora",
    "linkedin.com": "linkedin",
    "x.com": "x",
    "twitter.com": "x",
    "stackexchange.com": "stackexchange",
    "stackoverflow.com": "stackexchange",
    "superuser.com": "stackexchange",
}


def platform_for_host(host: str) -> str:
    """'www.facebook.com' -> 'facebook'; unknown sites -> 'web'."""
    host = host.lower().rstrip(".")
    for domain, platform in _HOST_PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return "web"

DIFFICULTIES = ("low", "medium", "high")

TOOLS = (
    "Google Sheets + Apps Script", "Zapier", "Make", "n8n", "Python",
    "Etsy", "SEO tools", "Google Business Profile", "Other",
)

STATUSES = ("New", "Ticked", "Building", "Done", "Sold", "Skipped")
DEFAULT_STATUS = "New"
TICKED_STATUS = "Ticked"

PATHS = ("Product", "Reply", "Custom job")

# Longest values we accept, so one huge post can't bloat the database.
MAX_TITLE_CHARS = 500
MAX_BODY_CHARS = 20_000
MAX_NOTES_CHARS = 2_000
MAX_REPLY_CHARS = 3_000

TICKED_STATUSES = ("Ticked", "Building", "Done", "Sold")  # "acted on": reports allowed
REPORT_VERDICTS = ("Do it", "Think first", "Skip")


@dataclass(frozen=True)
class Post:
    """One collected post, already cleaned of contact details."""

    id: str
    source: str
    platform: str
    url: str
    title: str
    body: str
    author_handle: str
    posted_at: datetime

    def __post_init__(self) -> None:
        if self.platform not in PLATFORM_NAMES:
            raise ValueError(f"Unknown platform {self.platform!r}")
        if not self.id or not self.url.startswith("https://"):
            raise ValueError("A post needs an id and an https URL")
        if self.posted_at.tzinfo is None:
            raise ValueError("posted_at must be timezone-aware")


@dataclass(frozen=True)
class AnalysisRecord:
    """Claude's verdict on a post plus the values the app works out from it."""

    post_id: str
    summary: str
    category: str
    difficulty: str
    tool: str
    est_hours: float
    buildable: bool
    product_potential: int
    seriousness: int
    seriousness_reason: str
    wants_to_hire: bool
    shares_own_website: bool
    region_hint: str
    reason: str
    delivery_min_days: int | None
    delivery_max_days: int | None
    contact_method: str
    opportunity_score: int
    model: str
    service: str = "automation"


def utc_now() -> datetime:
    """The current time in UTC (one helper so tests can rely on the format)."""
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Store times as ISO-8601 UTC text, which SQLite sorts correctly."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
