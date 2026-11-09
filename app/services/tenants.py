"""Tenants and API keys. Used by the CLI and by request authentication."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Tenant
from app.domain.api_keys import GeneratedKey, generate_key, parse_prefix, verify_key

# Writing last_used_at on every request would turn every read into a write.
LAST_USED_RESOLUTION = timedelta(minutes=1)


async def create_tenant(session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(name=name)
    session.add(tenant)
    await session.flush()
    return tenant


async def create_api_key(session: AsyncSession, tenant_id: str, pepper: str) -> GeneratedKey:
    key = generate_key(pepper)
    session.add(ApiKey(tenant_id=tenant_id, prefix=key.prefix, key_hash=key.key_hash))
    await session.flush()
    return key


async def revoke_api_key(session: AsyncSession, prefix: str) -> bool:
    result = await session.execute(
        update(ApiKey)
        .where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
        .returning(ApiKey.id)
    )
    return result.first() is not None


async def authenticate(
    session: AsyncSession, plain_key: str, pepper: str
) -> tuple[Tenant, ApiKey] | None:
    """Return the tenant and key for a valid, unrevoked API key, or None."""
    prefix = parse_prefix(plain_key)
    if prefix is None:
        return None
    row = (
        await session.execute(
            select(ApiKey, Tenant)
            .join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(ApiKey.prefix == prefix)
        )
    ).first()
    if row is None:
        return None
    api_key, tenant = row
    if api_key.revoked_at is not None or not verify_key(plain_key, pepper, api_key.key_hash):
        return None

    now = datetime.now(UTC)
    if api_key.last_used_at is None or now - api_key.last_used_at > LAST_USED_RESOLUTION:
        api_key.last_used_at = now
        await session.commit()
    return tenant, api_key
