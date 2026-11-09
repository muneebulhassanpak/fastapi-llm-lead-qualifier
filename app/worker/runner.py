"""The worker loop. Runs as a background task inside the API process."""

import asyncio
import contextlib
import os
import random
import secrets
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Job
from app.domain.jobs import JobStatus, JobType, backoff_seconds
from app.logging import get_logger
from app.worker import queue
from app.worker.handlers import JobHandler, PermanentJobError

log = get_logger(__name__)


def make_worker_id() -> str:
    return f"{socket.gethostname()[:40]}:{os.getpid()}:{secrets.token_hex(3)}"


@dataclass(frozen=True)
class WorkerConfig:
    batch_size: int
    poll_interval: float
    lock_timeout: timedelta
    backoff_base: float
    backoff_max: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "WorkerConfig":
        return cls(
            batch_size=settings.worker_batch_size,
            poll_interval=settings.worker_poll_interval_seconds,
            lock_timeout=timedelta(seconds=settings.job_lock_timeout_seconds),
            backoff_base=settings.job_backoff_base_seconds,
            backoff_max=settings.job_backoff_max_seconds,
        )


class Worker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        handlers: Mapping[JobType, JobHandler],
        config: WorkerConfig,
        *,
        worker_id: str | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._handlers = handlers
        self._config = config
        self._rng = rng
        self.worker_id = worker_id or make_worker_id()

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until `stop` is set. The job in progress is always finished first."""
        log.info("worker_started", worker_id=self.worker_id)
        while not stop.is_set():
            try:
                processed = await self.run_once(stop)
            except Exception:
                # e.g. the database is unreachable: log, back off, try again.
                log.exception("worker_iteration_failed", worker_id=self.worker_id)
                processed = 0
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._config.poll_interval)
        log.info("worker_stopped", worker_id=self.worker_id)

    async def run_once(self, stop: asyncio.Event | None = None) -> int:
        """Recover stale jobs, claim one batch and process it. Returns jobs processed."""
        await self._recover_stale()
        async with self._sessionmaker() as session:
            jobs = await queue.claim(session, self.worker_id, self._config.batch_size)
            await session.commit()
        for index, job in enumerate(jobs):
            if stop is not None and stop.is_set():
                # Hand unstarted jobs back right away instead of waiting for the lock timeout.
                await self._release([j.id for j in jobs[index:]])
                return index
            await self._process(job.id)
        return len(jobs)

    async def _recover_stale(self) -> None:
        async with self._sessionmaker() as session:
            recovered = await queue.recover_stale(session, self._config.lock_timeout)
            for job in recovered:
                log.warning("job_recovered", job_id=job.id, type=job.type, status=job.status)
                if job.status == JobStatus.FAILED:
                    await self._dead_letter(session, job)
            await session.commit()

    async def _release(self, job_ids: list[str]) -> None:
        async with self._sessionmaker() as session:
            for job_id in job_ids:
                job = await session.get(Job, job_id)
                if job is not None and job.status == JobStatus.RUNNING:
                    job.status = JobStatus.QUEUED
                    job.attempts -= 1
                    job.locked_at = None
                    job.locked_by = None
            await session.commit()

    async def _process(self, job_id: str) -> None:
        started = time.perf_counter()
        error: Exception | None = None
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:  # deleted along with its lead
                return
            context = {
                "job_id": job.id,
                "type": job.type,
                "lead_id": job.lead_id,
                "attempt": job.attempts,
            }
            handler = self._handlers.get(JobType(job.type))
            try:
                if handler is None:
                    raise PermanentJobError(f"No handler registered for job type '{job.type}'.")
                await handler.run(session, job)
                queue.complete(job)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                error = exc

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if error is None:
            log.info("job_done", duration_ms=duration_ms, **context)
            return
        await self._record_failure(job_id, handler, error, duration_ms, context)

    async def _record_failure(
        self,
        job_id: str,
        handler: JobHandler | None,
        error: Exception,
        duration_ms: float,
        context: dict[str, object],
    ) -> None:
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            retry_in = None
            if not isinstance(error, PermanentJobError):
                delay = backoff_seconds(
                    job.attempts,
                    base=self._config.backoff_base,
                    cap=self._config.backoff_max,
                    rng=self._rng,
                )
                retry_in = timedelta(seconds=delay)
            failure = queue.fail(job, f"{type(error).__name__}: {error}", retry_in=retry_in)
            if failure.outcome is queue.FailureOutcome.DEAD and handler is not None:
                await self._dead_letter(session, job, handler)
            await session.commit()

        log.warning(
            "job_failed",
            outcome=failure.outcome,
            retry_in_s=failure.retry_in.total_seconds() if failure.retry_in else None,
            error=type(error).__name__,
            duration_ms=duration_ms,
            **context,
        )

    async def _dead_letter(
        self, session: AsyncSession, job: Job, handler: JobHandler | None = None
    ) -> None:
        handler = handler or self._handlers.get(JobType(job.type))
        if handler is None:
            return
        try:
            async with session.begin_nested():
                await handler.on_dead_letter(session, job)
        except Exception:
            # The job is failed either way; don't let a broken hook block that.
            log.exception("dead_letter_hook_failed", job_id=job.id, type=job.type)
