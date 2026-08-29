"""Human operator CLI for first-class governed Airport aliases
(docs/architecture, "RWI - Governed Canonical Airport Aliases -
Cross-Script Identity Design" mission, the locked design this capability
implements).

    python -m scripts.record_airport_alias --database data/runway_safe.db \\
        --airport-id 88
        -> pure inspection: Airport identity (canonical name, existing
           identifiers), currently-admitted aliases, alias history. Never
           writes.

    python -m scripts.record_airport_alias --database ... --airport-id 88 \\
        --source-id 82 --source-assertion-id 232 --alias "..." \\
        --evidence-excerpt "..." --analyst human:you
        -> dry-run: shows the mandatory impact preview and whether this
           admission would be accepted (via a rolled-back SAVEPOINT, so
           eligibility/anti-circularity is authoritative, never re-derived
           by this CLI). Never writes.

    ... --allow-database-write
        -> the only invocation shape that writes: calls
           app.services.airport_alias.record_airport_alias() (imported,
           never reimplemented) exactly once. The impact preview is
           RECOMPUTED transactionally inside that same call, immediately
           before persistence - this CLI never trusts its own earlier,
           possibly-stale preview output as a substitute (Phase 16's own
           explicit instruction).

THIS TOOL NEVER SHOWS OR ASKS FOR AN IDENTITY-DECISION CHOICE - there is
no `--decision`/`--outcome`/`--confirm` flag anywhere in this script. The
analyst supplies evidence only; the real, unmodified IdentityGuard
computes any resulting identity outcome, and only when a SEPARATE,
explicit EB4 re-evaluation is later run against a specific SourceAssertion
- never automatically, never from this tool.

ONE AIRPORT, ONE ALIAS, ONE EVIDENCE SOURCE PER INVOCATION, NO BULK MODE
(mission's own repeated, explicit instruction, matching every other
governed CLI in this pipeline): there is no "admit several aliases" or
"scan for candidate aliases" feature anywhere in this script.

NO WEB FETCHING, NO TRANSLATION, NO TRANSLITERATION: this tool only ever
reads what is already preserved on the cited SourceAssertion row itself
(`raw_relevant_text`) - it never fetches a URL and never attempts to
generate, guess, or "help" with a proposed alias string.

NO DUPLICATED BUSINESS RULES: every precondition (Source/SourceAssertion
binding, literal containment, independent identity anchor, source
reliability, anti-circularity, duplicate/reversal safety) is enforced
exactly once, inside record_airport_alias()/
check_airport_alias_admission_eligibility() themselves - this script only
ever calls them and prints whatever they raise.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.models import Airport, Source, SourceAssertion
from app.models.airport_alias import AIRPORT_ALIAS_EVIDENCE_CLASSES, AIRPORT_ALIAS_STATUSES, AirportAlias
from app.services.airport_alias import (
    AirportNotFoundError,
    AliasNotInExcerptError,
    CircularAliasEvidenceError,
    ConflictingAliasStatusRequiresSupersessionError,
    DuplicateActiveAliasError,
    EmptyAliasError,
    EmptyAnalystError,
    EmptyEvidenceExcerptError,
    ExcerptNotInPreservedEvidenceError,
    InsufficientSourceReliabilityError,
    NoIdentityAnchorError,
    SourceAssertionNotFoundError,
    SourceAssertionSourceMismatchError,
    SourceNotFoundError,
    get_admitted_airport_aliases,
    preview_airport_alias_admission_impact,
    record_airport_alias,
)

_KNOWN_ERRORS = (
    AirportNotFoundError,
    SourceNotFoundError,
    SourceAssertionNotFoundError,
    SourceAssertionSourceMismatchError,
    EmptyAliasError,
    EmptyEvidenceExcerptError,
    EmptyAnalystError,
    AliasNotInExcerptError,
    ExcerptNotInPreservedEvidenceError,
    NoIdentityAnchorError,
    InsufficientSourceReliabilityError,
    CircularAliasEvidenceError,
    DuplicateActiveAliasError,
    ConflictingAliasStatusRequiresSupersessionError,
    ValueError,
)


@dataclass(frozen=True)
class RecordAirportAliasConfig:
    database: Path
    airport_id: int
    source_id: Optional[int] = None
    source_assertion_id: Optional[int] = None
    alias: Optional[str] = None
    evidence_excerpt: Optional[str] = None
    analyst: Optional[str] = None
    language: Optional[str] = None
    script: Optional[str] = None
    evidence_class: str = "AUTHORITATIVE_DIRECT"
    status: str = "ADMITTED"
    supersedes_alias_id: Optional[int] = None
    allow_database_write: bool = False


@dataclass
class RecordAirportAliasResult:
    airport_id: int
    airport_found: bool = False
    airport_name: Optional[str] = None
    airport_iata: Optional[str] = None
    airport_icao: Optional[str] = None
    airport_faa: Optional[str] = None
    currently_admitted_aliases: "tuple[str, ...]" = ()
    alias_history: "list[dict]" = field(default_factory=list)

    proposed_source_id: Optional[int] = None
    proposed_source_assertion_id: Optional[int] = None
    proposed_alias: Optional[str] = None
    proposed_evidence_excerpt: Optional[str] = None
    proposed_analyst: Optional[str] = None
    proposed_language: Optional[str] = None
    proposed_script: Optional[str] = None
    proposed_evidence_class: Optional[str] = None
    proposed_status: Optional[str] = None
    proposed_supersedes_alias_id: Optional[int] = None

    impact_preview: "list[dict]" = field(default_factory=list)
    impact_changed_count: int = 0

    decision_eligible: bool = False
    decision_refusal_reason: Optional[str] = None
    written: bool = False
    written_alias_id: Optional[int] = None

    blockers: "list[str]" = field(default_factory=list)


def run_record(config: RecordAirportAliasConfig) -> RecordAirportAliasResult:
    result = RecordAirportAliasResult(airport_id=config.airport_id)
    engine = create_engine(f"sqlite:///{config.database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        airport = session.get(Airport, config.airport_id)
        if airport is None:
            result.blockers.append(f"Airport {config.airport_id} does not exist")
            return result

        result.airport_found = True
        result.airport_name = airport.name
        result.airport_iata = airport.iata_code
        result.airport_icao = airport.icao_code
        result.airport_faa = airport.faa_code
        result.currently_admitted_aliases = tuple(sorted(get_admitted_airport_aliases(session, airport.id)))

        rows = session.scalars(
            select(AirportAlias)
            .where(AirportAlias.airport_id == airport.id)
            .order_by(AirportAlias.created_at.asc(), AirportAlias.id.asc())
        ).all()
        result.alias_history = [
            {
                "id": row.id, "alias": row.alias, "status": row.status, "evidence_class": row.evidence_class,
                "source_id": row.source_id, "source_assertion_id": row.source_assertion_id,
                "analyst": row.analyst, "supersedes_alias_id": row.supersedes_alias_id, "created_at": row.created_at,
            }
            for row in rows
        ]

        if (
            config.alias is None and config.evidence_excerpt is None and config.analyst is None
            and config.source_id is None and config.source_assertion_id is None
        ):
            return result

        result.proposed_source_id = config.source_id
        result.proposed_source_assertion_id = config.source_assertion_id
        result.proposed_alias = config.alias
        result.proposed_evidence_excerpt = config.evidence_excerpt
        result.proposed_analyst = config.analyst
        result.proposed_language = config.language
        result.proposed_script = config.script
        result.proposed_evidence_class = config.evidence_class
        result.proposed_status = config.status
        result.proposed_supersedes_alias_id = config.supersedes_alias_id

        for field_name, value in (
            ("--source-id", config.source_id), ("--source-assertion-id", config.source_assertion_id),
            ("--alias", config.alias), ("--evidence-excerpt", config.evidence_excerpt), ("--analyst", config.analyst),
        ):
            if value is None or (isinstance(value, str) and not value.strip()):
                result.blockers.append(f"{field_name} is required to propose an alias admission")
        if result.blockers:
            return result

        if config.status not in AIRPORT_ALIAS_STATUSES:
            result.blockers.append(f"--status must be one of {AIRPORT_ALIAS_STATUSES!r}")
            return result
        if config.evidence_class not in AIRPORT_ALIAS_EVIDENCE_CLASSES:
            result.blockers.append(f"--evidence-class must be one of {AIRPORT_ALIAS_EVIDENCE_CLASSES!r}")
            return result

        # Mandatory impact preview (Phase 15/16) - shown BEFORE any
        # eligibility/write attempt, using the same pure simulation the
        # write path itself recomputes transactionally.
        try:
            preview = preview_airport_alias_admission_impact(
                session, airport_id=config.airport_id, proposed_alias=config.alias,
            )
            result.impact_preview = [
                {
                    "source_assertion_id": r.source_assertion_id, "source_id": r.source_id,
                    "has_snapshot": r.has_snapshot, "current_outcome": r.current_outcome,
                    "hypothetical_outcome": r.hypothetical_outcome, "changed": r.changed,
                }
                for r in preview.rows
            ]
            result.impact_changed_count = len(preview.changed_source_assertion_ids)
        except _KNOWN_ERRORS as exc:
            result.blockers.append(f"impact preview failed: {exc}")
            return result

        try:
            savepoint = session.begin_nested()
            try:
                record_airport_alias(
                    session,
                    airport_id=config.airport_id, source_id=config.source_id,
                    source_assertion_id=config.source_assertion_id, alias=config.alias,
                    evidence_excerpt=config.evidence_excerpt, analyst=config.analyst,
                    evidence_class=config.evidence_class, language=config.language, script=config.script,
                    status=config.status, supersedes_alias_id=config.supersedes_alias_id,
                )
            finally:
                savepoint.rollback()
            result.decision_eligible = True
        except _KNOWN_ERRORS as exc:
            result.decision_eligible = False
            result.decision_refusal_reason = str(exc)
            return result

        if not config.allow_database_write:
            return result

        write_result = record_airport_alias(
            session,
            airport_id=config.airport_id, source_id=config.source_id,
            source_assertion_id=config.source_assertion_id, alias=config.alias,
            evidence_excerpt=config.evidence_excerpt, analyst=config.analyst,
            evidence_class=config.evidence_class, language=config.language, script=config.script,
            status=config.status, supersedes_alias_id=config.supersedes_alias_id,
        )
        session.commit()
        result.written = True
        result.written_alias_id = write_result.alias_id
        result.currently_admitted_aliases = tuple(sorted(get_admitted_airport_aliases(session, airport.id)))
        return result
    finally:
        session.close()


def render_result(result: RecordAirportAliasResult) -> str:
    lines: "list[str]" = []
    lines.append(f"Airport id: {result.airport_id}")

    if result.blockers:
        for blocker in result.blockers:
            lines.append(f"BLOCKED: {blocker}")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("AIRPORT (canonical identity, never mutated by this tool)")
    lines.append(f"  name: {result.airport_name!r}")
    lines.append(f"  iata_code: {result.airport_iata!r}  icao_code: {result.airport_icao!r}  faa_code: {result.airport_faa!r}")
    lines.append(f"  currently admitted aliases: {list(result.currently_admitted_aliases)!r}")

    lines.append("")
    lines.append(f"ALIAS HISTORY ({len(result.alias_history)} total)")
    if not result.alias_history:
        lines.append("  (none)")
    for row in result.alias_history:
        lines.append(
            f"  #{row['id']} alias={row['alias']!r} status={row['status']} evidence_class={row['evidence_class']!r} "
            f"source_id={row['source_id']} source_assertion_id={row['source_assertion_id']} "
            f"analyst={row['analyst']!r} supersedes={row['supersedes_alias_id']} created_at={row['created_at']}"
        )

    if result.proposed_alias is not None or result.proposed_source_id is not None:
        lines.append("")
        lines.append("Proposed alias admission (HUMAN EVIDENCE ONLY - no identity decision is ever accepted here):")
        lines.append(f"  source_id: {result.proposed_source_id}")
        lines.append(f"  source_assertion_id: {result.proposed_source_assertion_id}")
        lines.append(f"  alias: {result.proposed_alias!r}")
        lines.append(f"  evidence_excerpt: {result.proposed_evidence_excerpt!r}")
        lines.append(f"  analyst: {result.proposed_analyst!r}")
        lines.append(f"  language: {result.proposed_language!r}  script: {result.proposed_script!r}")
        lines.append(f"  evidence_class: {result.proposed_evidence_class!r}  status: {result.proposed_status!r}")
        lines.append(f"  supersedes_alias_id: {result.proposed_supersedes_alias_id}")

        lines.append("")
        lines.append(f"MANDATORY IMPACT PREVIEW ({result.impact_changed_count} SourceAssertion(s) would change outcome)")
        for row in result.impact_preview:
            lines.append(
                f"  SA{row['source_assertion_id']} (source_id={row['source_id']}): "
                f"has_snapshot={row['has_snapshot']} current={row['current_outcome']} "
                f"-> hypothetical={row['hypothetical_outcome']}  changed={row['changed']}"
            )

        lines.append("")
        lines.append(f"  eligible: {result.decision_eligible}")
        if result.decision_refusal_reason:
            lines.append(f"  refused: {result.decision_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: AirportAlias #{result.written_alias_id}")
            lines.append(f"  currently admitted aliases now: {list(result.currently_admitted_aliases)!r}")
            lines.append(
                "  REMINDER: no SourceAssertion was re-evaluated by this write - alias admission and EB4 "
                "re-evaluation remain two separate, explicit operations."
            )
        elif result.decision_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this alias)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database. No default - never the real production database by accident.",
    )
    parser.add_argument("--airport-id", type=int, required=True, dest="airport_id")
    parser.add_argument("--source-id", type=int, default=None, dest="source_id")
    parser.add_argument("--source-assertion-id", type=int, default=None, dest="source_assertion_id")
    parser.add_argument("--alias", type=str, default=None)
    parser.add_argument("--evidence-excerpt", type=str, default=None, dest="evidence_excerpt")
    parser.add_argument("--analyst", type=str, default=None)
    parser.add_argument("--language", type=str, default=None)
    parser.add_argument("--script", type=str, default=None)
    parser.add_argument(
        "--evidence-class", type=str, default="AUTHORITATIVE_DIRECT", dest="evidence_class",
        choices=AIRPORT_ALIAS_EVIDENCE_CLASSES,
    )
    parser.add_argument("--status", type=str, default="ADMITTED", choices=AIRPORT_ALIAS_STATUSES)
    parser.add_argument(
        "--supersedes-alias-id", type=int, default=None, dest="supersedes_alias_id",
        help="Required when --status contradicts the latest existing row's own status for this exact "
        "(airport, alias) pair (ADMITTED <-> REJECTED/RETIRED) - must equal that latest row's own id, "
        "proving the reversal is deliberate.",
    )
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = RecordAirportAliasConfig(
        database=args.database, airport_id=args.airport_id, source_id=args.source_id,
        source_assertion_id=args.source_assertion_id, alias=args.alias, evidence_excerpt=args.evidence_excerpt,
        analyst=args.analyst, language=args.language, script=args.script, evidence_class=args.evidence_class,
        status=args.status, supersedes_alias_id=args.supersedes_alias_id, allow_database_write=args.allow_database_write,
    )
    result = run_record(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_alias is not None and not result.decision_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
