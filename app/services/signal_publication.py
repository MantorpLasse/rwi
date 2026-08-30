"""Governed Signal publication ("RWI - Signal Publication Governance - Design
+ Implementation" mission).

    Signal (published=False, created via
        app.services.governed_signal_creation.create_signal_from_approved_review())
        + every linked SourceAssertion (via supporting_source_assertions)
          already governed: EFFECTIVE identity == ATTACH_CONFIRMED,
          intelligence_review_decision == REVIEW_REQUIRED,
          promotion_policy_decision == HUMAN_REVIEW_REQUIRED,
          latest ReviewerAction == APPROVE_SIGNAL or CONFIRM_DISTINCT_SIGNAL
        + explicit human reviewer + reason
        -> publish_signal()
        -> one immutable SignalPublicationAction(PUBLISH) row appended
           AND Signal.published: False -> True, in the same transaction
        -> STOP (this is the terminal step of the governed pipeline; no
           further automatic action)

    Signal (published=True)
        + explicit human reviewer + reason
        -> unpublish_signal()
        -> one immutable SignalPublicationAction(UNPUBLISH) row appended
           AND Signal.published: True -> False, in the same transaction

THIS MODULE NEVER PUBLISHES ANYTHING ITSELF: it is imported and exercised
throughout this mission's own tests, and used read-only (evaluate_publication_
eligibility only) against the real Signal #69, but this mission's own
instruction is that Signal #69 must remain published=False at the end - no
call site in this repository, as of this mission, calls publish_signal()
against real data.

CURRENT-STATE STRATEGY (Phase 3): `Signal.published` remains the single
denormalized, current-state boolean the static exporter already reads
(`app.static_export.build._is_public_signal()`, unchanged by this mission -
still just `return signal.published`). `SignalPublicationAction` is a pure
audit log; it is never read by the exporter and never re-derived into
`Signal.published` by any reconciliation job. `publish_signal()`/
`unpublish_signal()` update both the audit row and the flag atomically, in
the same uncommitted transaction, so they can never drift apart under this
module's own writes. (A future integrity check could assert the two stay in
sync, mirroring `test_public_signal_id_set_identical_before_and_after_migration`'s
own reasoning in the Slice 9A report - not built here, out of this mission's
narrow scope.)

GOVERNED-vs-LEGACY BOUNDARY (Phase 4): `evaluate_publication_eligibility()`
requires the Signal to have at least one linked SourceAssertion (a row whose
`SourceAssertion.signal_id` points back at it - the exact relationship
`app.services.governed_signal_creation.create_signal_from_approved_review()`
establishes, and the only current source of that link in this repository).
A Signal with zero linked SourceAssertions - every one of the six pre-
existing legacy writer paths (`app.services.signal_rules` and five
`scripts/*.py` importers), all of which predate and remain outside the
governed evidence pipeline - is refused here, not because it may never be
published, but because THIS mechanism only knows how to evaluate governed
evidence; a legacy Signal's own historical `published` value (almost always
already `True` from the Slice 9A backfill default) is left completely alone
by this module, which never reads or writes any Signal this function refuses.

RAW-vs-EFFECTIVE IDENTITY: exactly like every other governance gate in this
pipeline (`app.services.reviewer_action_persistence`,
`app.services.governed_signal_creation`), the identity check here is the
EFFECTIVE decision -
`app.services.effective_identity_guard_decision.resolve_effective_identity_guard_decision()`
(EB5) - never the raw `identity_guard_decision` column directly.

SOURCE PROVENANCE (Phase 5): `publish_signal()`/`unpublish_signal()` never
read, write, or otherwise touch `Signal.source_id` or `Source.url` - the
original-source link a public Signal Detail page renders
(`app/static_export/templates/signal_detail.html`'s own "Källa" card,
untouched by the "RWI - Sacheon Evidence Surfacing" mission for the same
reason) cannot be broken or dropped by this module by construction, since
this module has no code path that assigns to either field.
`evaluate_publication_eligibility()` still performs one narrow,
defense-in-depth, read-only check - if `signal.source_id` is set, the
referenced `Source` row must still exist - mirroring the "checked explicitly
rather than assumed" discipline `app.services.reviewer_action_persistence`'s
own module docstring already documents for a structurally analogous case.
This is a referential-integrity check, not a content rewrite, and it never
fabricates a URL or requires one to exist when a Source legitimately has
none (`Source.url` is nullable - see app/models/source.py).

PUBLISH ELIGIBILITY IS FAIL-CLOSED (Phase 4/12): every blocker
`evaluate_publication_eligibility()` finds is collected and returned (never
short-circuited on the first one) so a human reviewer sees the full picture
at once; `publish_signal()` raises `ValueError` listing every blocker if
`eligible` is False, and appends no audit row and touches no Signal field in
that case.

UNPUBLISH HAS NO FORWARD-GOVERNANCE GATE, DELIBERATELY (Phase 9): revoking
an already-public Signal is a strictly safety-DECREASING action (fewer
people can see less), the mirror-opposite of the safety-INCREASING PUBLISH
gate - so `unpublish_signal()` only requires a non-empty human reviewer and
reason, never re-validates identity/intelligence-review/promotion-
policy/ReviewerAction state. This deliberately allows unpublishing a Signal
whose underlying governance would no longer independently qualify it for
(re-)publication (e.g. a later-discovered identity problem) without first
requiring that problem to be resolved - exactly the scenario Phase 9 lists
("bad source", "Signal mistakenly published") as motivating this
mechanism's existence at all. Re-publishing after an UNPUBLISH still goes
through the full `publish_signal()` gate again, unchanged.

IDEMPOTENCY (Phase 8): `publish_signal()` on an already-published Signal, or
`unpublish_signal()` on an already-unpublished one, returns immediately with
`changed=False` and the current latest `SignalPublicationAction` (possibly
`None`, if the Signal has never had one recorded, e.g. a legacy Signal whose
`published=True` came only from the Slice 9A model default) - no new audit
row is appended and the eligibility gate is not re-run (avoiding needless
duplicate-history rows and needless re-evaluation work on a true no-op,
per Phase 8's own stated preference). `reviewer`/`reason` are still
validated as non-empty even on this no-op path, so a caller's malformed
request is never silently swallowed.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only so a constraint violation
(including the immutability event listeners on SignalPublicationAction)
surfaces immediately; the caller owns the transaction boundary entirely,
matching every other persistence service in this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Signal, Source, SourceAssertion
from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE
from app.models.signal_publication_action import PUBLICATION_ACTIONS, SignalPublicationAction
from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.reviewer_action_persistence import get_latest_reviewer_action

__all__ = [
    "REQUIRED_IDENTITY_DECISION_FOR_PUBLISH",
    "REQUIRED_INTELLIGENCE_DECISION_FOR_PUBLISH",
    "REQUIRED_PROMOTION_DECISION_FOR_PUBLISH",
    "VALID_LATEST_REVIEWER_ACTIONS_FOR_PUBLISH",
    "PublicationEligibilityDecision",
    "SignalPublicationResult",
    "evaluate_publication_eligibility",
    "get_latest_signal_publication_action",
    "record_signal_publication_action",
    "publish_signal",
    "unpublish_signal",
]

REQUIRED_IDENTITY_DECISION_FOR_PUBLISH = "ATTACH_CONFIRMED"
REQUIRED_INTELLIGENCE_DECISION_FOR_PUBLISH = "REVIEW_REQUIRED"
REQUIRED_PROMOTION_DECISION_FOR_PUBLISH = "HUMAN_REVIEW_REQUIRED"
VALID_LATEST_REVIEWER_ACTIONS_FOR_PUBLISH = ("APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL")


@dataclass(frozen=True)
class PublicationEligibilityDecision:
    """Pure, read-only result of `evaluate_publication_eligibility()`. Never
    persisted anywhere - explainability metadata only, mirroring
    `app.services.governed_signal_creation.GovernedSignalCreationResult.
    reconciliation_decision`'s own "result-only" discipline."""

    signal_id: int
    eligible: bool
    blockers: "tuple[str, ...]"
    checked_source_assertion_ids: "tuple[int, ...]"


