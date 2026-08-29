"""Governed persistence, preview, and deterministic adaptation for
first-class manual claim evidence ("RWI - First-Class Manual Claim
Evidence - Implementation" mission; see app.models.manual_claim_evidence
for the persisted row shape).

    already-canonical, effectively-ATTACH_CONFIRMED SourceAssertion, whose
        source family has no automatic claims adapter
        (app.services.human_review_claim_enrichment.enrich_claims()
        returns None for its parser_identifier)
        -> check_manual_claim_evidence_eligibility() (canonical attachment,
           EFFECTIVE identity confirmed, literal containment, identity
           anchor, group-completeness, mechanical token verification,
           duplicate check)
        -> record_manual_claim_evidence()
        -> one ManualClaimEvidence row, append-only
        -> manual_claim_evidence_to_claim() (pure, deterministic)
        -> get_manual_claims_for_source_assertion() (re-checks EFFECTIVE
           identity at CONSUMPTION time too - see "EFFECTIVE IDENTITY GATE"
           below)
        -> tuple[Claim, ...] - the EXISTING, UNMODIFIED
           app.services.evidence_claim_semantics.Claim type, consumed
           identically to an automatically-extracted claim by
           app.services.intelligence_review_persistence.persist_intelligence_review()
           and everything downstream of it. NEVER a parallel claims
           universe.

CORE PRINCIPLE (mission's own explicit framing, reproduced here because
every check below exists only to make it mechanically true): this module
is AUDITED MANUAL EXTRACTION, never manual intelligence judgment, never
manual Signal creation, never free-form interpretation, never translation-
as-evidence, and never a way to bypass identity governance. Every
structured value persisted here must be supported by literal preserved
source evidence.

EFFECTIVE IDENTITY GATE (mission's own explicit Phase 7 "preferred safety
posture: both"): BOTH `record_manual_claim_evidence()` (write eligibility)
AND `get_manual_claims_for_source_assertion()` (consumption) independently
call `app.services.effective_identity_guard_decision.
resolve_effective_identity_guard_decision()` and require
`effective_decision == ATTACH_CONFIRMED` - never the raw
`SourceAssertion.identity_guard_decision` column alone (SA235's own real,
concrete, motivating case: raw=ATTACH_PROVISIONAL,
effective=ATTACH_CONFIRMED via CROSS_SOURCE_ALIAS_ATTESTATION - a row this
gate must accept). Re-checking at CONSUMPTION time (not merely trusting a
write-time snapshot) matters because effective identity can legitimately
change AFTER a claim was transcribed (e.g. a governed alias later retired -
see app.services.cross_source_alias_attestation's own identical staleness
philosophy) - a claim must never be usable downstream once its own
identity backing has become stale.

IDENTITY ANCHOR (mirrors app.services.airport_alias's own
NoIdentityAnchorError precondition exactly, applied to claims instead of
aliases): `evidence_excerpt` must independently, literally co-present an
already-established identity fact of the assertion's OWN canonical Airport
(its current name, an existing iata_code/icao_code/faa_code, or a
currently-ADMITTED AirportAlias) - without this anchor, nothing
distinguishes "this excerpt is genuinely about the claimed Airport/project"
from "this excerpt was transcribed from unrelated surrounding text."

FACT VS INFERENCE (mission's own explicit Phase 6 boundary, enforced
structurally, not merely by instruction): `ProvenanceKind` is NEVER a
caller-supplied field anywhere in this module - every row this module ever
produces a Claim from is hardcoded `ProvenanceKind.EXPLICIT` (see
manual_claim_evidence_to_claim()). A DERIVED claim (assembled by combining
multiple explicit facts, or containing any analyst inference/estimate/
prediction) has no representation in this table at all; an analyst wanting
to record such a thing has no field to put it in.

MECHANICAL LITERAL-TOKEN VERIFICATION, NEVER NLP/FUZZY/TRANSLATION (mission
Phase 5, reusing app.services.manual_identity_evidence's own
normalize_for_containment_check()/excerpt_contains_value() verbatim - no
second normalization implementation introduced, matching every other
governed-evidence module in this pipeline): `evidence_excerpt` must be a
literal substring of `source_assertion.raw_relevant_text`;
`financial_amount_evidence_token` must be a literal substring of
`evidence_excerpt` AND must parse (after stripping thousands separators/
whitespace) to EXACTLY `financial_amount`; `financial_currency` and
`relationship_party` must each be literal substrings of `evidence_excerpt`;
every `temporal_year_tokens` entry must be a literal substring of
`evidence_excerpt`. `subject`/`statement`/`financial_semantic_role`/
`relationship_role`/`relationship_scope` are all free-text classification/
restatement fields, exactly as free-text and non-literal-checked as
app.services.evidence_claim_semantics.FinancialFact.semantic_role and
Claim.statement already are by that module's own design (see that
module's own docstring: "statement... NORMALIZED restatement... never a
substitute for provenance.raw_text_excerpt").

TEMPORAL SEMANTICS - A DISCLOSED, GENUINE LIMITATION (mission Phase 11):
`TemporalContext.as_of_date` is ALWAYS derived from the being-transcribed
SourceAssertion's own Source.published_date (the cited document's own
date) - never caller-supplied, never `date.today()`. `temporal_year_tokens`
(mechanically-verified literal year strings) are the ONLY mechanism for
preserving a source's own multi-year granularity (e.g. "budget secured
across FY2025-2026") - folded verbatim, comma-joined, into
`TemporalContext.detail`. The EXISTING `TemporalQualifier` vocabulary has
NO member that can honestly represent "spans exactly these N specific
years" as a structured fact - this is a genuine, disclosed gap in the
existing claim taxonomy, not something this module works around by
inventing a synthetic date range (which the source excerpt does not
literally support with day/month precision in the general case).

GROUP-COMPLETENESS, NO OPAQUE PARTIAL PAYLOADS (mission Phase 9): each of
the three optional attachment groups (financial, temporal, relationship)
is validated as an atomic ALL-OR-NOTHING set of its own REQUIRED member
columns - see _validate_financial_group()/_validate_temporal_group()/
_validate_relationship_group() below. A row may combine zero, one, two, or
all three groups (mirrors Claim's own "all three attachments optional and
independently combinable" shape exactly) - but never a half-populated
group silently passing.

DUPLICATE DETECTION (mission Phase 12): refuses an EXACT duplicate governed
fact for the same source_assertion_id - the normalized key is
(claim_category, normalized evidence_excerpt, and every populated
structured field, normalized) - see _duplicate_key(). A different literal
claim (even about the same general topic) is never blocked merely because
it looks similar.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only, so any constraint violation
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.manual_claim_evidence import (
    MANUAL_CLAIM_EVIDENCE_CATEGORIES,
    MANUAL_CLAIM_EVIDENCE_EXTRACTION_MODES,
    MANUAL_CLAIM_EVIDENCE_NORMALIZATION_VERSION,
    MANUAL_CLAIM_EVIDENCE_TEMPORAL_QUALIFIERS,
    ManualClaimEvidence,
)
from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    FinancialFact,
    ProvenanceKind,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)
from app.services.manual_identity_evidence import excerpt_contains_value, normalize_for_containment_check
from app.services.resolved_candidate_evidence_reevaluation import SourceAssertionNotFoundError

__all__ = [
    "SourceAssertionNotFoundError",
    "EmptySubjectError",
    "EmptyStatementError",
    "EmptyExcerptError",
    "EmptyAnalystError",
    "InvalidClaimCategoryError",
    "NotCanonicallyAttachedError",
    "IdentityNotEffectivelyConfirmedError",
    "ExcerptNotInPreservedEvidenceError",
    "NoIdentityAnchorError",
    "IncompleteFinancialGroupError",
    "AmountEvidenceTokenNotInExcerptError",
    "AmountEvidenceTokenMismatchError",
    "InvalidTemporalQualifierError",
    "TemporalYearTokenNotInExcerptError",
    "IncompleteRelationshipGroupError",
    "RelationshipPartyNotInExcerptError",
    "DuplicateManualClaimEvidenceError",
    "ManualClaimEvidenceResult",
    "ManualClaimEvidencePreview",
    "get_admitted_aliases_or_empty",
    "manual_claim_evidence_to_claim",
    "get_manual_claims_for_source_assertion",
    "preview_manual_claim_evidence",
    "check_manual_claim_evidence_eligibility",
    "record_manual_claim_evidence",
]


class EmptySubjectError(ValueError):
    def __init__(self) -> None:
        super().__init__("subject is required and cannot be empty")


class EmptyStatementError(ValueError):
    def __init__(self) -> None:
        super().__init__("statement is required and cannot be empty")


class EmptyExcerptError(ValueError):
    def __init__(self) -> None:
        super().__init__("evidence_excerpt is required and cannot be empty")


class EmptyAnalystError(ValueError):
    def __init__(self) -> None:
        super().__init__("analyst is required and cannot be empty")


class InvalidClaimCategoryError(ValueError):
    def __init__(self, claim_category: str) -> None:
        self.claim_category = claim_category
        super().__init__(f"claim_category must be one of {MANUAL_CLAIM_EVIDENCE_CATEGORIES!r}, got {claim_category!r}")


class NotCanonicallyAttachedError(ValueError):
    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} has no canonical airport_id - manual claim evidence "
            "only applies to an already-attached assertion."
        )


class IdentityNotEffectivelyConfirmedError(ValueError):
    def __init__(self, source_assertion_id: int, *, effective_decision: str, basis: str) -> None:
        self.source_assertion_id = source_assertion_id
        self.effective_decision = effective_decision
        self.basis = basis
        super().__init__(
            f"SourceAssertion {source_assertion_id}'s EFFECTIVE identity decision is "
            f"{effective_decision!r} (basis={basis!r}), not ATTACH_CONFIRMED - manual claim evidence "
            "requires effective identity confirmation (resolve_effective_identity_guard_decision()), "
            "never merely the raw identity_guard_decision column."
        )


class ExcerptNotInPreservedEvidenceError(ValueError):
    def __init__(self, source_assertion_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        super().__init__(
            f"evidence_excerpt does not occur literally within SourceAssertion {source_assertion_id}'s "
            "own preserved raw_relevant_text (or that field is empty)."
        )


class NoIdentityAnchorError(ValueError):
    def __init__(self, airport_id: int) -> None:
        self.airport_id = airport_id
        super().__init__(
            f"evidence_excerpt does not literally co-present an independent identity anchor for Airport "
            f"{airport_id} (its current name, an existing iata_code/icao_code/faa_code, or a currently-"
            "ADMITTED AirportAlias) - refusing to bind a claim without proof the excerpt genuinely "
            "concerns this Airport."
        )


class IncompleteFinancialGroupError(ValueError):
    def __init__(self) -> None:
        super().__init__(
            "financial_amount/financial_amount_evidence_token/financial_currency/financial_semantic_role "
            "must all be supplied together, or none at all."
        )


class AmountEvidenceTokenNotInExcerptError(ValueError):
    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(f"financial_amount_evidence_token={token!r} does not occur literally in evidence_excerpt")


class AmountEvidenceTokenMismatchError(ValueError):
    def __init__(self, token: str, *, amount: str) -> None:
        self.token = token
        self.amount = amount
        super().__init__(
            f"financial_amount_evidence_token={token!r} does not numerically equal financial_amount={amount!r} "
            "after stripping thousands separators/whitespace."
        )


class InvalidTemporalQualifierError(ValueError):
    def __init__(self, temporal_qualifier: str) -> None:
        self.temporal_qualifier = temporal_qualifier
        super().__init__(
            f"temporal_qualifier must be one of {MANUAL_CLAIM_EVIDENCE_TEMPORAL_QUALIFIERS!r}, "
            f"got {temporal_qualifier!r}"
        )


class TemporalYearTokenNotInExcerptError(ValueError):
    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(f"temporal year token {token!r} does not occur literally in evidence_excerpt")


class IncompleteRelationshipGroupError(ValueError):
    def __init__(self) -> None:
        super().__init__("relationship_party and relationship_role must both be supplied together, or neither.")


class RelationshipPartyNotInExcerptError(ValueError):
    def __init__(self, party: str) -> None:
        self.party = party
        super().__init__(f"relationship_party={party!r} does not occur literally in evidence_excerpt")


class DuplicateManualClaimEvidenceError(ValueError):
    def __init__(self, source_assertion_id: int, *, existing_id: int) -> None:
        self.source_assertion_id = source_assertion_id
        self.existing_id = existing_id
        super().__init__(
            f"SourceAssertion {source_assertion_id} already has an identical ManualClaimEvidence "
            f"(#{existing_id}) - refusing to record an exact duplicate governed fact."
        )


@dataclass(frozen=True)
class ManualClaimEvidenceResult:
    manual_claim_evidence_id: int
    source_assertion_id: int
    claim_category: str


@dataclass(frozen=True)
class ManualClaimEvidencePreview:
    source_assertion_id: int
    airport_id: "int | None"
    source_id: "int | None"
    raw_identity_decision: "str | None"
    effective_identity_decision: "str | None"
    effective_identity_basis: "str | None"
    claim_category: str
    evidence_excerpt: str
    literal_excerpt_match: bool
    identity_anchor_present: bool
    duplicate_of_id: "int | None"
    eligible: bool
    refusal_reason: "str | None"
    predicted_claim: "Claim | None"


def _normalize_amount_token(token: str) -> str:
    return token.replace(",", "").replace(" ", "").strip()


def get_admitted_aliases_or_empty(session: Session, airport_id: int) -> "frozenset[str]":
    """Deferred import to avoid a top-level circular dependency (this
    module is imported by app.services.effective_identity_guard_decision
    indirectly via app.services.cross_source_alias_attestation in some
    call chains); mirrors that module's own deferred-import discipline."""
    from app.services.airport_alias import get_admitted_airport_aliases

    return get_admitted_airport_aliases(session, airport_id)


