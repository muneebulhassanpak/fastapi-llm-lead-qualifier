import pytest
from pydantic import SecretStr, ValidationError

from app.config import LLMMode, Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.factory import build_provider
from app.llm.mock import MockProvider
from app.llm.openai_provider import OpenAIProvider

KEY = SecretStr("sk-test")


def test_mock_is_the_default() -> None:
    assert isinstance(build_provider(Settings(_env_file=None)), MockProvider)


def test_anthropic_defaults_to_haiku() -> None:
    provider = build_provider(Settings(_env_file=None, llm_mode=LLMMode.ANTHROPIC, llm_api_key=KEY))

    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-haiku-5-5"


def test_anthropic_model_can_be_overridden() -> None:
    settings = Settings(
        _env_file=None, llm_mode=LLMMode.ANTHROPIC, llm_api_key=KEY, llm_model="claude-sonnet-5-5"
    )

    assert build_provider(settings).name == "anthropic"
    assert getattr(build_provider(settings), "model", None) == "claude-sonnet-5-5"


def test_openai_uses_the_configured_model() -> None:
    settings = Settings(
        _env_file=None, llm_mode=LLMMode.OPENAI, llm_api_key=KEY, llm_model="some-model"
    )

    provider = build_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "some-model"


@pytest.mark.parametrize("mode", [LLMMode.ANTHROPIC, LLMMode.OPENAI])
def test_real_provider_requires_a_key(mode: LLMMode) -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        Settings(_env_file=None, llm_mode=mode, llm_model="m")


def test_openai_requires_a_model() -> None:
    with pytest.raises(ValidationError, match="LLM_MODEL is required"):
        Settings(_env_file=None, llm_mode=LLMMode.OPENAI, llm_api_key=KEY)
