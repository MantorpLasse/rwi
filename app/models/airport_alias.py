"""First-class governed Airport aliases (docs/architecture, "RWI - Governed
Canonical Airport Aliases - Cross-Script Identity Design" mission, the
locked design this module implements).

An `AirportAlias` row represents exactly one thing: an evidence-backed
claim that a specific literal string is ALSO a valid name for one
canonical Airport - never a translation, never a transliteration, never a
human's own linguistic judgment call presented as fact.

    independent authoritative Source -> preserved SourceAssertion excerpt
        -> AirportAlias (this table, append-only)
        -> app.services.airport_alias.get_admitted_airport_aliases()
        -> app.services.evidence_attachment_guard.candidate_airport_from_airport_like(
               airport, aliases=...)
           (existing, UNMODIFIED - CandidateAirport.aliases and
           _name_evidence()'s own exact normalized-comparison logic
           already consume this generically; see that module's own
           docstring)
        -> existing, unmodified evaluate_attachment()/
           evaluate_attachment_for_candidates()

THE EVIDENCE BASIS IS NEVER A HUMAN'S OWN JUDGMENT (this module's single
most important property, and the reason `evidence_class` deliberately has
exactly ONE member this slice): `analyst` records who transcribed and
governed the admission - GOVERNANCE, never the EVIDENCE. The evidence
itself must be `AUTHORITATIVE_DIRECT`: an already-preserved
SourceAssertion whose own excerpt independently, literally co-presents
BOTH the proposed alias AND an already-established identity fact of the
SAME Airport (its current canonical name, or one of its existing
iata_code/icao_code/faa_code) - see
app.services.airport_alias.check_airport_alias_admission_eligibility()'s
own docstring for the full precondition chain, including the mandatory
anti-circularity gate. `HUMAN_RESOLUTION` is deliberately NOT a member of
`AIRPORT_ALIAS_EVIDENCE_CLASSES` - a reviewer/analyst is part of
governance, never a substitute for evidence (design mission's own explicit
Phase 3 clarification). `CORROBORATED_DIRECT` is deliberately NOT
implemented this slice either: representing "multiple independent sources,
each individually insufficient, jointly sufficient" truthfully would
require a many-to-many evidence model this one-row-per-admission schema
cannot honestly provide - see the service module's own docstring for the
full reasoning (design mission's own explicit Phase 10 instruction: prefer
supporting only AUTHORITATIVE_DIRECT over faking corroboration).

GENERIC, NOT SOURCE-FAMILY-SPECIFIC: nothing in this model or its writer
mentions Korea, Sacheon, translation, or transliteration - it is the same
generic capability regardless of which airport or script motivated its
existence. There is no language-detection, no translation table, no
transliteration logic anywhere in this module.

`source_assertion_id` IS REQUIRED (deliberately NOT nullable, a
strengthening beyond what the design report sketched - design mission's
own Phase 7 explicitly invited re-inspecting this at implementation time):
`app.models.source.Source` carries no governed, immutable, preserved-
evidence field of its own (`Source.summary` is a plain, ordinarily-mutable
free-text column with none of `SourceAssertion.raw_relevant_text`'s
governance discipline) - only `SourceAssertion` gives this mechanism a
safe, already-established text container to mechanically verify the
submitted excerpt against. Requiring it structurally guarantees every
admission excerpt is checked against real, already-preserved evidence,
exactly mirroring `app.models.manual_identity_evidence.ManualIdentityEvidence`'s
own precedent.

`source_id` is redundant with `source_assertion.source_id` (the FK already
implies it) but copied here deliberately, for the identical reason
`ManualIdentityEvidence.source_id` is - a fact that must survive
independently of a live join, and lets the anti-circularity check
(app.services.airport_alias) reason about Source-level independence
directly against a persisted, snapshotted value.

STATUS / APPEND-ONLY HISTORY (design mission Phase 4/12): exactly three
members - `ADMITTED` (the only status a first-time admission for a new
alias may have), `REJECTED` (a later reviewer determined a previously-
ADMITTED alias was actually wrong), `RETIRED` (a previously-ADMITTED alias
is no longer current, e.g. an airport rename - was correct once, isn't
now). Both REJECTED and RETIRED are always REVERSALS of an existing
ADMITTED row, never a first write. `supersedes_alias_id` is REQUIRED (not
merely audit-only metadata) exactly when the new row's own `status`
CONTRADICTS the immediately-latest existing row's own `status` for the
SAME (airport_id, alias) pair (ADMITTED <-> REJECTED/RETIRED, in either
direction) - see app.services.airport_alias's own docstring for the full
reversal-safety reasoning, mirroring
app.models.source_assertion_legacy_identity_attestation.SourceAssertionLegacyIdentityAttestation's
own identical, already-proven pattern exactly.

Immutable (ORM before_update/before_delete event listeners), matching
every other append-only evidence/decision table in this pipeline. A
mistaken admission is never edited in place - a human changing their mind
always appends a NEW row (REJECTED/RETIRED, naming the row it supersedes),
never edits an existing one.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Deliberately one member this slice - see module docstring "THE EVIDENCE
# BASIS IS NEVER A HUMAN'S OWN JUDGMENT." CORROBORATED_DIRECT is
# deliberately absent (would require a many-to-many evidence model this
# schema cannot honestly represent - see the service module's own
# docstring). HUMAN_RESOLUTION is deliberately absent and MUST NEVER be
# added as a member here - a reviewer is governance, never evidence.
AIRPORT_ALIAS_EVIDENCE_CLASSES = ("AUTHORITATIVE_DIRECT",)

AIRPORT_ALIAS_STATUSES = ("ADMITTED", "REJECTED", "RETIRED")


class AirportAlias(Base):
    """One append-only, immutable record of an evidence-backed alternate
    name for one canonical Airport. See module docstring for the full
    design rationale."""

    __tablename__ = "airport_aliases"
    __table_args__ = (
        CheckConstraint(
            "evidence_class IN ('AUTHORITATIVE_DIRECT')",
            name="ck_airport_aliases_evidence_class",
        ),
        CheckConstraint(
            "status IN ('ADMITTED', 'REJECTED', 'RETIRED')",
            name="ck_airport_aliases_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique at the DB layer - append-only; "which alias is currently
    # active for this airport" is a business rule derived by recency
    # (app.services.airport_alias.get_admitted_airport_aliases()), never a
    # DB uniqueness constraint - matching
    # SourceAssertionLegacyIdentityAttestation's own identical posture.
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)

    alias: Mapped[str] = mapped_column(String(300))

    # Audit-only - NEVER read by any guard/matching code (see module
    # docstring). Plain nullable free text, no controlled vocabulary: the
    # guard needs no language awareness to do exact string comparison, so
    # inventing an enum here would be pure overengineering.
    language: Mapped[Optional[str]] = mapped_column(String(50))
    script: Mapped[Optional[str]] = mapped_column(String(50))

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    # Required, not nullable - see module docstring.
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    # The exact, literal excerpt of source_assertion.raw_relevant_text this
    # admission is evidenced by - never a paraphrase or summary. Must
    # literally contain both the alias and an independent identity anchor
    # (app.services.airport_alias's own eligibility check) - never
    # mechanically validated here in the model, only in the service.
    evidence_excerpt: Mapped[str] = mapped_column(Text)

    # Plain free-text identity, matching every other reviewer/analyst
    # field in this pipeline (RWI has no auth infrastructure). Governance
    # attribution only - never evidence (see module docstring).
    analyst: Mapped[str] = mapped_column(String(100))

    evidence_class: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="ADMITTED")

    # Self-referential, REQUIRED for a reversal (see module docstring),
    # audit-only metadata otherwise - mirrors
    # SourceAssertionLegacyIdentityAttestation.supersedes_attestation_id
    # exactly in shape. "Current" state is still always derived by
    # recency (created_at, id), never by walking this chain.
    supersedes_alias_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airport_aliases.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # All one-directional (no back_populates) - matches every other
    # append-only evidence/decision table in this pipeline; "many
    # AirportAlias rows may exist per Airport/Source/SourceAssertion" is
    # provable by a plain query, never an eagerly-loaded ORM collection.
    airport: Mapped["Airport"] = relationship(foreign_keys=[airport_id])
    source: Mapped["Source"] = relationship()
    source_assertion: Mapped["SourceAssertion"] = relationship()
    supersedes: Mapped[Optional["AirportAlias"]] = relationship(
        remote_side="AirportAlias.id", foreign_keys=[supersedes_alias_id]
    )


@event.listens_for(AirportAlias, "before_update")
def _prevent_airport_alias_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "AirportAlias rows are immutable; a status change (REJECTED/RETIRED) is recorded as a new, "
        "appended row naming this one as supersedes_alias_id, never an edit of an existing one."
    )


@event.listens_for(AirportAlias, "before_delete")
def _prevent_airport_alias_delete(_mapper, _connection, _target) -> None:
    raise ValueError("AirportAlias rows are auditable and cannot be deleted.")
