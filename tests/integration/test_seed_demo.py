import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, Lead, LeadEvent
from app.services.events import SEED_ACTOR
from scripts.seed_demo import SAMPLES, seed
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]


async def test_seed_queues_every_sample_for_the_tenant(committed: Sessions) -> None:
    tenant_id = (await seed_lead(committed)).tenant_id

    async with committed() as session:
        lead_ids = await seed(session, tenant_id, job_max_attempts=3)

    assert len(lead_ids) == len(SAMPLES) == 8
    async with committed() as session:
        leads = (await session.scalars(select(Lead).where(Lead.id.in_(lead_ids)))).all()
        assert {lead.tenant_id for lead in leads} == {tenant_id}
        actors = (
            await session.scalars(
                select(LeadEvent.actor).where(
                    LeadEvent.type == "received", LeadEvent.lead_id.in_(lead_ids)
                )
            )
        ).all()
        assert set(actors) == {SEED_ACTOR}
        jobs = (await session.scalars(select(Job).where(Job.lead_id.in_(lead_ids)))).all()
        assert len(jobs) == 8


async def test_seed_for_unknown_tenant_fails(committed: Sessions) -> None:
    async with committed() as session:
        with pytest.raises(LookupError):
            await seed(session, "tn_missing", job_max_attempts=3)
