"""Job handler interface and the handlers registered by default."""

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.domain.jobs import JobType
from app.logging import get_logger

log = get_logger(__name__)


class PermanentJobError(Exception):
    """Retrying cannot help (e.g. invalid data): dead-letter the job at once."""


class JobHandler:
    """Work for one job type.

    `run` executes inside a transaction that also marks the job done, so database changes and
    job completion commit together or not at all. A job can still run more than once (after a
    crash, or a retry), so handlers must be idempotent.
    Raise PermanentJobError to skip retries; any other exception is retried with backoff.
    """

    async def run(self, session: AsyncSession, job: Job) -> None:
        raise NotImplementedError

    async def on_dead_letter(self, session: AsyncSession, job: Job) -> None:
        """Called once, in the transaction that marks the job failed for good."""


class PendingQualificationHandler(JobHandler):
    """Placeholder until the LLM qualifier lands (Phase 05): leaves the lead in `received`."""

    async def run(self, session: AsyncSession, job: Job) -> None:
        log.info("qualification_pending", lead_id=job.lead_id)


def default_handlers() -> Mapping[JobType, JobHandler]:
    return {JobType.QUALIFY_LEAD: PendingQualificationHandler()}
