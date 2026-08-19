"""Pure evidence-claim semantics core
(docs/architecture/evidence-to-signal-semantics-design.md, Slice 1 -
docs/architecture/evidence-claim-semantics-core-report.md).

Answers exactly one question: "what does authoritative evidence actually
say?" - never "should RWI create a Signal from this?" (that is
SignalCandidate territory, Slice 3, not built here). This module knows
nothing about SourceAssertion rows, the database, Signal, or any
source-specific extraction pattern (MAC Granicus or otherwise) - it is
the generic representation layer every future source-family extractor
will produce claims INTO, exactly the way
app.services.evidence_attachment_guard.EvidenceBag is the generic
representation layer every extractor already produces evidence bags
into.

Every type here is an immutable, frozen dataclass or str-Enum. This
module performs NO database query, NO database write, NO network call,
NO filesystem access, and reads NO current time (no datetime.now()/
date.today() anywhere) - a claim's temporal meaning is a property of the
EVIDENCE, fixed forever at the moment it was extracted, never of when
the code happens to run.

FINANCIAL SAFETY (docs/architecture/evidence-to-signal-semantics-design.md
S5/S7): there is deliberately no generic "cost"/"amount" field anywhere
in this module. FinancialFact.semantic_role is REQUIRED - a caller
cannot construct a financial fact without stating what kind of amount it
is, and two FinancialFacts with different semantic_role values are
structurally, permanently distinct claims that can never silently merge
into a single "value" the way app/models/signal.py's own
estimated_total_value_usd field already can (see the design doc S2 for
the real, already-shipping collapse this module exists to prevent).
FinancialFact.not_established exists precisely so a financial fact can
carry an explicit record of which conflated readings the evidence does
NOT support, without this module ever attempting general-purpose
logical inference.

TEMPORAL SAFETY (design doc S6): TemporalContext.as_of_date is the date
the CITED DOCUMENT itself carries (its own publication/meeting date) -
never "today." A PLANNED_FUTURE_ACTION claim's qualifier never changes
because wall-clock time has passed; only a NEW Claim, built from NEW
evidence, can ever represent an updated real-world status (see
Claim.provenance_kind and the design doc's own "HISTORY NEVER DELETED"
principle - reused, not reinvented, here).

GENERICITY (design doc S12): no field, enum member, or default value
anywhere in this module names a specific airport, agency, vendor,
currency, or language. The MSP/MAC/Runway Safe worked example lives
entirely in this module's own test suite, never here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

__all__ = [
    "ClaimCategory",
    "ProvenanceKind",
    "TemporalQualifier",
    "TemporalContext",
    "FinancialFact",
    "RelationshipFact",
    "ClaimProvenance",
    "Claim",
]


class ClaimCategory(str, Enum):
    """The claim's own epistemic status - what KIND of statement this
    is, independent of its subject matter. Mirrors docs/architecture/
    evidence-to-signal-semantics-design.md S4's own category table
    exactly - every member here was justified against a real claim
    found in SourceAssertion #222, none added speculatively. str+Enum
    matches the one existing typed-outcome convention already
    established by app.services.evidence_attachment_guard.AttachmentOutcome
    (itself matching app.models.acquisition.AcquisitionRunStatus).

    A single Claim may combine this category with a FinancialFact,
    TemporalContext, and/or RelationshipFact attachment - category is
    the claim's own epistemic shape, not an exhaustive classification of
    everything the claim carries (design doc S4: claim D is a
    PROCEDURAL_REQUEST that also carries a FinancialFact; claim F is an
    EXPLICIT_DOCUMENT_FACT that also carries one).
    """

    EXPLICIT_DOCUMENT_FACT = "explicit_document_fact"
    PROCEDURAL_REQUEST = "procedural_request"
    TEMPORAL_STATEMENT = "temporal_statement"
    RELATIONSHIP = "relationship"


class ProvenanceKind(str, Enum):
    """Whether this claim is stated directly in the cited text (EXPLICIT)
    or was assembled by combining multiple explicit claims/context
    (DERIVED) - e.g. "this evidence concerns MSP" is a DERIVED_ATTACHMENT
    concept in the design doc, never itself raw text. Kept as its own
    orthogonal dimension from ClaimCategory (design doc S2's own "explicit
    vs derived semantics" requirement) rather than folded into category,
    since a DERIVED claim can still be, e.g., a TEMPORAL_STATEMENT."""

    EXPLICIT = "explicit"
    DERIVED = "derived"


class TemporalQualifier(str, Enum):
    """Minimum temporal vocabulary required by
    docs/architecture/evidence-to-signal-semantics-design.md S6 and this
    slice's own task S5 ("historical/current factual statement,
    future/planned statement, completed statement, unknown temporal
    state"). COMPLETED and UNKNOWN complete the design doc's own
    4-member sketch (HISTORICAL_FACT/CURRENT_STATE_AS_OF_DOCUMENT_DATE/
    PLANNED_FUTURE_ACTION/REQUESTED_PENDING_APPROVAL) without
    contradicting it - the design doc itself only ever claimed these were
    the minimum needed for the MSP worked example, not an exhaustive
    final vocabulary."""

    HISTORICAL_FACT = "historical_fact"
    CURRENT_STATE_AS_OF_DOCUMENT_DATE = "current_state_as_of_document_date"
    PLANNED_FUTURE_ACTION = "planned_future_action"
    REQUESTED_PENDING_APPROVAL = "requested_pending_approval"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TemporalContext:
    """A claim's temporal meaning, fixed at extraction time.

    `as_of_date` is the CITED DOCUMENT's own date (e.g. its publication
    or meeting date) - the caller must supply it explicitly; this class
    has no default and this module never calls datetime.now()/
    date.today() to invent one. A PLANNED_FUTURE_ACTION claim with
    as_of_date=2024-08-28 means exactly "as of 2024-08-28, this was a
    planned future action" - forever, regardless of when the code
    evaluating it runs (the hard test this slice's own task requires:
    "the current system date must have ZERO effect on the claim's
    meaning").
    """

    qualifier: TemporalQualifier
    as_of_date: "date | None"
    detail: "str | None" = None


@dataclass(frozen=True)
class FinancialFact:
    """One monetary amount, always paired with its semantic role.

    `semantic_role` is REQUIRED and deliberately free-text (not a closed
    enum): financial roles are domain- and source-family-specific and
    will keep growing (mirrors how app.models.signal.Signal.category is
    itself a plain string column, not a DB-level enum, with only a
    presentation-layer mapping curating known values). What IS fixed,
    structurally, by this dataclass's own shape: there is no
    "amount"/"cost"/"value" field anywhere in this module without a role
    attached - a caller cannot accidentally construct an unlabeled
    financial fact and there is no generic field for one to collapse
    into.

    `not_established` names conflated readings this SPECIFIC fact does
    NOT, by itself, support (design doc S7's negative-constraint
    requirement) - e.g. a "cip_project_ceiling" fact naming
    "contract_value" and "confirmed_vendor_award_amount" as NOT
    established. Free-text labels, not a logical inference engine (per
    instruction) - this module never checks these against anything
    itself; they exist so a human reviewer, or a future Slice 3
    SignalCandidate evaluator, can see the boundary explicitly rather
    than having to re-derive it from raw text every time.
    """

    amount: Decimal
    currency: str
    semantic_role: str
    not_established: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipFact:
    """One named party's role relative to a claim's subject.

    `role` is free-text for the same reason FinancialFact.semantic_role
    is: relationship roles are domain-specific (material_supplier,
    installation_oversight, installation_contractor, sole_source_vendor
    are all real, distinct roles in the MSP worked example - see this
    module's own test suite - and a different source family will have
    its own vocabulary). `scope` optionally narrows what the relationship
    covers (e.g. "EMAS material procurement" vs "installation labor") -
    exactly the distinction that keeps "Runway Safe has oversight of
    installation" from being misread as "Runway Safe is the installation
    contractor."
    """

    party: str
    role: str
    scope: "str | None" = None


@dataclass(frozen=True)
class ClaimProvenance:
    """Traces a claim back to the exact preserved evidence fragment it
    came from - reuses CandidateFragment's/SourceAssertion's own existing
    identity fields verbatim (artifact_identity/source_locator/
    fragment_hash - docs/architecture/ai-discovery-candidate-envelope-
    lifecycle.md S6/S17), never a new identity scheme. `raw_text_excerpt`
    is the actual substring of the fragment's raw text this claim is
    grounded in, so a human reviewer never has to trust a claim's
    `statement` restatement without being able to check it against real
    words. Claims are in-memory and re-derivable (design doc S5) - this
    dataclass does NOT imply a database table; it is exactly what a
    future, still-unbuilt Slice 2 extractor would need to fill in from an
    already-built CandidateFragment, nothing more.
    """

    artifact_identity: str
    source_locator: str
    fragment_hash: str
    raw_text_excerpt: str


@dataclass(frozen=True)
class Claim:
    """One independently supportable statement, decomposed from one
    piece of preserved evidence.

    `subject` names what/who the claim is about (e.g. "the EMAS bed at
    the cited runway end", "the installation contract") - deliberately a
    plain string, not a link to any canonical Airport/Runway/RunwayEnd
    row (this module has no database dependency and resolves no
    identity; subject is a free-text restatement of scope, exactly like
    SourceAssertion.raw_airport_name/raw_runway_value are already
    free-text, unresolved fields).

    `statement` is a short, human-readable, NORMALIZED restatement of
    what the claim asserts - never a substitute for `provenance.raw_text_excerpt`,
    which is what a reviewer actually checks the claim against.

    financial/temporal/relationship are all optional and independently
    combinable - a claim may carry any subset (design doc S4/S15: claim D
    is PROCEDURAL_REQUEST + financial; claim H is TEMPORAL_STATEMENT with
    no financial component at all).
    """

    category: ClaimCategory
    subject: str
    statement: str
    provenance: ClaimProvenance
    provenance_kind: ProvenanceKind = ProvenanceKind.EXPLICIT
    financial: "FinancialFact | None" = None
    temporal: "TemporalContext | None" = None
    relationship: "RelationshipFact | None" = None
