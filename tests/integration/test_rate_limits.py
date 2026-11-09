import json
import time

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.webhook import WebhookProvider
from app.services import webhooks as webhook_service
from app.webhooks.signatures import sign_generic
from tests.integration.conftest import WEBHOOK_SERVER_KEY, MakeTenant, TenantWithKey

LEAD = {"message": "Need an audit"}


def _limit(app: FastAPI, settings: Settings, *, reads: int = 300, writes: int = 60) -> None:
    app.state.settings = settings.model_copy(
        update={"rate_limit_reads_per_minute": reads, "rate_limit_writes_per_minute": writes}
    )


async def test_writes_over_the_limit_get_429_with_retry_after(
    app: FastAPI, settings: Settings, client: AsyncClient, acme: TenantWithKey
) -> None:
    _limit(app, settings, writes=2)

    responses = [await client.post("/v1/leads", json=LEAD, headers=acme.headers) for _ in range(3)]

    assert [r.status_code for r in responses] == [202, 202, 429]
    assert responses[1].headers["X-RateLimit-Limit"] == "2"
    assert responses[1].headers["X-RateLimit-Remaining"] == "0"
    refused = responses[2]
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert refused.headers["X-RateLimit-Remaining"] == "0"


async def test_reads_and_writes_are_counted_separately(
    app: FastAPI, settings: Settings, client: AsyncClient, acme: TenantWithKey
) -> None:
    _limit(app, settings, reads=1, writes=1)

    write = await client.post("/v1/leads", json=LEAD, headers=acme.headers)
    read = await client.get("/v1/leads", headers=acme.headers)
    second_read = await client.get("/v1/leads", headers=acme.headers)

    assert (write.status_code, read.status_code, second_read.status_code) == (202, 200, 429)


async def test_each_key_has_its_own_allowance(
    app: FastAPI,
    settings: Settings,
    client: AsyncClient,
    acme: TenantWithKey,
    make_tenant: MakeTenant,
) -> None:
    _limit(app, settings, writes=1)
    other = await make_tenant("Other")

    await client.post("/v1/leads", json=LEAD, headers=acme.headers)
    acme_again = await client.post("/v1/leads", json=LEAD, headers=acme.headers)
    other_first = await client.post("/v1/leads", json=LEAD, headers=other.headers)

    assert (acme_again.status_code, other_first.status_code) == (429, 202)


async def test_review_actions_count_as_writes(
    app: FastAPI, settings: Settings, client: AsyncClient, acme: TenantWithKey
) -> None:
    _limit(app, settings, writes=1)
    await client.post("/v1/leads", json=LEAD, headers=acme.headers)

    response = await client.post("/v1/leads/ld_x/approve", headers=acme.headers)

    assert response.status_code == 429


async def test_webhooks_are_limited_per_endpoint_and_forgeries_do_not_count(
    app: FastAPI,
    settings: Settings,
    client: AsyncClient,
    acme: TenantWithKey,
    db_session: AsyncSession,
) -> None:
    _limit(app, settings, writes=1)
    created = await webhook_service.create_endpoint(
        db_session, acme.tenant.id, WebhookProvider.GENERIC, WEBHOOK_SERVER_KEY
    )
    await db_session.commit()
    url = f"/v1/webhooks/generic/{created.endpoint.id}"

    def signed(event_id: str, secret: str = created.secret) -> tuple[bytes, dict[str, str]]:
        body = json.dumps({"event_id": event_id, "lead": LEAD}).encode()
        ts = int(time.time())
        return body, {"X-Timestamp": str(ts), "X-Signature": sign_generic(secret, ts, body)}

    forged = [await client.post(url, content=b, headers=h) for b, h in [signed("e0", "x")] * 3]
    first = await client.post(url, content=(e1 := signed("e1"))[0], headers=e1[1])
    second = await client.post(url, content=(e2 := signed("e2"))[0], headers=e2[1])

    assert [r.status_code for r in forged] == [401, 401, 401]
    assert (first.status_code, second.status_code) == (202, 429)
    assert "Retry-After" in second.headers
