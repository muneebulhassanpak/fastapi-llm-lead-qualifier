from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin
from app.domain.ids import new_id


class WebhookProvider(StrEnum):
    GENERIC = "generic"
    TALLY = "tally"


_PROVIDER_VALUES = ", ".join(f"'{p.value}'" for p in WebhookProvider)


class WebhookEndpoint(CreatedAtMixin, Base):
    """Where one provider posts one tenant's enquiries. The ID is public (it is in the URL);
    the signing secret is stored encrypted, because we need it back to check signatures."""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (CheckConstraint(f"provider IN ({_PROVIDER_VALUES})", name="provider_valid"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("whk"))
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
