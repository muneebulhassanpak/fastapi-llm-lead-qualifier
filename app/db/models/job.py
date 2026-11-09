from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.domain.ids import new_id
from app.domain.jobs import JobStatus, JobType

_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in JobStatus)
_TYPE_VALUES = ", ".join(f"'{t.value}'" for t in JobType)


class Job(TimestampMixin, Base):
    """A unit of background work. The table is the queue."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_valid"),
        CheckConstraint(f"type IN ({_TYPE_VALUES})", name="type_valid"),
        # Serves the claim query: queued jobs that are due, oldest first.
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("job"))
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
