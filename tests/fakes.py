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
        if isinstance(body, (dict, list)):
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


def fake_message(payload, *, stop_reason="end_turn", input_tokens=1000, output_tokens=200):
    """Shape of an anthropic Message, as far as the app reads it.

    `payload` is a dict (sent as JSON) or a raw string (to test bad output).
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def claude_status_error(cls, status: int):
    """A real anthropic SDK error (e.g. AuthenticationError) with an HTTP status."""
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


def claude_connection_error():
    import anthropic
    import httpx2

    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: messages.create returns queued results."""

    def __init__(self, *results):
        self.queue = deque(results)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queue:
            raise AssertionError("Unexpected Claude call")
        item = self.queue.popleft()
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(**kwargs)
        return item
