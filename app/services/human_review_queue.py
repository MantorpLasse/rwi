"""Human review queue — read-only governed-evidence query
(docs/architecture/human-review-queue-slice8-report.md, Slice 8 of
docs/architecture/evidence-to-signal-semantics-design.md).

    SourceAssertion (already governed: identity + intelligence review +
    promotion policy, all already persisted by Slices 1/4/7)
        -> list_human_review_items()
        -> tuple[HumanReviewItem, ...]
        -> STOP (no reviewer actions, no Signal write - a future,
           separately-authorized slice)

Answers exactly one question: "which already-governed SourceAssertion rows
need a human's judgment before anything further happens, and what does a
reviewer need to see to make that judgment?" It never asks "create/update a
Signal," never performs a reviewer action, and never writes anything -
`session.scalars()`/`session.execute()` (SELECT only) are the only session
calls this module makes. No `session.add()`, no `session.flush()`, no
`session.commit()` anywhere.

GENERIC, NOT SOURCE-SPECIFIC: this module has no knowledge of MAC/Granicus
or any other source family - it reads only fields every governed
SourceAssertion row already carries (identity/intelligence/promotion
decision-and-reason pairs, raw evidence, Source/Airport metadata). It does
NOT import app.acquisition.mac_granicus_claims or any other source-specific
extractor - claim re-derivation, where available, is a deliberately separate
concern (app.services.human_review_claim_enrichment), composed by the CLI,
never required by this module to function.

QUEUE FILTER: `SourceAssertion.promotion_policy_decision ==
PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED.value` exactly. No fuzzy
interpretation - AUTO_ELIGIBLE, DO_NOT_PROMOTE, NULL, and any unrecognized
value are all excluded identically. NULL is never treated as "needs review"
- an unevaluated row is not a queue item, it is simply not yet evaluated
(design doc's own "history never deleted, never silently promoted" posture).

INVARIANT WARNINGS, NEVER SILENT NORMALIZATION: promotion policy is only
ever computed for evidence whose identity is ATTACH_CONFIRMED and whose
intelligence review is REVIEW_REQUIRED (app.services.promotion_policy_evaluation's
own outcome mapping) - so a HUMAN_REVIEW_REQUIRED row should always carry
`identity_guard_decision == "ATTACH_CONFIRMED"` and `intelligence_review_decision
== "REVIEW_REQUIRED"`. If a real row is ever found violating this (e.g. from
data corruption, or the two persistence functions having been called at
different times against evidence that changed in between), this module
surfaces it as an explicit `HumanReviewItem.invariant_warnings` entry -
never corrects, never hides, never modifies the row.

SOURCE AUTHORITY: `HumanReviewItem.source_reliability_level_raw` carries
`Source.reliability_level` verbatim, explicitly named and documented as the
existing coarse field - never relabeled as, or treated as equivalent to,
`app.services.promotion_policy_evaluation.SourceAuthorityTier` (design doc
S12/S15's own already-documented gap; this module does not attempt to close
it, only to avoid conflating the two).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import SourceAssertion
from app.services.promotion_policy_evaluation import PromotionPolicyOutcome
from app.services.signal_candidate_evaluation import SignalCandidateOutcome

__all__ = ["HumanReviewItem", "list_human_review_items"]

_EXPECTED_IDENTITY_DECISION = "ATTACH_CONFIRMED"
_EXPECTED_INTELLIGENCE_DECISION = SignalCandidateOutcome.REVIEW_REQUIRED.value
_QUEUE_DECISION = PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED.value


@dataclass(frozen=True)
class HumanReviewItem:
    """One queue entry - an immutable, ORM-free snapshot of one governed
    SourceAssertion row's already-persisted fields. Never a live ORM
    instance (safe to hold/compare/serialize after the session closes)."""

    source_assertion_id: int
    airport_id: "int | None"
    airport_name: "str | None"
    airport_code: "str | None"

    source_id: int
    source_title: "str | None"
    source_publisher: "str | None"
    source_url: "str | None"
    source_document_reference: "str | None"
    source_reliability_level_raw: "str | None"

    artifact_identity: "str | None"
    source_locator: "str | None"
    raw_fragment_hash: "str | None"
    raw_relevant_text: "str | None"
    parser_identifier: "str | None"

    identity_guard_decision: "str | None"
    identity_guard_reason: "str | None"
    intelligence_review_decision: "str | None"
    intelligence_review_reason: "str | None"
    promotion_policy_decision: str
    promotion_policy_reason: str

    invariant_warnings: "tuple[str, ...]" = ()


def _airport_code(airport) -> "str | None":
    if airport is None:
        return None
    return airport.iata_code or airport.icao_code or airport.faa_code


def _invariant_warnings(assertion: SourceAssertion) -> "tuple[str, ...]":
    warnings: "list[str]" = []
    if assertion.identity_guard_decision != _EXPECTED_IDENTITY_DECISION:
        warnings.append(
            f"INVARIANT_VIOLATION: promotion_policy_decision={_QUEUE_DECISION!r} but "
            f"identity_guard_decision={assertion.identity_guard_decision!r} (expected "
            f"{_EXPECTED_IDENTITY_DECISION!r}) - identity/intelligence/promotion review may "
            f"have been evaluated at different times against different evidence."
        )
    if assertion.intelligence_review_decision != _EXPECTED_INTELLIGENCE_DECISION:
        warnings.append(
            f"INVARIANT_VIOLATION: promotion_policy_decision={_QUEUE_DECISION!r} but "
            f"intelligence_review_decision={assertion.intelligence_review_decision!r} (expected "
            f"{_EXPECTED_INTELLIGENCE_DECISION!r})."
        )
    return tuple(warnings)


def _to_item(assertion: SourceAssertion) -> HumanReviewItem:
    airport = assertion.airport
    source = assertion.source
    return HumanReviewItem(
        source_assertion_id=assertion.id,
        airport_id=assertion.airport_id,
        airport_name=airport.name if airport else None,
        airport_code=_airport_code(airport),
        source_id=assertion.source_id,
        source_title=source.title if source else None,
        source_publisher=source.publisher if source else None,
        source_url=source.url if source else None,
        source_document_reference=source.document_reference if source else None,
        source_reliability_level_raw=source.reliability_level if source else None,
        artifact_identity=assertion.artifact_identity,
        source_locator=assertion.source_locator,
        raw_fragment_hash=assertion.raw_fragment_hash,
        raw_relevant_text=assertion.raw_relevant_text,
        parser_identifier=assertion.parser_identifier,
        identity_guard_decision=assertion.identity_guard_decision,
        identity_guard_reason=assertion.identity_guard_reason,
        intelligence_review_decision=assertion.intelligence_review_decision,
        intelligence_review_reason=assertion.intelligence_review_reason,
        promotion_policy_decision=assertion.promotion_policy_decision,
        promotion_policy_reason=assertion.promotion_policy_reason,
        invariant_warnings=_invariant_warnings(assertion),
    )


def list_human_review_items(session: Session, *, limit: "int | None" = None) -> "tuple[HumanReviewItem, ...]":
    """Read-only: SELECT only, never add/flush/commit. Ordered by
    `created_at` descending (newest governed evidence first - `created_at`
    is the one always-populated, non-nullable timestamp every SourceAssertion
    carries), with `id` descending as a deterministic tiebreaker for rows
    sharing the same timestamp - never relies on unordered/implicit DB
    order. `limit`, if given, bounds the result directly in SQL (never
    fetches the full table and slices in Python)."""
    stmt = (
        select(SourceAssertion)
        .where(SourceAssertion.promotion_policy_decision == _QUEUE_DECISION)
        .options(selectinload(SourceAssertion.airport), selectinload(SourceAssertion.source))
        .order_by(SourceAssertion.created_at.desc(), SourceAssertion.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = session.scalars(stmt).all()
    return tuple(_to_item(row) for row in rows)
