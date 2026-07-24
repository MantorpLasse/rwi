from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Default probability_score for a Signal an automatic rule creates, keyed by
# the confidence it assigns. Lets rule-generated signals still surface in
# score-sorted views (e.g. the dashboard) instead of always sorting last.
DEFAULT_SCORE_BY_CONFIDENCE = {"high": 8.0, "medium": 6.0, "low": 3.0}


class Signal(Base):
    """Something that could become a future EMAS order.

    Replaces the old Project + Observation + Verification + Fact +
    Intelligence review pipeline with a single row you fill in yourself
    (or that gets created automatically by a rule).
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(250), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    confidence: Mapped[str] = mapped_column(String(30), index=True)
    target_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Personal, unverified year guess set via scripts/annotate_signal.py -
    # deliberately separate from target_year/planning_year/procurement_year
    # so an outside hunch is never confused with an officially sourced date.
    manual_year_estimate: Mapped[Optional[int]] = mapped_column(Integer)

    # Carried over from the old Project model so real research data
    # (financial estimates, pipeline stage, supplier reasoning) isn't lost.
    status: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    planning_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    procurement_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    construction_start: Mapped[Optional[date]] = mapped_column(Date)
    completion_date: Mapped[Optional[date]] = mapped_column(Date)
    estimated_total_value_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    estimated_emas_value_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    probability_score: Mapped[Optional[float]] = mapped_column(Float)
    supplier: Mapped[Optional[str]] = mapped_column(String(150))
    likely_supplier: Mapped[Optional[str]] = mapped_column(String(150))
    supplier_reason: Mapped[Optional[str]] = mapped_column(Text)

    # A vendor the source explicitly names as awarded/contracted - distinct
    # from likely_supplier (our own guess), so a confirmed fact is never
    # displayed the same way as an analytical judgment call.
    confirmed_vendor: Mapped[Optional[str]] = mapped_column(String(150))
    last_verified_at: Mapped[Optional[date]] = mapped_column(Date)

    airport: Mapped["Airport"] = relationship(back_populates="signals")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="signals")
    source: Mapped[Optional["Source"]] = relationship()
