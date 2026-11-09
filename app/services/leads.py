"""Creating and reading leads."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import IdempotencyKey, Lead
from app.domain.jobs import JobType
from app.domain.pagination import decode_cursor, encode_cursor
from app.domain.status import LeadStatus
from app.errors import ConflictError
from app.services.events import record_event
from app.worker import queue


@dataclass(frozen=True)
class NewLead:
    message: str
    source: str
    name: str | None
    email: str | None
    metadata: dict[str, Any]

    def fingerprint(self) -> str:
        """Hash of the request body, to tell a true retry from a reused key."""
        canonical = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CreateResult:
    lead: Lead
    replayed: bool


@dataclass(frozen=True)
class LeadFilters:
    status: LeadStatus | None = None
    source: str | None = None
    min_score: int | None = None
    created_after: datetime | None = None


@dataclass(frozen=True)
class LeadPage:
    leads: list[Lead]
    next_cursor: str | None


class IdempotencyConflictError(ConflictError):
    code = "idempotency_key_reused"


async def create_lead(
    session: AsyncSession,
    tenant_id: str,
    new: NewLead,
    *,
    actor: str,
    idempotency_key: str | None = None,
    idempotency_ttl: timedelta = timedelta(hours=24),
    job_max_attempts: int = 5,
) -> CreateResult:
    """Store a lead, its `received` event and its qualification job, in one transaction.

    With an idempotency key, a repeat of the same request returns the original lead instead
    of creating a second one, and reusing the key for a different request is a conflict.
    """
    fingerprint = new.fingerprint()
    if idempotency_key is not None:
        replay = await _find_replay(
            session, tenant_id, idempotency_key, fingerprint, idempotency_ttl
        )
        if replay is not None:
            return CreateResult(lead=replay, replayed=True)

    lead = Lead(
        tenant_id=tenant_id,
        status=LeadStatus.RECEIVED,
        source=new.source,
        name=new.name,
        email=new.email,
        message=new.message,
        extra_metadata=new.metadata,
    )
    session.add(lead)
    await session.flush()
    record_event(session, lead, "received", actor=actor, data={"source": new.source})
    queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=job_max_attempts)
    if idempotency_key is not None:
        session.add(
            IdempotencyKey(
                tenant_id=tenant_id, key=idempotency_key, request_hash=fingerprint, lead_id=lead.id
            )
        )
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent request with the same key committed first: return its lead.
        await session.rollback()
        if idempotency_key is None:
            raise
        replay = await _find_replay(
            session, tenant_id, idempotency_key, fingerprint, idempotency_ttl
        )
        if replay is None:
            raise
        return CreateResult(lead=replay, replayed=True)
    return CreateResult(lead=lead, replayed=False)


async def _find_replay(
    session: AsyncSession, tenant_id: str, key: str, fingerprint: str, ttl: timedelta
) -> Lead | None:
    row = await session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == key
        )
    )
    if row is None:
        return None
    if row.created_at < datetime.now(UTC) - ttl:
        # Expired: free the key so this request is treated as new.
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.id == row.id))
        await session.flush()
        return None
    if row.request_hash != fingerprint:
        raise IdempotencyConflictError(
            "This Idempotency-Key was already used with a different request body."
        )
    return await session.get(Lead, row.lead_id)


async def get_lead(session: AsyncSession, tenant_id: str, lead_id: str) -> Lead | None:
    """A lead with its events, only if it belongs to the tenant."""
    return await session.scalar(
        select(Lead)
        .where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        .options(selectinload(Lead.events))
    )


async def list_leads(
    session: AsyncSession,
    tenant_id: str,
    filters: LeadFilters,
    *,
    limit: int,
    cursor: str | None = None,
) -> LeadPage:
    """Newest first. Raises InvalidCursorError for a cursor we did not issue."""
    query = select(Lead).where(Lead.tenant_id == tenant_id)
    if filters.status is not None:
        query = query.where(Lead.status == filters.status)
    if filters.source is not None:
        query = query.where(Lead.source == filters.source)
    if filters.min_score is not None:
        query = query.where(Lead.score >= filters.min_score)
    if filters.created_after is not None:
        query = query.where(Lead.created_at > filters.created_after)
    if cursor is not None:
        query = query.where(Lead.id < decode_cursor(cursor))

    rows = list(await session.scalars(query.order_by(Lead.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    leads = rows[:limit]
    return LeadPage(leads=leads, next_cursor=encode_cursor(leads[-1].id) if has_more else None)
