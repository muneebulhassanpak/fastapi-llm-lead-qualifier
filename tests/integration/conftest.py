"""Integration tests run against a real Postgres.

A fresh database is created once per test run and migrated with Alembic. Each test runs
inside a transaction that is rolled back afterwards, so tests never see each other's data.
Code under test may call `session.commit()`: in "create_savepoint" mode that only releases
a savepoint inside the outer transaction.

Start the local database first: `docker compose up -d db`.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import get_session
from app.config import AppEnv, Settings
from app.db.base import Base
from app.db.engine import create_engine, create_sessionmaker
from app.db.models import Lead, Tenant
from app.domain.status import LeadStatus
from app.main import create_app
from app.services import tenants as tenant_service

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://leadqual:leadqual@localhost:5433/leadqual_test"
)
PEPPER = "test-pepper"


async def _recreate_database(url: str) -> None:
    target = make_url(url)
    admin = target.set(drivername="postgresql", database="postgres")
    try:
        conn = await asyncpg.connect(admin.render_as_string(hide_password=False))
    except OSError as exc:
        raise RuntimeError(
            f"Cannot reach the test database at {target.host}:{target.port}. "
            "Start it with `docker compose up -d db`, or set TEST_DATABASE_URL."
        ) from exc
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{target.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    asyncio.run(_recreate_database(TEST_DATABASE_URL))
    config = Config("alembic.ini")
    config.attributes["database_url"] = TEST_DATABASE_URL
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="session")
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
def settings(migrated_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        app_version="9.9.9",
        worker_enabled=False,
        database_url=migrated_database_url,
        api_key_pepper=SecretStr(PEPPER),
    )


@pytest_asyncio.fixture
async def app(settings: Settings, db_session: AsyncSession) -> AsyncIterator[FastAPI]:
    app = create_app(settings)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    yield app
    await app.state.engine.dispose()


class TenantWithKey:
    def __init__(self, tenant: Tenant, key: str, prefix: str) -> None:
        self.tenant = tenant
        self.key = key
        self.prefix = prefix

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.key}


MakeTenant = Callable[[str], Coroutine[Any, Any, TenantWithKey]]


@pytest.fixture
def make_tenant(db_session: AsyncSession) -> MakeTenant:
    async def _make(name: str = "Acme") -> TenantWithKey:
        tenant = await tenant_service.create_tenant(db_session, name)
        key = await tenant_service.create_api_key(db_session, tenant.id, PEPPER)
        await db_session.commit()
        return TenantWithKey(tenant, key.plain, key.prefix)

    return _make


@pytest_asyncio.fixture
async def acme(make_tenant: MakeTenant) -> TenantWithKey:
    return await make_tenant("Acme")


MakeLead = Callable[..., Coroutine[Any, Any, Lead]]


@pytest.fixture
def make_lead(db_session: AsyncSession) -> MakeLead:
    """Insert a lead directly, in any status, bypassing the API."""

    async def _make(
        tenant: Tenant,
        *,
        status: LeadStatus = LeadStatus.RECEIVED,
        source: str = "api",
        score: int | None = None,
        message: str = "Hello",
    ) -> Lead:
        lead = Lead(tenant_id=tenant.id, status=status, source=source, score=score, message=message)
        db_session.add(lead)
        await db_session.commit()
        return lead

    return _make


@pytest_asyncio.fixture
async def committed(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions that really commit, for tests that need several connections (locking,
    concurrency, the worker). Every table is emptied afterwards."""
    yield create_sessionmaker(engine)
    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


async def seed_lead(sessionmaker: async_sessionmaker[AsyncSession]) -> Lead:
    """A committed tenant and lead to hang jobs on."""
    async with sessionmaker() as session:
        tenant = await tenant_service.create_tenant(session, "Queue Co")
        lead = Lead(tenant_id=tenant.id, status=LeadStatus.RECEIVED, source="api", message="Hi")
        session.add(lead)
        await session.commit()
        return lead
