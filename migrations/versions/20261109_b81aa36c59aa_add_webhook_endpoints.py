"""Add webhook_endpoints: where providers post enquiries, with encrypted signing secrets.

Revision ID: b81aa36c59aa
Revises: 51c6c8842229
Create Date: 2026-11-09 16:58:51.968188+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b81aa36c59aa"
down_revision: str | Sequence[str] | None = "51c6c8842229"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('generic', 'tally')", name=op.f("ck_webhook_endpoints_provider_valid")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_webhook_endpoints_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
    )
    op.create_index(
        op.f("ix_webhook_endpoints_tenant_id"), "webhook_endpoints", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_webhook_endpoints_tenant_id"), table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
