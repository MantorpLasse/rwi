"""SLT2 - human operator CLI for governed Signal lifecycle assessment
(RWI HQ "SLT2 - Governed Signal Lifecycle Assessment" mission, implementing
the SLT2 slice already specified in docs/architecture/rwi-signal-temporal-
relevance-opportunity-lifecycle-design.md §8 "Option E", §9, §14, §16).

    python -m scripts.record_signal_lifecycle_assessment --database data/runway_safe.db \\
        --signal-id 67
        -> pure inspection: the Signal's identity, the SLT1 machine
           lifecycle + reason (recomputed fresh, never stored), the latest
           SLT2 assessment if any, the effective lifecycle, and the full
           assessment history. Never writes.

    python -m scripts.record_signal_lifecycle_assessment --database ... --signal-id 67 \\
        --state realized_historical --reason "..." --reviewer human:you
        -> dry-run: shows whether this assessment would be accepted, and
           the resulting before/after effective lifecycle. Never writes.

    python -m scripts.record_signal_lifecycle_assessment --database ... --signal-id 67 \\
        --state realized_historical --reason "..." --reviewer human:you \\
        --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.signal_lifecycle_assessment.record_signal_lifecycle_assessment()
           (imported, never reimplemented) exactly once.

MODE: a single write mode, matching scripts/resolve_source_assertion_identity.py's
own design (no record-then-execute split needed). --state/--reason/--reviewer
together propose one assessment; omitting --state leaves the CLI in pure
inspection mode.

NO DUPLICATED BUSINESS RULES: every precondition (Signal existence, valid
lifecycle state, non-blank reason/reviewer, idempotent no-op on an
identical repeat) is enforced exactly once, inside
record_signal_lifecycle_assessment() itself - this script only ever calls
that function (once for the dry-run preview via a rolled-back transaction,
once more for the real write) and prints whatever it raises. It never
re-implements or re-derives any of those checks.

This script never sets Signal.status, Signal.completion_date, or
Signal.published, never creates an Installation, and never touches
SourceAssertion or ReviewerAction - lifecycle relevance is recorded as its
own, separate, additional governed fact.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.signal import Signal
from app.models.signal_lifecycle_assessment import SignalLifecycleAssessment
from app.services.signal_lifecycle_assessment import (
    SignalLifecycleAssessmentError,
    get_latest_signal_lifecycle_assessment,
    record_signal_lifecycle_assessment,
    resolve_effective_signal_lifecycle,
)


@dataclass(frozen=True)
class RecordSignalLifecycleAssessmentConfig:
    database: Path
    signal_id: int
    state: Optional[str] = None
    reason: Optional[str] = None
    reviewer: Optional[str] = None
    allow_database_write: bool = False


@dataclass
class RecordSignalLifecycleAssessmentResult:
    signal_id: int
    signal_found: bool = False
    signal_title: Optional[str] = None
    signal_status: Optional[str] = None

    machine_state: Optional[str] = None
    machine_reason: Optional[str] = None
    effective_state: Optional[str] = None
    effective_reason: Optional[str] = None
    effective_source: Optional[str] = None
    effective_reviewer: Optional[str] = None
    effective_assessed_at: Optional[str] = None

    assessment_history: "list[dict]" = field(default_factory=list)

    proposed_state: Optional[str] = None
    proposed_reason: Optional[str] = None
    proposed_reviewer: Optional[str] = None
    action_eligible: bool = False
    action_refusal_reason: Optional[str] = None
    written: bool = False
    written_assessment_id: Optional[int] = None
    written_is_noop: bool = False

    blockers: "list[str]" = field(default_factory=list)


def run_record(
    config: RecordSignalLifecycleAssessmentConfig, *, today: Optional[date] = None,
) -> RecordSignalLifecycleAssessmentResult:
    today = today if today is not None else date.today()
    result = RecordSignalLifecycleAssessmentResult(signal_id=config.signal_id)
    engine = create_engine(f"sqlite:///{config.database}")
    session = Session(engine)
    try:
        signal = session.get(Signal, config.signal_id)
        if signal is None:
            result.blockers.append(f"Signal {config.signal_id} does not exist")
            return result

        result.signal_found = True
        result.signal_title = signal.title
        result.signal_status = signal.status

        effective = resolve_effective_signal_lifecycle(session, signal, today=today)
        result.machine_state = effective.machine_state.value
        result.machine_reason = effective.machine_reason
        result.effective_state = effective.effective_state.value
        result.effective_reason = effective.effective_reason
        result.effective_source = effective.effective_source
        result.effective_reviewer = effective.reviewer
        result.effective_assessed_at = str(effective.assessed_at) if effective.assessed_at else None

        rows = (
            session.query(SignalLifecycleAssessment)
            .filter(SignalLifecycleAssessment.signal_id == config.signal_id)
            .order_by(SignalLifecycleAssessment.created_at.asc(), SignalLifecycleAssessment.id.asc())
            .all()
        )
        result.assessment_history = [
            {
                "id": row.id, "state": row.state, "reason": row.reason,
                "reviewer": row.reviewer, "created_at": row.created_at,
            }
            for row in rows
        ]

        if config.state is None:
            return result

        result.proposed_state = config.state
        result.proposed_reason = config.reason
        result.proposed_reviewer = config.reviewer

        try:
            savepoint = session.begin_nested()
            try:
                before = get_latest_signal_lifecycle_assessment(session, config.signal_id)
                write_result = record_signal_lifecycle_assessment(
                    session,
                    signal_id=config.signal_id,
                    state=config.state,
                    reason=config.reason or "",
                    reviewer=config.reviewer or "",
                )
                would_be_noop = before is not None and before.id == write_result.id
            finally:
                savepoint.rollback()
            result.action_eligible = True
        except SignalLifecycleAssessmentError as exc:
            result.action_eligible = False
            result.action_refusal_reason = str(exc)
            return result

        if not config.allow_database_write:
            return result

        write_result = record_signal_lifecycle_assessment(
            session,
            signal_id=config.signal_id,
            state=config.state,
            reason=config.reason or "",
            reviewer=config.reviewer or "",
        )
        session.commit()
        result.written = True
        result.written_assessment_id = write_result.id
        result.written_is_noop = would_be_noop

        effective_after = resolve_effective_signal_lifecycle(session, signal, today=today)
        result.effective_state = effective_after.effective_state.value
        result.effective_reason = effective_after.effective_reason
        result.effective_source = effective_after.effective_source
        result.effective_reviewer = effective_after.reviewer
        result.effective_assessed_at = str(effective_after.assessed_at) if effective_after.assessed_at else None
        return result
    finally:
        session.close()


def render_result(result: RecordSignalLifecycleAssessmentResult) -> str:
    lines: "list[str]" = []
    lines.append(f"Signal id: {result.signal_id}")

    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    lines.append(f"  title: {result.signal_title!r}")
    lines.append(f"  status: {result.signal_status!r}  (never written by this tool)")

    lines.append("")
    lines.append("MACHINE LIFECYCLE (SLT1, recomputed fresh, never stored)")
    lines.append(f"  state: {result.machine_state}")
    lines.append(f"  reason: {result.machine_reason!r}")

    lines.append("")
    lines.append(f"ASSESSMENT HISTORY (SLT2, {len(result.assessment_history)} total)")
    if not result.assessment_history:
        lines.append("  (none - effective lifecycle currently equals the machine baseline)")
    for row in result.assessment_history:
        lines.append(
            f"  #{row['id']} state={row['state']} reviewer={row['reviewer']!r} "
            f"reason={row['reason']!r} created_at={row['created_at']}"
        )

    lines.append("")
    lines.append("EFFECTIVE LIFECYCLE (what presentation/export should use)")
    lines.append(f"  effective_state: {result.effective_state}")
    lines.append(f"  effective_source: {result.effective_source}")
    lines.append(f"  effective_reason: {result.effective_reason!r}")
    if result.effective_source == "governed_assessment":
        lines.append(f"  reviewer: {result.effective_reviewer!r}")
        lines.append(f"  assessed_at: {result.effective_assessed_at}")

    if result.proposed_state is not None:
        lines.append("")
        lines.append(f"Proposed assessment: state={result.proposed_state}")
        lines.append(f"  reason: {result.proposed_reason!r}")
        lines.append(f"  reviewer: {result.proposed_reviewer!r}")
        lines.append(f"  eligible: {result.action_eligible}")
        if result.action_refusal_reason:
            lines.append(f"  refused: {result.action_refusal_reason}")
        if result.written:
            if result.written_is_noop:
                lines.append(
                    f"  NO-OP: identical to the latest existing assessment "
                    f"(#{result.written_assessment_id}) - no duplicate row written"
                )
            else:
                lines.append(f"  WRITTEN: SignalLifecycleAssessment #{result.written_assessment_id}")
        elif result.action_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this assessment)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database, read-only unless --state is also given with "
        "--allow-database-write. No default - never the real production database by accident.",
    )
    parser.add_argument("--signal-id", type=int, required=True, dest="signal_id")
    parser.add_argument(
        "--state", type=str, default=None,
        help="One of: active_opportunity, developing_watch, stale_unresolved, realized_historical, other. "
        "Omit for pure inspection mode.",
    )
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = RecordSignalLifecycleAssessmentConfig(
        database=args.database,
        signal_id=args.signal_id,
        state=args.state,
        reason=args.reason,
        reviewer=args.reviewer,
        allow_database_write=args.allow_database_write,
    )
    result = run_record(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_state is not None and not result.action_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
