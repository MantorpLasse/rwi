"""Human reviewer CLI for FH-D4 Signal-group dispositions
(D4D5 of docs/architecture/fh-d4-signal-disposition-design.md's own §19
slice table; docs/architecture/fh-d4-signal-disposition-d4d4-fleet-health-integration-report.md's
own §55 "exact recommended D4D5 scope").

    python -m scripts.review_signal_disposition --database data/runway_safe.db
        -> overview: every current FH-D4 group needing attention (unreviewed
           or ambiguous history), plus resolved groups shown separately.
           Never writes.

    python -m scripts.review_signal_disposition --database ... \\
        --signal-id 41 --signal-id 67
        -> targeted inspection: current disposition-aware state for exactly
           this Signal set. Never writes.

    python -m scripts.review_signal_disposition --database ... \\
        --signal-id 41 --signal-id 67 --decision DISTINCT \\
        --reviewer human:you --reason "..."
        -> dry-run: shows whether this exact decision is currently eligible
           and, if so, the exact disposition it would record (including the
           planned supersedes_id). Never writes.

    python -m scripts.review_signal_disposition --database ... \\
        --signal-id 41 --signal-id 67 --decision DISTINCT \\
        --reviewer human:you --reason "..." --allow-database-write
        -> the only invocation shape that writes: re-verifies the target is
           still a current FH-D4 group with the same disposition state,
           then records exactly ONE SignalDisposition row (+ its member
           rows) via `record_signal_group_disposition()`. One commit.

MIRRORS `scripts/review_reconciliation_item.py`'s own proven three-tier
safety philosophy (inspect / dry-run / write, `--database` required with no
default, `--allow-database-write` required for any write, read-only engine
whenever not writing, one caller-owned commit, rollback on any exception) -
adapted to FH-D4 Signal groups instead of SourceAssertion reconciliation.
Two deliberate differences from that precedent, both explained below:

1. NO CONFIRMATION-FINGERPRINT HANDSHAKE. `review_reconciliation_item.py`
   requires `--confirm-current-plan <fingerprint>` at write time because a
   `ReconciliationReviewPlan` has nine heterogeneous fields that must all
   agree byte-for-byte, and its own SHA-256 fingerprint is the established
   mechanism for that comparison. A `SignalDisposition`, by contrast, has
   no fingerprint field at all and this project's own D4D1/D4D3 design
   explicitly rejected fingerprinting for this domain ("no fingerprint is
   used - deliberately... a fingerprint would add a layer of translation
   around a comparison Python can already do directly and cheaply" - see
   `fh-d4-signal-disposition-design.md` §8). Instead, EVERY invocation of
   `run_review()` independently, freshly recomputes the target group's
   current state via `run_disposition_aware_fh_d4_review()` - there is no
   cached dry-run result a write invocation could ever trust instead of
   reality, by construction. A write invocation additionally recomputes
   that same state a SECOND time, immediately before calling
   `record_signal_group_disposition()`, and refuses (never writes) if
   anything about the target group's exact status/latest_disposition_id/
   independent_root_count/related_history changed between the two reads
   within this one call - the "critical safety property" this mission's
   own §14 describes, made a real, directly testable code path rather than
   an inferred consequence of two separate CLI invocations.

2. TARGET GROUP MUST BE A CURRENT RAW FH-D4 FINDING. Unlike
   `review_reconciliation_item.py` (which acts on any existing
   `SourceAssertion` id, whether or not it currently blocks anything), this
   CLI refuses to disposition a Signal set that is not, right now, an
   actual FH-D4 candidate group (`app.services.fh_d4_disposition_resolution.
   run_disposition_aware_fh_d4_review()`'s own output is the sole source of
   truth for "is this exact set current"). A disposition can never be
   recorded for an arbitrary, detector-unrelated Signal set through this
   CLI - see this mission's own §9.

DECISION VOCABULARY: exactly `DISTINCT` / `SAME_REAL_WORLD_EFFORT` (D4D1's
own `SIGNAL_DISPOSITION_DECISIONS`, imported, never redefined here). No
MERGE/DELETE/CANONICAL/IGNORE/SUPPRESS/DUPLICATE_SIGNAL value exists
anywhere in this file's own vocabulary - this CLI cannot express any of
those actions even if asked to, because `record_signal_group_disposition()`
itself has no code path for them (see that function's own module
docstring).

NO AUTO-DECISION: `config.decision` is the ONLY source of the persisted
`decision` value - always the caller's own explicit `--decision` argument,
never inferred from `HealthFinding.summary`, `structured_evidence`, title,
category, vendor, or any other Signal content. This module never imports
`Signal.title`/`.notes`/`.category`/`.confidence`/`.status`/`.published`/
any financial field, and performs no text-similarity, scoring, or ranking
computation of any kind - verified structurally (AST) and behaviorally in
`tests/test_review_signal_disposition.py`.

AMBIGUOUS-HISTORY POLICY (this mission's own §16/§18, a genuine design
decision): if the target group's `independent_root_count > 1` (D4D3's own
signal that multiple independently-recorded root dispositions exist for
this exact set), this CLI REFUSES any decision-bearing action (dry-run and
write both report `action_eligible=False`) - it never silently supersedes
one arbitrarily-chosen competing root. Inspection (no `--decision`
supplied) remains fully available; a human can always SEE an ambiguous
group's full state through this CLI, just never resolve it through an
ordinary action. No override flag is offered in this slice - the mission's
own explicit "do not add it unless genuinely needed" is honored; resolving
genuine competing-root ambiguity is deferred as an explicit, separate,
future decision (see this file's own report, "Ambiguous-group policy").

IDEMPOTENCY: requesting the SAME decision the target group's latest exact
disposition already records (e.g. DISTINCT -> DISTINCT, unchanged current
group) is refused with `ALREADY_CONFIRMED_CURRENT_DECISION` - no duplicate
audit row is ever created for a no-op re-confirmation. Requesting a
DIFFERENT decision is treated as a genuine re-review: the new disposition
supersedes the target's own single latest exact-set disposition
(`supersedes_id`), matching D4D1's own supersession contract exactly (no
locally-invented linkage).

SCHEMA GATE: reuses `scripts.migrate_signal_disposition_d4d2.inspect()`
verbatim (never reimplemented, never imports migration EXECUTION -
`upgrade()`/`downgrade()` - into this module or any `app/services/*`
module) - refuses with `SIGNAL_DISPOSITION_SCHEMA_MIGRATION_REQUIRED`
before ever running FH-D4 detection or disposition resolution if the D4D2
tables are not ready.

WRITE AUTHORIZATION / DATABASE ARGUMENT: `--database` has no default
(never the real production database by accident); `--allow-database-write`
is required for any write, matching every write-capable script in this
project. Inspect and dry-run always open the target database via SQLite's
own read-only URI mode (`mode=ro`) - even a coding mistake that tried to
write would be refused at the driver level. No `app.database.SessionLocal`,
no process-global engine.

TRANSACTION BOUNDARY: the write path opens exactly one writable session,
performs its two reads and one `record_signal_group_disposition()` call
inside it, and issues exactly one `session.commit()` - any exception
anywhere in that block triggers `session.rollback()` and re-raises; there
is no multi-commit workflow and no partial durable state.

CONCURRENCY / OPERATIONAL ASSUMPTION (critical-review addition, honestly
documented rather than silently assumed): the pre-write re-read
(described above) closes the window between a HUMAN's earlier dry-run and
a later write invocation, and the window WITHIN one write invocation
between its own two reads - but a narrow window still remains between the
second (fresh) read and this same invocation's own `session.commit()`:
`record_signal_group_disposition()` itself re-validates Signal existence
and `supersedes_id` target validity, but does not re-check "has some OTHER
process, in the last few milliseconds, also written a disposition for
this exact set." Two genuinely concurrent operators targeting the SAME
exact Signal set could, in principle, both pass their own fresh re-read
and both successfully commit - producing exactly the independent-competing
-roots shape D4D3/D4D4 already detect and this CLI's own ambiguous-history
refusal already protects the NEXT decision-bearing action against (see
above). This CLI does not add cross-process locking or serialization to
close that last, narrow window - doing so for a low-frequency, human-paced
review tool would be genuine over-engineering for a real risk this small.
The explicit, honest operational assumption this slice makes: a single
human reviewer operates this CLI against a given database at a time. If
that assumption is ever violated, the WORST outcome is an extra, visible
`ambiguous_groups` entry a future decision must explicitly resolve -
never a silently corrupted or incorrectly superseded disposition history.
SUBGROUP MODE (D4D8D, below) makes the identical assumption, with the
identical worst case: two concurrent operators could each pass their own
fresh subgroup re-read and both commit, producing either an ordinary
exact-set `ambiguous_groups` entry (if they targeted the same exact
subgroup) or a D4D8C-detectable overlap conflict a future subgroup review
must explicitly resolve (if they targeted overlapping-but-different
subgroups) - never silent corruption. No new locking is added here either.

NO STATIC EXPORT: this CLI has nothing to do with FHC4/the static site -
no `--output-dir`, no `run_fleet_presentation_check()` import, no site
mutation of any kind.

SUBGROUP MODE (D4D8D of docs/architecture/fh-d4-signal-disposition-d4d8-
subgroup-semantics-design.md §11, adversarially reviewed and locked in
D4D8A/B/C): an EXPLICIT, opt-in extension alongside the whole-group mode
above, never inferred merely because fewer `--signal-id` values are passed
than some group's own size. A reviewer opts in by ALSO supplying
`--parent-signal-id` (repeatable, >= 2 distinct ids) naming the PARENT raw
FH-D4 group; `--signal-id` then means the TARGET SUBGROUP (a strict,
proper subset of the parent) instead of a whole group. The whole-group
code path above is completely untouched by this addition - every existing
behavior (inspect/dry-run/write, idempotency, supersession, ambiguity
refusal, fresh re-read before write, transaction boundary) remains exactly
as already reviewed and committed.

PARENT VALIDITY: the parent must be found, by EXACT member-set match,
specifically within the current `active_findings` bucket (`_find_active_
parent()`) - the only bucket D4D8B ever computes `resolved_subgroups`/
`unresolved_remainder_signal_ids`/`subgroup_conflict` for at all (exact-set
precedence). A parent already resolved at the whole-group level
(`CONFIRMED_DISTINCT`/`CONFIRMED_SAME_REAL_WORLD_EFFORT`) or already
`ambiguous_groups` is refused (`PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER`) -
reviewing a subgroup of an already-decided or already-contested whole
group is a usage error a human should reconsider, not silently
accommodate. TARGET VALIDITY (`--signal-id` in subgroup mode) is checked
at config-validation time, before any database is opened (mirrors the
existing whole-group cardinality check exactly): >= 2 distinct ids, a
STRICT/PROPER subset of the parent as given (never equal, never containing
an id outside the parent) - a pure set-shape check, no DB access needed,
so it can never "silently span multiple live raw groups" (a target is only
ever validated relative to the ONE parent set the reviewer explicitly
named).

CONFLICT GUARD - MANDATORY, NO OVERRIDE (design doc §7, D4D8C): before any
decision-bearing subgroup action (dry-run's eligibility preview and write
alike), `find_signal_disposition_conflicts()` is called for the target
subgroup's own proposed exact member set. A non-empty result REFUSES the
action unconditionally - there is no `--force`, no advisory-only path, no
override flag anywhere in this module. The target's OWN exact-set history
is never itself an overlap conflict (D4D8C's own `exclude_exact_set=True`
default excludes it structurally) - ordinary exact-set idempotency/
supersession semantics (the same `_evaluate_eligibility()` whole-group mode
already uses, refactored to accept raw values instead of an
`FhD4OperationalGroup` so both modes share one implementation) still govern
whether a DIFFERENT proposed decision for the target's own exact set is
eligible, exactly as they always have.

REMAINDER SEMANTICS: `target_remainder_signal_ids` (parent minus target) is
displayed, never dispositioned, never used to infer anything about the
remaining Signals - a singleton remainder is shown exactly like a
multi-member one, and this module contains no code path capable of
creating a disposition for it.

RE-READ BEFORE WRITE (subgroup mode's own version of the same critical
safety property): immediately before persistence, freshly re-finds the
parent (refusing if its own `FhD4OperationalGroup` - membership, status,
`resolved_subgroups`, `subgroup_conflict`, everything - differs at all from
the planning-time read: this alone correctly refuses a grown, shrunk, or
disappeared parent), re-runs the global conflict scan (refusing if any
conflict now exists, even one invisible to the parent-scoped view above),
and re-evaluates the target's own exact-set eligibility fresh. Any
divergence refuses with the SAME `STATE_CHANGED_BEFORE_WRITE` reason the
whole-group path already uses - one shared vocabulary for "the state I
verified before persisting was not the state I actually persisted against."

PERSISTENCE: both modes call the SAME, single, unmodified
`record_signal_group_disposition()` - no direct ORM insert, no raw SQL, no
second persistence implementation anywhere in this module. `supersedes_id`
is `None` for a target subgroup's first exact-set disposition, or the
target's own single latest exact-set disposition id for a changed-decision
re-review - never a related subset/superset disposition's id (D4D1's own
supersession gate would reject that mismatch anyway, but this module never
even attempts it).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.signal_disposition import SIGNAL_DISPOSITION_DECISIONS
from app.services.fh_d4_disposition_resolution import (
    CONFIRMED_DISTINCT,
    CONFIRMED_SAME_REAL_WORLD_EFFORT,
    UNREVIEWED,
    FhD4DispositionResolution,
    FhD4OperationalGroup,
    SubgroupDispositionSummary,
    run_disposition_aware_fh_d4_review,
)
from app.services.signal_disposition_conflicts import (
    SignalDispositionConflict,
    find_signal_disposition_conflicts,
)
from app.services.signal_disposition_persistence import (
    MINIMUM_GROUP_CARDINALITY,
    record_signal_group_disposition,
)
from app.services.signal_disposition_resolution import (
    RelatedHistoricalDisposition,
    find_related_historical_dispositions,
    resolve_fh_d4_group_status,
)
from scripts.migrate_signal_disposition_d4d2 import inspect as inspect_signal_disposition_schema

__all__ = [
    "SCHEMA_MIGRATION_REQUIRED_BLOCKER",
    "TARGET_GROUP_NOT_CURRENT_BLOCKER",
    "PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER",
    "AttentionGroupSummary",
    "SignalDispositionReviewConfig",
    "SignalDispositionReviewResult",
    "check_schema_readiness",
    "build_engine",
    "run_review",
    "render_result",
    "main",
]

SCHEMA_MIGRATION_REQUIRED_BLOCKER = "SIGNAL_DISPOSITION_SCHEMA_MIGRATION_REQUIRED"
TARGET_GROUP_NOT_CURRENT_BLOCKER = "TARGET_GROUP_NOT_A_CURRENT_FH_D4_GROUP"
PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER = "PARENT_GROUP_NOT_A_CURRENT_ACTIVE_FH_D4_GROUP"

_ALREADY_CONFIRMED_REFUSAL = (
    "ALREADY_CONFIRMED_CURRENT_DECISION: the latest disposition for this exact Signal set already "
    "records {decision!r} - no new row is needed."
)
_AMBIGUOUS_REFUSAL = (
    "AMBIGUOUS_HISTORY_REQUIRES_EXPLICIT_RESOLUTION: independent_root_count={count} for this exact "
    "Signal set - multiple independently-recorded root dispositions exist; this CLI does not "
    "silently resolve competing history through an ordinary action."
)
_STATE_CHANGED_REFUSAL = (
    "STATE_CHANGED_BEFORE_WRITE: this exact Signal set's disposition-aware state changed between "
    "the planning read and the immediate pre-write re-read - refusing to record a disposition "
    "against state that is no longer current. Re-run inspection and try again."
)
_SUBGROUP_CONFLICT_REFUSAL = (
    "SUBGROUP_OVERLAP_CONFLICT_DETECTED: {count} conflicting disposition(s) found for this exact "
    "target subgroup - see 'conflicts' for full detail (conflicting disposition id, exact set, "
    "decision, overlap ids, relation, independent_root_count, ambiguous_history). No override is "
    "offered; record a fresh, unified disposition for the correct set instead, or resolve the "
    "conflicting history first."
)


@dataclass(frozen=True)
class AttentionGroupSummary:
    """Narrow, display-only summary of one operational group - overview
    mode only (`SignalDispositionReviewConfig.signal_ids` empty); never
    itself the input to a write. `has_related_history` is a plain boolean
    indicator (mission §30: "related history indicator"), not the full
    `RelatedHistoricalDisposition` tuple - the targeted-mode result carries
    the full detail for a specific group instead."""

    signal_ids: "tuple[int, ...]"
    airport_id: "int | None"
    status: str
    has_related_history: bool
    ambiguous_history: bool
    independent_root_count: int


@dataclass(frozen=True)
class SignalDispositionReviewConfig:
    database: Path
    signal_ids: "tuple[int, ...]" = ()
    # D4D8D: non-empty ONLY when the reviewer has explicitly opted into
    # subgroup mode - `signal_ids` then means the TARGET SUBGROUP (a
    # strict, proper subset of this parent) instead of a whole group.
    # Never inferred from `signal_ids` alone.
    parent_signal_ids: "tuple[int, ...]" = ()
    decision: "str | None" = None
    reviewer: "str | None" = None
    reason: "str | None" = None
    allow_database_write: bool = False


@dataclass(frozen=True)
class SignalDispositionReviewResult:
    """The one result shape both `main()` and the test suite consume - no
    parallel/duplicated code path between CLI output and importable,
    testable behavior, matching every write-capable script in this
    pipeline.

    Overview-mode fields (`attention_required`/`confirmed_distinct`/
    `confirmed_same_effort`) are populated only when `signal_ids` was not
    supplied. Targeted-mode fields are populated only when it was.
    """

    database: str
    schema_readiness: dict
    blockers: "tuple[str, ...]" = ()

    # Overview mode.
    attention_required: "tuple[AttentionGroupSummary, ...]" = ()
    confirmed_distinct: "tuple[AttentionGroupSummary, ...]" = ()
    confirmed_same_effort: "tuple[AttentionGroupSummary, ...]" = ()

    # Targeted mode.
    signal_ids: "tuple[int, ...]" = ()
    target_group_found: "bool | None" = None
    airport_id: "int | None" = None
    raw_summary: "str | None" = None
    status: "str | None" = None
    latest_disposition_id: "int | None" = None
    latest_decision: "str | None" = None
    latest_reviewer: "str | None" = None
    latest_reason: "str | None" = None
    independent_root_count: int = 0
    ambiguous_history: bool = False
    related_history: "tuple[RelatedHistoricalDisposition, ...]" = ()

    proposed_decision: "str | None" = None
    planned_reviewer: "str | None" = None
    planned_reason: "str | None" = None
    planned_supersedes_id: "int | None" = None
    action_eligible: "bool | None" = None
    action_refusal_reason: "str | None" = None

    written: bool = False
    written_disposition_id: "int | None" = None

    # Subgroup mode (D4D8D) - populated only when `parent_signal_ids` was
    # supplied. `signal_ids`/`status`/`latest_*`/`independent_root_count`/
    # `ambiguous_history`/`related_history`/`proposed_decision`/`planned_*`/
    # `action_eligible`/`action_refusal_reason`/`written`/
    # `written_disposition_id` above are REUSED to describe the TARGET
    # SUBGROUP's own exact-set state (never the parent's) - one shared
    # vocabulary, not a parallel field set, for "the thing this action is
    # about."
    subgroup_mode: bool = False
    parent_signal_ids: "tuple[int, ...]" = ()
    parent_found: "bool | None" = None
    parent_airport_id: "int | None" = None
    parent_raw_summary: "str | None" = None
    parent_status: "str | None" = None
    parent_resolved_subgroups: "tuple[SubgroupDispositionSummary, ...]" = ()
    parent_unresolved_remainder_signal_ids: "tuple[int, ...]" = ()
    parent_subgroup_conflict: bool = False
    target_remainder_signal_ids: "tuple[int, ...]" = ()
    conflicts: "tuple[SignalDispositionConflict, ...]" = ()


def check_schema_readiness(database: Path) -> dict:
    """Read-only, via D4D2's own already-proven `inspect()` - reused
    verbatim, never reimplemented. Migration EXECUTION (`upgrade()`/
    `downgrade()`) is never imported here or anywhere in `app/services/*`
    (see this module's own top-of-file "SCHEMA GATE" section)."""
    result = inspect_signal_disposition_schema(database)
    return {
        "tables_exist": result["tables_exist"],
        "matches_expected_schema": result["matches_expected_schema"],
        "counts": result["counts"],
        "ready": result["ready"],
    }


def build_engine(database: Path, *, writable: bool):
    """Bound to EXACTLY the resolved `database` path. Read-only SQLite URI
    mode whenever `writable` is False (every inspect/dry-run invocation) -
    even a coding mistake that tried to write would be refused at the
    driver level, matching `review_reconciliation_item.py`'s own
    `build_engine()` precedent verbatim."""
    resolved = database.resolve()
    if writable:
        return create_engine(f"sqlite:///{resolved}", future=True)
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def _validate_config(config: SignalDispositionReviewConfig) -> None:
    """Argument-combination misuse invalid regardless of database state -
    raised before any database is opened. Subgroup-mode target-subset
    validation (D4D8D) is pure set-shape checking on the two id
    collections as given - no DB access needed, so it can never "silently
    span multiple live raw groups" (a target is only ever validated
    relative to the ONE parent set the reviewer explicitly named)."""
    if config.parent_signal_ids:
        deduplicated_parent = tuple(sorted(set(config.parent_signal_ids)))
        if len(deduplicated_parent) < MINIMUM_GROUP_CARDINALITY:
            raise ValueError(
                f"--parent-signal-id must name at least {MINIMUM_GROUP_CARDINALITY} distinct Signal ids, "
                f"got {config.parent_signal_ids!r}"
            )
        if not config.signal_ids:
            raise ValueError(
                "subgroup mode (--parent-signal-id) requires --signal-id to name the target subgroup"
            )
        deduplicated_target = tuple(sorted(set(config.signal_ids)))
        if len(deduplicated_target) < MINIMUM_GROUP_CARDINALITY:
            raise ValueError(
                f"--signal-id must name at least {MINIMUM_GROUP_CARDINALITY} distinct Signal ids, "
                f"got {config.signal_ids!r}"
            )
        if not (set(deduplicated_target) < set(deduplicated_parent)):
            raise ValueError(
                "target subgroup (--signal-id) must be a STRICT, PROPER subset of the parent group "
                f"(--parent-signal-id) - target={deduplicated_target!r}, parent={deduplicated_parent!r}"
            )
    elif config.signal_ids:
        deduplicated = tuple(sorted(set(config.signal_ids)))
        if len(deduplicated) < MINIMUM_GROUP_CARDINALITY:
            raise ValueError(
                f"--signal-id must name at least {MINIMUM_GROUP_CARDINALITY} distinct Signal ids, "
                f"got {config.signal_ids!r}"
            )
    elif config.decision is not None:
        raise ValueError("--decision requires --signal-id (at least 2) to name a target group")

    if config.decision is not None and config.decision not in SIGNAL_DISPOSITION_DECISIONS:
        raise ValueError(f"--decision must be one of {SIGNAL_DISPOSITION_DECISIONS!r}, got {config.decision!r}")

    if config.decision is not None:
        # Required whenever a decision is PROPOSED, not merely at write
        # time - dry-run output must be able to preview exactly what would
        # be persisted (this mission's own §13), unlike
        # review_reconciliation_item.py's own narrower precedent where a
        # dry-run's fingerprint does not depend on reviewer identity at all.
        if not config.reviewer or not config.reviewer.strip():
            raise ValueError("--reviewer is required when --decision is supplied")
        if not config.reason or not config.reason.strip():
            raise ValueError("--reason is required when --decision is supplied")

    if config.allow_database_write and config.decision is None:
        raise ValueError("--allow-database-write requires --decision (and --signal-id)")


def _find_group(
    resolution: FhD4DispositionResolution, canonical_key: "tuple[int, ...]",
) -> "FhD4OperationalGroup | None":
    """The four primary D4D4 buckets are pairwise disjoint (D4D4's own
    accounting invariant) - at most one match can exist across all of
    them."""
    for bucket in (
        resolution.active_findings, resolution.confirmed_distinct,
        resolution.confirmed_same_effort, resolution.ambiguous_groups,
    ):
        for group in bucket:
            if group.signal_ids == canonical_key:
                return group
    return None


def _find_active_parent(
    resolution: FhD4DispositionResolution, canonical_parent_key: "tuple[int, ...]",
) -> "FhD4OperationalGroup | None":
    """Subgroup mode's own parent lookup (D4D8D) - unlike `_find_group()`
    (which searches all four primary buckets for whole-group mode), a valid
    subgroup-review parent must be found SPECIFICALLY in `active_findings`
    - the only bucket D4D8B ever computes `resolved_subgroups`/
    `unresolved_remainder_signal_ids`/`subgroup_conflict` for at all
    (exact-set precedence, design doc §10). A parent already resolved at
    the whole-group level or already `ambiguous_groups` is refused - see
    this module's own top-of-file "PARENT VALIDITY" section."""
    for group in resolution.active_findings:
        if group.signal_ids == canonical_parent_key:
            return group
    return None


def _summarize(groups: "tuple[FhD4OperationalGroup, ...]") -> "tuple[AttentionGroupSummary, ...]":
    return tuple(
        AttentionGroupSummary(
            signal_ids=g.signal_ids, airport_id=g.airport_id, status=g.status,
            has_related_history=bool(g.related_history), ambiguous_history=g.ambiguous_history,
            independent_root_count=g.independent_root_count,
        )
        for g in groups
    )


def _evaluate_eligibility(
    *, status: str, ambiguous_history: bool, independent_root_count: int,
    latest_disposition_id: "int | None", decision: str,
) -> "tuple[bool, str | None, int | None]":
    """Returns (eligible, refusal_reason, planned_supersedes_id).

    Ambiguous history (independent_root_count > 1) refuses unconditionally
    (this mission's own §16/§18 - fail closed, no silent competing-root
    resolution). Otherwise: UNREVIEWED plans supersedes_id=None; a
    resolved group requesting the SAME decision it already records is
    idempotent (refused, no new row); a resolved group requesting a
    DIFFERENT decision supersedes its own single latest exact-set
    disposition.

    D4D8D: takes raw values rather than an `FhD4OperationalGroup` so this
    identical logic serves BOTH whole-group mode (a raw FH-D4 finding's own
    exact-set state) and subgroup mode (a target subgroup's own exact-set
    state, from D3's `SignalDispositionStatus` - no `FhD4OperationalGroup`
    exists for an arbitrary subgroup) without duplicating the
    implementation or relying on duck typing."""
    if ambiguous_history:
        return False, _AMBIGUOUS_REFUSAL.format(count=independent_root_count), None
    if status == UNREVIEWED:
        return True, None, None
    if status == CONFIRMED_DISTINCT and decision == "DISTINCT":
        return False, _ALREADY_CONFIRMED_REFUSAL.format(decision="DISTINCT"), None
    if status == CONFIRMED_SAME_REAL_WORLD_EFFORT and decision == "SAME_REAL_WORLD_EFFORT":
        return False, _ALREADY_CONFIRMED_REFUSAL.format(decision="SAME_REAL_WORLD_EFFORT"), None
    return True, None, latest_disposition_id


def run_review(config: SignalDispositionReviewConfig) -> SignalDispositionReviewResult:
    """The one function that does the work (schema check, read, validate,
    optionally write) and returns a single, importable result."""
    _validate_config(config)
    database_str = str(config.database.resolve())
    schema = check_schema_readiness(config.database)
    if not schema["ready"]:
        return SignalDispositionReviewResult(
            database=database_str, schema_readiness=schema, blockers=(SCHEMA_MIGRATION_REQUIRED_BLOCKER,),
        )

    engine = build_engine(config.database, writable=config.allow_database_write)
    try:
        with Session(engine) as session:
            if config.parent_signal_ids:
                return _run_subgroup_review(config, session, database_str, schema)

            resolution = run_disposition_aware_fh_d4_review(session)

            if not config.signal_ids:
                session.rollback()
                return SignalDispositionReviewResult(
                    database=database_str, schema_readiness=schema,
                    attention_required=_summarize(resolution.attention_required),
                    confirmed_distinct=_summarize(resolution.confirmed_distinct),
                    confirmed_same_effort=_summarize(resolution.confirmed_same_effort),
                )

            canonical_key = tuple(sorted(set(config.signal_ids)))
            group = _find_group(resolution, canonical_key)
            if group is None:
                session.rollback()
                return SignalDispositionReviewResult(
                    database=database_str, schema_readiness=schema,
                    blockers=(TARGET_GROUP_NOT_CURRENT_BLOCKER,),
                    signal_ids=canonical_key, target_group_found=False,
                )

            base_kwargs = dict(
                database=database_str, schema_readiness=schema,
                signal_ids=canonical_key, target_group_found=True,
                airport_id=group.airport_id, raw_summary=group.raw_finding.summary,
                status=group.status, latest_disposition_id=group.latest_disposition_id,
                latest_decision=group.decision, latest_reviewer=group.reviewer, latest_reason=group.reason,
                independent_root_count=group.independent_root_count, ambiguous_history=group.ambiguous_history,
                related_history=group.related_history,
            )

            if config.decision is None:
                session.rollback()
                return SignalDispositionReviewResult(**base_kwargs)

            base_kwargs = dict(
                **base_kwargs, proposed_decision=config.decision,
                planned_reviewer=config.reviewer, planned_reason=config.reason,
            )

            eligible, refusal, planned_supersedes_id = _evaluate_eligibility(
                status=group.status, ambiguous_history=group.ambiguous_history,
                independent_root_count=group.independent_root_count,
                latest_disposition_id=group.latest_disposition_id, decision=config.decision,
            )
            if not eligible:
                session.rollback()
                return SignalDispositionReviewResult(**base_kwargs, action_eligible=False, action_refusal_reason=refusal)

            if not config.allow_database_write:
                session.rollback()
                return SignalDispositionReviewResult(
                    **base_kwargs, action_eligible=True, planned_supersedes_id=planned_supersedes_id,
                )

            # RE-READ BEFORE WRITE - critical safety property (this
            # mission's own §14). Never trust the read above at write
            # time: recompute fresh, immediately before persistence, and
            # refuse if the target group's state changed or disappeared.
            fresh_resolution = run_disposition_aware_fh_d4_review(session)
            fresh_group = _find_group(fresh_resolution, canonical_key)
            if fresh_group is None or fresh_group != group:
                session.rollback()
                return SignalDispositionReviewResult(
                    **base_kwargs, action_eligible=False, action_refusal_reason=_STATE_CHANGED_REFUSAL,
                )
            fresh_eligible, fresh_refusal, fresh_supersedes_id = _evaluate_eligibility(
                status=fresh_group.status, ambiguous_history=fresh_group.ambiguous_history,
                independent_root_count=fresh_group.independent_root_count,
                latest_disposition_id=fresh_group.latest_disposition_id, decision=config.decision,
            )
            if not fresh_eligible:
                session.rollback()
                return SignalDispositionReviewResult(**base_kwargs, action_eligible=False, action_refusal_reason=fresh_refusal)

            try:
                disposition = record_signal_group_disposition(
                    session, signal_ids=canonical_key, decision=config.decision,
                    reviewer=config.reviewer, reason=config.reason, supersedes_id=fresh_supersedes_id,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

            return SignalDispositionReviewResult(
                **base_kwargs, action_eligible=True, planned_supersedes_id=fresh_supersedes_id,
                written=True, written_disposition_id=disposition.id,
            )
    finally:
        engine.dispose()


def _run_subgroup_review(
    config: SignalDispositionReviewConfig, session: Session, database_str: str, schema: dict,
) -> SignalDispositionReviewResult:
    """Subgroup mode's own inspect/dry-run/write path (D4D8D) - called from
    `run_review()` only when `config.parent_signal_ids` is non-empty (input
    shape already validated by `_validate_config()`). Mirrors the
    whole-group path's own structure exactly (schema gate already checked
    by the caller; one session; rollback on every non-write return; exactly
    one commit on a successful write) - see this module's own top-of-file
    "SUBGROUP MODE" section for the full contract.
    """
    canonical_parent = tuple(sorted(set(config.parent_signal_ids)))
    canonical_target = tuple(sorted(set(config.signal_ids)))

    resolution = run_disposition_aware_fh_d4_review(session)
    parent = _find_active_parent(resolution, canonical_parent)
    if parent is None:
        session.rollback()
        return SignalDispositionReviewResult(
            database=database_str, schema_readiness=schema,
            blockers=(PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER,),
            subgroup_mode=True, parent_signal_ids=canonical_parent, parent_found=False,
            signal_ids=canonical_target,
        )

    parent_kwargs = dict(
        subgroup_mode=True, parent_signal_ids=canonical_parent, parent_found=True,
        parent_airport_id=parent.airport_id, parent_raw_summary=parent.raw_finding.summary,
        parent_status=parent.status, parent_resolved_subgroups=parent.resolved_subgroups,
        parent_unresolved_remainder_signal_ids=parent.unresolved_remainder_signal_ids,
        parent_subgroup_conflict=parent.subgroup_conflict,
    )
    target_remainder = tuple(sorted(set(canonical_parent) - set(canonical_target)))

    target_status = resolve_fh_d4_group_status(session, canonical_target)
    target_related = find_related_historical_dispositions(session, canonical_target)
    target_ambiguous = target_status.independent_root_count > 1
    # exclude_exact_set relies on find_signal_disposition_conflicts()'s own
    # documented default (True) - the target's own exact-set history is
    # never itself an overlap conflict (design doc §7, module docstring's
    # own "CONFLICT GUARD" section).
    conflicts = find_signal_disposition_conflicts(session, signal_ids=canonical_target)

    base_kwargs = dict(
        database=database_str, schema_readiness=schema, **parent_kwargs,
        signal_ids=canonical_target, target_remainder_signal_ids=target_remainder,
        status=target_status.status, latest_disposition_id=target_status.latest_disposition_id,
        latest_decision=target_status.decision, latest_reviewer=target_status.reviewer,
        latest_reason=target_status.reason, independent_root_count=target_status.independent_root_count,
        ambiguous_history=target_ambiguous, related_history=target_related, conflicts=conflicts,
    )

    if config.decision is None:
        session.rollback()
        return SignalDispositionReviewResult(**base_kwargs)

    base_kwargs = dict(
        **base_kwargs, proposed_decision=config.decision,
        planned_reviewer=config.reviewer, planned_reason=config.reason,
    )

    # CONFLICT GUARD - mandatory, no override (module docstring's own
    # "CONFLICT GUARD" section). Checked BEFORE exact-set eligibility so a
    # genuine overlap is never masked by an idempotency/supersession result.
    if conflicts:
        session.rollback()
        return SignalDispositionReviewResult(
            **base_kwargs, action_eligible=False,
            action_refusal_reason=_SUBGROUP_CONFLICT_REFUSAL.format(count=len(conflicts)),
        )

    eligible, refusal, planned_supersedes_id = _evaluate_eligibility(
        status=target_status.status, ambiguous_history=target_ambiguous,
        independent_root_count=target_status.independent_root_count,
        latest_disposition_id=target_status.latest_disposition_id, decision=config.decision,
    )
    if not eligible:
        session.rollback()
        return SignalDispositionReviewResult(**base_kwargs, action_eligible=False, action_refusal_reason=refusal)

    if not config.allow_database_write:
        session.rollback()
        return SignalDispositionReviewResult(
            **base_kwargs, action_eligible=True, planned_supersedes_id=planned_supersedes_id,
        )

    # RE-READ BEFORE WRITE - subgroup mode's own version of the same
    # critical safety property (module docstring's own "RE-READ BEFORE
    # WRITE" section). Re-verifies the parent's ENTIRE FhD4OperationalGroup
    # (membership, status, resolved_subgroups, subgroup_conflict - a grown,
    # shrunk, or disappeared parent, OR a newly-appeared/changed subgroup
    # fact for the SAME parent, all fail this comparison), a fresh global
    # conflict scan (catches a new conflict invisible to the parent-scoped
    # view above), and the target's own exact-set eligibility fresh.
    fresh_resolution = run_disposition_aware_fh_d4_review(session)
    fresh_parent = _find_active_parent(fresh_resolution, canonical_parent)
    if fresh_parent is None or fresh_parent != parent:
        session.rollback()
        return SignalDispositionReviewResult(
            **base_kwargs, action_eligible=False, action_refusal_reason=_STATE_CHANGED_REFUSAL,
        )

    fresh_conflicts = find_signal_disposition_conflicts(session, signal_ids=canonical_target)
    if fresh_conflicts:
        session.rollback()
        return SignalDispositionReviewResult(
            **base_kwargs, action_eligible=False,
            action_refusal_reason=_SUBGROUP_CONFLICT_REFUSAL.format(count=len(fresh_conflicts)),
        )

    fresh_target_status = resolve_fh_d4_group_status(session, canonical_target)
    fresh_target_ambiguous = fresh_target_status.independent_root_count > 1
    if (fresh_target_status.status, fresh_target_status.latest_disposition_id, fresh_target_ambiguous) != (
        target_status.status, target_status.latest_disposition_id, target_ambiguous
    ):
        session.rollback()
        return SignalDispositionReviewResult(
            **base_kwargs, action_eligible=False, action_refusal_reason=_STATE_CHANGED_REFUSAL,
        )
    fresh_eligible, fresh_refusal, fresh_supersedes_id = _evaluate_eligibility(
        status=fresh_target_status.status, ambiguous_history=fresh_target_ambiguous,
        independent_root_count=fresh_target_status.independent_root_count,
        latest_disposition_id=fresh_target_status.latest_disposition_id, decision=config.decision,
    )
    if not fresh_eligible:
        session.rollback()
        return SignalDispositionReviewResult(**base_kwargs, action_eligible=False, action_refusal_reason=fresh_refusal)

    try:
        disposition = record_signal_group_disposition(
            session, signal_ids=canonical_target, decision=config.decision,
            reviewer=config.reviewer, reason=config.reason, supersedes_id=fresh_supersedes_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return SignalDispositionReviewResult(
        **base_kwargs, action_eligible=True, planned_supersedes_id=fresh_supersedes_id,
        written=True, written_disposition_id=disposition.id,
    )


# ---------------------------------------------------------------------------
# Human-readable report rendering - text formatting only, no new data.
# ---------------------------------------------------------------------------


def _mode_label(result: SignalDispositionReviewResult) -> str:
    """Critical-review addition: an explicit, unmissable mode label at the
    top of every rendered result - the operator must never have to infer
    from the presence/absence of other fields whether a write actually
    happened. D4D8D: prefixed with "SUBGROUP " whenever `subgroup_mode` is
    set, so PARENT RAW GROUP vs TARGET SUBGROUP display can never be
    confused with ordinary whole-group mode."""
    prefix = "SUBGROUP " if result.subgroup_mode else ""
    if result.written:
        return f"{prefix}WRITE (committed)"
    if result.proposed_decision is not None:
        return f"{prefix}DRY RUN (no write)"
    return f"{prefix}INSPECT (read-only)"


def _render_subgroup_result(result: SignalDispositionReviewResult) -> str:
    """D4D8D's own rendering path - self-contained, never touches or
    reinterprets the whole-group rendering below. Always displays PARENT RAW
    GROUP and TARGET SUBGROUP as clearly separate, labeled sections (module
    docstring's own "DISPLAY / EXPLAINABILITY" requirement) plus the
    remainder and the full D4D8C conflict scan - never hidden, never
    summarized away."""
    lines: "list[str]" = [f"Database: {result.database}", f"Mode: {_mode_label(result)}"]
    lines.append(f"PARENT RAW FH-D4 GROUP: {list(result.parent_signal_ids)}")
    if result.parent_found is False:
        lines.append(f"  NOT a currently-active FH-D4 group - refused: {', '.join(result.blockers)}")
        return "\n".join(lines) + "\n"

    lines.append(f"  airport_id: {result.parent_airport_id}")
    lines.append(f"  raw FH-D4 summary: {result.parent_raw_summary}")
    lines.append(f"  parent status: {result.parent_status}")
    lines.append("  existing resolved subgroups for this parent (D4D8B):")
    if not result.parent_resolved_subgroups:
        lines.append("    (none)")
    for s in result.parent_resolved_subgroups:
        lines.append(
            f"    signal_ids={list(s.signal_ids)} decision={s.decision} "
            f"disposition_id={s.latest_disposition_id} "
            f"ambiguous_history={'yes' if s.ambiguous_history else 'no'}"
        )
    lines.append(
        f"  parent unresolved remainder (across ALL existing subgroups): "
        f"{list(result.parent_unresolved_remainder_signal_ids)}"
    )
    lines.append(
        f"  parent subgroup_conflict (pre-existing overlap among the parent's OWN "
        f"subgroups, D4D8B): {result.parent_subgroup_conflict}"
    )

    lines.append("")
    lines.append(f"TARGET SUBGROUP: {list(result.signal_ids)}")
    lines.append(f"  REMAINDER (parent minus this target): {list(result.target_remainder_signal_ids)}")
    lines.append(f"  target exact-set status: {result.status}")
    lines.append(f"  target latest_disposition_id: {result.latest_disposition_id}")
    lines.append(f"  target latest_decision: {result.latest_decision}")
    lines.append(f"  target latest_reviewer: {result.latest_reviewer}")
    lines.append(f"  target latest_reason: {result.latest_reason}")
    lines.append(f"  target independent_root_count: {result.independent_root_count}")
    lines.append(f"  target ambiguous_history: {result.ambiguous_history}")
    if result.related_history:
        lines.append("  target related_history:")
        for r in result.related_history:
            lines.append(
                f"    disposition_id={r.disposition_id} relation={r.relation} "
                f"member_signal_ids={list(r.member_signal_ids)} decision={r.decision}"
            )
    else:
        lines.append("  target related_history: (none)")

    lines.append("")
    lines.append(f"CONFLICTS (D4D8C conflict scan): {len(result.conflicts)} found")
    if not result.conflicts:
        lines.append("  (none)")
    for c in result.conflicts:
        lines.append(
            f"  conflicting_disposition_id={c.conflicting_disposition_id} "
            f"conflicting_signal_ids={list(c.conflicting_signal_ids)} "
            f"conflicting_decision={c.conflicting_decision} relation={c.relation} "
            f"overlap_signal_ids={list(c.overlap_signal_ids)} "
            f"independent_root_count={c.independent_root_count} "
            f"ambiguous_history={c.ambiguous_history}"
        )

    if result.proposed_decision is not None:
        lines.append("")
        lines.append(f"Proposed decision: {result.proposed_decision}")
        lines.append(f"  reviewer: {result.planned_reviewer}")
        lines.append(f"  reason: {result.planned_reason}")
        lines.append(f"  planned_supersedes_id: {result.planned_supersedes_id}")
        lines.append(f"  eligible: {result.action_eligible}")
        if result.action_refusal_reason:
            lines.append(f"  refused: {result.action_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: SignalDisposition #{result.written_disposition_id}")
        elif result.action_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this disposition)")

    return "\n".join(lines) + "\n"


def render_result(result: SignalDispositionReviewResult) -> str:
    if result.blockers and result.target_group_found is not False and result.parent_found is not False:
        return (
            f"Database: {result.database}\n"
            f"BLOCKED: {', '.join(result.blockers)}\n"
            f"schema_readiness: {result.schema_readiness}\n"
        )

    if result.subgroup_mode:
        return _render_subgroup_result(result)

    lines: "list[str]" = [f"Database: {result.database}"]
    lines.append(f"Mode: {_mode_label(result)}")

    if not result.signal_ids:
        lines.append("")
        lines.append("ATTENTION REQUIRED FH-D4 GROUPS")
        if not result.attention_required:
            lines.append("  (none)")
        for g in result.attention_required:
            lines.append(
                f"  signals={list(g.signal_ids)} airport_id={g.airport_id} status={g.status} "
                f"related_history={'yes' if g.has_related_history else 'no'} "
                f"ambiguous={'yes' if g.ambiguous_history else 'no'} "
                f"independent_root_count={g.independent_root_count}"
            )
        lines.append("")
        lines.append("CONFIRMED DISTINCT (resolved)")
        if not result.confirmed_distinct:
            lines.append("  (none)")
        for g in result.confirmed_distinct:
            lines.append(f"  signals={list(g.signal_ids)} airport_id={g.airport_id}")
        lines.append("")
        lines.append("CONFIRMED SAME REAL-WORLD EFFORT (resolved)")
        if not result.confirmed_same_effort:
            lines.append("  (none)")
        for g in result.confirmed_same_effort:
            lines.append(f"  signals={list(g.signal_ids)} airport_id={g.airport_id}")
        return "\n".join(lines) + "\n"

    lines.append(f"Target Signal group: {list(result.signal_ids)}")
    if result.target_group_found is False:
        lines.append(f"  NOT a current FH-D4 group - refused: {', '.join(result.blockers)}")
        return "\n".join(lines) + "\n"

    lines.append(f"  airport_id: {result.airport_id}")
    lines.append(f"  raw FH-D4 summary: {result.raw_summary}")
    lines.append(f"  status: {result.status}")
    lines.append(f"  latest_disposition_id: {result.latest_disposition_id}")
    lines.append(f"  latest_decision: {result.latest_decision}")
    lines.append(f"  latest_reviewer: {result.latest_reviewer}")
    lines.append(f"  latest_reason: {result.latest_reason}")
    lines.append(f"  independent_root_count: {result.independent_root_count}")
    lines.append(f"  ambiguous_history: {result.ambiguous_history}")
    if result.related_history:
        lines.append("  related_history:")
        for r in result.related_history:
            lines.append(
                f"    disposition_id={r.disposition_id} relation={r.relation} "
                f"member_signal_ids={list(r.member_signal_ids)} decision={r.decision}"
            )
    else:
        lines.append("  related_history: (none)")

    if result.proposed_decision is not None:
        lines.append("")
        lines.append(f"Proposed decision: {result.proposed_decision}")
        lines.append(f"  reviewer: {result.planned_reviewer}")
        lines.append(f"  reason: {result.planned_reason}")
        lines.append(f"  planned_supersedes_id: {result.planned_supersedes_id}")
        lines.append(f"  eligible: {result.action_eligible}")
        if result.action_refusal_reason:
            lines.append(f"  refused: {result.action_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: SignalDisposition #{result.written_disposition_id}")
        elif result.action_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this disposition)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database, read-only unless --allow-database-write is also given. "
        "No default - never the real production database by accident.",
    )
    parser.add_argument(
        "--signal-id", type=int, action="append", dest="signal_ids", default=None,
        help="Repeatable. At least 2 distinct ids name the target FH-D4 Signal group. "
        "Omit entirely for the overview (all attention-required/resolved groups). "
        "In SUBGROUP MODE (--parent-signal-id also given), names the TARGET SUBGROUP instead.",
    )
    parser.add_argument(
        "--parent-signal-id", type=int, action="append", dest="parent_signal_ids", default=None,
        help="Repeatable. SUBGROUP MODE: at least 2 distinct ids naming the PARENT raw FH-D4 group. "
        "When supplied, --signal-id must name a strict, proper subset of this parent (the TARGET "
        "SUBGROUP) rather than a whole group. Never inferred - subgroup mode is always explicit.",
    )
    parser.add_argument("--decision", choices=SIGNAL_DISPOSITION_DECISIONS, default=None)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = SignalDispositionReviewConfig(
        database=args.database, signal_ids=tuple(args.signal_ids or ()),
        parent_signal_ids=tuple(args.parent_signal_ids or ()),
        decision=args.decision, reviewer=args.reviewer, reason=args.reason,
        allow_database_write=args.allow_database_write,
    )
    result = run_review(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_decision is not None and not result.action_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
