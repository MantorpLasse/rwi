from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Source(Base):
    """A link or document something (a Signal/Incident/Installation) came from.

    Referenced by source_id from the other tables rather than owning them,
    so one Source can back more than one row without a document management
    system.
    """

    __tablename__ = "sources"
    __table_args__ = (
        Index("uq_sources_external_id", "external_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    published_date: Mapped[Optional[date]] = mapped_column(Date)
    retrieved_at: Mapped[Optional[date]] = mapped_column(Date)
    document_reference: Mapped[Optional[str]] = mapped_column(String(200))
    page_number: Mapped[Optional[str]] = mapped_column(String(30))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    reliability_level: Mapped[str] = mapped_column(String(30), default="official")
    # Stable identifier from the source system (e.g. USAspending's
    # generated_internal_id), namespaced per system, so re-running an import
    # script - or a second script covering the same real-world grant - can
    # detect and skip a row it already created.
    external_id: Mapped[Optional[str]] = mapped_column(String(200))
