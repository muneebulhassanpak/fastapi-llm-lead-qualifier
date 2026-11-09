import random
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crm.base import CRMLead, CRMPermanentError, CRMTransientError
from app.crm.mock import MockCRMAdapter
from app.db.models import MockCrmRecord

Sessions = async_sessionmaker[AsyncSession]


def _lead(lead_id: str = "ld_1", **changes: Any) -> CRMLead:
    fields: dict[str, Any] = {
        "lead_id": lead_id,
        "tenant_id": "tn_1",
        "source": "api",
        "email": "sam@initech.example",
        "name": "Sam",
        "company": "Initech",
        "intent": "API integration",
        "score": 85,
        "reason": "Clear project.",
    }
    return CRMLead(**(fields | changes))


async def _records(sessions: Sessions) -> list[MockCrmRecord]:
    async with sessions() as session:
        return list((await session.scalars(select(MockCrmRecord))).all())


async def test_upsert_creates_a_record(committed: Sessions) -> None:
    result = await MockCRMAdapter(committed).upsert_lead(_lead())

    [record] = await _records(committed)
    assert result.external_id == record.id
    assert record.id.startswith("crm_")
    assert record.external_ref == "ld_1"
    assert record.properties["email"] == "sam@initech.example"
    assert record.version == 1


async def test_repeated_upsert_updates_the_same_record(committed: Sessions) -> None:
    adapter = MockCRMAdapter(committed)

    first = await adapter.upsert_lead(_lead(score=70))
    second = await adapter.upsert_lead(_lead(score=90))

    assert first == second
    [record] = await _records(committed)
    assert record.properties["score"] == 90
    assert record.version == 2


async def test_different_leads_get_different_records(committed: Sessions) -> None:
    adapter = MockCRMAdapter(committed)

    a = await adapter.upsert_lead(_lead("ld_a"))
    b = await adapter.upsert_lead(_lead("ld_b"))

    assert a != b
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(MockCrmRecord)) == 2


@pytest.mark.parametrize("mode", [True, "unavailable"])
async def test_simulated_outage_is_transient(committed: Sessions, mode: object) -> None:
    with pytest.raises(CRMTransientError) as caught:
        await MockCRMAdapter(committed).upsert_lead(_lead(metadata={"simulate_crm_failure": mode}))

    assert caught.value.http_status == 503
    assert caught.value.retry_after is None
    assert await _records(committed) == []


async def test_simulated_rate_limit_carries_retry_after(committed: Sessions) -> None:
    with pytest.raises(CRMTransientError) as caught:
        await MockCRMAdapter(committed).upsert_lead(
            _lead(metadata={"simulate_crm_failure": "rate_limited"})
        )

    assert caught.value.http_status == 429
    assert caught.value.retry_after == 30


async def test_simulated_validation_error_is_permanent(committed: Sessions) -> None:
    with pytest.raises(CRMPermanentError) as caught:
        await MockCRMAdapter(committed).upsert_lead(
            _lead(metadata={"simulate_crm_failure": "invalid"})
        )

    assert caught.value.http_status == 422


async def test_fail_rate_fails_that_share_of_calls(committed: Sessions) -> None:
    adapter = MockCRMAdapter(committed, fail_rate=0.5, rng=random.Random(7))
    outcomes = []
    for i in range(40):
        try:
            await adapter.upsert_lead(_lead(f"ld_{i}"))
            outcomes.append(True)
        except CRMTransientError:
            outcomes.append(False)

    assert 10 < outcomes.count(False) < 30
    assert len(await _records(committed)) == outcomes.count(True)
