"""Remove email addresses and phone numbers from text before it is stored.

The app must never save personal contact details, even when a post contains
them. Web links are kept, but emails inside links are still removed.
"""

from __future__ import annotations

import re

EMAIL_PLACEHOLDER = "[email removed]"
PHONE_PLACEHOLDER = "[phone removed]"

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Obfuscated forms such as "jane (at) gmail (dot) com" or "jane at gmail dot com".
_OBFUSCATED_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+\s*(?:\(at\)|\[at\]|\{at\}|\s+at\s+)\s*[A-Za-z0-9-]+"
    r"(?:\s*(?:\(dot\)|\[dot\]|\{dot\}|\s+dot\s+|\.)\s*[A-Za-z0-9-]+)*"
    r"\s*(?:\(dot\)|\[dot\]|\{dot\}|\s+dot\s+)\s*[A-Za-z]{2,}",
    re.IGNORECASE,
)

# A run of digits with optional +, brackets, spaces, dots or dashes between
# them. Whether it is really a phone number is decided in `_looks_like_phone`.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w/])\+?\(?\d[\d\s().-]{5,}\d(?![\w/])")


def strip_contact_details(text: str) -> str:
    """Return `text` with emails and phone numbers replaced by placeholders."""
    if not text:
        return ""
    text = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    text = _OBFUSCATED_EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)

    # Strip phones only outside URLs, so links with long ids keep working.
    pieces: list[str] = []
    last = 0
    for match in _URL_RE.finditer(text):
        pieces.append(_strip_phones(text[last : match.start()]))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(_strip_phones(text[last:]))
    return "".join(pieces)


def _strip_phones(segment: str) -> str:
    return _PHONE_CANDIDATE_RE.sub(
        lambda m: PHONE_PLACEHOLDER if _looks_like_phone(m.group(0)) else m.group(0), segment
    )


def _looks_like_phone(candidate: str) -> bool:
    digits = sum(ch.isdigit() for ch in candidate)
    has_marker = candidate.startswith("+") or "(" in candidate
    # 10-15 digits is a full phone number in almost every country. With a "+"
    # or brackets we also accept 8-9 digit numbers. Shorter runs (years, prices,
    # "2020-2024") are left alone.
    return 10 <= digits <= 15 or (has_marker and 8 <= digits <= 15)
