"""muse.ai page layout (as of 2026-11-12). When muse.ai changes its page,
these are the first thing to fix, and the only place to fix them."""

import re

BASE_URL = "https://muse.ai"
COMPOSER_LABEL = "Message"          # role=textbox
LOGIN_BUTTON = "Log in"             # role=button, exact; only shown when logged out
CHATS_BUTTON = re.compile(r"^Chats")  # role=button, first match
MAIN_CHAT = "Main chat"             # role=button, exact
MESSAGE_LOG = '[role="log"][aria-label="Chat messages"]'
