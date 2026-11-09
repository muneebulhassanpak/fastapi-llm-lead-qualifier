from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crm.base import CRMLead, CRMResult, CRMTransientError
from app.crm.mock import MockCRMAdapter
from app.db.models import Job, Lead, LeadEvent, MockCrmRecord, SyncAttempt
from app.domain.jobs import JobStatus, JobType
from app.domain.status import LeadStatus
from app.worker import queue
from app.worker.runner import Worker, WorkerConfig
from app.worker.sync import SyncLeadHandler
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]
CONFIG = WorkerConfig(
    batch_size=10,
    poll_interval=0.01,
    lock_timeout=timedelta(minutes=5),
    backoff_base=1,
    backoff_max=10,
)


class FlakyCRM(MockCRMAdapter):
    """The mock CRM, but the first `failures` calls raise `error`."""

    def __init__(self, sessions: Sessions, failures: int, error: Exception) -> None:
        super().__init__(sessions)
        self.failures = failures
        self.error = error
        self.calls = 0

    async def upsert_lead(self, lead: CRMLead) -> CRMResult:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return await super().upsert_lead(lead)


async def _setup(
    sessions: Sessions,
    *,
    status: LeadStatus = LeadStatus.APPROVED,
    metadata: dict[str, Any] | None = None,
    max_attempts: int = 5,
) -> tuple[str, str]:
    lead = await seed_lead(sessions, status=status, metadata=metadata)
    async with sessions() as session:
        job = queue.enqueue(session, JobType.SYNC_LEAD, lead.id, max_attempts=max_attempts)
        await session.commit()
        return job.id, lead.id


def _worker(sessions: Sessions, crm: MockCRMAdapter | None = None) -> Worker:
    handler = SyncLeadHandler(crm or MockCRMAdapter(sessions))
    return Worker(sessions, {JobType.SYNC_LEAD: handler}, CONFIG, worker_id="test-worker")


async def _run_until_settled(sessions: Sessions, worker: Worker, job_id: str) -> Job:
    """Run the job, skipping each backoff wait, until it is done or failed."""
    for _ in range(10):
        await worker.run_once()
        async with sessions() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            if job.status in (JobStatus.DONE, JobStatus.FAILED):
                return job
            await session.execute(update(Job).where(Job.id == job_id).values(run_after=func.now()))
            await session.commit()
    raise AssertionError("job never settled")


async def _lead(sessions: Sessions, lead_id: str) -> Lead:
    async with sessions() as session:
        lead = await session.get(Lead, lead_id)
        assert lead is not None
        return lead


async def _attempts(sessions: Sessions, lead_id: str) -> Sequence[SyncAttempt]:
    async with sessions() as session:
        stmt = select(SyncAttempt).where(SyncAttempt.lead_id == lead_id).order_by(SyncAttempt.id)
        return (await session.scalars(stmt)).all()


async def _event_types(sessions: Sessions, lead_id: str) -> list[str]:
    async with sessions() as session:
        stmt = select(LeadEvent.type).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.id)
        return list((await session.scalars(stmt)).all())


async def _crm_records(sessions: Sessions) -> Sequence[MockCrmRecord]:
    async with sessions() as session:
        return (await session.scalars(select(MockCrmRecord))).all()


async def test_approved_lead_is_synced(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed)

    job = await _run_until_settled(committed, _worker(committed), job_id)

    assert job.status == JobStatus.DONE
    lead = await _lead(committed, lead_id)
    assert lead.status == LeadStatus.SYNCED
    [record] = await _crm_records(committed)
    assert lead.crm_external_id == record.id
    assert record.external_ref == lead_id
    [attempt] = await _attempts(committed, lead_id)
    assert (attempt.status, attempt.provider, attempt.job_id) == ("succeeded", "mock", job_id)
    assert await _event_types(committed, lead_id) == ["syncing", "crm_synced"]


async def test_two_failures_then_success(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed)
    crm = FlakyCRM(committed, 2, CRMTransientError("503 Service Unavailable", http_status=503))

    job = await _run_until_settled(committed, _worker(committed, crm), job_id)

    assert (job.status, job.attempts) == (JobStatus.DONE, 3)
    assert (await _lead(committed, lead_id)).status == LeadStatus.SYNCED
    attempts = await _attempts(committed, lead_id)
    assert [a.status for a in attempts] == ["failed", "failed", "succeeded"]
    assert [a.http_status for a in attempts] == [503, 503, None]
    assert attempts[0].error == "CRMTransientError: 503 Service Unavailable"
    assert len(await _crm_records(committed)) == 1


