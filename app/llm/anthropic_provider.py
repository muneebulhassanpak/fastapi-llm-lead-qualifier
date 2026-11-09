"""Claude, through the official Anthropic SDK, with structured outputs."""

import anthropic
from pydantic import ValidationError

from app.domain.qualification import Qualification
from app.llm.base import (
    LeadInput,
    LLMInvalidOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMTransientError,
    QualificationResult,
)
from app.llm.prompt import PROMPT_VERSION, SYSTEM_PROMPT, render_enquiry

DEFAULT_MODEL = "claude-haiku-5-5"
# Thinking is on by default on this model; qualification is a short classification task.
MAX_TOKENS = 4096
_TRANSIENT_STATUS = {408, 409, 429}


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = 30.0,
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.model = model
        # The job queue retries with backoff and survives restarts, so the SDK must not also
        # retry: that would multiply waits and hold a worker for minutes.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=0, http_client=http_client
        )

    async def qualify(self, lead: LeadInput) -> QualificationResult:
        try:
            response = await self._client.messages.parse(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": render_enquiry(lead)}],
                output_format=Qualification,
                output_config={"effort": "low"},
            )
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise LLMTransientError(f"{type(exc).__name__}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise LLMTransientError(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code in _TRANSIENT_STATUS or exc.status_code >= 500:
                raise LLMTransientError(f"HTTP {exc.status_code}: {exc.message}") from exc
            raise LLMRejectedError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except (ValidationError, ValueError) as exc:
            raise LLMInvalidOutputError(f"Output failed validation: {exc}") from exc

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMRefusalError(f"Model declined (category: {category}).")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMInvalidOutputError(f"No valid output (stop_reason: {response.stop_reason}).")

        return QualificationResult(
            qualification=response.parsed_output,
            provider=self.name,
            model=response.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
