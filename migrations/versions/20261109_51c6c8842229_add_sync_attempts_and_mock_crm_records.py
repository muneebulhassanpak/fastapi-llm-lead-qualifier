"""Add sync_attempts and mock_crm_records: CRM sync history and the mock CRM's storage.

Revision ID: 51c6c8842229
Revises: 9ff253876bb4
Create Date: 2026-11-09 16:35:58.172853+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "51c6c8842229"
down_revision: str | Sequence[str] | None = "9ff253876bb4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mock_crm_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mock_crm_records")),
        sa.UniqueConstraint("external_ref", name=op.f("uq_mock_crm_records_external_ref")),
    )
    op.create_table(
        "sync_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')", name=op.f("ck_sync_attempts_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_sync_attempts_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            name=op.f("fk_sync_attempts_lead_id_leads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_attempts")),
    )
    op.create_index(op.f("ix_sync_attempts_lead_id"), "sync_attempts", ["lead_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sync_attempts_lead_id"), table_name="sync_attempts")
    op.drop_table("sync_attempts")
    op.drop_table("mock_crm_records")
