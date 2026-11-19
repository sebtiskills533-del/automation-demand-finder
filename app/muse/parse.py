"""Read Muse's reply (the exact format from docs/muse-finder-procedure.md).

Success:
    POSTS START
    url,title,text,source,author,posted_at
    https://...,Title,"Text",Facebook,jane,2026-11-18
    POSTS END
Nothing found:  NO POSTS FOUND
Failure:        PROBLEM: <what happened>
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.errors import MuseReplyError
from app.importer import parse_import

_START = re.compile(r"^\W*POSTS\s+START\W*$", re.IGNORECASE)
_END = re.compile(r"^\W*POSTS\s+END\W*$", re.IGNORECASE)
_HEADER = re.compile(r"^\s*url\s*,\s*title\b", re.IGNORECASE)
_ROW = re.compile(r'^\s*"?https://', re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")
_NO_POSTS = re.compile(r"^\W*NO\s+POSTS\s+FOUND\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class MuseReply:
    rows: list[dict] = field(default_factory=list)
    note: str = ""


def clean_chat_text(text: str) -> str:
    """Remove invisible characters that chat pages put into copied text."""
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Co", "Cf"))


def parse_muse_reply(text: str, max_posts: int) -> MuseReply:
    """Rows to import, or MuseReplyError explaining why there are none."""
    lines = clean_chat_text(text).splitlines()

    problem = next((ln.strip() for ln in lines if ln.strip().upper().startswith("PROBLEM:")), None)
    # Inside the markers, still keep only the header and the rows: the chat page
    # can add a "csv" code-block label or a "Copy" button line.
    csv_lines = _after_header(_between_markers(lines)) or _after_header(lines)

    if not csv_lines:
        if problem:
            raise MuseReplyError(f"Muse reported a problem: {problem[len('PROBLEM:'):].strip()}")
        if any(_NO_POSTS.match(ln) for ln in lines):
            return MuseReply(rows=[], note="Muse found no matching posts this time.")
        raise MuseReplyError(
            "Muse's reply had no posts in the expected format. Open the chat and check "
            "that the side chat has the finder procedure."
        )

    try:
        rows = parse_import("\n".join(csv_lines))
    except ValueError as exc:
        raise MuseReplyError(f"Muse's post list could not be read: {exc}") from None
    note = f"Muse returned {len(rows)} posts; only the first {max_posts} were used." if len(rows) > max_posts else ""
    return MuseReply(rows=rows[:max_posts], note=note)


def _between_markers(lines: list[str]) -> list[str]:
    start = next((i for i, ln in enumerate(lines) if _START.match(ln)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if _END.match(lines[i])), len(lines))
    return lines[start + 1 : end]


def _after_header(lines: list[str]) -> list[str]:
    """Fallback when Muse forgot the markers: the header line plus the rows after it."""
    header = next((i for i, ln in enumerate(lines) if _HEADER.match(ln)), None)
    if header is None:
        return []
    rows = []
    for line in lines[header + 1 :]:
        if _FENCE.match(line) or not line.strip():
            continue
        if not _ROW.match(line):
            break  # sign-off text, timestamps, buttons...
        rows.append(line)
    return [lines[header], *rows] if rows else []
