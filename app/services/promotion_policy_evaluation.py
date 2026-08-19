"""Pure promotion-policy evaluation core
(docs/architecture/signal-promotion-policy-slice5-design.md, Slice 6 -
docs/architecture/promotion-policy-core-slice6-report.md).

    SignalCandidateDecision (app.services.signal_candidate_evaluation, unmodified)
        + tuple[Claim, ...] (app.services.evidence_claim_semantics, unmodified)
        + PromotionPolicyContext (new, small, explicit)
        -> evaluate_promotion_policy()
        -> PromotionPolicyDecision
        -> STOP (no Signal write happens here or ever automatically in this
           generation; a future, separately-authorized policy is required
           before AUTO_ELIGIBLE evidence is ever actually written)

Answers exactly one question, one layer downstream of Slice 3: "given
evidence that has already cleared intelligence review, could it someday be
safely automated, does it need a human's judgment, or should it never become
a Signal from what we currently have?" It never asks "create or update a
Signal" - no `Signal` import exists anywhere in this module, and none is
needed. This module knows nothing about a specific source family, airport,
vendor, or currency - claims are consumed only through their already-typed
category/financial/temporal/relationship shape, exactly the same
source-agnostic discipline `evaluate_signal_candidate()` (Slice 3) already
established one layer below.

This module performs NO database query, NO database write, NO network call,
NO filesystem access, and reads NO current time anywhere - every eligibility
judgment is computed purely from the structural shape of the Claim tuple and
the explicit, caller-supplied PromotionPolicyContext, never from re-reading
raw document text and never from wall-clock time.

AUTO_ELIGIBLE IS NOT A WRITE INSTRUCTION. It means exactly: "this evidence
satisfies deterministic structural conditions that could eventually permit
automation, once a separately-authorized policy decides to turn automation
on." Nothing in this module, and nothing this module is imported by, ever
writes a Signal - that remains a future, explicitly out-of-scope slice
(design doc S25's Slice 9/10).

SIGNALCANDIDATE MAPPING (design doc S5): five of SignalCandidateOutcome's six
members answer "is this evidence usable at all" and need no further
judgment - they map directly to DO_NOT_PROMOTE. Only REVIEW_REQUIRED (identity
confirmed, materiality established, no contradiction/staleness/duplication)
proceeds to the finer AUTO_ELIGIBLE / HUMAN_REVIEW_REQUIRED split below. This
module assumes evaluate_signal_candidate() has already been called on the
SAME claims tuple - it does not re-derive materiality, contradiction, or
identity confirmation itself, and never re-imports evidence_attachment_guard.

SOURCE-AUTHORITY TIER (design doc S12/S15): a real, unresolved infrastructure
gap - Source.reliability_level cannot today distinguish a primary authority's
own record from a government record about that authority from news
reporting. SourceAuthorityTier below is an IN-MEMORY POLICY TYPE ONLY, never
written to Source.reliability_level or any other column - the caller must
supply it from context this module cannot obtain itself, and an absent tier
(`None`) fails closed to DO_NOT_PROMOTE, never defaults to permissive.

FINANCIAL/RELATIONSHIP ALLOWLISTS (design doc S9/S22) are deliberately
narrow in this generation: only `contract_award_amount` (financial) and
`awarded_contractor`/`confirmed_contract_vendor` (relationship) are treated
as auto-safe. The design doc itself named `purchase_order_amount` and
`grant_award` as merely CONDITIONALLY safe (safe only when a further nuance -
"issued not requested," "already obligated not merely applied for" - also
holds, which this module cannot verify from a FinancialFact alone without
either reading raw text or trusting an unverifiable claim). Rather than
approximate that nuance, this generation leaves both out of the allowlist
entirely - narrower than the design doc's own ceiling, matching Slice 5's own
explicit "AUTO_ELIGIBLE must be narrow" principle. Any role not on the
allowlist - known-unsafe (`cip_project_ceiling`, `advance_deposit_purchase_order`,
`estimated_project_cost`, ...) or simply unrecognized - blocks AUTO_ELIGIBLE
identically; this is an allowlist, not a blocklist, so an unknown role always
fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.services.evidence_claim_semantics import Claim, ClaimCategory, TemporalQualifier
from app.services.signal_candidate_evaluation import SignalCandidateDecision, SignalCandidateOutcome

__all__ = [
    "SourceAuthorityTier",
    "PromotionPolicyOutcome",
    "PromotionPolicyContext",
    "PromotionPolicyDecision",
    "evaluate_promotion_policy",
]


class SourceAuthorityTier(str, Enum):
    """An in-memory-only policy classification of how authoritative a piece
    of evidence's source is - never written to Source.reliability_level or
    any other database column (design doc S6's own explicit instruction).
    The caller derives this from whatever it already knows about the
    acquisition provider/source family (e.g. app.acquisition.mac_granicus.py
    only ever scrapes an airport commission's own Granicus system - always
    Tier 1 for evidence that pathway produces), never from this module,
    which has no way to inspect a Source row itself."""

    TIER_1_PRIMARY_OFFICIAL = "TIER_1_PRIMARY_OFFICIAL"
    TIER_2_OFFICIAL_GOVERNMENT = "TIER_2_OFFICIAL_GOVERNMENT"
    TIER_3_CREDIBLE_SECONDARY = "TIER_3_CREDIBLE_SECONDARY"
    TIER_4_UNVERIFIED = "TIER_4_UNVERIFIED"


class PromotionPolicyOutcome(str, Enum):
    """The three-lane vocabulary from
    docs/architecture/signal-promotion-policy-slice5-design.md S5. No
    AUTO_PROMOTE, no automatic-write member - AUTO_ELIGIBLE is an
    eligibility classification only (module docstring above)."""

    AUTO_ELIGIBLE = "AUTO_ELIGIBLE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DO_NOT_PROMOTE = "DO_NOT_PROMOTE"


@dataclass(frozen=True)
class PromotionPolicyContext:
    """The minimal explicit input this pure core needs beyond
    SignalCandidateDecision and the claims themselves - never a database
    lookup, never raw source text, never provider-specific state.

    `source_authority_tier`: caller-supplied, `None` when unknown - fails
    closed (design doc S6 rule 7). `corroborating_source_count`: how many
    independently-provenanced sources the CALLER has already verified
    support this same real-world event - this module never infers
    independence itself (design doc S13/S16's own explicit prohibition on
    "source count = truth"). `requires_corroboration`: an explicit,
    caller-supplied flag for claim shapes the caller already knows need more
    than one source (e.g. an inference-shaped claim) - defaults to False,
    meaning a single sufficiently-authoritative source is assumed adequate
    for an explicit single-document award/completion claim, per design doc
    S13's own conclusion. `superseded`: reused verbatim from
    SignalCandidateContext's own field/discipline - never inferred from
    calendar age."""

    source_authority_tier: "SourceAuthorityTier | None" = None
    corroborating_source_count: int = 1
    requires_corroboration: bool = False
    superseded: bool = False


@dataclass(frozen=True)
class PromotionPolicyDecision:
    """An evaluation result - never an ORM object, never persisted by this
    module. `reason` is a deterministic, template-built string - never
    LLM-generated, never naming a specific source family/vendor by type
    (only by the values already present in the claims themselves).
    `auto_eligibility_reasons` names which AUTO_ELIGIBLE rules this claim
    set DID satisfy; `review_reasons` names which HUMAN_REVIEW_REQUIRED
    shapes applied; `blocking_reasons` names which specific rule(s) failed
    and why - all three may be non-empty at once (e.g. a HUMAN_REVIEW_REQUIRED
    decision reports both what it satisfied and what blocked it)."""

    outcome: PromotionPolicyOutcome
    reason: str
    auto_eligibility_reasons: "tuple[str, ...]" = ()
    review_reasons: "tuple[str, ...]" = ()
    blocking_reasons: "tuple[str, ...]" = ()


# Deliberately narrow - see module docstring. Allowlists, not blocklists: any
# role absent from these sets is treated as unsafe, whether it is a known
# ceiling/estimate/deposit role or simply unrecognized.
_AUTO_SAFE_FINANCIAL_ROLES = frozenset({"contract_award_amount"})
_AUTO_SAFE_RELATIONSHIP_ROLES = frozenset({"awarded_contractor", "confirmed_contract_vendor"})
_HAPPENED_QUALIFIERS = frozenset({TemporalQualifier.HISTORICAL_FACT, TemporalQualifier.COMPLETED})


def _is_actually_happened_event(claim: Claim) -> bool:
    """category in {EXPLICIT_DOCUMENT_FACT, PROCEDURAL_REQUEST} alone is not
    enough - the SAME claim's own temporal qualifier must assert the event
    has already occurred, never merely been planned or requested (design doc
    S6 rule 3)."""
    return (
        claim.category in (ClaimCategory.EXPLICIT_DOCUMENT_FACT, ClaimCategory.PROCEDURAL_REQUEST)
        and claim.temporal is not None
        and claim.temporal.qualifier in _HAPPENED_QUALIFIERS
    )


def evaluate_promotion_policy(
    signal_candidate: SignalCandidateDecision, claims: "tuple[Claim, ...]", context: PromotionPolicyContext,
) -> PromotionPolicyDecision:
    """Pure, deterministic: the same (signal_candidate, claims, context)
    triple always produces the same decision. Assumes `signal_candidate` was
    already computed by `evaluate_signal_candidate(claims, ...)` against the
    SAME claims tuple passed here - this module never re-derives identity
    confirmation, contradiction, staleness, duplication, or materiality
    itself; it only evaluates the further, narrower question of promotion
    eligibility for evidence that has already cleared all of those gates."""

    # --- Outcome mapping (design doc S5): five of six SignalCandidateOutcome
    # values need no further judgment - they already mean "not usable." ---
    if signal_candidate.outcome != SignalCandidateOutcome.REVIEW_REQUIRED:
        return PromotionPolicyDecision(
            outcome=PromotionPolicyOutcome.DO_NOT_PROMOTE,
            reason=(
                f"SignalCandidate outcome is {signal_candidate.outcome.value}, not REVIEW_REQUIRED - "
                f"promotion policy does not evaluate eligibility for evidence that has not cleared "
                f"intelligence review. No Signal-eligible evidence is lost: it remains preserved and "
                f"queryable as governed evidence regardless of this outcome."
            ),
            blocking_reasons=(f"signal_candidate_outcome={signal_candidate.outcome.value}",),
        )

    # --- Superseded gate (design doc S16): unconditional, regardless of how
    # strong the claims otherwise are. ---
    if context.superseded:
        return PromotionPolicyDecision(
            outcome=PromotionPolicyOutcome.DO_NOT_PROMOTE,
            reason=(
                "Evidence is explicitly marked superseded by newer evidence - promotion policy does "
                "not evaluate superseded evidence regardless of claim strength. Superseded status is "
                "never inferred from calendar age."
            ),
            blocking_reasons=("superseded",),
        )

    unique_claims = tuple(dict.fromkeys(claims))

    auto_eligibility_reasons: "list[str]" = []
    review_reasons: "list[str]" = []
    blocking_reasons: "list[str]" = []

    # Rule: at least one claim asserts an event that has actually happened.
    has_happened_event = any(_is_actually_happened_event(c) for c in unique_claims)
    if has_happened_event:
        auto_eligibility_reasons.append("at least one claim asserts an event with an explicit historical/completed temporal state")
    else:
        review_reasons.append("no claim asserts an explicit historical/completed event - evidence describes only a request, plan, or state")
        blocking_reasons.append("no_happened_event")

    # Rule: every financial claim present must carry an allowlisted role.
    unsafe_financial = [c for c in unique_claims if c.financial is not None and c.financial.semantic_role not in _AUTO_SAFE_FINANCIAL_ROLES]
    if unsafe_financial:
        roles = sorted({c.financial.semantic_role for c in unsafe_financial})
        review_reasons.append(f"financial semantic role(s) not on the auto-safe allowlist: {', '.join(roles)}")
        blocking_reasons.append(f"unsafe_financial_role:{','.join(roles)}")
    elif any(c.financial is not None for c in unique_claims):
        auto_eligibility_reasons.append("every financial claim present carries an auto-safe semantic role")

    # Rule: every relationship claim present must carry an allowlisted, award-shaped role.
    unsafe_relationship = [c for c in unique_claims if c.relationship is not None and c.relationship.role not in _AUTO_SAFE_RELATIONSHIP_ROLES]
    if unsafe_relationship:
        roles = sorted({c.relationship.role for c in unsafe_relationship})
        review_reasons.append(f"relationship role(s) not an explicit award/vendor-confirmation role: {', '.join(roles)}")
        blocking_reasons.append(f"unsafe_relationship_role:{','.join(roles)}")
    elif any(c.relationship is not None for c in unique_claims):
        auto_eligibility_reasons.append("every relationship claim present carries an explicit award/vendor-confirmation role")

    # Rule: source authority must be explicit and Tier 1 - fails closed.
    if context.source_authority_tier == SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL:
        auto_eligibility_reasons.append("source authority is explicit Tier 1 (primary official)")
    else:
        tier_label = context.source_authority_tier.value if context.source_authority_tier else "unknown"
        review_reasons.append(f"source authority is not confirmed Tier 1 (primary official): {tier_label}")
        blocking_reasons.append(f"source_authority_tier:{tier_label}")

    # Rule: corroboration, only where the caller explicitly says it's required.
    if context.requires_corroboration and context.corroborating_source_count < 2:
        review_reasons.append(
            f"this claim shape requires independent corroboration but only "
            f"{context.corroborating_source_count} corroborating source(s) were established"
        )
        blocking_reasons.append("insufficient_corroboration")
    else:
        auto_eligibility_reasons.append("corroboration requirement satisfied (single authoritative source sufficient, or independently corroborated)")

    if blocking_reasons:
        reason = (
            "HUMAN_REVIEW_REQUIRED: material evidence is supported (SignalCandidate REVIEW_REQUIRED), "
            "but automatic eligibility is blocked by: " + "; ".join(review_reasons) + ". "
            "This evidence is not discarded - human review determines whether it should become a Signal."
        )
        return PromotionPolicyDecision(
            outcome=PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED,
            reason=reason,
            auto_eligibility_reasons=tuple(auto_eligibility_reasons),
            review_reasons=tuple(review_reasons),
            blocking_reasons=tuple(blocking_reasons),
        )

    reason = (
        "AUTO_ELIGIBLE: this evidence satisfies deterministic structural conditions that could "
        "eventually permit automation under a separately-authorized policy - " + "; ".join(auto_eligibility_reasons) + ". "
        "This is an eligibility classification only; it does not create, update, or publish any Signal."
    )
    return PromotionPolicyDecision(
        outcome=PromotionPolicyOutcome.AUTO_ELIGIBLE,
        reason=reason,
        auto_eligibility_reasons=tuple(auto_eligibility_reasons),
    )