def _identity_anchor_present(session: Session, evidence_excerpt: str, airport: Airport) -> bool:
    anchors = [value for value in (airport.name, airport.iata_code, airport.icao_code, airport.faa_code) if value]
    anchors.extend(get_admitted_aliases_or_empty(session, airport.id))
    return any(excerpt_contains_value(evidence_excerpt, anchor) for anchor in anchors)


def _validate_financial_group(
    *, financial_amount, financial_amount_evidence_token, financial_currency, financial_semantic_role,
    evidence_excerpt: str,
) -> "Decimal | None":
    supplied = [
        financial_amount is not None, financial_amount_evidence_token is not None,
        financial_currency is not None, financial_semantic_role is not None,
    ]
    if not any(supplied):
        return None
    if not all(supplied):
        raise IncompleteFinancialGroupError()
    try:
        amount = Decimal(str(financial_amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"financial_amount={financial_amount!r} is not a valid decimal") from exc
    if not financial_amount_evidence_token.strip():
        raise ValueError("financial_amount_evidence_token cannot be empty")
    if not financial_currency.strip():
        raise ValueError("financial_currency cannot be empty")
    if not financial_semantic_role.strip():
        raise ValueError("financial_semantic_role cannot be empty")
    if not excerpt_contains_value(evidence_excerpt, financial_amount_evidence_token):
        raise AmountEvidenceTokenNotInExcerptError(financial_amount_evidence_token)
    normalized_token = _normalize_amount_token(financial_amount_evidence_token)
    try:
        token_value = Decimal(normalized_token)
    except (InvalidOperation, ValueError) as exc:
        raise AmountEvidenceTokenMismatchError(financial_amount_evidence_token, amount=str(amount)) from exc
    if token_value != amount:
        raise AmountEvidenceTokenMismatchError(financial_amount_evidence_token, amount=str(amount))
    if not excerpt_contains_value(evidence_excerpt, financial_currency):
        raise ValueError(f"financial_currency={financial_currency!r} does not occur literally in evidence_excerpt")
    return amount


def _validate_temporal_group(
    *, temporal_qualifier, temporal_year_tokens: "tuple[str, ...]", evidence_excerpt: str,
) -> None:
    if temporal_qualifier is None:
        if temporal_year_tokens:
            raise ValueError("temporal_year_tokens requires temporal_qualifier to also be supplied")
        return
    if temporal_qualifier not in MANUAL_CLAIM_EVIDENCE_TEMPORAL_QUALIFIERS:
        raise InvalidTemporalQualifierError(temporal_qualifier)
    for token in temporal_year_tokens:
        if not token or not token.strip():
            raise ValueError("temporal_year_tokens entries cannot be empty")
        if not excerpt_contains_value(evidence_excerpt, token):
            raise TemporalYearTokenNotInExcerptError(token)


def _validate_relationship_group(
    *, relationship_party, relationship_role, relationship_scope, evidence_excerpt: str,
) -> None:
    supplied = [relationship_party is not None, relationship_role is not None]
    if not any(supplied) and relationship_scope is None:
        return
    if relationship_scope is not None and not any(supplied):
        raise IncompleteRelationshipGroupError()
    if not all(supplied):
        raise IncompleteRelationshipGroupError()
    if not relationship_party.strip():
        raise ValueError("relationship_party cannot be empty")
    if not relationship_role.strip():
        raise ValueError("relationship_role cannot be empty")
    if not excerpt_contains_value(evidence_excerpt, relationship_party):
        raise RelationshipPartyNotInExcerptError(relationship_party)


def _duplicate_key(
    *, claim_category: str, evidence_excerpt: str, financial_amount, financial_currency, financial_semantic_role,
    temporal_qualifier, temporal_year_tokens: "tuple[str, ...]", relationship_party, relationship_role,
) -> tuple:
    return (
        claim_category,
        normalize_for_containment_check(evidence_excerpt),
        str(financial_amount) if financial_amount is not None else None,
        (financial_currency or "").strip().upper() or None,
        (financial_semantic_role or "").strip().casefold() or None,
        temporal_qualifier,
        tuple(sorted(t.strip() for t in temporal_year_tokens)),
        (relationship_party or "").strip().casefold() or None,
        (relationship_role or "").strip().casefold() or None,
    )


def _existing_duplicate(
    session: Session, source_assertion_id: int, key: tuple,
) -> "ManualClaimEvidence | None":
    rows = session.scalars(
        select(ManualClaimEvidence).where(ManualClaimEvidence.source_assertion_id == source_assertion_id)
    ).all()
    for row in rows:
        row_key = _duplicate_key(
            claim_category=row.claim_category, evidence_excerpt=row.evidence_excerpt,
            financial_amount=row.financial_amount, financial_currency=row.financial_currency,
            financial_semantic_role=row.financial_semantic_role, temporal_qualifier=row.temporal_qualifier,
            temporal_year_tokens=tuple(row.temporal_year_tokens.split(",")) if row.temporal_year_tokens else (),
            relationship_party=row.relationship_party, relationship_role=row.relationship_role,
        )
        if row_key == key:
            return row
    return None


def check_manual_claim_evidence_eligibility(
    session: Session,
    *,
    source_assertion: SourceAssertion,
    claim_category: str,
    subject: str,
    statement: str,
    evidence_excerpt: str,
    financial_amount=None,
    financial_amount_evidence_token: "str | None" = None,
    financial_currency: "str | None" = None,
    financial_semantic_role: "str | None" = None,
    temporal_qualifier: "str | None" = None,
    temporal_year_tokens: "tuple[str, ...]" = (),
    relationship_party: "str | None" = None,
    relationship_role: "str | None" = None,
    relationship_scope: "str | None" = None,
) -> "Decimal | None":
    """The single source of truth for eligibility - both
    record_manual_claim_evidence() and preview_manual_claim_evidence() call
    this exact function, so they can never disagree. Returns the parsed
    Decimal amount (or None) so the write path never re-parses separately.
    Raises the first violated precondition; never partially reports.
    """
    if not subject or not subject.strip():
        raise EmptySubjectError()
    if not statement or not statement.strip():
        raise EmptyStatementError()
    if not evidence_excerpt or not evidence_excerpt.strip():
        raise EmptyExcerptError()
    if claim_category not in MANUAL_CLAIM_EVIDENCE_CATEGORIES:
        raise InvalidClaimCategoryError(claim_category)

    if source_assertion.airport_id is None:
        raise NotCanonicallyAttachedError(source_assertion.id)

    effective = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion.id)
    if effective.effective_decision != AttachmentOutcome.ATTACH_CONFIRMED:
        raise IdentityNotEffectivelyConfirmedError(
            source_assertion.id, effective_decision=effective.effective_decision.value, basis=effective.basis.value,
        )

    if not source_assertion.raw_relevant_text or not excerpt_contains_value(
        source_assertion.raw_relevant_text, evidence_excerpt
    ):
        raise ExcerptNotInPreservedEvidenceError(source_assertion.id)

    airport = session.get(Airport, source_assertion.airport_id)
    if airport is None or not _identity_anchor_present(session, evidence_excerpt, airport):
        raise NoIdentityAnchorError(source_assertion.airport_id)

    amount = _validate_financial_group(
        financial_amount=financial_amount, financial_amount_evidence_token=financial_amount_evidence_token,
        financial_currency=financial_currency, financial_semantic_role=financial_semantic_role,
        evidence_excerpt=evidence_excerpt,
    )
    _validate_temporal_group(
        temporal_qualifier=temporal_qualifier, temporal_year_tokens=temporal_year_tokens,
        evidence_excerpt=evidence_excerpt,
    )
    _validate_relationship_group(
        relationship_party=relationship_party, relationship_role=relationship_role,
        relationship_scope=relationship_scope, evidence_excerpt=evidence_excerpt,
    )

    key = _duplicate_key(
        claim_category=claim_category, evidence_excerpt=evidence_excerpt, financial_amount=amount,
        financial_currency=financial_currency, financial_semantic_role=financial_semantic_role,
        temporal_qualifier=temporal_qualifier, temporal_year_tokens=temporal_year_tokens,
        relationship_party=relationship_party, relationship_role=relationship_role,
    )
    duplicate = _existing_duplicate(session, source_assertion.id, key)
    if duplicate is not None:
        raise DuplicateManualClaimEvidenceError(source_assertion.id, existing_id=duplicate.id)

    return amount


