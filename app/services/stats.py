"""Summary numbers for a tenant's recent leads."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead
from app.domain.status import LeadStatus


@dataclass(frozen=True)
class LeadStats:
    total: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    average_score: float | None


async def lead_stats(session: AsyncSession, tenant_id: str, since: datetime) -> LeadStats:
    recent = (Lead.tenant_id == tenant_id, Lead.created_at >= since)
    by_status = {s.value: 0 for s in LeadStatus}
    for status, count in await session.execute(
        select(Lead.status, func.count()).where(*recent).group_by(Lead.status)
    ):
        by_status[status] = count
    by_source: dict[str, int] = {}
    for source, count in await session.execute(
        select(Lead.source, func.count())
        .where(*recent)
        .group_by(Lead.source)
        .order_by(func.count().desc(), Lead.source)
    ):
        by_source[source] = count
    average = await session.scalar(select(func.avg(Lead.score)).where(*recent))
    return LeadStats(
        total=sum(by_status.values()),
        by_status=by_status,
        by_source=by_source,
        average_score=round(float(average), 1) if average is not None else None,
    )
