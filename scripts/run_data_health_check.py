"""Fleet Health Check CLI — the operational cockpit over the already-reviewed
FHC1/FHC3/FHC4 services (docs/architecture/fleet-health-check-*-report.md).

    python -m scripts.run_data_health_check --database data/runway_safe.db
    python -m scripts.run_data_health_check --database data/runway_safe.db --output-dir /tmp/site_check
    python -m scripts.run_data_health_check --database data/runway_safe.db --classification DETERMINISTIC_ERROR
    python -m scripts.run_data_health_check --database data/runway_safe.db --rule FH-C3 --details
    python -m scripts.run_data_health_check --database data/runway_safe.db --json

THIS SCRIPT CONTAINS NO HEALTH-RULE LOGIC OF ANY KIND. It composes exactly
three already-reviewed, already-committed service calls -
`app.services.fleet_health_check.run_fleet_hard_invariant_check()` (FHC1, 11
rules), `app.services.fleet_health_check.run_fleet_review_check()` (FHC3, 13
rules), and, only when `--output-dir` is supplied,
`app.services.fleet_health_presentation_check.run_fleet_presentation_check()`
(FHC4, 1 rule) - and renders their combined, unmodified `HealthFinding`
output. No airport-name keyword matching, no duplicate-detection logic, no
temporal-rule predicate, no governance-rule predicate, no publication-set
comparison exists anywhere in this file; every one of those already exists,
reviewed, in the three services above.

READ-ONLY, ALWAYS: `--database` is required (no default, never the real
production database by accident); the connection is opened via SQLite's own
read-only URI mode (`mode=ro`), the same pattern
scripts/list_human_review_queue.py and scripts/review_reconciliation_item.py
already established - even a coding mistake that tried to write would be
refused at the driver level, not merely by convention. `session.rollback()`
is called defensively before the session closes, exactly like
list_human_review_queue.py's own `run_review_queue()`, even though nothing
in this script's own call graph ever adds or flushes.

SCHEMA GATE: reuses five already-proven, read-only `inspect()` functions -
the three scripts/list_human_review_queue.py's own `check_schema_readiness()`
already composes (Slice 4 `intelligence_review_*`, Slice 7
`promotion_policy_*`, R4B `reviewer_actions.reconciliation_fingerprint`),
PLUS two more this CLI's own critical review added (Slice 1
`source_assertions.identity_guard_decision`/`.identity_guard_reason`, Slice
9C `source_assertions.signal_id`) - FHC1's own D2/G2/G3 rules and FHC3's own
G1 rule read exactly these five columns (FHC1's
`_build_source_assertion_governance()` selects the whole `ReviewerAction`
ORM entity, which includes `reconciliation_fingerprint`; FHC1's own D2 fact
builder and FHC3's `_build_source_assertion_governance_decisions()` both
read `SourceAssertion.signal_id`/`identity_guard_decision`/
`intelligence_review_decision`/`promotion_policy_decision` directly). A
database missing ANY of these five columns would otherwise fail with a raw,
unhelpful `sqlite3.OperationalError` instead of the clear, typed refusal
below - verified directly during this CLI's own critical review, which
found the original three-inspector composition (copied verbatim from
list_human_review_queue.py) did NOT cover Slice 1/9C and would genuinely
crash rather than refuse cleanly against a database missing those columns;
extending the gate to all five closes that gap. Refuses with
`FLEET_HEALTH_SCHEMA_MIGRATION_REQUIRED` rather than attempting to migrate,
repair, or continue partially.

PRESENTATION MODE: FHC4 (`run_fleet_presentation_check()`) only runs when
`--output-dir` is explicitly supplied - never defaulted, and never the
repository's own canonical `site/` output (see
app.services.fleet_health_presentation_check's own module docstring for why
that parameter has no default anywhere in this pipeline: `build_site()`
deletes and recreates whatever directory it is given). Without
`--output-dir`, the report explicitly states "Presentation check: NOT RUN" -
never silently treated as passing. If the real exporter raises (FH-H1's own
"run it and see" check - NOT_CURRENTLY_DETECTABLE as a row-level rule, see
app.services.fleet_health_presentation_rules's own module docstring), this
script is the one place in the whole pipeline that DOES catch that
exception - not to swallow it, but to convert it into a deterministic,
reported "Presentation check: ERROR (export failed)" state with exit code 2,
never an empty/healthy result. Every other exception (a genuine, unexpected
bug in FHC1/FHC3, whose schema gate has already been confirmed ready) is
deliberately left to propagate uncaught - "fail loud" means exactly one
narrow, documented catch, not a blanket try/except around everything.

NO REMEDIATION: this script never writes to any database, never creates a
ReviewerAction, never creates/updates/deletes a Signal/Airport/Runway/
Installation, never changes a `published` flag, never runs or imports an
`upgrade()`/`downgrade()` function, never touches the repository's canonical
`site/` output, and has no disposition workflow of any kind (no
acknowledge/resolve/suppress/ignore/repair/mark-false-positive flag exists
anywhere in this file or its argument parser). It reports; a human decides
what, if anything, to do next.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.fleet_health_check import (
    run_fleet_hard_invariant_check,
    run_fleet_review_check,
)
from app.services.fleet_health_presentation_check import run_fleet_presentation_check
from app.services.fleet_health_presentation_rules import PRESENTATION_RULE_IDS
from app.services.fleet_health_review_rules import REVIEW_RULE_IDS
from app.services.fleet_health_rules import RULE_IDS, HealthClassification, HealthFinding
from scripts.migrate_discovery_governed_evidence_slice1 import (
    inspect as inspect_identity_guard_schema,
)
from scripts.migrate_governed_signal_creation_slice9c import (
    inspect as inspect_source_assertion_signal_link_schema,
)
from scripts.migrate_intelligence_review_persistence_slice4 import (
    inspect as inspect_intelligence_review_schema,
)
from scripts.migrate_promotion_policy_persistence_slice7 import (
    inspect as inspect_promotion_policy_schema,
)
from scripts.migrate_reconciliation_confirmation_slice_r4b import (
    inspect as inspect_reconciliation_confirmation_schema,
)

__all__ = [
    "FleetHealthCliConfig",
    "FleetHealthCliReport",
    "SCHEMA_MIGRATION_REQUIRED_BLOCKER",
    "CANONICAL_SITE_DIR",
    "CANONICAL_SITE_OUTPUT_REFUSED",
    "EXIT_HEALTHY_OR_ATTENTION",
    "EXIT_DETERMINISTIC_ERROR",
    "EXIT_OPERATIONAL_FAILURE",
    "build_readonly_engine",
    "check_schema_readiness",
    "run_data_health_check",
    "overall_status",
    "render_report",
    "render_json_report",
    "main",
]

SCHEMA_MIGRATION_REQUIRED_BLOCKER = "FLEET_HEALTH_SCHEMA_MIGRATION_REQUIRED"

# The repository's own canonical static-export output directory - the one
# directory build_site() is allowed to delete/recreate in real production
# use (see scripts/export_static_site.py's own `default=Path("site")`).
# Critical-review addition: the original implementation had no guard
# against a caller passing this path (or an equivalent one) to
# `--output-dir`; since FHC4's own build_site() call deletes and recreates
# whatever directory it is given, that would have silently wiped the real
# generated site the moment a user typed `--output-dir site` from the repo
# root. `run_data_health_check()` refuses this case before ever calling
# FHC4 - see `_is_canonical_site_output()`.
REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SITE_DIR = REPO_ROOT / "site"
CANONICAL_SITE_OUTPUT_REFUSED = "CANONICAL_SITE_OUTPUT_REFUSED"

# Exit code semantics (mission-defined, documented once here - not
# scattered across the CLI): 0 covers BOTH a genuinely clean fleet and a
# fleet with only WARNING/REVIEW_REQUIRED/INFORMATIONAL findings - those are
# never grounds for a non-zero exit on their own, matching the mission's own
# explicit instruction not to use a nonzero exit merely because such
# findings exist. 1 means at least one DETERMINISTIC_ERROR finding was
# actually observed (from FHC1 and/or, when it ran, FHC4). 2 means the check
# itself could not run to completion (schema not ready, or the real exporter
# crashed) - an operational failure, categorically different from "the
# fleet has a real data/presentation problem."
EXIT_HEALTHY_OR_ATTENTION = 0
EXIT_DETERMINISTIC_ERROR = 1
EXIT_OPERATIONAL_FAILURE = 2

# All currently-known rule_ids, reused directly from the three already-
# reviewed rule registries - not reimplemented. Used only to validate
# --rule (fail loud on a typo/unknown rule rather than silently rendering
# zero findings, mission item 15) - never to decide what a rule DOES.
ALL_RULE_IDS = tuple(sorted(set(RULE_IDS) | set(REVIEW_RULE_IDS) | set(PRESENTATION_RULE_IDS)))

_CLASSIFICATION_ORDER = {
    HealthClassification.DETERMINISTIC_ERROR: 0,
    HealthClassification.DETERMINISTIC_WARNING: 1,
    HealthClassification.REVIEW_REQUIRED: 2,
    HealthClassification.INFORMATIONAL: 3,
    HealthClassification.NOT_CURRENTLY_DETECTABLE: 4,
}


@dataclass(frozen=True)
class FleetHealthCliConfig:
    database: Path
    output_dir: "Path | None" = None
    rule: "str | None" = None
    classification: "str | None" = None


@dataclass(frozen=True)
class FleetHealthCliReport:
    """The one result shape both `main()` and the test suite consume - no
    parallel/duplicated code path between CLI output and importable,
    testable behavior, matching scripts/list_human_review_queue.py's own
    `ReviewQueueReport` precedent.

    `presentation_findings` is `None` when `--output-dir` was not supplied
    (presentation check not run at all) and a (possibly empty) tuple when it
    was - the two must never be conflated; `render_report()`/`overall_status()`
    both distinguish them explicitly. `presentation_error` is the string
    form of any exception the real exporter raised (FH-H1's own "run it and
    see" check firing) - `None` whenever presentation checking was not
    requested or completed without raising.
    """

    database: str
    schema_readiness: dict
    blockers: "tuple[str, ...]" = ()
    hard_findings: "tuple[HealthFinding, ...]" = ()
    review_findings: "tuple[HealthFinding, ...]" = ()
    presentation_findings: "tuple[HealthFinding, ...] | None" = None
    presentation_error: "str | None" = None


def build_readonly_engine(database: Path):
    """Builds a SQLAlchemy engine bound to EXACTLY the resolved `database`
    path, opened in SQLite's own read-only URI mode - never
    app.database.SessionLocal/engine, never a process-global; every session
    used in this script is built from this engine, explicitly. Identical
    pattern to scripts/list_human_review_queue.py's own
    `build_readonly_engine()`."""
    resolved = database.resolve()
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def check_schema_readiness(database: Path) -> dict:
    """Read-only, via `sqlite3.connect(..., mode=ro)` inside each reused
    `inspect()` (never this script's own ORM engine) - reuses the migration
    scripts' own already-proven, already-tested schema inspection rather
    than reimplementing it.

    Composes FIVE inspectors, not the three
    scripts/list_human_review_queue.py's own `check_schema_readiness()`
    uses - this CLI's own critical review found the three-inspector
    composition alone does not cover every column FHC1/FHC3 actually read
    (`SourceAssertion.identity_guard_decision`/`.identity_guard_reason`
    (Slice 1) and `SourceAssertion.signal_id` (Slice 9C) are both read
    unconditionally by FHC1's own D2 fact builder and FHC3's own G1 rule,
    but were not gated), which a real, reproduced attack showed crashes
    with a raw `sqlite3.OperationalError` instead of refusing cleanly. All
    five columns gate readiness here."""
    identity_guard = inspect_identity_guard_schema(database)
    signal_link = inspect_source_assertion_signal_link_schema(database)
    intelligence = inspect_intelligence_review_schema(database)
    promotion = inspect_promotion_policy_schema(database)
    reconciliation_confirmation = inspect_reconciliation_confirmation_schema(database)
    ready = (
        identity_guard["identity_guard_decision_column_exists"]
        and identity_guard["identity_guard_reason_column_exists"]
        and signal_link["signal_id_column_exists"]
        and intelligence["intelligence_review_decision_column_exists"]
        and intelligence["intelligence_review_reason_column_exists"]
        and promotion["promotion_policy_decision_column_exists"]
        and promotion["promotion_policy_reason_column_exists"]
        and reconciliation_confirmation["reconciliation_fingerprint_column_exists"]
    )
    return {
        "identity_guard_decision_column_exists": identity_guard["identity_guard_decision_column_exists"],
        "identity_guard_reason_column_exists": identity_guard["identity_guard_reason_column_exists"],
        "signal_id_column_exists": signal_link["signal_id_column_exists"],
        "intelligence_review_decision_column_exists": intelligence["intelligence_review_decision_column_exists"],
        "intelligence_review_reason_column_exists": intelligence["intelligence_review_reason_column_exists"],
        "promotion_policy_decision_column_exists": promotion["promotion_policy_decision_column_exists"],
        "promotion_policy_reason_column_exists": promotion["promotion_policy_reason_column_exists"],
        "reconciliation_fingerprint_column_exists": reconciliation_confirmation["reconciliation_fingerprint_column_exists"],
        "ready": ready,
    }


def _is_canonical_site_output(output_dir: Path) -> bool:
    """True if `output_dir` resolves to the repository's own canonical
    `site/` static-export output - the one directory this CLI must never
    let FHC4 delete/recreate. See `CANONICAL_SITE_DIR`'s own module-level
    comment."""
    return Path(output_dir).resolve() == CANONICAL_SITE_DIR


def run_data_health_check(config: FleetHealthCliConfig) -> FleetHealthCliReport:
    """Never writes. Refuses (via `blockers`, never an exception) rather
    than querying an unready schema. The ONLY function that does the actual
    work - `main()` calls this and renders it; tests call this directly.

    Runs FHC1 then FHC3 unconditionally; runs FHC4 only when
    `config.output_dir` is not `None`. If `config.output_dir` resolves to
    the repository's own canonical `site/` output, FHC4 is refused
    (`presentation_error` set to `CANONICAL_SITE_OUTPUT_REFUSED: ...`)
    WITHOUT ever calling `run_fleet_presentation_check()`/`build_site()` -
    critical-review fix, see `CANONICAL_SITE_DIR`'s own comment. A real
    exporter crash during a legitimate FHC4 run is caught here (and only
    here) and recorded as `presentation_error` the same way - FHC1/FHC3
    results, already computed, are still returned alongside it either way.
    """
    database_str = str(config.database.resolve())
    schema = check_schema_readiness(config.database)
    if not schema["ready"]:
        return FleetHealthCliReport(
            database=database_str, schema_readiness=schema,
            blockers=(SCHEMA_MIGRATION_REQUIRED_BLOCKER,),
        )

    engine = build_readonly_engine(config.database)
    try:
        with Session(engine) as session:
            hard_findings = run_fleet_hard_invariant_check(session)
            review_findings = run_fleet_review_check(session)
            presentation_findings: "tuple[HealthFinding, ...] | None" = None
            presentation_error: "str | None" = None
            if config.output_dir is not None:
                if _is_canonical_site_output(config.output_dir):
                    presentation_error = (
                        f"{CANONICAL_SITE_OUTPUT_REFUSED}: refusing to run the presentation "
                        f"check against the repository's own canonical static-export output "
                        f"({CANONICAL_SITE_DIR}) - it would be deleted and regenerated. Supply "
                        f"a disposable, isolated --output-dir instead."
                    )
                else:
                    try:
                        presentation_findings = run_fleet_presentation_check(
                            session, config.output_dir
                        )
                    except Exception as exc:  # noqa: BLE001 - deliberate, narrow, documented catch (see module docstring)
                        presentation_error = f"{type(exc).__name__}: {exc}"
            session.rollback()  # defensive - this session never adds/flushes anything
    finally:
        engine.dispose()

    return FleetHealthCliReport(
        database=database_str,
        schema_readiness=schema,
        hard_findings=hard_findings,
        review_findings=review_findings,
        presentation_findings=presentation_findings,
        presentation_error=presentation_error,
    )


def _sort_key(finding: HealthFinding) -> tuple:
    return (
        _CLASSIFICATION_ORDER[finding.classification],
        finding.rule_id,
        finding.entity_ids,
    )


def _apply_filters(
    findings: "tuple[HealthFinding, ...]", config: FleetHealthCliConfig
) -> "tuple[HealthFinding, ...]":
    filtered = findings
    if config.rule is not None:
        filtered = tuple(f for f in filtered if f.rule_id == config.rule)
    if config.classification is not None:
        filtered = tuple(f for f in filtered if f.classification.value == config.classification)
    return tuple(sorted(filtered, key=_sort_key))


def overall_status(report: FleetHealthCliReport, config: FleetHealthCliConfig) -> str:
    """Deterministic, non-scored. INFORMATIONAL findings alone never make
    the fleet unhealthy. A presentation finding (FH-H2, DETERMINISTIC_ERROR)
    counts exactly like a hard-invariant finding for this purpose - both are
    the same classification, just from a different check.

    `config` is accepted but deliberately never consulted here - `--rule`/
    `--classification` are display-only filters (see `_apply_filters()`,
    used only inside `render_report()`/`render_json_report()`); this status
    is always computed from the COMPLETE, unfiltered FHC1/FHC3/FHC4 result,
    so a user can never hide a real hard error behind a filter and see
    `HEALTHY` - verified by
    `test_filtering_never_changes_overall_status_or_exit_code` (critical
    review addition).

    A crashed presentation check (`report.presentation_error is not None`)
    takes priority over every finding-derived state and reports
    `OPERATIONAL_FAILURE` - critical-review fix: the original implementation
    computed status from `hard_findings`/`review_findings` alone whenever
    `presentation_findings` was `None`, which meant a clean FHC1/FHC3 result
    plus a crashed FHC4 rendered "Overall status: HEALTHY" while the CLI
    still exited 2 - a real, reproduced, misleading "partial success"
    combination this review's own item 24 specifically warned about. See
    `render_report()`'s own "Presentation check" line for the crash detail
    text; this status field must never claim HEALTHY while that line says
    ERROR.
    """
    if report.blockers:
        return "SCHEMA_MIGRATION_REQUIRED"
    if report.presentation_error is not None:
        return "OPERATIONAL_FAILURE"
    all_findings = report.hard_findings + report.review_findings
    if report.presentation_findings is not None:
        all_findings = all_findings + report.presentation_findings
    if any(f.classification == HealthClassification.DETERMINISTIC_ERROR for f in all_findings):
        return "ERROR"
    if any(
        f.classification in (HealthClassification.DETERMINISTIC_WARNING, HealthClassification.REVIEW_REQUIRED)
        for f in all_findings
    ):
        return "ATTENTION_REQUIRED"
    return "HEALTHY"


def exit_code_for(report: FleetHealthCliReport) -> int:
    if report.blockers or report.presentation_error is not None:
        return EXIT_OPERATIONAL_FAILURE
    all_findings = report.hard_findings + (report.presentation_findings or ())
    if any(f.classification == HealthClassification.DETERMINISTIC_ERROR for f in all_findings):
        return EXIT_DETERMINISTIC_ERROR
    return EXIT_HEALTHY_OR_ATTENTION


def _presentation_status_line(report: FleetHealthCliReport) -> str:
    if report.presentation_error is not None:
        return f"Presentation check: ERROR (export failed: {report.presentation_error})"
    if report.presentation_findings is None:
        return "Presentation check: NOT RUN (no --output-dir supplied)"
    if not report.presentation_findings:
        return "Presentation check: PASS (0 findings)"
    return f"Presentation check: ERROR ({len(report.presentation_findings)} finding(s))"


_MAX_INLINE_ENTITY_IDS = 15


def _render_finding_line(finding: HealthFinding, *, details: bool) -> "list[str]":
    # Critical-review addition: a single HealthFinding can legitimately
    # cover many entities (e.g. real FH-F1 findings covering 67 Signals) -
    # capping the default inline id list keeps output "usable" (mission
    # item 21) without losing data, since the full, untruncated list is
    # always available via --json regardless of this cap.
    ids = finding.entity_ids
    if len(ids) > _MAX_INLINE_ENTITY_IDS:
        shown = ", ".join(str(i) for i in ids[:_MAX_INLINE_ENTITY_IDS])
        ids_text = f"{shown}, ... (+{len(ids) - _MAX_INLINE_ENTITY_IDS} more, see --json for the full list)"
    else:
        ids_text = ", ".join(str(i) for i in ids)
    lines = [f"    [{finding.entity_type}: {ids_text}] {finding.summary}"]
    if details:
        for key in sorted(finding.structured_evidence):
            lines.append(f"        {key}={finding.structured_evidence[key]!r}")
    return lines


def _render_findings_section(
    title: str, findings: "tuple[HealthFinding, ...]", *, details: bool
) -> "list[str]":
    lines = [title]
    if not findings:
        lines.append("  (none)")
        return lines
    by_rule: "dict[str, list[HealthFinding]]" = {}
    order: "list[str]" = []
    for finding in findings:
        if finding.rule_id not in by_rule:
            order.append(finding.rule_id)
        by_rule.setdefault(finding.rule_id, []).append(finding)
    for rule_id in order:
        group = by_rule[rule_id]
        lines.append(f"  {rule_id} ({len(group)} finding(s))")
        for finding in group:
            lines.extend(_render_finding_line(finding, details=details))
    return lines


def render_report(
    report: FleetHealthCliReport, config: FleetHealthCliConfig, *, details: bool = False
) -> str:
    lines: "list[str]" = []
    lines.append("RWI FLEET HEALTH")
    lines.append("=" * 60)
    lines.append(f"Database: {report.database}")

    if report.blockers:
        lines.append(f"BLOCKED: {', '.join(report.blockers)}")
        lines.append(f"schema_readiness: {report.schema_readiness}")
        return "\n".join(lines) + "\n"

    hard = _apply_filters(report.hard_findings, config)
    review = _apply_filters(report.review_findings, config)
    presentation = (
        _apply_filters(report.presentation_findings, config)
        if report.presentation_findings is not None
        else ()
    )

    warnings = tuple(f for f in review if f.classification == HealthClassification.DETERMINISTIC_WARNING)
    review_required = tuple(f for f in review if f.classification == HealthClassification.REVIEW_REQUIRED)
    informational = tuple(f for f in review if f.classification == HealthClassification.INFORMATIONAL)

    lines.append("")
    # Overall status/exit code are always computed from the COMPLETE,
    # unfiltered result (overall_status() never reads config.rule/
    # .classification) - a --rule/--classification filter can never hide a
    # real hard error behind a HEALTHY status. See overall_status()'s own
    # docstring.
    lines.append(f"Overall status: {overall_status(report, config)}")
    lines.append(_presentation_status_line(report))
    if config.rule is not None or config.classification is not None:
        lines.append(
            "NOTE: display filtered "
            f"(rule={config.rule!r}, classification={config.classification!r}) - counts below "
            "reflect the filter; Overall status above always reflects the COMPLETE result."
        )
    lines.append("")
    # Counts below are FINDING counts, not affected-entity counts - a
    # single finding (e.g. a real FH-F1 finding) can cover many entities at
    # once (critical-review clarification, mission item 11).
    lines.append(f"Hard errors ............. {len(hard)} finding(s)")
    lines.append(f"Warnings ................ {len(warnings)} finding(s)")
    lines.append(f"Review required ......... {len(review_required)} finding(s)")
    lines.append(f"Informational ........... {len(informational)} finding(s)")
    lines.append(
        "Presentation errors ..... "
        + (f"{len(presentation)} finding(s)" if report.presentation_findings is not None else "-")
    )

    lines.append("")
    lines.extend(_render_findings_section("Hard errors (DETERMINISTIC_ERROR)", hard, details=details))
    lines.append("")
    lines.extend(_render_findings_section("Warnings (DETERMINISTIC_WARNING)", warnings, details=details))
    lines.append("")
    lines.extend(_render_findings_section("Review required (REVIEW_REQUIRED)", review_required, details=details))
    lines.append("")
    lines.extend(_render_findings_section("Informational (INFORMATIONAL)", informational, details=details))
    if report.presentation_findings is not None:
        lines.append("")
        lines.extend(
            _render_findings_section("Presentation errors (DETERMINISTIC_ERROR)", presentation, details=details)
        )

    return "\n".join(lines) + "\n"


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _finding_to_dict(finding: HealthFinding) -> dict:
    return {
        "rule_id": finding.rule_id,
        "classification": finding.classification.value,
        "entity_type": finding.entity_type,
        "entity_ids": list(finding.entity_ids),
        "airport_id": finding.airport_id,
        "summary": finding.summary,
        "structured_evidence": dict(finding.structured_evidence),
    }


def render_json_report(report: FleetHealthCliReport, config: FleetHealthCliConfig) -> str:
    """Stable, narrow schema - no ORM serialization, no score. Findings are
    the same, already-reviewed `HealthFinding` fields; nothing computed here
    beyond what render_report() also derives (status, presentation-executed
    flag)."""
    if report.blockers:
        payload = {
            "database": report.database,
            "blocked": True,
            "blockers": list(report.blockers),
            "schema_readiness": report.schema_readiness,
        }
        return json.dumps(payload, indent=2, default=_json_default)

    hard = _apply_filters(report.hard_findings, config)
    review = _apply_filters(report.review_findings, config)
    presentation_executed = report.presentation_findings is not None
    presentation = (
        _apply_filters(report.presentation_findings, config) if presentation_executed else ()
    )

    payload = {
        "database": report.database,
        "blocked": False,
        "overall_status": overall_status(report, config),
        "presentation_check_executed": presentation_executed,
        "presentation_check_error": report.presentation_error,
        "counts": {
            "hard_errors": len(hard),
            "warnings": len(
                [f for f in review if f.classification == HealthClassification.DETERMINISTIC_WARNING]
            ),
            "review_required": len(
                [f for f in review if f.classification == HealthClassification.REVIEW_REQUIRED]
            ),
            "informational": len(
                [f for f in review if f.classification == HealthClassification.INFORMATIONAL]
            ),
            "presentation_errors": len(presentation) if presentation_executed else None,
        },
        "hard_findings": [_finding_to_dict(f) for f in hard],
        "review_findings": [_finding_to_dict(f) for f in review],
        "presentation_findings": (
            [_finding_to_dict(f) for f in presentation] if presentation_executed else None
        ),
    }
    return json.dumps(payload, indent=2, default=_json_default)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database to check, read-only. No default - never the real "
        "production database by accident.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="If supplied, also runs the FHC4 presentation cross-check (FH-H2) by exporting "
        "into this directory. WARNING: this directory is DELETED and fully recreated by the "
        "real static exporter - always pass a disposable, isolated path. The repository's own "
        "canonical site/ output is refused outright (never deleted), regardless of how the "
        "path is spelled. Without this flag, the presentation check is not run at all.",
    )
    parser.add_argument(
        "--rule", default=None, choices=ALL_RULE_IDS,
        help="Show only findings for this rule_id, e.g. FH-C3. An unknown rule_id is a "
        "usage error (fails loud), never silently zero findings.",
    )
    parser.add_argument(
        "--classification", default=None,
        choices=[c.value for c in HealthClassification],
        help="Show only findings of this classification.",
    )
    parser.add_argument(
        "--details", action="store_true",
        help="Show each finding's structured_evidence (omitted by default to keep output compact).",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report instead of text.")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = FleetHealthCliConfig(
        database=args.database, output_dir=args.output_dir,
        rule=args.rule, classification=args.classification,
    )
    report = run_data_health_check(config)
    if args.json:
        print(render_json_report(report, config))
    else:
        print(render_report(report, config, details=args.details))
    return exit_code_for(report)


if __name__ == "__main__":
    sys.exit(main())