def record_manual_claim_evidence(
    session: Session,
    *,
    source_assertion_id: int,
    claim_category: str,
    subject: str,
    statement: str,
    evidence_excerpt: str,
    analyst: str,
    financial_amount=None,
    financial_amount_evidence_token: "str | None" = None,
    financial_currency: "str | None" = None,
    financial_semantic_role: "str | None" = None,
    financial_not_established: "tuple[str, ...]" = (),
    temporal_qualifier: "str | None" = None,
    temporal_year_tokens: "tuple[str, ...]" = (),
    relationship_party: "str | None" = None,
    relationship_role: "str | None" = None,
    relationship_scope: "str | None" = None,
    extraction_mode: str = "HUMAN_TRANSCRIPTION",
) -> ManualClaimEvidenceResult:
    """Validates every precondition, persists exactly one immutable
    ManualClaimEvidence row. Never commits; calls session.flush() so any
    constraint violation surfaces immediately. Never mutates SourceAssertion,
    Airport, AirportAlias, AirportIdentifier, EvidenceBag,
    IdentityGuardEvaluation, CrossSourceAliasAttestation, IntelligenceReview,
    or Signal.
    """
    with session.no_autoflush:
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)
        if not analyst or not analyst.strip():
            raise EmptyAnalystError()
        if extraction_mode not in MANUAL_CLAIM_EVIDENCE_EXTRACTION_MODES:
            raise ValueError(f"extraction_mode must be one of {MANUAL_CLAIM_EVIDENCE_EXTRACTION_MODES!r}")

        amount = check_manual_claim_evidence_eligibility(
            session, source_assertion=source_assertion, claim_category=claim_category, subject=subject,
            statement=statement, evidence_excerpt=evidence_excerpt, financial_amount=financial_amount,
            financial_amount_evidence_token=financial_amount_evidence_token, financial_currency=financial_currency,
            financial_semantic_role=financial_semantic_role, temporal_qualifier=temporal_qualifier,
            temporal_year_tokens=temporal_year_tokens, relationship_party=relationship_party,
            relationship_role=relationship_role, relationship_scope=relationship_scope,
        )

    row = ManualClaimEvidence(
        source_assertion_id=source_assertion_id,
        claim_category=claim_category,
        subject=subject,
        statement=statement,
        evidence_excerpt=evidence_excerpt,
        analyst=analyst,
        extraction_mode=extraction_mode,
        normalization_version=MANUAL_CLAIM_EVIDENCE_NORMALIZATION_VERSION,
        financial_amount=str(amount) if amount is not None else None,
        financial_amount_evidence_token=financial_amount_evidence_token,
        financial_currency=financial_currency,
        financial_semantic_role=financial_semantic_role,
        financial_not_established=",".join(financial_not_established) if financial_not_established else None,
        temporal_qualifier=temporal_qualifier,
        temporal_year_tokens=",".join(temporal_year_tokens) if temporal_year_tokens else None,
        relationship_party=relationship_party,
        relationship_role=relationship_role,
        relationship_scope=relationship_scope,
    )
    session.add(row)
    session.flush()

    return ManualClaimEvidenceResult(
        manual_claim_evidence_id=row.id, source_assertion_id=source_assertion_id, claim_category=claim_category,
    )


