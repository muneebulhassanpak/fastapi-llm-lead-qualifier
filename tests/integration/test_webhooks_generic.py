import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Lead, LeadEvent
from app.db.models.webhook import WebhookProvider
from app.services import webhooks as webhook_service
from app.webhooks.signatures import sign_generic
from tests.integration.conftest import WEBHOOK_SERVER_KEY, TenantWithKey

PAYLOAD = {
    "event_id": "evt_001",
    "lead": {
        "message": "We need our booking form connected to HubSpot. Budget $4k.",
        "email": "dana@brightline.example",
        "name": "Dana",
    },
}


class Endpoint:
    def __init__(self, endpoint_id: str, secret: str, provider: str = "generic") -> None:
        self.id = endpoint_id
        self.secret = secret
        self.url = f"/v1/webhooks/{provider}/{endpoint_id}"


async def _endpoint(
    session: AsyncSession, tenant: TenantWithKey, provider: WebhookProvider
) -> Endpoint:
    created = await webhook_service.create_endpoint(
        session, tenant.tenant.id, provider, WEBHOOK_SERVER_KEY
    )
    await session.commit()
    return Endpoint(created.endpoint.id, created.secret, provider)


@pytest.fixture
async def endpoint(db_session: AsyncSession, acme: TenantWithKey) -> Endpoint:
    return await _endpoint(db_session, acme, WebhookProvider.GENERIC)


def _signed(
    secret: str, payload: Any = PAYLOAD, *, timestamp: int | None = None
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    ts = timestamp if timestamp is not None else int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": str(ts),
        "X-Signature": sign_generic(secret, ts, body),
    }
    return body, headers


async def _lead_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Lead)) or 0


async def test_valid_delivery_creates_a_lead(
    client: AsyncClient, endpoint: Endpoint, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    body, headers = _signed(endpoint.secret)

    response = await client.post(endpoint.url, content=body, headers=headers)

    assert response.status_code == 202, response.text
    lead = await db_session.get(Lead, response.json()["id"])
    assert lead is not None
    assert (lead.tenant_id, lead.source, lead.email) == (
        acme.tenant.id,
        "webhook",
        "dana@brightline.example",
    )
    event = await db_session.scalar(select(LeadEvent).where(LeadEvent.lead_id == lead.id))
    assert event is not None
    assert event.actor == f"webhook:{endpoint.id}"
    assert await db_session.scalar(select(func.count()).select_from(Job)) == 1


async def test_source_can_be_set_by_the_sender(
    client: AsyncClient, endpoint: Endpoint, db_session: AsyncSession
) -> None:
    payload = {"event_id": "evt_src", "lead": {"message": "Hello there", "source": "zapier"}}
    body, headers = _signed(endpoint.secret, payload)

    response = await client.post(endpoint.url, content=body, headers=headers)

    lead = await db_session.get(Lead, response.json()["id"])
    assert lead is not None
    assert lead.source == "zapier"


async def test_repeated_delivery_returns_the_same_lead(
    client: AsyncClient, endpoint: Endpoint, db_session: AsyncSession
) -> None:
    body, headers = _signed(endpoint.secret)

    first = await client.post(endpoint.url, content=body, headers=headers)
    second = await client.post(endpoint.url, content=body, headers=headers)

    assert (first.status_code, second.status_code) == (202, 200)
    assert first.json()["id"] == second.json()["id"]
    assert await _lead_count(db_session) == 1


async def test_event_id_reused_with_another_payload_is_409(
    client: AsyncClient, endpoint: Endpoint
) -> None:
    other = {"event_id": "evt_001", "lead": {"message": "Something else entirely"}}

    first_body, first_headers = _signed(endpoint.secret)
    await client.post(endpoint.url, content=first_body, headers=first_headers)
    body, headers = _signed(endpoint.secret, other)
    response = await client.post(endpoint.url, content=body, headers=headers)

    assert response.status_code == 409


def _tamper(kind: str, secret: str) -> tuple[bytes, dict[str, str]]:
    # Well past the 300 s window: the exact edges are unit-tested with a fixed clock.
    now = int(time.time())
    match kind:
        case "wrong_secret":
            return _signed("whsec_not_the_secret")
        case "body_changed":
            body, headers = _signed(secret)
            return body.replace(b"$4k", b"$40k"), headers
        case "expired":
            return _signed(secret, timestamp=now - 400)
        case "future":
            return _signed(secret, timestamp=now + 400)
        case "timestamp_changed":
            body, headers = _signed(secret, timestamp=now)
            return body, headers | {"X-Timestamp": str(now + 1)}
        case "no_signature":
            body, headers = _signed(secret)
            del headers["X-Signature"]
            return body, headers
        case "no_timestamp":
            body, headers = _signed(secret)
            del headers["X-Timestamp"]
            return body, headers
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "wrong_secret",
        "body_changed",
        "expired",
        "future",
        "timestamp_changed",
        "no_signature",
        "no_timestamp",
    ],
)
async def test_bad_signatures_are_401_and_create_nothing(
    client: AsyncClient, endpoint: Endpoint, db_session: AsyncSession, kind: str
) -> None:
    body, headers = _tamper(kind, endpoint.secret)

    response = await client.post(endpoint.url, content=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "invalid_signature",
        "message": "Invalid webhook signature.",
        "request_id": response.headers["x-request-id"],
    }
    assert await _lead_count(db_session) == 0


async def test_unknown_revoked_and_wrong_provider_endpoints_are_401(
    client: AsyncClient, endpoint: Endpoint, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    body, headers = _signed(endpoint.secret)
    tally = await _endpoint(db_session, acme, WebhookProvider.TALLY)

    unknown = await client.post("/v1/webhooks/generic/whk_nope", content=body, headers=headers)
    wrong_provider = await client.post(
        f"/v1/webhooks/generic/{tally.id}", content=body, headers=_signed(tally.secret)[1]
    )
    await webhook_service.revoke_endpoint(db_session, endpoint.id)
    await db_session.commit()
    revoked = await client.post(endpoint.url, content=body, headers=headers)

    assert [r.status_code for r in (unknown, wrong_provider, revoked)] == [401, 401, 401]


async def test_signed_but_unusable_payload_is_422(client: AsyncClient, endpoint: Endpoint) -> None:
    for payload in ({"event_id": "evt_x", "lead": {}}, {"lead": {"message": "Hi"}}, ["nope"]):
        body, headers = _signed(endpoint.secret, payload)
        response = await client.post(endpoint.url, content=body, headers=headers)
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_error"


async def test_oversized_body_is_413(client: AsyncClient, endpoint: Endpoint) -> None:
    payload = {"event_id": "evt_big", "lead": {"message": "x" * 70_000}}
    body, headers = _signed(endpoint.secret, payload)

    response = await client.post(endpoint.url, content=body, headers=headers)

    assert response.status_code == 413


async def test_oversized_body_without_content_length_is_413(
    client: AsyncClient, endpoint: Endpoint
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(70):
            yield b"x" * 1024

    response = await client.post(endpoint.url, content=chunks(), headers={"X-Timestamp": "1"})

    assert response.status_code == 413


async def test_unknown_provider_is_422(client: AsyncClient) -> None:
    response = await client.post("/v1/webhooks/zapier/whk_x", content=b"{}")

    assert response.status_code == 422
