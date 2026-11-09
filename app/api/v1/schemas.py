"""Request and response models for the v1 API."""

import json
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.db.models import Lead, LeadEvent
from app.domain.status import LeadStatus

MAX_METADATA_BYTES = 4096

Source = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$"),
]


class LeadCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "source": "contact_form",
                    "name": "Sara Khan",
                    "email": "sara@acme.example",
                    "message": "We need a Supabase app audited before launch. Budget around $3k.",
                    "metadata": {"page": "/pricing"},
                }
            ]
        },
    )

    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    source: Source = Field(default="api", description="Where the enquiry came from.")
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    email: EmailStr | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict, description=f"Free-form JSON, up to {MAX_METADATA_BYTES} bytes."
    )

    @field_validator("metadata")
    @classmethod
    def _limit_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, separators=(",", ":"))) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be at most {MAX_METADATA_BYTES} bytes as JSON")
        return value


class LeadAccepted(BaseModel):
    id: str = Field(examples=["ld_01JC9ZK3QX4V8R2M6N1P5T7W9Y"])
    status: LeadStatus
    created_at: datetime

    @classmethod
    def from_model(cls, lead: Lead) -> Self:
        return cls(id=lead.id, status=LeadStatus(lead.status), created_at=lead.created_at)


class LeadSummary(BaseModel):
    id: str
    status: LeadStatus
    source: str
    name: str | None
    email: str | None
    score: int | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, lead: Lead) -> Self:
        return cls(
            id=lead.id,
            status=LeadStatus(lead.status),
            source=lead.source,
            name=lead.name,
            email=lead.email,
            score=lead.score,
            confidence=lead.confidence,
            created_at=lead.created_at,
            updated_at=lead.updated_at,
        )


class LeadEventOut(BaseModel):
    type: str = Field(examples=["received"])
    actor: str = Field(examples=["api_key:ab12cd34"])
    data: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_model(cls, event: LeadEvent) -> Self:
        return cls(type=event.type, actor=event.actor, data=event.data, created_at=event.created_at)


class CRMState(BaseModel):
    external_id: str | None


class LeadDetail(LeadSummary):
    message: str
    metadata: dict[str, Any]
    extracted: dict[str, Any] | None
    reason: str | None
    crm: CRMState
    events: list[LeadEventOut] = Field(description="Audit trail, oldest first.")

    @classmethod
    def from_model(cls, lead: Lead) -> Self:
        summary = LeadSummary.from_model(lead).model_dump()
        return cls(
            **summary,
            message=lead.message,
            metadata=lead.extra_metadata,
            extracted=lead.extracted,
            reason=lead.reason,
            crm=CRMState(external_id=lead.crm_external_id),
            events=[LeadEventOut.from_model(e) for e in lead.events],
        )


class LeadList(BaseModel):
    data: list[LeadSummary]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page. Null on the last page."
    )
