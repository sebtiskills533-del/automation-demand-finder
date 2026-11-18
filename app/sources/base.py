"""What every post source looks like, plus helpers they share."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar

from app.domain import Post
from app.http_client import PoliteHttpClient
from app.privacy import strip_contact_details

log = logging.getLogger(__name__)
T = TypeVar("T")


class Source(Protocol):
    """A place posts come from. `name` is shown to the user, e.g. 'r/excel'."""

    name: str

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        """Return the newest posts. Raises SourceFetchError if the source is unreachable."""
        ...


def clean_text(text: str) -> str:
    """Remove contact details and surrounding whitespace before saving."""
    return strip_contact_details(text or "").strip()


def convert_entries(
    source_name: str, entries: Iterable[T], to_post: Callable[[T], Post | None]
) -> list[Post]:
    """Convert raw feed entries to Posts, skipping (and logging) any that are malformed,
    so one odd entry never loses the rest of the feed."""
    posts: list[Post] = []
    for entry in entries:
        try:
            post = to_post(entry)
        # OSError/OverflowError: absurd timestamps (Windows raises OSError for those).
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, OSError) as exc:
            log.warning("%s: skipped a malformed entry (%s: %s)", source_name, type(exc).__name__, exc)
            continue
        if post is not None:
            posts.append(post)
    return posts
