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

    # Additive (docs/architecture/ai-discovery-governed-evidence-persistence-report.md).
    # Populated ONLY by app/services/discovery_evidence_persistence.py, from
    # app.services.evidence_attachment_guard.AttachmentDecision - never by
    # NASR/USAspending/FAA ingestion, which leave both NULL exactly as
    # before this addition. identity_guard_decision holds one of the five
    # AttachmentOutcome values verbatim (.value, e.g. "ATTACH_CONFIRMED")
    # - deliberately NOT DB-CHECK-constrained in this slice (a CHECK would
    # require a full SQLite table rebuild, not a plain ADD COLUMN - see the
    # migration script's own docstring); the persistence service is the
    # sole writer and only ever writes a real AttachmentOutcome.value,
    # enforced in Python, not the database. identity_guard_reason holds the
    # guard's own deterministic AttachmentDecision.reason text verbatim -
    # never AI-regenerated or summarized.
    identity_guard_decision: Mapped[Optional[str]] = mapped_column(String(30))
    identity_guard_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Additive (docs/architecture/intelligence-review-persistence-slice4-report.md).
    # Populated ONLY by app/services/intelligence_review_persistence.py, from
    # app.services.signal_candidate_evaluation.SignalCandidateDecision - never
    # by NASR/USAspending/FAA ingestion, and never by the identity-guard
    # persistence path above, which leave both NULL exactly as before this
    # addition. A structurally SEPARATE gate from identity_guard_decision:
    # intelligence review only ever runs on rows where identity_guard_decision
    # is exactly "ATTACH_CONFIRMED" (ATTACH_PROVISIONAL is deliberately NOT
    # sufficient, per Slice 3's own stricter policy) - the two fields answer
    # two different questions ("which airport" vs "is this materially
    # interesting") and are never merged into one decision/reason pair.
    # intelligence_review_decision holds one of the six SignalCandidateOutcome
    # values verbatim (.value); deliberately NOT DB-CHECK-constrained in this
    # slice for the same reason identity_guard_decision above isn't (a CHECK
    # would require a full SQLite table rebuild, not a plain ADD COLUMN) -
    # the persistence service is the sole writer and only ever writes a real
    # SignalCandidateOutcome.value, enforced in Python. intelligence_review_reason
    # holds SignalCandidateDecision.reason verbatim - never AI-regenerated.
    intelligence_review_decision: Mapped[Optional[str]] = mapped_column(String(30))
    intelligence_review_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Additive (docs/architecture/promotion-policy-persistence-slice7-report.md).
    # Populated ONLY by app/services/promotion_policy_persistence.py, from
    # app.services.promotion_policy_evaluation.PromotionPolicyDecision - never
    # by NASR/USAspending/FAA ingestion, and never by the identity-guard or
    # intelligence-review persistence paths above, which leave both NULL
    # exactly as before this addition. A structurally SEPARATE, third gate:
    # promotion policy only ever runs after intelligence review would reach
    # REVIEW_REQUIRED (app.services.promotion_policy_evaluation's own outcome
    # mapping) - the three field pairs on this row answer three different
    # questions ("which airport," "is this materially interesting," "could
    # this ever be automated") and are never merged into one decision/reason.
    # promotion_policy_decision holds one of the three PromotionPolicyOutcome
    # values verbatim (.value); deliberately NOT DB-CHECK-constrained in this
    # slice for the same reason the two pairs above aren't (a CHECK would
    # require a full SQLite table rebuild, not a plain ADD COLUMN) - the
    # persistence service is the sole writer and only ever writes a real
    # PromotionPolicyOutcome.value, enforced in Python. promotion_policy_reason
    # holds PromotionPolicyDecision.reason verbatim - never AI-regenerated.
    # AUTO_ELIGIBLE recorded here is an eligibility classification only - it
    # never creates, updates, or publishes a Signal, and no code path in this
    # repository yet acts on it automatically (a future, separately-authorized
    # slice, explicitly not this one).
    promotion_policy_decision: Mapped[Optional[str]] = mapped_column(String(30))
    promotion_policy_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Additive (docs/architecture/human-approved-governed-signal-creation-slice9c-report.md,
    # Slice 9C). Populated ONLY by
    # app/services/governed_signal_creation.py::create_signal_from_approved_review(),
    # after a human ReviewerAction (Slice 9B) has approved this row - never
    # by any ingestion path, never by the identity-guard/intelligence-review/
    # promotion-policy persistence services above, which never read or write
    # it. NULL means no Signal has been created from this governed evidence
    # yet; non-NULL both proves which evidence produced the Signal it points
    # at AND is this slice's own idempotency/duplicate guard (a second
    # create_signal_from_approved_review() call for the same SourceAssertion
    # reuses the existing Signal rather than creating a second one - see the
    # service's own docstring). Many SourceAssertions may point at the same
    # Signal (a second, later, corroborating piece of evidence); one
    # SourceAssertion points at, at most, one Signal - the same forward-FK-
    # on-the-"many"-side cardinality already established by
    # Signal.installation_id (app/models/signal.py) for the structurally
    # analogous Signal -> Installation graduation step.
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    source: Mapped["Source"] = relationship(back_populates="assertions")
    airport: Mapped[Optional["Airport"]] = relationship(back_populates="source_assertions")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="source_assertions")
    installation_assertion_links: Mapped[list["InstallationAssertionLink"]] = relationship(back_populates="assertion")
    reviewer_actions: Mapped[list["ReviewerAction"]] = relationship(back_populates="source_assertion")
    signal: Mapped[Optional["Signal"]] = relationship(back_populates="supporting_source_assertions")
