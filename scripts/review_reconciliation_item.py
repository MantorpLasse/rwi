"""Human reviewer-action CLI for existing-Signal reconciliation
(docs/architecture/existing-signal-reconciliation-r4e-reviewer-action-cli-report.md,
R4E of docs/architecture/existing-signal-reconciliation-r4-human-resolution-
design.md's own S20 roadmap - see R4D's own
docs/architecture/existing-signal-reconciliation-r4d-review-queue-report.md
for the read-only view this CLI acts on top of).

    python -m scripts.review_reconciliation_item --database data/runway_safe.db --source-assertion-id 222
        -> pure inspection: current SourceAssertion/latest-action/fresh
           reconciliation state, no --action given, never writes

    python -m scripts.review_reconciliation_item --database ... --source-assertion-id 222 \\
        --action CONFIRM_DISTINCT_SIGNAL
        -> dry-run of a specific action: shows whether it is currently
           eligible and, if so, the exact fingerprint a subsequent write
           would need to supply. Still never writes.

    python -m scripts.review_reconciliation_item --database ... --source-assertion-id 222 \\
        --action CONFIRM_DISTINCT_SIGNAL --confirm-current-plan <fingerprint printed above> \\
        --reviewer human:you --reason "..." --allow-database-write
        -> the only invocation shape that writes: recomputes reconciliation
           fresh ONE MORE TIME, refuses if anything changed since the dry-run
           above, and only then records ONE ReviewerAction row.

THIS IS THE FIRST WRITE PATH THIS PROJECT HAS EVER EXPOSED THROUGH A
COMMITTED SCRIPT for `record_reviewer_action()` (confirmed by inspection
during the R4 design task - every real `ReviewerAction` recorded so far was
written via one-off interactive Python). It is deliberately narrow: exactly
five action values, no free-form model field acceptance, and no code path
that constructs a `Signal` or calls `create_signal_from_approved_review()` -
this CLI resolves RECONCILIATION only. Signal creation from an approved,
resolved review remains a wholly separate, already-existing, unmodified
operation this script never touches.

READ-FIRST / RECOMPUTE-FIRST, ALWAYS: before validating any requested
action, and again immediately before any write, this script loads the
`SourceAssertion`, its latest `ReviewerAction`, and runs FRESH R2 candidate
discovery and R1 reconciliation - exactly the same calls
`app.services.human_review_reconciliation.list_reconciliation_review_items()`
(R4D) and `create_signal_from_approved_review()` (R3/R4C) already make, with
the identical human-selected-context boundary (`category=None`,
`reference_year=None`, empty `claims`) R4D established, so the fingerprint
this script computes is always the SAME one a human would have seen in the
R4D queue view for the identical state - never a second, drifting
computation. No anchor rule, compatibility rule, candidate-discovery SQL,
canonicalization, or hashing logic is reimplemented here; R1/R2/R4A remain
the only truth sources, called verbatim.

FINGERPRINT NEVER SUPPLIED BY THE CALLER, ONLY CONFIRMED: this script does
not accept a `--fingerprint` argument that gets persisted. For
`CONFIRM_DISTINCT_SIGNAL`, the value actually written is always this
script's own freshly recomputed `compute_reconciliation_fingerprint()`
result, taken immediately before the write, in the same read that validates
everything else - never a value read from the command line and trusted.
`--confirm-current-plan` is a HANDSHAKE input only: a human-supplied value
that must exactly equal the freshly recomputed fingerprint before the write
proceeds, letting a human confirm "yes, I am looking at the same state I
was shown a moment ago," but the confirmed digit string is never itself the
persisted value.

WRITE AUTHORIZATION: `--allow-database-write` is required for any write,
never defaulted, matching the identical convention every migration script
in this project already establishes. `--database` has NO default here
(unlike the read-only R4D CLI, which may safely default since it can never
write) - deliberately, since this is the first write-capable CLI this
project has ever shipped; every invocation must be explicit about which
database it targets, whether reading or writing. No `app.database.SessionLocal`,
no process-global engine, no silently-resolved production database path.

NO BACKUP: unlike every migration script in this project, this CLI does not
back up the database before writing. Backups here are reserved for
irreversible SCHEMA changes (a full-table rebuild, a dropped column) - this
script only ever appends at most one `ReviewerAction` row (and, for
`MARK_DUPLICATE`, sets one already-nullable `SourceAssertion.signal_id`
column via the existing, unmodified `link_source_assertion_to_duplicate_signal()`),
both fully reversible by recording a further superseding action, matching
the append-only audit discipline this entire pipeline already relies on
instead of file-level backups for ordinary data writes.

SCHEMA GATE (mirrors R4D's own, reused rather than reimplemented): before
any ORM query, this script inspects the target database's raw schema via
the existing migration scripts' own read-only `inspect()` functions and
refuses with `RECONCILIATION_REVIEW_SCHEMA_MIGRATION_REQUIRED` if the Slice
4, Slice 7, or R4B (`reviewer_actions.reconciliation_fingerprint`) columns
are missing - the identical three-layer check R4D's own schema-gate fix
established, reused verbatim (same imported `inspect()` functions), never
duplicated.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import SourceAssertion
from app.services.effective_identity_guard_decision import resolve_effective_identity_guard_decision
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)
from app.services.existing_signal_reconciliation_review import (
    build_reconciliation_review_plan,
    compute_reconciliation_fingerprint,
)
from app.services.governed_signal_creation import link_source_assertion_to_duplicate_signal
from app.services.reviewer_action_persistence import get_latest_reviewer_action, record_reviewer_action
from scripts.migrate_intelligence_review_persistence_slice4 import inspect as inspect_intelligence_review_schema
from scripts.migrate_promotion_policy_persistence_slice7 import inspect as inspect_promotion_policy_schema
from scripts.migrate_reconciliation_confirmation_slice_r4b import (
    inspect as inspect_reconciliation_confirmation_schema,
)

SCHEMA_MIGRATION_REQUIRED_BLOCKER = "RECONCILIATION_REVIEW_SCHEMA_MIGRATION_REQUIRED"
SOURCE_ASSERTION_NOT_FOUND_BLOCKER = "SOURCE_ASSERTION_NOT_FOUND"

POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH

# The five values this CLI accepts for --action. CONFIRM_DISTINCT_SIGNAL and
# MARK_DUPLICATE are the two reconciliation-specific actions this slice's
# own mission centers on; DEFER/NEEDS_MORE_EVIDENCE/REJECT_SIGNAL are
# included because the reviewed design doc's own Section 12 "Reviewer
# presentation" explicitly maps all five onto this exact presentation
# surface ("DEFER" -> DEFER, "NEEDS MORE EVIDENCE" -> NEEDS_MORE_EVIDENCE,
# "REJECT" -> REJECT_SIGNAL, alongside the two reconciliation actions) - not
# a speculative addition. Each of the three generic actions is a thin,
# unconditional passthrough to record_reviewer_action() (no fingerprint, no
# duplicate target, no extra validation this script invents) - the safety
# surface is not broadened by including them, only the vocabulary of what a
# human may record through this one script. APPROVE_SIGNAL is deliberately
# NOT offered here - it is the precondition this CLI's own reconciliation
# actions assume already happened via a separate, existing governed-review
# path, not something this reconciliation-focused tool should also grant.
RECONCILIATION_ACTIONS = ("CONFIRM_DISTINCT_SIGNAL", "MARK_DUPLICATE")
GENERIC_ACTIONS = ("DEFER", "NEEDS_MORE_EVIDENCE", "REJECT_SIGNAL")
VALID_ACTIONS = RECONCILIATION_ACTIONS + GENERIC_ACTIONS


@dataclass(frozen=True)
class ReviewConfig:
    database: Path
    source_assertion_id: int
    action: "str | None" = None
    duplicate_of_signal_id: "int | None" = None
    confirm_current_plan: "str | None" = None
    reviewer: "str | None" = None
    reason: "str | None" = None
    allow_database_write: bool = False


@dataclass(frozen=True)
class ReviewResult:
    """The one result shape both `main()` and the test suite consume - no
    parallel/duplicated code path between CLI output and importable,
    testable behavior, matching every other script in this pipeline."""

    database: str
    schema_readiness: dict
    blockers: "tuple[str, ...]" = ()

    source_assertion_id: "int | None" = None
    latest_action: "str | None" = None
    linked_signal_id: "int | None" = None

    reconciliation_outcome: "str | None" = None
    candidate_signal_ids: "tuple[int, ...]" = ()
    anchor_reasons: "tuple[str, ...]" = ()
    current_fingerprint: "str | None" = None
    stored_fingerprint: "str | None" = None

    proposed_action: "str | None" = None
    action_eligible: "bool | None" = None
    action_refusal_reason: "str | None" = None

    written: bool = False
    written_reviewer_action_id: "int | None" = None
    written_linked_signal_id: "int | None" = None


def check_schema_readiness(database: Path) -> dict:
    """Read-only, via `sqlite3.connect(..., mode=ro)` inside each reused
    `inspect()` - the identical three-layer check
    scripts/list_human_review_queue.py's own R4D schema-gate fix already
    established, reused verbatim rather than reimplemented."""
    intelligence = inspect_intelligence_review_schema(database)
    promotion = inspect_promotion_policy_schema(database)
    reconciliation_confirmation = inspect_reconciliation_confirmation_schema(database)
    ready = (
        intelligence["intelligence_review_decision_column_exists"]
        and intelligence["intelligence_review_reason_column_exists"]
        and promotion["promotion_policy_decision_column_exists"]
        and promotion["promotion_policy_reason_column_exists"]
        and reconciliation_confirmation["reconciliation_fingerprint_column_exists"]
    )
    return {
        "intelligence_review_decision_column_exists": intelligence["intelligence_review_decision_column_exists"],
        "intelligence_review_reason_column_exists": intelligence["intelligence_review_reason_column_exists"],
        "promotion_policy_decision_column_exists": promotion["promotion_policy_decision_column_exists"],
        "promotion_policy_reason_column_exists": promotion["promotion_policy_reason_column_exists"],
        "reconciliation_fingerprint_column_exists": reconciliation_confirmation["reconciliation_fingerprint_column_exists"],
        "source_assertions_count": promotion["source_assertions_count"],
        "ready": ready,
    }


def build_engine(database: Path, *, writable: bool):
    """Bound to EXACTLY the resolved `database` path - the only
    database-binding function in this script. When `writable` is False
    (every invocation without `--allow-database-write`), the connection is
    opened in SQLite's own read-only URI mode, so even a coding mistake that
    tried to write would be refused at the driver level, not merely by
    convention - matching R4D's own `build_readonly_engine()` precedent for
    every mode that must never write."""
    resolved = database.resolve()
    if writable:
        return create_engine(f"sqlite:///{resolved}", future=True)
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def _validate_config(config: ReviewConfig) -> None:
    """Argument-combination misuse that is invalid regardless of database
    state - raised before any database is even opened, matching
    scripts/list_human_review_queue.py's own `state` validation precedent
    (ValueError for caller misuse, structured `blockers`/`action_refusal_reason`
    for data-dependent conditions)."""
    if config.action is not None and config.action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {VALID_ACTIONS!r}, got {config.action!r}")
    if config.action == "MARK_DUPLICATE" and config.duplicate_of_signal_id is None:
        raise ValueError("--duplicate-of-signal-id is required for --action MARK_DUPLICATE")
    if config.action != "MARK_DUPLICATE" and config.duplicate_of_signal_id is not None:
        raise ValueError("--duplicate-of-signal-id is only valid with --action MARK_DUPLICATE")
    if config.action != "CONFIRM_DISTINCT_SIGNAL" and config.confirm_current_plan is not None:
        raise ValueError("--confirm-current-plan is only valid with --action CONFIRM_DISTINCT_SIGNAL")
    if config.allow_database_write:
        if config.action is None:
            raise ValueError("--allow-database-write requires --action")
        if not config.reviewer or not config.reviewer.strip():
            raise ValueError("--reviewer is required when --allow-database-write is set")
        if not config.reason or not config.reason.strip():
            raise ValueError("--reason is required when --allow-database-write is set")


def _validate_confirm_distinct_signal(
    *, outcome: ExistingSignalReconciliationOutcome, stored_fingerprint: "str | None",
    fresh_fingerprint: "str | None", confirm_current_plan: "str | None", allow_database_write: bool,
) -> "tuple[bool, str | None]":
    if outcome != POSSIBLE:
        return False, (
            "NO_BLOCKING_PLAN: fresh reconciliation is not POSSIBLE_EXISTING_SIGNAL_MATCH - "
            "there is no current blocking plan to confirm distinct against."
        )
    if stored_fingerprint is not None and stored_fingerprint == fresh_fingerprint:
        return False, (
            "ALREADY_CONFIRMED_CURRENT_PLAN: the latest CONFIRM_DISTINCT_SIGNAL already matches "
            "the current blocking plan - no new confirmation is needed."
        )
    if allow_database_write:
        if confirm_current_plan is None:
            return False, (
                "CONFIRMATION_FINGERPRINT_REQUIRED: --confirm-current-plan must be supplied, set to "
                "the current fingerprint shown by a prior dry-run, before this write will proceed."
            )
        if confirm_current_plan != fresh_fingerprint:
            return False, (
                "CONFIRMATION_FINGERPRINT_MISMATCH: the supplied --confirm-current-plan value does "
                "not match the freshly recomputed current fingerprint - the blocking state changed "
                "since it was shown; re-run without --allow-database-write to see the current value."
            )
    return True, None


def _validate_mark_duplicate(
    *, session: Session, assertion: SourceAssertion, outcome: ExistingSignalReconciliationOutcome,
    candidate_signal_ids: "tuple[int, ...]", duplicate_of_signal_id: "int | None",
) -> "tuple[bool, str | None]":
    if outcome != POSSIBLE:
        return False, (
            "NO_BLOCKING_PLAN: fresh reconciliation is not POSSIBLE_EXISTING_SIGNAL_MATCH - "
            "MARK_DUPLICATE through this reconciliation CLI only resolves a genuine blocking match, "
            "never advisory-only similarity."
        )
    if duplicate_of_signal_id not in candidate_signal_ids:
        return False, (
            f"DUPLICATE_TARGET_NOT_A_CURRENT_CANDIDATE: Signal {duplicate_of_signal_id!r} is not one "
            f"of the current blocking candidates {candidate_signal_ids!r}."
        )
    # Surfaces the authoritative record_reviewer_action() gate here too, so
    # a plain dry-run (no --allow-database-write) shows the real refusal
    # instead of only failing when a write is actually attempted - the
    # invariant itself still lives exclusively in the service layer; this
    # is read-only, informational duplication of that same check.
    effective = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
    if effective.effective_decision != AttachmentOutcome.ATTACH_CONFIRMED:
        return False, (
            f"EFFECTIVE_IDENTITY_NOT_CONFIRMED: MARK_DUPLICATE requires the effective identity "
            f"decision to be 'ATTACH_CONFIRMED', got {effective.effective_decision.value!r} "
            f"(basis={effective.basis.value!r})."
        )
    return True, None


def run_review(config: ReviewConfig) -> ReviewResult:
    """The one function that does the work (schema check, read, validate,
    optionally write) and returns a single, importable result - `main()`
    and the tests both consume it, so there is exactly one code path."""
    _validate_config(config)
    database_str = str(config.database.resolve())
    schema = check_schema_readiness(config.database)
    if not schema["ready"]:
        return ReviewResult(database=database_str, schema_readiness=schema, blockers=(SCHEMA_MIGRATION_REQUIRED_BLOCKER,))

    engine = build_engine(config.database, writable=config.allow_database_write)
    try:
        with Session(engine) as session:
            assertion = session.get(SourceAssertion, config.source_assertion_id)
            if assertion is None:
                session.rollback()
                return ReviewResult(
                    database=database_str, schema_readiness=schema, blockers=(SOURCE_ASSERTION_NOT_FOUND_BLOCKER,),
                )

            latest = get_latest_reviewer_action(session, assertion.id)
            latest_action_name = latest.action if latest is not None else None
            linked_signal_id = assertion.signal_id

            if linked_signal_id is not None:
                # ALREADY_LINKED dominates, for every action including the
                # three generic ones - a resolved row is not reopened by
                # this CLI. Mirrors R1's own ALREADY_LINKED short-circuit
                # and R4D's own identical eligibility precondition.
                session.rollback()
                return ReviewResult(
                    database=database_str, schema_readiness=schema,
                    source_assertion_id=assertion.id, latest_action=latest_action_name,
                    linked_signal_id=linked_signal_id, proposed_action=config.action,
                    action_eligible=(False if config.action is not None else None),
                    action_refusal_reason=(
                        f"ALREADY_LINKED: source_assertion.signal_id={linked_signal_id!r} is already "
                        "set - this row is already resolved; no reconciliation action is offered."
                        if config.action is not None else None
                    ),
                )

            # Human-selected-context boundary (identical to R4D - see this
            # module's own docstring): category/reference_year are never
            # fabricated, so the fingerprint computed here is always the
            # same one R4D's own queue view would have shown for this state.
            subject = build_reconciliation_subject(assertion, (), category=None, reference_year=None)
            candidates = find_reconciliation_candidates(session, assertion)
            decision = evaluate_existing_signal_reconciliation(subject, candidates)

            fresh_fingerprint: "str | None" = None
            if decision.outcome == POSSIBLE:
                plan = build_reconciliation_review_plan(
                    source_assertion_id=assertion.id, subject=subject, decision=decision,
                )
                fresh_fingerprint = compute_reconciliation_fingerprint(plan)

            stored_fingerprint = (
                latest.reconciliation_fingerprint
                if latest is not None and latest.action == "CONFIRM_DISTINCT_SIGNAL"
                else None
            )

            base_kwargs = dict(
                database=database_str, schema_readiness=schema,
                source_assertion_id=assertion.id, latest_action=latest_action_name,
                linked_signal_id=linked_signal_id, reconciliation_outcome=decision.outcome.value,
                candidate_signal_ids=decision.candidate_signal_ids, anchor_reasons=decision.reasons,
                current_fingerprint=fresh_fingerprint, stored_fingerprint=stored_fingerprint,
                proposed_action=config.action,
            )

            if config.action is None:
                session.rollback()
                return ReviewResult(**base_kwargs)

            if config.action == "CONFIRM_DISTINCT_SIGNAL":
                eligible, refusal = _validate_confirm_distinct_signal(
                    outcome=decision.outcome, stored_fingerprint=stored_fingerprint,
                    fresh_fingerprint=fresh_fingerprint, confirm_current_plan=config.confirm_current_plan,
                    allow_database_write=config.allow_database_write,
                )
            elif config.action == "MARK_DUPLICATE":
                eligible, refusal = _validate_mark_duplicate(
                    session=session, assertion=assertion, outcome=decision.outcome,
                    candidate_signal_ids=decision.candidate_signal_ids,
                    duplicate_of_signal_id=config.duplicate_of_signal_id,
                )
            else:  # DEFER, NEEDS_MORE_EVIDENCE, REJECT_SIGNAL - no reconciliation precondition
                eligible, refusal = True, None

            if not eligible:
                session.rollback()
                return ReviewResult(**base_kwargs, action_eligible=False, action_refusal_reason=refusal)

            if not config.allow_database_write:
                session.rollback()
                return ReviewResult(**base_kwargs, action_eligible=True)

            supersedes_action_id = latest.id if latest is not None else None
            written_linked_signal_id: "int | None" = None
            try:
                if config.action == "CONFIRM_DISTINCT_SIGNAL":
                    new_action = record_reviewer_action(
                        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason=config.reason,
                        reviewer=config.reviewer, supersedes_action_id=supersedes_action_id,
                        reconciliation_fingerprint=fresh_fingerprint,
                    )
                elif config.action == "MARK_DUPLICATE":
                    new_action = record_reviewer_action(
                        session, assertion, action="MARK_DUPLICATE", reason=config.reason,
                        reviewer=config.reviewer, supersedes_action_id=supersedes_action_id,
                        duplicate_of_signal_id=config.duplicate_of_signal_id,
                    )
                    link_result = link_source_assertion_to_duplicate_signal(session, assertion)
                    written_linked_signal_id = link_result.signal.id
                else:
                    new_action = record_reviewer_action(
                        session, assertion, action=config.action, reason=config.reason,
                        reviewer=config.reviewer, supersedes_action_id=supersedes_action_id,
                    )
                session.commit()
            except Exception:
                session.rollback()
                raise

            return ReviewResult(
                **base_kwargs, action_eligible=True, written=True,
                written_reviewer_action_id=new_action.id, written_linked_signal_id=written_linked_signal_id,
            )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Human-readable report rendering - text formatting only, no new data.
# ---------------------------------------------------------------------------


def render_result(result: ReviewResult) -> str:
    if result.blockers:
        return (
            f"Database: {result.database}\n"
            f"BLOCKED: {', '.join(result.blockers)}\n"
            f"schema_readiness: {result.schema_readiness}\n"
        )

    lines: "list[str]" = []
    lines.append(f"Database: {result.database}")
    lines.append(f"SourceAssertion #{result.source_assertion_id}")
    lines.append(f"  latest_reviewer_action: {result.latest_action}")
    lines.append(f"  linked_signal_id: {result.linked_signal_id}")

    if result.reconciliation_outcome is not None:
        lines.append("")
        lines.append("Reconciliation (fresh R1/R2/R4A recomputation)")
        lines.append(f"  outcome: {result.reconciliation_outcome}")
        if result.candidate_signal_ids:
            lines.append(f"  blocking candidate Signals: {', '.join(str(i) for i in result.candidate_signal_ids)}")
            lines.append("  anchor reasons:")
            for reason in result.anchor_reasons:
                lines.append(f"    {reason}")
        if result.current_fingerprint:
            lines.append(f"  current fingerprint: {result.current_fingerprint}")
        if result.stored_fingerprint is not None:
            lines.append(f"  stored confirmation fingerprint: {result.stored_fingerprint}")
    elif result.linked_signal_id is not None:
        lines.append("")
        lines.append("  ALREADY_LINKED - resolved; no reconciliation action offered by this CLI.")

    if result.proposed_action is not None:
        lines.append("")
        lines.append(f"Proposed action: {result.proposed_action}")
        lines.append(f"  eligible: {result.action_eligible}")
        if result.action_refusal_reason:
            lines.append(f"  refused: {result.action_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: ReviewerAction #{result.written_reviewer_action_id}")
            if result.written_linked_signal_id is not None:
                lines.append(f"  SourceAssertion.signal_id linked to Signal #{result.written_linked_signal_id}")
        elif result.action_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this action)")

    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-assertion-id", type=int, required=True)
    parser.add_argument("--action", choices=VALID_ACTIONS, default=None)
    parser.add_argument("--duplicate-of-signal-id", type=int, default=None)
    parser.add_argument("--confirm-current-plan", type=str, default=None)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = ReviewConfig(
        database=args.database, source_assertion_id=args.source_assertion_id, action=args.action,
        duplicate_of_signal_id=args.duplicate_of_signal_id, confirm_current_plan=args.confirm_current_plan,
        reviewer=args.reviewer, reason=args.reason, allow_database_write=args.allow_database_write,
    )
    result = run_review(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_action is not None and not result.action_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
