"""add governed finding types

Revision ID: f2a7c84e9d10
Revises: d9e4b72a1c63
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision: str = "f2a7c84e9d10"
down_revision: str | None = "d9e4b72a1c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_FINDING_TYPES = (
    ("CURRENT_EMAS", "Current EMAS", "An accepted current EMAS installation is established.", "STATUS"),
    ("NO_VERIFIED_EMAS", "No verified EMAS", "No EMAS installation is established by verified evidence.", "STATUS"),
    ("CONFLICTING_EMAS", "Conflicting EMAS", "Accepted EMAS facts conflict.", "CONFLICT"),
    ("MISSING_INSTALLATION_YEAR", "Missing installation year", "The installation year is not established.", "COMPLETENESS"),
    ("MISSING_MANUFACTURER", "Missing manufacturer", "The EMAS manufacturer is not established.", "COMPLETENESS"),
    ("MISSING_LENGTH", "Missing length", "The EMAS installation length is not established.", "COMPLETENESS"),
    ("MULTIPLE_CURRENT_FACTS", "Multiple current Facts", "More than one current Fact exists for the governed statement.", "CONFLICT"),
    ("UNVERIFIED_INSTALLATION", "Unverified installation", "An installation claim lacks accepted verification.", "QUALITY"),
)


def _intelligence_table(*, governed: bool) -> sa.Table:
    metadata = sa.MetaData()
    columns = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]
    if governed:
        columns.append(
            sa.Column(
                "finding_type_id",
                sa.Integer(),
                sa.ForeignKey("finding_types.id", ondelete="RESTRICT", name="fk_intelligence_finding_type_id"),
                nullable=False,
            )
        )
    else:
        columns.append(
            sa.Column("finding_type", sa.String(length=150), nullable=False)
        )
    columns.extend(
        [
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=10), nullable=False),
            sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "supersedes_intelligence_id",
                sa.Integer(),
                sa.ForeignKey("intelligence.id", ondelete="RESTRICT"),
            ),
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
            sa.UniqueConstraint(
                "supersedes_intelligence_id",
                name="uq_intelligence_supersedes_intelligence_id",
            ),
        ]
    )
    if governed:
        sa.Table("finding_types", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    return sa.Table("intelligence", metadata, *columns)


def upgrade() -> None:
    op.create_table(
        "finding_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=150), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "key GLOB '[A-Z]*' "
            "AND key NOT GLOB '*[^A-Z0-9_]*' "
            "AND instr(key, '__') = 0 "
            "AND substr(key, -1, 1) != '_'",
            name="ck_finding_types_upper_snake_key",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_finding_types_key"),
    )

    finding_types = sa.table(
        "finding_types",
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("category", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    created_at = datetime(2026, 7, 20, tzinfo=UTC)
    op.bulk_insert(
        finding_types,
        [
            {
                "key": key,
                "name": name,
                "description": description,
                "category": category,
                "is_active": True,
                "created_at": created_at,
            }
            for key, name, description, category in SEED_FINDING_TYPES
        ],
    )

    if op.get_context().as_sql:
        op.drop_index("ix_intelligence_finding_type", table_name="intelligence")
        with op.batch_alter_table(
            "intelligence", copy_from=_intelligence_table(governed=False)
        ) as batch_op:
            batch_op.add_column(
                sa.Column("finding_type_id", sa.Integer(), nullable=False)
            )
            batch_op.create_foreign_key(
                "fk_intelligence_finding_type_id",
                "finding_types",
                ["finding_type_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.drop_column("finding_type")
        op.create_index(
            "ix_intelligence_finding_type_id",
            "intelligence",
            ["finding_type_id"],
        )
        return

    with op.batch_alter_table("intelligence") as batch_op:
        batch_op.add_column(
            sa.Column("finding_type_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_intelligence_finding_type_id",
            "finding_types",
            ["finding_type_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.execute(
        "UPDATE intelligence "
        "SET finding_type_id = ("
        "SELECT id FROM finding_types WHERE key = intelligence.finding_type"
        ")"
    )
    op.drop_index("ix_intelligence_finding_type", table_name="intelligence")
    with op.batch_alter_table("intelligence") as batch_op:
        batch_op.alter_column(
            "finding_type_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.drop_column("finding_type")
    op.create_index(
        "ix_intelligence_finding_type_id",
        "intelligence",
        ["finding_type_id"],
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        op.drop_index("ix_intelligence_finding_type_id", table_name="intelligence")
        with op.batch_alter_table(
            "intelligence", copy_from=_intelligence_table(governed=True)
        ) as batch_op:
            batch_op.add_column(
                sa.Column("finding_type", sa.String(length=150), nullable=False)
            )
            batch_op.drop_constraint(
                "fk_intelligence_finding_type_id", type_="foreignkey"
            )
            batch_op.drop_column("finding_type_id")
        op.create_index(
            "ix_intelligence_finding_type", "intelligence", ["finding_type"]
        )
        op.drop_table("finding_types")
        return

    op.drop_index("ix_intelligence_finding_type_id", table_name="intelligence")
    with op.batch_alter_table("intelligence") as batch_op:
        batch_op.add_column(
            sa.Column("finding_type", sa.String(length=150), nullable=True)
        )
    op.execute(
        "UPDATE intelligence "
        "SET finding_type = ("
        "SELECT key FROM finding_types WHERE id = intelligence.finding_type_id"
        ")"
    )
    with op.batch_alter_table("intelligence") as batch_op:
        batch_op.alter_column(
            "finding_type", existing_type=sa.String(length=150), nullable=False
        )
        batch_op.drop_constraint(
            "fk_intelligence_finding_type_id", type_="foreignkey"
        )
        batch_op.drop_column("finding_type_id")
    op.create_index(
        "ix_intelligence_finding_type", "intelligence", ["finding_type"]
    )
    keys = ", ".join(f"'{key}'" for key, *_ in SEED_FINDING_TYPES)
    op.execute(f"DELETE FROM finding_types WHERE key IN ({keys})")
    op.drop_table("finding_types")
