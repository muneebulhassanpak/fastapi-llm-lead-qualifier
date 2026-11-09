"""Deleting old demo data, so a public demo database doesn't grow without limit."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Delete, delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadEvent, MockCrmRecord
from app.services.events import SEED_ACTOR

BATCH_SIZE = 500


@dataclass(frozen=True)
class CleanupResult:
    leads: int
    crm_records: int


async def delete_old_data(session: AsyncSession, older_than: datetime) -> CleanupResult:
    """Delete leads created before `older_than`, with everything that hangs off them (events,
    jobs, sync attempts, idempotency keys go by cascade), and mock CRM records as old.
    The demo's seeded sample leads are kept, so the demo never ends up empty.

    Works in batches, committing each, so no single transaction locks many rows for long.
    """
    seeded = exists().where(
        LeadEvent.lead_id == Lead.id,
        LeadEvent.type == "received",
        LeadEvent.actor == SEED_ACTOR,
    )
    old_leads = select(Lead.id).where(Lead.created_at < older_than, ~seeded).limit(BATCH_SIZE)
    old_records = (
        select(MockCrmRecord.id).where(MockCrmRecord.created_at < older_than).limit(BATCH_SIZE)
    )
    return CleanupResult(
        leads=await _delete_in_batches(session, delete(Lead).where(Lead.id.in_(old_leads))),
        crm_records=await _delete_in_batches(
            session, delete(MockCrmRecord).where(MockCrmRecord.id.in_(old_records))
        ),
    )


async def _delete_in_batches(session: AsyncSession, statement: Delete) -> int:
    """Run a delete limited to BATCH_SIZE rows until it deletes fewer than that."""
    total = 0
    while True:
        result = cast(CursorResult[Any], await session.execute(statement))
        await session.commit()
        total += result.rowcount
        if result.rowcount < BATCH_SIZE:
            return total
