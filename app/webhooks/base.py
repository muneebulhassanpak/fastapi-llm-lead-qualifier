"""What a webhook provider adapter does: check the signature, turn the payload into a lead."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.leads import NewLead

MAX_EVENT_ID_LENGTH = 200


@dataclass(frozen=True)
class InboundLead:
    event_id: str  # the provider's delivery ID: repeats of it are deduplicated
    lead: NewLead


class InvalidPayloadError(Exception):
    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class WebhookAdapter(Protocol):
    def verify(self, secret: str, body: bytes, headers: Mapping[str, str], *, now: float) -> bool:
        """True only if the raw body was signed with `secret` (and is recent, if dated)."""
        ...

    def parse(self, body: bytes) -> InboundLead:
        """The lead in the payload. Raises InvalidPayloadError if there isn't a usable one."""
        ...
