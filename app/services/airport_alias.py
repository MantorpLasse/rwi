"""Governed persistence, impact-preview, and consumption for first-class
Airport aliases (docs/architecture, "RWI - Governed Canonical Airport
Aliases - Cross-Script Identity Design" mission, the locked design this
module implements; see app.models.airport_alias for the persisted row
shape).

    independent, already-preserved SourceAssertion excerpt
        -> check_airport_alias_admission_eligibility() (evidence-quality
           gates: literal containment, independent identity anchor,
           source reliability, anti-circularity)
        -> record_airport_alias()
        -> one AirportAlias row, append-only
        -> get_admitted_airport_aliases() (pure read helper)
        -> fed into the EXISTING, UNMODIFIED
           app.services.evidence_attachment_guard.candidate_airport_from_airport_like(
               airport, aliases=...)
        -> existing, unmodified evaluate_attachment()/
           evaluate_attachment_for_candidates()
        -> STOP. No SourceAssertion mutation, no automatic re-evaluation,
           no Signal. A later, separately-authorized, EXPLICIT call to
           app.services.resolved_candidate_evidence_reevaluation.reevaluate_resolved_candidate_evidence()
           (EB4, unmodified except its own aliases-sourcing addition) is
           the only thing that ever lets a newly-admitted alias affect any
           already-persisted SourceAssertion's identity outcome.

EVIDENCE, NEVER JUDGMENT (module docstring of app.models.airport_alias):
`evidence_class` has exactly one member this slice, `AUTHORITATIVE_DIRECT`
- a reviewer's own linguistic competence or an algorithm's transliteration
output is NEVER, by itself, sufficient grounds to mint canonical identity;
see check_airport_alias_admission_eligibility()'s own precondition list
below for the full mechanical standard AUTHORITATIVE_DIRECT actually
enforces.

WHY NOT CORROBORATED_DIRECT THIS SLICE: representing "multiple
independent sources, each individually insufficient, jointly sufficient"
honestly would require a many-to-many evidence-to-admission model (several
Source/SourceAssertion references contributing to ONE admission decision)
- this table's one-row-per-admission shape cannot represent that honestly.
Rather than label a single-source admission "CORROBORATED_DIRECT" merely
because a human claims other sources exist, this slice supports only
AUTHORITATIVE_DIRECT, where EVERY admission stands entirely on its own,
independently-sufficient evidence. Multiple independent AUTHORITATIVE_DIRECT
admissions for the same (airport, alias) are still possible (each is its
own fully-sufficient row) - deliberately rejected as a duplicate by this
slice anyway (see DuplicateActiveAliasError), since this slice does not
yet need or model "N independent confirmations" as a stronger status than
one sufficient admission; a future slice could revisit this.

MECHANICAL AUTHORITATIVE_DIRECT STANDARD (Phase 9's own explicit
instruction: do not invent a global authority ontology if the schema
cannot support one truthfully). `Source.reliability_level` is a plain,
unconstrained free-text column - already documented elsewhere in this
codebase (app.services.promotion_policy_evaluation's own SOURCE-AUTHORITY
TIER note) as "a real, unresolved infrastructure gap" that "cannot today
distinguish a primary authority's own record from ... news reporting."
This module does not pretend otherwise or invent a new authority-tier
column: it uses the ONE mechanically-checkable value already established
in production (reliability_level == "official", the column's own real
default, currently 70 of 83 real Source rows) as the narrowest available
proxy, and REJECTS every other value ("unverified", "internal", or
anything unrecognized) rather than accept a claim on trust. This is an
imperfect proxy, explicitly acknowledged as such - a Source could be
"official" without truly being a primary aviation/government authority for
THIS specific identity fact - but it is strictly narrower and safer than
accepting any Source at all, and does not introduce a second, competing
authority concept alongside the one this codebase has already flagged as
unresolved.

ANTI-CIRCULARITY (Phase 8's own hard gate - the single most important
correctness property in this module): admitting an alias using a
SourceAssertion whose OWN outcome would itself flip because of that exact
alias is circular reasoning wearing governance's clothes - it proves
nothing beyond "this SourceAssertion already says so." `_simulate_alias_impact()`
(the same pure simulation `preview_airport_alias_admission_impact()` uses)
is run BEFORE any write: for every SourceAssertion sharing the target
Airport that already has a persisted EvidenceBag, this module compares its
outcome with and without the proposed alias. If ANY assertion whose
outcome WOULD change shares its `source_id` with the admitting evidence's
own `source_id`, admission is refused (CircularAliasEvidenceError) - this
also, structurally, always catches the admitting SourceAssertion using
itself as evidence (its own source_id trivially equals itself), and
additionally catches a DIFFERENT SourceAssertion from the SAME document/
Source being used to "corroborate" its own sibling. No source IDs are
ever hard-coded; this check is entirely generic over airport_id/alias.

SOURCE_ASSERTION REQUIRED, NOT OPTIONAL (deliberate strengthening beyond
the design report's own sketch - see app.models.airport_alias's own
docstring "`source_assertion_id` IS REQUIRED" for the full reasoning):
`Source` alone carries no governed, immutable, preserved-evidence text to
mechanically verify a submitted excerpt against.

EXCERPT-VS-PRESERVED-EVIDENCE BINDING (a deliberate strengthening beyond
app.services.manual_identity_evidence's own precedent, where this check
lives only in the CLI): because one admitted alias can affect MANY
existing SourceAssertions at once (see the impact-preview machinery
below), this module enforces, INSIDE THE SERVICE ITSELF (not merely as a
CLI safety layer), that the submitted `evidence_excerpt` is a literal,
conservatively-normalized substring of `source_assertion.raw_relevant_text`
- never merely typed up freehand by the analyst.

NO TRANSLATION. NO TRANSLITERATION. NO FUZZY MATCHING. Every literal
containment check below reuses
app.services.manual_identity_evidence.normalize_for_containment_check()/
excerpt_contains_value() VERBATIM - this module introduces NO second
normalization implementation (design mission's own explicit Phase 18
instruction). Those functions do casefold + whitespace-collapse only, no
Unicode canonical-form (NFC/NFKC) normalization - a pre-existing, narrow,
UNFIXED latent gap this module inherits and does not attempt to fix (see
that module's own docstring; reported, not repaired, per this mission's
own explicit instruction).

BLAST-RADIUS SAFETY: admitting an alias NEVER automatically re-evaluates
any SourceAssertion. `record_airport_alias()` never calls
reevaluate_resolved_candidate_evidence() (EB4) or mutates any
SourceAssertion column. A later, separate, explicit, one-`source_assertion_id`-
at-a-time governed call is required for any existing evidence to actually
be affected - alias admission and EB4 re-evaluation remain two distinct
operations, by design.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only, so any constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from app.models import Airport, Runway, Source, SourceAssertion
from app.models.airport_alias import AIRPORT_ALIAS_EVIDENCE_CLASSES, AIRPORT_ALIAS_STATUSES, AirportAlias
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    candidate_airport_from_airport_like,
    evaluate_attachment,
)
from app.services.evidence_bag_serialization import deserialize_evidence_bag
from app.services.manual_identity_evidence import excerpt_contains_value, normalize_for_containment_check
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "AirportNotFoundError",
    "SourceNotFoundError",
    "SourceAssertionSourceMismatchError",
    "EmptyAliasError",
    "EmptyEvidenceExcerptError",
    "EmptyAnalystError",
    "AliasNotInExcerptError",
    "ExcerptNotInPreservedEvidenceError",
    "NoIdentityAnchorError",
    "InsufficientSourceReliabilityError",
    "CircularAliasEvidenceError",
    "DuplicateActiveAliasError",
    "ConflictingAliasStatusRequiresSupersessionError",
    "AliasImpactRow",
    "AliasImpactPreview",
    "AirportAliasAdmissionResult",
    "get_admitted_airport_aliases",
    "preview_airport_alias_admission_impact",
    "check_airport_alias_admission_eligibility",
    "record_airport_alias",
]

# The ONE mechanically-checkable reliability tier this module accepts for
# AUTHORITATIVE_DIRECT admission - see module docstring "MECHANICAL
# AUTHORITATIVE_DIRECT STANDARD" for the full, honest reasoning.
_REQUIRED_RELIABILITY_LEVEL = "official"

_ADMITTED = "ADMITTED"
_WITHDRAWN_STATUSES = ("REJECTED", "RETIRED")
_AIRPORT_ALIASES_TABLE = "airport_aliases"


def _airport_aliases_table_exists(session: Session) -> bool:
    """Backward-compatibility guard, mirroring
    app.services.effective_identity_guard_decision._identity_guard_evaluations_table_exists()'s
    own exact reasoning and technique: this migration (Phase 20 of this
    mission) is deliberately never applied to the REAL business database
    within this mission - reevaluate_resolved_candidate_evidence() (EB4)
    now calls get_admitted_airport_aliases() unconditionally for EVERY
    SourceAssertion re-evaluation, including ones with zero relationship
    to aliases. Querying `airport_aliases` unconditionally would make
    every such call raise a raw OperationalError against a real database
    that has never run this mission's own migration - a severe backward-
    compatibility regression this module must never introduce. Falls back
    to "no aliases exist yet" (an empty frozenset), the correct answer for
    a database that has never admitted any alias. Existence-only, queried
    through the Session's OWN connection via session.execute() (not a
    second engine.connect()), matching EB5's own identical, previously-
    adversarially-reviewed reasoning for why that specific technique
    matters."""
    return (
        session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table"),
            {"table": _AIRPORT_ALIASES_TABLE},
        ).first()
        is not None
    )


class AirportNotFoundError(ValueError):
    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(f"airport_id={airport_id!r} does not reference an existing Airport")


class SourceNotFoundError(ValueError):
    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        super().__init__(f"source_id={source_id!r} does not reference an existing Source")


class SourceAssertionSourceMismatchError(ValueError):
    """Raised when the caller-supplied `source_id` does not equal
    `source_assertion.source_id` exactly - prevents citing evidence
    attributed to one Source while actually pointing at a SourceAssertion
    belonging to a different one."""

    def __init__(self, source_assertion_id: int, *, expected_source_id: int, supplied_source_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.expected_source_id = expected_source_id
        self.supplied_source_id = supplied_source_id
        super().__init__(
            f"supplied source_id={supplied_source_id!r} does not equal SourceAssertion "
            f"{source_assertion_id}'s own source_id={expected_source_id!r}."
        )


class EmptyAliasError(ValueError):
    def __init__(self) -> None:
        super().__init__("alias is required and cannot be empty")


class EmptyEvidenceExcerptError(ValueError):
    def __init__(self) -> None:
        super().__init__("evidence_excerpt is required and cannot be empty")


class EmptyAnalystError(ValueError):
    def __init__(self) -> None:
        super().__init__("analyst is required and cannot be empty")


class AliasNotInExcerptError(ValueError):
    def __init__(self, alias: str) -> None:
        self.alias = alias
        super().__init__(
            f"alias={alias!r} does not occur literally within evidence_excerpt - a proposed alias "
            "must be mechanically verifiable against the exact evidence text."
        )


class ExcerptNotInPreservedEvidenceError(ValueError):
    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"evidence_excerpt does not occur literally within SourceAssertion {source_assertion_id}'s "
            "own preserved raw_relevant_text (or that field is empty) - refusing an excerpt that cannot "
            "be verified against real preserved evidence."
        )


class NoIdentityAnchorError(ValueError):
    """Raised when evidence_excerpt contains the proposed alias but NEVER
    independently ties it to the target Airport - neither the Airport's
    own current canonical name nor any of its existing iata_code/
    icao_code/faa_code occurs in the same excerpt. Without this anchor,
    nothing distinguishes "this text names the target Airport" from "this
    text names some other, similarly-named place" (Phase 6/19's own
    wrong-airport threat)."""

    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(
            f"evidence_excerpt contains the proposed alias but no independent identity anchor "
            f"(Airport {airport_id}'s own current name or an existing iata_code/icao_code/faa_code) - "
            "refusing to bind an alias to this Airport without the source itself co-presenting an "
            "already-established identity fact."
        )


class InsufficientSourceReliabilityError(ValueError):
    def __init__(self, source_id: int, *, reliability_level: "str | None") -> None:
        self.source_id = source_id
        self.reliability_level = reliability_level
        super().__init__(
            f"Source {source_id}'s reliability_level={reliability_level!r} does not meet the required "
            f"{_REQUIRED_RELIABILITY_LEVEL!r} tier for AUTHORITATIVE_DIRECT alias admission - see module "
            "docstring for why this mechanical proxy, imperfect as it is, is the narrowest available."
        )


class CircularAliasEvidenceError(ValueError):
    """Raised when the proposed alias would itself change the outcome of
    a SourceAssertion sharing the admitting evidence's own source_id -
    see module docstring "ANTI-CIRCULARITY" for the full reasoning. Never
    reachable via a hard-coded source id; computed fresh from the real
    impact simulation every time."""

    def __init__(self, airport_id: int, *, alias: str, conflicting_source_assertion_ids: "tuple[int, ...]") -> None:
        self.airport_id = airport_id
        self.alias = alias
        self.conflicting_source_assertion_ids = conflicting_source_assertion_ids
        super().__init__(
            f"admitting alias={alias!r} for Airport {airport_id} would change the outcome of "
            f"SourceAssertion(s) {conflicting_source_assertion_ids!r}, which share the admitting "
            "evidence's own source_id - the evidence source must be independent of any assertion its "
            "own admission would confirm."
        )


class DuplicateActiveAliasError(ValueError):
    def __init__(self, airport_id: int, *, alias: str, existing_alias_id: int) -> None:
        self.airport_id = airport_id
        self.alias = alias
        self.existing_alias_id = existing_alias_id
        super().__init__(
            f"Airport {airport_id} already has an ADMITTED alias {alias!r} (AirportAlias "
            f"#{existing_alias_id}) - refusing to record a duplicate active admission."
        )


class ConflictingAliasStatusRequiresSupersessionError(ValueError):
    def __init__(
        self, airport_id: int, *, alias: str, latest_alias_id: "int | None", latest_status: "str | None", new_status: str,
    ) -> None:
        self.airport_id = airport_id
        self.alias = alias
        self.latest_alias_id = latest_alias_id
        self.latest_status = latest_status
        self.new_status = new_status
        super().__init__(
            f"status={new_status!r} for alias={alias!r} on Airport {airport_id} requires "
            f"supersedes_alias_id={latest_alias_id!r}, naming the current latest row "
            f"(status={latest_status!r}) explicitly."
        )


@dataclass(frozen=True)
class AliasImpactRow:
    """One SourceAssertion's outcome, with and without the proposed
    alias - never persisted, pure simulation output."""

    source_assertion_id: int
    source_id: int
    has_snapshot: bool
    current_outcome: "str | None"
    hypothetical_outcome: "str | None"
    changed: bool


@dataclass(frozen=True)
class AliasImpactPreview:
    """Deterministic, ORM-free summary of
    preview_airport_alias_admission_impact()'s own read-only simulation."""

    airport_id: int
    proposed_alias: str
    rows: "tuple[AliasImpactRow, ...]"

    @property
    def changed_source_assertion_ids(self) -> "tuple[int, ...]":
        return tuple(row.source_assertion_id for row in self.rows if row.changed)


