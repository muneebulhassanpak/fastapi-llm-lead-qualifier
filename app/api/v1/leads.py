from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import ReaderDep, SessionDep, SettingsDep, WriterDep
from app.api.v1.schemas import LeadAccepted, LeadCreate, LeadDetail, LeadList, LeadSummary
from app.domain.pagination import InvalidCursorError
from app.domain.status import LeadStatus
from app.errors import BadRequestError, ErrorResponse, NotFoundError
from app.services import leads as lead_service

router = APIRouter(
    prefix="/leads",
    tags=["leads"],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded; see Retry-After"},
    },
)

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        pattern=r"^[\x21-\x7e]+$",
        description=(
            "Optional. Send the same key when retrying a request: you get the original lead "
            "back instead of a duplicate. Keys are kept for 24 hours."
        ),
    ),
]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an enquiry",
    description=(
        "Stores the enquiry and queues it for qualification. Returns at once with status "
        "`received`; poll `GET /v1/leads/{id}` for the score and decision.\n\n"
        "With an `Idempotency-Key`, repeating the same request returns `200` with the "
        "original lead, and reusing the key for a different body returns `409`."
    ),
    responses={
        200: {"model": LeadAccepted, "description": "Replay of an earlier request"},
        409: {"model": ErrorResponse, "description": "Idempotency-Key reused with another body"},
    },
)
async def create_lead(
    body: LeadCreate,
    response: Response,
    caller: WriterDep,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> LeadAccepted:
    result = await lead_service.create_lead(
        session,
        caller.tenant.id,
        lead_service.NewLead(
            message=body.message,
            source=body.source,
            name=body.name,
            email=str(body.email) if body.email else None,
            metadata=body.metadata,
        ),
        actor=caller.actor,
        idempotency_key=idempotency_key,
        idempotency_ttl=timedelta(hours=settings.idempotency_ttl_hours),
        job_max_attempts=settings.job_max_attempts,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return LeadAccepted.from_model(result.lead)


@router.get(
    "",
    summary="List leads",
    description=(
        "Newest first, only the calling tenant's leads. Use `status=needs_review` to get the "
        "human review queue."
    ),
    responses={400: {"model": ErrorResponse, "description": "Invalid cursor"}},
)
async def list_leads(
    caller: ReaderDep,
    session: SessionDep,
    lead_status: Annotated[LeadStatus | None, Query(alias="status")] = None,
    source: Annotated[str | None, Query(max_length=50)] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    created_after: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> LeadList:
    try:
        page = await lead_service.list_leads(
            session,
            caller.tenant.id,
            lead_service.LeadFilters(
                status=lead_status, source=source, min_score=min_score, created_after=created_after
            ),
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError:
        raise BadRequestError("Invalid cursor.") from None
    return LeadList(
        data=[LeadSummary.from_model(lead) for lead in page.leads], next_cursor=page.next_cursor
    )


@router.get(
    "/{lead_id}",
    summary="Get one lead",
    description="The full record: original enquiry, score, decision, CRM state and audit trail.",
    responses={404: {"model": ErrorResponse, "description": "No such lead for this tenant"}},
)
async def get_lead(lead_id: str, caller: ReaderDep, session: SessionDep) -> LeadDetail:
    lead = await lead_service.get_lead(session, caller.tenant.id, lead_id)
    if lead is None:
        raise NotFoundError("Lead not found.")
    return LeadDetail.from_model(lead)
