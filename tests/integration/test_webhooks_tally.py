from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead
from app.db.models.webhook import WebhookProvider
from app.services import webhooks as webhook_service
from app.webhooks.signatures import sign_tally
from tests.integration.conftest import WEBHOOK_SERVER_KEY, TenantWithKey

BODY = (Path(__file__).parents[1] / "fixtures" / "tally_form_response.json").read_bytes()


async def test_signed_tally_submission_creates_one_lead(
    client: AsyncClient, acme: TenantWithKey, db_session: AsyncSession
) -> None:
    created = await webhook_service.create_endpoint(
        db_session, acme.tenant.id, WebhookProvider.TALLY, WEBHOOK_SERVER_KEY
    )
    await db_session.commit()
    url = f"/v1/webhooks/tally/{created.endpoint.id}"
    headers = {
        "Content-Type": "application/json",
        "Tally-Signature": sign_tally(created.secret, BODY),
    }

    first = await client.post(url, content=BODY, headers=headers)
    retry = await client.post(url, content=BODY, headers=headers)
    forged = await client.post(url, content=BODY, headers={"Tally-Signature": "AAAA"})

    assert (first.status_code, retry.status_code, forged.status_code) == (202, 200, 401)
    assert retry.json()["id"] == first.json()["id"]
    lead = await db_session.get(Lead, first.json()["id"])
    assert lead is not None
    assert (lead.source, lead.email) == ("tally", "dana@brightline.example")
