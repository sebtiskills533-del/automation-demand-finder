"""Turn the HTML found in RSS feeds into plain text."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# Tags that start a new line when converted to text.
_BLOCK_TAGS = {"p", "br", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "pre", "table"}
# Tags whose content is never shown to readers.
_SKIP_TAGS = {"script", "style"}

# Reddit wraps the post's own text between these comments; everything after
# (the "submitted by /u/... [link] [comments]" footer) is boilerplate.
_REDDIT_BODY_RE = re.compile(r"<!--\s*SC_OFF\s*-->(.*?)<!--\s*SC_ON\s*-->", re.DOTALL)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Return readable plain text: no tags, entities decoded, tidy whitespace."""
    if not html:
        return ""
    reddit_body = _REDDIT_BODY_RE.search(html)
    if reddit_body:
        html = reddit_body.group(1)

    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = unescape("".join(parser.parts))

    lines = [" ".join(line.split()) for line in text.splitlines()]
    # Collapse runs of blank lines into a single blank line.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
