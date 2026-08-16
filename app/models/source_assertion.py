from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


ASSERTION_TYPES = (
    "airport_inventory",
    "runway",
    "runway_end",
    "physical_system",
    "historical",
    "project_construction",
)
EVIDENCE_QUALITIES = ("unverified_candidate", "direct_strong", "corroborated", "partial", "ambiguous")
REVIEW_STATES = ("unreviewed", "reviewed")


class SourceAssertion(Base):
    """One upstream record's claim, preserved before any identity reconciliation."""

    __tablename__ = "source_assertions"
    __table_args__ = (
        CheckConstraint(
            "assertion_type IN ('airport_inventory', 'runway', 'runway_end', "
            "'physical_system', 'historical', 'project_construction')",
            name="ck_source_assertions_type",
        ),
        CheckConstraint(
            "evidence_quality IN ('unverified_candidate', 'direct_strong', 'corroborated', 'partial', 'ambiguous')",
            name="ck_source_assertions_evidence_quality",
        ),
        CheckConstraint("review_state IN ('unreviewed', 'reviewed')", name="ck_source_assertions_review_state"),
        CheckConstraint(
            "source_record_identifier IS NOT NULL OR "
            "(source_locator IS NOT NULL AND raw_fragment_hash IS NOT NULL)",
            name="ck_source_assertions_record_identity",
        ),
        UniqueConstraint("source_id", "source_record_identifier", name="uq_source_assertions_source_record"),
        UniqueConstraint(
            "source_id", "artifact_identity", "source_locator", "raw_fragment_hash",
            name="uq_source_assertions_locator_fragment",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    airport_id: Mapped[Optional[int]] = mapped_column(ForeignKey("airports.id"), nullable=True, index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)

    assertion_type: Mapped[str] = mapped_column(String(30))
    runway_end: Mapped[Optional[str]] = mapped_column(String(20))
    raw_airport_identifier: Mapped[Optional[str]] = mapped_column(String(100))
    raw_airport_name: Mapped[Optional[str]] = mapped_column(String(300))
    raw_runway_value: Mapped[Optional[str]] = mapped_column(String(100))
    raw_runway_end_value: Mapped[Optional[str]] = mapped_column(String(100))
    raw_product_type: Mapped[Optional[str]] = mapped_column(String(200))
    raw_year_date_wording: Mapped[Optional[str]] = mapped_column(String(300))
    raw_vendor_manufacturer_wording: Mapped[Optional[str]] = mapped_column(String(300))
    raw_count: Mapped[Optional[str]] = mapped_column(String(100))
    raw_relevant_text: Mapped[Optional[str]] = mapped_column(Text)

    source_record_identifier: Mapped[Optional[str]] = mapped_column(String(300))
    source_locator: Mapped[Optional[str]] = mapped_column(String(500))
    raw_fragment_hash: Mapped[Optional[str]] = mapped_column(String(128))
    artifact_identity: Mapped[Optional[str]] = mapped_column(String(500))
    parser_identifier: Mapped[Optional[str]] = mapped_column(String(200))
    extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    evidence_quality: Mapped[str] = mapped_column(String(30), default="unverified_candidate")
    review_state: Mapped[str] = mapped_column(String(20), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    source: Mapped["Source"] = relationship(back_populates="assertions")
    airport: Mapped[Optional["Airport"]] = relationship(back_populates="source_assertions")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="source_assertions")