def manual_claim_evidence_to_claim(evidence: ManualClaimEvidence, *, source_assertion: SourceAssertion) -> Claim:
    """Pure, deterministic, zero-inference adapter: same input -> same
    Claim, always. Never queries the database, never fetches a URL, never
    performs currency conversion, never performs translation. `provenance_kind`
    is hardcoded EXPLICIT (see module docstring "FACT VS INFERENCE")."""
    financial = None
    if evidence.financial_amount is not None:
        financial = FinancialFact(
            amount=Decimal(evidence.financial_amount),
            currency=evidence.financial_currency,
            semantic_role=evidence.financial_semantic_role,
            not_established=tuple(evidence.financial_not_established.split(",")) if evidence.financial_not_established else (),
        )

    temporal = None
    if evidence.temporal_qualifier is not None:
        published_date = source_assertion.source.published_date if source_assertion.source is not None else None
        year_tokens = tuple(evidence.temporal_year_tokens.split(",")) if evidence.temporal_year_tokens else ()
        temporal = TemporalContext(
            qualifier=TemporalQualifier(evidence.temporal_qualifier),
            as_of_date=published_date,
            detail=", ".join(year_tokens) if year_tokens else None,
        )

    relationship = None
    if evidence.relationship_party is not None:
        relationship = RelationshipFact(
            party=evidence.relationship_party, role=evidence.relationship_role, scope=evidence.relationship_scope,
        )

    provenance = ClaimProvenance(
        artifact_identity=source_assertion.artifact_identity or f"source_assertion:{source_assertion.id}",
        source_locator=source_assertion.source_locator or source_assertion.source_record_identifier or f"source_assertion:{source_assertion.id}",
        fragment_hash=_sha256_of_text(source_assertion.raw_relevant_text or ""),
        raw_text_excerpt=evidence.evidence_excerpt,
    )

    return Claim(
        category=ClaimCategory(evidence.claim_category),
        subject=evidence.subject,
        statement=evidence.statement,
        provenance=provenance,
        provenance_kind=ProvenanceKind.EXPLICIT,
        financial=financial,
        temporal=temporal,
        relationship=relationship,
    )


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_manual_claims_for_source_assertion(
    session: Session, source_assertion_id: int,
) -> "tuple[Claim, ...] | None":
    """Read-only consumption path - mirrors
    app.services.human_review_claim_enrichment.enrich_claims()'s own exact
    `tuple[Claim, ...] | None` contract (None = "not produced", never a
    fabricated empty tuple pretending to be a real "zero claims" result).
    Returns None when: the SourceAssertion does not exist or has no
    canonical airport_id, EFFECTIVE identity is not currently
    ATTACH_CONFIRMED (re-checked fresh every call - see module docstring
    "EFFECTIVE IDENTITY GATE"), or there are zero ManualClaimEvidence rows.
    Callers wanting the union of automatic + manual claims for one
    assertion should call both this function and enrich_claims()
    separately and combine per their own needs - this module intentionally
    does not provide a fused entrypoint this slice (see module docstring;
    no real assertion currently has both automatic-eligible AND manual
    claims, and general auto/manual reconciliation policy is deliberately
    out of scope)."""
    source_assertion = session.get(SourceAssertion, source_assertion_id)
    if source_assertion is None or source_assertion.airport_id is None:
        return None
    effective = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion_id)
    if effective.effective_decision != AttachmentOutcome.ATTACH_CONFIRMED:
        return None
    rows = session.scalars(
        select(ManualClaimEvidence)
        .where(ManualClaimEvidence.source_assertion_id == source_assertion_id)
        .order_by(ManualClaimEvidence.created_at.asc(), ManualClaimEvidence.id.asc())
    ).all()
    if not rows:
        return None
    return tuple(manual_claim_evidence_to_claim(row, source_assertion=source_assertion) for row in rows)


