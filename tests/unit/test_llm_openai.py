"""The OpenAI provider against a fake HTTP transport: the real SDK, no network, no cost."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest

from app.llm.base import (
    LeadInput,
    LLMInvalidOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMTransientError,
)
from app.llm.openai_provider import OpenAIProvider

LEAD = LeadInput(message="We need an app audited. Budget $3k.", source="contact_form")
GOOD = {
    "name": None,
    "company": None,
    "intent": "app security audit",
    "budget_hint": "$3k",
    "urgency": "unknown",
    "score": 70,
    "confidence": 0.8,
    "reason": "Clear project and budget.",
}
MODEL = "test-model"

Handler = Callable[[httpx2.Request], httpx2.Response]


def _completion(
    content: str | None, finish_reason: str = "stop", refusal: str | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 300, "completion_tokens": 80, "total_tokens": 380},
    }


def _error(status: int) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"message": "nope", "type": "x", "code": None}})


def _provider(handler: Handler) -> OpenAIProvider:
    http_client = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return OpenAIProvider(api_key="test-key", model=MODEL, timeout=5, http_client=http_client)


async def test_valid_response_is_parsed_with_usage() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion(json.dumps(GOOD)))

    result = await _provider(handler).qualify(LEAD)

    assert result.qualification.score == 70
    assert (result.provider, result.model) == ("openai", MODEL)
    assert (result.input_tokens, result.output_tokens) == (300, 80)
    [body] = requests
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "system"
    assert "<enquiry>" in body["messages"][1]["content"]


@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_retryable_errors_are_transient(status: int) -> None:
    with pytest.raises(LLMTransientError):
        await _provider(lambda _: _error(status)).qualify(LEAD)


async def test_network_timeout_is_transient() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("timed out", request=request)

    with pytest.raises(LLMTransientError):
        await _provider(handler).qualify(LEAD)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_rejected_requests_are_not_retried(status: int) -> None:
    with pytest.raises(LLMRejectedError):
        await _provider(lambda _: _error(status)).qualify(LEAD)


async def test_refusal_is_reported() -> None:
    body = _completion(None, refusal="I can't help with that.")

    with pytest.raises(LLMRefusalError):
        await _provider(lambda _: httpx2.Response(200, json=body)).qualify(LEAD)


async def test_content_filter_is_a_refusal() -> None:
    body = _completion(None, finish_reason="content_filter")

    with pytest.raises(LLMRefusalError):
        await _provider(lambda _: httpx2.Response(200, json=body)).qualify(LEAD)


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [
        ("not json", "stop"),
        (json.dumps({**GOOD, "confidence": 3}), "stop"),
        ('{"name": nu', "length"),
    ],
)
async def test_invalid_output_is_reported(content: str, finish_reason: str) -> None:
    body = _completion(content, finish_reason)

    with pytest.raises(LLMInvalidOutputError):
        await _provider(lambda _: httpx2.Response(200, json=body)).qualify(LEAD)
