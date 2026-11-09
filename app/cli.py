"""Admin CLI. Tenants and API keys are created here, never through the public API.

python -m app.cli create-tenant --name "Acme"
python -m app.cli create-key --tenant tn_...
python -m app.cli revoke-key --prefix ab12cd34
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
from app.services import tenants as tenant_service

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


@cli.command("list-tenants")
def list_tenants() -> None:
    """List tenants."""

    async def work(session: AsyncSession) -> list[Tenant]:
        return list(await session.scalars(select(Tenant).order_by(Tenant.created_at)))

    for tenant in _run(work):
        typer.echo(f"{tenant.id}  {tenant.name}")


if __name__ == "__main__":
    cli()
