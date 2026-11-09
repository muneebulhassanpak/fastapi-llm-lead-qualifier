"""Webhook endpoints: created and revoked from the CLI, looked up on each delivery."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WebhookEndpoint
from app.db.models.webhook import WebhookProvider
from app.webhooks.secrets import encrypt_secret, generate_secret


@dataclass(frozen=True)
class CreatedEndpoint:
    endpoint: WebhookEndpoint
    secret: str  # shown once, never stored in clear


async def create_endpoint(
    session: AsyncSession, tenant_id: str, provider: WebhookProvider, server_key: str
) -> CreatedEndpoint:
    secret = generate_secret()
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        provider=provider,
        secret_encrypted=encrypt_secret(secret, server_key),
    )
    session.add(endpoint)
    await session.flush()
    return CreatedEndpoint(endpoint=endpoint, secret=secret)


async def revoke_endpoint(session: AsyncSession, endpoint_id: str) -> bool:
    result = await session.execute(
        update(WebhookEndpoint)
        .where(WebhookEndpoint.id == endpoint_id, WebhookEndpoint.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
        .returning(WebhookEndpoint.id)
    )
    return result.first() is not None


async def get_active_endpoint(
    session: AsyncSession, endpoint_id: str, provider: str
) -> WebhookEndpoint | None:
    """The endpoint if it exists, is for this provider and is not revoked."""
    endpoint = await session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.provider != provider or endpoint.revoked_at is not None:
        return None
    return endpoint
