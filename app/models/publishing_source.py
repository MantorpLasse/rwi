from __future__ import annotations

from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PublishingSource(Base):
    """Who publishes an AcquisitionSource feed (e.g. the FAA).

    Backs AcquisitionSource only - not a user-facing model.
    """

    __tablename__ = "publishing_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    homepage_url: Mapped[Optional[str]] = mapped_column(String(1000))
    country_code: Mapped[Optional[str]] = mapped_column(String(2))
    reliability_level: Mapped[Optional[str]] = mapped_column(String(30))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    acquisition_sources: Mapped[list["AcquisitionSource"]] = relationship(
        back_populates="publishing_source"
    )
