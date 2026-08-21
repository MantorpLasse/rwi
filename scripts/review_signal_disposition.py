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

NO STATIC EXPORT: this CLI has nothing to do with FHC4/the static site -
no `--output-dir`, no `run_fleet_presentation_check()` import, no site
mutation of any kind.
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
    run_disposition_aware_fh_d4_review,
)
from app.services.signal_disposition_persistence import (
    MINIMUM_GROUP_CARDINALITY,
    record_signal_group_disposition,
)
from app.services.signal_disposition_resolution import RelatedHistoricalDisposition
from scripts.migrate_signal_disposition_d4d2 import inspect as inspect_signal_disposition_schema

__all__ = [
    "SCHEMA_MIGRATION_REQUIRED_BLOCKER",
    "TARGET_GROUP_NOT_CURRENT_BLOCKER",
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
    raised before any database is opened."""
    if config.signal_ids:
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
    group: FhD4OperationalGroup, decision: str,
) -> "tuple[bool, str | None, int | None]":
    """Returns (eligible, refusal_reason, planned_supersedes_id).

    Ambiguous history (independent_root_count > 1) refuses unconditionally
    (this mission's own §16/§18 - fail closed, no silent competing-root
    resolution). Otherwise: UNREVIEWED plans supersedes_id=None; a
    resolved group requesting the SAME decision it already records is
    idempotent (refused, no new row); a resolved group requesting a
    DIFFERENT decision supersedes its own single latest exact-set
    disposition."""
    if group.ambiguous_history:
        return False, _AMBIGUOUS_REFUSAL.format(count=group.independent_root_count), None
    if group.status == UNREVIEWED:
        return True, None, None
    if group.status == CONFIRMED_DISTINCT and decision == "DISTINCT":
        return False, _ALREADY_CONFIRMED_REFUSAL.format(decision="DISTINCT"), None
    if group.status == CONFIRMED_SAME_REAL_WORLD_EFFORT and decision == "SAME_REAL_WORLD_EFFORT":
        return False, _ALREADY_CONFIRMED_REFUSAL.format(decision="SAME_REAL_WORLD_EFFORT"), None
    return True, None, group.latest_disposition_id


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

            eligible, refusal, planned_supersedes_id = _evaluate_eligibility(group, config.decision)
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
            fresh_eligible, fresh_refusal, fresh_supersedes_id = _evaluate_eligibility(fresh_group, config.decision)
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


# ---------------------------------------------------------------------------
# Human-readable report rendering - text formatting only, no new data.
# ---------------------------------------------------------------------------


def _mode_label(result: SignalDispositionReviewResult) -> str:
    """Critical-review addition: an explicit, unmissable mode label at the
    top of every rendered result - the operator must never have to infer
    from the presence/absence of other fields whether a write actually
    happened."""
    if result.written:
        return "WRITE (committed)"
    if result.proposed_decision is not None:
        return "DRY RUN (no write)"
    return "INSPECT (read-only)"


def render_result(result: SignalDispositionReviewResult) -> str:
    if result.blockers and result.target_group_found is not False:
        return (
            f"Database: {result.database}\n"
            f"BLOCKED: {', '.join(result.blockers)}\n"
            f"schema_readiness: {result.schema_readiness}\n"
        )

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
        "Omit entirely for the overview (all attention-required/resolved groups).",
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