@dataclass(frozen=True)
class SignalPublicationResult:
    """`changed=True` only when this call itself flipped `Signal.published`
    and appended a new `SignalPublicationAction` row; `False` on an
    idempotent no-op repeat (see module docstring "IDEMPOTENCY"). `action`
    is the relevant `SignalPublicationAction` - the newly appended row when
    `changed=True`, or the pre-existing latest row (possibly `None`) on a
    no-op. `eligibility` is populated only by a `publish_signal()` call that
    actually ran the gate (i.e. `changed=True`); `None` otherwise."""

    signal: Signal
    changed: bool
    action: Optional[SignalPublicationAction]
    eligibility: Optional[PublicationEligibilityDecision] = None


def evaluate_publication_eligibility(session: Session, signal: Signal) -> PublicationEligibilityDecision:
    """Fail-closed, read-only, side-effect-free evaluation of every PUBLISH
    gate (Phase 4/12). Collects every blocker rather than short-circuiting on
    the first one. Safe to call repeatedly and safe to call against a Signal
    that is already published (used that way by `Phase 16`'s own read-only
    eligibility check) - never mutates `session`, `signal`, or any related
    row."""
    blockers: "list[str]" = []

    assertions = (
        session.query(SourceAssertion)
        .filter(SourceAssertion.signal_id == signal.id)
        .order_by(SourceAssertion.id)
        .all()
    )
    if not assertions:
        blockers.append(
            "signal has no linked SourceAssertion (no SourceAssertion.signal_id points at this "
            "Signal) - this service governs publication only for Signals created through the "
            "governed discovery/intelligence pipeline "
            "(app.services.governed_signal_creation.create_signal_from_approved_review()); a "
            "legacy Signal with no governed evidence link is out of scope for this mechanism"
        )

    for source_assertion in assertions:
        prefix = f"SourceAssertion #{source_assertion.id}: "
        if source_assertion.signal_id != signal.id:
            # Unreachable given the query filter above; kept as an explicit,
            # cheap, documented invariant re-check rather than assumed - the
            # same "checked explicitly rather than assumed" discipline this
            # module's own docstring already cites.
            blockers.append(prefix + "signal_id does not point back at this Signal")
            continue

        # See module docstring "RAW-vs-EFFECTIVE IDENTITY" - the EFFECTIVE
        # decision (EB5), never the raw identity_guard_decision column.
        effective = resolve_effective_identity_guard_decision(
            session, source_assertion_id=source_assertion.id
        )
        if effective.effective_decision != AttachmentOutcome(REQUIRED_IDENTITY_DECISION_FOR_PUBLISH):
            blockers.append(
                prefix + "effective identity decision "
                f"(resolve_effective_identity_guard_decision()) is "
                f"{effective.effective_decision.value!r}, required "
                f"{REQUIRED_IDENTITY_DECISION_FOR_PUBLISH!r} (basis={effective.basis.value!r})"
            )
        if source_assertion.intelligence_review_decision != REQUIRED_INTELLIGENCE_DECISION_FOR_PUBLISH:
            blockers.append(
                prefix + f"intelligence_review_decision is "
                f"{source_assertion.intelligence_review_decision!r}, required "
                f"{REQUIRED_INTELLIGENCE_DECISION_FOR_PUBLISH!r}"
            )
        if source_assertion.promotion_policy_decision != REQUIRED_PROMOTION_DECISION_FOR_PUBLISH:
            blockers.append(
                prefix + f"promotion_policy_decision is "
                f"{source_assertion.promotion_policy_decision!r}, required "
                f"{REQUIRED_PROMOTION_DECISION_FOR_PUBLISH!r}"
            )
        latest_reviewer_action = get_latest_reviewer_action(session, source_assertion.id)
        if (
            latest_reviewer_action is None
            or latest_reviewer_action.action not in VALID_LATEST_REVIEWER_ACTIONS_FOR_PUBLISH
        ):
            blockers.append(
                prefix + "latest ReviewerAction is "
                f"{(latest_reviewer_action.action if latest_reviewer_action else None)!r}, "
                f"required one of {VALID_LATEST_REVIEWER_ACTIONS_FOR_PUBLISH!r}"
            )

    if not signal.title or not signal.title.strip():
        blockers.append("signal.title is empty")
    if not signal.category or not signal.category.strip():
        blockers.append("signal.category is empty")
    if signal.confidence not in DEFAULT_SCORE_BY_CONFIDENCE:
        blockers.append(
            f"signal.confidence is {signal.confidence!r}, must be one of "
            f"{sorted(DEFAULT_SCORE_BY_CONFIDENCE)!r}"
        )
    if not signal.status or not signal.status.strip():
        blockers.append("signal.status is empty")

    # See module docstring "SOURCE PROVENANCE" - referential-integrity check
    # only, never a URL-existence requirement.
    if signal.source_id is not None and session.get(Source, signal.source_id) is None:
        blockers.append(f"signal.source_id={signal.source_id!r} does not reference an existing Source")

    return PublicationEligibilityDecision(
        signal_id=signal.id,
        eligible=not blockers,
        blockers=tuple(blockers),
        checked_source_assertion_ids=tuple(sa.id for sa in assertions),
    )


