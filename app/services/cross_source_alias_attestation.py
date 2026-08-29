"""Governed persistence, impact-preview, and EB5-support for cross-source
alias attestation (docs/architecture, "RWI - Cross-Source Governed Airport
Identity Binding - Architecture Recon" mission's own Option C, the locked
architecture this module implements; see
app.models.source_assertion_cross_source_alias_attestation for the
persisted row shape).

    already-canonical SourceAssertion S, raw identity_guard_decision ==
        ATTACH_PROVISIONAL exactly
        + existing, ADMITTED, currently-unambiguous AirportAlias A for
          S's own Airport
        + S's own preserved raw_relevant_text literally contains A's alias
          text
        + A's own admitting source_id differs from S's own source_id
        -> check_cross_source_alias_attestation_eligibility()
        -> record_cross_source_alias_attestation()
        -> one SourceAssertionCrossSourceAliasAttestation row, append-only
        -> STOP. Never mutates S.identity_guard_decision, never creates an
           EvidenceBag or IdentityGuardEvaluation, never touches AirportAlias,
           AirportIdentifier, Airport, or Signal.
        -> app.services.effective_identity_guard_decision (EB5), separately,
           narrowly extended to consult this table: a CURRENT (see
           is_cross_source_alias_attestation_current() below) attestation
           elevates S's own EFFECTIVE (never raw) identity decision to
           ATTACH_CONFIRMED, basis=CROSS_SOURCE_ALIAS_ATTESTATION.

WHAT THIS MEANS, EXACTLY (the mission's own "CORE SEMANTIC" - reproduced
here verbatim because every precondition below exists to make this claim,
and only this claim, mechanically true): "A human reviewer has accepted
that this already-canonical SourceAssertion refers to Airport X because its
literal source evidence uses an exact, currently-ADMITTED, independently-
governed AirportAlias for Airport X, and that alias is unambiguous across
the current canonical Airport population." It does NOT mean the alias is
globally unique in the real world (RWI's own database can only ever attest
to DB-local uniqueness - see _current_admitted_alias_owners() below), does
NOT mean the alias is IATA/ICAO-equivalent (see
docs/architecture/rwi-cross-source-governed-airport-identity-binding-
architecture-recon.md's own Option B rejection: natural-language strings
are never drawn from a globally-unique-by-design namespace the way aviation
codes are, no matter how rigorously one specific admission is governed),
does NOT mean S's own raw IdentityGuard evaluation was wrong (it is never
mutated), and does NOT double-count NAME evidence (IdentityGuard,
CandidateAirport, and EvidenceBag are completely untouched by this module -
see EB5 integration note above; the attestation is a wholly separate fact
EB5 consumes, never a second NAME-category vote).

WHY RAW MUST BE EXACTLY ATTACH_PROVISIONAL (Phase 5's own explicit,
narrow-scope instruction): this mechanism exists to elevate a weak but
legitimate NAME-based attachment, never to rescue REJECT_CROSS_AIRPORT,
INSUFFICIENT_IDENTITY, or a NULL (never-evaluated) raw decision - see
RawDecisionNotEligibleError below. A row that already reached
ATTACH_CONFIRMED on its own needs no elevation; a row IdentityGuard
actively rejected or never evaluated is a different, out-of-scope problem.

WHY GLOBAL ALIAS-OWNER UNIQUENESS, NOT MERELY "AirportAlias.airport_id
MATCHES" (Phase 7/13's own explicit fail-closed collision requirement):
app.services.airport_alias.get_admitted_airport_aliases() is scoped to ONE
Airport by construction and cannot answer "does any OTHER Airport also
currently hold this exact alias" - that question requires a NEW, separate,
GLOBAL scan (_current_admitted_alias_owners() below), added here rather
than to app.services.airport_alias itself (this mission's own explicit
"do not modify AirportAlias semantics" boundary - that module's own
uniqueness posture, airport-scoped only, is completely unchanged).

ANTI-CIRCULARITY (mirrors app.services.airport_alias's own single most
important correctness property, applied to a structurally different
question): `matched_alias.source_id != source_assertion.source_id` is the
one mechanical independence check this module makes - it structurally
catches every named threat in the mission's own Phase 6/9 threat list at
once: the alias being admitted using the SAME source as the being-attested
assertion, the being-attested assertion being used as the alias's own
evidence, and a same-Source sibling assertion "corroborating" its own
family's alias. True editorial/document-lineage independence (syndication,
republication under a different nameplate) is NOT, and cannot be,
mechanically provable from source_id alone - this is why human approval
remains mandatory (see record_cross_source_alias_attestation()'s own
docstring); the CLI surfaces both source identities so a human can judge
this residual question directly, exactly as the architecture-recon mission
concluded is necessary.

WHY NO STATUS/SUPERSESSION VOCABULARY THIS SLICE (Phase 14's own explicit
"prefer safe incompleteness" instruction): record_cross_source_alias_attestation()
refuses outright if ANY attestation already exists for the target
source_assertion_id (DuplicateAttestationError) - a human who wants to
retract or replace one must currently do so by hand, outside this module (a
documented limitation, not an oversight). `supersedes_attestation_id` exists
in the schema for future-proofing only and is never accepted as an argument
here.

STALENESS / CURRENCY (mirrors
app.services.source_assertion_legacy_identity_attestation's own
is_attestation_current() philosophy - "recompute fresh, compare against the
CURRENT real state" - applied here to the underlying AirportAlias fact
rather than a snapshot hash, since that fact is itself already a governed,
append-only table with its own natural "current" derivation):
is_cross_source_alias_attestation_current() is the SINGLE function both
this module (for a future correction workflow) and EB5 call, so the two can
never independently define "stale" differently. An attestation stops being
current the moment its own matched AirportAlias is RETIRED/REJECTED, OR a
second Airport is later independently granted the identical normalized
alias (Phase 19's own "duplicate-alias-across-airports" and "retired alias"
threats, both re-checked at EVERY read, never trusted from write time
alone).

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only, so any constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.models.airport_alias import AirportAlias
from app.models.source_assertion_cross_source_alias_attestation import (
    SourceAssertionCrossSourceAliasAttestation,
)
from app.services.manual_identity_evidence import excerpt_contains_value, normalize_for_containment_check
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "AliasNotFoundError",
    "EmptyAnalystError",
    "EmptyReasonError",
    "NotCanonicallyAttachedError",
    "SignalAlreadyLinkedError",
    "AliasNotAdmittedError",
    "AliasAirportMismatchError",
    "RawDecisionNotEligibleError",
    "AliasNotInAssertionEvidenceError",
    "AmbiguousAliasAcrossAirportsError",
    "NotIndependentSourceError",
    "DuplicateAttestationError",
    "CrossSourceAliasAttestationResult",
    "CrossSourceAliasAttestationPreview",
    "get_latest_cross_source_alias_attestation",
    "is_cross_source_alias_attestation_current",
    "preview_cross_source_alias_attestation",
    "check_cross_source_alias_attestation_eligibility",
    "record_cross_source_alias_attestation",
]

# The one raw decision this mechanism may ever elevate - see module
# docstring "WHY RAW MUST BE EXACTLY ATTACH_PROVISIONAL".
_ELIGIBLE_RAW_DECISION = "ATTACH_PROVISIONAL"


class AliasNotFoundError(ValueError):
    def __init__(self, alias_id: int) -> None:
        self.alias_id = alias_id
        super().__init__(f"matched_alias_id={alias_id!r} does not reference an existing AirportAlias")


class EmptyAnalystError(ValueError):
    def __init__(self) -> None:
        super().__init__("analyst is required and cannot be empty")


class EmptyReasonError(ValueError):
    def __init__(self) -> None:
        super().__init__("reason is required and cannot be empty")


class NotCanonicallyAttachedError(ValueError):
    """Raised when `source_assertion.airport_id` is NULL - per
    SourceAssertion's own DB-level mutual-exclusivity CheckConstraint, this
    means the row is still candidate-linked or fully unresolved (Phase 4's
    own "no unresolved candidate/UAC state" precondition); this mechanism
    only ever reviews an already-canonically-attached row."""

    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no canonical airport_id - it is still candidate-"
            "linked or unresolved; this mechanism only reviews already-attached assertions."
        )


class SignalAlreadyLinkedError(ValueError):
    """Mirrors SourceAssertionLegacyIdentityAttestation's own identical
    precedent: a row that already produced a Signal despite this specific
    identity question never being formally elevated is a data-integrity
    question outside this mechanism's scope, never silently accepted."""

    def __init__(self, source_assertion_id: int, *, signal_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.signal_id = signal_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has signal_id={signal_id!r} set - refusing "
            "to attest identity for evidence that has already produced a Signal."
        )


