"""What every post source looks like, plus helpers they share."""

from __future__ import annotations

from typing import Protocol

from app.domain import Post
from app.http_client import PoliteHttpClient
from app.privacy import strip_contact_details


class Source(Protocol):
    """A place posts come from. `name` is shown to the user, e.g. 'r/excel'."""

    name: str

    def fetch(self, http: PoliteHttpClient) -> list[Post]:
        """Return the newest posts. Raises SourceFetchError if the source is unreachable."""
        ...


def clean_text(text: str) -> str:
    """Remove contact details and surrounding whitespace before saving."""
    return strip_contact_details(text or "").strip()
