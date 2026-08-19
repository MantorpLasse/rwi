"""Read-only human review queue report
(docs/architecture/human-review-queue-slice8-report.md, Slice 8).

    python -m scripts.list_human_review_queue --database data/runway_safe.db --limit 20

Prints every SourceAssertion row whose already-persisted
`promotion_policy_decision` is exactly "HUMAN_REVIEW_REQUIRED" - never
AUTO_ELIGIBLE, never DO_NOT_PROMOTE, never NULL/unevaluated rows. Performs
ZERO writes: the database connection is opened in SQLite's own read-only
URI mode (`mode=ro`), so even a coding mistake that tried to write would be
refused at the driver level, not merely by convention. Never creates a
backup, never runs or imports an `upgrade()`/`downgrade()` function from any
migration script, never fetches network, never touches `Signal`.

SCHEMA GATE: before running any ORM query, this script inspects the target
database's raw schema (via the existing migration scripts' own read-only
`inspect()` functions, reused rather than reimplemented) and refuses with
`REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED` if either the Slice 4
(`intelligence_review_*`) or Slice 7 (`promotion_policy_*`) columns are
missing - it does not attempt to migrate, and it does not silently query a
schema that would raise an ORM-level `OperationalError` instead of a clear,
typed refusal.

`run_review_queue()` is the one function that does the work (schema check,
then query) and returns a single, importable report - `main()` and the
tests both consume it, so there is exactly one code path, not two.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.human_review_claim_enrichment import enrich_claims
from app.services.human_review_queue import HumanReviewItem, list_human_review_items
from scripts.migrate_intelligence_review_persistence_slice4 import inspect as inspect_intelligence_review_schema
from scripts.migrate_promotion_policy_persistence_slice7 import inspect as inspect_promotion_policy_schema

DEFAULT_DATABASE = Path("data/runway_safe.db")
DEFAULT_LIMIT = 20
SCHEMA_MIGRATION_REQUIRED_BLOCKER = "REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED"


@dataclass(frozen=True)
class ReviewQueueConfig:
    database: Path
    limit: "int | None" = DEFAULT_LIMIT


@dataclass(frozen=True)
class ReviewQueueReport:
    """The one result shape both `main()` and the test suite consume - no
    parallel/duplicated code path between CLI output and importable,
    testable behavior."""

    database: str
    schema_readiness: dict
    blockers: "tuple[str, ...]" = ()
    items: "tuple[HumanReviewItem, ...]" = ()


def build_readonly_engine(database: Path):
    """Builds a SQLAlchemy engine bound to EXACTLY the resolved `database`
    path, opened in SQLite's own read-only URI mode - the only
    database-binding function in this module. Never
    app.database.SessionLocal/engine, never a process-global; every
    session used in this script is built from this engine, explicitly."""
    resolved = database.resolve()
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def check_schema_readiness(database: Path) -> dict:
    """Read-only, via `sqlite3.connect(..., mode=ro)` inside each reused
    `inspect()` (never this script's own ORM engine, and never a write
    connection) - reuses the migration scripts' own already-proven,
    already-tested schema inspection rather than reimplementing it."""
    intelligence = inspect_intelligence_review_schema(database)
    promotion = inspect_promotion_policy_schema(database)
    ready = (
        intelligence["intelligence_review_decision_column_exists"]
        and intelligence["intelligence_review_reason_column_exists"]
        and promotion["promotion_policy_decision_column_exists"]
        and promotion["promotion_policy_reason_column_exists"]
    )
    return {
        "intelligence_review_decision_column_exists": intelligence["intelligence_review_decision_column_exists"],
        "intelligence_review_reason_column_exists": intelligence["intelligence_review_reason_column_exists"],
        "promotion_policy_decision_column_exists": promotion["promotion_policy_decision_column_exists"],
        "promotion_policy_reason_column_exists": promotion["promotion_policy_reason_column_exists"],
        "source_assertions_count": promotion["source_assertions_count"],
        "ready": ready,
    }


def run_review_queue(config: ReviewQueueConfig) -> ReviewQueueReport:
    """Never writes. Refuses (via `blockers`, never an exception) rather
    than querying an unready schema. The ONLY function that does the actual
    work - `main()` calls this and renders it; tests call this directly."""
    database_str = str(config.database.resolve())
    schema = check_schema_readiness(config.database)
    if not schema["ready"]:
        return ReviewQueueReport(
            database=database_str, schema_readiness=schema, blockers=(SCHEMA_MIGRATION_REQUIRED_BLOCKER,),
        )

    engine = build_readonly_engine(config.database)
    try:
        with Session(engine) as session:
            items = list_human_review_items(session, limit=config.limit)
            session.rollback()  # defensive - this session never adds/flushes anything
    finally:
        engine.dispose()

    return ReviewQueueReport(database=database_str, schema_readiness=schema, items=items)


# ---------------------------------------------------------------------------
# Human-readable report rendering - text formatting only, no new data.
# ---------------------------------------------------------------------------


def _format_financial(claim) -> "str | None":
    if claim.financial is None:
        return None
    return f"{claim.financial.amount} {claim.financial.currency} — {claim.financial.semantic_role}"


def _format_relationship(claim) -> "str | None":
    if claim.relationship is None:
        return None
    scope = f" ({claim.relationship.scope})" if claim.relationship.scope else ""
    return f"{claim.relationship.party} — {claim.relationship.role}{scope}"


def _format_temporal(claim) -> "str | None":
    if claim.temporal is None:
        return None
    as_of = f", as of {claim.temporal.as_of_date}" if claim.temporal.as_of_date else ""
    detail = f" ({claim.temporal.detail})" if claim.temporal.detail else ""
    return f"{claim.temporal.qualifier.value}{as_of}{detail}"


def render_item_report(item: HumanReviewItem) -> str:
    lines: "list[str]" = []
    lines.append("=" * 78)
    lines.append(f"SourceAssertion #{item.source_assertion_id}")
    lines.append("=" * 78)

    lines.append("")
    lines.append("Airport")
    lines.append(f"  id={item.airport_id}  code={item.airport_code}  name={item.airport_name}")

    lines.append("")
    lines.append("Source")
    lines.append(f"  id={item.source_id}  title={item.source_title!r}")
    lines.append(f"  publisher={item.source_publisher}  url={item.source_url}")
    lines.append(f"  document_reference={item.source_document_reference}")
    lines.append(
        f"  reliability_level (existing coarse field, NOT a PromotionPolicy "
        f"SourceAuthorityTier)={item.source_reliability_level_raw!r}"
    )

    lines.append("")
    lines.append("Evidence")
    lines.append(f"  artifact_identity={item.artifact_identity}")
    lines.append(f"  source_locator={item.source_locator}")
    lines.append(f"  raw_fragment_hash={item.raw_fragment_hash}")
    lines.append(f"  parser_identifier={item.parser_identifier}")
    lines.append("  raw_relevant_text:")
    for text_line in (item.raw_relevant_text or "(none preserved)").splitlines():
        lines.append(f"    {text_line}")

    lines.append("")
    lines.append("Identity review")
    lines.append(f"  identity_guard_decision={item.identity_guard_decision}")
    lines.append(f"  identity_guard_reason={item.identity_guard_reason}")

    lines.append("")
    lines.append("Intelligence review")
    lines.append(f"  intelligence_review_decision={item.intelligence_review_decision}")
    lines.append(f"  intelligence_review_reason={item.intelligence_review_reason}")

    lines.append("")
    lines.append("Promotion policy")
    lines.append(f"  promotion_policy_decision={item.promotion_policy_decision}")
    lines.append(f"  promotion_policy_reason={item.promotion_policy_reason}")

    if item.invariant_warnings:
        lines.append("")
        lines.append("!! INVARIANT WARNINGS !!")
        for warning in item.invariant_warnings:
            lines.append(f"  {warning}")

    lines.append("")
    lines.append("Claims / constraints")
    claims = enrich_claims(item)
    if claims is None:
        lines.append(
            f"  No source-specific claim extraction available for parser_identifier="
            f"{item.parser_identifier!r} - review the raw evidence text above directly."
        )
    elif not claims:
        lines.append("  Claim extraction ran but produced no claims for this text.")
    else:
        not_established: "list[str]" = []
        for claim in claims:
            lines.append(f"  - [{claim.category.value}] {claim.subject}: {claim.statement}")
            financial_line = _format_financial(claim)
            if financial_line:
                lines.append(f"      {financial_line}")
                if claim.financial.not_established:
                    lines.append(f"      NOT ESTABLISHED: {', '.join(claim.financial.not_established)}")
                    not_established.extend(claim.financial.not_established)
            relationship_line = _format_relationship(claim)
            if relationship_line:
                lines.append(f"      {relationship_line}")
            temporal_line = _format_temporal(claim)
            if temporal_line:
                lines.append(f"      {temporal_line}")

        lines.append("")
        lines.append("What is NOT established")
        if not_established:
            for label in sorted(set(not_established)):
                lines.append(f"  - {label}")
        else:
            lines.append("  (no explicit not_established constraints on the re-derived claims)")

    return "\n".join(lines)


def render_report(report: ReviewQueueReport) -> str:
    if report.blockers:
        return (
            f"Database: {report.database}\n"
            f"BLOCKED: {', '.join(report.blockers)}\n"
            f"schema_readiness: {report.schema_readiness}\n"
        )
    if not report.items:
        return f"Database: {report.database}\nHuman review queue is empty. Nothing requires review.\n"

    parts = [f"Database: {report.database}\n{len(report.items)} item(s) in the human review queue:\n"]
    for item in report.items:
        parts.append(render_item_report(item))
        parts.append("")
    return "\n".join(parts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = ReviewQueueConfig(database=args.database, limit=args.limit)
    report = run_review_queue(config)
    print(render_report(report))
    return 1 if report.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
