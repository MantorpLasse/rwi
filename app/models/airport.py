from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Airport(Base):
    __tablename__ = "airports"

    id: Mapped[int] = mapped_column(primary_key=True)
    iata_code: Mapped[Optional[str]] = mapped_column(String(3), index=True)
    icao_code: Mapped[Optional[str]] = mapped_column(String(4), index=True)
    faa_code: Mapped[Optional[str]] = mapped_column(String(5), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state_region: Mapped[Optional[str]] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100), index=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    website_url: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    runways: Mapped[list["Runway"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    signals: Mapped[list["Signal"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    installations: Mapped[list["Installation"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    source_assertions: Mapped[list["SourceAssertion"]] = relationship(back_populates="airport")
    physical_installation_identities: Mapped[list["PhysicalInstallationIdentity"]] = relationship(back_populates="airport")


class Runway(Base):
    __tablename__ = "runways"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    designation: Mapped[str] = mapped_column(String(20), index=True)
    length_m: Mapped[Optional[int]] = mapped_column(Integer)
    width_m: Mapped[Optional[int]] = mapped_column(Integer)
    surface: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    airport: Mapped["Airport"] = relationship(back_populates="runways")
    signals: Mapped[list["Signal"]] = relationship(back_populates="runway")
    installations: Mapped[list["Installation"]] = relationship(back_populates="runway")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="runway")
    source_assertions: Mapped[list["SourceAssertion"]] = relationship(back_populates="runway")
    physical_installation_identities: Mapped[list["PhysicalInstallationIdentity"]] = relationship(back_populates="runway")
