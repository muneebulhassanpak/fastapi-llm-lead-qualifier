from app.webhooks.generic import GenericAdapter
from app.webhooks.tally import TallyAdapter
from scripts.send_webhook import build_request


def test_generic_request_is_signed_and_parseable() -> None:
    body, headers = build_request("generic", "whsec_x", "evt_1", "Hello there", now=1_800_000_000)
    lowered = {k.lower(): v for k, v in headers.items()}

    assert GenericAdapter().verify("whsec_x", body, lowered, now=1_800_000_000)
    inbound = GenericAdapter().parse(body)
    assert (inbound.event_id, inbound.lead.message) == ("evt_1", "Hello there")


def test_tally_request_is_signed_and_parseable() -> None:
    body, headers = build_request("tally", "whsec_x", "evt_2", "ignored")
    lowered = {k.lower(): v for k, v in headers.items()}

    assert TallyAdapter().verify("whsec_x", body, lowered, now=0)
    assert TallyAdapter().parse(body).event_id == "evt_2"
