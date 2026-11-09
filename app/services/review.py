"""What a person can do to a lead: approve, reject, or retry after a failure.

Each action locks the lead row first, so two concurrent actions on the same lead run one
after the other: the second sees the new status and gets a 409 instead of acting twice.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Lead, LeadEvent
from app.domain.jobs import JobStatus, JobType
from app.domain.status import LeadStatus
from app.errors import ConflictError, NotFoundError
from app.llm.base import (
    LLMError,
    LLMInvalidOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMTransientError,
)
from app.services.events import record_event, transition
from app.services.leads import approve_lead
from app.worker import queue

# Review reasons caused by the model rather than by the lead: retrying can help.
LLM_FAILURE_REASONS = frozenset(
    {
        LLMError.code,
        LLMTransientError.code,
        LLMInvalidOutputError.code,
        LLMRefusalError.code,
        LLMRejectedError.code,
        "llm_daily_cap",
    }
)


class LeadStatusConflictError(ConflictError):
    code = "invalid_status"

    def __init__(self, action: str, lead: Lead, allowed: Iterable[LeadStatus]) -> None:
        allowed_values = sorted(s.value for s in allowed)
        super().__init__(
            f"Cannot {action} a lead in status '{lead.status}'.",
            details={"status": lead.status, "allowed_from": allowed_values},
        )


async def approve(
    session: AsyncSession,
    tenant_id: str,
    lead_id: str,
    *,
    actor: str,
    note: str | None,
    score_override: int | None,
    sync_max_attempts: int,
) -> Lead:
    lead = await _lock(session, tenant_id, lead_id)
    if lead.status != LeadStatus.NEEDS_REVIEW:
        raise LeadStatusConflictError("approve", lead, [LeadStatus.NEEDS_REVIEW])
    data: dict[str, Any] = {"note": note}
    if score_override is not None:
        data |= {"score_before": lead.score, "score_after": score_override}
        lead.score = score_override
    approve_lead(
        session,
        lead,
        sync_max_attempts=sync_max_attempts,
        event_type="approved",
        actor=actor,
        data=data,
    )
    await session.commit()
    return lead


async def reject(
    session: AsyncSession, tenant_id: str, lead_id: str, *, actor: str, note: str | None
) -> Lead:
    lead = await _lock(session, tenant_id, lead_id)
    if lead.status != LeadStatus.NEEDS_REVIEW:
        raise LeadStatusConflictError("reject", lead, [LeadStatus.NEEDS_REVIEW])
    transition(session, lead, LeadStatus.REJECTED, actor=actor, data={"note": note})
    await session.commit()
    return lead


async def retry(
    session: AsyncSession,
    tenant_id: str,
    lead_id: str,
    *,
    actor: str,
    qualify_max_attempts: int,
    sync_max_attempts: int,
) -> Lead:
    """Re-queue whatever failed or got stuck.

    - `sync_failed`: sync again.
    - `needs_review` because the model failed (not because of the lead): qualify again.
    - `received` or `qualifying` with no job queued or running (lost): qualify again.
    """
    lead = await _lock(session, tenant_id, lead_id)
    match LeadStatus(lead.status):
        case LeadStatus.SYNC_FAILED:
            transition(
                session,
                lead,
                LeadStatus.SYNCING,
                event_type="retry_requested",
                actor=actor,
                data={"retry": "sync"},
            )
            queue.enqueue(session, JobType.SYNC_LEAD, lead.id, max_attempts=sync_max_attempts)
        case LeadStatus.NEEDS_REVIEW if await _review_reason(session, lead) in LLM_FAILURE_REASONS:
            transition(
                session,
                lead,
                LeadStatus.QUALIFYING,
                event_type="retry_requested",
                actor=actor,
                data={"retry": "qualify"},
            )
            queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=qualify_max_attempts)
        case LeadStatus.RECEIVED | LeadStatus.QUALIFYING if not await _has_active_job(
            session, lead
        ):
            record_event(session, lead, "retry_requested", actor=actor, data={"retry": "qualify"})
            queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=qualify_max_attempts)
        case _:
            raise ConflictError(
                "Nothing to retry: the lead has not failed, or is still being processed.",
                details={"status": lead.status},
            )
    await session.commit()
    return lead


async def _lock(session: AsyncSession, tenant_id: str, lead_id: str) -> Lead:
    lead = await session.scalar(
        select(Lead)
        .where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if lead is None:
        raise NotFoundError("Lead not found.")
    return lead


async def _review_reason(session: AsyncSession, lead: Lead) -> str | None:
    """Why the lead was last sent to review."""
    data = await session.scalar(
        select(LeadEvent.data)
        .where(LeadEvent.lead_id == lead.id, LeadEvent.type == "sent_to_review")
        .order_by(LeadEvent.id.desc())
        .limit(1)
    )
    return data.get("reason") if data else None


async def _has_active_job(session: AsyncSession, lead: Lead) -> bool:
    active = exists().where(
        Job.lead_id == lead.id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
    )
    return bool(await session.scalar(select(active)))