class AliasNotAdmittedError(ValueError):
    def __init__(self, alias_id: int, *, status: str) -> None:
        self.alias_id = alias_id
        self.status = status
        super().__init__(f"AirportAlias {alias_id} has status={status!r}, not ADMITTED - refusing to rely on it.")


class AliasAirportMismatchError(ValueError):
    def __init__(self, source_assertion_id: int, *, assertion_airport_id: int, alias_airport_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.assertion_airport_id = assertion_airport_id
        self.alias_airport_id = alias_airport_id
        super().__init__(
            f"AirportAlias belongs to Airport {alias_airport_id!r}, which does not equal SourceAssertion "
            f"{source_assertion_id}'s own airport_id={assertion_airport_id!r} - this mechanism never "
            "attaches to, or proposes, a different Airport."
        )


class RawDecisionNotEligibleError(ValueError):
    def __init__(self, source_assertion_id: int, *, raw_decision: "str | None") -> None:
        self.source_assertion_id = source_assertion_id
        self.raw_decision = raw_decision
        super().__init__(
            f"SourceAssertion {source_assertion_id}'s raw identity_guard_decision={raw_decision!r} is not "
            f"{_ELIGIBLE_RAW_DECISION!r} - this mechanism only elevates a genuine ATTACH_PROVISIONAL "
            "NAME-based attachment, never REJECT_CROSS_AIRPORT, INSUFFICIENT_IDENTITY, an already-"
            "CONFIRMED row, or one that never ran the identity guard at all."
        )


class AliasNotInAssertionEvidenceError(ValueError):
    def __init__(self, source_assertion_id: int, *, alias: str) -> None:
        self.source_assertion_id = source_assertion_id
        self.alias = alias
        super().__init__(
            f"alias={alias!r} does not occur literally within SourceAssertion {source_assertion_id}'s own "
            "preserved raw_relevant_text (or that field is empty) - no translation, transliteration, or "
            "fuzzy matching is ever accepted."
        )


class AmbiguousAliasAcrossAirportsError(ValueError):
    def __init__(self, *, alias: str, owner_airport_ids: "tuple[int, ...]") -> None:
        self.alias = alias
        self.owner_airport_ids = owner_airport_ids
        super().__init__(
            f"alias={alias!r} is currently ADMITTED for {len(owner_airport_ids)} different Airport(s) "
            f"{owner_airport_ids!r} - refusing to bind an ambiguous alias to any one of them. This is a "
            "DB-local uniqueness check only; it can never prove real-world global uniqueness."
        )


class NotIndependentSourceError(ValueError):
    def __init__(self, *, alias_source_id: int, assertion_source_id: int) -> None:
        self.alias_source_id = alias_source_id
        self.assertion_source_id = assertion_source_id
        super().__init__(
            f"the matched AirportAlias's own admitting source_id={alias_source_id!r} equals the being-"
            f"attested SourceAssertion's own source_id={assertion_source_id!r} - the alias's evidence "
            "source must be independent of the assertion it would confirm."
        )


class DuplicateAttestationError(ValueError):
    def __init__(self, source_assertion_id: int, *, existing_attestation_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.existing_attestation_id = existing_attestation_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has a cross-source alias attestation "
            f"(#{existing_attestation_id}) - this slice supports no correction/reversal mechanics; "
            "refusing to record a duplicate."
        )


@dataclass(frozen=True)
class CrossSourceAliasAttestationResult:
    """Deterministic, ORM-free summary of what
    record_cross_source_alias_attestation() did."""

    attestation_id: int
    source_assertion_id: int
    matched_airport_id: int
    matched_alias_id: int
    analyst: str
    reason: str


@dataclass(frozen=True)
class CrossSourceAliasAttestationPreview:
    """Deterministic, ORM-free, read-only report of every fact
    check_cross_source_alias_attestation_eligibility() itself checks, plus
    the predicted EB5 consequence - built from the SAME small pure helper
    functions the eligibility gate uses, so preview and write eligibility
    can never silently disagree (mission's own explicit Phase 10
    instruction)."""

    source_assertion_id: int
    matched_alias_id: int
    matched_alias_text: "str | None"
    matched_alias_source_id: "int | None"
    matched_alias_status: "str | None"
    target_airport_id: "int | None"
    being_attested_source_id: "int | None"
    raw_decision: "str | None"
    current_effective_decision: "str | None"
    current_effective_basis: "str | None"
    literal_match: bool
    alias_currently_unique: bool
    current_alias_owner_airport_ids: "tuple[int, ...]"
    source_independent: "bool | None"
    already_has_attestation: bool
    eligible: bool
    refusal_reason: "str | None"
    predicted_effective_decision: str
    predicted_effective_basis: str
    would_change: bool


def get_latest_cross_source_alias_attestation(
    session: Session, source_assertion_id: int,
) -> "SourceAssertionCrossSourceAliasAttestation | None":
    """Read-only. Recency alone determines "current" - never derived by
    walking supersedes_attestation_id, matching every other append-only
    table in this pipeline."""
    return session.scalars(
        select(SourceAssertionCrossSourceAliasAttestation)
        .where(SourceAssertionCrossSourceAliasAttestation.source_assertion_id == source_assertion_id)
        .order_by(
            SourceAssertionCrossSourceAliasAttestation.created_at.desc(),
            SourceAssertionCrossSourceAliasAttestation.id.desc(),
        )
        .limit(1)
    ).first()


def _current_admitted_alias_owners(session: Session, normalized_alias_key: str) -> "frozenset[int]":
    """Read-only. Returns every Airport currently (by recency, per
    (airport_id, normalized-alias) group - the identical derivation
    app.services.airport_alias.get_admitted_airport_aliases() uses, just
    computed GLOBALLY across every Airport rather than scoped to one)
    holding an ADMITTED alias whose normalized form equals
    normalized_alias_key. See module docstring "WHY GLOBAL ALIAS-OWNER
    UNIQUENESS" for why this cannot be answered by that existing, airport-
    scoped function alone."""
    rows = session.scalars(
        select(AirportAlias).order_by(AirportAlias.created_at.asc(), AirportAlias.id.asc())
    ).all()
    latest_by_key: "dict[tuple[int, str], AirportAlias]" = {}
    for row in rows:
        latest_by_key[(row.airport_id, normalize_for_containment_check(row.alias))] = row
    return frozenset(
        airport_id
        for (airport_id, key), row in latest_by_key.items()
        if key == normalized_alias_key and row.status == "ADMITTED"
    )


def is_cross_source_alias_attestation_current(
    session: Session, attestation: SourceAssertionCrossSourceAliasAttestation,
) -> bool:
    """Read-only. The SINGLE function both this module and EB5 call - see
    module docstring "STALENESS / CURRENCY". True only if: the matched
    AirportAlias row still exists and is still ADMITTED; it is still the
    UNIQUE current ADMITTED owner of its own normalized alias text across
    ALL Airports (a later independent admission for a DIFFERENT Airport
    invalidates a previously-safe attestation); and the being-attested
    SourceAssertion's own airport_id still equals the attestation's own
    matched_airport_id."""
    alias = session.get(AirportAlias, attestation.matched_alias_id)
    if alias is None or _current_alias_status(session, alias) != "ADMITTED":
        return False
    owners = _current_admitted_alias_owners(session, normalize_for_containment_check(alias.alias))
    if owners != frozenset({attestation.matched_airport_id}):
        return False
    assertion = session.get(SourceAssertion, attestation.source_assertion_id)
    if assertion is None or assertion.airport_id != attestation.matched_airport_id:
        return False
    return True


def _literal_match(source_assertion: SourceAssertion, alias_text: str) -> bool:
    return bool(source_assertion.raw_relevant_text) and excerpt_contains_value(
        source_assertion.raw_relevant_text, alias_text
    )


def _current_alias_status(session: Session, alias: AirportAlias) -> str:
    """The CURRENT effective status of one AirportAlias row - "current" is
    always the LATEST row for the same (airport_id, normalized alias) key,
    matching app.services.airport_alias.get_admitted_airport_aliases()'s
    own derivation exactly. Deliberately NEVER the individual row's own
    `.status` column read in isolation: AirportAlias rows are immutable and
    append-only, so an original ADMITTED row's own `.status` column reads
    "ADMITTED" forever, even after a LATER row supersedes it with RETIRED/
    REJECTED - a caller citing that original row's id must still be
    refused, which requires walking the full history for its key, exactly
    as get_admitted_airport_aliases() already does."""
    rows = session.scalars(
        select(AirportAlias)
        .where(AirportAlias.airport_id == alias.airport_id)
        .order_by(AirportAlias.created_at.asc(), AirportAlias.id.asc())
    ).all()
    key = normalize_for_containment_check(alias.alias)
    matching = [row for row in rows if normalize_for_containment_check(row.alias) == key]
    latest = matching[-1] if matching else alias
    return latest.status


def check_cross_source_alias_attestation_eligibility(
    session: Session, *, source_assertion: SourceAssertion, matched_alias: AirportAlias,
) -> None:
    """The single source of truth for "is this (SourceAssertion, AirportAlias)
    pair even eligible for this mechanism at all" - both
    record_cross_source_alias_attestation() and
    preview_cross_source_alias_attestation() call this exact function (the
    preview via a try/except, never a re-derived copy), so they can never
    disagree about eligibility. Raises the first violated precondition;
    never partially reports.

    Preconditions, checked in this exact order:

    1. source_assertion.airport_id is not None -> NotCanonicallyAttachedError
    2. source_assertion.signal_id is None -> SignalAlreadyLinkedError
    3. matched_alias.status == "ADMITTED" -> AliasNotAdmittedError
    4. matched_alias.airport_id == source_assertion.airport_id -> AliasAirportMismatchError
    5. source_assertion.identity_guard_decision == "ATTACH_PROVISIONAL" -> RawDecisionNotEligibleError
    6. matched_alias.alias occurs literally in source_assertion.raw_relevant_text -> AliasNotInAssertionEvidenceError
    7. matched_alias's own normalized alias text currently has exactly ONE ADMITTED owner Airport,
       and it is source_assertion.airport_id -> AmbiguousAliasAcrossAirportsError
    8. matched_alias.source_id != source_assertion.source_id -> NotIndependentSourceError
    9. no existing attestation already governs source_assertion -> DuplicateAttestationError
    """
    if source_assertion.airport_id is None:
        raise NotCanonicallyAttachedError(source_assertion.id)
    if source_assertion.signal_id is not None:
        raise SignalAlreadyLinkedError(source_assertion.id, signal_id=source_assertion.signal_id)
    current_alias_status = _current_alias_status(session, matched_alias)
    if current_alias_status != "ADMITTED":
        raise AliasNotAdmittedError(matched_alias.id, status=current_alias_status)
    if matched_alias.airport_id != source_assertion.airport_id:
        raise AliasAirportMismatchError(
            source_assertion.id,
            assertion_airport_id=source_assertion.airport_id,
            alias_airport_id=matched_alias.airport_id,
        )
    if source_assertion.identity_guard_decision != _ELIGIBLE_RAW_DECISION:
        raise RawDecisionNotEligibleError(source_assertion.id, raw_decision=source_assertion.identity_guard_decision)
    if not _literal_match(source_assertion, matched_alias.alias):
        raise AliasNotInAssertionEvidenceError(source_assertion.id, alias=matched_alias.alias)

    with session.no_autoflush:
        owners = _current_admitted_alias_owners(session, normalize_for_containment_check(matched_alias.alias))
    if owners != frozenset({source_assertion.airport_id}):
        raise AmbiguousAliasAcrossAirportsError(alias=matched_alias.alias, owner_airport_ids=tuple(sorted(owners)))

    if matched_alias.source_id == source_assertion.source_id:
        raise NotIndependentSourceError(
            alias_source_id=matched_alias.source_id, assertion_source_id=source_assertion.source_id,
        )

    existing = get_latest_cross_source_alias_attestation(session, source_assertion.id)
    if existing is not None:
        raise DuplicateAttestationError(source_assertion.id, existing_attestation_id=existing.id)


def preview_cross_source_alias_attestation(
    session: Session, *, source_assertion_id: int, matched_alias_id: int,
) -> CrossSourceAliasAttestationPreview:
    """Read-only. Never persists, flushes, or commits anything. Reuses the
    real eligibility gate (via try/except) for the authoritative
    eligible/refusal_reason fields, and the SAME small pure helpers it uses
    internally for every individual fact reported - see class docstring."""
    # Deferred import - avoids a real circular import: EB5
    # (app.services.effective_identity_guard_decision) needs to consult
    # THIS module to check attestations, so this module must never import
    # EB5 at module load time.
    from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision

    with session.no_autoflush:
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)
        matched_alias = session.get(AirportAlias, matched_alias_id)
        if matched_alias is None:
            raise AliasNotFoundError(matched_alias_id)

        literal_match = _literal_match(source_assertion, matched_alias.alias)
        owners = _current_admitted_alias_owners(session, normalize_for_containment_check(matched_alias.alias))
        alias_currently_unique = owners == frozenset({source_assertion.airport_id})
        source_independent = (
            matched_alias.source_id != source_assertion.source_id
            if source_assertion.airport_id is not None
            else None
        )
        already_has_attestation = get_latest_cross_source_alias_attestation(session, source_assertion_id) is not None

        current = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion_id)

        eligible = True
        refusal_reason: "str | None" = None
        try:
            check_cross_source_alias_attestation_eligibility(
                session, source_assertion=source_assertion, matched_alias=matched_alias,
            )
        except (ValueError,) as exc:
            eligible = False
            refusal_reason = str(exc)

        predicted_decision = "ATTACH_CONFIRMED" if eligible else current.effective_decision.value
        predicted_basis = "CROSS_SOURCE_ALIAS_ATTESTATION" if eligible else current.basis.value

    return CrossSourceAliasAttestationPreview(
        source_assertion_id=source_assertion_id,
        matched_alias_id=matched_alias_id,
        matched_alias_text=matched_alias.alias,
        matched_alias_source_id=matched_alias.source_id,
        matched_alias_status=matched_alias.status,
        target_airport_id=source_assertion.airport_id,
        being_attested_source_id=source_assertion.source_id,
        raw_decision=source_assertion.identity_guard_decision,
        current_effective_decision=current.effective_decision.value,
        current_effective_basis=current.basis.value,
        literal_match=literal_match,
        alias_currently_unique=alias_currently_unique,
        current_alias_owner_airport_ids=tuple(sorted(owners)),
        source_independent=source_independent,
        already_has_attestation=already_has_attestation,
        eligible=eligible,
        refusal_reason=refusal_reason,
        predicted_effective_decision=predicted_decision,
        predicted_effective_basis=predicted_basis,
        would_change=eligible and current.effective_decision.value != "ATTACH_CONFIRMED",
    )


