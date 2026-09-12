"""RWI HQ "Human KEEP -> Governed Evidence Persistence Bridge" mission.

    Snapshot (already fetched - scripts/fetch_research_candidate.py)
        -> load_snapshot_for_extraction() / extract_document()      [unmodified]
        -> select_fragments()                                        [unmodified]
        -> apply_keep_decisions(keep_indices=...)                    [unmodified -
           the EXACT human KEEP gate scripts/review_fragment_selection.py
           already uses - this script reconstructs the identical
           CandidateFragment(s) deterministically from the same inputs,
           never a parallel/second selection implementation]
        -> CandidateFragment(s)
        -> [THIS SCRIPT'S OWN NEW STEP] build_metadata_from_kept_fragments() +
           preview_kept_fragments() / persist_kept_fragments() below - the
           adapter to
           app.services.discovery_evidence_persistence.DiscoverySourceMetadata
           + explicit, already-known airport_id/assertion_type
        -> app.services.known_airport_evidence_persistence
           .plan_known_airport_evidence_persistence()  [PREVIEW, zero writes]
           .apply_known_airport_evidence_persistence() [PERSISTENCE, only with
           --allow-database-write]
        -> Source (create/reuse) + SourceAssertion (create/reuse)
        -> STOP (never a Signal, never a ReviewerAction, never a publication
           change - known_airport_evidence_persistence itself never creates
           any of those; see its own module docstring)

PART 1 RECON (documented here, not re-derived elsewhere):
  1. Exact object emitted after human KEEP today: scripts/review_fragment_selection.py
     calls app.selection.review.apply_keep_decisions(), which returns a
     tuple[FragmentReview, ...]; each KEPT one carries a
     `.candidate_fragment: CandidateFragment` (only KEEP rows have one -
     see app.selection.review's own docstring). That is the exact,
     already-existing object this script consumes - nothing new is
     invented to represent "a human kept this."
  2. Exact persistence function that should receive it:
     app.services.known_airport_evidence_persistence
     .apply_known_airport_evidence_persistence() (plan_ for preview) - the
     sibling to app.services.stage_only_evidence_persistence deliberately
     built for "identity already known, project/funding MEANING still not
     accepted" (its own module docstring). This is the correct match for
     the mission's own "common V1 case is a known airport from the current
     watch set" - a WatchItem's `airport_id` is always a real, already-
     resolved Airport.id.
  3. Adapter fields needed between the two (no new semantics, only a name
     mapping - see build_metadata_from_kept_fragments()/preview_/persist_
     below):
       CandidateFragment.raw_text        -> apply_/plan_'s `raw_text`
       CandidateFragment.source_locator  -> apply_/plan_'s `source_locator`
       CandidateFragment.artifact_identity (== the fragment's own
         `selection.document_identity`, itself
         app.services.snapshot_extraction.build_document_identity()'s
         output) -> DiscoverySourceMetadata.document_identity - the SAME
         value for every fragment kept from the same document, so
         apply_known_airport_evidence_persistence()'s own Source-reuse-by-
         external_id logic naturally reuses one Source across many kept
         fragments from one document (Part 9's own MHT requirement).
     No other adapter exists or is needed - every other
     plan_/apply_known_airport_evidence_persistence() parameter
     (airport_id, assertion_type, runway_id) is an explicit, already-known
     operator input, never derived from the CandidateFragment.
  4. Current provenance fields that must be preserved, and how each is
     sourced (never invented):
       - source URL: `Snapshot.source.canonical_url` (the AcquisitionSource
         row's own real, governed fetch URL) - used as the default, with
         `--url` only as an explicit override for a caller who knows a
         more specific in-document URL than the page-level fetch target.
       - document/source title: NEVER auto-populated anywhere in this
         pipeline today (app.selection.candidate_fragment_adapter's own
         docstring: "As of Mission #14B, neither Snapshot, AcquisitionRun,
         ExtractedDocument, nor FragmentSelection carries a document title
         anywhere") - `--document-title` is therefore a REQUIRED CLI
         argument, never defaulted to something that could look like real
         metadata.
       - language: NEVER populated anywhere in this pipeline today either
         (app.selection.candidate_fragment_adapter.build_candidate_fragment()
         leaves CandidateFragment.language at its own default, None, by
         its own explicit docstring) - this script honestly reports
         "not available" rather than fabricating a value.
       - exact fragment text / Snapshot sha256 / document_identity: read
         straight through from CandidateFragment/SnapshotExtractionInput,
         never re-derived or re-hashed by this script.

HUMAN GATES (unchanged, both required together to write anything):
  - `--keep INDICES`: the SAME gate app.selection.review.apply_keep_decisions()
    already enforces - selects which fragment(s) are even candidates for
    persistence. Omit it and this script behaves exactly like
    scripts/review_fragment_selection.py's own no-`--keep` case: prints
    the numbered fragment list and stops.
  - `--allow-database-write`: a SECOND, separate, explicit gate (matching
    scripts/fetch_research_candidate.py's own existing convention for "the
    operator is authorizing an actual write") - `--keep` alone NEVER
    writes anything; it only produces the PREVIEW (Part 4). Mission Part 3
    explicitly forbids inferring KEEP-to-write from the presence of an
    index/fragment id alone - this script honors that by requiring the
    second, independent flag.

NEVER: creates/links a Signal, creates a ReviewerAction, amends a Signal,
publishes a Signal, or evaluates IdentityGuard/UAC (this script never
imports any of those - known_airport_evidence_persistence.py's own module
docstring already establishes this discipline; this script inherits it
unmodified).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.extraction.dispatch import extract_document
from app.models.acquisition import Snapshot
from app.selection.fragment_selection import AirportIdentityContext, select_fragments
from app.selection.review import FragmentReview, ReviewDecision, apply_keep_decisions
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata
from app.services.known_airport_evidence_persistence import (
    ALLOWED_ASSERTION_TYPES,
    AssertionTypeNotAllowedError,
    KnownAirportEvidenceConflictError,
    KnownAirportPersistenceResult,
    PlannedKnownAirportEvidence,
    UnknownAirportIdError,
    UnknownRunwayIdError,
    apply_known_airport_evidence_persistence,
    plan_known_airport_evidence_persistence,
)
from app.services.snapshot_extraction import load_snapshot_for_extraction

__all__ = [
    "build_metadata_from_kept_fragments",
    "preview_kept_fragments",
    "persist_kept_fragments",
    "build_readonly_engine",
    "build_writable_engine",
    "main",
]

_DISCLAIMER = (
    "KEEP means only 'this fragment is worth persisting as staged, unverified evidence' - "
    "NOT evidence accepted, NOT an airport/EMAS/project confirmation, NOT a Signal, NOT a claim. "
    "No Signal, ReviewerAction, or publication is ever created by this script."
)

EXIT_OK = 0
EXIT_ERROR = 1


def _safe_print(*values: object, file=None, sep: str = " ", end: str = "\n") -> None:
    """Mirrors scripts/fetch_research_candidate.py's own `_safe_print()` -
    real evidence text can legitimately contain any Unicode character; a
    restrictive terminal encoding must never crash this report."""
    stream = file if file is not None else sys.stdout
    text = sep.join(str(value) for value in values) + end
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace"))


def build_readonly_engine(database: Path):
    """Used whenever `--allow-database-write` is NOT given - the same
    SQLite read-only URI mode scripts/run_update_report.py and
    scripts/list_human_review_queue.py already use, so even a coding
    mistake could not write during a preview run."""
    resolved = database.resolve()
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def build_writable_engine(database: Path):
    return create_engine(f"sqlite:///{database}", future=True)


# --- Core, directly-testable orchestration (no argv/session-lifecycle) -----


def build_metadata_from_kept_fragments(
    kept: "tuple[FragmentReview, ...]", *, title: str, source_type: str = "web_discovery",
    publisher: "Optional[str]" = None, url: "Optional[str]" = None,
    published_date: "Optional[date]" = None, reliability_level: str = "unverified",
) -> DiscoverySourceMetadata:
    """The one adapter this bridge adds (Part 1 recon item 3):
    `document_identity` comes from the first KEPT fragment's own
    `CandidateFragment.artifact_identity` - every fragment KEPT from one
    `apply_keep_decisions()` call shares the identical document_identity
    (it is fixed per-document, from `DocumentSelection.document_identity`),
    so reading it off any one of them is exact, never re-derived or
    guessed. Raises IndexError if `kept` is empty - callers must check
    `kept` is non-empty first (mirrors this script's own CLI behavior)."""
    return DiscoverySourceMetadata(
        document_identity=kept[0].candidate_fragment.artifact_identity, title=title, source_type=source_type,
        publisher=publisher, url=url, published_date=published_date, reliability_level=reliability_level,
    )


def preview_kept_fragments(
    session: Session, kept: "tuple[FragmentReview, ...]", metadata: DiscoverySourceMetadata,
    *, airport_id: int, assertion_type: str, runway_id: "Optional[int]" = None,
) -> "tuple[tuple[FragmentReview, PlannedKnownAirportEvidence], ...]":
    """Read-only (Part 4): one `plan_known_airport_evidence_persistence()`
    call per KEPT fragment - zero writes, regardless of what the plan
    would do. Raises UnknownAirportIdError/UnknownRunwayIdError/
    AssertionTypeNotAllowedError immediately (caller-input-shape errors),
    exactly like the underlying service does."""
    previews = []
    for review in kept:
        cf = review.candidate_fragment
        assert cf is not None
        plan = plan_known_airport_evidence_persistence(
            session, metadata, airport_id=airport_id, assertion_type=assertion_type,
            source_locator=cf.source_locator, raw_text=cf.raw_text, runway_id=runway_id,
        )
        previews.append((review, plan))
    return tuple(previews)


def persist_kept_fragments(
    session: Session, kept: "tuple[FragmentReview, ...]", metadata: DiscoverySourceMetadata,
    *, airport_id: int, assertion_type: str, runway_id: "Optional[int]" = None,
) -> "tuple[tuple[FragmentReview, KnownAirportPersistenceResult], ...]":
    """The only write path this script calls (Part 5) - one
    `apply_known_airport_evidence_persistence()` per KEPT fragment, in the
    SAME session/transaction; the caller owns commit()/rollback(). Raises
    on the FIRST failure (KnownAirportEvidenceConflictError or a caller-
    input-shape error) - the caller is expected to roll back the whole
    batch, making one CLI invocation all-or-nothing (Part 6/10 item 21:
    "failure leaves DB unchanged")."""
    results = []
    for review in kept:
        cf = review.candidate_fragment
        assert cf is not None
        result = apply_known_airport_evidence_persistence(
            session, metadata, airport_id=airport_id, assertion_type=assertion_type,
            source_locator=cf.source_locator, raw_text=cf.raw_text, runway_id=runway_id,
        )
        results.append((review, result))
    return tuple(results)


# --- CLI ---------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, type=Path, help="SQLite database path (e.g. data/runway_safe.db).")
    parser.add_argument("--snapshot-id", required=True, type=int)
    parser.add_argument("--identity-name", default=None, help="Optional search-seed airport name (Selection attention context only).")
    parser.add_argument("--identity-iata", default=None)
    parser.add_argument("--identity-icao", default=None)
    parser.add_argument(
        "--keep", default=None,
        help="Comma-separated 1-based fragment positions to KEEP (e.g. '1,4,12'). "
        "Omit to only print the numbered list - the human KEEP gate, identical to "
        "scripts/review_fragment_selection.py's own --keep.",
    )

    parser.add_argument("--airport-id", type=int, default=None, help="Required with --keep: the already-known Airport.id this evidence is about.")
    parser.add_argument("--assertion-type", default="project_construction", choices=ALLOWED_ASSERTION_TYPES)
    parser.add_argument("--runway-id", type=int, default=None)

    parser.add_argument("--document-title", default=None, help="Required with --keep - never fabricated by this script.")
    parser.add_argument("--url", default=None, help="Overrides the Snapshot's own AcquisitionSource.canonical_url if given.")
    parser.add_argument("--source-type", default="web_discovery")
    parser.add_argument("--publisher", default=None)
    parser.add_argument("--published-date", default=None, help="ISO date, e.g. 2026-03-01")
    parser.add_argument("--reliability-level", default="unverified")

    parser.add_argument(
        "--allow-database-write", action="store_true",
        help="SECOND, separate gate (matches scripts/fetch_research_candidate.py's own convention) - "
        "without it, --keep only produces a zero-write preview.",
    )
    parser.add_argument(
        "--classify-after", action="store_true",
        help="After a successful persist, also run the existing, unmodified "
        "app.services.update_report_change_detection.classify_candidate() read-only "
        "against each resulting SourceAssertion and print the result. Optional, off by default.",
    )
    return parser


def _print_numbered_fragments(selection) -> None:
    _safe_print(f"Document identity: {selection.document_identity}")
    _safe_print(f"Fragments: {len(selection.fragments)}")
    if selection.suppressed_lines:
        _safe_print(f"Suppressed repeated lines: {list(selection.suppressed_lines)}")
    _safe_print()
    for i, fragment in enumerate(selection.fragments, start=1):
        reasons = ", ".join(f"{r.kind.value}:{r.matched_text!r}" for r in fragment.reasons)
        excerpt = fragment.text[:150].replace("\n", " ")
        _safe_print(f"[{i}] page {fragment.page_number}  offsets={fragment.start_offset}-{fragment.end_offset}")
        _safe_print(f"    reasons: {reasons}")
        _safe_print(f"    excerpt: {excerpt!r}")


def _print_preview(index: int, review: FragmentReview, plan: PlannedKnownAirportEvidence, *, airport_id: int, metadata: DiscoverySourceMetadata) -> None:
    cf = review.candidate_fragment
    assert cf is not None
    _safe_print(f"\n--- PREVIEW: fragment #{index} (zero writes) ---")
    _safe_print(f"  airport_id: {airport_id}")
    _safe_print(f"  source URL: {metadata.url!r}")
    _safe_print(f"  document/extraction identity (artifact_identity): {plan.document_identity}")
    _safe_print(f"  source_locator: {plan.source_locator}")
    _safe_print(f"  raw_fragment_hash: {plan.raw_fragment_hash}")
    _safe_print(f"  exact evidence text: {plan.raw_text!r}")
    _safe_print("  language: not available (not extracted anywhere in this pipeline - never fabricated)")
    _safe_print(f"  document/source title: {metadata.title!r}")
    _safe_print(f"  extracted airport_names (audit only, not used for persistence identity): {sorted(cf.airport_names)}")
    _safe_print(f"  extracted airport_identifiers (audit only): {sorted(cf.airport_identifiers)}")
    _safe_print(
        f"  proposed Source: {'CREATE' if plan.source_would_be_created else 'REUSE'}"
        + (f" (existing id={plan.source_id_if_existing})" if plan.source_id_if_existing is not None else "")
    )
    _safe_print(f"  proposed assertion_type: {plan.assertion_type}")
    _safe_print("  proposed evidence_quality: unverified_candidate")
    _safe_print("  proposed review_state: unreviewed")
    _safe_print("  signal_id: NULL (never linked automatically by this script or the persistence service it calls)")
    if plan.conflict:
        _safe_print(f"  CONFLICT (would refuse to write): {plan.conflict}")
    elif plan.source_assertion_would_be_created:
        _safe_print("  proposed SourceAssertion: CREATE")
    else:
        _safe_print(f"  proposed SourceAssertion: REUSE (existing id={plan.source_assertion_id_if_existing}) - idempotent replay")


def main(argv: "Optional[list[str]]" = None) -> int:
    args = _parser().parse_args(argv)

    identity = None
    if args.identity_name:
        identity = AirportIdentityContext(name=args.identity_name, iata_code=args.identity_iata, icao_code=args.identity_icao)

    engine = build_writable_engine(args.database) if args.allow_database_write else build_readonly_engine(args.database)
    with Session(engine) as session:
        try:
            loaded = load_snapshot_for_extraction(session, args.snapshot_id)
        except ValueError as exc:
            _safe_print(f"Could not load Snapshot: {exc}", file=sys.stderr)
            return EXIT_ERROR

        document = extract_document(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)
        selection = select_fragments(document, airport_identity=identity)

        _safe_print(_DISCLAIMER)
        _safe_print()
        _print_numbered_fragments(selection)

        if not args.keep:
            _safe_print("\nNo --keep given: nothing was authorized. No CandidateFragment created, no preview shown.")
            return EXIT_OK

        if args.airport_id is None:
            _safe_print("--keep requires --airport-id (the already-known Airport this evidence is about).", file=sys.stderr)
            return EXIT_ERROR
        if not args.document_title:
            _safe_print("--keep requires --document-title (never fabricated by this script).", file=sys.stderr)
            return EXIT_ERROR

        try:
            keep_indices = frozenset(int(x) for x in args.keep.split(","))
        except ValueError:
            _safe_print(f"--keep must be comma-separated integers, got: {args.keep!r}", file=sys.stderr)
            return EXIT_ERROR

        snapshot = session.get(Snapshot, args.snapshot_id)
        resolved_url = args.url or (snapshot.source.canonical_url if snapshot is not None else None)

        published_date = None
        if args.published_date:
            try:
                published_date = date.fromisoformat(args.published_date)
            except ValueError:
                _safe_print(f"--published-date must be ISO format (YYYY-MM-DD), got: {args.published_date!r}", file=sys.stderr)
                return EXIT_ERROR

        reviews = apply_keep_decisions(selection, keep_indices=keep_indices, document_title=args.document_title, url=resolved_url)
        kept = tuple(r for r in reviews if r.decision == ReviewDecision.KEEP)
        indices_by_review = {review: (selection.fragments.index(review.fragment) + 1) for review in kept}

        _safe_print(f"\n=== {len(kept)} fragment(s) KEPT (human gate satisfied) ===")
        if not kept:
            _safe_print("No valid --keep index matched a fragment - nothing to persist.", file=sys.stderr)
            return EXIT_ERROR

        metadata = build_metadata_from_kept_fragments(
            kept, title=args.document_title, source_type=args.source_type, publisher=args.publisher,
            url=resolved_url, published_date=published_date, reliability_level=args.reliability_level,
        )

        try:
            previews = preview_kept_fragments(
                session, kept, metadata, airport_id=args.airport_id, assertion_type=args.assertion_type, runway_id=args.runway_id,
            )
        except (UnknownAirportIdError, UnknownRunwayIdError, AssertionTypeNotAllowedError) as exc:
            _safe_print(f"Refused: {exc}", file=sys.stderr)
            return EXIT_ERROR

        for review, plan in previews:
            _print_preview(indices_by_review[review], review, plan, airport_id=args.airport_id, metadata=metadata)

        if not args.allow_database_write:
            _safe_print("\nPREVIEW ONLY - no --allow-database-write given. Nothing persisted.")
            return EXIT_OK

        try:
            results = persist_kept_fragments(
                session, kept, metadata, airport_id=args.airport_id, assertion_type=args.assertion_type, runway_id=args.runway_id,
            )
        except (UnknownAirportIdError, UnknownRunwayIdError, AssertionTypeNotAllowedError, KnownAirportEvidenceConflictError) as exc:
            session.rollback()
            _safe_print(f"\nPersistence refused: {exc}", file=sys.stderr)
            _safe_print("No rows were committed for this run (all-or-nothing).", file=sys.stderr)
            return EXIT_ERROR

        session.commit()

        _safe_print("\n=== PERSISTED ===")
        source_assertion_ids = []
        for review, result in results:
            _safe_print(f"fragment #{indices_by_review[review]}:")
            _safe_print(f"  Source: {'CREATED' if result.source_created else 'REUSED'} (id={result.source_id})")
            _safe_print(f"  SourceAssertion: {'CREATED' if result.source_assertion_created else 'REUSED'} (id={result.source_assertion_id})")
            _safe_print(f"  airport_id={result.airport_id}  assertion_type={result.assertion_type}")
            _safe_print("  review_state=unreviewed  evidence_quality=unverified_candidate  signal_id=NULL")
            source_assertion_ids.append(result.source_assertion_id)

        candidate_flags = " ".join(f"--candidate {sa_id}" for sa_id in source_assertion_ids)
        _safe_print(f"\nNext step (Update & Report, read-only): python -m scripts.run_update_report {candidate_flags}")

        if args.classify_after:
            from app.services.update_report_change_detection import CandidateEvidenceInput, classify_candidate

            _safe_print("\n=== READ-ONLY CLASSIFICATION (--classify-after) ===")
            for sa_id in source_assertion_ids:
                classification = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=sa_id))
                _safe_print(f"SourceAssertion {sa_id}: {classification.classification.value} (priority={classification.report_priority.value})")

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
