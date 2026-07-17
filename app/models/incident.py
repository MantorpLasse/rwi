from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)

    incident_date: Mapped[date] = mapped_column(Date, index=True)
    aircraft_type: Mapped[Optional[str]] = mapped_column(String(100))
    operator: Mapped[Optional[str]] = mapped_column(String(150))
    incident_type: Mapped[str] = mapped_column(String(100))
    emas_engaged: Mapped[bool] = mapped_column(default=False, index=True)
    injuries: Mapped[Optional[str]] = mapped_column(String(100))
    aircraft_damage: Mapped[Optional[str]] = mapped_column(String(100))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    official_report_url: Mapped[Optional[str]] = mapped_column(String(1000))

    airport: Mapped["Airport"] = relationship(back_populates="incidents")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="incidents")
