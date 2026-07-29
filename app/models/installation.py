from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Installation(Base):
    """What's installed today (EMASMAX / greenEMAS) at an airport.

    Replaces the old, duplicated EmasInstallation + EmasBed models.
    """

    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)

    runway_end: Mapped[Optional[str]] = mapped_column(String(20))
    type: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(150))
    product_name: Mapped[Optional[str]] = mapped_column(String(100))
    install_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    replacement_year: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    length_m: Mapped[Optional[float]] = mapped_column(Float)
    width_m: Mapped[Optional[float]] = mapped_column(Float)
    faa_accepted: Mapped[Optional[bool]] = mapped_column(Boolean)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # A vendor the source explicitly names as having done the install -
    # see app/models/signal.py's confirmed_vendor for why this is kept
    # separate from a mere guess.
    confirmed_vendor: Mapped[Optional[str]] = mapped_column(String(150))

    # See app/models/signal.py's created_at/updated_at for why these are
    # nullable (added retroactively, existing rows left NULL).
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    airport: Mapped["Airport"] = relationship(back_populates="installations")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="installations")
    source: Mapped[Optional["Source"]] = relationship()
