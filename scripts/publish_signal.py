"""Human operator CLI for governed Signal publication ("RWI - Signal
Publication Governance - Design + Implementation" mission).

    python -m scripts.publish_signal --database data/runway_safe.db \\
        --signal-id 69 --reviewer human:you --reason "..."
        -> dry-run (default): shows the mandatory preview (current
           signal.published, latest SignalPublicationAction, full
           eligibility decision - every gate and every blocker) and whether
           this Signal is currently eligible to publish. Never writes.

    ... --action UNPUBLISH --reviewer human:you --reason "..." --apply \\
        --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.signal_publication.publish_signal()/unpublish_signal()
           (imported, never reimplemented) exactly once, against exactly one
           Signal. Eligibility is RECOMPUTED transactionally inside that
           same call - never trusted from the dry-run preview.

ONE Signal PER INVOCATION, NO BULK MODE, NO WILDCARD, NO "publish all
eligible" (matching every other governed CLI in this pipeline -
scripts/record_manual_claim_evidence.py,
scripts/record_cross_source_alias_attestation.py).

BOTH --apply AND --allow-database-write ARE REQUIRED TO WRITE, deliberately
stronger than the single-flag convention every other CLI in this pipeline
uses: a PUBLISH call is this pipeline's first mechanism that can make
previously-internal evidence world-visible on the next static-site export,
a materially different consequence from any other governed write this
repository has, and this CLI's own author judged that consequence worth one
extra explicit flag. `--apply` expresses "I intend to change this Signal's
publication state"; `--allow-database-write` is the existing repository-wide
real-database-write gate, reused verbatim.

Real-database use expects the caller has already taken (or will take, via
scripts/migrate_signal_publication_action.py's own --allow-database-write
backup step) a fresh backup - this script does not create one itself, since
it is a row-level write against an already-migrated table, not a schema
migration.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models import Signal
from app.services.signal_publication import (
    evaluate_publication_eligibility,
    get_latest_signal_publication_action,
    publish_signal,
    unpublish_signal,
)

_KNOWN_ERRORS = (ValueError,)
_ACTIONS = ("PUBLISH", "UNPUBLISH")


@dataclass(frozen=True)
class PublishSignalConfig:
    database: Path
    signal_id: int
    action: str = "PUBLISH"
    reviewer: Optional[str] = None
    reason: Optional[str] = None
    apply: bool = False
    allow_database_write: bool = False


@dataclass
class PublishSignalResult:
    signal_id: int
    preview: "dict | None" = None
    written: bool = False
    changed: bool = False
    blockers: "list[str]" = field(default_factory=list)


def run_publish(config: PublishSignalConfig) -> PublishSignalResult:
    result = PublishSignalResult(signal_id=config.signal_id)
    if config.action not in _ACTIONS:
        result.blockers.append(f"--action must be one of {_ACTIONS!r}, got {config.action!r}")
        return result

    engine = create_engine(f"sqlite:///{config.database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        signal = session.get(Signal, config.signal_id)
        if signal is None:
            result.blockers.append(f"no Signal with id={config.signal_id!r}")
            return result

        eligibility = evaluate_publication_eligibility(session, signal)
        latest_action = get_latest_signal_publication_action(session, signal.id)
        result.preview = {
            "current_published": signal.published,
            "latest_action": latest_action.action if latest_action else None,
            "latest_action_reviewer": latest_action.reviewer if latest_action else None,
            "latest_action_created_at": str(latest_action.created_at) if latest_action else None,
            "publish_eligible": eligibility.eligible,
            "publish_blockers": list(eligibility.blockers),
            "checked_source_assertion_ids": list(eligibility.checked_source_assertion_ids),
        }

        if not config.apply:
            return result
        if not config.allow_database_write:
            result.blockers.append("--apply requires --allow-database-write as well")
            return result
        if not config.reviewer or not config.reviewer.strip():
            result.blockers.append("--reviewer is required to apply a publication action")
            return result
        if not config.reason or not config.reason.strip():
            result.blockers.append("--reason is required to apply a publication action")
            return result

        operation = publish_signal if config.action == "PUBLISH" else unpublish_signal
        try:
            write_result = operation(session, signal, reviewer=config.reviewer, reason=config.reason)
        except _KNOWN_ERRORS as exc:
            result.blockers.append(str(exc))
            return result

        session.commit()
        result.written = True
        result.changed = write_result.changed
        return result
    finally:
        session.close()


def render_result(result: PublishSignalResult) -> str:
    lines: "list[str]" = [f"Signal id: {result.signal_id}"]
    if result.preview:
        p = result.preview
        lines.append("")
        lines.append("MANDATORY PREVIEW (never writes)")
        lines.append(f"  current signal.published: {p['current_published']}")
        lines.append(
            f"  latest publication action: {p['latest_action']} "
            f"(reviewer={p['latest_action_reviewer']}, at={p['latest_action_created_at']})"
        )
        lines.append(f"  checked SourceAssertion ids: {p['checked_source_assertion_ids']}")
        lines.append(f"  publish eligible: {p['publish_eligible']}")
        for blocker in p["publish_blockers"]:
            lines.append(f"    blocker: {blocker}")
    if result.blockers:
        lines.append("")
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"
    lines.append("")
    if result.written:
        lines.append(f"  WRITTEN: changed={result.changed}")
    else:
        lines.append("  DRY RUN - no write performed (pass --reviewer, --reason, --apply and "
                      "--allow-database-write to apply this action)")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--signal-id", type=int, required=True, dest="signal_id")
    parser.add_argument("--action", type=str, default="PUBLISH", choices=_ACTIONS)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = PublishSignalConfig(
        database=args.database, signal_id=args.signal_id, action=args.action,
        reviewer=args.reviewer, reason=args.reason, apply=args.apply,
        allow_database_write=args.allow_database_write,
    )
    result = run_publish(config)
    print(render_result(result))
    if result.blockers:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
