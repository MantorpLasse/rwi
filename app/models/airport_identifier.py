"""First-class governed Airport identifiers (docs/architecture, "RWI -
Governed Canonical Airport Identifiers - Architecture Design" mission, the
locked design this module implements).

An `AirportIdentifier` row represents exactly one thing: an evidence-backed
claim that a specific, TYPED code (IATA, ICAO, or FAA) is the canonical
identifier of one Airport - never a translation, never an inference from
code length or shape, never a human's own aviation-trivia judgment
presented as fact.

    independent authoritative Source -> preserved SourceAssertion excerpt,
        literally establishing BOTH the code value AND its type (e.g.
        "XYZ(IATA)"), AND independently anchored to the target Airport's
        own already-established identity (canonical name, an effective
        ADMITTED AirportAlias, or an already-known canonical identifier)
        -> AirportIdentifier (this table, append-only)
        -> app.services.airport_identifier's own strictly-gated, one-time
           NULL -> value write onto EXACTLY the matching typed column:
               IATA -> Airport.iata_code
               ICAO -> Airport.icao_code
               FAA  -> Airport.faa_code
        -> existing, UNMODIFIED
           app.services.evidence_attachment_guard.candidate_airport_from_airport_like(),
           which already reads these three columns directly - no
           IdentityGuard change of any kind is required or made.

THE EVIDENCE BASIS IS NEVER A HUMAN'S OWN JUDGMENT, AND THE TYPE IS NEVER
INFERRED (this module's two central properties, mirroring
app.models.airport_alias.AirportAlias's own "evidence, never judgment"
principle exactly, plus one additional guarantee that mechanism does not
need): `evidence_class` has exactly one member this slice,
`AUTHORITATIVE_DIRECT` - see app.services.airport_identifier's own
docstring for the full mechanical standard it enforces, including the
requirement that the preserved excerpt itself literally state which TYPE
the code is (e.g. "XYZ(IATA)"), never guessed from string length or shape.
`identifier_type` is one of `AIRPORT_IDENTIFIER_TYPES` below - a caller
can never construct a row without an explicit, real type.

WHY A SEPARATE TABLE FROM AirportAlias (design mission's own explicit
Phase/Option D - rejected): codes are typed, structurally distinct from
free-text names, and are written onto three DIFFERENT existing Airport
columns depending on that type - `AirportAlias` has no `identifier_type`
concept and never writes onto `Airport` at all (aliases live entirely in
their own table). Reusing it would require broadening its own schema for
a fundamentally different validation shape - exactly the "don't overload
IDENTIFIER merely to reach a stronger outcome" warning the alias-semantics
mission itself raised.

`source_assertion_id` IS REQUIRED (not nullable), for the identical reason
`AirportAlias.source_assertion_id` is: `Source` alone carries no governed,
immutable, preserved-evidence text to mechanically verify a submitted
excerpt against.

`Airport.iata_code`/`icao_code`/`faa_code` REMAIN DIRECTLY AUTHORITATIVE
(design mission's own Option A, not B/C): this table is a governance/audit
record, never itself read by `candidate_airport_from_airport_like()` or
any other live code path - the Airport columns themselves stay the single,
simple, already-proven read path, unchanged in shape or semantics. This
table only ever WRITES to them, once, under a strict gate
(app.services.airport_identifier.record_airport_identifier()).

STATUS / APPEND-ONLY HISTORY (mirrors AirportAlias's own exact,
already-proven vocabulary and reversal-safety pattern - design mission's
own explicit "prefer simplicity" instruction, no fourth "SUPERSEDED"
state added): `ADMITTED`, `REJECTED`, `RETIRED`. A first-time admission
targeting a currently-NULL Airport column is the only way `ADMITTED` may
be a first write; `REJECTED`/`RETIRED` are always reversals of an
existing `ADMITTED` row, requiring `supersedes_identifier_id`. This slice
deliberately does NOT implement a correction/reassignment workflow that
replaces an already-populated Airport column - see
app.services.airport_identifier's own docstring "NO REPLACEMENT OF
POPULATED COLUMNS THIS SLICE" for why safe incompleteness is preferred
over broad correction machinery here.

Immutable (ORM before_update/before_delete event listeners), matching
every other append-only evidence/decision table in this pipeline
(`AirportAlias`, `ManualIdentityEvidence`, `SourceAssertionLegacyIdentityAttestation`).
A mistaken admission is never edited in place.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The three canonical Airport code columns this mechanism may ever write
# to - never any other column, never inferred, never a fourth type.
AIRPORT_IDENTIFIER_TYPES = ("IATA", "ICAO", "FAA")

# Deliberately one member this slice - see module docstring. Mirrors
# AirportAlias's own identical, already-reviewed reasoning verbatim.
AIRPORT_IDENTIFIER_EVIDENCE_CLASSES = ("AUTHORITATIVE_DIRECT",)

AIRPORT_IDENTIFIER_STATUSES = ("ADMITTED", "REJECTED", "RETIRED")


class AirportIdentifier(Base):
    """One append-only, immutable record of an evidence-backed, typed
    canonical identifier claim for one Airport. See module docstring for
    the full design rationale."""

    __tablename__ = "airport_identifiers"
    __table_args__ = (
        CheckConstraint(
            "identifier_type IN ('IATA', 'ICAO', 'FAA')",
            name="ck_airport_identifiers_identifier_type",
        ),
        CheckConstraint(
            "evidence_class IN ('AUTHORITATIVE_DIRECT')",
            name="ck_airport_identifiers_evidence_class",
        ),
        CheckConstraint(
            "status IN ('ADMITTED', 'REJECTED', 'RETIRED')",
            name="ck_airport_identifiers_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique at the DB layer - append-only; "which identifier is
    # currently active for this airport/type" is a business rule derived
    # by recency (app.services.airport_identifier's own read helper),
    # never a DB uniqueness constraint - matching AirportAlias's own
    # identical posture.
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)

    identifier_type: Mapped[str] = mapped_column(String(4))
    identifier_value: Mapped[str] = mapped_column(String(10))

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    # Required, not nullable - see module docstring.
    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    # The exact, literal excerpt this admission is evidenced by - must
    # literally contain both the identifier value AND its type (e.g.
    # "XYZ(IATA)"), and an independent identity anchor. Never mechanically
    # validated here in the model, only in the service.
    evidence_excerpt: Mapped[str] = mapped_column(Text)

    # Plain free-text identity, matching every other reviewer/analyst
    # field in this pipeline (RWI has no auth infrastructure). Governance
    # attribution only - never evidence (see module docstring).
    analyst: Mapped[str] = mapped_column(String(100))

    evidence_class: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="ADMITTED")

    # Self-referential, REQUIRED for a reversal, audit-only metadata
    # otherwise - mirrors AirportAlias.supersedes_alias_id exactly in
    # shape. "Current" state is still always derived by recency
    # (created_at, id), never by walking this chain.
    supersedes_identifier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airport_identifiers.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # All one-directional (no back_populates) - matches every other
    # append-only evidence/decision table in this pipeline.
    airport: Mapped["Airport"] = relationship(foreign_keys=[airport_id])
    source: Mapped["Source"] = relationship()
    source_assertion: Mapped["SourceAssertion"] = relationship()
    supersedes: Mapped[Optional["AirportIdentifier"]] = relationship(
        remote_side="AirportIdentifier.id", foreign_keys=[supersedes_identifier_id]
    )


@event.listens_for(AirportIdentifier, "before_update")
def _prevent_airport_identifier_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "AirportIdentifier rows are immutable; a status change (REJECTED/RETIRED) is recorded as a "
        "new, appended row naming this one as supersedes_identifier_id, never an edit of an existing one."
    )


@event.listens_for(AirportIdentifier, "before_delete")
def _prevent_airport_identifier_delete(_mapper, _connection, _target) -> None:
    raise ValueError("AirportIdentifier rows are auditable and cannot be deleted.")
