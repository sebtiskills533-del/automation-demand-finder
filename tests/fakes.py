"""Test doubles for the network and the Claude API."""

from __future__ import annotations

import json
from collections import deque
from types import SimpleNamespace

import requests


class FakeResponse:
    def __init__(self, status_code: int = 200, body: str | bytes | dict = b"", headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        if isinstance(body, dict):
            body = json.dumps(body)
        self.content = body.encode() if isinstance(body, str) else body

    def json(self):
        return json.loads(self.content)


class FakeSession:
    """Returns queued responses (or raises queued exceptions) in order."""

    def __init__(self, *responses):
        self.queue = deque(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if not self.queue:
            raise AssertionError(f"Unexpected request to {url}")
        item = self.queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def connection_error() -> requests.ConnectionError:
    return requests.ConnectionError("no internet")


class FakeClock:
    """A clock that only moves when `sleep` is called."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSource:
    def __init__(self, name, posts=None, error=None):
        self.name = name
        self._posts = posts or []
        self._error = error

    def fetch(self, http):
        if self._error:
            raise self._error
        return list(self._posts)


def fake_message(parsed, *, stop_reason="end_turn", input_tokens=1000, output_tokens=200):
    """Shape of an anthropic ParsedMessage, as far as the app reads it."""
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        _request_id="req_test",
    )


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: messages.parse returns queued results."""

    def __init__(self, *results):
        self.queue = deque(results)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queue:
            raise AssertionError("Unexpected Claude call")
        item = self.queue.popleft()
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(**kwargs)
        return item
