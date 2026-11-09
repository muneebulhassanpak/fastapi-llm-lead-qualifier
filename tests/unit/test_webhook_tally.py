import json
from pathlib import Path
from typing import Any

import pytest

from app.webhooks.base import InvalidPayloadError
from app.webhooks.signatures import sign_tally
from app.webhooks.tally import TallyAdapter

FIXTURE = Path(__file__).parents[1] / "fixtures" / "tally_form_response.json"


def _payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def _parse(payload: dict[str, Any]) -> Any:
    return TallyAdapter().parse(json.dumps(payload).encode())


def test_form_response_becomes_a_lead() -> None:
    inbound = _parse(_payload())

    assert inbound.event_id == "a4cb511e-d513-4fa5-baee-b815d718dfd1"
    lead = inbound.lead
    assert (lead.name, lead.email, lead.source) == (
        "Dana Okafor",
        "dana@brightline.example",
        "tally",
    )
    assert lead.message == (
        "Our booking form should create contacts in HubSpot so new patients land in our CRM "
        "automatically.\n\n"
        "utm_source: linkedin\n"
        "Company: Brightline Dental\n"
        "Budget: $3k-$5k\n"
        "When do you need it live?: Within a month"
    )
    assert lead.metadata == {
        "tally": {"form_id": "VwbNEw", "form_name": "Work with us", "response_id": "2wgx4n"}
    }


def test_form_without_long_text_uses_the_other_answers() -> None:
    payload = _payload()
    payload["data"]["fields"] = [f for f in payload["data"]["fields"] if f["type"] != "TEXTAREA"]

    message = _parse(payload).lead.message

    assert message.startswith("utm_source: linkedin\nCompany: Brightline Dental")


def test_invalid_email_is_dropped_not_fatal() -> None:
    payload = _payload()
    for field in payload["data"]["fields"]:
        if field["type"] == "INPUT_EMAIL":
            field["value"] = "not an email"

    assert _parse(payload).lead.email is None


def test_long_answers_are_truncated() -> None:
    payload = _payload()
    for field in payload["data"]["fields"]:
        if field["type"] == "TEXTAREA":
            field["value"] = "x" * 9000

    assert len(_parse(payload).lead.message) == 5000


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p.update(eventType="FORM_DELETED"), "Unsupported Tally event"),
        (lambda p: p["data"].update(fields=[]), "no answers"),
        (lambda p: p.pop("eventId"), "Not a Tally webhook payload"),
    ],
)
def test_unusable_payloads(change: Any, message: str) -> None:
    payload = _payload()
    change(payload)

    with pytest.raises(InvalidPayloadError, match=message):
        _parse(payload)


def test_verify_uses_the_tally_signature_header() -> None:
    body = FIXTURE.read_bytes()
    adapter = TallyAdapter()

    assert adapter.verify("whsec_x", body, {"tally-signature": sign_tally("whsec_x", body)}, now=0)
    assert not adapter.verify("whsec_x", body, {}, now=0)
