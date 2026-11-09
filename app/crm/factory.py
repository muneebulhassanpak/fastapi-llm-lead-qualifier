from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import CRMMode, Settings
from app.crm.base import CRMAdapter
from app.crm.mock import MockCRMAdapter


def build_crm_adapter(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> CRMAdapter:
    """The adapter selected by CRM_MODE."""
    match settings.crm_mode:
        case CRMMode.MOCK:
            return MockCRMAdapter(sessionmaker, fail_rate=settings.mock_crm_fail_rate)
