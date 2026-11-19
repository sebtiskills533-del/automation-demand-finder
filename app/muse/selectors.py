"""muse.ai page layout (as of 2026-11-12). When muse.ai changes its page,
these are the first thing to fix, and the only place to fix them."""

import re

BASE_URL = "https://muse.ai"
COMPOSER_LABEL = "Message"          # role=textbox
LOGIN_BUTTON = "Log in"             # role=button, exact; only shown when logged out
CHATS_BUTTON = re.compile(r"^Chats")  # role=button; only present when the chat list is collapsed
MAIN_CHAT = "Main chat"             # role=button
MESSAGE_LOG = '[role="log"][aria-label="Chat messages"]'


def chat_button_name(chat_name: str) -> re.Pattern[str]:
    """A side chat's button name. muse.ai appends ' Unread updates' when the chat
    has new messages (seen 2026-11-19), so match the name with or without it."""
    return re.compile(rf"^{re.escape(chat_name)}(\s+Unread updates)?$")
