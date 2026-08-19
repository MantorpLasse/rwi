from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
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

    # Set when scripts/graduate_signal_to_installation.py turns a confirmed-
    # built Signal into a real Installation row - lets status="completed"
    # link forward to what it became, instead of just going quiet.
    installation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("installations.id"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(250), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    confidence: Mapped[str] = mapped_column(String(30), index=True)
    target_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # Private. Personal, unverified annotations set via
    # scripts/annotate_signal.py - never exposed to the public static export
    # (see app/static_export/build.py's _signal_view), only to the
    # devserver. Same split as manual_year_estimate below: an outside hunch
    # is never confused with officially sourced data.
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Personal, unverified year guess set via scripts/annotate_signal.py -
    # deliberately separate from target_year/planning_year/procurement_year
    # so an outside hunch is never confused with an officially sourced date.
    manual_year_estimate: Mapped[Optional[int]] = mapped_column(Integer)

    # Public. Sourced research findings with a citation (document name, URL,
    # date) - free text that doesn't have its own column, same role as
    # Installation.notes ("Detaljer från källan" in the UI - see
    # app/static_export/templates/airport_detail.html). Written by one-off
    # enrichment scripts (e.g. scripts/update_fty_emas_details.py,
    # scripts/update_lex_emas_details.py) or by hand when the content is a
    # verifiable research finding rather than a personal guess. Shown
    # publicly in the static export, unlike `notes` above.
    source_notes: Mapped[Optional[str]] = mapped_column(Text)

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

    # Whether this Signal is eligible for public/static export - and only
    # that. Not a proxy for confidence, status, reviewer action, promotion
    # policy, or source reliability; those stay on their own fields. Defaults
    # to True so every existing (legacy) Signal-creation path keeps its
    # current effective publication behavior unchanged (see
    # scripts/migrate_signal_publication_slice9a.py, which backfills the two
    # previously hardcoded-excluded rows to False). A future governed-write
    # path (docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md
    # Slice 9C) must explicitly pass published=False itself - this column's
    # default is a backward-compatibility default, not a fail-closed
    # governance default; see
    # docs/architecture/signal-publication-separation-slice9a-report.md S5.
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Added retroactively (see docs/utredning_senast_uppdaterat.md) -
    # nullable, unlike AcquisitionRun's non-null created_at, because rows
    # from before this migration have no real creation time to record and
    # are left NULL rather than falsely stamped with the migration date.
    # Only rows created/edited from here on get a real value.
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    airport: Mapped["Airport"] = relationship(back_populates="signals")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="signals")
    source: Mapped[Optional["Source"]] = relationship()
    installation: Mapped[Optional["Installation"]] = relationship()