def preview_manual_claim_evidence(
    session: Session,
    *,
    source_assertion_id: int,
    claim_category: str,
    subject: str,
    statement: str,
    evidence_excerpt: str,
    financial_amount=None,
    financial_amount_evidence_token: "str | None" = None,
    financial_currency: "str | None" = None,
    financial_semantic_role: "str | None" = None,
    temporal_qualifier: "str | None" = None,
    temporal_year_tokens: "tuple[str, ...]" = (),
    relationship_party: "str | None" = None,
    relationship_role: "str | None" = None,
    relationship_scope: "str | None" = None,
) -> ManualClaimEvidencePreview:
    """Read-only. Never persists, flushes, or commits. Reuses the real
    eligibility gate (via try/except) so preview and write can never
    diverge."""
    with session.no_autoflush:
        source_assertion = session.get(SourceAssertion, source_assertion_id)
        if source_assertion is None:
            raise SourceAssertionNotFoundError(source_assertion_id)

        effective = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion_id)
        literal_match = bool(source_assertion.raw_relevant_text) and excerpt_contains_value(
            source_assertion.raw_relevant_text, evidence_excerpt
        )
        airport = session.get(Airport, source_assertion.airport_id) if source_assertion.airport_id else None
        anchor_present = bool(airport) and _identity_anchor_present(session, evidence_excerpt, airport)

        duplicate_id = None
        try:
            amount = _validate_financial_group(
                financial_amount=financial_amount, financial_amount_evidence_token=financial_amount_evidence_token,
                financial_currency=financial_currency, financial_semantic_role=financial_semantic_role,
                evidence_excerpt=evidence_excerpt,
            )
            key = _duplicate_key(
                claim_category=claim_category, evidence_excerpt=evidence_excerpt, financial_amount=amount,
                financial_currency=financial_currency, financial_semantic_role=financial_semantic_role,
                temporal_qualifier=temporal_qualifier, temporal_year_tokens=temporal_year_tokens,
                relationship_party=relationship_party, relationship_role=relationship_role,
            )
            duplicate = _existing_duplicate(session, source_assertion_id, key)
            duplicate_id = duplicate.id if duplicate else None
        except ValueError:
            pass

        eligible = True
        refusal_reason = None
        predicted_claim = None
        try:
            check_manual_claim_evidence_eligibility(
                session, source_assertion=source_assertion, claim_category=claim_category, subject=subject,
                statement=statement, evidence_excerpt=evidence_excerpt, financial_amount=financial_amount,
                financial_amount_evidence_token=financial_amount_evidence_token, financial_currency=financial_currency,
                financial_semantic_role=financial_semantic_role, temporal_qualifier=temporal_qualifier,
                temporal_year_tokens=temporal_year_tokens, relationship_party=relationship_party,
                relationship_role=relationship_role, relationship_scope=relationship_scope,
            )
            synthetic = ManualClaimEvidence(
                source_assertion_id=source_assertion_id, claim_category=claim_category, subject=subject,
                statement=statement, evidence_excerpt=evidence_excerpt, analyst="preview",
                financial_amount=str(Decimal(str(financial_amount))) if financial_amount is not None else None,
                financial_amount_evidence_token=financial_amount_evidence_token, financial_currency=financial_currency,
                financial_semantic_role=financial_semantic_role, temporal_qualifier=temporal_qualifier,
                temporal_year_tokens=",".join(temporal_year_tokens) if temporal_year_tokens else None,
                relationship_party=relationship_party, relationship_role=relationship_role,
                relationship_scope=relationship_scope,
            )
            predicted_claim = manual_claim_evidence_to_claim(synthetic, source_assertion=source_assertion)
        except (ValueError,) as exc:
            eligible = False
            refusal_reason = str(exc)

    return ManualClaimEvidencePreview(
        source_assertion_id=source_assertion_id,
        airport_id=source_assertion.airport_id,
        source_id=source_assertion.source_id,
        raw_identity_decision=source_assertion.identity_guard_decision,
        effective_identity_decision=effective.effective_decision.value,
        effective_identity_basis=effective.basis.value,
        claim_category=claim_category,
        evidence_excerpt=evidence_excerpt,
        literal_excerpt_match=literal_match,
        identity_anchor_present=anchor_present,
        duplicate_of_id=duplicate_id,
        eligible=eligible,
        refusal_reason=refusal_reason,
        predicted_claim=predicted_claim,
    )
