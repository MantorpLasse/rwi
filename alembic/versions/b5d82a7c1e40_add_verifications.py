"""add verifications

Revision ID: b5d82a7c1e40
Revises: a91e6c3f4b27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b5d82a7c1e40"
down_revision: str | None = "a91e6c3f4b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'undecided')",
            name="verification_status",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_verifications_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"], ["observations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_verifications_observation_id",
        "verifications",
        ["observation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_verifications_observation_id", table_name="verifications")
    op.drop_table("verifications")
