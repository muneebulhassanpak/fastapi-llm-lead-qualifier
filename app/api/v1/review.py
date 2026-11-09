"""Human review actions on a lead: approve, reject, retry."""

from typing import Annotated

from fastapi import APIRouter, Body

from app.api.deps import SessionDep, SettingsDep, WriterDep
from app.api.v1.schemas import ApproveRequest, LeadDetail, RejectRequest
from app.errors import ErrorResponse, NotFoundError
from app.services import leads as lead_service
from app.services import review

router = APIRouter(
    prefix="/leads",
    tags=["review"],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        404: {"model": ErrorResponse, "description": "No such lead for this tenant"},
        409: {"model": ErrorResponse, "description": "Not allowed in the lead's current status"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded; see Retry-After"},
    },
)


async def _detail(session: SessionDep, tenant_id: str, lead_id: str) -> LeadDetail:
    lead = await lead_service.get_lead(session, tenant_id, lead_id)
    if lead is None:  # pragma: no cover - the action just found it
        raise NotFoundError("Lead not found.")
    return LeadDetail.from_model(lead)


@router.post(
    "/{lead_id}/approve",
    summary="Approve a lead in review",
    description=(
        "Only for leads in `needs_review`. Moves the lead to `approved` and queues the CRM "
        "sync. Optionally overrides the score; the note and both scores go in the audit trail."
    ),
)
async def approve_lead(
    lead_id: str,
    caller: WriterDep,
    session: SessionDep,
    settings: SettingsDep,
    body: Annotated[ApproveRequest | None, Body()] = None,
) -> LeadDetail:
    body = body or ApproveRequest()
    await review.approve(
        session,
        caller.tenant.id,
        lead_id,
        actor=caller.actor,
        note=body.note,
        score_override=body.score_override,
        sync_max_attempts=settings.crm_max_attempts,
    )
    return await _detail(session, caller.tenant.id, lead_id)


@router.post(
    "/{lead_id}/reject",
    summary="Reject a lead in review",
    description="Only for leads in `needs_review`. Moves the lead to `rejected`, which is final.",
)
async def reject_lead(
    lead_id: str,
    caller: WriterDep,
    session: SessionDep,
    body: Annotated[RejectRequest | None, Body()] = None,
) -> LeadDetail:
    await review.reject(
        session, caller.tenant.id, lead_id, actor=caller.actor, note=(body or RejectRequest()).note
    )
    return await _detail(session, caller.tenant.id, lead_id)


@router.post(
    "/{lead_id}/retry",
    summary="Retry a failed lead",
    description=(
        "Re-queues what failed:\n\n"
        "- `sync_failed`: syncs to the CRM again.\n"
        "- `needs_review` because the model failed (unavailable, invalid output, refused, "
        "daily cap): qualifies again.\n"
        "- `received` or `qualifying` with no job queued or running: qualifies again.\n\n"
        "Anything else returns `409`."
    ),
)
async def retry_lead(
    lead_id: str, caller: WriterDep, session: SessionDep, settings: SettingsDep
) -> LeadDetail:
    await review.retry(
        session,
        caller.tenant.id,
        lead_id,
        actor=caller.actor,
        qualify_max_attempts=settings.job_max_attempts,
        sync_max_attempts=settings.crm_max_attempts,
    )
    return await _detail(session, caller.tenant.id, lead_id)
