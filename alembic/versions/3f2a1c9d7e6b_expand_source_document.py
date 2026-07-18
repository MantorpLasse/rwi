"""expand Source and Document schema

Revision ID: 3f2a1c9d7e6b
Revises: 8edd52d34c76
Create Date: 2026-07-17

"""
from typing import Sequence, Union
from unicodedata import normalize
from urllib.parse import urlparse

from alembic import context, op
import sqlalchemy as sa


revision: str = "3f2a1c9d7e6b"
down_revision: Union[str, Sequence[str], None] = "8edd52d34c76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNKNOWN_PUBLISHER = "Unknown Publisher"


def _normalized_publisher_name(value: str | None) -> str:
    if value is None or not value.strip():
        return UNKNOWN_PUBLISHER
    return " ".join(normalize("NFC", value).split())


def _is_internal_watch_item(row) -> bool:
    # This exact three-field match is the approved conservative exclusion.
    return (
        row.source_type == "Watchlist"
        and row.publisher == "Runway Safe Intelligence"
        and row.title == "Internal watch item"
    )


def _is_homepage_only_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.path in {"", "/"} and not parsed.query and not parsed.fragment


def _migrate_legacy_sources() -> None:
    connection = op.get_bind()
    legacy_sources = sa.table(
        "sources",
        sa.column("id", sa.Integer),
        sa.column("project_id", sa.Integer),
        sa.column("title", sa.String),
        sa.column("source_type", sa.String),
        sa.column("publisher", sa.String),
        sa.column("url", sa.String),
        sa.column("published_date", sa.Date),
        sa.column("accessed_date", sa.Date),
        sa.column("document_reference", sa.String),
        sa.column("summary", sa.Text),
        sa.column("reliability_level", sa.String),
    )
    migration_metadata = sa.MetaData()
    publishing_sources = sa.Table(
        "publishing_sources",
        migration_metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
        sa.Column("source_type", sa.String),
        sa.Column("homepage_url", sa.String),
        sa.Column("country_code", sa.String),
        sa.Column("reliability_level", sa.String),
        sa.Column("notes", sa.Text),
    )
    documents = sa.Table(
        "documents",
        migration_metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer),
        sa.Column("title", sa.String),
        sa.Column("document_type", sa.String),
        sa.Column("url", sa.String),
        sa.Column("published_date", sa.Date),
        sa.Column("accessed_date", sa.Date),
        sa.Column("document_reference", sa.String),
        sa.Column("summary", sa.Text),
        sa.Column("revision", sa.String),
        sa.Column("content_hash", sa.String),
        sa.Column("status", sa.String),
    )
    project_documents = sa.table(
        "project_documents",
        sa.column("project_id", sa.Integer),
        sa.column("document_id", sa.Integer),
    )

    rows = connection.execute(sa.select(legacy_sources).order_by(legacy_sources.c.id)).all()
    source_ids: dict[str, int] = {}
    document_ids: dict[tuple, int] = {}

    for row in rows:
        if _is_internal_watch_item(row):
            continue

        source_name = _normalized_publisher_name(row.publisher)
        source_id = source_ids.get(source_name)
        if source_id is None:
            source_result = connection.execute(
                publishing_sources.insert().values(
                    name=source_name,
                    source_type=None,
                    homepage_url=row.url if _is_homepage_only_url(row.url) else None,
                    country_code=None,
                    reliability_level=row.reliability_level,
                    notes=None,
                )
            )
            source_id = source_result.inserted_primary_key[0]
            source_ids[source_name] = source_id

        # Exact publication-field matching intentionally merges only identical
        # legacy publications, including the duplicated FAA report.
        document_key = (
            source_name,
            row.title,
            row.source_type,
            row.url,
            row.published_date,
            row.document_reference,
        )
        document_id = document_ids.get(document_key)
        if document_id is None:
            document_result = connection.execute(
                documents.insert().values(
                    source_id=source_id,
                    title=row.title,
                    document_type=row.source_type,
                    url=row.url,
                    published_date=row.published_date,
                    accessed_date=row.accessed_date,
                    document_reference=row.document_reference,
                    summary=row.summary,
                    revision=None,
                    content_hash=None,
                    status="incomplete" if _is_homepage_only_url(row.url) else "active",
                )
            )
            document_id = document_result.inserted_primary_key[0]
            document_ids[document_key] = document_id

        connection.execute(
            project_documents.insert().values(
                project_id=row.project_id,
                document_id=document_id,
            )
        )


def upgrade() -> None:
    op.create_table(
        "publishing_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("homepage_url", sa.String(length=1000), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("reliability_level", sa.String(length=30), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("accessed_date", sa.Date(), nullable=True),
        sa.Column("document_reference", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("revision", sa.String(length=100), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, default="active"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'withdrawn', 'unavailable', 'incomplete')",
            name="ck_documents_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["publishing_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_source_id", "documents", ["source_id"], unique=False)
    op.create_table(
        "project_documents",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("project_id", "document_id"),
    )

    if not context.is_offline_mode():
        _migrate_legacy_sources()


def downgrade() -> None:
    op.drop_table("project_documents")
    op.drop_index("ix_documents_source_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("publishing_sources")
