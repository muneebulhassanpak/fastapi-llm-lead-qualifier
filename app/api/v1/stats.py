from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import ReaderDep, SessionDep
from app.domain.status import LeadStatus
from app.errors import ErrorResponse
from app.services.stats import lead_stats

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded; see Retry-After"},
    },
)


class StatsOut(BaseModel):
    days: int
    since: datetime
    total: int = Field(description="Leads received in the window.")
    by_status: dict[LeadStatus, int] = Field(description="Current status of those leads.")
    by_source: dict[str, int]
    average_score: float | None = Field(description="Over leads that have a score.")


@router.get(
    "",
    summary="Summary of recent leads",
    description="Counts and average score for the calling tenant's leads from the last `days`.",
)
async def get_stats(
    caller: ReaderDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> StatsOut:
    since = datetime.now(UTC) - timedelta(days=days)
    stats = await lead_stats(session, caller.tenant.id, since)
    return StatsOut(
        days=days,
        since=since,
        total=stats.total,
        by_status={LeadStatus(k): v for k, v in stats.by_status.items()},
        by_source=stats.by_source,
        average_score=stats.average_score,
    )
