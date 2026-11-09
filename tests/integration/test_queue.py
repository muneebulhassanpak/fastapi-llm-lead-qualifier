import asyncio
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, Lead
from app.domain.jobs import JobStatus, JobType
from app.worker import queue
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]


async def _enqueue(sessions: Sessions, count: int = 1, *, max_attempts: int = 3) -> list[str]:
    lead = await seed_lead(sessions)
    async with sessions() as session:
        jobs = [
            queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=max_attempts)
            for _ in range(count)
        ]
        await session.commit()
        return [job.id for job in jobs]


async def _claim(sessions: Sessions, limit: int = 10, worker: str = "w1") -> list[Job]:
    async with sessions() as session:
        jobs = await queue.claim(session, worker, limit)
        await session.commit()
        return jobs


async def _get(sessions: Sessions, job_id: str) -> Job:
    async with sessions() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def test_claim_locks_job_and_counts_attempt(committed: Sessions) -> None:
    [job_id] = await _enqueue(committed)

    [job] = await _claim(committed, worker="worker-a")

    assert job.id == job_id
    assert job.status == JobStatus.RUNNING
    assert job.attempts == 1
    assert job.locked_by == "worker-a"
    assert job.locked_at is not None
    assert await _claim(committed) == []


async def test_claim_respects_limit_and_order(committed: Sessions) -> None:
    ids = await _enqueue(committed, 3)
    async with committed() as session:
        # Make the last job the most overdue.
        await session.execute(
            update(Job).where(Job.id == ids[2]).values(run_after=func.now() - timedelta(hours=1))
        )
        await session.commit()

    first = await _claim(committed, limit=2)

    assert [job.id for job in first] == [ids[2], ids[0]]


async def test_delayed_job_is_not_claimed_early(committed: Sessions) -> None:
    lead = await seed_lead(committed)
    async with committed() as session:
        queue.enqueue(
            session, JobType.QUALIFY_LEAD, lead.id, max_attempts=3, delay=timedelta(minutes=5)
        )
        await session.commit()

    assert await _claim(committed) == []


async def test_concurrent_claimers_never_share_a_job(committed: Sessions) -> None:
    ids = await _enqueue(committed, 40)

    batches = await asyncio.gather(*(_claim(committed, limit=7, worker=f"w{i}") for i in range(8)))

    claimed = [job.id for batch in batches for job in batch]
    assert len(claimed) == len(set(claimed)) == 40
    assert set(claimed) == set(ids)


async def test_failure_schedules_retry(committed: Sessions) -> None:
    [job_id] = await _enqueue(committed)
    await _claim(committed)

    async with committed() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        failure = queue.fail(job, "Timeout: LLM", retry_in=timedelta(seconds=30))
        await session.commit()

    assert failure.outcome is queue.FailureOutcome.RETRY
    job = await _get(committed, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.last_error == "Timeout: LLM"
    assert job.locked_by is None
    assert await _claim(committed) == [], "retry must wait for its backoff"


async def test_permanent_failure_dead_letters_at_once(committed: Sessions) -> None:
    [job_id] = await _enqueue(committed)
    await _claim(committed)

    async with committed() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        failure = queue.fail(job, "bad data", retry_in=None)
        await session.commit()

    assert failure.outcome is queue.FailureOutcome.DEAD
    assert (await _get(committed, job_id)).status == JobStatus.FAILED


async def test_last_attempt_dead_letters(committed: Sessions) -> None:
    [job_id] = await _enqueue(committed, max_attempts=1)
    await _claim(committed)

    async with committed() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        failure = queue.fail(job, "still failing", retry_in=timedelta(seconds=1))
        await session.commit()

    assert failure.outcome is queue.FailureOutcome.DEAD


async def test_error_text_is_truncated(committed: Sessions) -> None:
    [job_id] = await _enqueue(committed)
    await _claim(committed)

    async with committed() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        queue.fail(job, "x" * 10_000, retry_in=timedelta(seconds=1))
        await session.commit()

    assert len((await _get(committed, job_id)).last_error or "") == queue.MAX_ERROR_LENGTH


async def test_recover_stale_requeues_or_fails(committed: Sessions) -> None:
    retryable, exhausted, fresh = await _enqueue(committed, 3, max_attempts=2)
    await _claim(committed)
    async with committed() as session:
        await session.execute(
            update(Job)
            .where(Job.id.in_([retryable, exhausted]))
            .values(locked_at=func.now() - timedelta(minutes=10))
        )
        await session.execute(update(Job).where(Job.id == exhausted).values(attempts=2))
        await session.commit()

    async with committed() as session:
        recovered = await queue.recover_stale(session, timedelta(minutes=5))
        await session.commit()

    statuses = {job.id: job.status for job in recovered}
    assert statuses == {retryable: JobStatus.QUEUED, exhausted: JobStatus.FAILED}
    assert (await _get(committed, fresh)).status == JobStatus.RUNNING
    assert (await _get(committed, retryable)).last_error == queue.LOST_WORKER_ERROR


async def test_jobs_are_deleted_with_their_lead(committed: Sessions) -> None:
    [job_id] = await _enqueue(committed)
    job = await _get(committed, job_id)

    async with committed() as session:
        await session.execute(delete(Lead).where(Lead.id == job.lead_id))
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
