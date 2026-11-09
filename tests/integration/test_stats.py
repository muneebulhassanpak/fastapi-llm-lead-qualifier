from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead
from app.domain.status import LeadStatus
from tests.integration.conftest import MakeLead, MakeTenant, TenantWithKey


async def test_stats_for_recent_leads(
    client: AsyncClient,
    acme: TenantWithKey,
    make_tenant: MakeTenant,
    make_lead: MakeLead,
    db_session: AsyncSession,
) -> None:
    await make_lead(acme.tenant, status=LeadStatus.SYNCED, score=80, source="tally")
    await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW, score=50, source="api")
    await make_lead(acme.tenant, status=LeadStatus.RECEIVED, source="api")
    old = await make_lead(acme.tenant, status=LeadStatus.SYNCED, score=10)
    await db_session.execute(
        update(Lead)
        .where(Lead.id == old.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=8))
    )
    await db_session.commit()
    other = await make_tenant("Other")
    await make_lead(other.tenant, status=LeadStatus.SYNCED, score=99)

    response = await client.get("/v1/stats", headers=acme.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7
    assert body["total"] == 3
    assert body["by_status"]["synced"] == 1
    assert body["by_status"]["needs_review"] == 1
    assert body["by_status"]["received"] == 1
    assert body["by_status"]["rejected"] == 0
    assert body["by_source"] == {"api": 2, "tally": 1}
    assert body["average_score"] == 65.0
    assert "x-ratelimit-limit" in response.headers


async def test_window_can_be_widened(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead, db_session: AsyncSession
) -> None:
    old = await make_lead(acme.tenant)
    await db_session.execute(
        update(Lead)
        .where(Lead.id == old.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=20))
    )
    await db_session.commit()

    week = await client.get("/v1/stats", headers=acme.headers)
    month = await client.get("/v1/stats", params={"days": 30}, headers=acme.headers)

    assert (week.json()["total"], month.json()["total"]) == (0, 1)
    assert week.json()["average_score"] is None


async def test_days_is_bounded(client: AsyncClient, acme: TenantWithKey) -> None:
    for days in (0, 91):
        response = await client.get("/v1/stats", params={"days": days}, headers=acme.headers)
        assert response.status_code == 422
