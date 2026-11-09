"""A job queue on a Postgres table.

Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of workers can
poll the same table without ever getting the same job. All times come from the database
clock (`now()`), so app servers with skewed clocks still agree on when a job is due.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.domain.jobs import JobStatus, JobType

MAX_ERROR_LENGTH = 2000
LOST_WORKER_ERROR = "Worker stopped while running the job (lock timed out)."


def enqueue(
    session: AsyncSession,
    job_type: JobType,
    lead_id: str,
    *,
    max_attempts: int,
    delay: timedelta | None = None,
) -> Job:
    """Add a job in the caller's transaction, so it exists only if the caller's change commits."""
    job = Job(type=job_type, lead_id=lead_id, status=JobStatus.QUEUED, max_attempts=max_attempts)
    if delay is not None:
        job.run_after = func.now() + delay
    session.add(job)
    return job


async def claim(session: AsyncSession, worker_id: str, limit: int) -> list[Job]:
    """Lock up to `limit` due jobs for this worker and count the attempt. Caller commits."""
    due = (
        select(Job.id)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= func.now())
        .order_by(Job.run_after, Job.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = await session.scalars(
        update(Job)
        .where(Job.id.in_(due))
        .values(
            status=JobStatus.RUNNING,
            locked_at=func.now(),
            locked_by=worker_id,
            attempts=Job.attempts + 1,
        )
        .returning(Job)
        .execution_options(populate_existing=True)
    )
    return sorted(claimed, key=lambda job: (job.run_after, job.id))


def complete(job: Job) -> None:
    job.status = JobStatus.DONE
    job.locked_at = None
    job.locked_by = None
    job.last_error = None


class FailureOutcome(StrEnum):
    RETRY = "retry"
    DEAD = "dead"


@dataclass(frozen=True)
class Failure:
    outcome: FailureOutcome
    retry_in: timedelta | None = None


def fail(job: Job, error: str, *, retry_in: timedelta | None) -> Failure:
    """Record a failed attempt: schedule a retry, or dead-letter the job.

    `retry_in=None` means the error is permanent. A job also dies once it has used all of
    its attempts.
    """
    job.last_error = error[:MAX_ERROR_LENGTH]
    job.locked_at = None
    job.locked_by = None
    if retry_in is None or job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
        return Failure(FailureOutcome.DEAD)
    job.status = JobStatus.QUEUED
    job.run_after = func.now() + retry_in
    return Failure(FailureOutcome.RETRY, retry_in)


async def recover_stale(session: AsyncSession, lock_timeout: timedelta) -> list[Job]:
    """Re-queue jobs stuck in `running` (their worker crashed or restarted mid-job).

    Jobs that have no attempts left are returned as `failed` instead, so a job that crashes
    the process every time cannot loop forever. Caller commits.
    """
    stale = (
        select(Job.id)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < func.now() - lock_timeout)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    recovered = await session.scalars(
        update(Job)
        .where(Job.id.in_(stale))
        .values(
            status=case(
                (Job.attempts >= Job.max_attempts, JobStatus.FAILED.value),
                else_=JobStatus.QUEUED.value,
            ),
            locked_at=None,
            locked_by=None,
            last_error=LOST_WORKER_ERROR,
        )
        .returning(Job)
        .execution_options(populate_existing=True)
    )
    return list(recovered)
