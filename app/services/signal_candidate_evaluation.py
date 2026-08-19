"""Pure SignalCandidate evaluation core
(docs/architecture/evidence-to-signal-semantics-design.md, Slice 3 -
docs/architecture/signal-candidate-core-slice3-report.md).

    tuple[Claim, ...] (app.services.evidence_claim_semantics, unmodified)
        -> evaluate_signal_candidate()
        -> SignalCandidateDecision
        -> STOP (human review; no Signal write happens here or ever
           automatically in this generation)

Answers exactly one question: "are these supported claims materially
interesting enough that a human should look at them?" - never "create or
update a Signal" (no `Signal` import exists anywhere in this module, and
none is needed). This module knows nothing about a specific source family
(MAC/Granicus or otherwise), a specific airport, a specific vendor, or a
specific currency - it is the second, source-agnostic evaluation layer
sitting directly on top of the Slice 1 claim core, exactly as
app.services.evidence_attachment_guard is the deterministic evaluation
layer sitting on top of app.services.discovery_candidate_fragment.

This module performs NO database query, NO database write, NO network
call, NO filesystem access, and reads NO current time anywhere - every
materiality judgment is computed purely from the structural shape of the
Claim tuple handed to it (categories, and which of financial/temporal/
relationship each claim carries), never from re-reading raw document text
and never from wall-clock time.

NO OUTCOME IN THIS MODULE MEANS "WRITE A SIGNAL AUTOMATICALLY." Every path
that could plausibly become a Signal terminates at REVIEW_REQUIRED -
matching the design doc's own S10 table and RWI's own existing, working
precedent (app.services.signal_rules never auto-promotes past
confidence="low" either). There is deliberately no AUTO_PROMOTE outcome
and never will be one added silently.

IDENTITY GATE (S7): reuses app.services.evidence_attachment_guard.AttachmentOutcome
directly as the identity-state vocabulary, rather than inventing a second,
parallel enum that could drift out of sync with the real identity-guard
decision values - the same discipline as reusing CandidateFragment's own
field names verbatim in app.services.evidence_claim_semantics.ClaimProvenance.
Only AttachmentOutcome.ATTACH_CONFIRMED is treated as sufficient to run
intelligence review at all (a deliberately stricter fail-closed posture
than the design doc's own S10 table, per this slice's own explicit
instruction: "ATTACH_PROVISIONAL must NOT be treated as confirmed unless
the approved architecture explicitly allows it" - it does not, here).

CATEGORY-SUGGESTION GAP (documented, not solved): `Claim` (Slice 1) has no
structured "event type" field - `subject`/`statement` are free text and
`FinancialFact.semantic_role`/`RelationshipFact.role` are extractor-chosen,
source-family-specific strings (e.g. "advance_deposit_purchase_order" is
MAC-Granicus vocabulary, not a generic Signal-category signal). Building a
`suggested_signal_category` mapping would therefore require either reading
raw document text (this module deliberately never does) or hard-coding one
source family's own role vocabulary into an otherwise source-agnostic
module (exactly the kind of premature MAC-specific leakage task S21
forbids). `SignalCandidateDecision.suggested_signal_category` is kept in
the result shape for forward compatibility but is never populated in this
generation - flagged explicitly in `warnings` instead of silently guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.evidence_claim_semantics import Claim, ClaimCategory, TemporalQualifier

__all__ = [
    "SignalCandidateOutcome",
    "SignalCandidateContext",
    "SignalCandidateDecision",
    "evaluate_signal_candidate",
]


class SignalCandidateOutcome(str, Enum):
    """The approved decision vocabulary from
    docs/architecture/evidence-to-signal-semantics-design.md S10, carried
    forward unchanged - no outcome added, none renamed, no AUTO_PROMOTE
    invented. A distinct type from AttachmentOutcome even though both use
    a "REVIEW_REQUIRED" label: identity review and intelligence review are
    two genuinely separate queues (design doc S13), and conflating their
    result types would blur that boundary even if the string looks the
    same."""

    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INSUFFICIENT_MATERIALITY = "INSUFFICIENT_MATERIALITY"
    IDENTITY_NOT_CONFIRMED = "IDENTITY_NOT_CONFIRMED"
    CONTRADICTED = "CONTRADICTED"
    DUPLICATE_WITHIN_EVIDENCE = "DUPLICATE_WITHIN_EVIDENCE"
    STALE_OR_SUPERSEDED = "STALE_OR_SUPERSEDED"


@dataclass(frozen=True)
class SignalCandidateContext:
    """The minimal explicit input this pure core needs beyond the claims
    themselves - never a database lookup. `identity_decision` is the
    already-computed identity-guard outcome for the evidence these claims
    were extracted from (the caller reads it from
    SourceAssertion.identity_guard_decision or an equivalent in-memory
    AttachmentDecision - this module never queries either). `superseded`/
    `superseded_reason` are likewise caller-supplied facts, never inferred
    from calendar age (task S14: "Do not infer staleness from calendar age
    alone")."""

    identity_decision: AttachmentOutcome
    superseded: bool = False
    superseded_reason: "str | None" = None


@dataclass(frozen=True)
class SignalCandidateDecision:
    """An evaluation result - never an ORM object, never persisted by this
    module. `material_claims` is a subset of the (deduplicated) input
    claims this decision's reasoning actually relied on. `reason` is a
    deterministic, template-built string - never LLM-generated - built
    only from each claim's own already-honest category/semantic_role/
    qualifier/role values, so it structurally cannot say more than the
    claims themselves already say (task S17)."""

    outcome: SignalCandidateOutcome
    reason: str
    material_claims: "tuple[Claim, ...]" = ()
    suggested_signal_category: "str | None" = None
    warnings: "tuple[str, ...]" = ()


def _is_event_or_action(claim: Claim) -> bool:
    """A claim asserts something is true or requests that something be
    done - the two categories that represent "something is happening,"
    as opposed to RELATIONSHIP (who) or TEMPORAL_STATEMENT (when) alone,
    which describe context but not, by themselves, an event."""
    return claim.category in (ClaimCategory.EXPLICIT_DOCUMENT_FACT, ClaimCategory.PROCEDURAL_REQUEST)


def _detect_contradiction(claims: "tuple[Claim, ...]") -> "tuple[str, tuple[Claim, ...]] | None":
    """One narrow, explainable contradiction rule (task S13: "keep
    contradiction rules narrow and explainable... do not build a general
    theorem prover"): if two claims in the SAME evidence set name
    DIFFERENT parties for the IDENTICAL relationship role on the IDENTICAL
    subject, the evidence set cannot be safely interpreted (who, in fact,
    holds that role?). Deliberately narrow: this is the one contradiction
    shape the Claim Core's existing structural fields (subject + role +
    party) can express without inference. Broader contradictions (e.g. one
    claim implying a project was cancelled, another implying it proceeds)
    are NOT detected - the Claim Core has no negation/status-change
    vocabulary to express that safely today; documented as a known
    limitation (task S13's own explicit instruction), not solved via
    text-based inference here.

    Returns the reason plus only the SPECIFIC claims involved in the
    conflicting (subject, role) pair - never every relationship claim in
    the set, so a reviewer sees exactly what conflicts and nothing else."""
    claims_by_subject_role: "dict[tuple[str, str], list[Claim]]" = {}
    for claim in claims:
        if claim.relationship is not None:
            key = (claim.subject, claim.relationship.role)
            claims_by_subject_role.setdefault(key, []).append(claim)
    for (subject, role), grouped in claims_by_subject_role.items():
        parties = {c.relationship.party for c in grouped}
        if len(parties) > 1:
            reason = (
                f"conflicting parties named for relationship role '{role}' on subject "
                f"'{subject}': {', '.join(sorted(parties))} - cannot be safely interpreted."
            )
            return reason, tuple(grouped)
    return None


def _financial_summary(claims: "tuple[Claim, ...]") -> "list[str]":
    seen: "set[tuple[str, object, str]]" = set()
    parts: "list[str]" = []
    for claim in claims:
        if claim.financial is None:
            continue
        key = (claim.financial.semantic_role, claim.financial.amount, claim.financial.currency)
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{claim.financial.semantic_role}={claim.financial.amount} {claim.financial.currency}")
    return parts


def _relationship_summary(claims: "tuple[Claim, ...]") -> "list[str]":
    seen: "set[tuple[str, str]]" = set()
    parts: "list[str]" = []
    for claim in claims:
        if claim.relationship is None:
            continue
        key = (claim.relationship.party, claim.relationship.role)
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{claim.relationship.party} ({claim.relationship.role})")
    return parts


def _procedural_summary(claims: "tuple[Claim, ...]") -> "list[str]":
    return [claim.subject for claim in claims if claim.category == ClaimCategory.PROCEDURAL_REQUEST]


def _temporal_summary(claims: "tuple[Claim, ...]") -> "list[str]":
    parts: "list[str]" = []
    for claim in claims:
        if claim.temporal is None or claim.temporal.qualifier == TemporalQualifier.UNKNOWN:
            continue
        as_of = f" (as of {claim.temporal.as_of_date})" if claim.temporal.as_of_date else ""
        parts.append(f"{claim.subject}: {claim.temporal.qualifier.value}{as_of}")
    return parts


def _is_materially_interesting(claims: "tuple[Claim, ...]") -> bool:
    """Explicit rule combination (task S5/S11), never fuzzy scoring:
    there must be at least one EXPLICIT_DOCUMENT_FACT/PROCEDURAL_REQUEST
    claim (something is asserted or requested) AND at least one of:
    - a second, distinct EXPLICIT_DOCUMENT_FACT/PROCEDURAL_REQUEST claim
      sharing the same subject (S11.A - lifecycle expiry + replacement
      required both describing the same physical thing),
    - any claim carrying a financial fact (S11.C - procurement + financial),
    - any claim carrying a relationship fact (S11.B - replacement + named
      vendor relationship),
    - any claim carrying a non-UNKNOWN temporal qualifier (S11.D - future
      installation action / completion-status-change + event context).

    A single bare event/action claim with no corroborating structure at
    all (S19: "generic EMAS mention," "static runway inventory fact," "old
    non-material descriptive fact") does NOT clear this bar -
    INSUFFICIENT_MATERIALITY. A relationship or financial claim with NO
    event/action claim asserting anything is happening (S19: "generic
    vendor mention," "airport identity only") does not clear it either.
    """
    event_claims = [c for c in claims if _is_event_or_action(c)]
    if not event_claims:
        return False

    subjects_seen: "set[str]" = set()
    duplicate_subject = False
    for claim in event_claims:
        if claim.subject in subjects_seen:
            duplicate_subject = True
        subjects_seen.add(claim.subject)

    has_relationship = any(c.relationship is not None for c in claims)
    has_financial = any(c.financial is not None for c in claims)
    has_active_temporal = any(
        c.temporal is not None and c.temporal.qualifier != TemporalQualifier.UNKNOWN for c in claims
    )

    return duplicate_subject or has_relationship or has_financial or has_active_temporal


def evaluate_signal_candidate(
    claims: "tuple[Claim, ...]", context: SignalCandidateContext,
) -> SignalCandidateDecision:
    """Pure, deterministic: the same (claims, context) pair always
    produces the same decision. Consumes already-extracted Claims only -
    never reads a document, never runs extraction, never queries a
    database, never resolves an airport."""

    # --- Identity gate (task S7) - runs before claims are even inspected. ---
    if context.identity_decision != AttachmentOutcome.ATTACH_CONFIRMED:
        return SignalCandidateDecision(
            outcome=SignalCandidateOutcome.IDENTITY_NOT_CONFIRMED,
            reason=(
                f"Airport identity is not confirmed (identity_decision="
                f"{context.identity_decision.value}). Intelligence review does not run until "
                f"identity review independently reaches ATTACH_CONFIRMED."
            ),
        )

    unique_claims = tuple(dict.fromkeys(claims))

    # --- Contradiction (task S13). ---
    contradiction = _detect_contradiction(unique_claims)
    if contradiction is not None:
        contradiction_reason, conflicting = contradiction
        return SignalCandidateDecision(
            outcome=SignalCandidateOutcome.CONTRADICTED,
            reason=f"Evidence set contains a contradiction: {contradiction_reason}",
            material_claims=conflicting,
        )

    # --- Stale/superseded (task S14) - only ever from explicit context. ---
    if context.superseded:
        return SignalCandidateDecision(
            outcome=SignalCandidateOutcome.STALE_OR_SUPERSEDED,
            reason=context.superseded_reason or "Evidence explicitly marked superseded by newer evidence.",
            material_claims=unique_claims,
        )

    # --- Duplicate-within-evidence (task S12): the ENTIRE claim set
    # collapses to one distinct claim, repeated - not corroborating
    # evidence, just the same fact restated. ---
    if claims and len(unique_claims) == 1 and len(claims) > len(unique_claims):
        return SignalCandidateDecision(
            outcome=SignalCandidateOutcome.DUPLICATE_WITHIN_EVIDENCE,
            reason=(
                f"All {len(claims)} input claims are structurally equal to a single distinct "
                f"claim - no corroborating or additional evidence present, only repeated copies "
                f"of the same fact."
            ),
            material_claims=unique_claims,
        )

    # --- Materiality (task S5/S11). ---
    if not _is_materially_interesting(unique_claims):
        return SignalCandidateDecision(
            outcome=SignalCandidateOutcome.INSUFFICIENT_MATERIALITY,
            reason=(
                f"{len(unique_claims)} claim(s) present, but none combine a category-qualifying "
                f"fact/request with corroborating financial, relationship, or dated-activity "
                f"evidence. Identity or topic mention alone is not investor intelligence."
            ),
        )

    financial_parts = _financial_summary(unique_claims)
    relationship_parts = _relationship_summary(unique_claims)
    procedural_parts = _procedural_summary(unique_claims)
    temporal_parts = _temporal_summary(unique_claims)
    event_categories = sorted({c.category.value for c in unique_claims if _is_event_or_action(c)})

    reason_segments = [
        f"Material claim combination found: {len(event_categories)} category-qualifying claim "
        f"type(s) present ({', '.join(event_categories)}).",
    ]
    if financial_parts:
        reason_segments.append("Financial roles (kept structurally distinct): " + "; ".join(financial_parts) + ".")
    if relationship_parts:
        reason_segments.append("Named relationships: " + "; ".join(relationship_parts) + ".")
    if procedural_parts:
        reason_segments.append(
            "Procedural request(s), pending - not approved/awarded/executed: " + "; ".join(procedural_parts) + "."
        )
    if temporal_parts:
        reason_segments.append("Dated/temporal facts: " + "; ".join(temporal_parts) + ".")
    reason_segments.append("Human review required before any Signal is created or updated.")

    warnings: "list[str]" = [
        "suggested_signal_category not populated: Claim Core has no structured event-type field "
        "to map onto Signal's category vocabulary without either reading raw document text or "
        "hard-coding one source family's role vocabulary into this generic evaluator; category "
        "selection remains a human decision at promotion time.",
    ]

    return SignalCandidateDecision(
        outcome=SignalCandidateOutcome.REVIEW_REQUIRED,
        reason=" ".join(reason_segments),
        material_claims=unique_claims,
        suggested_signal_category=None,
        warnings=tuple(warnings),
    )
