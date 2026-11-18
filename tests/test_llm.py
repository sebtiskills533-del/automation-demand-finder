import anthropic
import pytest
from pydantic import BaseModel

from app.errors import LLMAuthError, LLMConfigError, LLMOutputError, LLMTemporaryError
from app.llm import ClaudeClient
from app.models_registry import ModelInfo
from tests.fakes import FakeAnthropic, claude_connection_error, claude_status_error, fake_message

MODEL = ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", 1, "Basic", 0.10, 0.50, "")


class Answer(BaseModel):
    value: int


def make_client(*results):
    usage = []
    fake = FakeAnthropic(*results)
    client = ClaudeClient("key", lambda *args: usage.append(args), client=fake)
    return client, fake, usage


def ask(client):
    return client.structured(task="analyze", model=MODEL, system="sys", user_text="hi", output_type=Answer)


api_error = claude_status_error


def test_success_returns_validated_data_and_logs_usage():
    client, fake, usage = make_client(fake_message({"value": 7}, input_tokens=2000, output_tokens=100))
    result = ask(client)
    assert result.data == Answer(value=7)
    assert result.cost_usd == pytest.approx((2000 * 0.10 + 100 * 0.50) / 1_000_000)
    assert usage == [("analyze", "claude-haiku-5-5", 2000, 100, result.cost_usd)]

    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "value" in call["output_config"]["format"]["schema"]["properties"]


@pytest.mark.parametrize(
    ("message", "match"),
    [
        (fake_message("not json"), "expected format"),
        (fake_message({"value": "seven"}), "expected format"),
        (fake_message({"value": 1}, stop_reason="refusal"), "declined"),
        (fake_message({"value": 1}, stop_reason="max_tokens"), "cut off"),
    ],
)
def test_unusable_answers_raise_but_still_log_usage(message, match):
    client, _, usage = make_client(message)
    with pytest.raises(LLMOutputError, match=match):
        ask(client)
    assert len(usage) == 1  # tokens were spent, so they are counted


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(anthropic.AuthenticationError, 401), LLMAuthError),
        (api_error(anthropic.PermissionDeniedError, 403), LLMAuthError),
        (api_error(anthropic.NotFoundError, 404), LLMConfigError),
        (api_error(anthropic.BadRequestError, 400), LLMConfigError),
        (api_error(anthropic.RateLimitError, 429), LLMTemporaryError),
        (api_error(anthropic.InternalServerError, 500), LLMTemporaryError),
        (claude_connection_error(), LLMTemporaryError),
    ],
)
def test_sdk_errors_become_app_errors(error, expected):
    client, _, usage = make_client(error)
    with pytest.raises(expected):
        ask(client)
    assert usage == []


def test_auth_error_message_is_helpful():
    client, _, _ = make_client(api_error(anthropic.AuthenticationError, 401))
    with pytest.raises(LLMAuthError, match="ANTHROPIC_API_KEY"):
        ask(client)
