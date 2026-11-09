"""Our own webhook format, for any service that can sign a request (or a small relay script).

POST /v1/webhooks/generic/{endpoint_id}
X-Timestamp: 1800000000
X-Signature: sha256=<hex HMAC-SHA256 of "{timestamp}.{body}">

{"event_id": "evt_123", "lead": {"message": "...", "email": "...", "name": "...",
                                  "source": "...", "metadata": {...}}}
"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.v1.schemas import LeadCreate
from app.services.leads import NewLead
from app.webhooks.base import MAX_EVENT_ID_LENGTH, InboundLead, InvalidPayloadError
from app.webhooks.signatures import verify_generic

DEFAULT_SOURCE = "webhook"


class GenericPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=MAX_EVENT_ID_LENGTH)
    lead: LeadCreate


class GenericAdapter:
    def verify(self, secret: str, body: bytes, headers: Mapping[str, str], *, now: float) -> bool:
        return verify_generic(
            secret, body, headers.get("x-timestamp"), headers.get("x-signature"), now=now
        )

    def parse(self, body: bytes) -> InboundLead:
        try:
            payload = GenericPayload.model_validate_json(body)
        except ValidationError as exc:
            details = [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
            ]
            raise InvalidPayloadError("The webhook payload is invalid.", details=details) from exc
        lead = payload.lead
        return InboundLead(
            event_id=payload.event_id,
            lead=NewLead(
                message=lead.message,
                source=lead.source if "source" in lead.model_fields_set else DEFAULT_SOURCE,
                name=lead.name,
                email=str(lead.email) if lead.email else None,
                metadata=lead.metadata,
            ),
        )
