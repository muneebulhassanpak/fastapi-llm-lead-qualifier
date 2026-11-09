import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.crm.factory import build_crm_adapter
from app.crm.mock import MockCRMAdapter


def test_mock_is_the_default() -> None:
    adapter = build_crm_adapter(Settings(_env_file=None), async_sessionmaker())

    assert isinstance(adapter, MockCRMAdapter)
    assert adapter.name == "mock"


def test_unknown_crm_mode_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_MODE", "hubspot")  # not in v1

    with pytest.raises(ValidationError, match="crm_mode"):
        Settings(_env_file=None)
