from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RunwayEnd(Base):
    __tablename__ = "runway_ends"
    __table_args__ = (UniqueConstraint("runway_id", "designation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    runway_id: Mapped[int] = mapped_column(ForeignKey("runways.id"), index=True)
    designation: Mapped[str] = mapped_column(String(20))
    heading: Mapped[Optional[int]] = mapped_column(Integer)
    resa_length_m: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    runway: Mapped["Runway"] = relationship(back_populates="runway_ends")
    emas_beds: Mapped[list["EmasBed"]] = relationship(back_populates="runway_end")