def record_cross_source_alias_attestation(
    session: Session, *, source_assertion_id: int, matched_alias_id: int, analyst: str, reason: str,
) -> CrossSourceAliasAttestationResult:
    """Validates every precondition (§ check_cross_source_alias_attestation_eligibility
    plus analyst/reason non-empty), then appends exactly one new
    SourceAssertionCrossSourceAliasAttestation row. Never commits; calls
    session.flush() so any constraint violation surfaces immediately. Never
    mutates SourceAssertion, AirportAlias, AirportIdentifier, Airport,
    EvidenceBag, or IdentityGuardEvaluation - see module docstring.

    HUMAN APPROVAL IS THE POINT, NOT A FORMALITY (mission's own explicit
    Phase 17 instruction): this function never runs automatically merely
    because a preview reports eligible=True. A caller (the CLI, this
    module's only intended entry point for real use) must supply an
    explicit analyst and reason every time - the human is the one accepting
    the one residual risk no mechanical check here can close: that RWI's
    own DB-local alias uniqueness does not, and cannot, prove real-world
    global uniqueness, and that true source independence (vs. undetectable
    syndication/republication) is a judgment call, not a provable fact.
    """
    with session.no_autoflush:
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)
        matched_alias = session.get(AirportAlias, matched_alias_id)
        if matched_alias is None:
            raise AliasNotFoundError(matched_alias_id)
        if not analyst or not analyst.strip():
            raise EmptyAnalystError()
        if not reason or not reason.strip():
            raise EmptyReasonError()

        check_cross_source_alias_attestation_eligibility(
            session, source_assertion=source_assertion, matched_alias=matched_alias,
        )
        matched_airport_id = source_assertion.airport_id

    row = SourceAssertionCrossSourceAliasAttestation(
        source_assertion_id=source_assertion_id,
        matched_airport_id=matched_airport_id,
        matched_alias_id=matched_alias_id,
        analyst=analyst,
        reason=reason,
    )
    session.add(row)
    session.flush()

    return CrossSourceAliasAttestationResult(
        attestation_id=row.id,
        source_assertion_id=source_assertion_id,
        matched_airport_id=matched_airport_id,
        matched_alias_id=matched_alias_id,
        analyst=analyst,
        reason=reason,
    )
