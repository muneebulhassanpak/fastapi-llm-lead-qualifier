"""A CRM stand-in that stores records in our own database. The default, and the demo.

It can fail on purpose so the retry and dead-letter paths can be shown live:

- `MOCK_CRM_FAIL_RATE` makes that share of calls fail with a 503.
- A lead created with `metadata.simulate_crm_failure` fails every time: `"unavailable"` (or
  `true`) with a 503, `"rate_limited"` with a 429 and Retry-After, `"invalid"` with a 422.
"""

import random
from typing import Final

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crm.base import CRMLead, CRMPermanentError, CRMResult, CRMTransientError
from app.db.models import MockCrmRecord
from app.domain.ids import new_id

SIMULATE_FAILURE_KEY: Final = "simulate_crm_failure"
RATE_LIMIT_RETRY_AFTER_SECONDS: Final = 30.0


class MockCRMAdapter:
    name = "mock"

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        fail_rate: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fail_rate = fail_rate
        self._rng = rng

    async def upsert_lead(self, lead: CRMLead) -> CRMResult:
        self._maybe_fail(lead)
        properties = {
            "email": lead.email,
            "name": lead.name,
            "company": lead.company,
            "intent": lead.intent,
            "source": lead.source,
            "score": lead.score,
            "reason": lead.reason,
        }
        insert_stmt = insert(MockCrmRecord).values(
            id=new_id("crm"),
            external_ref=lead.lead_id,
            tenant_id=lead.tenant_id,
            properties=properties,
            version=1,
        )
        upsert = insert_stmt.on_conflict_do_update(
            index_elements=[MockCrmRecord.external_ref],
            set_={
                "properties": insert_stmt.excluded.properties,
                "version": MockCrmRecord.version + 1,
                "updated_at": func.now(),
            },
        ).returning(MockCrmRecord.id)
        # Its own transaction, like a call to an outside system.
        async with self._sessionmaker() as session:
            external_id = (await session.execute(upsert)).scalar_one()
            await session.commit()
        return CRMResult(external_id=external_id)

    def _maybe_fail(self, lead: CRMLead) -> None:
        mode = lead.metadata.get(SIMULATE_FAILURE_KEY)
        if mode == "invalid":
            raise CRMPermanentError("422 Unprocessable Entity (simulated)", http_status=422)
        if mode == "rate_limited":
            raise CRMTransientError(
                "429 Too Many Requests (simulated)",
                http_status=429,
                retry_after=RATE_LIMIT_RETRY_AFTER_SECONDS,
            )
        if mode in (True, "unavailable") or (self._rng or random).random() < self._fail_rate:
            raise CRMTransientError("503 Service Unavailable (simulated)", http_status=503)