def get_latest_signal_publication_action(
    session: Session, signal_id: int
) -> Optional[SignalPublicationAction]:
    """The effective current publication-audit row for a Signal: the most
    recently recorded SignalPublicationAction, ordered by created_at then id
    - mirrors `app.services.reviewer_action_persistence.
    get_latest_reviewer_action()` exactly, including its own "recency alone,
    never chain-walking" reasoning. Returns None if no publication action has
    ever been recorded for this Signal (true for every legacy Signal, and
    for a governed Signal that has never been published or unpublished)."""
    actions = (
        session.query(SignalPublicationAction)
        .filter(SignalPublicationAction.signal_id == signal_id)
        .order_by(SignalPublicationAction.created_at.desc(), SignalPublicationAction.id.desc())
        .limit(1)
        .all()
    )
    return actions[0] if actions else None


def record_signal_publication_action(
    session: Session,
    signal: Signal,
    *,
    action: str,
    reviewer: str,
    reason: str,
    supersedes_action_id: Optional[int] = None,
) -> SignalPublicationAction:
    """Validates and appends exactly one SignalPublicationAction row. Never
    commits; calls session.flush() only so a constraint violation surfaces
    immediately. Never mutates `signal.published` itself - that is
    `publish_signal()`/`unpublish_signal()`'s own responsibility, done in the
    same transaction immediately after this call succeeds, so the two never
    observably diverge from any external caller's perspective. Exposed as
    its own function (rather than inlined) purely to mirror
    `record_reviewer_action()`'s own separation of "validate and append the
    audit row" from the higher-level governed operation - not intended to be
    called directly by ordinary callers, who should prefer `publish_signal()`
    / `unpublish_signal()`."""
    if action not in PUBLICATION_ACTIONS:
        raise ValueError(f"action must be one of {PUBLICATION_ACTIONS!r}, got {action!r}")
    if not reason.strip():
        raise ValueError("reason is required for a signal publication action")
    if not reviewer.strip():
        raise ValueError("reviewer is required for a signal publication action")
    if signal.id is None:
        raise ValueError("signal must already be persisted (has no id)")
    if session.get(Signal, signal.id) is None:
        raise ValueError("referenced Signal does not exist")

    if supersedes_action_id is not None:
        previous = session.get(SignalPublicationAction, supersedes_action_id)
        if previous is None or previous.signal_id != signal.id:
            raise ValueError("superseded action must exist and concern the same Signal")

    record = SignalPublicationAction(
        signal_id=signal.id,
        action=action,
        reason=reason.strip(),
        reviewer=reviewer.strip(),
        supersedes_action_id=supersedes_action_id,
    )
    session.add(record)
    session.flush()
    return record


