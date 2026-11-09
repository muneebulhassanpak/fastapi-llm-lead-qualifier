from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, TimestampMixin
from app.domain.ids import new_id
from app.domain.status import LeadStatus

_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in LeadStatus)


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_valid"),
        CheckConstraint("score IS NULL OR (score BETWEEN 0 AND 100)", name="score_range"),
        CheckConstraint(
            "confidence IS NULL OR (confidence BETWEEN 0 AND 1)", name="confidence_range"
        ),
        Index("ix_leads_tenant_id_id", "tenant_id", "id"),
        Index("ix_leads_tenant_id_status_id", "tenant_id", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("ld"))
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=LeadStatus.RECEIVED)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # "metadata" is reserved on declarative classes, so the attribute has a different name.
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    crm_external_id: Mapped[str | None] = mapped_column(String(200))

    events: Mapped[list["LeadEvent"]] = relationship(
        back_populates="lead", order_by="LeadEvent.id", cascade="all, delete-orphan"
    )


class LeadEvent(CreatedAtMixin, Base):
    """Append-only audit log of everything that happens to a lead."""

    __tablename__ = "lead_events"

    # A sequence, not a ULID: events written in the same millisecond must keep their order.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="events")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
