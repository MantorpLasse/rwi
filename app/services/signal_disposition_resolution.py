"""Read-only resolution of persisted Signal-group dispositions
(D4D3 of docs/architecture/fh-d4-signal-disposition-design.md §13).

    tuple of Signal ids (an FH-D4 candidate group, or any other caller-
        supplied group of two or more Signals)
        -> resolve_fh_d4_group_status() / resolve_fh_d4_group_statuses()
        -> SignalDispositionStatus (UNREVIEWED / CONFIRMED_DISTINCT /
           CONFIRMED_SAME_REAL_WORLD_EFFORT)
        -> STOP (no write, no Fleet Health integration, no CLI - this
           module answers exactly the question the design doc's own §13
           names: "what is the current disposition status of this exact
           Signal set?" - nothing about presentation, suppression, or
           publication)

Pairs with `app.services.signal_disposition_persistence` (D4D1) - the write
side - as this domain's read side. Deliberately NOT named
`fh_d4_disposition_resolution.py` (the design doc's own §14 sketch for a
*future*, separate, Fleet-Health-specific adapter that will eventually call
this module): this module itself knows nothing about FH-D4, Fleet Health,
or any specific caller - it is a generic, domain-level read service over
`signal_dispositions`/`signal_disposition_members`, exactly as narrow and
caller-agnostic as `app.services.reviewer_action_persistence.
get_latest_reviewer_action()` is for `ReviewerAction`.

EXACT-SET IDENTITY (design doc §8): a disposition's identity is the exact,
deduplicated SET of its member Signal ids - `{41, 67}` and `{41, 67, 80}`
are two entirely different identities, never a subset/superset
relationship the "current status" question treats as related. No
fingerprint, no hash - plain `frozenset(int)` equality.

LATEST-ORDER SEMANTICS (design doc §13, mirroring
`get_latest_reviewer_action()`'s own already-reviewed precedent exactly):
"latest" for one exact member set means most-recently-recorded
(`created_at` DESC, `id` DESC as the deterministic tie-break) - never a
`supersedes_id` chain walk. D4D1's own `record_signal_group_disposition()`
does not require every later disposition for the same exact set to
supply `supersedes_id` at all (only a genuine *supersession* requires an
exact-set match with its own target, per D4D1 §8/§12) - so two
independently-recorded dispositions can legitimately exist for the same
exact set without one explicitly referencing the other. This module
resolves that the same way `get_latest_reviewer_action()` already resolves
the analogous case for `ReviewerAction`: recency alone identifies current
state, unconditionally, regardless of `supersedes_id` linkage. This is a
deliberate design decision, not an oversight - see this module's own
critical-review documentation for the analysis that confirmed it matches
established precedent rather than inventing a new governance rule.

RELATED/STALE HISTORICAL CONTEXT (design doc §21's first and third
"unresolved questions", resolved by this mission's own explicit scope):
`find_related_historical_dispositions()` is a second, narrow, ADVISORY-ONLY
function - it never affects `resolve_fh_d4_group_status()`'s own three-
state answer. "Related" is defined narrowly as a STRICT subset or superset
relationship only (never bare set intersection) - a historical disposition
for `{A, B}` is related to a current query for `{A, B, C}` (subset) or
`{A, B, C, D}` shrinking to `{A, B, C}` is related to the current `{A, B}`
(superset), but a historical `{A, B}` is NOT "related" to a current query
for `{A, C}` merely because both happen to include Signal A - two sets
sharing exactly one element out of otherwise-disjoint membership are much
more likely to be two genuinely unrelated historical cases than a grown/
shrunk version of the same reviewed group, and treating every such overlap
as "related" would flood a future presentation layer with noise for no
real benefit (see this module's own critical-review documentation for the
full reasoning).

READ-ONLY, ALWAYS: every function here is wrapped in `session.no_autoflush`
end-to-end (validation included) - the exact same review-checkpoint fix
`app.services.fleet_health_check`'s own `build_fleet_hard_invariant_snapshot()`/
`build_fleet_review_snapshot()` already established, applied here for the
identical reason: a caller's own pending, uncommitted changes on the
session must never be silently flushed as a side effect of calling a
"read-only" resolution function. Nothing here ever calls `session.add()`,
`.flush()`, `.commit()`, or `.delete()`.

INFORMATION FIREWALL: this module reads exactly six values -
`SignalDisposition.id`/`.decision`/`.reviewer`/`.reason`/`.created_at` and
`SignalDispositionMember.signal_id`/`.disposition_id` - plus `Signal.id`
(existence-check only, via `session.get(Signal, id)`, which touches no
other Signal column). It never reads `Signal.title`, `.notes`,
`.source_notes`, any financial field, `.category`, `.confidence`,
`.status`, `.supplier`/`.likely_supplier`/`.confirmed_vendor`,
`.published`, `.airport_id`, or any other Signal column - resolution
answers only "what disposition (if any) exists for this exact id set,"
never anything about what those ids' own Signal rows contain. Verified
structurally (AST) and behaviorally in
`tests/test_signal_disposition_resolution.py`.

FAIL LOUD: a missing `signal_dispositions`/`signal_disposition_members`
table, or any other genuine query failure, propagates as an ordinary
uncaught `sqlalchemy.exc.OperationalError` (or whatever the real failure
is) - never silently converted into `UNREVIEWED`, an empty tuple, or any
other "looks like a normal, healthy answer" result. This module has no
try/except anywhere in its own code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models import Signal
from app.models.signal_disposition import SignalDisposition, SignalDispositionMember

__all__ = [
    "UNREVIEWED",
    "CONFIRMED_DISTINCT",
    "CONFIRMED_SAME_REAL_WORLD_EFFORT",
    "SignalDispositionStatus",
    "RelatedHistoricalDisposition",
    "get_latest_disposition_for_member_set",
    "resolve_fh_d4_group_status",
    "resolve_fh_d4_group_statuses",
    "find_related_historical_dispositions",
]

# The smallest designed status contract (design doc §13, this mission's own
# §4) - exactly three values, no persisted STALE state. "Stale" is derived,
# advisory context (find_related_historical_dispositions()), never a value
# this vocabulary itself carries.
UNREVIEWED = "UNREVIEWED"
CONFIRMED_DISTINCT = "CONFIRMED_DISTINCT"
CONFIRMED_SAME_REAL_WORLD_EFFORT = "CONFIRMED_SAME_REAL_WORLD_EFFORT"

_DECISION_TO_STATUS = {
    "DISTINCT": CONFIRMED_DISTINCT,
    "SAME_REAL_WORLD_EFFORT": CONFIRMED_SAME_REAL_WORLD_EFFORT,
}

_MINIMUM_GROUP_CARDINALITY = 2


@dataclass(frozen=True)
class SignalDispositionStatus:
    """The one result shape `resolve_fh_d4_group_status()`/
    `resolve_fh_d4_group_statuses()` return - narrow, JSON-serializable-
    shaped fields only, never an ORM object (design doc's own D4D3 mission
    §12: "the D4D4 integration should not need lazy ORM traversal").

    `signal_ids` is always the deduplicated, ascending-sorted
    representation of whatever the caller supplied - the canonical form
    every other field is computed against. `latest_disposition_id`/
    `decision`/`reviewer`/`reason`/`created_at` are all `None` together
    when `status == UNREVIEWED`; populated together otherwise.

    `independent_root_count` (review-checkpoint addition): the number of
    exact-set-matching dispositions whose own `supersedes_id IS NULL` -
    normally exactly 1 (one initial disposition, with every later
    correction for the same exact set properly chained via
    `supersedes_id`) or 0 (`UNREVIEWED`). D4D1's own
    `record_signal_group_disposition()` does not require a later
    disposition for an already-dispositioned exact set to link to the
    prior one at all - two independent reviewers could each record their
    own, unlinked conclusion for the same group without either ever
    seeing the other's. "Latest wins" resolution (this module's own
    documented, precedent-matched policy - see the module docstring's own
    "LATEST-ORDER SEMANTICS" section) still determines `status`
    deterministically even when this happens, but silently picking a
    winner with no visibility into the fact that a genuine disagreement
    exists would leave a real operational blind spot. A value greater than
    1 here is exactly that signal - proof that more than one independently
    -recorded root disposition exists for this exact set, worth a human's
    attention regardless of which one "won" by recency. This field never
    changes what `status`/`decision`/`latest_disposition_id` resolve to -
    it is additional visibility, not a different resolution algorithm; no
    chain-walking is performed to compute it (it is a plain count over the
    already-fetched exact-set-matching dispositions' own `supersedes_id`
    field).
    """

    status: str
    signal_ids: "tuple[int, ...]"
    latest_disposition_id: "Optional[int]" = None
    decision: "Optional[str]" = None
    reviewer: "Optional[str]" = None
    reason: "Optional[str]" = None
    created_at: "Optional[datetime]" = None
    independent_root_count: int = 0


@dataclass(frozen=True)
class RelatedHistoricalDisposition:
    """One advisory, non-current historical disposition whose member set is
    a strict subset or superset of a queried group - never returned for an
    exact match (that is `resolve_fh_d4_group_status()`'s own "current"
    answer) and never for a merely-overlapping, non-subset/superset set.

    `relation` is `"SUBSET"` (this historical disposition's own member set
    is a strict subset of the queried set - the group has since grown) or
    `"SUPERSET"` (a strict superset - the group has since shrunk)."""

    disposition_id: int
    member_signal_ids: "tuple[int, ...]"
    decision: str
    reviewer: str
    reason: str
    created_at: datetime
    relation: str


def _normalize(signal_ids: Sequence[int]) -> "tuple[int, ...]":
    """Pure, no DB access: deduplicates/sorts (matching
    `record_signal_group_disposition()`'s own identical normalization
    exactly, so a read query and the write that created the row it might
    match always agree on canonical form) and enforces the same minimum
    group cardinality the write side enforces."""
    deduplicated = tuple(sorted(set(signal_ids)))
    if len(deduplicated) < _MINIMUM_GROUP_CARDINALITY:
        raise ValueError(
            f"signal_ids must contain at least {_MINIMUM_GROUP_CARDINALITY} distinct Signal ids, "
            f"got {deduplicated!r}"
        )
    return deduplicated


def _validate_all_exist(session: Session, signal_ids: "set[int]") -> None:
    """Requires every id in `signal_ids` to reference a real, existing
    Signal - a caller claiming a real current group made of ids that do
    not exist is a caller bug, and returning `UNREVIEWED` for it would
    misleadingly imply "this real group has not been reviewed yet" rather
    than "this group does not even reference real Signals." Mirrors
    `record_signal_group_disposition()`'s own existence-check
    precondition and error shape.

    Review-checkpoint fix: originally checked one id at a time via
    `session.get(Signal, id)` in a loop, called once per GROUP - a real,
    reproduced O(distinct Signal ids) query cost that made the module's
    own "bounded query count" claim false the moment a batch call spanned
    more than a handful of ids (measured directly: 100 two-Signal groups
    emitted 179 SQL statements, not the originally-claimed constant few).
    Fixed by checking the ENTIRE UNION of ids across an entire
    `resolve_fh_d4_group_statuses()` call in exactly one query
    (`SELECT id FROM signals WHERE id IN (...)`), called once per batch
    call - not once per group and not once per id."""
    if not signal_ids:
        return
    existing_ids = {
        row[0] for row in session.query(Signal.id).filter(Signal.id.in_(signal_ids)).all()
    }
    missing = sorted(signal_ids - existing_ids)
    if missing:
        raise ValueError(f"referenced Signal(s) do not exist: {missing!r}")


def _member_sets_and_headers_for_candidates(
    session: Session, all_signal_ids: "set[int]",
) -> "tuple[dict[int, frozenset], dict[int, SignalDisposition]]":
    """Bounded, non-N+1 query strategy for the batch resolver: (1) find
    every disposition_id with at least one member among `all_signal_ids`,
    (2) fetch the COMPLETE member list for exactly those candidate
    dispositions (a disposition with even one member outside
    `all_signal_ids` can never exactly match any queried group anyway, but
    its full member set is still needed to prove that), (3) fetch the
    header rows for those same candidates. Exactly three queries total,
    regardless of how many groups or how many Signals are being resolved
    in one batch call - never one query per group and never one query per
    Signal. (Existence validation is a separate, single additional query -
    see `_validate_all_exist()` - for four total per
    `resolve_fh_d4_group_statuses()` call.)"""
    if not all_signal_ids:
        return {}, {}

    candidate_disposition_ids = {
        row[0]
        for row in session.query(SignalDispositionMember.disposition_id)
        .filter(SignalDispositionMember.signal_id.in_(all_signal_ids))
        .distinct()
        .all()
    }
    if not candidate_disposition_ids:
        return {}, {}

    member_rows = (
        session.query(SignalDispositionMember.disposition_id, SignalDispositionMember.signal_id)
        .filter(SignalDispositionMember.disposition_id.in_(candidate_disposition_ids))
        .all()
    )
    member_sets: "dict[int, set]" = {}
    for disposition_id, signal_id in member_rows:
        member_sets.setdefault(disposition_id, set()).add(signal_id)
    member_sets = {disposition_id: frozenset(members) for disposition_id, members in member_sets.items()}

    headers = (
        session.query(SignalDisposition)
        .filter(SignalDisposition.id.in_(candidate_disposition_ids))
        .all()
    )
    headers_by_id = {header.id: header for header in headers}
    return member_sets, headers_by_id


def resolve_fh_d4_group_statuses(
    session: Session, groups: Sequence[Sequence[int]],
) -> "dict[tuple[int, ...], SignalDispositionStatus]":
    """Batch resolver: one result per input group, keyed by that group's
    own canonical (deduplicated, sorted) tuple form. No cross-group
    contamination - each group's result is computed only from dispositions
    whose member set exactly equals that group's own set.

    Exactly four queries total for the ENTIRE batch call, regardless of
    how many groups or how many distinct Signals are involved - never one
    query per group and never one query per Signal (review-checkpoint fix;
    see `_validate_all_exist()`'s own docstring for the real, reproduced
    scaling defect this closes - the original implementation's existence
    check alone cost one query per distinct Signal id, not a constant
    number).
    """
    with session.no_autoflush:
        normalized_groups = [_normalize(group) for group in groups]
        all_signal_ids = {signal_id for group in normalized_groups for signal_id in group}
        _validate_all_exist(session, all_signal_ids)
        member_sets, headers_by_id = _member_sets_and_headers_for_candidates(session, all_signal_ids)

        results: "dict[tuple[int, ...], SignalDispositionStatus]" = {}
        for group in normalized_groups:
            group_set = frozenset(group)
            matching_ids = [
                disposition_id
                for disposition_id, members in member_sets.items()
                if members == group_set
            ]
            if not matching_ids:
                results[group] = SignalDispositionStatus(status=UNREVIEWED, signal_ids=group)
                continue

            # Latest-order tie-break: created_at DESC, id DESC - the exact
            # tie-break get_latest_reviewer_action() already uses (module
            # docstring's own "LATEST-ORDER SEMANTICS" section).
            matching_headers = [headers_by_id[disposition_id] for disposition_id in matching_ids]
            latest = max(matching_headers, key=lambda h: (h.created_at, h.id))
            # Review-checkpoint addition - see SignalDispositionStatus's own
            # "independent_root_count" docstring: a plain count, no chain-
            # walking, over dispositions already fetched above.
            independent_root_count = sum(1 for header in matching_headers if header.supersedes_id is None)
            results[group] = SignalDispositionStatus(
                status=_DECISION_TO_STATUS[latest.decision],
                signal_ids=group,
                latest_disposition_id=latest.id,
                decision=latest.decision,
                reviewer=latest.reviewer,
                reason=latest.reason,
                created_at=latest.created_at,
                independent_root_count=independent_root_count,
            )
        return results


def resolve_fh_d4_group_status(session: Session, signal_ids: Sequence[int]) -> SignalDispositionStatus:
    """Single-group resolver, implemented as a trivial, provably-equivalent
    special case of `resolve_fh_d4_group_statuses()` (calls it with a
    one-group batch and indexes the result) - single-group and batch
    resolution can never disagree, by construction, not by coincidence."""
    canonical_key = _normalize(signal_ids)
    return resolve_fh_d4_group_statuses(session, [signal_ids])[canonical_key]


def get_latest_disposition_for_member_set(
    session: Session, signal_ids: Sequence[int],
) -> "Optional[SignalDisposition]":
    """Lower-level counterpart to `resolve_fh_d4_group_status()`, returning
    the actual `SignalDisposition` ORM object (or `None`) rather than the
    narrow dataclass - mirrors `get_latest_reviewer_action()`'s own
    established precedent of returning the real ORM row. Prefer
    `resolve_fh_d4_group_status()` for any caller that only needs the
    status/decision/audit fields; this function exists for a caller that
    genuinely needs the ORM object itself (e.g. to read `.members` or to
    use `.id` as a future `supersedes_id`)."""
    status = resolve_fh_d4_group_status(session, signal_ids)
    if status.latest_disposition_id is None:
        return None
    with session.no_autoflush:
        return session.get(SignalDisposition, status.latest_disposition_id)


def find_related_historical_dispositions(
    session: Session, signal_ids: Sequence[int],
) -> "tuple[RelatedHistoricalDisposition, ...]":
    """Advisory-only: dispositions whose member set is a STRICT subset or
    superset of the given (deduplicated) signal_ids - never an exact match
    (that is `resolve_fh_d4_group_status()`'s own "current" answer, not
    "related") and never a merely-overlapping set (module docstring's own
    "RELATED/STALE HISTORICAL CONTEXT" section explains why bare
    intersection was rejected as too noisy). Results are sorted by
    `(created_at, disposition_id)`, oldest first.

    Fetches every `SignalDispositionMember` row once (a full scan, not
    filtered by the queried ids) rather than a more complex SQL subset/
    superset query - a deliberate, documented tradeoff: SQLite has no
    clean built-in for set subset/superset comparison, and the real
    disposition population this table is designed for is small (a few
    dozen rows even if every one of the real 12 FH-D4 groups were
    dispositioned - see the D4D1 report's own §17 simulation), so a full
    fetch-and-filter in Python is simpler and safer than hand-rolled SQL
    for a table at this expected scale. Never used by
    `resolve_fh_d4_group_status()`'s own exact-match resolution - calling
    this function can never change what that one returns.
    """
    with session.no_autoflush:
        normalized = _normalize(signal_ids)
        _validate_all_exist(session, set(normalized))
        query_set = frozenset(normalized)

        all_members = session.query(
            SignalDispositionMember.disposition_id, SignalDispositionMember.signal_id,
        ).all()
        member_sets: "dict[int, set]" = {}
        for disposition_id, signal_id in all_members:
            member_sets.setdefault(disposition_id, set()).add(signal_id)

        related_ids = []
        relation_by_id: "dict[int, str]" = {}
        for disposition_id, members in member_sets.items():
            members_frozen = frozenset(members)
            if members_frozen == query_set:
                continue  # exact match - "current," not "related"
            if members_frozen < query_set:
                related_ids.append(disposition_id)
                relation_by_id[disposition_id] = "SUBSET"
            elif query_set < members_frozen:
                related_ids.append(disposition_id)
                relation_by_id[disposition_id] = "SUPERSET"
            # else: merely overlapping, not a subset/superset - not related

        if not related_ids:
            return ()

        headers = session.query(SignalDisposition).filter(SignalDisposition.id.in_(related_ids)).all()

    results = tuple(
        RelatedHistoricalDisposition(
            disposition_id=header.id,
            member_signal_ids=tuple(sorted(member_sets[header.id])),
            decision=header.decision,
            reviewer=header.reviewer,
            reason=header.reason,
            created_at=header.created_at,
            relation=relation_by_id[header.id],
        )
        for header in headers
    )
    return tuple(sorted(results, key=lambda r: (r.created_at, r.disposition_id)))
