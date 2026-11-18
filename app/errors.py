"""Exception types used across the app.

Each one carries a message written for the user (plain English); the full
technical detail goes to the log file.
"""


class AppError(Exception):
    """Base class for every error the app raises on purpose."""


class ConfigError(AppError):
    """A setting in .env or a file in config/ is missing or invalid."""


class SourceFetchError(AppError):
    """A post source (Reddit, Hacker News) could not be read."""


class BudgetExceededError(AppError):
    """This month's AI spending limit has been reached."""


class LLMError(AppError):
    """Base class for problems talking to the Claude API."""


class LLMAuthError(LLMError):
    """The API key is missing, wrong, or lacks permission. Stops the run."""


class LLMConfigError(LLMError):
    """The request itself is wrong (for example an unknown model). Stops the run."""


class LLMTemporaryError(LLMError):
    """Network trouble, rate limit or server error. Safe to try again later."""


class LLMOutputError(LLMError):
    """Claude answered, but the answer was unusable (refused, cut off, or invalid)."""
