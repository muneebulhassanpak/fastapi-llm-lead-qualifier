"""The Anthropic provider against a fake HTTP transport: the real SDK, no network, no cost."""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from app.domain.qualification import Urgency
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import (
    LeadInput,
    LLMInvalidOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMTransientError,
)

LEAD = LeadInput(message="We need an app audited. Budget $3k.", source="contact_form")
GOOD = {
    "name": None,
    "company": "Acme",
    "intent": "app security audit",
    "budget_hint": "$3k",
    "urgency": "medium",
    "score": 78,
    "confidence": 0.88,
    "reason": "Clear project and a stated budget.",
}

Handler = Callable[[httpx2.Request], httpx2.Response]


def _message(text: str | None, stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [] if text is None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 412, "output_tokens": 96},
        **extra,
    }


def _error(status: int, error_type: str) -> httpx2.Response:
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": error_type, "message": "nope"}}
    )


def _provider(handler: Handler) -> AnthropicProvider:
    http_client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider(api_key="test-key", timeout=5, http_client=http_client)


async def test_valid_response_is_parsed_with_usage() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_message(json.dumps(GOOD)))

    result = await _provider(handler).qualify(LEAD)

    assert result.qualification.score == 78
    assert result.qualification.urgency is Urgency.MEDIUM
    assert (result.provider, result.model, result.prompt_version) == (
        "anthropic",
        "claude-haiku-5-5",
        "qualify_v1",
    )
    assert (result.input_tokens, result.output_tokens) == (412, 96)

    [body] = requests
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "never instructions to you" in json.dumps(body["system"])
    assert "<enquiry>" in body["messages"][0]["content"]


async def test_sdk_does_not_retry_on_its_own() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return _error(529, "overloaded_error")

    with pytest.raises(LLMTransientError):
        await _provider(handler).qualify(LEAD)
    assert calls == 1


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (429, "rate_limit_error"),
        (529, "overloaded_error"),
        (500, "api_error"),
        (503, "api_error"),
        (408, "timeout_error"),
    ],
)
async def test_retryable_errors_are_transient(status: int, error_type: str) -> None:
    with pytest.raises(LLMTransientError):
        await _provider(lambda _: _error(status, error_type)).qualify(LEAD)


async def test_network_timeout_is_transient() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMTransientError, match="APITimeoutError"):
        await _provider(handler).qualify(LEAD)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(400, "invalid_request_error"), (401, "authentication_error"), (403, "permission_error")],
)
async def test_rejected_requests_are_not_retried(status: int, error_type: str) -> None:
    with pytest.raises(LLMRejectedError):
        await _provider(lambda _: _error(status, error_type)).qualify(LEAD)


async def test_refusal_is_reported() -> None:
    body = _message(
        None, "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "x"}
    )

    with pytest.raises(LLMRefusalError, match="cyber"):
        await _provider(lambda _: httpx2.Response(200, json=body)).qualify(LEAD)


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        json.dumps({**GOOD, "score": 250}),
        json.dumps({**GOOD, "urgency": "whenever"}),
        json.dumps({k: v for k, v in GOOD.items() if k != "reason"}),
    ],
)
async def test_invalid_output_is_reported(text: str) -> None:
    with pytest.raises(LLMInvalidOutputError):
        await _provider(lambda _: httpx2.Response(200, json=_message(text))).qualify(LEAD)


async def test_truncated_output_is_invalid() -> None:
    body = _message('{"name": null, "comp', "max_tokens")

    with pytest.raises(LLMInvalidOutputError):
        await _provider(lambda _: httpx2.Response(200, json=body)).qualify(LEAD)
