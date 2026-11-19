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

# MULTILINE so it also finds the marker inside a whole chat log (client.latest_reply_in).
POSTS_START_RE = re.compile(r"^\W*POSTS\s+START\W*$", re.IGNORECASE | re.MULTILINE)
_END = re.compile(r"^\W*POSTS\s+END\W*$", re.IGNORECASE)
_HEADER = re.compile(r"^\s*url\s*,\s*title\b", re.IGNORECASE)
_ROW = re.compile(r'^\s*"?https://', re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")
_NOISE = re.compile(r"^\s*(copy|csv|copied!?)\s*$", re.IGNORECASE)  # code-block chrome
_NO_POSTS = re.compile(r"^\W*NO\s+POSTS\s+FOUND\b\W*(?P<reason>.*)$", re.IGNORECASE)


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
    # Inside the markers everything belongs to the list, so a long row the chat
    # page wrapped onto two lines is joined back; outside them we stop at the
    # first non-row line (sign-offs, timestamps).
    csv_lines = _after_header(_between_markers(lines), join_wrapped=True) or _after_header(lines)

    if not csv_lines:
        if problem:
            raise MuseReplyError(f"Muse reported a problem: {problem[len('PROBLEM:'):].strip()}")
        no_posts = next((m for m in map(_NO_POSTS.match, lines) if m), None)
        if no_posts:
            reason = no_posts.group("reason").strip()
            note = "Muse found no matching posts this time."
            return MuseReply(rows=[], note=f"{note} Reason: {reason}" if reason else note)
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
    start = next((i for i, ln in enumerate(lines) if POSTS_START_RE.match(ln)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if _END.match(lines[i])), len(lines))
    return lines[start + 1 : end]


def reply_is_complete(text: str) -> bool:
    """True once Muse's reply shows its end signal: POSTS END, NO POSTS FOUND or PROBLEM:."""
    for line in clean_chat_text(text).splitlines():
        if _END.match(line) or _NO_POSTS.match(line) or line.strip().upper().startswith("PROBLEM:"):
            return True
    return False


def _after_header(lines: list[str], join_wrapped: bool = False) -> list[str]:
    """The header line plus the rows after it.

    join_wrapped=True (inside the markers): a line that doesn't start a new row
    is the wrapped end of the previous row and is joined back onto it.
    Otherwise the first non-row line ends the list (sign-off text, timestamps).
    """
    header = next((i for i, ln in enumerate(lines) if _HEADER.match(ln)), None)
    if header is None:
        return []
    rows: list[str] = []
    for line in lines[header + 1 :]:
        if _FENCE.match(line) or not line.strip() or _NOISE.match(line):
            continue
        if _ROW.match(line):
            rows.append(line)
        elif join_wrapped and rows:
            rows[-1] = f"{rows[-1]} {line.strip()}"
        else:
            break
    return [lines[header], *rows] if rows else []
