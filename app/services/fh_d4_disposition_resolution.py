from __future__ import annotations

"""Fleet Health Check — D4D4: FH-D4 disposition-aware integration adapter
(docs/architecture/fh-d4-signal-disposition-design.md §14-15, this mission's
own §3 architecture boundary).

    DB facts
        -> pure FH-D4 rule (app.services.fleet_health_review_rules.evaluate_fh_d4,
           UNCHANGED, never imported or modified here)
        -> raw FH-D4 HealthFinding groups (entity_ids = member Signal ids)
        -> resolve_fh_d4_findings() (THIS module - disposition-aware adapter)
        -> FhD4DispositionResolution (operational active/resolved/ambiguous view)

This module is the "small, separate, single-purpose" home the design doc's
own §14 recommends - NOT `fleet_health_check.py` (that adapter's own
committed, tested contract is "pure DB reads assembling facts for the pure
rule modules"; it has no existing concept of reading a *governance* table)
and NOT the CLI (`scripts/run_data_health_check.py`'s own AST-verified
contract is "compose already-reviewed services, add zero logic of its
own" - a disposition-aware filter is exactly the kind of "logic" that
contract exists to keep out). `run_fleet_review_check()`/
`run_full_fleet_health_check()` in `fleet_health_check.py` are NOT modified
by this slice - both remain byte-for-byte identical to their D4D3-era
behavior, so every existing caller/test of those two functions is
unaffected. This module adds a NEW, additional entrypoint
(`run_disposition_aware_fh_d4_review()`) rather than extending an existing
one - this mission's own explicit choice (§13), made in favor of backward
compatibility over a unified entrypoint.

DETECTION != DISPOSITION: this module never imports
`app.services.fleet_health_review_rules` and never constructs or mutates a
`HealthFinding` - it only ever reads the `rule_id`/`entity_ids`/`airport_id`/
`summary`/`structured_evidence` fields of FH-D4 findings ALREADY PRODUCED by
that pure module, exactly as they were emitted. FH-D4's own detection
heuristic (co-location, no runway claimed) is completely untouched by this
slice - a disposition can change how a group is PRESENTED, never whether
FH-D4 itself finds it.

WHY A NEW BATCHED RELATED-HISTORY QUERY, NOT A CALL PER GROUP: D4D3's own
`find_related_historical_dispositions()` is deliberately single-group (one
full `SignalDispositionMember` table scan per call - a documented, accepted
tradeoff at D4D3's own single-group scale). Calling it once per raw FH-D4
group here would reproduce the exact class of defect D4D3's OWN critical
review already found and fixed once for existence-checking (Defect 1 in
that review: a per-group loop that was O(groups) queries instead of O(1)).
Rather than re-opening D4D3's already-reviewed, already-committed file to
add a batched variant there, this module implements its own private,
batched subset/superset scan (`_batched_related_history()`) directly against
the same two models D4D3 itself reads (`SignalDisposition`/
`SignalDispositionMember`) - reusing D4D3's own exact subset/superset
relation semantics (never bare intersection, matching D4D3's own module
docstring reasoning verbatim), just computed once for the WHOLE batch
instead of once per group. `resolve_fh_d4_group_statuses()` (D4D3's own
batch resolver, unmodified) is still the sole source of truth for "current"
exact-match status - this module never recomputes or second-guesses it.

INDEPENDENT-ROOT POLICY (this mission's own §6, a genuine design decision -
see this module's own report for the full reasoning): D4D3's own
`independent_root_count` field is exposed per group but NEVER changes
`status`/`decision`/`latest_disposition_id` - "latest wins" resolution stays
exactly as D4D3 already established, unmodified. What D4D4 decides is
purely an OPERATIONAL PRESENTATION question: a `CONFIRMED_DISTINCT` or
`CONFIRMED_SAME_REAL_WORLD_EFFORT` group whose `independent_root_count > 1`
(proof that more than one independently-recorded root disposition exists
for this exact member set) is placed in its own dedicated
`ambiguous_groups` bucket instead of `confirmed_distinct`/
`confirmed_same_effort` - never silently folded into "resolved" as if the
review history were unanimous. This is "fail-visible over silent
resolution" (this mission's own explicit preference, §19): the group's own
`status`/`decision` fields are still populated (a caller inspecting an
`ambiguous_groups` entry can see exactly what the latest disposition says),
but its OPERATIONAL bucket makes the disagreement impossible to miss. An
`UNREVIEWED` group can never be ambiguous by construction
(`independent_root_count` is only nonzero when at least one matching
disposition exists) - ambiguity only ever applies to a resolved group.

CRITICAL-REVIEW ADDITION - `attention_required`: keeping `ambiguous_groups`
as its own bucket, fully disjoint from `active_findings` (required for the
accounting invariant below), creates a real, reproduced usability risk: an
operational caller who naively treats `active_findings` alone as "today's
review queue" would silently never see an ambiguous group at all - exactly
the silent-suppression failure mode this whole mechanism exists to avoid.
`FhD4DispositionResolution.attention_required` closes that gap WITHOUT
weakening the accounting invariant: it is a derived, read-only VIEW (not a
fifth exclusive bucket) equal to `active_findings + ambiguous_groups`, in
original raw-finding order - every group that currently needs a human's
attention, whether because nothing has been decided yet or because the
decision history is itself ambiguous. `confirmed_distinct`/
`confirmed_same_effort` groups are never included. The four PRIMARY
buckets remain exactly as documented (pairwise disjoint, together
exhaustive over `d4_findings`); `attention_required` is a convenience
union over two of them, not a new partition.

ACCOUNTING INVARIANT (this mission's own §25): for every call,
`len(d4_findings) == len(active_findings) + len(confirmed_distinct) +
len(confirmed_same_effort) + len(ambiguous_groups)` - each raw FH-D4 finding
lands in EXACTLY one bucket, never zero, never more than one. No raw finding
is ever destroyed, mutated, or silently dropped; `non_d4_findings` carries
every non-FH-D4 finding through completely untouched (same object identity,
original order) so a caller reconstructing the full original result set
never loses anything either.

SCHEMA READINESS: this module deliberately does NOT check whether the D4D2
tables exist before querying them - it fails loud naturally (an ordinary,
uncaught `sqlalchemy.exc.OperationalError` propagates), exactly matching
D4D3's own already-reviewed, already-committed "no self-check, fail loud"
contract (`app/services/signal_disposition_resolution.py` has no schema-
readiness function of its own either). A Path-based, pre-session schema
gate (reusing `scripts.migrate_signal_disposition_d4d2.inspect()`, exactly
like `scripts/run_data_health_check.py`'s own existing `check_schema_
readiness()` already composes five other `inspect()` functions the same
way) is deliberately NOT added here: no `app/services/*` module anywhere in
this codebase imports from `scripts/` (verified by direct search before
writing this module) - only the reverse dependency exists throughout this
pipeline. Adding one here would introduce a new, backwards architectural
dependency for a single slice. A future CLI/D4D5 extension is responsible
for composing `migrate_signal_disposition_d4d2.inspect()` into ITS OWN
schema gate before ever opening a Session and calling this module's
functions - exactly the same pattern the existing CLI already uses for its
other five inspectors, not a new idea.

READ-ONLY, ALWAYS: wrapped in `session.no_autoflush` end-to-end, the same
review-checkpoint fix `fleet_health_check.py`'s own snapshot builders and
D4D3's own resolution functions already established. Nothing here ever
calls `session.add()`, `.flush()`, `.commit()`, or `.delete()`. No Signal is
ever mutated, merged, or deleted; no ReviewerAction or SignalDisposition is
ever created; `Signal.published` is never touched.

INFORMATION FIREWALL: this module composes ALREADY-COMPUTED decisions only
- D4D3's own `SignalDispositionStatus`/`RelatedHistoricalDisposition`
dataclasses (audit fields only: id/decision/reviewer/reason/created_at) and
FH-D4's own already-emitted `HealthFinding` (rule_id/entity_ids/airport_id/
summary/structured_evidence). It never reads `Signal.title`, `.notes`,
`.source_notes`, any financial field, `.category`, `.confidence`,
`.status`, `.supplier`/`.likely_supplier`/`.confirmed_vendor`, or
`.published` - verified structurally (AST) and behaviorally in
`tests/test_fh_d4_disposition_resolution.py`, mirroring D4D3's own firewall
test exactly.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models.signal_disposition import SignalDisposition, SignalDispositionMember
from app.services.fleet_health_check import run_fleet_review_check
from app.services.fleet_health_rules import HealthFinding
from app.services.signal_disposition_resolution import (
    CONFIRMED_DISTINCT,
    CONFIRMED_SAME_REAL_WORLD_EFFORT,
    UNREVIEWED,
    RelatedHistoricalDisposition,
    resolve_fh_d4_group_statuses,
)

__all__ = [
    "FH_D4_RULE_ID",
    "FhD4OperationalGroup",
    "FhD4DispositionResolution",
    "resolve_fh_d4_findings",
    "run_disposition_aware_fh_d4_review",
]

FH_D4_RULE_ID = "FH-D4"


@dataclass(frozen=True)
class FhD4OperationalGroup:
    """One FH-D4 candidate group, annotated with its disposition state - the
    "wrapper/annotation dataclass" this mission's own §17 calls for, never a
    mutated `HealthFinding` (the pure rule's own result type is completely
    unchanged; `raw_finding` below is the exact same object FH-D4 emitted,
    referenced, not copied or altered).

    `signal_ids` is the deduplicated, ascending-sorted canonical form (the
    same normalization D4D3's own `resolve_fh_d4_group_status()` applies) -
    always equal to `raw_finding.entity_ids` in practice, since FH-D4 itself
    already emits deduplicated, sorted ids, but computed independently
    rather than assumed, so this module never silently trusts an upstream
    invariant it does not itself enforce.

    `status`/`latest_disposition_id`/`decision`/`reviewer`/`reason`/
    `created_at`/`independent_root_count` are D4D3's own
    `SignalDispositionStatus` fields, copied through verbatim - this module
    never recomputes or reinterprets any of them.

    `ambiguous_history` is `True` iff `independent_root_count > 1` - see
    this module's own top-of-file docstring, "INDEPENDENT-ROOT POLICY."

    `related_history` is D4D3's own `RelatedHistoricalDisposition` tuple
    (strict subset/superset only, never bare intersection - D4D3's own
    semantics, reused verbatim) - populated for every group regardless of
    its own `status` (a resolved group can still have older, narrower or
    broader historical context worth showing), never affecting `status`
    itself.
    """

    raw_finding: HealthFinding
    signal_ids: "tuple[int, ...]"
    airport_id: Optional[int]
    status: str
    latest_disposition_id: Optional[int]
    decision: Optional[str]
    reviewer: Optional[str]
    reason: Optional[str]
    created_at: Optional[datetime]
    independent_root_count: int
    ambiguous_history: bool
    related_history: "tuple[RelatedHistoricalDisposition, ...]"


@dataclass(frozen=True)
class FhD4DispositionResolution:
    """The one result shape `resolve_fh_d4_findings()`/
    `run_disposition_aware_fh_d4_review()` return - narrow, frozen
    dataclasses only, never an ORM object (mirrors D4D3's own `SignalDisposition
    Status` contract exactly: "the integration should not need lazy ORM
    traversal").

    Every raw FH-D4 finding lands in EXACTLY one of the four PRIMARY group
    buckets (`active_findings`/`confirmed_distinct`/`confirmed_same_effort`/
    `ambiguous_groups`) - see this module's own top-of-file "ACCOUNTING
    INVARIANT" section. `attention_required` is a derived, read-only VIEW
    over two of those four buckets (`active_findings + ambiguous_groups`,
    in original raw-finding order) - critical-review addition, see this
    module's own top-of-file "CRITICAL-REVIEW ADDITION" section for why: it
    is never a fifth exclusive bucket and never counted separately in the
    accounting invariant, purely a convenience for a caller building "what
    needs a human's attention right now" without risking silently missing
    `ambiguous_groups` by only checking `active_findings`.

    `non_d4_findings` is every OTHER finding the caller supplied, completely
    untouched (same object identity, original relative order) - this
    module's own §16/§22 boundary: it touches ONLY FH-D4 findings.
    """

    active_findings: "tuple[FhD4OperationalGroup, ...]"
    confirmed_distinct: "tuple[FhD4OperationalGroup, ...]"
    confirmed_same_effort: "tuple[FhD4OperationalGroup, ...]"
    ambiguous_groups: "tuple[FhD4OperationalGroup, ...]"
    attention_required: "tuple[FhD4OperationalGroup, ...]"
    non_d4_findings: "tuple[HealthFinding, ...]"


def _batched_related_history(
    session: Session, group_sets: "Sequence[frozenset]",
) -> "dict[frozenset, tuple[RelatedHistoricalDisposition, ...]]":
    """Batched equivalent of calling D4D3's own
    `find_related_historical_dispositions()` once per group - see this
    module's own top-of-file docstring, "WHY A NEW BATCHED RELATED-HISTORY
    QUERY." Exactly two queries total regardless of how many groups are
    being resolved (a full `SignalDispositionMember` fetch, then a batched
    header fetch for whichever disposition ids turned out related - the
    second query is skipped entirely, matching D4D3's own short-circuit
    style, if nothing is related to any group). Reuses D4D3's own exact
    subset/superset relation definition (never bare intersection, never an
    exact match - an exact match is a "current" status, not "related")."""
    if not group_sets:
        return {}

    all_members = session.query(
        SignalDispositionMember.disposition_id, SignalDispositionMember.signal_id,
    ).all()
    member_sets: "dict[int, set]" = {}
    for disposition_id, signal_id in all_members:
        member_sets.setdefault(disposition_id, set()).add(signal_id)
    member_sets = {did: frozenset(members) for did, members in member_sets.items()}

    relation_by_group: "dict[frozenset, dict[int, str]]" = {gs: {} for gs in group_sets}
    all_related_ids: "set[int]" = set()
    for group_set in group_sets:
        for disposition_id, members in member_sets.items():
            if members == group_set:
                continue  # exact match - "current," not "related"
            if members < group_set:
                relation_by_group[group_set][disposition_id] = "SUBSET"
                all_related_ids.add(disposition_id)
            elif group_set < members:
                relation_by_group[group_set][disposition_id] = "SUPERSET"
                all_related_ids.add(disposition_id)
            # else: merely overlapping, not a subset/superset - not related

    if not all_related_ids:
        return {gs: () for gs in group_sets}

    headers = (
        session.query(SignalDisposition).filter(SignalDisposition.id.in_(all_related_ids)).all()
    )
    headers_by_id = {header.id: header for header in headers}

    result: "dict[frozenset, tuple[RelatedHistoricalDisposition, ...]]" = {}
    for group_set, relations in relation_by_group.items():
        items = tuple(
            RelatedHistoricalDisposition(
                disposition_id=disposition_id,
                member_signal_ids=tuple(sorted(member_sets[disposition_id])),
                decision=headers_by_id[disposition_id].decision,
                reviewer=headers_by_id[disposition_id].reviewer,
                reason=headers_by_id[disposition_id].reason,
                created_at=headers_by_id[disposition_id].created_at,
                relation=relation,
            )
            for disposition_id, relation in relations.items()
        )
        result[group_set] = tuple(sorted(items, key=lambda r: (r.created_at, r.disposition_id)))
    return result


def resolve_fh_d4_findings(
    session: Session, findings: Sequence[HealthFinding],
) -> FhD4DispositionResolution:
    """Takes ANY tuple of `HealthFinding` (a full FHC3 `run_fleet_review_check()`
    result, a full `run_full_fleet_health_check()` result, or any other
    mixed set) and returns a disposition-aware view of exactly the FH-D4
    findings within it - every other finding passes through
    `non_d4_findings`, byte/identity-unchanged (this mission's own §16).

    Bounded, non-N+1 query cost: at most 4 queries for the exact-match
    batch resolution (D4D3's own `resolve_fh_d4_group_statuses()`, unmodified)
    plus at most 2 for the batched related-history scan
    (`_batched_related_history()`) = at most 6 queries total for the ENTIRE
    call, regardless of how many FH-D4 groups are present - never one query
    per group. Zero queries at all if `findings` contains no FH-D4 rows.

    Fails loud naturally if the D4D2 tables are missing (an ordinary,
    uncaught `sqlalchemy.exc.OperationalError`) - see this module's own
    top-of-file "SCHEMA READINESS" section. Never returns a fabricated
    "everything unreviewed" or "everything resolved" result on failure.
    """
    with session.no_autoflush:
        d4_findings = tuple(f for f in findings if f.rule_id == FH_D4_RULE_ID)
        non_d4_findings = tuple(f for f in findings if f.rule_id != FH_D4_RULE_ID)

        if not d4_findings:
            return FhD4DispositionResolution(
                active_findings=(), confirmed_distinct=(), confirmed_same_effort=(),
                ambiguous_groups=(), attention_required=(), non_d4_findings=non_d4_findings,
            )

        for finding in d4_findings:
            if finding.entity_type != "Signal":
                raise ValueError(
                    f"FH-D4 finding {finding!r} has entity_type={finding.entity_type!r}, "
                    "expected 'Signal' - refusing to resolve a malformed FH-D4 finding"
                )

        canonical_keys = [tuple(sorted(set(f.entity_ids))) for f in d4_findings]
        statuses = resolve_fh_d4_group_statuses(session, [f.entity_ids for f in d4_findings])

        group_sets = [frozenset(key) for key in canonical_keys]
        related_by_set = _batched_related_history(session, group_sets)

        active: "list[FhD4OperationalGroup]" = []
        confirmed_distinct: "list[FhD4OperationalGroup]" = []
        confirmed_same_effort: "list[FhD4OperationalGroup]" = []
        ambiguous: "list[FhD4OperationalGroup]" = []
        attention_required: "list[FhD4OperationalGroup]" = []

        for finding, canonical_key in zip(d4_findings, canonical_keys):
            status = statuses[canonical_key]
            related = related_by_set.get(frozenset(canonical_key), ())
            ambiguous_history = status.independent_root_count > 1
            group = FhD4OperationalGroup(
                raw_finding=finding,
                signal_ids=canonical_key,
                airport_id=finding.airport_id,
                status=status.status,
                latest_disposition_id=status.latest_disposition_id,
                decision=status.decision,
                reviewer=status.reviewer,
                reason=status.reason,
                created_at=status.created_at,
                independent_root_count=status.independent_root_count,
                ambiguous_history=ambiguous_history,
                related_history=related,
            )
            if status.status == UNREVIEWED:
                active.append(group)
                attention_required.append(group)
            elif ambiguous_history:
                # Fail-visible over silent resolution (this mission's own
                # §6/§19): multiple independently-recorded roots exist for
                # this exact set - surfaced in its own bucket regardless of
                # which decision "won" by recency, never silently folded
                # into "resolved." Critical-review addition: also included
                # in attention_required (see that field's own docstring) so
                # a caller checking only active_findings can never silently
                # miss it.
                ambiguous.append(group)
                attention_required.append(group)
            elif status.status == CONFIRMED_DISTINCT:
                confirmed_distinct.append(group)
            elif status.status == CONFIRMED_SAME_REAL_WORLD_EFFORT:
                confirmed_same_effort.append(group)
            else:  # pragma: no cover - D4D3's own status vocabulary is exhaustively three values
                raise AssertionError(f"unreachable SignalDispositionStatus.status {status.status!r}")

        return FhD4DispositionResolution(
            active_findings=tuple(active),
            confirmed_distinct=tuple(confirmed_distinct),
            confirmed_same_effort=tuple(confirmed_same_effort),
            ambiguous_groups=tuple(ambiguous),
            attention_required=tuple(attention_required),
            non_d4_findings=non_d4_findings,
        )


def run_disposition_aware_fh_d4_review(session: Session) -> FhD4DispositionResolution:
    """Convenience, single-call entrypoint: runs the existing, UNMODIFIED
    FHC3 review check (`app.services.fleet_health_check.run_fleet_review_check()`)
    and resolves disposition state for exactly its FH-D4 findings. This is
    the new entrypoint this mission's own §13 chose (option A) over
    extending `run_fleet_review_check()`/`run_full_fleet_health_check()` in
    place - both of those remain completely untouched, so every existing
    caller/test of either is unaffected by this slice.
    """
    findings = run_fleet_review_check(session)
    return resolve_fh_d4_findings(session, findings)
