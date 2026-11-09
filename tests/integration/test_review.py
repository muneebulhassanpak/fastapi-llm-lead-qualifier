import asyncio
from collections.abc import Sequence

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, Lead, LeadEvent
from app.domain.jobs import JobStatus, JobType
from app.domain.status import LeadStatus
from app.errors import ConflictError, NotFoundError
from app.services import review
from app.services.events import record_event
from app.worker import queue
from tests.integration.conftest import seed_lead

Sessions = async_sessionmaker[AsyncSession]
ACTOR = "api_key:ab12cd34"


async def _lead(
    sessions: Sessions, status: LeadStatus, *, review_reason: str | None = None
) -> Lead:
    lead = await seed_lead(sessions, status=status)
    if review_reason:
        async with sessions() as session:
            record_event(session, lead, "sent_to_review", data={"reason": review_reason})
            await session.commit()
    return lead


async def _jobs(sessions: Sessions, lead_id: str) -> Sequence[Job]:
    async with sessions() as session:
        return (await session.scalars(select(Job).where(Job.lead_id == lead_id))).all()


async def _last_event(sessions: Sessions, lead_id: str) -> LeadEvent:
    async with sessions() as session:
        event = await session.scalar(
            select(LeadEvent).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.id.desc())
        )
        assert event is not None
        return event


async def _approve(sessions: Sessions, lead: Lead, *, score_override: int | None = None) -> Lead:
    async with sessions() as session:
        return await review.approve(
            session,
            lead.tenant_id,
            lead.id,
            actor=ACTOR,
            note="Good fit",
            score_override=score_override,
            sync_max_attempts=5,
        )


async def _retry(sessions: Sessions, lead: Lead) -> Lead:
    async with sessions() as session:
        return await review.retry(
            session,
            lead.tenant_id,
            lead.id,
            actor=ACTOR,
            qualify_max_attempts=3,
            sync_max_attempts=5,
        )


async def test_approve_moves_to_approved_and_queues_sync(committed: Sessions) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW)

    approved = await _approve(committed, lead)

    assert approved.status == LeadStatus.APPROVED
    [job] = await _jobs(committed, lead.id)
    assert (job.type, job.max_attempts) == (JobType.SYNC_LEAD, 5)
    event = await _last_event(committed, lead.id)
    assert (event.type, event.actor) == ("approved", ACTOR)
    assert event.data == {"from": "needs_review", "to": "approved", "note": "Good fit"}


async def test_approve_records_a_score_override(committed: Sessions) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW)
    async with committed() as session:
        await session.execute(update(Lead).where(Lead.id == lead.id).values(score=45))
        await session.commit()

    approved = await _approve(committed, lead, score_override=80)

    assert approved.score == 80
    event = await _last_event(committed, lead.id)
    assert (event.data["score_before"], event.data["score_after"]) == (45, 80)


@pytest.mark.parametrize("status", [s for s in LeadStatus if s is not LeadStatus.NEEDS_REVIEW])
async def test_approve_and_reject_need_review_status(
    committed: Sessions, status: LeadStatus
) -> None:
    lead = await _lead(committed, status)

    with pytest.raises(ConflictError) as caught:
        await _approve(committed, lead)
    assert caught.value.details == {"status": status.value, "allowed_from": ["needs_review"]}
    with pytest.raises(ConflictError):
        async with committed() as session:
            await review.reject(session, lead.tenant_id, lead.id, actor=ACTOR, note=None)
    assert await _jobs(committed, lead.id) == []


async def test_concurrent_approves_give_one_sync_job(committed: Sessions) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW)

    results = await asyncio.gather(
        *(_approve(committed, lead) for _ in range(5)), return_exceptions=True
    )

    assert sum(isinstance(r, Lead) for r in results) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 4
    assert len(await _jobs(committed, lead.id)) == 1


async def test_reject(committed: Sessions) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW)

    async with committed() as session:
        rejected = await review.reject(
            session, lead.tenant_id, lead.id, actor=ACTOR, note="Not our market"
        )

    assert rejected.status == LeadStatus.REJECTED
    event = await _last_event(committed, lead.id)
    assert (event.type, event.actor, event.data["note"]) == ("rejected", ACTOR, "Not our market")
    assert await _jobs(committed, lead.id) == []


async def test_other_tenants_lead_is_not_found(committed: Sessions) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW)

    with pytest.raises(NotFoundError):
        async with committed() as session:
            await review.reject(session, "tn_other", lead.id, actor=ACTOR, note=None)


async def test_retry_sync_failed_lead(committed: Sessions) -> None:
    lead = await _lead(committed, LeadStatus.SYNC_FAILED)

    retried = await _retry(committed, lead)

    assert retried.status == LeadStatus.SYNCING
    [job] = await _jobs(committed, lead.id)
    assert job.type == JobType.SYNC_LEAD
    event = await _last_event(committed, lead.id)
    assert (event.type, event.data["retry"]) == ("retry_requested", "sync")


@pytest.mark.parametrize(
    "reason", ["llm_unavailable", "llm_invalid_output", "llm_refused", "llm_daily_cap"]
)
async def test_retry_review_caused_by_the_model(committed: Sessions, reason: str) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW, review_reason=reason)

    retried = await _retry(committed, lead)

    assert retried.status == LeadStatus.QUALIFYING
    [job] = await _jobs(committed, lead.id)
    assert (job.type, job.max_attempts) == (JobType.QUALIFY_LEAD, 3)


@pytest.mark.parametrize("reason", ["borderline_score", "low_confidence", None])
async def test_review_caused_by_the_lead_cannot_be_retried(
    committed: Sessions, reason: str | None
) -> None:
    lead = await _lead(committed, LeadStatus.NEEDS_REVIEW, review_reason=reason)

    with pytest.raises(ConflictError):
        await _retry(committed, lead)


@pytest.mark.parametrize("status", [LeadStatus.RECEIVED, LeadStatus.QUALIFYING])
async def test_retry_lead_stuck_without_a_job(committed: Sessions, status: LeadStatus) -> None:
    lead = await _lead(committed, status)

    retried = await _retry(committed, lead)

    assert retried.status == status
    [job] = await _jobs(committed, lead.id)
    assert job.type == JobType.QUALIFY_LEAD
    assert (await _last_event(committed, lead.id)).type == "retry_requested"


@pytest.mark.parametrize("job_status", [JobStatus.QUEUED, JobStatus.RUNNING])
async def test_lead_with_a_live_job_cannot_be_retried(
    committed: Sessions, job_status: JobStatus
) -> None:
    lead = await _lead(committed, LeadStatus.QUALIFYING)
    async with committed() as session:
        job = queue.enqueue(session, JobType.QUALIFY_LEAD, lead.id, max_attempts=3)
        job.status = job_status
        await session.commit()

    with pytest.raises(ConflictError):
        await _retry(committed, lead)


@pytest.mark.parametrize(
    "status",
    [LeadStatus.APPROVED, LeadStatus.SYNCING, LeadStatus.SYNCED, LeadStatus.REJECTED],
)
async def test_nothing_to_retry(committed: Sessions, status: LeadStatus) -> None:
    lead = await _lead(committed, status)

    with pytest.raises(ConflictError) as caught:
        await _retry(committed, lead)
    assert caught.value.details == {"status": status.value}