@dataclass(frozen=True)
class AirportAliasAdmissionResult:
    """Deterministic, ORM-free summary of what record_airport_alias() did."""

    alias_id: int
    airport_id: int
    alias: str
    status: str
    evidence_class: "str | None"
    source_id: int
    source_assertion_id: int
    superseded_alias_id: "int | None"


def _load_airport_with_topology(session: Session, airport_id: int) -> "Airport | None":
    """Mirrors reevaluate_resolved_candidate_evidence()'s own eager-load
    convention exactly (selectinload both levels up front) - avoids one
    extra query per runway when this module evaluates many
    SourceAssertions for the same Airport in one impact-preview call."""
    return session.scalar(
        select(Airport)
        .where(Airport.id == airport_id)
        .options(selectinload(Airport.runways).selectinload(Runway.runway_ends))
    )


def get_admitted_airport_aliases(session: Session, airport_id: int) -> "frozenset[str]":
    """Pure read. Derives the CURRENTLY admitted alias strings for one
    Airport SOLELY from the append-only AirportAlias history - "current"
    is always determined by recency (created_at, id) per distinct
    normalized alias, never a DB uniqueness constraint, matching every
    other append-only table's own established convention in this
    pipeline. A RETIRED or REJECTED alias (i.e. the latest row for that
    normalized alias no longer has status=ADMITTED) is never returned. No
    caching; never mutates Airport or anything else.

    Falls back cleanly to an empty frozenset ("no aliases exist yet") when
    the airport_aliases table has never been migrated at all - see
    _airport_aliases_table_exists()'s own docstring for why this matters:
    this function is now called unconditionally by EB4's re-evaluation
    path for EVERY SourceAssertion, including the real, not-yet-migrated
    production database."""
    if not _airport_aliases_table_exists(session):
        return frozenset()
    rows = session.scalars(
        select(AirportAlias)
        .where(AirportAlias.airport_id == airport_id)
        .order_by(AirportAlias.created_at.asc(), AirportAlias.id.asc())
    ).all()
    latest_by_key: "dict[str, AirportAlias]" = {}
    for row in rows:
        latest_by_key[_alias_key(row.alias)] = row
    return frozenset(row.alias for row in latest_by_key.values() if row.status == _ADMITTED)


