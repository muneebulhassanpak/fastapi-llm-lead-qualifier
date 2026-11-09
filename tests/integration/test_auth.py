from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey
from app.services import tenants as tenant_service
from tests.integration.conftest import PEPPER, TenantWithKey


async def _status(client: AsyncClient, headers: dict[str, str]) -> int:
    return (await client.get("/v1/leads", headers=headers)).status_code


async def test_valid_key_is_accepted(client: AsyncClient, acme: TenantWithKey) -> None:
    assert await _status(client, acme.headers) == 200


async def test_missing_key_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/v1/leads")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    assert response.json()["error"]["code"] == "unauthorized"


async def test_wrong_secret_is_rejected(client: AsyncClient, acme: TenantWithKey) -> None:
    tampered = acme.key[:-4] + ("aaaa" if not acme.key.endswith("aaaa") else "bbbb")

    assert await _status(client, {"X-API-Key": tampered}) == 401


async def test_unknown_prefix_is_rejected(client: AsyncClient) -> None:
    assert await _status(client, {"X-API-Key": "lq_live_00000000_" + "x" * 43}) == 401


async def test_malformed_key_is_rejected(client: AsyncClient) -> None:
    assert await _status(client, {"X-API-Key": "not-a-key"}) == 401


async def test_revoked_key_is_rejected(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    assert await tenant_service.revoke_api_key(db_session, acme.prefix)
    await db_session.commit()

    assert await _status(client, acme.headers) == 401
    assert not await tenant_service.revoke_api_key(db_session, acme.prefix)


async def test_key_from_another_pepper_is_rejected(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    other = await tenant_service.create_api_key(db_session, acme.tenant.id, "another-pepper")
    await db_session.commit()

    assert await _status(client, {"X-API-Key": other.plain}) == 401


async def test_plain_key_is_never_stored(acme: TenantWithKey, db_session: AsyncSession) -> None:
    stored = await db_session.scalar(select(ApiKey).where(ApiKey.prefix == acme.prefix))

    assert stored is not None
    assert acme.key not in (stored.key_hash, stored.prefix)


async def test_last_used_at_is_updated_at_most_once_a_minute(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    await _status(client, acme.headers)
    key = await db_session.scalar(select(ApiKey).where(ApiKey.prefix == acme.prefix))
    assert key is not None
    first_use = key.last_used_at
    assert first_use is not None

    await _status(client, acme.headers)
    assert key.last_used_at == first_use

    key.last_used_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.commit()
    await _status(client, acme.headers)
    assert key.last_used_at > first_use


async def test_cli_service_creates_working_keys(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tenant = await tenant_service.create_tenant(db_session, "Via CLI")
    key = await tenant_service.create_api_key(db_session, tenant.id, PEPPER)
    await db_session.commit()

    assert await _status(client, {"X-API-Key": key.plain}) == 200
