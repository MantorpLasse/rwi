"""add facts

Revision ID: c8f1a34d6e72
Revises: b5d82a7c1e40
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8f1a34d6e72"
down_revision: str | None = "b5d82a7c1e40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fact_type_key", sa.String(length=150), nullable=False),
        sa.Column("subject_type", sa.String(length=50), nullable=False),
        sa.Column("subject_identifier", sa.String(length=200), nullable=False),
        sa.Column("accepted_value", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_fact_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_facts_valid_range",
        ),
        sa.CheckConstraint(
            "supersedes_fact_id IS NULL OR supersedes_fact_id != id",
            name="ck_facts_not_self_superseding",
        ),
        sa.CheckConstraint(
            "status != 'retired' OR supersedes_fact_id IS NOT NULL",
            name="ck_facts_retirement_has_predecessor",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'retired')",
            name="fact_status",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fact_id"], ["facts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supersedes_fact_id", name="uq_facts_supersedes_fact_id"
        ),
    )
    op.create_index("ix_facts_fact_type_key", "facts", ["fact_type_key"])
    op.create_index(
        "ix_facts_subject", "facts", ["subject_type", "subject_identifier"]
    )
    op.create_index(
        "ix_facts_supersedes_fact_id", "facts", ["supersedes_fact_id"]
    )
    op.create_table(
        "fact_verifications",
        sa.Column("fact_id", sa.Integer(), nullable=False),
        sa.Column("verification_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["fact_id"], ["facts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["verification_id"], ["verifications.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("fact_id", "verification_id"),
    )


def downgrade() -> None:
    op.drop_table("fact_verifications")
    op.drop_index("ix_facts_supersedes_fact_id", table_name="facts")
    op.drop_index("ix_facts_subject", table_name="facts")
    op.drop_index("ix_facts_fact_type_key", table_name="facts")
    op.drop_table("facts")
