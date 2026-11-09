import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, Lead, LeadEvent, MockCrmRecord, SyncAttempt
from app.domain.jobs import JobType
from app.services import cleanup
from app.services.events import record_event
from app.worker import queue
from app.worker.cleanup import run_cleanup
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]
OLD = datetime.now(UTC) - timedelta(days=10)


async def _lead_with_history(sessions: Sessions, *, old: bool) -> str:
    lead = await seed_lead(sessions)
    async with sessions() as session:
        record_event(session, lead, "received")
        job = queue.enqueue(session, JobType.SYNC_LEAD, lead.id, max_attempts=5)
        await session.flush()
        session.add(
            SyncAttempt(
                lead_id=lead.id, job_id=job.id, provider="mock", status="failed", duration_ms=1
            )
        )
        session.add(
            MockCrmRecord(external_ref=lead.id, tenant_id=lead.tenant_id, properties={}, version=1)
        )
        if old:
            await session.flush()
            await session.execute(update(Lead).where(Lead.id == lead.id).values(created_at=OLD))
            await session.execute(
                update(MockCrmRecord)
                .where(MockCrmRecord.external_ref == lead.id)
                .values(created_at=OLD)
            )
        await session.commit()
    return lead.id


async def _count(sessions: Sessions, model: type[object]) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_deletes_old_leads_with_their_history_and_keeps_new_ones(
    committed: Sessions,
) -> None:
    old = await _lead_with_history(committed, old=True)
    new = await _lead_with_history(committed, old=False)

    async with committed() as session:
        result = await cleanup.delete_old_data(session, datetime.now(UTC) - timedelta(days=7))

    assert (result.leads, result.crm_records) == (1, 1)
    async with committed() as session:
        assert await session.get(Lead, old) is None
        assert await session.get(Lead, new) is not None
    for model in (LeadEvent, Job, SyncAttempt, MockCrmRecord):
        assert await _count(committed, model) == 1, model


async def test_deletes_in_batches(committed: Sessions, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 2)
    for _ in range(5):
        await _lead_with_history(committed, old=True)

    async with committed() as session:
        result = await cleanup.delete_old_data(session, datetime.now(UTC) - timedelta(days=7))

    assert result.leads == 5
    assert await _count(committed, Lead) == 0


async def test_loop_runs_at_once_and_stops_on_request(committed: Sessions) -> None:
    await _lead_with_history(committed, old=True)
    stop = asyncio.Event()

    task = asyncio.create_task(run_cleanup(committed, timedelta(days=7), stop, interval=60))
    for _ in range(50):
        if await _count(committed, Lead) == 0:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert await _count(committed, Lead) == 0


async def test_loop_survives_a_failed_run(
    committed: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def flaky(session: AsyncSession, older_than: datetime) -> cleanup.CleanupResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database went away")
        return cleanup.CleanupResult(leads=0, crm_records=0)

    monkeypatch.setattr("app.worker.cleanup.delete_old_data", flaky)
    stop = asyncio.Event()
    task = asyncio.create_task(run_cleanup(committed, timedelta(days=7), stop, interval=0.01))
    for _ in range(50):
        if calls >= 2:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert calls >= 2
