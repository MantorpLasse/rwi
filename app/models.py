from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
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
    website_url: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    runways: Mapped[list["Runway"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    projects: Mapped[list["Project"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    installations: Mapped[list["EmasInstallation"]] = relationship(back_populates="airport", cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="airport", cascade="all, delete-orphan")


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
    projects: Mapped[list["Project"]] = relationship(back_populates="runway")
    installations: Mapped[list["EmasInstallation"]] = relationship(back_populates="runway")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="runway")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(250), index=True)
    project_type: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(50), index=True)
    confidence_level: Mapped[str] = mapped_column(String(30), index=True)

    planning_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    procurement_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    construction_start: Mapped[Optional[date]] = mapped_column(Date)
    completion_date: Mapped[Optional[date]] = mapped_column(Date)

    estimated_total_value_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    estimated_emas_value_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    probability_score: Mapped[float] = mapped_column(Float, default=5.0, index=True)

    supplier: Mapped[Optional[str]] = mapped_column(String(150))
    likely_supplier: Mapped[Optional[str]] = mapped_column(String(150))
    supplier_reason: Mapped[Optional[str]] = mapped_column(Text)

    description: Mapped[Optional[str]] = mapped_column(Text)
    last_verified_at: Mapped[Optional[date]] = mapped_column(Date)

    airport: Mapped["Airport"] = relationship(back_populates="projects")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="projects")
    sources: Mapped[list["Source"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class EmasInstallation(Base):
    __tablename__ = "emas_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)

    runway_end: Mapped[Optional[str]] = mapped_column(String(20))
    manufacturer: Mapped[Optional[str]] = mapped_column(String(150))
    product_name: Mapped[Optional[str]] = mapped_column(String(100))
    installation_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    replacement_year: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    length_m: Mapped[Optional[float]] = mapped_column(Float)
    width_m: Mapped[Optional[float]] = mapped_column(Float)
    faa_accepted: Mapped[Optional[bool]] = mapped_column()
    notes: Mapped[Optional[str]] = mapped_column(Text)

    airport: Mapped["Airport"] = relationship(back_populates="installations")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="installations")


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