def publish_signal(
    session: Session, signal: Signal, *, reviewer: str, reason: str
) -> SignalPublicationResult:
    """The governed PUBLISH operation (Phase 6). Runs every eligibility gate
    fresh (never trusts a previously computed decision); on success, appends
    one SignalPublicationAction(PUBLISH) row and sets `signal.published =
    True`, atomically, in the same uncommitted transaction. Raises
    `ValueError` (listing every blocker) if any gate fails - no audit row is
    appended and `signal.published` is left untouched in that case. See
    module docstring "IDEMPOTENCY" for the already-published no-op case, and
    "PUBLISH ELIGIBILITY IS FAIL-CLOSED" for the gate itself."""
    if not reviewer.strip():
        raise ValueError("reviewer is required to publish a Signal")
    if not reason.strip():
        raise ValueError("reason is required to publish a Signal")

    latest = get_latest_signal_publication_action(session, signal.id)
    if signal.published:
        return SignalPublicationResult(signal=signal, changed=False, action=latest)

    eligibility = evaluate_publication_eligibility(session, signal)
    if not eligibility.eligible:
        raise ValueError(
            "publish_signal requires every publication eligibility gate to pass - blockers: "
            + "; ".join(eligibility.blockers)
        )

    record = record_signal_publication_action(
        session, signal, action="PUBLISH", reviewer=reviewer, reason=reason,
        supersedes_action_id=latest.id if latest is not None else None,
    )
    signal.published = True
    session.flush()
    return SignalPublicationResult(signal=signal, changed=True, action=record, eligibility=eligibility)


def unpublish_signal(
    session: Session, signal: Signal, *, reviewer: str, reason: str
) -> SignalPublicationResult:
    """The governed UNPUBLISH operation (Phase 9). See module docstring
    "UNPUBLISH HAS NO FORWARD-GOVERNANCE GATE, DELIBERATELY" for why this
    requires only a non-empty human reviewer/reason, and "IDEMPOTENCY" for
    the already-unpublished no-op case. Appends one
    SignalPublicationAction(UNPUBLISH) row and sets `signal.published =
    False`, atomically, in the same uncommitted transaction. Never deletes
    or mutates any prior SignalPublicationAction row."""
    if not reviewer.strip():
        raise ValueError("reviewer is required to unpublish a Signal")
    if not reason.strip():
        raise ValueError("reason is required to unpublish a Signal")

    latest = get_latest_signal_publication_action(session, signal.id)
    if not signal.published:
        return SignalPublicationResult(signal=signal, changed=False, action=latest)

    record = record_signal_publication_action(
        session, signal, action="UNPUBLISH", reviewer=reviewer, reason=reason,
        supersedes_action_id=latest.id if latest is not None else None,
    )
    signal.published = False
    session.flush()
    return SignalPublicationResult(signal=signal, changed=True, action=record)
