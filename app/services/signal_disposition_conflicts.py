"""Read-only, write-time overlap/contradiction conflict scan for a PROPOSED
Signal-group disposition member set (D4D8C of
docs/architecture/fh-d4-signal-disposition-d4d8-subgroup-semantics-design.md
§7, adversarially reviewed and locked in D4D8A).

    a caller's proposed exact Signal-id set (not yet persisted)
        -> find_signal_disposition_conflicts()
        -> tuple of SignalDispositionConflict (possibly empty)
        -> STOP (no write, no SignalDisposition created, no D4D5 CLI wiring,
           no persistence-service modification - this module answers
           exactly one question: "would writing a disposition for this
           proposed set create a syntactically overlapping, potentially
           contradictory CURRENT disposition history?")

This module implements ONLY the read-side conflict-detection helper a
future write path (D4D8D) will call BEFORE ever invoking
`record_signal_group_disposition()`. It does not itself gate, block, or
modify that function - `record_signal_group_disposition()` is completely
untouched by this slice, exactly matching D4D8A's own explicit scope
boundary ("do not modify record_signal_group_disposition() unless the
locked architecture explicitly requires wiring there now" - it does not;
wiring belongs to whichever future slice actually adds a write-time gate).

LOCKED OVERLAP POLICY (design doc §7, reproduced here as this module's own
authoritative contract): given a proposed member set P and an existing
CURRENT disposition's member set E,

    EXACT MATCH       (P == E)                 -> NOT a conflict (owned by
                                                    the existing exact-set
                                                    re-review/supersession
                                                    path, D3/D4D1)
    DISJOINT          (P intersect E == empty)  -> NOT a conflict
    NON-EXACT OVERLAP (P intersect E != empty
                        and P != E)             -> a CONFLICT, regardless of
                                                    whether E is a strict
                                                    subset, strict superset,
                                                    or a bare partial overlap
                                                    of P, and regardless of
                                                    whether E's own decision
                                                    happens to match what the
                                                    caller might propose for
                                                    P (design doc §7: "the
                                                    rule is syntactic, not
                                                    semantic... redundancy is
                                                    not exempted").

HISTORICAL / LATEST-SET POLICY (this module's own genuine design decision,
required because the design doc's prose alone does not spell out how a
superseded row participates): a proposed set is compared only against each
existing exact member-set's CURRENT (latest-wins) disposition - never
against every raw historical row independently. This mirrors, verbatim, the
SAME latest-wins reduction `app.services.signal_disposition_resolution.
resolve_fh_d4_group_statuses()` (D3) and `app.services.
fh_d4_disposition_resolution._batched_subgroup_discovery()` (D4D8B) already
apply before ANY downstream decision is made about a member set - "current
state for a given member set is derived by recency" is the one, single,
uniformly-applied rule this whole pipeline uses everywhere else a member
set's status matters. An old, superseded {1,2} SAME row must not create a
permanent false blocker against a proposed {2,3} once {1,2} has since been
corrected to {1,2} DISTINCT (or vice versa) - only the CURRENT {1,2} fact
participates in conflict detection; the superseded row remains permanently
auditable (nothing is deleted or hidden), it is simply not independently
"live" for this purpose, exactly as it is not independently "live" for
D3's own `resolve_fh_d4_group_status()` answer either.

GLOBAL SCOPE (design doc §7's own explicit requirement): this scan is NOT
limited to the current FH-D4 raw group, the current airport, or the current
detector run - it scans the ENTIRE `signal_dispositions`/
`signal_disposition_members` table. No parent-group foreign key, contextual
identity, or detector-run scoping is introduced anywhere in this module.

NO TRANSITIVE INFERENCE, EVER: this module never combines two different
existing member sets with each other or with the proposed set to derive a
third fact. It performs exactly one comparison - proposed set vs. each
CURRENT existing member set, independently - and returns the union of
whichever comparisons are non-trivial (non-disjoint, non-exact). No
union-find, no graph, no equivalence-class structure, no closure of any
kind exists anywhere in this module's code (verified structurally by
`tests/test_signal_disposition_conflicts.py::TestNoTransitiveInference`).

READ-ONLY, ALWAYS: wrapped in `session.no_autoflush` end-to-end, matching
D3/D4D4's own established review-checkpoint fix. Nothing here ever calls
`session.add()`, `.flush()`, `.commit()`, or `.delete()`.

INFORMATION FIREWALL: this module reads exactly the same narrow field set
D3/D4D4 already read - `SignalDisposition.id`/`.decision`/`.reviewer`/
`.reason`/`.created_at`/`.supersedes_id` and `SignalDispositionMember.
disposition_id`/`.signal_id` - and nothing from `Signal` at all (this
module never imports `Signal` and never queries it; see "INPUT
NORMALIZATION" below for why no existence check is performed here).

INPUT NORMALIZATION / VALIDATION CONTRACT: `signal_ids` is deduplicated and
sorted exactly like `record_signal_group_disposition()`/
`resolve_fh_d4_group_status()` already do (`tuple(sorted(set(...)))`), and
the same `MINIMUM_GROUP_CARDINALITY` (imported from
`app.services.signal_disposition_persistence`, not re-declared, so the two
modules can never silently drift apart) is enforced. Deliberately NOT
re-implementing a second, competing normalization rule.

UNLIKE `record_signal_group_disposition()`/`resolve_fh_d4_group_status()`,
this module does NOT verify that every id in `signal_ids` references a real,
existing `Signal` row. This is an explicit, narrow contract, not an
oversight: the intended caller (a future D4D8D write-time gate) will always
have already resolved a live FH-D4 group or otherwise independently
confirmed the proposed ids are real Signal ids before ever reaching this
conflict scan, so re-validating existence here would be a redundant query on
every call in the one workflow this module is built for. This module's own
queries never touch the `signals` table at all - its answer ("does this
member set overlap existing disposition history") is well-defined and
correct regardless of whether the proposed ids happen to reference real
Signals, since it only ever compares `signal_ids` (a plain set of integers)
against OTHER already-persisted `signal_ids` sets.

QUERY COST: mirrors D3's own established "find candidates from the ids
actually named, then fetch their full data" pattern
(`app.services.signal_disposition_resolution.
_member_sets_and_headers_for_candidates()`), not the "full-table-scan up
front" pattern D4D4's own batched functions use - D4D4 needed a full scan
because it resolves MANY raw groups in one call; this module resolves
exactly ONE proposed set per call, so finding candidates via
`signal_id IN proposed_ids` first is the more targeted, less wasteful
choice. At most 3 queries total per call (candidate disposition ids ->
their complete member rows -> their header rows), skipping the second and
third entirely (1 query total) when no disposition anywhere names any of
the proposed ids - never one query per candidate, never one query per
Signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models.signal_disposition import SignalDisposition, SignalDispositionMember
from app.services.signal_disposition_persistence import MINIMUM_GROUP_CARDINALITY

__all__ = [
    "STRICT_SUBSET",
    "STRICT_SUPERSET",
    "PARTIAL_OVERLAP",
    "EXACT_MATCH",
    "SignalDispositionConflict",
    "find_signal_disposition_conflicts",
]

# Relation of an existing CURRENT disposition's member set (E) to the
# proposed set (P) - direction matches D3's own `RelatedHistoricalDisposition
# .relation` convention exactly ("SUBSET"/"SUPERSET" are always stated
# relative to the set being queried against): STRICT_SUBSET means E < P,
# STRICT_SUPERSET means P < E. EXACT_MATCH (E == P) is never returned as a
# conflict (design doc §7) - it is exposed ONLY when a caller explicitly
# passes `exclude_exact_set=False`, purely as informational context for a
# future CLI wanting to explain "this exact set already has a decision,"
# never counted as a conflict and never assigned any ranking/severity.
STRICT_SUBSET = "STRICT_SUBSET"
STRICT_SUPERSET = "STRICT_SUPERSET"
PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
EXACT_MATCH = "EXACT_MATCH"


@dataclass(frozen=True)
class SignalDispositionConflict:
    """One CURRENT (latest-wins) existing disposition whose member set
    overlaps a proposed set - or, only when `exclude_exact_set=False`, an
    exact match. Plain, JSON-serializable-shaped fields only, never an ORM
    object - mirrors D3's `SignalDispositionStatus`/D4D4's
    `SubgroupDispositionSummary` contract exactly.

    `overlap_signal_ids` is `proposed_signal_ids intersect
    conflicting_signal_ids` - always non-empty (an entry is never returned
    for a disjoint existing set). `conflicting_supersedes_id` is the
    CURRENT disposition's own `supersedes_id` (whether this particular
    latest-wins row is itself a correction of an earlier one for the same
    exact set) - not a chain, just that one field, exactly as persisted.

    `independent_root_count`/`ambiguous_history` (D4D8C critical-review
    addition): the CONFLICTING member set's own root count/ambiguity - the
    exact same fields D3's `SignalDispositionStatus` and D4D8B's
    `SubgroupDispositionSummary` already expose for every other latest-wins
    -reduced fact in this pipeline, computed identically (a plain count of
    `supersedes_id IS NULL` headers sharing the conflicting exact member
    set, no chain-walking). Orthogonal to `relation`: this describes
    whether the CONFLICTING set's own history is itself contested, not
    whether it overlaps the proposed set. Without this, a future caller
    would have no way to know "conflicts with disposition #47" might itself
    rest on disputed history without a second, separate call to
    `resolve_fh_d4_group_status()` - exactly the kind of opacity the
    mission's own API-sufficiency review would flag as a defect.

    No ranking, confidence, or severity field exists anywhere on this
    dataclass (mission's own explicit instruction) - relation is a
    classification, not a priority, and `ambiguous_history` is a visibility
    flag, not a priority either.
    """

    proposed_signal_ids: "tuple[int, ...]"
    conflicting_disposition_id: int
    conflicting_signal_ids: "tuple[int, ...]"
    conflicting_decision: str
    conflicting_reviewer: str
    conflicting_reason: str
    conflicting_created_at: datetime
    conflicting_supersedes_id: Optional[int]
    overlap_signal_ids: "tuple[int, ...]"
    relation: str
    independent_root_count: int
    ambiguous_history: bool


def _normalize(signal_ids: Sequence[int]) -> "tuple[int, ...]":
    """Identical normalization to `record_signal_group_disposition()`/
    `resolve_fh_d4_group_status()` - deduplicate, sort, enforce the same
    imported `MINIMUM_GROUP_CARDINALITY` - deliberately not a second,
    competing rule."""
    deduplicated = tuple(sorted(set(signal_ids)))
    if len(deduplicated) < MINIMUM_GROUP_CARDINALITY:
        raise ValueError(
            f"signal_ids must contain at least {MINIMUM_GROUP_CARDINALITY} distinct Signal ids, "
            f"got {deduplicated!r}"
        )
    return deduplicated


def find_signal_disposition_conflicts(
    session: Session,
    *,
    signal_ids: Sequence[int],
    exclude_exact_set: bool = True,
) -> "tuple[SignalDispositionConflict, ...]":
    """Scans the ENTIRE persisted `signal_dispositions`/
    `signal_disposition_members` history (global scope, design doc §7) for
    every CURRENT (latest-wins per distinct exact member set, "HISTORICAL /
    LATEST-SET POLICY" above) disposition whose member set has a non-empty
    intersection with the proposed `signal_ids`.

    `exclude_exact_set=True` (default): an exact match (the proposed set's
    own exact-set disposition, if any) is never returned - that case is
    owned entirely by the existing exact-set re-review/supersession path
    and is not, by the locked policy's own definition, an overlap conflict
    at all. `exclude_exact_set=False`: the exact match, if one exists, IS
    included, with `relation=EXACT_MATCH` - purely informational, never
    itself a reason to refuse anything.

    Returns an empty tuple when nothing overlaps - never raises for that
    case. Fails loud (an ordinary, uncaught `sqlalchemy.exc.
    OperationalError`) if the D4D2 tables are missing, matching D3/D4D4's
    own established "no self-check, fail loud" contract - never silently
    returns "no conflicts" on a genuine query failure.
    """
    with session.no_autoflush:
        proposed = _normalize(signal_ids)
        proposed_set = frozenset(proposed)

        candidate_disposition_ids = {
            row[0]
            for row in session.query(SignalDispositionMember.disposition_id)
            .filter(SignalDispositionMember.signal_id.in_(proposed_set))
            .distinct()
            .all()
        }
        if not candidate_disposition_ids:
            return ()

        member_rows = (
            session.query(SignalDispositionMember.disposition_id, SignalDispositionMember.signal_id)
            .filter(SignalDispositionMember.disposition_id.in_(candidate_disposition_ids))
            .all()
        )
        member_sets: "dict[int, set]" = {}
        for disposition_id, signal_id in member_rows:
            member_sets.setdefault(disposition_id, set()).add(signal_id)
        member_sets = {did: frozenset(members) for did, members in member_sets.items()}

        headers = (
            session.query(SignalDisposition)
            .filter(SignalDisposition.id.in_(candidate_disposition_ids))
            .all()
        )
        headers_by_id = {header.id: header for header in headers}

        # Latest-wins reduction, PER DISTINCT exact member set among the
        # candidates - identical tie-break (created_at DESC, id DESC) D3's
        # own resolve_fh_d4_group_statuses() and D4D4's own
        # _batched_subgroup_discovery() already use. A member set with no
        # candidate rows at all (fully disjoint from `proposed`) never
        # appears in `member_sets` in the first place - the initial
        # signal_id-scoped candidate query already excludes it.
        headers_by_member_set: "dict[frozenset, list[SignalDisposition]]" = {}
        for disposition_id, members in member_sets.items():
            headers_by_member_set.setdefault(members, []).append(headers_by_id[disposition_id])

        conflicts: "list[SignalDispositionConflict]" = []
        for member_set, headers_for_set in headers_by_member_set.items():
            latest = max(headers_for_set, key=lambda h: (h.created_at, h.id))
            # Same plain count D3/D4D8B already compute for this exact
            # purpose - no chain-walking, just how many of this exact
            # member set's own rows are themselves roots (supersedes_id
            # IS NULL).
            independent_root_count = sum(1 for h in headers_for_set if h.supersedes_id is None)
            # Defensive invariant, not a real filter: every member_set here
            # was grouped from a candidate whose own member rows were found
            # via `signal_id IN proposed_set`, so `overlap` is guaranteed
            # non-empty by construction - this guard documents that
            # invariant rather than silently trusting it.
            overlap = tuple(sorted(proposed_set & member_set))
            if not overlap:  # pragma: no cover - unreachable by construction, see comment above
                raise AssertionError(f"candidate member set {member_set!r} unexpectedly disjoint from proposed set")

            if member_set == proposed_set:
                if exclude_exact_set:
                    continue
                relation = EXACT_MATCH
            elif member_set < proposed_set:
                relation = STRICT_SUBSET
            elif proposed_set < member_set:
                relation = STRICT_SUPERSET
            else:
                relation = PARTIAL_OVERLAP

            conflicts.append(
                SignalDispositionConflict(
                    proposed_signal_ids=proposed,
                    conflicting_disposition_id=latest.id,
                    conflicting_signal_ids=tuple(sorted(member_set)),
                    conflicting_decision=latest.decision,
                    conflicting_reviewer=latest.reviewer,
                    conflicting_reason=latest.reason,
                    conflicting_created_at=latest.created_at,
                    conflicting_supersedes_id=latest.supersedes_id,
                    overlap_signal_ids=overlap,
                    relation=relation,
                    independent_root_count=independent_root_count,
                    ambiguous_history=independent_root_count > 1,
                )
            )

        return tuple(sorted(conflicts, key=lambda c: (c.conflicting_signal_ids, c.conflicting_disposition_id)))