def _alias_key(alias: str) -> str:
    """Groups AirportAlias rows referring to the same logical alias
    string - reuses app.services.manual_identity_evidence's own
    containment-check normalization verbatim (no second normalization
    implementation; see module docstring)."""
    return normalize_for_containment_check(alias)


def _candidate_for_airport(airport: Airport, *, extra_alias: "str | None" = None, aliases: "frozenset[str]" = frozenset()) -> CandidateAirport:
    effective_aliases = aliases | ({extra_alias} if extra_alias else set())
    return candidate_airport_from_airport_like(airport, aliases=frozenset(effective_aliases))


def _simulate_alias_impact(
    session: Session, *, airport: Airport, proposed_alias: str,
) -> "tuple[AliasImpactRow, ...]":
    """Shared, pure simulation core for BOTH
    preview_airport_alias_admission_impact() (Phase 15) and the
    anti-circularity gate inside check_airport_alias_admission_eligibility()
    (Phase 8) - one implementation, never duplicated. Persists nothing;
    never flushes; never commits. For every SourceAssertion sharing this
    Airport's own airport_id that has an existing, immutable
    SourceAssertionEvidenceBag snapshot, computes the REAL, unmodified
    evaluate_attachment() outcome twice - once against the currently-
    admitted alias set alone, once with the proposed alias added - and
    reports whether the outcome changed. A row with no snapshot is
    reported as NO_SNAPSHOT/unaffected, never fabricated into an
    evaluation (Phase 15's own explicit instruction)."""
    current_aliases = get_admitted_airport_aliases(session, airport.id)
    candidate_without = _candidate_for_airport(airport, aliases=current_aliases)
    candidate_with = _candidate_for_airport(airport, aliases=current_aliases, extra_alias=proposed_alias)

    assertions = session.scalars(
        select(SourceAssertion).where(SourceAssertion.airport_id == airport.id)
    ).all()

    rows: "list[AliasImpactRow]" = []
    for assertion in assertions:
        snapshot = session.scalar(
            select(SourceAssertionEvidenceBag).where(
                SourceAssertionEvidenceBag.source_assertion_id == assertion.id
            )
        )
        if snapshot is None:
            rows.append(AliasImpactRow(
                source_assertion_id=assertion.id, source_id=assertion.source_id, has_snapshot=False,
                current_outcome=None, hypothetical_outcome=None, changed=False,
            ))
            continue
        evidence = deserialize_evidence_bag(snapshot.evidence_bag_json)
        current_decision = evaluate_attachment(candidate_without, evidence)
        hypothetical_decision = evaluate_attachment(candidate_with, evidence)
        rows.append(AliasImpactRow(
            source_assertion_id=assertion.id, source_id=assertion.source_id, has_snapshot=True,
            current_outcome=current_decision.outcome.value,
            hypothetical_outcome=hypothetical_decision.outcome.value,
            changed=current_decision.outcome != hypothetical_decision.outcome,
        ))
    return tuple(rows)


