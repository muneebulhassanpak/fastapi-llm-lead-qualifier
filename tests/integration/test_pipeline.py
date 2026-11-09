from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.crm.mock import MockCRMAdapter
from app.db.models import Lead, LeadEvent
from app.domain.jobs import JobType
from app.domain.status import LeadStatus
from app.llm.base import LeadInput, LLMProvider, LLMRefusalError, QualificationResult
from app.llm.mock import MockProvider
from app.services import review
from app.worker import queue
from app.worker.registry import default_handlers
from app.worker.runner import Worker, WorkerConfig
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]


CONFIG = WorkerConfig(
    batch_size=10,
    poll_interval=0.01,
    lock_timeout=timedelta(minutes=5),
    backoff_base=1,
    backoff_max=10,
)


def _worker(sessions: Sessions, settings: Settings, provider: LLMProvider) -> Worker:
    handlers = default_handlers(settings, provider, MockCRMAdapter(sessions))
    return Worker(sessions, handlers, CONFIG, worker_id="test-worker")


async def _queued_lead(sessions: Sessions, message: str) -> str:
    lead = await seed_lead(sessions, message)
    async with sessions() as session:
        queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=3)
        await session.commit()
    return lead.id


async def _status_and_events(sessions: Sessions, lead_id: str) -> tuple[str, list[str]]:
    async with sessions() as session:
        lead = await session.get(Lead, lead_id)
        assert lead is not None
        events = await session.scalars(
            select(LeadEvent.type).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.id)
        )
        return lead.status, list(events)


async def test_strong_lead_goes_from_received_to_synced(
    committed: Sessions, settings: Settings
) -> None:
    lead = await seed_lead(
        committed, "We need a Supabase app audited before launch. Budget around $3k."
    )
    async with committed() as session:
        queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=3)
        await session.commit()
    worker = _worker(committed, settings, MockProvider())

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


async def test_reviewed_lead_is_approved_by_a_person_and_synced(
    committed: Sessions, settings: Settings
) -> None:
    lead_id = await _queued_lead(committed, "hi")  # too short to judge: review
    worker = _worker(committed, settings, MockProvider())
    await worker.run_once()
    assert (await _status_and_events(committed, lead_id))[0] == LeadStatus.NEEDS_REVIEW

    async with committed() as session:
        lead = await session.get(Lead, lead_id)
        assert lead is not None
        await review.approve(
            session,
            lead.tenant_id,
            lead_id,
            actor="api_key:ab12cd34",
            note="Known customer",
            score_override=None,
            sync_max_attempts=5,
        )
    await worker.run_once()

    status, events = await _status_and_events(committed, lead_id)
    assert status == LeadStatus.SYNCED
    assert events[-3:] == ["approved", "syncing", "crm_synced"]


class RefusesOnce(MockProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def qualify(self, lead: LeadInput) -> QualificationResult:
        self.calls += 1
        if self.calls == 1:
            raise LLMRefusalError("declined")
        return await super().qualify(lead)


async def test_lead_failed_by_the_model_is_retried_and_scored(
    committed: Sessions, settings: Settings
) -> None:
    lead_id = await _queued_lead(
        committed, "We need a Supabase app audited before launch. Budget around $3k."
    )
    worker = _worker(committed, settings, RefusesOnce())
    await worker.run_once()
    assert (await _status_and_events(committed, lead_id))[0] == LeadStatus.NEEDS_REVIEW

    async with committed() as session:
        lead = await session.get(Lead, lead_id)
        assert lead is not None
        await review.retry(
            session,
            lead.tenant_id,
            lead_id,
            actor="api_key:ab12cd34",
            qualify_max_attempts=3,
            sync_max_attempts=5,
        )
    await worker.run_once()  # qualify again
    await worker.run_once()  # sync

    status, events = await _status_and_events(committed, lead_id)
    assert status == LeadStatus.SYNCED
    assert "retry_requested" in events
    assert events[-4:] == ["scored", "auto_approved", "syncing", "crm_synced"]
