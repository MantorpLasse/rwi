"""First-class governed cross-source alias attestation (docs/architecture,
"RWI - Cross-Source Governed Airport Identity Binding - Architecture Recon"
mission's own Option C, the locked architecture this module implements; see
app.services.cross_source_alias_attestation for the full precondition
chain and app.services.effective_identity_guard_decision (EB5) for how a
row here is consumed).

A `SourceAssertionCrossSourceAliasAttestation` row represents exactly one
thing: "a human reviewer has accepted that this already-canonical
SourceAssertion refers to Airport X because its own literal source evidence
uses an exact, currently-ADMITTED, independently-governed AirportAlias for
Airport X, and that alias is currently unambiguous across canonical
Airports." It does NOT mean the alias is globally unique in the real world,
does NOT mean the alias is IATA/ICAO-equivalent, does NOT mean the
SourceAssertion's own raw IdentityGuard evaluation was wrong, and does NOT
double-count NAME evidence - see the service module's own docstring for the
full reasoning.

WHY A SEPARATE TABLE FROM AirportAlias/SourceAssertionLegacyIdentityAttestation:
this row's subject is a DIFFERENT SourceAssertion than the one whose
evidence originally admitted the alias (that is the entire point of "cross-
source") - it cites an existing AirportAlias as its evidence rather than
minting new identity evidence itself. It is also structurally distinct
from SourceAssertionLegacyIdentityAttestation: that mechanism reviews an
assertion that NEVER ran the modern identity guard at all
(identity_guard_decision IS NULL); this one reviews an assertion that
ALREADY ran the modern identity guard and reached exactly ATTACH_PROVISIONAL
- a governed elevation of a real machine result, never a substitute for one
that never ran.

`source_assertion_id` - the SourceAssertion being elevated. `matched_airport_id`
- the canonical Airport this attestation confirms (must equal the
assertion's own current airport_id at write time - this mechanism never
attaches to, or proposes, a DIFFERENT Airport). `matched_alias_id` - the
SPECIFIC, existing, ADMITTED AirportAlias row whose evidence this
attestation relies on; provenance (alias text, its own admitting Source,
evidence excerpt, evidence class) is read from that row directly, never
copied or duplicated here (Phase 2's own explicit "do not store duplicate
derived data" instruction).

`analyst`/`reason` - plain free-text identity/justification, matching every
other reviewer/analyst field in this pipeline (RWI has no auth
infrastructure) - governance attribution only, never evidence.

STATUS: deliberately NONE this slice - unlike AirportAlias/AirportIdentifier/
SourceAssertionLegacyIdentityAttestation, this table has no REJECTED/RETIRED
vocabulary. Phase 14's own explicit instruction: "prefer safe incompleteness
... allow immutable admission only, refuse duplicate active attestation,
defer correction/reversal mechanics." `supersedes_attestation_id` exists in
the schema for future-proofing (so a later slice can add reversal support
without a migration) but is never accepted as a non-NULL value by
app.services.cross_source_alias_attestation.record_cross_source_alias_attestation()
this slice - see that module's own docstring.

Immutable (ORM before_update/before_delete event listeners), matching every
other append-only evidence/decision table in this pipeline. A mistaken
attestation is never edited in place this slice - see the service module's
own docstring for the documented limitation this implies (no reversal path
yet).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SourceAssertionCrossSourceAliasAttestation(Base):
    """One append-only, immutable record of a human's decision that an
    already-canonical, ATTACH_PROVISIONAL SourceAssertion may be treated as
    ATTACH_CONFIRMED for downstream (EB5) purposes, because its own literal
    evidence uses an existing, governed, currently-unambiguous AirportAlias.
    See module docstring for the full design rationale."""

    __tablename__ = "source_assertion_cross_source_alias_attestations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique at the DB layer - append-only; "is there a currently-active
    # attestation for this assertion" is a business rule derived by recency
    # (app.services.cross_source_alias_attestation's own read helper), never
    # a DB uniqueness constraint - matching AirportAlias's own identical
    # posture. This slice's write path additionally refuses more than one
    # row per source_assertion_id outright (see module docstring "STATUS").
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    # Must equal source_assertion.airport_id at write time - this mechanism
    # never attaches to, or proposes, a DIFFERENT Airport than the one the
    # assertion is already canonically attached to.
    matched_airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)

    # The specific, existing, ADMITTED AirportAlias row this attestation
    # relies on as its evidence - provenance is read from that row, never
    # copied here (module docstring).
    matched_alias_id: Mapped[int] = mapped_column(ForeignKey("airport_aliases.id"), index=True)

    # Plain free-text identity, matching every other reviewer/analyst field
    # in this pipeline (RWI has no auth infrastructure). Governance
    # attribution only - never evidence.
    analyst: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Self-referential, present for future-proofing only - never accepted
    # as non-NULL by the write service this slice (see module docstring
    # "STATUS"). "Current" state is still always derived by recency
    # (created_at, id), never by walking this chain, matching every other
    # append-only table in this pipeline.
    supersedes_attestation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_assertion_cross_source_alias_attestations.id"), nullable=True, index=True
    )

    # All one-directional (no back_populates) - matches every other
    # append-only evidence/decision table in this pipeline.
    source_assertion: Mapped["SourceAssertion"] = relationship()
    matched_airport: Mapped["Airport"] = relationship(foreign_keys=[matched_airport_id])
    matched_alias: Mapped["AirportAlias"] = relationship(foreign_keys=[matched_alias_id])
    supersedes: Mapped[Optional["SourceAssertionCrossSourceAliasAttestation"]] = relationship(
        remote_side="SourceAssertionCrossSourceAliasAttestation.id", foreign_keys=[supersedes_attestation_id]
    )


@event.listens_for(SourceAssertionCrossSourceAliasAttestation, "before_update")
def _prevent_cross_source_alias_attestation_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "SourceAssertionCrossSourceAliasAttestation rows are immutable; this slice supports no "
        "correction/reversal mechanics (see module docstring) - a mistaken attestation cannot be edited."
    )


@event.listens_for(SourceAssertionCrossSourceAliasAttestation, "before_delete")
def _prevent_cross_source_alias_attestation_delete(_mapper, _connection, _target) -> None:
    raise ValueError("SourceAssertionCrossSourceAliasAttestation rows are auditable and cannot be deleted.")
