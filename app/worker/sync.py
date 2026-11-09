"""The `sync_lead` job: push an approved lead to the CRM."""

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.crm.base import CRMAdapter, CRMError, CRMLead, CRMPermanentError, CRMTransientError
from app.db.models import Job, Lead, SyncAttempt
from app.db.models.crm import SyncAttemptStatus
from app.domain.status import LeadStatus
from app.logging import get_logger
from app.services.events import transition
from app.services.leads import lock_lead
from app.worker.handlers import JobHandler, PermanentJobError, RetryLaterError

log = get_logger(__name__)

MAX_ERROR_LENGTH = 1000


class SyncLeadHandler(JobHandler):
    """Like qualification, the CRM call happens with no lock or transaction open:

    1. lock the lead, move it `approved` → `syncing`, commit;
    2. upsert it in the CRM, and commit a `sync_attempts` row whatever happens;
    3. on success store the external ID and mark it `synced`, with the job's completion.

    Failures are left to the queue: 4xx-style errors dead-letter at once, everything else
    retries with backoff (at least as long as any Retry-After). When the job is dead the lead
    goes to `sync_failed`. The upsert is keyed by our lead ID, so a job that runs twice still
    leaves one CRM record.
    """

    def __init__(self, crm: CRMAdapter) -> None:
        self._crm = crm

    async def run(self, session: AsyncSession, job: Job) -> None:
        lead = await lock_lead(session, job.lead_id)
        if lead is None or lead.status not in (LeadStatus.APPROVED, LeadStatus.SYNCING):
            return  # deleted, already synced, or sent elsewhere by a person: nothing to do
        if lead.status == LeadStatus.APPROVED:
            transition(session, lead, LeadStatus.SYNCING, data={"job_id": job.id})
        crm_lead = _to_crm_lead(lead)
        await session.commit()

        started = time.perf_counter()
        try:
            result = await self._crm.upsert_lead(crm_lead)
        except Exception as exc:
            self._record_attempt(session, job, started, error=exc)
            await session.commit()
            job_error = _job_error(exc)
            if job_error is exc:
                raise
            raise job_error from exc
        self._record_attempt(session, job, started)

        lead = await lock_lead(session, job.lead_id)
        if lead is None or lead.status != LeadStatus.SYNCING:
            return
        lead.crm_external_id = result.external_id
        transition(
            session,
            lead,
            LeadStatus.SYNCED,
            event_type="crm_synced",
            data={"provider": self._crm.name, "external_id": result.external_id},
        )
        log.info("lead_synced", lead_id=lead.id, provider=self._crm.name)

    async def on_dead_letter(self, session: AsyncSession, job: Job) -> None:
        lead = await lock_lead(session, job.lead_id)
        if lead is None:
            return
        if lead.status == LeadStatus.APPROVED:
            transition(session, lead, LeadStatus.SYNCING, data={"job_id": job.id})
        if lead.status == LeadStatus.SYNCING:
            transition(
                session,
                lead,
                LeadStatus.SYNC_FAILED,
                event_type="crm_sync_failed",
                data={"attempts": job.attempts, "error": (job.last_error or "")[:300]},
            )

    def _record_attempt(
        self, session: AsyncSession, job: Job, started: float, *, error: Exception | None = None
    ) -> None:
        session.add(
            SyncAttempt(
                lead_id=job.lead_id,
                job_id=job.id,
                provider=self._crm.name,
                status=SyncAttemptStatus.FAILED if error else SyncAttemptStatus.SUCCEEDED,
                http_status=error.http_status if isinstance(error, CRMError) else None,
                error=f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH] if error else None,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        )


def _job_error(exc: Exception) -> Exception:
    """How the queue should treat a failed CRM call."""
    if isinstance(exc, CRMPermanentError):
        return PermanentJobError(str(exc))
    if isinstance(exc, CRMTransientError) and exc.retry_after is not None:
        return RetryLaterError(str(exc), retry_after=exc.retry_after)
    return exc


def _to_crm_lead(lead: Lead) -> CRMLead:
    extracted = lead.extracted or {}
    return CRMLead(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        source=lead.source,
        email=lead.email,
        name=lead.name or extracted.get("name"),
        company=extracted.get("company"),
        intent=extracted.get("intent"),
        score=lead.score,
        reason=lead.reason,
        metadata=dict(lead.extra_metadata),
    )
