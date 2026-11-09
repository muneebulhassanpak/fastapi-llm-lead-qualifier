from httpx import AsyncClient

from app.domain.status import LeadStatus
from tests.integration.conftest import MakeLead, MakeTenant, TenantWithKey


async def test_approve(client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW, score=50)

    response = await client.post(
        f"/v1/leads/{lead.id}/approve",
        json={"note": "Fits our Q3 focus.", "score_override": 75},
        headers=acme.headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["score"]) == ("approved", 75)
    event = body["events"][-1]
    assert event["type"] == "approved"
    assert event["actor"] == f"api_key:{acme.prefix}"
    assert event["data"] == {
        "from": "needs_review",
        "to": "approved",
        "note": "Fits our Q3 focus.",
        "score_before": 50,
        "score_after": 75,
    }


async def test_approve_and_reject_need_no_body(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    a = await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW)
    b = await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW)

    approved = await client.post(f"/v1/leads/{a.id}/approve", headers=acme.headers)
    rejected = await client.post(f"/v1/leads/{b.id}/reject", headers=acme.headers)

    assert (approved.status_code, approved.json()["status"]) == (200, "approved")
    assert (rejected.status_code, rejected.json()["status"]) == (200, "rejected")
    assert rejected.json()["events"][-1]["data"]["note"] is None


async def test_reject_with_note(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW)

    response = await client.post(
        f"/v1/leads/{lead.id}/reject", json={"note": "Outside our market."}, headers=acme.headers
    )

    assert response.json()["events"][-1]["data"]["note"] == "Outside our market."


async def test_wrong_status_is_409_with_current_status(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.SYNCED)

    response = await client.post(f"/v1/leads/{lead.id}/approve", headers=acme.headers)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_status"
    assert error["message"] == "Cannot approve a lead in status 'synced'."
    assert error["details"] == {"status": "synced", "allowed_from": ["needs_review"]}


async def test_retry_sync_failed(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.SYNC_FAILED)

    response = await client.post(f"/v1/leads/{lead.id}/retry", headers=acme.headers)

    assert response.status_code == 200
    assert response.json()["status"] == "syncing"
    assert response.json()["events"][-1]["type"] == "retry_requested"


async def test_retry_with_nothing_to_retry_is_409(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.SYNCED)

    response = await client.post(f"/v1/leads/{lead.id}/retry", headers=acme.headers)

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"status": "synced"}


async def test_other_tenants_lead_is_404(
    client: AsyncClient, acme: TenantWithKey, make_tenant: MakeTenant, make_lead: MakeLead
) -> None:
    other = await make_tenant("Other")
    lead = await make_lead(other.tenant, status=LeadStatus.NEEDS_REVIEW)

    for action in ("approve", "reject", "retry"):
        response = await client.post(f"/v1/leads/{lead.id}/{action}", headers=acme.headers)
        assert response.status_code == 404, action


async def test_invalid_body_is_422(
    client: AsyncClient, acme: TenantWithKey, make_lead: MakeLead
) -> None:
    lead = await make_lead(acme.tenant, status=LeadStatus.NEEDS_REVIEW)

    for body in ({"score_override": 101}, {"note": ""}, {"surprise": True}):
        response = await client.post(
            f"/v1/leads/{lead.id}/approve", json=body, headers=acme.headers
        )
        assert response.status_code == 422, body


async def test_actions_need_an_api_key(client: AsyncClient) -> None:
    response = await client.post("/v1/leads/ld_x/approve")

    assert response.status_code == 401
