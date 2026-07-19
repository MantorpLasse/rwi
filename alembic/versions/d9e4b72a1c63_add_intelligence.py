"""add intelligence

Revision ID: d9e4b72a1c63
Revises: c8f1a34d6e72
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d9e4b72a1c63"
down_revision: str | None = "c8f1a34d6e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intelligence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finding_type", sa.String(length=150), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_intelligence_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "supersedes_intelligence_id IS NULL OR supersedes_intelligence_id != id",
            name="ck_intelligence_not_self_superseding",
        ),
        sa.CheckConstraint(
            "status = 'active' OR supersedes_intelligence_id IS NOT NULL",
            name="ck_intelligence_inactive_has_predecessor",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'archived')",
            name="intelligence_status",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_intelligence_id"],
            ["intelligence.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supersedes_intelligence_id",
            name="uq_intelligence_supersedes_intelligence_id",
        ),
    )
    op.create_index(
        "ix_intelligence_created_at", "intelligence", ["created_at"]
    )
    op.create_index(
        "ix_intelligence_finding_type", "intelligence", ["finding_type"]
    )
    op.create_index("ix_intelligence_status", "intelligence", ["status"])
    op.create_index(
        "ix_intelligence_supersedes_intelligence_id",
        "intelligence",
        ["supersedes_intelligence_id"],
    )
    op.create_table(
        "intelligence_facts",
        sa.Column("intelligence_id", sa.Integer(), nullable=False),
        sa.Column("fact_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["intelligence_id"], ["intelligence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["fact_id"], ["facts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("intelligence_id", "fact_id"),
    )


def downgrade() -> None:
    op.drop_table("intelligence_facts")
    op.drop_index(
        "ix_intelligence_supersedes_intelligence_id", table_name="intelligence"
    )
    op.drop_index("ix_intelligence_status", table_name="intelligence")
    op.drop_index("ix_intelligence_finding_type", table_name="intelligence")
    op.drop_index("ix_intelligence_created_at", table_name="intelligence")
    op.drop_table("intelligence")
