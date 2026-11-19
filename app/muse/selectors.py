"""muse.ai page layout (as of 2026-11-12). When muse.ai changes its page,
these are the first thing to fix, and the only place to fix them."""

import re

BASE_URL = "https://muse.ai"

# The "Muse from Meta" Chrome extension. Only messages sent from its panel may use
# the device browser (Muse said so on 2026-11-19 when we typed on the muse.ai tab).
# The panel embeds muse.ai (?surface=chrome_side_panel) in an iframe, so every
# selector below works inside that frame too.
DEFAULT_EXTENSION_ID = "jnakhhjmpmoohbjgfccbccfoifnmbdjd"
EXTENSION_PANEL_PATH = "panel/index.html"


def extension_panel_url(extension_id: str) -> str:
    return f"chrome-extension://{extension_id}/{EXTENSION_PANEL_PATH}"
COMPOSER_LABEL = "Message"          # role=textbox
LOGIN_BUTTON = "Log in"             # role=button, exact; only shown when logged out
CHATS_BUTTON = re.compile(r"^Chats")  # role=button; only present when the chat list is collapsed
MAIN_CHAT = "Main chat"             # role=button
MESSAGE_LOG = '[role="log"][aria-label="Chat messages"]'


def chat_button_name(chat_name: str) -> re.Pattern[str]:
    """A side chat's button name. muse.ai appends ' Unread updates' when the chat
    has new messages (seen 2026-11-19), so match the name with or without it."""
    return re.compile(rf"^{re.escape(chat_name)}(\s+Unread updates)?$")
