"""The CLI commits for real (outside the per-test transaction) into the throwaway test DB."""

import re

import pytest
from typer.testing import CliRunner

from app import cli as cli_module
from app.config import AppEnv, Settings

runner = CliRunner()
KEY_RE = re.compile(r"API key:\s+(lq_live_([a-z0-9]{8})_\S+)")
TENANT_RE = re.compile(r"Tenant:\s+(tn_\w+)")


@pytest.fixture(autouse=True)
def _cli_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    # Never let the CLI pick up DATABASE_URL from a developer's .env during tests.
    test_settings = settings.model_copy(update={"app_env": AppEnv.TEST})
    monkeypatch.setattr(cli_module, "get_settings", lambda: test_settings)


def test_create_tenant_prints_key_once() -> None:
    result = runner.invoke(cli_module.cli, ["create-tenant", "--name", "CLI Co"])

    assert result.exit_code == 0, result.output
    assert TENANT_RE.search(result.output)
    assert KEY_RE.search(result.output)
    assert "cannot be shown again" in result.output


def test_create_and_revoke_key() -> None:
    created = runner.invoke(cli_module.cli, ["create-tenant", "--name", "Keys Co"])
    tenant_match = TENANT_RE.search(created.output)
    assert tenant_match is not None

    extra = runner.invoke(cli_module.cli, ["create-key", "--tenant", tenant_match.group(1)])
    key_match = KEY_RE.search(extra.output)
    assert extra.exit_code == 0
    assert key_match is not None

    revoked = runner.invoke(cli_module.cli, ["revoke-key", "--prefix", key_match.group(2)])
    assert revoked.exit_code == 0
    again = runner.invoke(cli_module.cli, ["revoke-key", "--prefix", key_match.group(2)])
    assert again.exit_code == 1


def test_create_key_for_unknown_tenant_fails() -> None:
    result = runner.invoke(cli_module.cli, ["create-key", "--tenant", "tn_missing"])

    assert result.exit_code == 1


def test_list_tenants() -> None:
    runner.invoke(cli_module.cli, ["create-tenant", "--name", "Listed Co"])

    result = runner.invoke(cli_module.cli, ["list-tenants"])

    assert result.exit_code == 0
    assert "Listed Co" in result.output
