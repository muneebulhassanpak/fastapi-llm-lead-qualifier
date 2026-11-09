from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey, Lead, LeadEvent
from app.services import leads as lead_service
from tests.integration.conftest import MakeTenant, TenantWithKey

ENQUIRY = {
    "source": "contact_form",
    "name": "Sara Khan",
    "email": "sara@acme.example",
    "message": "We need a Supabase app audited before launch. Budget around $3k.",
    "metadata": {"page": "/pricing"},
}


async def _count_leads(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Lead)) or 0


async def test_create_returns_202_and_stores_lead(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    response = await client.post("/v1/leads", json=ENQUIRY, headers=acme.headers)

    assert response.status_code == 202
    body = response.json()
    assert body["id"].startswith("ld_")
    assert body["status"] == "received"

    lead = await db_session.get(Lead, body["id"])
    assert lead is not None
    assert lead.tenant_id == acme.tenant.id
    assert lead.email == "sara@acme.example"
    assert lead.extra_metadata == {"page": "/pricing"}


async def test_create_records_received_event(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    lead_id = (await client.post("/v1/leads", json=ENQUIRY, headers=acme.headers)).json()["id"]

    events = list(await db_session.scalars(select(LeadEvent).where(LeadEvent.lead_id == lead_id)))
    assert [(e.type, e.actor) for e in events] == [("received", f"api_key:{acme.prefix}")]


async def test_only_message_is_required(client: AsyncClient, acme: TenantWithKey) -> None:
    response = await client.post("/v1/leads", json={"message": "Hi"}, headers=acme.headers)

    assert response.status_code == 202


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "message"),
        ({"message": "   "}, "message"),
        ({"message": "x" * 5001}, "message"),
        ({"message": "Hi", "email": "not-an-email"}, "email"),
        ({"message": "Hi", "source": "Contact Form"}, "source"),
        ({"message": "Hi", "metadata": {"blob": "x" * 5000}}, "metadata"),
        ({"message": "Hi", "unexpected": True}, "unexpected"),
    ],
)
async def test_invalid_input_is_rejected(
    client: AsyncClient, acme: TenantWithKey, payload: dict[str, Any], field: str
) -> None:
    response = await client.post("/v1/leads", json=payload, headers=acme.headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"][-1] == field


async def test_idempotent_replay_returns_original_lead(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    headers = {**acme.headers, "Idempotency-Key": "order-42"}

    first = await client.post("/v1/leads", json=ENQUIRY, headers=headers)
    second = await client.post("/v1/leads", json=ENQUIRY, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == first.json()
    assert await _count_leads(db_session) == 1


async def test_idempotency_key_reused_with_different_body_conflicts(
    client: AsyncClient, acme: TenantWithKey
) -> None:
    headers = {**acme.headers, "Idempotency-Key": "order-42"}
    await client.post("/v1/leads", json=ENQUIRY, headers=headers)

    response = await client.post(
        "/v1/leads", json={**ENQUIRY, "message": "Something else"}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"


async def test_idempotency_keys_are_scoped_per_tenant(
    client: AsyncClient, acme: TenantWithKey, make_tenant: MakeTenant
) -> None:
    globex = await make_tenant("Globex")

    a = await client.post(
        "/v1/leads", json=ENQUIRY, headers={**acme.headers, "Idempotency-Key": "k"}
    )
    b = await client.post(
        "/v1/leads", json=ENQUIRY, headers={**globex.headers, "Idempotency-Key": "k"}
    )

    assert a.status_code == b.status_code == 202
    assert a.json()["id"] != b.json()["id"]


async def test_expired_idempotency_key_creates_new_lead(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    headers = {**acme.headers, "Idempotency-Key": "order-42"}
    first = await client.post("/v1/leads", json=ENQUIRY, headers=headers)
    await db_session.execute(
        update(IdempotencyKey).values(created_at=datetime.now(UTC) - timedelta(hours=25))
    )
    await db_session.commit()

    second = await client.post("/v1/leads", json=ENQUIRY, headers=headers)

    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]


@pytest.mark.parametrize("bad_key", ["", "has space", "x" * 256])
async def test_invalid_idempotency_key_is_rejected(
    client: AsyncClient, acme: TenantWithKey, bad_key: str
) -> None:
    response = await client.post(
        "/v1/leads", json=ENQUIRY, headers={**acme.headers, "Idempotency-Key": bad_key}
    )

    assert response.status_code == 422


async def test_concurrent_duplicate_returns_the_winner(
    acme: TenantWithKey, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests with the same key race: the loser's insert hits the unique constraint."""
    new = lead_service.NewLead(message="Hi", source="api", name=None, email=None, metadata={})
    winner = await lead_service.create_lead(
        db_session, acme.tenant.id, new, actor="test", idempotency_key="race"
    )

    # Make the loser miss the winner on its first lookup, as if both checked at the same time.
    real_find = lead_service._find_replay
    calls = 0

    async def find_once_missing(*args: Any, **kwargs: Any) -> Lead | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_find(*args, **kwargs)

    monkeypatch.setattr(lead_service, "_find_replay", find_once_missing)
    loser = await lead_service.create_lead(
        db_session, acme.tenant.id, new, actor="test", idempotency_key="race"
    )

    assert loser.replayed
    assert loser.lead.id == winner.lead.id
    assert await _count_leads(db_session) == 1
