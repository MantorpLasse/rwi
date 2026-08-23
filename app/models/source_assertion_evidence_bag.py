"""EB1 - immutable, lossless EvidenceBag snapshot persistence
(docs/architecture/rwi-eb1-evidencebag-persistence-foundation-report.md,
Slice 1 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

A `SourceAssertionEvidenceBag` is the frozen, exactly-reproducible input
the identity guard (app.services.evidence_attachment_guard) actually
consumed at discovery time - never a second, independently-normalized
representation of it. Exactly ONE per `SourceAssertion` (enforced by the
`unique=True` FK below, mirroring `Snapshot.first_acquisition_run_id`'s
own precedent exactly). This table has EXACTLY ONE writer in this
mission's own scope - none yet: EB1 defines the persistence shape only;
EB3 is responsible for actually writing a row alongside a new
SourceAssertion. EB1 itself never wires this into live discovery.

Deliberately does NOT modify app/models/source_assertion.py - "many
SourceAssertions may each have at most one snapshot" is provable by a
plain, narrow query (mirroring get_latest_unknown_airport_candidate_review()'s
own established convention, never an eagerly-loaded ORM collection),
exactly the same reasoning UAC2B's own review already gave for why
SourceAssertion.unknown_airport_candidate carries no back_populates and
required zero change to the target model. Adding a reciprocal collection
to SourceAssertion is not required for correctness and would touch an
already-six-times-migrated, already-adversarially-reviewed model file for
a capability that does not need it.

Immutable, matching app.models.acquisition.Snapshot's own precedent
exactly: once persisted, a snapshot represents "what was extracted at
discovery time" - a genuinely different extraction result is, by this
project's own established fragment-identity model, a structurally
DIFFERENT fragment (a new SourceAssertion via the existing
fragment-identity/dedup mechanism), never an edit of this row.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SourceAssertionEvidenceBag(Base):
    """The exact, lossless, structured EvidenceBag snapshot used to
    produce ONE SourceAssertion's own identity_guard_decision - see the
    module docstring. `evidence_bag_json` is produced/consumed only by
    app.services.evidence_bag_serialization (serialize_evidence_bag()/
    deserialize_evidence_bag()) - never hand-constructed or hand-parsed
    elsewhere.

    The `UniqueConstraint("id", "source_assertion_id")` below is
    redundant with `id` already being the primary key on its own - it
    exists solely so `IdentityGuardEvaluation` (app/models/identity_guard_evaluation.py)
    can reference `(id, source_assertion_id)` together as a composite FK
    target, structurally guaranteeing a future evaluation can never claim
    to concern SourceAssertion A while actually pointing at a snapshot
    that belongs to a different SourceAssertion B - a causal-integrity
    property this table's own single-column `id` FK alone could not
    enforce (adversarial-review finding; see this module's own EB1
    review addendum)."""

    __tablename__ = "source_assertion_evidence_bags"
    __table_args__ = (
        UniqueConstraint("id", "source_assertion_id", name="uq_source_assertion_evidence_bags_id_source_assertion_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # 1:1 with SourceAssertion - unique=True (not a separate index=True;
    # SQLite's own UNIQUE constraint already provides an efficient lookup
    # path, matching Snapshot.first_acquisition_run_id's own precedent,
    # which likewise omits a redundant explicit index).
    source_assertion_id: Mapped[int] = mapped_column(
        ForeignKey("source_assertions.id"), unique=True
    )

    # Produced only by app.services.evidence_bag_serialization.serialize_evidence_bag().
    evidence_bag_json: Mapped[str] = mapped_column(Text)

    # SHA-256 hex digest of evidence_bag_json - the EXACT persisted string,
    # never a second, independently-normalized representation (see
    # hash_serialized_evidence_bag()'s own docstring). Sized and indexed
    # identically to Snapshot.sha256.
    evidence_bag_hash: Mapped[str] = mapped_column(String(64), index=True)

    # app.services.evidence_bag_serialization.EVIDENCE_BAG_SCHEMA_VERSION
    # at write time - lets a future reader know unambiguously which field
    # set this row was serialized under, so a future schema evolution can
    # never be silently misinterpreted under a different shape's
    # assumptions.
    schema_version: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # One-directional (no back_populates) - see module docstring.
    source_assertion: Mapped["SourceAssertion"] = relationship()


@event.listens_for(SourceAssertionEvidenceBag, "before_update")
def _prevent_evidence_bag_snapshot_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "SourceAssertionEvidenceBag snapshots are immutable; a genuinely different extraction "
        "result is a structurally different fragment (a new SourceAssertion), never an edit of "
        "an existing snapshot."
    )


@event.listens_for(SourceAssertionEvidenceBag, "before_delete")
def _prevent_evidence_bag_snapshot_delete(_mapper, _connection, _target) -> None:
    raise ValueError("SourceAssertionEvidenceBag snapshots are auditable and cannot be deleted.")
