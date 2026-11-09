"""The `qualify_lead` job: score a lead with the LLM and route it."""

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Lead
from app.domain.qualification import Route, Thresholds, decide
from app.domain.status import LeadStatus
from app.llm.base import (
    LeadInput,
    LLMError,
    LLMInvalidOutputError,
    LLMProvider,
    LLMTransientError,
    QualificationResult,
)
from app.logging import get_logger
from app.services import llm_usage
from app.services.events import record_event, transition
from app.worker.handlers import JobHandler

log = get_logger(__name__)

_ROUTE_TO_STATUS = {
    Route.APPROVE: (LeadStatus.APPROVED, "auto_approved"),
    Route.DISQUALIFY: (LeadStatus.DISQUALIFIED, "auto_disqualified"),
}


class QualifyLeadHandler(JobHandler):
    """Runs in three steps so the slow model call holds no lock or open transaction:

    1. lock the lead, mark it `qualifying`, reserve a call against the daily cap, commit;
    2. call the model (one extra try if the output doesn't validate);
    3. store the result and route the lead, committed together with the job's completion.

    Transient provider errors propagate, so the job is retried with backoff; when it runs out
    of attempts the lead goes to review. Any other failure sends it to review at once.
    """

    def __init__(self, provider: LLMProvider, thresholds: Thresholds, daily_cap: int) -> None:
        self._provider = provider
        self._thresholds = thresholds
        self._daily_cap = daily_cap

    async def run(self, session: AsyncSession, job: Job) -> None:
        lead = await _lock_lead(session, job.lead_id)
        if lead is None or lead.status not in (LeadStatus.RECEIVED, LeadStatus.QUALIFYING):
            return  # deleted, or already handled by an earlier run: nothing to do

        if lead.status == LeadStatus.RECEIVED:
            transition(session, lead, LeadStatus.QUALIFYING, data={"job_id": job.id})
        lead_input = LeadInput(
            message=lead.message, source=lead.source, name=lead.name, email=lead.email
        )
        tenant_id, day = lead.tenant_id, _today()
        if not await llm_usage.reserve_call(session, tenant_id, day, self._daily_cap):
            _send_to_review(session, lead, "llm_daily_cap")
            return
        await session.commit()

        try:
            result = await self._qualify(session, tenant_id, day, lead_input)
        except LLMTransientError:
            raise
        except LLMError as exc:
            lead = await _lock_lead(session, job.lead_id)
            if lead is not None and lead.status == LeadStatus.QUALIFYING:
                _send_to_review(session, lead, exc.code, detail=str(exc))
            return

        await llm_usage.record_tokens(
            session, tenant_id, day, result.input_tokens, result.output_tokens
        )
        lead = await _lock_lead(session, job.lead_id)
        if lead is None or lead.status != LeadStatus.QUALIFYING:
            return  # changed while the model was running; keep what is there
        self._apply(session, lead, result)

    async def on_dead_letter(self, session: AsyncSession, job: Job) -> None:
        lead = await _lock_lead(session, job.lead_id)
        if lead is None:
            return
        if lead.status == LeadStatus.RECEIVED:
            transition(session, lead, LeadStatus.QUALIFYING, data={"job_id": job.id})
        if lead.status == LeadStatus.QUALIFYING:
            _send_to_review(session, lead, LLMTransientError.code, detail=job.last_error)

    async def _qualify(
        self, session: AsyncSession, tenant_id: str, day: date, lead_input: LeadInput
    ) -> QualificationResult:
        try:
            return await self._provider.qualify(lead_input)
        except LLMInvalidOutputError as first_error:
            log.warning("llm_invalid_output_retrying", error=str(first_error)[:200])
            if not await llm_usage.reserve_call(session, tenant_id, day, self._daily_cap):
                raise
            await session.commit()
            return await self._provider.qualify(lead_input)

    def _apply(self, session: AsyncSession, lead: Lead, result: QualificationResult) -> None:
        q = result.qualification
        lead.score = q.score
        lead.confidence = q.confidence
        lead.reason = q.reason
        lead.extracted = q.model_dump(
            mode="json", include={"name", "company", "intent", "budget_hint", "urgency"}
        )
        record_event(
            session,
            lead,
            "scored",
            data={
                "score": q.score,
                "confidence": q.confidence,
                "provider": result.provider,
                "model": result.model,
                "prompt_version": result.prompt_version,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
        decision = decide(q.score, q.confidence, self._thresholds)
        if decision.route is Route.REVIEW:
            _send_to_review(session, lead, decision.review_reason or "review")
        else:
            status, event = _ROUTE_TO_STATUS[decision.route]
            transition(session, lead, status, event_type=event)
        log.info(
            "lead_qualified",
            lead_id=lead.id,
            score=q.score,
            confidence=q.confidence,
            route=decision.route,
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )


def _today() -> date:
    return datetime.now(UTC).date()


async def _lock_lead(session: AsyncSession, lead_id: str) -> Lead | None:
    return await session.get(Lead, lead_id, with_for_update=True, populate_existing=True)


def _send_to_review(
    session: AsyncSession, lead: Lead, reason: str, *, detail: str | None = None
) -> None:
    data = {"reason": reason} | ({"detail": detail[:300]} if detail else {})
    transition(session, lead, LeadStatus.NEEDS_REVIEW, event_type="sent_to_review", data=data)
