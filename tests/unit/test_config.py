import pytest
from pydantic import SecretStr, ValidationError

from app.config import AppEnv, LLMMode, Settings


def test_defaults_are_safe_for_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.DEVELOPMENT
    assert settings.llm_mode is LLMMode.MOCK


def test_production_requires_secrets() -> None:
    with pytest.raises(ValidationError, match="API_KEY_PEPPER, WEBHOOK_SECRET_KEY"):
        Settings(_env_file=None, app_env=AppEnv.PRODUCTION)


def test_production_with_real_llm_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        Settings(
            _env_file=None,
            app_env=AppEnv.PRODUCTION,
            api_key_pepper=SecretStr("p"),
            webhook_secret_key=SecretStr("w"),
            llm_mode=LLMMode.ANTHROPIC,
        )


def test_production_with_secrets_is_valid() -> None:
    settings = Settings(
        _env_file=None,
        app_env=AppEnv.PRODUCTION,
        api_key_pepper=SecretStr("p"),
        webhook_secret_key=SecretStr("w"),
    )

    assert settings.is_production


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="DISQUALIFY_SCORE"):
        Settings(_env_file=None, auto_approve_score=30, disqualify_score=30)


def test_demo_cleanup_is_off_unless_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).demo_retention_days is None
    monkeypatch.setenv("DEMO_RETENTION_DAYS", "")
    assert Settings(_env_file=None).demo_retention_days is None
    monkeypatch.setenv("DEMO_RETENTION_DAYS", "7")
    assert Settings(_env_file=None).demo_retention_days == 7
