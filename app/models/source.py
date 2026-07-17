from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)

    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    published_date: Mapped[Optional[date]] = mapped_column(Date)
    accessed_date: Mapped[Optional[date]] = mapped_column(Date)
    document_reference: Mapped[Optional[str]] = mapped_column(String(200))
    page_number: Mapped[Optional[str]] = mapped_column(String(30))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    reliability_level: Mapped[str] = mapped_column(String(30), default="official")

    project: Mapped["Project"] = relationship(back_populates="sources")
