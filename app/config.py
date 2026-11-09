"""Application settings, loaded from environment variables and `.env`."""

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.url import to_async_url


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMMode(StrEnum):
    MOCK = "mock"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class CRMMode(StrEnum):
    MOCK = "mock"


class Settings(BaseSettings):
    """Every setting has a safe local default. Production must set the secrets explicitly."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.DEVELOPMENT
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # Any Postgres URL works, including Neon's as copied (see app/db/url.py).
    database_url: str = "postgresql+asyncpg://leadqual:leadqual@localhost:5433/leadqual"
    idempotency_ttl_hours: int = Field(default=24, gt=0)

    api_key_pepper: SecretStr = SecretStr("")
    webhook_secret_key: SecretStr = SecretStr("")

    worker_enabled: bool = True
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_batch_size: int = Field(default=5, ge=1, le=100)
    job_lock_timeout_seconds: int = Field(default=300, gt=0)
    job_max_attempts: int = Field(default=5, ge=1)
    job_backoff_base_seconds: float = Field(default=5.0, gt=0)
    job_backoff_max_seconds: float = Field(default=600.0, gt=0)

    llm_mode: LLMMode = LLMMode.MOCK
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_daily_cap: int = Field(default=200, ge=0)

    auto_approve_score: int = Field(default=60, ge=0, le=100)
    disqualify_score: int = Field(default=30, ge=0, le=100)
    min_confidence: float = Field(default=0.75, ge=0, le=1)

    crm_mode: CRMMode = CRMMode.MOCK
    crm_max_attempts: int = Field(default=5, ge=1)
    mock_crm_fail_rate: float = Field(default=0.0, ge=0, le=1)

    rate_limit_writes_per_minute: int = Field(default=60, gt=0)
    rate_limit_reads_per_minute: int = Field(default=300, gt=0)

    demo_retention_days: int = Field(default=7, gt=0)

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        to_async_url(value)  # raises on a malformed or non-Postgres URL
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.disqualify_score >= self.auto_approve_score:
            raise ValueError("DISQUALIFY_SCORE must be lower than AUTO_APPROVE_SCORE")
        # A real provider without its key or model would fail on every lead; fail at startup.
        if self.llm_mode is not LLMMode.MOCK and not self.llm_api_key.get_secret_value():
            raise ValueError(f"LLM_API_KEY is required when LLM_MODE={self.llm_mode.value}")
        if self.llm_mode is LLMMode.OPENAI and not self.llm_model:
            raise ValueError("LLM_MODEL is required when LLM_MODE=openai")
        if self.is_production:
            missing = [
                name
                for name, value in (
                    ("API_KEY_PEPPER", self.api_key_pepper),
                    ("WEBHOOK_SECRET_KEY", self.webhook_secret_key),
                )
                if not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"Missing required production settings: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
