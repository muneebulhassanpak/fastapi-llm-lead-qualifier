from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, SyncAttempt
from app.domain.status import LeadStatus
from tests.integration.conftest import MakeLead, MakeTenant, TenantWithKey


async def test_get_lead_returns_detail_with_events(
    client: AsyncClient, acme: TenantWithKey
) -> None:
    created = await client.post(
        "/v1/leads",
        json={"message": "Need an audit", "metadata": {"utm": "ads"}},
        headers=acme.headers,
    )
    lead_id = created.json()["id"]

    response = await client.get(f"/v1/leads/{lead_id}", headers=acme.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == lead_id
    assert body["status"] == "received"
    assert body["message"] == "Need an audit"
    assert body["metadata"] == {"utm": "ads"}
    assert body["score"] is None
    assert body["crm"] == {
        "provider": None,
        "external_id": None,
        "attempts": 0,
        "last_error": None,
    }
    assert [e["type"] for e in body["events"]] == ["received"]


async def test_get_lead_shows_crm_sync_state(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead, db_session: AsyncSession
) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.SYNC_FAILED)
    for error in ("CRMTransientError: 503 first", "CRMTransientError: 503 second"):
        db_session.add(
            SyncAttempt(
                lead_id=lead.id,
                provider="mock",
                status="failed",
                http_status=503,
                error=error,
                duration_ms=12,
            )
        )
    await db_session.commit()

    response = await client.get(f"/v1/leads/{lead.id}", headers=acme.headers)

    assert response.json()["crm"] == {
        "provider": "mock",
        "external_id": None,
        "attempts": 2,
        "last_error": "CRMTransientError: 503 second",
    }


async def test_unknown_lead_is_404(client: AsyncClient, acme: TenantWithKey) -> None:
    response = await client.get("/v1/leads/ld_01JC9ZK3QX4V8R2M6N1P5T7W9Y", headers=acme.headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_tenant_cannot_read_another_tenants_lead(
    client: AsyncClient, acme: TenantWithKey, make_tenant: MakeTenant, make_lead: MakeLead
) -> None:
    globex = await make_tenant("Globex")
    secret = await make_lead(globex.tenant)

    response = await client.get(f"/v1/leads/{secret.id}", headers=acme.headers)

    # 404, not 403: don't reveal that the ID exists.
    assert response.status_code == 404


async def test_list_only_shows_own_leads(
    client: AsyncClient, acme: TenantWithKey, make_tenant: MakeTenant, make_lead: MakeLead
) -> None:
    globex = await make_tenant("Globex")
    mine = await make_lead(acme.tenant)
    await make_lead(globex.tenant)

    response = await client.get("/v1/leads", headers=acme.headers)

    assert [lead["id"] for lead in response.json()["data"]] == [mine.id]


async def test_list_paginates_newest_first(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    created = [await make_lead(acme.tenant, message=f"#{i}") for i in range(5)]
    expected = sorted((lead.id for lead in created), reverse=True)

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/v1/leads", params=params, headers=acme.headers)).json()
        seen += [lead["id"] for lead in body["data"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == expected
    assert pages == 3


async def test_list_filters(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead, db_session: AsyncSession
) -> None:
    review = await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW, score=55)
    synced = await make_lead(acme.tenant, status=LeadStatus.SYNCED, score=90, source="webhook")
    old = await make_lead(acme.tenant, status=LeadStatus.DISQUALIFIED, score=10)
    await db_session.execute(
        update(Lead)
        .where(Lead.id == old.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=3))
    )
    await db_session.commit()

    async def ids(**params: str | int) -> set[str]:
        body = (await client.get("/v1/leads", params=params, headers=acme.headers)).json()
        return {lead["id"] for lead in body["data"]}

    assert await ids(status="needs_review") == {review.id}
    assert await ids(source="webhook") == {synced.id}
    assert await ids(min_score=50) == {review.id, synced.id}
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert await ids(created_after=since) == {review.id, synced.id}


async def test_invalid_filters_are_rejected(client: AsyncClient, acme: TenantWithKey) -> None:
    for params in ({"status": "nope"}, {"limit": 0}, {"limit": 101}, {"min_score": 101}):
        response = await client.get("/v1/leads", params=params, headers=acme.headers)
        assert response.status_code == 422, params


async def test_invalid_cursor_is_400(client: AsyncClient, acme: TenantWithKey) -> None:
    response = await client.get("/v1/leads", params={"cursor": "garbage"}, headers=acme.headers)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid cursor."
