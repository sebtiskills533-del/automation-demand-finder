"""The one place the app talks to the Claude API.

Every call: uses structured output (Claude must answer in our exact JSON
shape), logs tokens and cost, and turns SDK errors into the app's own errors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

import anthropic
import pydantic

from app.errors import LLMAuthError, LLMConfigError, LLMOutputError, LLMTemporaryError
from app.models_registry import ModelInfo

log = logging.getLogger(__name__)

T = TypeVar("T", bound=pydantic.BaseModel)

# Called after every API response that used tokens: (task, model, in, out, cost).
UsageSink = Callable[[str, str, int, int, float], None]


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    data: T
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        usage_sink: UsageSink,
        *,
        client: object | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        # The SDK already retries rate limits, overloads and network errors.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )
        self._usage_sink = usage_sink

    def structured(
        self,
        *,
        task: str,
        model: ModelInfo,
        system: str,
        user_text: str,
        output_type: type[T],
        max_tokens: int = 4000,
        effort: str = "low",
    ) -> LLMResult[T]:
        """Ask Claude and get back a validated `output_type` instance."""
        try:
            response = self._client.messages.create(
                model=model.id,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_text}],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": _schema_for(output_type)},
                },
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            log.error("Claude rejected the API key: %s", exc)
            raise LLMAuthError(
                "Claude rejected the API key. Check ANTHROPIC_API_KEY in .env and "
                "that your Anthropic account has credit."
            ) from exc
        except anthropic.NotFoundError as exc:
            log.error("Model not found: %s", exc)
            raise LLMConfigError(
                f"Claude does not know the model {model.id!r}. Check config/models.json."
            ) from exc
        except anthropic.BadRequestError as exc:
            log.error("Bad request to Claude (%s): %s", model.id, exc)
            raise LLMConfigError(f"Claude refused the request as invalid: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMTemporaryError("Claude's rate limit was reached; try again later.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMTemporaryError("Could not reach Claude. Check your internet connection.") from exc
        except anthropic.APIStatusError as exc:
            log.error("Claude API error %s: %s", exc.status_code, exc)
            raise LLMTemporaryError(f"Claude had a server problem (HTTP {exc.status_code}).") from exc

        # Tokens were spent even if the answer turns out unusable: log first.
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        cost = model.cost_usd(input_tokens, output_tokens)
        self._usage_sink(task, model.id, input_tokens, output_tokens, cost)

        if response.stop_reason == "refusal":
            raise LLMOutputError("Claude declined to answer this one.")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError("Claude's answer was cut off (too long).")

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = output_type.model_validate_json(text)
        except pydantic.ValidationError as exc:
            log.warning("Invalid structured output from %s: %s", model.id, exc)
            raise LLMOutputError("Claude's answer did not match the expected format.") from exc

        return LLMResult(data, model.id, input_tokens, output_tokens, cost)


    def structured_with_retry(self, *, attempts: int = 2, **kwargs) -> LLMResult[T]:
        """`structured`, asking again (once by default) if the answer was unusable."""
        for attempt in range(1, attempts + 1):
            try:
                return self.structured(**kwargs)
            except LLMOutputError:
                if attempt == attempts:
                    raise
                log.info("Retrying %s after an unusable answer", kwargs.get("task"))
        raise AssertionError("unreachable")


def _schema_for(output_type: type[pydantic.BaseModel]) -> dict:
    """The JSON schema Claude must follow. Range limits the API can't enforce
    are moved into descriptions by the SDK and checked by pydantic afterwards."""
    return anthropic.transform_schema(output_type)