async def test_validation_error_dead_letters_at_once(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed, metadata={"simulate_crm_failure": "invalid"})

    job = await _run_until_settled(committed, _worker(committed), job_id)

    assert (job.status, job.attempts) == (JobStatus.FAILED, 1)
    assert (await _lead(committed, lead_id)).status == LeadStatus.SYNC_FAILED
    [attempt] = await _attempts(committed, lead_id)
    assert attempt.http_status == 422
    assert await _event_types(committed, lead_id) == ["syncing", "crm_sync_failed"]
    assert await _crm_records(committed) == []


async def test_lead_goes_to_sync_failed_after_the_last_attempt(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed, metadata={"simulate_crm_failure": "unavailable"})

    job = await _run_until_settled(committed, _worker(committed), job_id)

    assert (job.status, job.attempts) == (JobStatus.FAILED, 5)
    lead = await _lead(committed, lead_id)
    assert lead.status == LeadStatus.SYNC_FAILED
    assert lead.crm_external_id is None
    assert len(await _attempts(committed, lead_id)) == 5
    async with committed() as session:
        event = await session.scalar(
            select(LeadEvent).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.id.desc())
        )
    assert event is not None
    assert event.type == "crm_sync_failed"
    assert event.data["attempts"] == 5
    assert "503" in event.data["error"]


async def test_rate_limit_waits_for_retry_after(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed, metadata={"simulate_crm_failure": "rate_limited"})

    await _worker(committed).run_once()

    async with committed() as session:
        job = await session.get(Job, job_id)
        wait = await session.scalar(
            select(func.extract("epoch", Job.run_after - func.now())).where(Job.id == job_id)
        )
    assert job is not None
    assert job.status == JobStatus.QUEUED
    assert wait is not None
    assert float(wait) > 9, "Retry-After is 30 s, capped at backoff_max (10 s) in this config"
    assert (await _lead(committed, lead_id)).status == LeadStatus.SYNCING


async def test_unexpected_error_is_recorded_and_retried(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed)
    crm = FlakyCRM(committed, 1, ConnectionResetError("connection reset"))

    job = await _run_until_settled(committed, _worker(committed, crm), job_id)

    assert job.status == JobStatus.DONE
    first, _ = await _attempts(committed, lead_id)
    assert (first.http_status, first.error) == (None, "ConnectionResetError: connection reset")


async def test_running_the_sync_twice_keeps_one_crm_record(committed: Sessions) -> None:
    """E.g. the worker died after the CRM call but before its own commit."""
    job_id, lead_id = await _setup(committed)
    worker = _worker(committed)
    await _run_until_settled(committed, worker, job_id)
    first_id = (await _lead(committed, lead_id)).crm_external_id
    async with committed() as session:
        await session.execute(
            update(Lead).where(Lead.id == lead_id).values(status=LeadStatus.SYNCING)
        )
        again = queue.enqueue(session, JobType.SYNC_LEAD, lead_id, max_attempts=5)
        await session.commit()
        again_id = again.id

    await _run_until_settled(committed, worker, again_id)

    [record] = await _crm_records(committed)
    assert record.id == first_id == (await _lead(committed, lead_id)).crm_external_id
    assert record.version == 2


@pytest.mark.parametrize("status", [LeadStatus.SYNCED, LeadStatus.NEEDS_REVIEW])
async def test_lead_not_waiting_for_sync_is_left_alone(
    committed: Sessions, status: LeadStatus
) -> None:
    job_id, lead_id = await _setup(committed, status=status)

    job = await _run_until_settled(committed, _worker(committed), job_id)

    assert job.status == JobStatus.DONE
    assert (await _lead(committed, lead_id)).status == status
    assert await _attempts(committed, lead_id) == []


async def test_stale_job_out_of_attempts_marks_approved_lead_failed(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed, max_attempts=1)
    async with committed() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.RUNNING,
                attempts=1,
                locked_by="crashed-worker",
                locked_at=func.now() - timedelta(hours=1),
            )
        )
        await session.commit()

    await _worker(committed).run_once()

    assert (await _lead(committed, lead_id)).status == LeadStatus.SYNC_FAILED
    assert await _event_types(committed, lead_id) == ["syncing", "crm_sync_failed"]