def preview_airport_alias_admission_impact(
    session: Session, *, airport_id: int, proposed_alias: str,
) -> AliasImpactPreview:
    """Read-only. Never persists, flushes, or commits anything. See
    _simulate_alias_impact()'s own docstring for the exact semantics."""
    if not proposed_alias or not proposed_alias.strip():
        raise EmptyAliasError()
    with session.no_autoflush:
        airport = _load_airport_with_topology(session, airport_id)
        if airport is None:
            raise AirportNotFoundError(airport_id)
        rows = _simulate_alias_impact(session, airport=airport, proposed_alias=proposed_alias)
    return AliasImpactPreview(airport_id=airport_id, proposed_alias=proposed_alias, rows=rows)


def check_airport_alias_admission_eligibility(
    session: Session,
    *,
    airport: Airport,
    source: Source,
    source_assertion: SourceAssertion,
    alias: str,
    evidence_excerpt: str,
    evidence_class: str,
) -> AliasImpactPreview:
    """The full evidence-quality gate for a NEW ADMITTED alias (never
    applied to a REJECTED/RETIRED withdrawal - see record_airport_alias()).
    Raises the first violated precondition; never partially reports.
    Returns the freshly-computed AliasImpactPreview so the write path
    below reuses this exact computation rather than trusting separately-
    supplied, possibly-stale CLI output (Phase 16's own explicit
    instruction).

    Preconditions, checked in this exact order:

    1. source.id == source_assertion.source_id -> SourceAssertionSourceMismatchError
    2. alias non-empty -> EmptyAliasError
    3. evidence_excerpt non-empty -> EmptyEvidenceExcerptError
    4. evidence_class is a real vocabulary member -> ValueError
    5. alias literally occurs in evidence_excerpt -> AliasNotInExcerptError
    6. evidence_excerpt literally occurs in source_assertion.raw_relevant_text
       -> ExcerptNotInPreservedEvidenceError
    7. evidence_excerpt also literally contains an independent identity
       anchor (airport.name or a non-null iata_code/icao_code/faa_code)
       -> NoIdentityAnchorError
    8. source.reliability_level == "official" -> InsufficientSourceReliabilityError
    9. anti-circularity: no SourceAssertion whose outcome would change
       shares source_assertion's own source_id -> CircularAliasEvidenceError
    """
    if source.id != source_assertion.source_id:
        raise SourceAssertionSourceMismatchError(
            source_assertion.id, expected_source_id=source_assertion.source_id, supplied_source_id=source.id,
        )
    if not alias or not alias.strip():
        raise EmptyAliasError()
    if not evidence_excerpt or not evidence_excerpt.strip():
        raise EmptyEvidenceExcerptError()
    if evidence_class not in AIRPORT_ALIAS_EVIDENCE_CLASSES:
        raise ValueError(f"evidence_class must be one of {AIRPORT_ALIAS_EVIDENCE_CLASSES!r}, got {evidence_class!r}")
    if not excerpt_contains_value(evidence_excerpt, alias):
        raise AliasNotInExcerptError(alias)
    if not source_assertion.raw_relevant_text or not excerpt_contains_value(source_assertion.raw_relevant_text, evidence_excerpt):
        raise ExcerptNotInPreservedEvidenceError(source_assertion.id)

    identity_anchors = [value for value in (airport.name, airport.iata_code, airport.icao_code, airport.faa_code) if value]
    if not any(excerpt_contains_value(evidence_excerpt, anchor) for anchor in identity_anchors):
        raise NoIdentityAnchorError(airport.id)

    if (source.reliability_level or "") != _REQUIRED_RELIABILITY_LEVEL:
        raise InsufficientSourceReliabilityError(source.id, reliability_level=source.reliability_level)

    with session.no_autoflush:
        rows = _simulate_alias_impact(session, airport=airport, proposed_alias=alias)
    conflicting = tuple(
        row.source_assertion_id for row in rows if row.changed and row.source_id == source_assertion.source_id
    )
    if conflicting:
        raise CircularAliasEvidenceError(airport.id, alias=alias, conflicting_source_assertion_ids=conflicting)

    return AliasImpactPreview(airport_id=airport.id, proposed_alias=alias, rows=rows)


