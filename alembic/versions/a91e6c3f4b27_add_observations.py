"""add observations

Revision ID: a91e6c3f4b27
Revises: 7c4f1d8e2a90
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a91e6c3f4b27"
down_revision: str | None = "7c4f1d8e2a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("observation_type_id", sa.Integer(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("evidence_locator", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.String(length=50), nullable=True),
        sa.Column("extractor_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_observation_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0)",
            name="ck_observations_extraction_confidence",
        ),
        sa.CheckConstraint(
            "supersedes_observation_id IS NULL OR supersedes_observation_id != id",
            name="ck_observations_not_self_superseding",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["observation_type_id"], ["observation_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_observation_id"], ["observations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_observations_document_id", "observations", ["document_id"])
    op.create_index("ix_observations_observation_type_id", "observations", ["observation_type_id"])
    op.create_index("ix_observations_supersedes_observation_id", "observations", ["supersedes_observation_id"])


def downgrade() -> None:
    op.drop_index("ix_observations_supersedes_observation_id", table_name="observations")
    op.drop_index("ix_observations_observation_type_id", table_name="observations")
    op.drop_index("ix_observations_document_id", table_name="observations")
    op.drop_table("observations")
