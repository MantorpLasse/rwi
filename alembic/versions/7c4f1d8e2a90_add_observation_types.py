"""add governed ObservationType vocabulary

Revision ID: 7c4f1d8e2a90
Revises: 3f2a1c9d7e6b
Create Date: 2026-07-18

"""
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c4f1d8e2a90"
down_revision: Union[str, Sequence[str], None] = "3f2a1c9d7e6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OBSERVATION_TYPE_ROWS = (
    {
        "key": "airport.emas.product",
        "display_label": "Airport EMAS product",
        "description": "EMAS product family reported as present at an airport.",
        "value_type": "enumeration",
    },
    {
        "key": "airport.emas.system_count",
        "display_label": "Airport EMAS system count",
        "description": "Number of EMAS systems reported for an airport.",
        "value_type": "integer",
    },
    {
        "key": "airport.emas.installation_year_display",
        "display_label": "Airport EMAS installation year display",
        "description": (
            "Source display text containing installation or replacement years "
            "whose precise semantics may be unresolved."
        ),
        "value_type": "raw_text",
    },
)


def upgrade() -> None:
    observation_types = op.create_table(
        "observation_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=150), nullable=False),
        sa.Column("display_label", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("value_type", sa.String(length=30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "value_type IN ('enumeration', 'integer', 'raw_text')",
            name="ck_observation_types_value_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_observation_types_key"),
    )

    created_at = datetime.now(UTC)
    op.bulk_insert(
        observation_types,
        [
            {**row, "active": True, "created_at": created_at}
            for row in OBSERVATION_TYPE_ROWS
        ],
    )


def downgrade() -> None:
    op.drop_table("observation_types")

