"""The CLI commits for real (outside the per-test transaction) into the throwaway test DB."""

import re

import pytest
from pydantic import SecretStr
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


ENDPOINT_RE = re.compile(r"Endpoint:\s+(whk_\w+) \((\w+)\)")
SECRET_RE = re.compile(r"Secret:\s+(whsec_\S+)")


def _tenant_id(name: str) -> str:
    created = runner.invoke(cli_module.cli, ["create-tenant", "--name", name])
    match = TENANT_RE.search(created.output)
    assert match is not None
    return match.group(1)


def test_create_and_revoke_webhook() -> None:
    tenant_id = _tenant_id("Hooks Co")

    created = runner.invoke(
        cli_module.cli, ["create-webhook", "--tenant", tenant_id, "--provider", "tally"]
    )

    assert created.exit_code == 0, created.output
    endpoint = ENDPOINT_RE.search(created.output)
    assert endpoint is not None
    assert endpoint.group(2) == "tally"
    assert f"/v1/webhooks/tally/{endpoint.group(1)}" in created.output
    assert SECRET_RE.search(created.output)
    revoked = runner.invoke(cli_module.cli, ["revoke-webhook", "--id", endpoint.group(1)])
    assert revoked.exit_code == 0
    again = runner.invoke(cli_module.cli, ["revoke-webhook", "--id", endpoint.group(1)])
    assert again.exit_code == 1


def test_create_webhook_for_unknown_tenant_or_provider_fails() -> None:
    unknown_tenant = runner.invoke(
        cli_module.cli, ["create-webhook", "--tenant", "tn_missing", "--provider", "generic"]
    )
    unknown_provider = runner.invoke(
        cli_module.cli, ["create-webhook", "--tenant", "tn_missing", "--provider", "zapier"]
    )

    assert unknown_tenant.exit_code == 1
    assert unknown_provider.exit_code == 2


def test_create_webhook_needs_the_server_key(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    tenant_id = _tenant_id("No Key Co")
    no_key = settings.model_copy(
        update={"app_env": AppEnv.TEST, "webhook_secret_key": SecretStr("")}
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: no_key)

    result = runner.invoke(
        cli_module.cli, ["create-webhook", "--tenant", tenant_id, "--provider", "generic"]
    )

    assert result.exit_code == 1
    assert "WEBHOOK_SECRET_KEY" in result.output


def test_cleanup_needs_confirmation_and_reports_counts() -> None:
    aborted = runner.invoke(cli_module.cli, ["cleanup", "--older-than-days", "7"], input="n\n")
    confirmed = runner.invoke(cli_module.cli, ["cleanup", "--older-than-days", "7", "--yes"])

    assert aborted.exit_code == 1
    assert confirmed.exit_code == 0, confirmed.output
    assert "Deleted 0 leads and 0 mock CRM records." in confirmed.output
