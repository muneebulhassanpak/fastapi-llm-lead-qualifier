from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.crm.mock import MockCRMAdapter
from app.db.models import Lead, LeadEvent
from app.domain.jobs import JobType
from app.domain.status import LeadStatus
from app.llm.mock import MockProvider
from app.worker import queue
from app.worker.registry import default_handlers
from app.worker.runner import Worker, WorkerConfig
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]


async def test_strong_lead_goes_from_received_to_synced(
    committed: Sessions, settings: Settings
) -> None:
    lead = await seed_lead(
        committed, "We need a Supabase app audited before launch. Budget around $3k."
    )
    async with committed() as session:
        queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=3)
        await session.commit()
    handlers = default_handlers(settings, MockProvider(), MockCRMAdapter(committed))
    config = WorkerConfig(
        batch_size=10,
        poll_interval=0.01,
        lock_timeout=timedelta(minutes=5),
        backoff_base=1,
        backoff_max=10,
    )
    worker = Worker(committed, handlers, config, worker_id="test-worker")

    assert await worker.run_once() == 1  # qualify, which queues the sync
    assert await worker.run_once() == 1  # sync

    async with committed() as session:
        synced = await session.get(Lead, lead.id)
        events = (
            await session.scalars(
                select(LeadEvent.type).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.id)
            )
        ).all()
    assert synced is not None
    assert synced.status == LeadStatus.SYNCED
    assert synced.crm_external_id is not None
    assert events == ["qualifying", "scored", "auto_approved", "syncing", "crm_synced"]
