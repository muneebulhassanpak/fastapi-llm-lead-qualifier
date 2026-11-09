import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LeadEvent
from app.domain.status import InvalidTransitionError, LeadStatus
from app.services.events import transition
from tests.integration.conftest import MakeLead, TenantWithKey


async def test_transition_changes_status_and_records_event(
    db_session: AsyncSession, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant)

    transition(db_session, lead, LeadStatus.QUALIFYING, data={"job": "j1"})
    await db_session.commit()

    assert lead.status == LeadStatus.QUALIFYING
    event = await db_session.scalar(select(LeadEvent).where(LeadEvent.lead_id == lead.id))
    assert event is not None
    assert event.type == "qualifying"
    assert event.actor == "system"
    assert event.data == {"from": "received", "to": "qualifying", "job": "j1"}


async def test_forbidden_transition_changes_nothing(
    db_session: AsyncSession, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant)

    with pytest.raises(InvalidTransitionError):
        transition(db_session, lead, LeadStatus.SYNCED)

    assert lead.status == LeadStatus.RECEIVED
    events = await db_session.scalars(select(LeadEvent).where(LeadEvent.lead_id == lead.id))
    assert list(events) == []


async def test_database_rejects_unknown_status(
    db_session: AsyncSession, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    from sqlalchemy.exc import IntegrityError

    lead = await make_lead(acme.tenant)
    lead.status = "bogus"

    with pytest.raises(IntegrityError):
        await db_session.commit()
