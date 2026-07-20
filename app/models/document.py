from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


DOCUMENT_STATUSES = frozenset(
    {"active", "superseded", "withdrawn", "unavailable", "incomplete"}
)


project_documents = Table(
    "project_documents",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("document_id", ForeignKey("documents.id"), primary_key=True),
)


class PublishingSource(Base):
    __tablename__ = "publishing_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    homepage_url: Mapped[Optional[str]] = mapped_column(String(1000))
    country_code: Mapped[Optional[str]] = mapped_column(String(2))
    reliability_level: Mapped[Optional[str]] = mapped_column(String(30))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    documents: Mapped[list["Document"]] = relationship(back_populates="source")
    acquisition_sources: Mapped[list["AcquisitionSource"]] = relationship(
        back_populates="publishing_source"
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded', 'withdrawn', 'unavailable', 'incomplete')",
            name="ck_documents_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("publishing_sources.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    document_type: Mapped[Optional[str]] = mapped_column(String(50))
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    published_date: Mapped[Optional[date]] = mapped_column(Date)
    accessed_date: Mapped[Optional[date]] = mapped_column(Date)
    document_reference: Mapped[Optional[str]] = mapped_column(String(200))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    revision: Mapped[Optional[str]] = mapped_column(String(100))
    content_hash: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(30), default="active")

    source: Mapped["PublishingSource"] = relationship(back_populates="documents")
    projects: Mapped[list["Project"]] = relationship(
        secondary=project_documents,
        back_populates="documents",
    )
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="document", passive_deletes="all"
    )


from app.models.acquisition import AcquisitionSource  # noqa: E402
