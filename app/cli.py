"""Admin CLI. Tenants and API keys are created here, never through the public API.

python -m app.cli create-tenant --name "Acme"
python -m app.cli create-key --tenant tn_...
python -m app.cli revoke-key --prefix ab12cd34
python -m app.cli create-webhook --tenant tn_... --provider tally
python -m app.cli revoke-webhook --id whk_...
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import create_engine, create_sessionmaker
from app.db.models import Tenant
from app.db.models.webhook import WebhookProvider
from app.services import tenants as tenant_service
from app.services import webhooks as webhook_service
from app.services.webhooks import CreatedEndpoint
from app.webhooks.secrets import SecretKeyMissingError

cli = typer.Typer(help="Lead Qualification API admin commands.", no_args_is_help=True)


def _run[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def main() -> T:
        engine = create_engine(get_settings().database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(main())


def _pepper() -> str:
    return get_settings().api_key_pepper.get_secret_value()


def _show_key(plain: str) -> None:
    typer.echo(f"API key:   {plain}")
    typer.secho("Store it now. It is not saved anywhere and cannot be shown again.", fg="yellow")


@cli.command("create-tenant")
def create_tenant(name: Annotated[str, typer.Option(help="Tenant display name.")]) -> None:
    """Create a tenant and its first API key."""

    async def work(session: AsyncSession) -> tuple[Tenant, str]:
        tenant = await tenant_service.create_tenant(session, name)
        key = await tenant_service.create_api_key(session, tenant.id, _pepper())
        return tenant, key.plain

    tenant, plain = _run(work)
    typer.echo(f"Tenant:    {tenant.id} ({tenant.name})")
    _show_key(plain)


@cli.command("create-key")
def create_key(tenant: Annotated[str, typer.Option(help="Tenant ID (tn_...).")]) -> None:
    """Create another API key for an existing tenant."""

    async def work(session: AsyncSession) -> str | None:
        if await session.get(Tenant, tenant) is None:
            return None
        return (await tenant_service.create_api_key(session, tenant, _pepper())).plain

    plain = _run(work)
    if plain is None:
        typer.secho(f"No tenant with ID {tenant}.", fg="red", err=True)
        raise typer.Exit(1)
    _show_key(plain)


@cli.command("revoke-key")
def revoke_key(
    prefix: Annotated[str, typer.Option(help="Key prefix (the part after lq_live_).")],
) -> None:
    """Revoke an API key. Requests using it get 401 from then on."""
    if not _run(lambda session: tenant_service.revoke_api_key(session, prefix)):
        typer.secho(f"No active key with prefix {prefix}.", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"Revoked key {prefix}.")


@cli.command("create-webhook")
def create_webhook(
    tenant: Annotated[str, typer.Option(help="Tenant ID (tn_...).")],
    provider: Annotated[WebhookProvider, typer.Option(help="Who will send the webhooks.")],
) -> None:
    """Create a webhook endpoint and print its URL path and signing secret once."""
    server_key = get_settings().webhook_secret_key.get_secret_value()

    async def work(session: AsyncSession) -> CreatedEndpoint | None:
        if await session.get(Tenant, tenant) is None:
            return None
        return await webhook_service.create_endpoint(session, tenant, provider, server_key)

    try:
        created = _run(work)
    except SecretKeyMissingError:
        typer.secho("Set WEBHOOK_SECRET_KEY first (any long random string).", fg="red", err=True)
        raise typer.Exit(1) from None
    if created is None:
        typer.secho(f"No tenant with ID {tenant}.", fg="red", err=True)
        raise typer.Exit(1)
    endpoint = created.endpoint
    typer.echo(f"Endpoint:  {endpoint.id} ({endpoint.provider})")
    typer.echo(f"URL path:  /v1/webhooks/{endpoint.provider}/{endpoint.id}")
    typer.echo(f"Secret:    {created.secret}")
    typer.secho("Store the secret now. It cannot be shown again.", fg="yellow")


@cli.command("revoke-webhook")
def revoke_webhook(endpoint_id: Annotated[str, typer.Option("--id", help="Endpoint ID.")]) -> None:
    """Revoke a webhook endpoint. Deliveries to it get 401 from then on."""
    if not _run(lambda session: webhook_service.revoke_endpoint(session, endpoint_id)):
        typer.secho(f"No active webhook endpoint {endpoint_id}.", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"Revoked webhook endpoint {endpoint_id}.")


@cli.command("list-tenants")
def list_tenants() -> None:
    """List tenants."""

    async def work(session: AsyncSession) -> list[Tenant]:
        return list(await session.scalars(select(Tenant).order_by(Tenant.created_at)))

    for tenant in _run(work):
        typer.echo(f"{tenant.id}  {tenant.name}")


if __name__ == "__main__":
    cli()
