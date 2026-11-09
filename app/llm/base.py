"""The interface every LLM provider implements, and the errors it may raise."""

from dataclasses import dataclass
from typing import Protocol

from app.domain.qualification import Qualification


@dataclass(frozen=True)
class LeadInput:
    """The parts of a lead the model sees. All of it is untrusted visitor input."""

    message: str
    source: str
    name: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class QualificationResult:
    qualification: Qualification
    provider: str
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(Exception):
    """Base class. `code` is stored on the lead's review event."""

    code = "llm_error"


class LLMTransientError(LLMError):
    """Timeout, rate limit, overload or server error: worth retrying later."""

    code = "llm_unavailable"


class LLMInvalidOutputError(LLMError):
    """The model answered, but not with output that validates against the schema."""

    code = "llm_invalid_output"


class LLMRefusalError(LLMError):
    """The model declined to answer."""

    code = "llm_refused"


class LLMProvider(Protocol):
    name: str

    async def qualify(self, lead: LeadInput) -> QualificationResult:
        """Score one lead. Raises an LLMError subclass on failure."""
        ...
