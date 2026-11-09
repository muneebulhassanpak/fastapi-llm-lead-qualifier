"""What every CRM adapter takes, returns and raises."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class CRMLead:
    """The lead as the CRM sees it. `lead_id` is the external reference upserts key on."""

    lead_id: str
    tenant_id: str
    source: str
    email: str | None
    name: str | None
    company: str | None
    intent: str | None
    score: int | None
    reason: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CRMResult:
    external_id: str


class CRMError(Exception):
    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class CRMTransientError(CRMError):
    """Timeout, rate limit or server error: worth retrying, after `retry_after` if given."""

    def __init__(
        self, message: str, *, http_status: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message, http_status=http_status)
        self.retry_after = retry_after


class CRMPermanentError(CRMError):
    """The CRM refused the data (e.g. a 4xx validation error). Retrying cannot help."""


class CRMAdapter(Protocol):
    name: str

    async def upsert_lead(self, lead: CRMLead) -> CRMResult:
        """Create or update the CRM record for `lead.lead_id`. Must be safe to call twice."""
        ...
