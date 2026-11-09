"""Status changes and the audit log. Every status change goes through `transition`."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadEvent
from app.domain.status import LeadStatus, check_transition

SYSTEM_ACTOR = "system"


def api_key_actor(prefix: str) -> str:
    return f"api_key:{prefix}"


def record_event(
    session: AsyncSession,
    lead: Lead,
    event_type: str,
    *,
    actor: str = SYSTEM_ACTOR,
    data: dict[str, Any] | None = None,
) -> LeadEvent:
    event = LeadEvent(lead_id=lead.id, type=event_type, actor=actor, data=data or {})
    session.add(event)
    return event


def transition(
    session: AsyncSession,
    lead: Lead,
    target: LeadStatus,
    *,
    event_type: str | None = None,
    actor: str = SYSTEM_ACTOR,
    data: dict[str, Any] | None = None,
) -> LeadEvent:
    """Move a lead to `target` and record why, in the caller's transaction.

    Raises InvalidTransitionError if the move is not in ALLOWED_TRANSITIONS.
    """
    current = LeadStatus(lead.status)
    check_transition(current, target)
    lead.status = target
    payload = {"from": current.value, "to": target.value, **(data or {})}
    return record_event(session, lead, event_type or target.value, actor=actor, data=payload)
