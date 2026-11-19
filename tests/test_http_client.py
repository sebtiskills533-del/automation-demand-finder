import pytest

from app.errors import SourceFetchError
from app.http_client import PoliteHttpClient, build_user_agent
from tests.fakes import FakeClock, FakeResponse, FakeSession, connection_error


def make_client(session, clock=None, **kwargs):
    clock = clock or FakeClock()
    return PoliteHttpClient(
        "test-agent", session=session, sleep=clock.sleep, clock=clock.time, **kwargs
    ), clock


def test_user_agent_is_set_and_formatted():
    session = FakeSession()
    make_client(session)
    assert session.headers["User-Agent"] == "test-agent"
    assert build_user_agent("shop_owner_1").endswith("(by /u/shop_owner_1)")
    assert build_user_agent().startswith("python:automation-demand-finder:")


def test_waits_at_least_two_seconds_between_requests():
    session = FakeSession(FakeResponse(), FakeResponse())
    client, clock = make_client(session)
    client.get("https://example.com/a")
    client.get("https://example.com/b")
    assert clock.sleeps == [2.0]


def test_retries_temporary_errors_then_succeeds():
    session = FakeSession(FakeResponse(503), connection_error(), FakeResponse(200, b"ok"))
    client, clock = make_client(session)
    assert client.get("https://example.com").content == b"ok"
    assert len(session.calls) == 3


def test_honours_retry_after_but_caps_it():
    session = FakeSession(FakeResponse(429, headers={"Retry-After": "600"}), FakeResponse())
    client, clock = make_client(session)
    client.get("https://example.com")
    assert 60 in clock.sleeps


def test_gives_up_after_max_retries():
    session = FakeSession(FakeResponse(500), FakeResponse(500), FakeResponse(500))
    client, _ = make_client(session)
    with pytest.raises(SourceFetchError, match="HTTP 500"):
        client.get("https://example.com")


def test_no_internet_gives_clear_error():
    session = FakeSession(connection_error(), connection_error(), connection_error())
    client, _ = make_client(session)
    with pytest.raises(SourceFetchError, match="network error"):
        client.get("https://example.com")


def test_client_errors_are_not_retried():
    session = FakeSession(FakeResponse(403))
    client, _ = make_client(session)
    with pytest.raises(SourceFetchError, match="403"):
        client.get("https://example.com")
    assert len(session.calls) == 1


def test_refuses_plain_http():
    client, _ = make_client(FakeSession())
    with pytest.raises(SourceFetchError, match="non-HTTPS"):
        client.get("http://example.com")
