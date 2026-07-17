from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EmasBed(Base):
    __tablename__ = "emas_beds"
    __table_args__ = (
        Index(
            "uq_emas_beds_current_runway_end",
            "runway_end_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    runway_end_id: Mapped[int] = mapped_column(ForeignKey("runway_ends.id"), index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(150))
    product_name: Mapped[Optional[str]] = mapped_column(String(100))
    installation_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    replacement_year: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    length_m: Mapped[Optional[float]] = mapped_column(Float)
    width_m: Mapped[Optional[float]] = mapped_column(Float)
    faa_accepted: Mapped[Optional[bool]] = mapped_column(Boolean)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    runway_end: Mapped["RunwayEnd"] = relationship(back_populates="emas_beds")
