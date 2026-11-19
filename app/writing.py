"""How drafts for strangers (public replies, report drafts, emails) are written.

Two styles the user can switch between (remembered in settings):
- "convincing" (default): the reader understands the problem and wants your help.
- "tip_first" (the earlier style): a useful tip first, then one short line offering help.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from app import store
from app.timeline import delivery_label

CONVINCING = "convincing"
TIP_FIRST = "tip_first"
DEFAULT_STYLE = CONVINCING
STYLE_LABELS = {CONVINCING: "✨ Clear & convincing", TIP_FIRST: "💡 Short tip first"}
STYLE_HELP = {
    CONVINCING: "Restates their problem, explains it simply, gives a tip, the benefit and a clear next step.",
    TIP_FIRST: "The earlier style: straight to a useful tip, then one short line offering help.",
}
_SETTING_KEY = "writing.style"

CLEAR_AND_CONVINCING = """How to write it (the reader is usually a busy, non-technical business owner):
1. Show you understood: in the first sentence, restate their problem in their own words, \
so they feel heard (e.g. "Copying every order into your sheet by hand eats your evenings").
2. Explain simply: say in plain words what is causing the problem and how it can be fixed. \
Short sentences, everyday words, no jargon. If you must name a tool (Apps Script, Zapier, Make, \
n8n, webhook), add a few words saying what it does ("Zapier, a tool that connects your apps").
3. Give one practical tip they can try or understand right now, so they trust you know the answer.
4. Make the benefit concrete: what changes for them (time saved each week, no missed orders, \
no copy-paste mistakes). Use their numbers if they gave any; never invent numbers or results.
5. End with one clear, friendly next step (for example offering to set it up, with the \
delivery time if one is given). Confident, warm and honest: no pressure, no hype, no fake \
promises, no prices.
Write like a helpful person, not a company: use "you" and "I", contractions, and no buzzwords \
("leverage", "streamline", "synergy", "cutting-edge")."""

_TIP_FIRST_REPLY = """How to write it:
- Start with a genuinely useful, specific tip that moves their problem forward: the exact node, \
setting, formula, trigger or steps. Be concrete; no generic advice.
- Then, optionally, ONE short line offering help (for example "Happy to help set this up if you get stuck.")."""

_TIP_FIRST_EMAIL = """How to write it:
- Refer to their actual problem, suggest a concrete first step, and propose a short call or a quick \
written plan. Be helpful, not salesy."""


def style_rules(style: str, kind: Literal["reply", "email"]) -> str:
    """The writing rules to put in a prompt for this style and kind of draft."""
    if style == TIP_FIRST:
        return _TIP_FIRST_EMAIL if kind == "email" else _TIP_FIRST_REPLY
    return CLEAR_AND_CONVINCING


def get_style(conn: sqlite3.Connection) -> str:
    saved = store.get_setting(conn, _SETTING_KEY)
    return saved if saved in STYLE_LABELS else DEFAULT_STYLE


def set_style(conn: sqlite3.Connection, style: str) -> None:
    if style not in STYLE_LABELS:
        raise ValueError(f"Unknown writing style {style!r}")
    store.set_setting(conn, _SETTING_KEY, style)


def delivery_hint(post: dict | None) -> str:
    """A line telling the writer how long a job would take, if the app knows."""
    if not post or post.get("delivery_min_days") is None:
        return ""
    label = delivery_label(post["delivery_min_days"], post["delivery_max_days"],
                           post["difficulty"], post["est_hours"])
    return f"\nRealistic delivery time you may mention: {label}."
