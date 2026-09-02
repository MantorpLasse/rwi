"""Selection -> Durable Evidence Persistence V1 capture runner (Mission
#15B, following the recon in Mission #15A).

Orchestrates the already-committed, unmodified pipeline:

    preserved Snapshot (app.services.snapshot_extraction)
        -> GenericPdfExtractor (app.extraction.generic_pdf)
        -> FragmentSelection (app.selection.fragment_selection)
        -> human KEEP (app.selection.review)
        -> structured airport-name extraction (app.selection.structured_extraction,
           run internally by app.selection.candidate_fragment_adapter)
        -> CandidateFragment (app.services.discovery_candidate_fragment)
        -> Evidence Attachment Guard (app.services.evidence_attachment_guard)
        -> UAC3 discovery-identity orchestration
           (app.services.unknown_airport_discovery_integration.resolve_or_persist_discovery_identity)
           -> exactly one of: known-canonical attachment / ambiguous-known
              identity / governed UnknownAirportCandidate formation /
              unresolved identity (composed, not reimplemented, by this
              runner - see module docstring of that orchestrator)

This module implements NO new pipeline logic of its own. It is an
adapter/orchestrator, not a new persistence subsystem: identity routing,
Source/SourceAssertion creation and reuse, EvidenceBag snapshotting, and
UnknownAirportCandidate fingerprinting are all delegated entirely to the
already-committed, already-reviewed services above - this runner only
wires them together, plus CLI safety gates and preview formatting,
mirroring scripts/capture_mac_discovery.py's own established structure
and safety model as closely as the two pipelines' real differences allow.

SAFETY MODEL (default-closed, mirrors capture_mac_discovery.py exactly):
  - No database write unless BOTH --apply AND --allow-database-write.
  - A real apply additionally requires the EB2/UAC evidence-bag persistence
    schema to already exist on the target database (checked the same way
    persist_discovery_fragment() itself checks, via
    app.services.discovery_evidence_persistence._verify_evidence_bag_schema_ready -
    reused, not reimplemented) and a matching --expected-fingerprint,
    computed fresh at apply time - a stale/mismatched fingerprint refuses
    to write.
  - EVERY database operation is bound to the SAME explicitly-resolved
    --database path via scripts.capture_mac_discovery.build_engine (reused
    directly). This module never imports app.database.SessionLocal or
    app.database.engine (the process-global default database) anywhere.
  - A real apply against a persistent file database backs it up first
    (scripts.migrate_discovery_governed_evidence_slice1.backup_database,
    reused directly) unless --skip-backup, which the CLI help text marks
    "isolated/temp DBs only" exactly like capture_mac_discovery.py's own.

CANDIDATE-AIRPORT V1 DECISION (Mission #15A Part C, Mission #15B Part C):
  Candidate Airport IDs are ALWAYS explicitly supplied by the operator via
  --candidate-airport-id (repeatable), or the operator explicitly opts out
  via --no-known-candidates to deliberately exercise the unknown-airport-
  only path. This runner never derives candidate Airport IDs from the
  search/discovery seed, DiscoveryContext, SelectionReason, fragment text,
  airport-name extraction alone, or document metadata - the exact
  prohibition Mission #15A Part C states, and the exact behavior already
  proven by test_selection_reason_alone_has_zero_effect_on_identity_guard
  (tests/test_selection_identity_guard_integration.py): a search/discovery
  seed genuinely has zero effect on IdentityGuard in this pipeline. If a
  reviewer passes --identity-name/--identity-iata/--identity-icao, that
  value affects ONLY which fragments FragmentSelection includes (the same,
  pre-existing, frozen Mission #13B behavior) and is displayed in the
  report purely as review context - it is never passed to
  evaluate_attachment_for_candidates() or resolve_or_persist_discovery_identity()
  in any form.

DRY-RUN GUARANTEE: the preview pass performs ONLY SELECT queries plus the
existing, reused, read-only scripts.capture_mac_discovery.plan_governed_persistence()
helper (itself SELECT-only, proven zero-mutation by its own test suite) -
it never calls session.add()/flush()/commit() for governed evidence. The
preserved-document read path (load_snapshot_for_extraction) is also
read-only. Refusing at any apply gate leaves the session exactly as clean
as a pure dry-run, since nothing above the gates ever mutates it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from sqlalchemy.orm import Session

from app.extraction.generic_pdf import extract_pdf
from app.models import Airport
from app.selection.fragment_selection import AirportIdentityContext, select_fragments
from app.selection.review import ReviewDecision, apply_keep_decisions
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.discovery_evidence_persistence import (
    EvidenceBagSchemaRequiredError,
    _verify_evidence_bag_schema_ready,
)
from app.services.evidence_attachment_guard import (
    CandidateAirport,
    candidate_airport_from_airport_like,
    evaluate_attachment_for_candidates,
)
from app.services.selection_source_metadata import build_discovery_source_metadata_for_snapshot
from app.services.snapshot_extraction import load_snapshot_for_extraction
from app.services.unknown_airport_discovery_integration import (
    DiscoveryIdentityResolutionResult,
    resolve_or_persist_discovery_identity,
)
# Reused directly, never duplicated - the exact convention
# scripts/capture_mac_discovery.py's own module docstring establishes for
# every helper it imports from elsewhere. build_engine gives this runner
# the same explicit-DB-binding guarantee as that script, without a second
# implementation of it; plan_governed_persistence is a generic, read-only,
# SELECT-only preview of Source/SourceAssertion reuse that has no MAC-
# specific assumption baked into it (it takes only session/document_identity/
# fragment/decisions - all supplied here exactly as capture_mac_discovery.py
# itself supplies them).
from scripts.capture_mac_discovery import (
    PlannedGovernedEvidence,
    build_engine,
    plan_governed_persistence,
)
from scripts.migrate_discovery_governed_evidence_slice1 import BACKUP_DIRECTORY, backup_database

DEFAULT_DATABASE = Path("data/runway_safe.db")

_DISCLAIMER = (
    "PERSISTED CITATION != ACCEPTED EVIDENCE. A persisted SourceAssertion means only "
    "'this exact preserved source contains this exact excerpt, machine identity-matched as shown' - "
    "never an airport confirmation, an EMAS confirmation, a project confirmation, or a claim accepted. "
    "ATTACH_PROVISIONAL/ATTACH_CONFIRMED describe IDENTITY MATCH ONLY, not installation, approval, or fact."
)


class PersistRunnerError(ValueError):
    """Raised for any refuse-to-proceed safety-gate failure."""


# ---------------------------------------------------------------------------
# Fingerprint - extends capture_mac_discovery.compute_plan_fingerprint's own
# row shape with the explicitly-supplied candidate_airport_ids (Mission
# #15A Part H / Mission #15B Part H): capture_mac_discovery's own
# fingerprint does not need this, because ITS candidates are re-derived
# deterministically from real DB topology identically at preview and apply
# time; this runner's candidates are an explicit, human-supplied,
# otherwise-unconstrained list, so two different candidate sets that
# happen to produce the same winning outcome/code must still fingerprint
# differently, or a stale preview could survive an apply-time candidate-
# list change undetected.
# ---------------------------------------------------------------------------


def compute_selected_fragment_plan_fingerprint(
    planned: Sequence[PlannedGovernedEvidence], candidate_airport_ids: Sequence[int]
) -> str:
    rows = sorted(
        (
            p.document_identity, p.fragment_identity[0], p.fragment_identity[1], p.fragment_identity[2],
            p.guard_outcome, p.attached_airport_code or "", p.source_external_id,
            p.would_form_unknown_airport_candidate,
        )
        for p in planned
    )
    payload = json.dumps(
        {"rows": rows, "candidate_airport_ids": sorted(candidate_airport_ids)},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Config / top-level orchestration.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistConfig:
    database: Path = DEFAULT_DATABASE
    snapshot_id: int = 0
    keep_indices: frozenset = field(default_factory=frozenset)
    candidate_airport_ids: tuple = ()
    no_known_candidates: bool = False
    identity_name: "str | None" = None
    identity_iata: "str | None" = None
    identity_icao: "str | None" = None
    apply: bool = False
    allow_database_write: bool = False
    expected_fingerprint: "str | None" = None
    skip_backup: bool = False
    backup_directory: Path = BACKUP_DIRECTORY


def run_persist(config: PersistConfig) -> dict:
    """The single entry point both the CLI and tests use. Every database
    operation is bound to config.database, explicitly, via build_engine()
    (reused from scripts.capture_mac_discovery) - never app.database.SessionLocal."""
    if config.apply and not config.allow_database_write:
        raise PersistRunnerError("--apply requires --allow-database-write.")
    if config.allow_database_write and not config.apply:
        raise PersistRunnerError("--allow-database-write requires --apply.")
    if config.no_known_candidates and config.candidate_airport_ids:
        raise PersistRunnerError("--no-known-candidates cannot be combined with --candidate-airport-id.")

    database = Path(config.database)
    report: dict = {
        "database": str(database.resolve()),
        "snapshot_id": config.snapshot_id,
        "document_identity": None,
        "fragments_selected": 0,
        "fragments_kept": 0,
        "search_seed_display_only": {
            "name": config.identity_name, "iata": config.identity_iata, "icao": config.identity_icao,
        },
        "candidate_airport_ids": list(config.candidate_airport_ids),
        "no_known_candidates": config.no_known_candidates,
        "fragments_preview": [],
        "planned_evidence": [],
        "plan_fingerprint": None,
        "schema_ready": None,
        "schema_error": None,
        "applied": False,
        "apply_result": [],
        "blockers": [],
    }

    engine = build_engine(database)
    with Session(engine) as session:
        try:
            loaded = load_snapshot_for_extraction(session, config.snapshot_id)
        except ValueError as exc:
            raise PersistRunnerError(f"PRESERVED_SNAPSHOT_NOT_AVAILABLE: {exc}") from exc

        try:
            source_metadata = build_discovery_source_metadata_for_snapshot(session, config.snapshot_id)
        except ValueError as exc:
            raise PersistRunnerError(f"PRESERVED_SNAPSHOT_NOT_AVAILABLE: {exc}") from exc

        report["document_identity"] = source_metadata.document_identity

        identity_context = None
        if config.identity_name:
            identity_context = AirportIdentityContext(
                name=config.identity_name, iata_code=config.identity_iata, icao_code=config.identity_icao,
            )

        document = extract_pdf(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)
        selection = select_fragments(document, airport_identity=identity_context)
        reviews = apply_keep_decisions(
            selection, keep_indices=config.keep_indices, document_title=source_metadata.title, url=source_metadata.url,
        )
        report["fragments_selected"] = len(selection.fragments)
        # Every selected fragment is shown here - independent of whether it
        # was KEPT - so an operator (or a test) can see the full numbered
        # list, exactly like scripts/review_fragment_selection.py's own
        # listing, BEFORE deciding what to KEEP. Never mutated by --keep.
        report["fragments_preview"] = [
            {
                "fragment_index": i,
                "page_number": f.page_number,
                "source_locator": f"page:{f.page_number};chars:{f.start_offset}-{f.end_offset}",
                "excerpt": f.text[:150].replace("\n", " "),
                # Full, untruncated fragment text (Mission #15B Part G: "the
                # fragment shown during preview must be the exact fragment
                # that would be persisted") - the excerpt above is a display
                # convenience only, never the sole review representation.
                "text": f.text,
            }
            for i, f in enumerate(selection.fragments, start=1)
        ]

        kept = [(i, r) for i, r in enumerate(reviews, start=1) if r.decision == ReviewDecision.KEEP]
        report["fragments_kept"] = len(kept)

        if kept and not config.candidate_airport_ids and not config.no_known_candidates:
            raise PersistRunnerError(
                "MISSING_CANDIDATE_AIRPORT_IDS: pass --candidate-airport-id at least once, or "
                "--no-known-candidates to explicitly test the unknown-airport-only path. "
                "Candidate Airport IDs are never derived automatically (Mission #15A Part C)."
            )

        candidate_airports: list[CandidateAirport] = []
        invalid_ids: list[int] = []
        for airport_id in config.candidate_airport_ids:
            airport = session.get(Airport, airport_id)
            if airport is None:
                invalid_ids.append(airport_id)
                continue
            candidate_airports.append(candidate_airport_from_airport_like(airport))
        if invalid_ids:
            raise PersistRunnerError(f"INVALID_CANDIDATE_AIRPORT_ID(S): no Airport exists for id(s) {invalid_ids}.")

        schema_ready = True
        try:
            _verify_evidence_bag_schema_ready(session)
        except EvidenceBagSchemaRequiredError as exc:
            schema_ready = False
            report["schema_error"] = str(exc)
        report["schema_ready"] = schema_ready

        # --- PLANNING PASS: read-only, always runs, never gated. ---
        planned: list[PlannedGovernedEvidence] = []
        evaluated: list[tuple[int, CandidateFragment]] = []
        for index, review in kept:
            fragment = review.candidate_fragment
            assert fragment is not None
            evidence = candidate_fragment_to_evidence_bag(fragment)
            decisions = evaluate_attachment_for_candidates(evidence, candidate_airports) if candidate_airports else {}
            plan = plan_governed_persistence(session, source_metadata.document_identity, fragment, decisions)
            planned.append(plan)
            evaluated.append((index, fragment))

            report["planned_evidence"].append({
                "fragment_index": index,
                "source_locator": fragment.source_locator,
                "raw_text": fragment.raw_text,
                "airport_names": sorted(fragment.airport_names),
                "airport_identifiers": sorted(fragment.airport_identifiers),
                "candidate_airport_ids": list(config.candidate_airport_ids),
                "guard_outcome": plan.guard_outcome,
                "guard_reason": plan.guard_reason,
                "attached_airport_id": plan.attached_airport_id,
                "attached_airport_code": plan.attached_airport_code,
                "source_would_be_created": plan.source_would_be_created,
                "source_id_if_existing": plan.source_id_if_existing,
                "source_assertion_would_be_created": plan.source_assertion_would_be_created,
                "source_assertion_id_if_existing": plan.source_assertion_id_if_existing,
                "would_form_unknown_airport_candidate": plan.would_form_unknown_airport_candidate,
            })

        fingerprint = compute_selected_fragment_plan_fingerprint(planned, config.candidate_airport_ids)
        report["plan_fingerprint"] = fingerprint

        if not config.apply:
            session.rollback()
            return report

        # --- APPLY GATES: must ALL pass before any persistence attempt. ---
        if not schema_ready:
            session.rollback()
            report["blockers"].append("EVIDENCE_BAG_SCHEMA_REQUIRED")
            return report
        if config.expected_fingerprint is None or config.expected_fingerprint != fingerprint:
            session.rollback()
            report["blockers"].append(
                f"FINGERPRINT_MISMATCH: expected {config.expected_fingerprint!r}, computed {fingerprint!r}."
            )
            return report

        if not config.skip_backup:
            backup_path = backup_database(database, config.backup_directory)
            report["backup_path"] = str(backup_path)

        apply_results: list[DiscoveryIdentityResolutionResult] = []
        for _index, fragment in evaluated:
            apply_results.append(
                resolve_or_persist_discovery_identity(session, source_metadata, fragment, candidate_airports)
            )
        session.commit()
        report["applied"] = True
        report["apply_result"] = [
            {
                "routing_outcome": r.outcome.value,
                "attachment_outcome": r.attachment_outcome.value,
                "source_id": r.source_id, "source_created": r.source_created,
                "source_assertion_id": r.source_assertion_id, "source_assertion_created": r.source_assertion_created,
                "attached_airport_id": r.attached_airport_id,
                "unknown_airport_candidate_id": r.unknown_airport_candidate_id,
                "unknown_airport_candidate_created": r.unknown_airport_candidate_created,
                "evidence_bag_snapshot_id": r.evidence_bag_snapshot_id,
            }
            for r in apply_results
        ]
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--snapshot-id", required=True, type=int)
    parser.add_argument(
        "--keep", default=None,
        help="Comma-separated 1-based fragment positions to KEEP (e.g. '1,4,12'). Omit to KEEP nothing.",
    )
    parser.add_argument(
        "--candidate-airport-id", type=int, action="append", default=[], dest="candidate_airport_ids",
        help="Explicit, operator-supplied candidate Airport id to evaluate against (repeatable). "
        "Never derived automatically - see module docstring.",
    )
    parser.add_argument(
        "--no-known-candidates", action="store_true",
        help="Explicitly evaluate KEPT fragments against zero known-Airport candidates "
        "(exercises the unknown-airport/unresolved path only). Required in place of "
        "--candidate-airport-id when that is the intended test.",
    )
    parser.add_argument("--identity-name", default=None, help="Optional search-seed airport name (review context only, never affects IdentityGuard).")
    parser.add_argument("--identity-iata", default=None)
    parser.add_argument("--identity-icao", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--expected-fingerprint", type=str, default=None)
    parser.add_argument("--skip-backup", action="store_true", help="isolated/temp DBs only")
    parser.add_argument("--backup-directory", type=Path, default=BACKUP_DIRECTORY)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    try:
        keep_indices = frozenset(int(x) for x in args.keep.split(",")) if args.keep else frozenset()
    except ValueError:
        print(f"--keep must be comma-separated integers, got: {args.keep!r}")
        return 2

    config = PersistConfig(
        database=args.database, snapshot_id=args.snapshot_id, keep_indices=keep_indices,
        candidate_airport_ids=tuple(args.candidate_airport_ids), no_known_candidates=args.no_known_candidates,
        identity_name=args.identity_name, identity_iata=args.identity_iata, identity_icao=args.identity_icao,
        apply=args.apply, allow_database_write=args.allow_database_write,
        expected_fingerprint=args.expected_fingerprint, skip_backup=args.skip_backup,
        backup_directory=args.backup_directory,
    )
    print(_DISCLAIMER)
    print()
    try:
        report = run_persist(config)
    except PersistRunnerError as exc:
        print(f"Refused: {exc}")
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0 if not report.get("blockers") else 1


if __name__ == "__main__":
    raise SystemExit(main())
