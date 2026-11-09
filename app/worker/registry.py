"""Which handler runs each job type."""

from collections.abc import Mapping

from app.config import Settings
from app.crm.base import CRMAdapter
from app.domain.jobs import JobType
from app.domain.qualification import Thresholds
from app.llm.base import LLMProvider
from app.worker.handlers import JobHandler
from app.worker.qualify import QualifyLeadHandler
from app.worker.sync import SyncLeadHandler


def default_handlers(
    settings: Settings, provider: LLMProvider, crm: CRMAdapter
) -> Mapping[JobType, JobHandler]:
    thresholds = Thresholds(
        auto_approve_score=settings.auto_approve_score,
        disqualify_score=settings.disqualify_score,
        min_confidence=settings.min_confidence,
    )
    return {
        JobType.QUALIFY_LEAD: QualifyLeadHandler(
            provider,
            thresholds,
            daily_cap=settings.llm_daily_cap,
            sync_max_attempts=settings.crm_max_attempts,
        ),
        JobType.SYNC_LEAD: SyncLeadHandler(crm),
    }
