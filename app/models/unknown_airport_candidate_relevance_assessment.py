"""ERG2 - append-only persistence for automatic EMAS-relevance assessments
(docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md,
persistence bridge for app.services.emas_relevance_evaluation, ERG1/
ERG1.5/ERG1.6).

A row represents exactly: "at this point in time, this
UnknownAirportCandidate's evidence was evaluated by
app.services.emas_relevance_evaluation.evaluate_emas_relevance() version X
and produced result Y." It is a RECORD of a pure evaluation, never a second,
independently-computed judgment - see
app.services.unknown_airport_candidate_relevance_persistence, the only
writer, which always calls evaluate_emas_relevance() itself rather than
accepting a caller-supplied outcome (so a row can never disagree with what
the real evaluator would say about the same evidence).

NEVER represents mutable current state: like UnknownAirportCandidateReview,
"current" is always derived by recency (see
get_latest_unknown_airport_candidate_relevance_assessment()), never by an
in-place update - this table has no `current`/`is_latest` column for
exactly the same anti-stale-cache reason UnknownAirportCandidate itself
carries no `review_state` column (docs/architecture/
rwi-governed-new-airport-discovery-design.md).

`is_canonical_admission_relevant` is DELIBERATELY NOT a column here -
ERG1.6 locked it as `is_inventory_relevant OR is_watch_worthy`, always
re-derivable from the two columns that ARE persisted; storing it would be
redundant truth that could drift from its own definition if either source
column were ever read in isolation by a future bug.

EVIDENCE TRACEABILITY: `UnknownAirportCandidateRelevanceAssessmentEvidenceLink`
is a normalized child membership table (not a comma-joined id list) linking
one assessment to the exact SourceAssertion(s) whose evidence the caller
says produced it - since one assessment may legitimately aggregate
evidence from more than one SourceAssertion (unlike ReviewerAction, which
is scoped to exactly one), and a naive `candidate_id`-only join would
incorrectly imply ALL of a candidate's current SourceAssertions (which can
grow over time via rediscovery) produced any one HISTORICAL assessment row.

Both tables immutable (ORM before_update/before_delete event listeners,
matching ReviewerAction/UnknownAirportCandidateReview/
SourceAssertionEvidenceBag's own identical precedent) - a later
re-evaluation on more/different evidence is always a NEW appended row,
never an edit of an existing one.

Deliberately does NOT add a `relevance_assessments` back-populated
collection to UnknownAirportCandidate, and does NOT add a
`relevance_assessment_links`/similar collection to SourceAssertion -
mirrors SourceAssertionEvidenceBag's own explicit, already-reviewed
reasoning verbatim: not required for correctness (a plain filtered query,
`get_latest_unknown_airport_candidate_relevance_assessment()`, already
answers "what is current" without an eagerly-loaded collection), and would
touch two already-many-times-migrated, already-adversarially-reviewed
model files for a capability that does not need it. This module is
therefore fully additive - it imports from, but never edits,
app/models/unknown_airport_candidate.py or app/models/source_assertion.py.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The exact five-member RelevanceOutcome vocabulary
# (app.services.emas_relevance_evaluation.RelevanceOutcome) - a plain
# String + CHECK constraint, not a Python str Enum on the model, matching
# ReviewerAction.action/UnknownAirportCandidateReview.action's own
# established convention for a persisted record of a deterministic
# evaluation-core output (see reviewer_action.py's own docstring for why:
# this table is a persisted RECORD, not the pure evaluation core itself,
# which already has its own typed Enum in emas_relevance_evaluation.py).
RELEVANCE_ASSESSMENT_OUTCOMES = (
    "EMAS_CONFIRMED",
    "EMAS_STRONG_SIGNAL",
    "EMAS_PLAUSIBLE_SIGNAL",
    "RUNWAY_ONLY_NOT_EMAS_RELEVANT",
    "INSUFFICIENT_EVIDENCE",
)


class UnknownAirportCandidateRelevanceAssessment(Base):
    """One append-only, immutable record of a single
    evaluate_emas_relevance() call for one UnknownAirportCandidate. See
    module docstring for the full design rationale.

    `evidence_classes_matched_json`/`contradicting_evidence_classes_json`:
    a JSON array of EvidenceClass string values, sorted for determinism -
    chosen over a comma-joined string despite EvidenceClass's own fixed,
    comma-free vocabulary making a comma-join technically safe today,
    because JSON is unambiguous by construction (immune to a future
    vocabulary member ever containing a comma) and matches this
    codebase's own most recent, strongest precedent for persisting a
    structured evaluation-core shape
    (app.models.source_assertion_evidence_bag.SourceAssertionEvidenceBag.evidence_bag_json).
    Produced/consumed only via app.services.
    unknown_airport_candidate_relevance_persistence's own small
    serialize/deserialize helpers - never hand-constructed elsewhere."""

    __tablename__ = "unknown_airport_candidate_relevance_assessments"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('EMAS_CONFIRMED','EMAS_STRONG_SIGNAL','EMAS_PLAUSIBLE_SIGNAL',"
            "'RUNWAY_ONLY_NOT_EMAS_RELEVANT','INSUFFICIENT_EVIDENCE')",
            name="ck_unknown_airport_candidate_relevance_assessments_outcome",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("unknown_airport_candidates.id"), index=True
    )

    outcome: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    evidence_classes_matched_json: Mapped[str] = mapped_column(Text)
    contradicting_evidence_classes_json: Mapped[str] = mapped_column(Text)
    is_inventory_relevant: Mapped[bool] = mapped_column(Boolean)
    is_watch_worthy: Mapped[bool] = mapped_column(Boolean)

    # app.services.emas_relevance_evaluation.EVALUATOR_VERSION at write time -
    # always stamped by the persistence service from the real evaluator
    # module's own constant, never caller-supplied (see the persistence
    # service's own docstring for why this is fabrication-proof by
    # construction, not merely by convention).
    evaluator_version: Mapped[str] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # One-directional (no back_populates) - see module docstring.
    candidate: Mapped["UnknownAirportCandidate"] = relationship()


class UnknownAirportCandidateRelevanceAssessmentEvidenceLink(Base):
    """Normalized child membership row: one SourceAssertion that
    contributed evidence to one UnknownAirportCandidateRelevanceAssessment.
    Immutable, append-only - see module docstring's EVIDENCE TRACEABILITY
    section. The UNIQUE constraint prevents a duplicate link row for the
    same (assessment, source_assertion) pair; it does not prevent the SAME
    SourceAssertion from being linked to multiple DIFFERENT assessments
    over time (expected - rediscovery/re-evaluation reuses the same
    underlying evidence across new assessment rows).

    CROSS-CANDIDATE INTEGRITY (adversarial-review finding, considered and
    DELIBERATELY NOT schema-enforced - see
    docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md
    for the full derivation): a composite-FK design analogous to
    app.models.identity_guard_evaluation.IdentityGuardEvaluation's own
    causal-integrity technique was prototyped (a redundant `candidate_id`
    column on this table, doubly-referenced by composite FKs to both the
    assessment and the source_assertion) and would structurally guarantee
    `assessment.candidate_id == source_assertion.unknown_airport_candidate_id`
    at the database layer. It was REVERTED after empirical testing proved
    it would break `upgrade()` against the REAL, already-migrated
    production `source_assertions` table: SQLite requires a matching
    UNIQUE index on the REFERENCED columns for any composite FK, and the
    live table does not have one - adding it would require a genuine
    table-rebuild migration against a real, already-populated table, a
    materially higher-risk operation this slice's own scope does not
    authorize. Cross-candidate integrity is therefore enforced at the
    SERVICE layer only (app.services.unknown_airport_candidate_relevance_persistence's
    own explicit `source_assertion.unknown_airport_candidate_id != candidate.id`
    check, before any row is constructed) - a raw-SQL insert that bypassed
    that service entirely could still create a mismatched link; this is a
    documented, accepted boundary, not an oversight."""

    __tablename__ = "unknown_airport_candidate_relevance_assessment_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "source_assertion_id",
            name="uq_uac_relevance_assessment_evidence_links_assessment_source_assertion",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("unknown_airport_candidate_relevance_assessments.id"), index=True
    )
    source_assertion_id: Mapped[int] = mapped_column(
        ForeignKey("source_assertions.id"), index=True
    )

    # One-directional (no back_populates) on both sides - see module
    # docstring.
    assessment: Mapped["UnknownAirportCandidateRelevanceAssessment"] = relationship()
    source_assertion: Mapped["SourceAssertion"] = relationship()


@event.listens_for(UnknownAirportCandidateRelevanceAssessment, "before_update")
def _prevent_relevance_assessment_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "UnknownAirportCandidateRelevanceAssessment rows are immutable; a genuinely different "
        "evaluation result is a new appended assessment, never an edit of an existing one."
    )


@event.listens_for(UnknownAirportCandidateRelevanceAssessment, "before_delete")
def _prevent_relevance_assessment_delete(_mapper, _connection, _target) -> None:
    raise ValueError("UnknownAirportCandidateRelevanceAssessment rows are auditable and cannot be deleted.")


@event.listens_for(UnknownAirportCandidateRelevanceAssessmentEvidenceLink, "before_update")
def _prevent_relevance_assessment_evidence_link_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "UnknownAirportCandidateRelevanceAssessmentEvidenceLink rows are immutable; record a new "
        "assessment (with its own new links) instead of editing an existing link."
    )


@event.listens_for(UnknownAirportCandidateRelevanceAssessmentEvidenceLink, "before_delete")
def _prevent_relevance_assessment_evidence_link_delete(_mapper, _connection, _target) -> None:
    raise ValueError(
        "UnknownAirportCandidateRelevanceAssessmentEvidenceLink rows are auditable and cannot be deleted."
    )
