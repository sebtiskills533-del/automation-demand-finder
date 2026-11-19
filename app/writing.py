"""How every draft for a stranger (public reply, report draft, email) is written:
clear enough for a non-technical reader, and convincing without being pushy.
One place, so all drafts sound the same."""

from __future__ import annotations

from app.timeline import delivery_label

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


def delivery_hint(post: dict | None) -> str:
    """A line telling the writer how long a job would take, if the app knows."""
    if not post or post.get("delivery_min_days") is None:
        return ""
    label = delivery_label(post["delivery_min_days"], post["delivery_max_days"],
                           post["difficulty"], post["est_hours"])
    return f"\nRealistic delivery time you may mention: {label}."
