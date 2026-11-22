"""Small, reusable dashboard building blocks so every page looks the same."""

from __future__ import annotations

import re

import streamlit as st

from app.scoring import SKIP, WORK_ON_THIS, WORTH_A_LOOK, opportunity_label

# Verdict -> (badge colour from the theme, icon, emoji for tables)
VERDICT_STYLE = {
    WORK_ON_THIS: ("green", ":material/rocket_launch:", "🟢"),
    WORTH_A_LOOK: ("orange", ":material/visibility:", "🟠"),
    SKIP: ("gray", ":material/do_not_disturb_on:", "⚪"),
}
STATUS_ICONS = {
    "New": "🆕", "Ticked": "✅", "Building": "🛠️", "Done": "🏁", "Sold": "💰", "Skipped": "🚫",
}
REPORT_STYLE = {"Do it": ("green", "✅"), "Think first": ("orange", "🤔"), "Skip": ("gray", "⛔")}

_PAGE_CSS = """
<style>
  .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }
  [data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #D5E0E4; border-radius: 0.6rem;
    padding: 0.8rem 1rem; box-shadow: 0 1px 2px rgba(15, 61, 58, 0.06);
  }
  [data-testid="stMetricLabel"] p { font-size: 0.85rem; color: #4B5B63; }
  [data-testid="stVerticalBlockBorderWrapper"] { background: #FFFFFF; }
  h1 { font-size: 1.9rem !important; }
  h2, h3 { color: #0F3D3A; }
</style>
"""
# Characters that Markdown would interpret; escaped so stranger-written text shows literally.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def apply_page_style() -> None:
    """Our own fixed CSS (never user content), for a consistent, card-like look."""
    st.markdown(_PAGE_CSS, unsafe_allow_html=True)


def md_escape(text: str) -> str:
    """Make untrusted text safe to show in Markdown: no links, images or formatting."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text or "").split()))


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def verdict_badge(score: int) -> None:
    label = opportunity_label(score)
    color, icon, _ = VERDICT_STYLE[label]
    st.badge(f"{label} · {score}", icon=icon, color=color)


def verdict_text(score: int) -> str:
    """Verdict with a coloured dot, for tables (which can't show badges)."""
    label = opportunity_label(score)
    return f"{VERDICT_STYLE[label][2]} {label}"


def status_text(status: str) -> str:
    return f"{STATUS_ICONS.get(status, '')} {status}".strip()


def empty_state(message: str, hint: str = "") -> None:
    with st.container(border=True):
        st.markdown(f"#### {message}")
        if hint:
            st.caption(hint)


def writing_style_picker(conn, place: str) -> str:
    """Switch between the writing styles; the choice is saved and shared by every picker."""
    from app.writing import REPLY_STYLES, STYLE_HELP, STYLE_LABELS, get_style, set_style

    current = get_style(conn)
    # The current style is part of the key, so every picker re-shows the saved choice.
    # (Gigs and job listings always use the proposal style, so it isn't offered here.)
    choice = st.segmented_control(
        "Writing style", list(REPLY_STYLES), default=current, format_func=STYLE_LABELS.get,
        key=f"style_{place}_{current}", help=" · ".join(f"{STYLE_LABELS[s]}: {STYLE_HELP[s]}" for s in REPLY_STYLES),
    )
    if choice and choice != current:
        set_style(conn, choice)
        st.toast(f"Writing style: {STYLE_LABELS[choice]}")
        return choice
    return current


def labelled_text(label: str, text: str) -> None:
    """A small label with plain (never formatted) text under it."""
    st.caption(label)
    st.text(text or "-")
