from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin
from app.domain.ids import new_id


class SyncAttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_ATTEMPT_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in SyncAttemptStatus)


class SyncAttempt(CreatedAtMixin, Base):
    """One try at pushing a lead to the CRM, kept whether it worked or not."""

    __tablename__ = "sync_attempts"
    __table_args__ = (
        CheckConstraint(f"status IN ({_ATTEMPT_STATUS_VALUES})", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class MockCrmRecord(TimestampMixin, Base):
    """The mock CRM's own storage, standing in for an external system.

    No foreign keys on purpose: a real CRM knows nothing about our tables and keeps its records
    when our leads are deleted. `external_ref` (our lead ID) is unique, which is what makes a
    repeated upsert update the same record instead of creating a second one.
    """

    __tablename__ = "mock_crm_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("crm"))
    external_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
