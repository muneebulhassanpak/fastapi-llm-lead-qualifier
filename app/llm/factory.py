from app.config import LLMMode, Settings
from app.llm.anthropic_provider import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.mock import MockProvider
from app.llm.openai_provider import OpenAIProvider


def build_provider(settings: Settings) -> LLMProvider:
    """The provider selected by LLM_MODE. Settings validation guarantees key and model exist."""
    api_key = settings.llm_api_key.get_secret_value()
    match settings.llm_mode:
        case LLMMode.MOCK:
            return MockProvider()
        case LLMMode.ANTHROPIC:
            return AnthropicProvider(
                api_key,
                settings.llm_model or ANTHROPIC_DEFAULT_MODEL,
                timeout=settings.llm_timeout_seconds,
            )
        case LLMMode.OPENAI:
            return OpenAIProvider(api_key, settings.llm_model, timeout=settings.llm_timeout_seconds)
