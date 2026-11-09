import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LlmUsage
from app.services import llm_usage
from app.services import tenants as tenant_service
from tests.integration.conftest import TenantWithKey

TODAY = date(2026, 11, 9)


async def test_reservations_stop_at_the_cap(db_session: AsyncSession, acme: TenantWithKey) -> None:
    results = [
        await llm_usage.reserve_call(db_session, acme.tenant.id, TODAY, cap=3) for _ in range(5)
    ]

    assert results == [True, True, True, False, False]


async def test_cap_is_per_tenant_and_per_day(db_session: AsyncSession, acme: TenantWithKey) -> None:
    other = await tenant_service.create_tenant(db_session, "Other")
    assert await llm_usage.reserve_call(db_session, acme.tenant.id, TODAY, cap=1)

    assert not await llm_usage.reserve_call(db_session, acme.tenant.id, TODAY, cap=1)
    assert await llm_usage.reserve_call(db_session, other.id, TODAY, cap=1)
    assert await llm_usage.reserve_call(db_session, acme.tenant.id, date(2026, 11, 10), cap=1)


async def test_zero_cap_blocks_every_call(db_session: AsyncSession, acme: TenantWithKey) -> None:
    assert not await llm_usage.reserve_call(db_session, acme.tenant.id, TODAY, cap=0)


async def test_tokens_accumulate(db_session: AsyncSession, acme: TenantWithKey) -> None:
    for tokens in ((100, 20), (50, 5)):
        await llm_usage.reserve_call(db_session, acme.tenant.id, TODAY, cap=10)
        await llm_usage.record_tokens(db_session, acme.tenant.id, TODAY, *tokens)

    row = await db_session.get(LlmUsage, (acme.tenant.id, TODAY))
    assert row is not None
    await db_session.refresh(row)
    assert (row.calls, row.input_tokens, row.output_tokens) == (2, 150, 25)


async def test_concurrent_reservations_never_exceed_the_cap(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async with committed() as session:
        tenant = await tenant_service.create_tenant(session, "Busy")
        await session.commit()

    async def reserve() -> bool:
        async with committed() as session:
            granted = await llm_usage.reserve_call(session, tenant.id, TODAY, cap=5)
            await session.commit()
            return granted

    results = await asyncio.gather(*(reserve() for _ in range(20)))

    assert sum(results) == 5
