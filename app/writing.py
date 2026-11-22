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
QUICK_TIP = "quick_tip"
PROPOSAL = "proposal"
DEFAULT_STYLE = CONVINCING
STYLE_LABELS = {
    QUICK_TIP: "🛠️ Quick tip (detailed how-to)",
    TIP_FIRST: "💡 Short tip first",
    CONVINCING: "✨ Clear & convincing",
    PROPOSAL: "📝 Proposal (for gigs)",
}
STYLE_HELP = {
    QUICK_TIP: "The first style: 'Quick tip:' with exact steps, tools and numbers, then "
               "'I build small automations like this' and an offer to share how you'd set it up.",
    TIP_FIRST: "Straight to a useful tip, then one short line offering help.",
    CONVINCING: "Restates their problem, explains it simply, gives a tip, the benefit and a clear next step.",
    PROPOSAL: "For Freelancer.com bids and job applications: their goal first, a 3-step plan, the delivery "
              "time, a spot for your portfolio link and one question.",
}
# Public replies and emails use these; gigs and job listings use JOB_STYLES.
REPLY_STYLES = (QUICK_TIP, TIP_FIRST, CONVINCING)
JOB_STYLES = (PROPOSAL, CONVINCING)
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


_QUICK_TIP_REPLY = """How to write it (one practical paragraph, about 90-130 words):
- Start with "Quick tip:" and give a concrete, step-by-step how-to in the order they should do it: \
the exact tools, settings and numbers (e.g. which checker to use, which Google Sheet columns to \
add, which Make or n8n trigger pulls the rows, when the sequence should stop).
- Then end with the sentence "I build small automations like this." and ONE short offer, for \
example "Feel free to DM me and I can share how I'd set it up for your case." (On Stack Exchange \
leave out both of these sentences.)"""

_QUICK_TIP_EMAIL = """How to write it:
- After the greeting, write "Here's a quick plan:" and give the exact steps in order, with the \
tools, settings and numbers that fit their case.
- Then one sentence saying you build small automations like this, and offer to set it up or walk \
them through how you'd do it for their case."""


_PROPOSAL = """How to write it (a bid on Freelancer.com or a short application; the client reads only the \
first two lines in a list of 50-300 bids, so those lines decide everything):
1. First sentence: their goal in their own words and that you can deliver it (e.g. "You want every \
new Squarespace signup added to Brevo automatically, and I can set that up for you"). Never start with \
"Hi, I am..." or "I have read your project".
2. A short plan in 3 numbered steps written as plain sentences on separate lines (1. ... 2. ... 3. ...), \
specific to their tools; this replaces the tip.
3. The delivery time you are given, and that you will test it with them before handover.
4. One line of proof with a placeholder the freelancer fills in: "Similar work: [link to my project]".
5. End with ONE specific question about their setup that shows you understood (e.g. "Are your \
signups coming from a form block or from checkout?").
No price (it goes in the bid amount), no buzzwords, no fake experience or reviews."""


def styles_for(platform: str) -> tuple[str, ...]:
    """Which styles to offer for a post: proposals for gigs and job listings, replies elsewhere."""
    from app.domain import JOB_BOARD_PLATFORMS

    return JOB_STYLES if platform in JOB_BOARD_PLATFORMS else REPLY_STYLES


def style_rules(style: str, kind: Literal["reply", "email"]) -> str:
    """The writing rules to put in a prompt for this style and kind of draft."""
    if style == PROPOSAL and kind == "reply":
        return _PROPOSAL
    if style == TIP_FIRST:
        return _TIP_FIRST_EMAIL if kind == "email" else _TIP_FIRST_REPLY
    if style == QUICK_TIP:
        return _QUICK_TIP_EMAIL if kind == "email" else _QUICK_TIP_REPLY
    return CLEAR_AND_CONVINCING


def reply_format_rules(style: str, max_words: int) -> str:
    """Length / closing rules for replies. The quick-tip style keeps its own one-paragraph
    shape and its 'DM me' offer, exactly as the first drafts were written."""
    if style == PROPOSAL:
        return (f"- 90-{max_words} words (never more), plain text, no headings or markdown; the 3 plan steps on "
                "their own lines.\n- The placeholder in square brackets is the only allowed 'link'.")
    if style == QUICK_TIP:
        return ("- One paragraph, plain text, no more than 130 words. No headings, no markdown, no lists.\n"
                "- Invite them to DM you only if the contact method is a direct message; otherwise make "
                "the offer \"Happy to share how I'd set it up for your case.\"")
    return (f"- Aim for 80-{max_words} words (never more), plain text, 2-4 short paragraphs. No headings, no "
            "markdown, no bullet lists.\n"
            "- If the contact method is a direct message, this text IS that message: never ask them to "
            "message you; end with one simple question that is easy to answer (e.g. \"Which email tool "
            "do you use now?\").")


def get_style(conn: sqlite3.Connection, platform: str | None = None) -> str:
    """The preferred style; for gigs and job listings always the proposal."""
    if platform is not None and styles_for(platform) == JOB_STYLES:
        return PROPOSAL
    saved = store.get_setting(conn, _SETTING_KEY)
    return saved if saved in REPLY_STYLES else DEFAULT_STYLE


def set_style(conn: sqlite3.Connection, style: str) -> None:
    if style not in REPLY_STYLES:
        raise ValueError(f"Unknown writing style {style!r}")
    store.set_setting(conn, _SETTING_KEY, style)


def delivery_hint(post: dict | None) -> str:
    """A line telling the writer how long a job would take, if the app knows."""
    if not post or post.get("delivery_min_days") is None:
        return ""
    label = delivery_label(post["delivery_min_days"], post["delivery_max_days"],
                           post["difficulty"], post["est_hours"])
    return f"\nRealistic delivery time you may mention: {label}."
