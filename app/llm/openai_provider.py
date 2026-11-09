"""OpenAI models, through the official SDK, with structured outputs. Same contract as Claude."""

import openai
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

MAX_COMPLETION_TOKENS = 4096
_TRANSIENT_STATUS = {408, 409, 429}


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 30.0,
        http_client: openai.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.model = model
        # Retries belong to the job queue, as with the Anthropic provider.
        self._client = openai.AsyncOpenAI(
            api_key=api_key, timeout=timeout, max_retries=0, http_client=http_client
        )

    async def qualify(self, lead: LeadInput) -> QualificationResult:
        try:
            completion = await self._client.chat.completions.parse(
                model=self.model,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": render_enquiry(lead)},
                ],
                response_format=Qualification,
            )
        except (openai.RateLimitError, openai.InternalServerError) as exc:
            raise LLMTransientError(f"{type(exc).__name__}: {exc.message}") from exc
        except openai.APIConnectionError as exc:  # includes APITimeoutError
            raise LLMTransientError(f"{type(exc).__name__}: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code in _TRANSIENT_STATUS or exc.status_code >= 500:
                raise LLMTransientError(f"HTTP {exc.status_code}: {exc.message}") from exc
            raise LLMRejectedError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except openai.ContentFilterFinishReasonError as exc:
            raise LLMRefusalError("Output blocked by the content filter.") from exc
        except (openai.LengthFinishReasonError, ValidationError, ValueError) as exc:
            raise LLMInvalidOutputError(f"Output failed validation: {exc}") from exc

        message = completion.choices[0].message
        if message.refusal:
            raise LLMRefusalError(f"Model declined: {message.refusal[:200]}")
        if message.parsed is None:
            raise LLMInvalidOutputError("No parsed output.")

        usage = completion.usage
        return QualificationResult(
            qualification=message.parsed,
            provider=self.name,
            model=completion.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
