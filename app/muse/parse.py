"""Read Muse's replies (the exact formats from docs/muse-finder-procedure.md).

Two kinds of answer share one layout:

    POSTS START                         REPLIES START
    url,title,text,source,author,...    url,their_name,their_reply,email,replied_at
    https://...                         https://...
    POSTS END                           REPLIES END

Nothing found:  NO POSTS FOUND: <why>   /  NO REPLIES FOUND: <why>
Failure:        PROBLEM: <what happened>
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from app.errors import MuseReplyError
from app.importer import parse_import

_ROW = re.compile(r'^\s*"?https://', re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")
_NOISE = re.compile(r"^\s*(copy|csv|copied!?)\s*$", re.IGNORECASE)  # code-block chrome
MAX_REPLY_ROWS = 100


@dataclass(frozen=True)
class MuseReply:
    rows: list[dict] = field(default_factory=list)
    note: str = ""


@dataclass(frozen=True)
class BlockFormat:
    """One kind of Muse answer."""

    what: str                        # "posts" / "replies", for messages
    start: re.Pattern[str]
    end: re.Pattern[str]
    header: re.Pattern[str]
    nothing: re.Pattern[str]         # "NO ... FOUND: <reason>"
    read_rows: Callable[[str], list[dict]]


def _marker(words: str) -> re.Pattern[str]:
    # MULTILINE so it also finds the marker inside a whole chat log (client.latest_reply_in).
    return re.compile(rf"^\W*{words}\W*$", re.IGNORECASE | re.MULTILINE)


def _read_reply_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in reader.fieldnames or []}
    if not {"url", "their_name", "their_reply"} <= headers:
        raise ValueError("the header line needs url,their_name,their_reply")
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    if len(rows) > MAX_REPLY_ROWS:
        raise ValueError(f"too many replies at once ({len(rows)})")
    return rows


POSTS = BlockFormat(
    what="posts",
    start=_marker(r"POSTS\s+START"),
    end=_marker(r"POSTS\s+END"),
    header=re.compile(r"^\s*url\s*,\s*title\b", re.IGNORECASE),
    nothing=re.compile(r"^\W*NO\s+POSTS\s+FOUND\b\W*(?P<reason>.*)$", re.IGNORECASE),
    read_rows=parse_import,
)
REPLIES = BlockFormat(
    what="replies",
    start=_marker(r"REPLIES\s+START"),
    end=_marker(r"REPLIES\s+END"),
    header=re.compile(r"^\s*url\s*,\s*their_name\b", re.IGNORECASE),
    nothing=re.compile(r"^\W*NO\s+REPLIES\s+FOUND\b\W*(?P<reason>.*)$", re.IGNORECASE),
    read_rows=_read_reply_rows,
)
POSTS_START_RE = POSTS.start  # used by the importer and the chat reader


def clean_chat_text(text: str) -> str:
    """Remove invisible characters that chat pages put into copied text."""
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Co", "Cf"))


def parse_block(text: str, fmt: BlockFormat, max_rows: int) -> MuseReply:
    """Rows from one kind of Muse answer, or MuseReplyError explaining why there are none."""
    lines = clean_chat_text(text).splitlines()
    problem = next((ln.strip() for ln in lines if ln.strip().upper().startswith("PROBLEM:")), None)
    # Inside the markers everything belongs to the list, so a long row the chat
    # page wrapped onto two lines is joined back; outside them we stop at the
    # first non-row line (sign-offs, timestamps).
    csv_lines = _after_header(_between(lines, fmt), fmt, join_wrapped=True) or _after_header(lines, fmt)

    if not csv_lines:
        if problem:
            raise MuseReplyError(f"Muse reported a problem: {problem[len('PROBLEM:'):].strip()}")
        nothing = next((m for m in map(fmt.nothing.match, lines) if m), None)
        if nothing:
            reason = nothing.group("reason").strip()
            note = f"Muse found no matching {fmt.what} this time."
            return MuseReply(rows=[], note=f"{note} Reason: {reason}" if reason else note)
        raise MuseReplyError(
            f"Muse's reply had no {fmt.what} in the expected format. Open the chat and check "
            "that the side chat has the finder procedure."
        )
    try:
        rows = fmt.read_rows("\n".join(csv_lines))
    except ValueError as exc:
        raise MuseReplyError(f"Muse's {fmt.what} list could not be read: {exc}") from None
    note = (f"Muse returned {len(rows)} {fmt.what}; only the first {max_rows} were used."
            if len(rows) > max_rows else "")
    return MuseReply(rows=rows[:max_rows], note=note)


def is_complete(text: str, fmt: BlockFormat) -> bool:
    """True once the answer shows its end signal: the END marker, NO ... FOUND, or PROBLEM:."""
    for line in clean_chat_text(text).splitlines():
        if fmt.end.match(line) or fmt.nothing.match(line) or line.strip().upper().startswith("PROBLEM:"):
            return True
    return False


# The post finder (kept as named functions: used across the app and tests).
def parse_muse_reply(text: str, max_posts: int) -> MuseReply:
    return parse_block(text, POSTS, max_posts)


def reply_is_complete(text: str) -> bool:
    return is_complete(text, POSTS)


def _between(lines: list[str], fmt: BlockFormat) -> list[str]:
    start = next((i for i, ln in enumerate(lines) if fmt.start.match(ln)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if fmt.end.match(lines[i])), len(lines))
    return lines[start + 1 : end]


def _after_header(lines: list[str], fmt: BlockFormat, join_wrapped: bool = False) -> list[str]:
    """The header line plus the rows after it.

    join_wrapped=True (inside the markers): a line that doesn't start a new row
    is the wrapped end of the previous row and is joined back onto it.
    Otherwise the first non-row line ends the list (sign-off text, timestamps).
    """
    header = next((i for i, ln in enumerate(lines) if fmt.header.match(ln)), None)
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
