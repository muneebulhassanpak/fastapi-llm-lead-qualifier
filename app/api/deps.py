"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request, Response, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Tenant
from app.errors import RateLimitedError, UnauthorizedError
from app.ratelimit import SlidingWindowLimiter
from app.services.events import api_key_actor
from app.services.tenants import authenticate


def get_app_settings(request: Request) -> Settings:
    """Settings the running app was created with (not the process-wide default)."""
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = cast(async_sessionmaker[AsyncSession], request.app.state.sessionmaker)
    async with sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Tenant API key, e.g. `lq_live_ab12cd34_...`. Created with the CLI.",
)


@dataclass(frozen=True)
class Caller:
    tenant: Tenant
    key_prefix: str

    @property
    def actor(self) -> str:
        return api_key_actor(self.key_prefix)


async def get_caller(
    session: SessionDep,
    settings: SettingsDep,
    api_key: Annotated[str | None, Security(api_key_header)],
) -> Caller:
    if not api_key:
        raise UnauthorizedError("Missing X-API-Key header.")
    result = await authenticate(session, api_key, settings.api_key_pepper.get_secret_value())
    if result is None:
        raise UnauthorizedError("Invalid or revoked API key.")
    tenant, key = result
    return Caller(tenant=tenant, key_prefix=key.prefix)


CallerDep = Annotated[Caller, Depends(get_caller)]


def get_rate_limiter(request: Request) -> SlidingWindowLimiter:
    return cast(SlidingWindowLimiter, request.app.state.rate_limiter)


RateLimiterDep = Annotated[SlidingWindowLimiter, Depends(get_rate_limiter)]


def enforce_rate_limit(
    limiter: SlidingWindowLimiter, key: str, limit: int, response: Response
) -> None:
    """Count one request against `key`; 429 with Retry-After if it is over the limit."""
    result = limiter.hit(key, limit)
    if not result.allowed:
        raise RateLimitedError(
            f"Rate limit of {limit} requests per minute exceeded.", headers=result.headers()
        )
    response.headers.update(result.headers())


async def get_reader(
    caller: CallerDep, settings: SettingsDep, limiter: RateLimiterDep, response: Response
) -> Caller:
    limit = settings.rate_limit_reads_per_minute
    enforce_rate_limit(limiter, f"read:{caller.key_prefix}", limit, response)
    return caller


async def get_writer(
    caller: CallerDep, settings: SettingsDep, limiter: RateLimiterDep, response: Response
) -> Caller:
    limit = settings.rate_limit_writes_per_minute
    enforce_rate_limit(limiter, f"write:{caller.key_prefix}", limit, response)
    return caller


# Authenticated callers, counted against their key's read or write limit.
ReaderDep = Annotated[Caller, Depends(get_reader)]
WriterDep = Annotated[Caller, Depends(get_writer)]