def _get_latest_alias_row(session: Session, *, airport_id: int, alias: str) -> "AirportAlias | None":
    """Read-only. "Current" is always recency (created_at, id) among rows
    for the same (airport_id, normalized alias) - never derived by
    walking supersedes_alias_id, matching every other append-only table's
    own convention."""
    key = _alias_key(alias)
    rows = session.scalars(
        select(AirportAlias)
        .where(AirportAlias.airport_id == airport_id)
        .order_by(AirportAlias.created_at.asc(), AirportAlias.id.asc())
    ).all()
    matching = [row for row in rows if _alias_key(row.alias) == key]
    return matching[-1] if matching else None


def record_airport_alias(
    session: Session,
    *,
    airport_id: int,
    source_id: int,
    source_assertion_id: int,
    alias: str,
    evidence_excerpt: str,
    analyst: str,
    evidence_class: str = "AUTHORITATIVE_DIRECT",
    language: "str | None" = None,
    script: "str | None" = None,
    status: str = _ADMITTED,
    supersedes_alias_id: "int | None" = None,
) -> AirportAliasAdmissionResult:
    """Validates every precondition, persists exactly one immutable
    AirportAlias row. Never commits; calls session.flush() so any
    constraint violation surfaces immediately. Never touches
    SourceAssertion, EvidenceBag, or triggers EB4 re-evaluation (see
    module docstring "BLAST-RADIUS SAFETY").

    For status="ADMITTED" (the only status a first-time admission may
    have): runs the FULL evidence-quality gate
    (check_airport_alias_admission_eligibility(), items 1-9 above), then
    refuses a duplicate if a currently-ADMITTED row already exists for
    this exact (airport_id, alias) UNLESS supersedes_alias_id explicitly
    names a REJECTED/RETIRED row being reversed back to ADMITTED
    (reversal-safety, mirroring
    app.services.source_assertion_legacy_identity_attestation's own
    identical pattern).

    For status in ("REJECTED", "RETIRED") (always a withdrawal of a prior
    ADMITTED row, never a first write): runs only the light checks
    (Source/SourceAssertion binding, alias/excerpt/analyst non-empty) -
    the full evidence-quality gate (identity anchor, reliability,
    anti-circularity) does not apply to WITHDRAWING evidence. Requires
    supersedes_alias_id to name the CURRENT latest row for this
    (airport_id, alias), which must itself be ADMITTED.
    """
    with session.no_autoflush:
        airport = session.get(Airport, airport_id)
        if airport is None:
            raise AirportNotFoundError(airport_id)
        source = session.get(Source, source_id)
        if source is None:
            raise SourceNotFoundError(source_id)
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        if status not in AIRPORT_ALIAS_STATUSES:
            raise ValueError(f"status must be one of {AIRPORT_ALIAS_STATUSES!r}, got {status!r}")
        if not analyst or not analyst.strip():
            raise EmptyAnalystError()

        latest = _get_latest_alias_row(session, airport_id=airport_id, alias=alias)

        if status == _ADMITTED:
            preview = check_airport_alias_admission_eligibility(
                session, airport=airport, source=source, source_assertion=source_assertion,
                alias=alias, evidence_excerpt=evidence_excerpt, evidence_class=evidence_class,
            )
            del preview  # already validated; result not otherwise needed here
            if latest is not None and latest.status == _ADMITTED:
                raise DuplicateActiveAliasError(airport_id, alias=alias, existing_alias_id=latest.id)
            if latest is not None and latest.status in _WITHDRAWN_STATUSES:
                # Deliberate reversal back to ADMITTED after a prior
                # withdrawal - reversal-safety requires explicit naming.
                if supersedes_alias_id != latest.id:
                    raise ConflictingAliasStatusRequiresSupersessionError(
                        airport_id, alias=alias, latest_alias_id=latest.id,
                        latest_status=latest.status, new_status=status,
                    )
            elif supersedes_alias_id is not None:
                raise ValueError("supersedes_alias_id must be omitted for a first-time admission")
        else:
            # REJECTED / RETIRED: a withdrawal, never a first write. Only
            # the light checks apply (module docstring); the full
            # evidence-quality gate is for ADDING new positive alias
            # evidence, not removing it.
            if not alias or not alias.strip():
                raise EmptyAliasError()
            if not evidence_excerpt or not evidence_excerpt.strip():
                raise EmptyEvidenceExcerptError()
            if source.id != source_assertion.source_id:
                raise SourceAssertionSourceMismatchError(
                    source_assertion.id, expected_source_id=source_assertion.source_id, supplied_source_id=source.id,
                )
            if latest is None or latest.status != _ADMITTED or supersedes_alias_id != latest.id:
                raise ConflictingAliasStatusRequiresSupersessionError(
                    airport_id, alias=alias,
                    latest_alias_id=latest.id if latest is not None else None,
                    latest_status=latest.status if latest is not None else None,
                    new_status=status,
                )

        row = AirportAlias(
            airport_id=airport_id,
            alias=alias,
            language=language,
            script=script,
            source_id=source_id,
            source_assertion_id=source_assertion_id,
            evidence_excerpt=evidence_excerpt,
            analyst=analyst,
            evidence_class=evidence_class if status == _ADMITTED else (latest.evidence_class if latest else evidence_class),
            status=status,
            supersedes_alias_id=supersedes_alias_id,
        )
        session.add(row)
        session.flush()

        return AirportAliasAdmissionResult(
            alias_id=row.id, airport_id=airport_id, alias=alias, status=status,
            evidence_class=row.evidence_class, source_id=source_id, source_assertion_id=source_assertion_id,
            superseded_alias_id=supersedes_alias_id,
        )
