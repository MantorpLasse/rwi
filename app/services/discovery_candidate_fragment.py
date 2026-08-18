"""AI/web-discovery candidate-fragment extraction envelope
(docs/architecture/ai-discovery-candidate-envelope-lifecycle.md S6,
docs/architecture/ai-discovery-candidate-fragment-core-report.md).

Sits between document acquisition (AcquisitionSource/AcquisitionRun/
Snapshot, app/models/acquisition.py - unchanged, not touched here) and the
deterministic Evidence Attachment Guard (app/services/evidence_attachment_guard.py
- unchanged, not touched here). A CandidateFragment is an EXTRACTION
RESULT, never canonical evidence: it carries raw strings an extractor
found in one addressable piece of one acquired document, never an airport
decision. Modeled directly on the existing
app.acquisition.faa_emas_parser.FAAEmasCandidate/FAAEmasParseReport shape
(raw fields, source_locator, fail-closed typed errors) - see the core
report S14 for the exact comparison.

This module performs NO database query, NO database write, NO network
call, and NO filesystem access anywhere. It imports nothing from
app.models or app.database.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from app.services.evidence_attachment_guard import EvidenceBag

__all__ = [
    "CandidateFragmentErrorCode",
    "CandidateFragmentError",
    "ExtractedMoney",
    "ExtractedDate",
    "DiscoveryContext",
    "CandidateFragment",
    "candidate_fragment_to_evidence_bag",
]


class CandidateFragmentErrorCode(str, Enum):
    """Mirrors the fail-closed-with-typed-codes convention already
    established by app.acquisition.faa_emas_parser.FAAEmasParseErrorCode."""

    MISSING_ARTIFACT_IDENTITY = "missing_artifact_identity"
    MISSING_SOURCE_LOCATOR = "missing_source_locator"
    EMPTY_RAW_TEXT = "empty_raw_text"


class CandidateFragmentError(ValueError):
    def __init__(self, code: CandidateFragmentErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code.value


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExtractedMoney:
    """One monetary figure found in a fragment, as an extraction FACT -
    never an investor fact. `context_label` names the figure's semantic
    role ONLY when extraction explicitly determined it (e.g.
    "total_project_cost", "advance_deposit", "faa_funding_share") - left
    None when unknown, never guessed. A $40 million figure with
    context_label=None means exactly that: a dollar figure was found,
    and nothing yet establishes what it is the cost OF (see the SFO 2026
    EMAS temporal-evidence pilot, docs/product/sfo-2026-emas-temporal-evidence-pilot.md,
    for the concrete failure this guards against)."""

    raw_text: str
    numeric_value: Decimal | None = None
    currency: str | None = None
    context_label: str | None = None


@dataclass(frozen=True)
class ExtractedDate:
    """One date found in a fragment, as an extraction fact. `semantic_role`
    (e.g. "construction_start", "approval_date", "publication_date") is
    populated only when explicitly extracted, never inferred - an
    unlabeled date is exactly as reliable as it looks and no more."""

    raw_text: str
    normalized_date: date | None = None
    semantic_role: str | None = None


@dataclass(frozen=True)
class DiscoveryContext:
    """Audit-only discovery/search metadata. Deliberately isolated in its
    own dataclass, never merged into CandidateFragment's other fields, so
    that candidate_fragment_to_evidence_bag() (below) can be reviewed by
    inspection alone as never touching it - and is proven never to by
    test (test_discovery_candidate_fragment.py). The search query that
    led to a fragment's discovery is preserved here for audit ONLY - it
    must never become identity evidence (the literal SFO/MSP lesson;
    docs/architecture/ai-discovery-evidence-attachment-guard.md S2)."""

    search_query: str | None = None
    discovery_channel: str | None = None
    seed_airport: str | None = None
    discovered_at: datetime | None = None


@dataclass(frozen=True)
class CandidateFragment:
    """One addressable, extracted piece of one acquired document.

    REQUIRED: artifact_identity, source_locator, raw_text - together this
    is the fragment's identity (`.identity` property below), reusing
    SourceAssertion's own field names/semantics
    (artifact_identity/source_locator/raw_fragment_hash) rather than
    inventing a parallel identity scheme
    (docs/architecture/ai-discovery-candidate-envelope-lifecycle.md S6/S17).
    `artifact_identity` names the parent document/Snapshot this fragment
    came from (e.g. an AcquisitionSource.key + Snapshot.sha256 composite)
    - this module never validates it against a database; it is an opaque,
    caller-supplied string.

    OPTIONAL EXTRACTED VALUES map directly onto
    app.services.evidence_attachment_guard.EvidenceBag's own categories
    (see candidate_fragment_to_evidence_bag() below) plus a small set of
    extraction facts (money/dates/project-and-contract identifiers/
    terminology) the guard has no concept of and does not consume -
    those remain on this object only, for later correlation/review, never
    resolved to a database row here (no DB lookup, per instruction).

    `organizations` is deliberately NOT a separate field from `issuers`:
    it would be a pure alias of the same "issuer/publisher/authority"
    concept EvidenceBag.issuers already represents, and adding it would
    be exactly the kind of speculative duplicate field this module's
    design brief warns against.

    AUDIT_ONLY: parser_identifier, extracted_at, publication_date,
    document_title, url, discovery_context - carried for provenance/
    review only; discovery_context specifically is NEVER read by
    candidate_fragment_to_evidence_bag() (proven by test).
    """

    # --- REQUIRED ---
    artifact_identity: str
    source_locator: str
    raw_text: str

    # --- OPTIONAL EXTRACTED VALUES: mapped to EvidenceBag ---
    airport_identifiers: frozenset[str] = field(default_factory=frozenset)
    airport_names: frozenset[str] = field(default_factory=frozenset)
    locations: frozenset[str] = field(default_factory=frozenset)
    runway_ends: frozenset[str] = field(default_factory=frozenset)
    runway_pairs: frozenset[str] = field(default_factory=frozenset)
    issuers: frozenset[str] = field(default_factory=frozenset)

    # Pre-classified only (by upstream extraction/reference-table lookup/
    # human review) as identifying a DIFFERENT, specific airport than
    # whichever candidate this fragment is later evaluated against - this
    # module never classifies these itself, only passes them through
    # (task S9 - the adapter must not guess contradictions).
    contradicting_names: frozenset[str] = field(default_factory=frozenset)
    contradicting_issuers: frozenset[str] = field(default_factory=frozenset)
    contradicting_locations: frozenset[str] = field(default_factory=frozenset)

    # Pre-resolved only, by a caller EXTERNAL to both this module and the
    # guard (see app.services.candidate_fragment_enrichment - a separate,
    # optional, deterministic post-extraction step) as the real, known
    # canonical runway topology of one SPECIFIC other airport this
    # fragment's own runway_ends/runway_pairs are independently confirmed
    # to belong to. Mirrors EvidenceBag.alternate_airport_runway_ends/
    # _pairs field-for-field (docs/architecture/ai-discovery-evidence-
    # attachment-guard.md S5 rule 3) - this class never computes,
    # infers, or looks these up itself; extraction (app/acquisition/*
    # extractors) never populates them either. Empty by default, exactly
    # like contradicting_* above - a fragment with no enrichment step
    # applied carries no alternate-airport evidence at all, which is
    # exactly the pre-enrichment MSP pilot behavior
    # (docs/product/msp-authoritative-discovery-provider-pilot.md S8).
    alternate_airport_runway_ends: frozenset[str] = field(default_factory=frozenset)
    alternate_airport_runway_pairs: frozenset[str] = field(default_factory=frozenset)

    # --- OPTIONAL EXTRACTED VALUES: extraction facts, not guard inputs ---
    project_identifiers: frozenset[str] = field(default_factory=frozenset)
    contract_identifiers: frozenset[str] = field(default_factory=frozenset)
    money_values: tuple[ExtractedMoney, ...] = field(default_factory=tuple)
    dates: tuple[ExtractedDate, ...] = field(default_factory=tuple)
    terminology_hits: frozenset[str] = field(default_factory=frozenset)
    language: str | None = None

    # --- AUDIT_ONLY ---
    document_title: str | None = None
    url: str | None = None
    publication_date: date | None = None
    parser_identifier: str | None = None
    extracted_at: datetime | None = None
    discovery_context: DiscoveryContext | None = None

    def __post_init__(self) -> None:
        if not self.artifact_identity or not self.artifact_identity.strip():
            raise CandidateFragmentError(
                CandidateFragmentErrorCode.MISSING_ARTIFACT_IDENTITY,
                "CandidateFragment.artifact_identity is required and cannot be empty.",
            )
        if not self.source_locator or not self.source_locator.strip():
            raise CandidateFragmentError(
                CandidateFragmentErrorCode.MISSING_SOURCE_LOCATOR,
                "CandidateFragment.source_locator is required and cannot be empty.",
            )
        if not self.raw_text or not self.raw_text.strip():
            raise CandidateFragmentError(
                CandidateFragmentErrorCode.EMPTY_RAW_TEXT,
                "CandidateFragment.raw_text is required and cannot be empty.",
            )
        # Computed once at construction, exactly the normalize-once
        # posture CandidateAirport/EvidenceBag already use
        # (app/services/evidence_attachment_guard.py) - deterministic:
        # same raw_text always yields the same hash.
        object.__setattr__(self, "_raw_fragment_sha256", _sha256_of_text(self.raw_text))

    @property
    def fragment_hash(self) -> str:
        """SHA-256 of `raw_text`, computed once at construction."""
        return self._raw_fragment_sha256  # type: ignore[attr-defined]

    @property
    def identity(self) -> tuple[str, str, str]:
        """Deterministic fragment identity:
        (artifact_identity, source_locator, fragment_hash) - reuses
        exactly the (document identity, source_locator) + raw fragment
        hash shape already described for SourceAssertion
        (docs/architecture/ai-discovery-candidate-envelope-lifecycle.md
        S6/S17), never derived from search-query text or any
        DiscoveryContext field."""
        return (self.artifact_identity, self.source_locator, self.fragment_hash)


def candidate_fragment_to_evidence_bag(fragment: CandidateFragment) -> EvidenceBag:
    """Pure adapter: CandidateFragment -> EvidenceBag. Performs no I/O,
    resolves no Airport, infers no topology, computes no confidence score,
    and NEVER reads `fragment.discovery_context` - proven by test
    (test_search_context_excluded_from_evidence_bag and
    test_evidence_bag_identical_regardless_of_discovery_context). Any
    contradiction fields present, and any alternate_airport_runway_ends/
    _pairs, are passed through exactly as supplied, one-to-one; this
    function never classifies a name/issuer/location as contradictory
    itself and never computes/looks up alternate-airport topology itself
    (that remains the job of the separate, optional
    app.services.candidate_fragment_enrichment step, applied - if at all -
    before this adapter runs).
    """
    return EvidenceBag(
        identifiers=fragment.airport_identifiers,
        names=fragment.airport_names,
        runway_ends=fragment.runway_ends,
        runway_pairs=fragment.runway_pairs,
        issuers=fragment.issuers,
        locations=fragment.locations,
        contradicting_names=fragment.contradicting_names,
        contradicting_issuers=fragment.contradicting_issuers,
        contradicting_locations=fragment.contradicting_locations,
        alternate_airport_runway_ends=fragment.alternate_airport_runway_ends,
        alternate_airport_runway_pairs=fragment.alternate_airport_runway_pairs,
        document_title=fragment.document_title,
        project_number=_join_or_none(fragment.project_identifiers),
        contract_number=_join_or_none(fragment.contract_identifiers),
        url=fragment.url,
    )


def _join_or_none(values: frozenset[str]) -> str | None:
    """Audit-only join for EvidenceBag's singular project_number/
    contract_number fields (never used in any guard decision) - a
    CandidateFragment may carry more than one of either; this collapses
    them into one readable string for the decision's own reason text,
    never for identity comparison."""
    if not values:
        return None
    return ", ".join(sorted(values))
