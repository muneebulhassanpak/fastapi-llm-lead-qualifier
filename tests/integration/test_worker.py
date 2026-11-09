import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, Lead
from app.domain.jobs import JobStatus, JobType
from app.worker import queue
from app.worker.handlers import JobHandler, PermanentJobError
from app.worker.runner import Worker, WorkerConfig
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]
CONFIG = WorkerConfig(
    batch_size=10,
    poll_interval=0.01,
    lock_timeout=timedelta(minutes=5),
    backoff_base=1,
    backoff_max=10,
)


class RecordingHandler(JobHandler):
    """Fails the first `fail_times` runs, then renames the lead to prove it ran."""

    def __init__(self, fail_times: int = 0, error: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.error = error or RuntimeError("boom")
        self.runs = 0
        self.dead_letters: list[str] = []

    async def run(self, session: AsyncSession, job: Job) -> None:
        self.runs += 1
        lead = await session.get(Lead, job.lead_id)
        assert lead is not None
        lead.name = f"run {self.runs}"
        if self.runs <= self.fail_times:
            raise self.error

    async def on_dead_letter(self, session: AsyncSession, job: Job) -> None:
        self.dead_letters.append(job.id)


async def _setup(sessions: Sessions, *, max_attempts: int = 3) -> tuple[str, str]:
    lead = await seed_lead(sessions)
    async with sessions() as session:
        job = queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=max_attempts)
        await session.commit()
        return job.id, lead.id


async def _job(sessions: Sessions, job_id: str) -> Job:
    async with sessions() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def _lead_name(sessions: Sessions, lead_id: str) -> str | None:
    async with sessions() as session:
        lead = await session.get(Lead, lead_id)
        assert lead is not None
        return lead.name


async def _make_due(sessions: Sessions, job_id: str) -> None:
    async with sessions() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(run_after=func.now()))
        await session.commit()


def _worker(sessions: Sessions, handler: JobHandler | None) -> Worker:
    handlers = {JobType.QUALIFY_LEAD: handler} if handler else {}
    return Worker(sessions, handlers, CONFIG, worker_id="test-worker")


async def test_successful_job_commits_work_and_completes(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed)
    handler = RecordingHandler()

    assert await _worker(committed, handler).run_once() == 1

    job = await _job(committed, job_id)
    assert job.status == JobStatus.DONE
    assert job.locked_by is None
    assert await _lead_name(committed, lead_id) == "run 1"


async def test_failed_job_rolls_back_its_work_and_retries(committed: Sessions) -> None:
    job_id, lead_id = await _setup(committed)
    handler = RecordingHandler(fail_times=1)
    worker = _worker(committed, handler)

    await worker.run_once()

    job = await _job(committed, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.last_error == "RuntimeError: boom"
    assert await _lead_name(committed, lead_id) is None, "handler's writes must be rolled back"
    assert await worker.run_once() == 0, "retry waits for its backoff"

    await _make_due(committed, job_id)
    await worker.run_once()

    job = await _job(committed, job_id)
    assert job.status == JobStatus.DONE
    assert job.attempts == 2
    assert await _lead_name(committed, lead_id) == "run 2"


async def test_exhausted_job_is_dead_lettered_once(committed: Sessions) -> None:
    job_id, _ = await _setup(committed, max_attempts=2)
    handler = RecordingHandler(fail_times=99)
    worker = _worker(committed, handler)

    await worker.run_once()
    await _make_due(committed, job_id)
    await worker.run_once()

    assert (await _job(committed, job_id)).status == JobStatus.FAILED
    assert handler.dead_letters == [job_id]


async def test_permanent_error_skips_retries(committed: Sessions) -> None:
    job_id, _ = await _setup(committed, max_attempts=5)
    handler = RecordingHandler(fail_times=1, error=PermanentJobError("invalid lead"))

    await _worker(committed, handler).run_once()

    job = await _job(committed, job_id)
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert handler.dead_letters == [job_id]


async def test_broken_dead_letter_hook_still_fails_the_job(committed: Sessions) -> None:
    class BrokenHook(RecordingHandler):
        async def on_dead_letter(self, session: AsyncSession, job: Job) -> None:
            raise RuntimeError("hook broke")

    job_id, _ = await _setup(committed, max_attempts=1)

    await _worker(committed, BrokenHook(fail_times=1)).run_once()

    assert (await _job(committed, job_id)).status == JobStatus.FAILED


async def test_job_without_handler_fails(committed: Sessions) -> None:
    job_id, _ = await _setup(committed)

    await _worker(committed, None).run_once()

    job = await _job(committed, job_id)
    assert job.status == JobStatus.FAILED
    assert "No handler registered" in (job.last_error or "")


async def test_stale_job_is_recovered_and_finished(committed: Sessions) -> None:
    """Simulates a worker that died mid-job: the job is re-run after the lock timeout."""
    job_id, lead_id = await _setup(committed)
    async with committed() as session:
        await queue.claim(session, "dead-worker", 1)
        await session.execute(
            update(Job).where(Job.id == job_id).values(locked_at=func.now() - timedelta(hours=1))
        )
        await session.commit()

    await _worker(committed, RecordingHandler()).run_once()

    job = await _job(committed, job_id)
    assert job.status == JobStatus.DONE
    assert job.attempts == 2
    assert await _lead_name(committed, lead_id) == "run 1"


async def test_stale_job_without_attempts_left_is_dead_lettered(committed: Sessions) -> None:
    job_id, _ = await _setup(committed, max_attempts=1)
    async with committed() as session:
        await queue.claim(session, "dead-worker", 1)
        await session.execute(
            update(Job).where(Job.id == job_id).values(locked_at=func.now() - timedelta(hours=1))
        )
        await session.commit()
    handler = RecordingHandler()

    await _worker(committed, handler).run_once()

    assert (await _job(committed, job_id)).status == JobStatus.FAILED
    assert handler.dead_letters == [job_id]
    assert handler.runs == 0


async def test_stop_mid_batch_hands_back_unstarted_jobs(committed: Sessions) -> None:
    lead = await seed_lead(committed)
    async with committed() as session:
        for _ in range(3):
            queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=3)
        await session.commit()
    stop = asyncio.Event()

    class StopAfterFirst(RecordingHandler):
        async def run(self, session: AsyncSession, job: Job) -> None:
            await super().run(session, job)
            stop.set()

    processed = await _worker(committed, StopAfterFirst()).run_once(stop)

    assert processed == 1
    async with committed() as session:
        queued = await session.scalar(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
        )
    assert queued == 2


async def test_run_loop_processes_jobs_and_stops_cleanly(committed: Sessions) -> None:
    job_id, _ = await _setup(committed)
    stop = asyncio.Event()
    task = asyncio.create_task(_worker(committed, RecordingHandler()).run(stop))

    for _ in range(200):
        if (await _job(committed, job_id)).status == JobStatus.DONE:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert (await _job(committed, job_id)).status == JobStatus.DONE


async def test_run_loop_survives_iteration_errors(
    committed: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker(committed, RecordingHandler())
    stop = asyncio.Event()
    calls = 0

    async def flaky_run_once(_stop: asyncio.Event | None = None) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database went away")
        stop.set()
        return 0

    monkeypatch.setattr(worker, "run_once", flaky_run_once)

    await asyncio.wait_for(worker.run(stop), timeout=2)

    assert calls == 2
