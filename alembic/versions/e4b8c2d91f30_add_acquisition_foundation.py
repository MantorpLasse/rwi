"""add acquisition foundation

Revision ID: e4b8c2d91f30
Revises: f2a7c84e9d10
Create Date: 2026-07-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b8c2d91f30"
down_revision: Union[str, Sequence[str], None] = "f2a7c84e9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "acquisition_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publishing_source_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=150), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("acquisition_type", sa.String(length=50), nullable=False),
        sa.Column("canonical_url", sa.String(length=1000), nullable=False),
        sa.Column("expected_media_type", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["publishing_source_id"], ["publishing_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_acquisition_sources_key"),
    )
    op.create_index("ix_acquisition_sources_publishing_source_id", "acquisition_sources", ["publishing_source_id"])
    op.create_table(
        "acquisition_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("acquisition_source_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=18), nullable=False),
        sa.Column("request_url", sa.String(length=1000), nullable=False),
        sa.Column("final_url", sa.String(length=1000), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("response_headers", sa.Text(), nullable=True),
        sa.Column("provider_version", sa.String(length=100), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("error_category", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("is_new_snapshot", sa.Boolean(), nullable=True),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_acquisition_runs_duration"),
        sa.CheckConstraint(
            "status IN ('running','success','no_change','partial_success','unavailable','blocked','invalid_response','unsupported_format','permission_failure','rate_limited','failed')",
            name="acquisition_run_status",
        ),
        sa.ForeignKeyConstraint(["acquisition_source_id"], ["acquisition_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshots.id"],
            name="fk_acquisition_runs_snapshot_id", ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_acquisition_runs_acquisition_source_id", "acquisition_runs", ["acquisition_source_id"])
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("acquisition_source_id", sa.Integer(), nullable=False),
        sa.Column("first_acquisition_run_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_snapshots_byte_size"),
        sa.ForeignKeyConstraint(["acquisition_source_id"], ["acquisition_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["first_acquisition_run_id"], ["acquisition_runs.id"],
            name="fk_snapshots_first_acquisition_run_id", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("first_acquisition_run_id"),
        sa.UniqueConstraint("acquisition_source_id", "sha256", "byte_size", name="uq_snapshots_source_hash_size"),
    )
    op.create_index("ix_snapshots_acquisition_source_id", "snapshots", ["acquisition_source_id"])
    op.create_index("ix_snapshots_sha256", "snapshots", ["sha256"])
    op.create_index("ix_acquisition_runs_snapshot_id", "acquisition_runs", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_acquisition_runs_snapshot_id", table_name="acquisition_runs")
    op.drop_index("ix_snapshots_sha256", table_name="snapshots")
    op.drop_index("ix_snapshots_acquisition_source_id", table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_index("ix_acquisition_runs_acquisition_source_id", table_name="acquisition_runs")
    op.drop_table("acquisition_runs")
    op.drop_index("ix_acquisition_sources_publishing_source_id", table_name="acquisition_sources")
    op.drop_table("acquisition_sources")
