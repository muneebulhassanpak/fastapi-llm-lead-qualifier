"""Which handler runs each job type."""

from collections.abc import Mapping

from app.config import Settings
from app.domain.jobs import JobType
from app.domain.qualification import Thresholds
from app.llm.base import LLMProvider
from app.worker.handlers import JobHandler
from app.worker.qualify import QualifyLeadHandler


def default_handlers(settings: Settings, provider: LLMProvider) -> Mapping[JobType, JobHandler]:
    thresholds = Thresholds(
        auto_approve_score=settings.auto_approve_score,
        disqualify_score=settings.disqualify_score,
        min_confidence=settings.min_confidence,
    )
    return {
        JobType.QUALIFY_LEAD: QualifyLeadHandler(provider, thresholds, settings.llm_daily_cap),
    }
