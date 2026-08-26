"""KAR3 - human operator CLI for source-assertion known-airport identity
resolution (docs/architecture/rwi-known-airport-ambiguity-resolution-design.md,
locked design contract for this capability).

    python -m scripts.resolve_source_assertion_identity --database data/runway_safe.db \\
        --source-assertion-id 229
        -> pure inspection: original machine identity_guard_decision/reason
           (never recomputed here), the assertion's own EvidenceBag identity
           evidence, current airport_id/unresolved state, and full
           resolution decision history. Never writes.

    python -m scripts.resolve_source_assertion_identity --database ... --source-assertion-id 229 \\
        --action ATTACH_TO_EXISTING_AIRPORT --matched-airport-id 8 \\
        --reviewer human:you --reason "..."
        -> dry-run: shows whether this decision would be accepted, and
           displays the target Airport's own identity so the operator can
           verify the selection before writing. Never writes. The CLI never
           auto-selects an Airport from name/topology - --matched-airport-id
           is always explicit, human-supplied input.

    python -m scripts.resolve_source_assertion_identity --database ... --source-assertion-id 229 \\
        --action ATTACH_TO_EXISTING_AIRPORT --matched-airport-id 8 \\
        --reviewer human:you --reason "..." --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.source_assertion_identity_resolution.record_source_assertion_identity_resolution()
           (imported, never reimplemented) exactly once. No separate execute
           step - see that module's own docstring for why record and mutate
           are atomic here, unlike UAC4's own two-step review/execute shape.

MODE: a single write mode (no MODE SEPARATION split into record-then-execute
- design doc S5's own explicit reasoning for why this capability does not
need one). --action/--matched-airport-id/--reviewer/--reason together
propose one resolution decision; omitting --action leaves the CLI in pure
inspection mode.

NO DUPLICATED BUSINESS RULES: every precondition (already resolved,
candidate-linked, missing evidence snapshot, nonexistent target Airport,
action/target consistency, empty reviewer/reason) is enforced exactly once,
inside record_source_assertion_identity_resolution() itself - this script
only ever calls that function (once for the real write, once more for the
dry-run preview via a rolled-back transaction) and prints whatever it
raises. It never re-implements or re-derives any of those checks.

REMINDER, NOT A GATE: after a successful ATTACH_TO_EXISTING_AIRPORT write,
this CLI prints a plain reminder that Signal-track eligibility requires a
separately-run EB4 re-evaluation
(app.services.resolved_candidate_evidence_reevaluation.reevaluate_resolved_candidate_evidence())
- display-only, never auto-triggered here (design doc S19's own documented
open item: this module deliberately does not auto-run EB4).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.source_assertion_identity_resolution import (
    SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS,
    SourceAssertionIdentityResolution,
)
from app.services.evidence_bag_serialization import deserialize_evidence_bag
from app.services.source_assertion_identity_resolution import (
    CandidateLinkedAssertionError,
    MissingEvidenceBagSnapshotError,
    SourceAssertionAlreadyResolvedError,
    SourceAssertionNotFoundError,
    TargetAirportNotFoundError,
    get_latest_source_assertion_identity_resolution,
    record_source_assertion_identity_resolution,
)
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag

_ATTACH_ACTION = "ATTACH_TO_EXISTING_AIRPORT"

_KNOWN_ERRORS = (
    SourceAssertionNotFoundError,
    SourceAssertionAlreadyResolvedError,
    CandidateLinkedAssertionError,
    MissingEvidenceBagSnapshotError,
    TargetAirportNotFoundError,
    ValueError,
)


@dataclass(frozen=True)
class ResolveSourceAssertionIdentityConfig:
    database: Path
    source_assertion_id: int
    action: Optional[str] = None
    reviewer: Optional[str] = None
    reason: Optional[str] = None
    matched_airport_id: Optional[int] = None
    supersedes_resolution_id: Optional[int] = None
    allow_database_write: bool = False


@dataclass
class ResolveSourceAssertionIdentityResult:
    source_assertion_id: int
    assertion_found: bool = False
    identity_guard_decision: Optional[str] = None
    identity_guard_reason: Optional[str] = None
    airport_id: Optional[int] = None
    unknown_airport_candidate_id: Optional[int] = None
    resolved_airport_name: Optional[str] = None
    evidence_names: "tuple[str, ...]" = ()
    evidence_runway_pairs: "tuple[str, ...]" = ()
    evidence_runway_ends: "tuple[str, ...]" = ()
    evidence_issuers: "tuple[str, ...]" = ()
    evidence_document_title: Optional[str] = None
    resolution_history: "list[dict]" = None  # type: ignore[assignment]

    proposed_action: Optional[str] = None
    proposed_matched_airport_id: Optional[int] = None
    proposed_matched_airport_name: Optional[str] = None
    proposed_reviewer: Optional[str] = None
    proposed_reason: Optional[str] = None
    action_eligible: bool = False
    action_refusal_reason: Optional[str] = None
    written: bool = False
    written_resolution_id: Optional[int] = None

    blockers: "list[str]" = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.resolution_history is None:
            self.resolution_history = []
        if self.blockers is None:
            self.blockers = []


def run_resolve(config: ResolveSourceAssertionIdentityConfig) -> ResolveSourceAssertionIdentityResult:
    result = ResolveSourceAssertionIdentityResult(source_assertion_id=config.source_assertion_id)
    engine = create_engine(f"sqlite:///{config.database}")
    session = Session(engine)
    try:
        assertion = session.get(SourceAssertion, config.source_assertion_id)
        if assertion is None:
            result.blockers.append(f"SourceAssertion {config.source_assertion_id} does not exist")
            return result

        result.assertion_found = True
        result.identity_guard_decision = assertion.identity_guard_decision
        result.identity_guard_reason = assertion.identity_guard_reason
        result.airport_id = assertion.airport_id
        result.unknown_airport_candidate_id = assertion.unknown_airport_candidate_id
        if assertion.airport_id is not None:
            resolved_airport = session.get(Airport, assertion.airport_id)
            result.resolved_airport_name = resolved_airport.name if resolved_airport else None

        snapshot = session.query(SourceAssertionEvidenceBag).filter(
            SourceAssertionEvidenceBag.source_assertion_id == assertion.id
        ).one_or_none()
        if snapshot is not None:
            try:
                bag = deserialize_evidence_bag(snapshot.evidence_bag_json)
                result.evidence_names = tuple(sorted(bag.names))
                result.evidence_runway_pairs = tuple(sorted(bag.runway_pairs))
                result.evidence_runway_ends = tuple(sorted(bag.runway_ends))
                result.evidence_issuers = tuple(sorted(bag.issuers))
                result.evidence_document_title = bag.document_title
            except Exception:  # noqa: BLE001 - display-only best-effort, never blocks inspection
                pass

        rows = (
            session.query(SourceAssertionIdentityResolution)
            .filter(SourceAssertionIdentityResolution.source_assertion_id == assertion.id)
            .order_by(SourceAssertionIdentityResolution.created_at.asc(), SourceAssertionIdentityResolution.id.asc())
            .all()
        )
        result.resolution_history = [
            {
                "id": row.id,
                "action": row.action,
                "matched_airport_id": row.matched_airport_id,
                "reviewer": row.reviewer,
                "reason": row.reason,
                "created_at": row.created_at,
            }
            for row in rows
        ]

        if config.action is None:
            return result

        if config.action not in SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS:
            result.blockers.append(
                f"--action must be one of {SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS!r}, got {config.action!r}"
            )
            return result

        result.proposed_action = config.action
        result.proposed_matched_airport_id = config.matched_airport_id
        result.proposed_reviewer = config.reviewer
        result.proposed_reason = config.reason

        if config.matched_airport_id is not None:
            matched = session.get(Airport, config.matched_airport_id)
            result.proposed_matched_airport_name = matched.name if matched else None

        if not config.reviewer or not config.reviewer.strip():
            result.blockers.append("--reviewer is required with --action")
            return result
        if not config.reason or not config.reason.strip():
            result.blockers.append("--reason is required with --action")
            return result

        try:
            savepoint = session.begin_nested()
            try:
                write_result = record_source_assertion_identity_resolution(
                    session,
                    source_assertion_id=config.source_assertion_id,
                    action=config.action,
                    reason=config.reason,
                    reviewer=config.reviewer,
                    matched_airport_id=config.matched_airport_id,
                    supersedes_resolution_id=config.supersedes_resolution_id,
                )
            finally:
                savepoint.rollback()
            result.action_eligible = True
        except _KNOWN_ERRORS as exc:
            result.action_eligible = False
            result.action_refusal_reason = str(exc)
            return result

        if not config.allow_database_write:
            return result

        write_result = record_source_assertion_identity_resolution(
            session,
            source_assertion_id=config.source_assertion_id,
            action=config.action,
            reason=config.reason,
            reviewer=config.reviewer,
            matched_airport_id=config.matched_airport_id,
            supersedes_resolution_id=config.supersedes_resolution_id,
        )
        session.commit()
        result.written = True
        result.written_resolution_id = write_result.resolution.id
        result.airport_id = write_result.airport_id_set
        if result.airport_id is not None:
            resolved_airport = session.get(Airport, result.airport_id)
            result.resolved_airport_name = resolved_airport.name if resolved_airport else None
        result.resolution_history = result.resolution_history + [
            {
                "id": write_result.resolution.id,
                "action": write_result.resolution.action,
                "matched_airport_id": write_result.resolution.matched_airport_id,
                "reviewer": write_result.resolution.reviewer,
                "reason": write_result.resolution.reason,
                "created_at": write_result.resolution.created_at,
            }
        ]
        return result
    finally:
        session.close()


def render_result(result: ResolveSourceAssertionIdentityResult) -> str:
    lines: "list[str]" = []
    lines.append(f"SourceAssertion id: {result.source_assertion_id}")

    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("ORIGINAL MACHINE DECISION (permanent historical fact, never recomputed here)")
    lines.append(f"  identity_guard_decision: {result.identity_guard_decision}")
    lines.append(f"  identity_guard_reason: {result.identity_guard_reason}")

    lines.append("")
    lines.append("EVIDENCE (from the assertion's own immutable EvidenceBag snapshot)")
    lines.append(f"  names: {list(result.evidence_names)}")
    lines.append(f"  runway_pairs: {list(result.evidence_runway_pairs)}")
    lines.append(f"  runway_ends: {list(result.evidence_runway_ends)}")
    lines.append(f"  issuers: {list(result.evidence_issuers)}")
    lines.append(f"  document_title: {result.evidence_document_title!r}")

    lines.append("")
    lines.append("CURRENT RESOLUTION STATE")
    if result.airport_id is not None:
        lines.append(f"  RESOLVED - airport_id={result.airport_id} ({result.resolved_airport_name!r})")
    elif result.unknown_airport_candidate_id is not None:
        lines.append(
            f"  CANDIDATE-LINKED - unknown_airport_candidate_id={result.unknown_airport_candidate_id} "
            "(governed exclusively by UAC4, not this tool)"
        )
    else:
        lines.append("  UNRESOLVED (eligible for this workflow)")

    lines.append("")
    lines.append(f"RESOLUTION DECISION HISTORY ({len(result.resolution_history)} total)")
    if not result.resolution_history:
        lines.append("  (none)")
    for row in result.resolution_history:
        lines.append(
            f"  #{row['id']} action={row['action']} matched_airport_id={row['matched_airport_id']} "
            f"reviewer={row['reviewer']!r} reason={row['reason']!r} created_at={row['created_at']}"
        )

    if result.proposed_action is not None:
        lines.append("")
        lines.append(f"Proposed resolution: {result.proposed_action}")
        lines.append(f"  matched_airport_id: {result.proposed_matched_airport_id}")
        if result.proposed_matched_airport_id is not None:
            lines.append(f"  matched_airport_name: {result.proposed_matched_airport_name!r}")
        lines.append(f"  reviewer: {result.proposed_reviewer!r}")
        lines.append(f"  reason: {result.proposed_reason!r}")
        lines.append(f"  eligible: {result.action_eligible}")
        if result.action_refusal_reason:
            lines.append(f"  refused: {result.action_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: SourceAssertionIdentityResolution #{result.written_resolution_id}")
            if result.proposed_action == _ATTACH_ACTION:
                lines.append(
                    "  REMINDER: this attachment is not yet Signal-track eligible until a separate, "
                    "explicit EB4 re-evaluation is run "
                    "(app.services.resolved_candidate_evidence_reevaluation.reevaluate_resolved_candidate_evidence()) "
                    "- not triggered automatically by this tool."
                )
        elif result.action_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this resolution)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database, read-only unless --action is also given with "
        "--allow-database-write. No default - never the real production database by accident.",
    )
    parser.add_argument("--source-assertion-id", type=int, required=True, dest="source_assertion_id")
    parser.add_argument("--action", choices=SOURCE_ASSERTION_IDENTITY_RESOLUTION_ACTIONS, default=None)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--matched-airport-id", type=int, default=None, dest="matched_airport_id")
    parser.add_argument(
        "--supersedes-resolution-id", type=int, default=None, dest="supersedes_resolution_id",
        help="Optional. Only valid with --action. Cross-references an earlier resolution row this "
        "one supersedes (audit annotation only - get_latest_source_assertion_identity_resolution() "
        "determines currency by recency, never by walking this chain).",
    )
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = ResolveSourceAssertionIdentityConfig(
        database=args.database,
        source_assertion_id=args.source_assertion_id,
        action=args.action,
        reviewer=args.reviewer,
        reason=args.reason,
        matched_airport_id=args.matched_airport_id,
        supersedes_resolution_id=args.supersedes_resolution_id,
        allow_database_write=args.allow_database_write,
    )
    result = run_resolve(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_action is not None and not result.action_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
