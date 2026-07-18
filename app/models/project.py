from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
    documents: Mapped[list["Document"]] = relationship(
        secondary="project_documents",
        back_populates="projects",
    )
