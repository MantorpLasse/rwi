"""Commander Research Fetch Handoff CLI (RWI HQ "Discovery Research Loop V1
- Slice 4").

    Research Loop candidate report (existing, unmodified)
    -> Commander explicitly selects ONE URL
    -> this script -> existing fetch_discovered_url() [unmodified]
    -> immutable Snapshot -> existing extract_document() dispatcher [pure,
       unmodified] -> bounded, deterministic extraction preview -> STOP

THE RESEARCH LOOP MAY FIND A SOURCE. THE COMMANDER CHOOSES WHETHER TO
FETCH IT. FETCHED CONTENT MAY BECOME PRESERVED EVIDENCE. PRESERVED
EVIDENCE STILL DOES NOT BECOME A FACT AUTOMATICALLY.

This script does not search, does not rank, does not select a candidate
URL on its own, and does not accept a list/batch - exactly one explicit
`url` argument, exactly like scripts/fetch_discovered_url.py's own
convention. It calls that same fetch layer verbatim (no second HTTP
implementation) and then the same generic extraction dispatcher used
elsewhere in this repository (no second parser). It writes ONLY the rows
fetch_discovered_url() itself already writes (PublishingSource /
AcquisitionSource / AcquisitionRun / Snapshot) - there is no persisted
"Extraction" row anywhere in this codebase; extraction is always a pure,
in-memory computation recomputed from the Snapshot's payload. This script
never creates Source, SourceAssertion, CandidateFragment, Signal,
Installation, ReviewerAction, or SignalDisposition rows, and never
performs Selection or human-KEEP - that remains a separate, later,
explicitly human-driven step via scripts/review_fragment_selection.py.

The optional --candidate-* flags below are never inferred or looked up by
this script - they exist only so the Commander can echo, for the record,
metadata they already read directly from the Research Loop's own prior
report (title, dimension, originating query, triage band, reasons). This
script performs no search of its own to obtain them.

Invocation (matches this repository's existing script convention):

    python -m scripts.fetch_research_candidate --database data/runway_safe.db \\
        "https://www.flylouisville.com/corporate/sdf-airport-improvements/" \\
        --allow-live-network --allow-database-write \\
        --candidate-title "SDF Airport Improvements | Louisville Muhammad Ali International Airport" \\
        --candidate-dimension PROJECT_PHASE \\
        --candidate-triage-band MEDIUM \\
        --candidate-reason "Exact airport name in title" --candidate-reason "IATA in title" \\
        --candidate-reason "construction in snippet" --candidate-reason "Surfaced by 2 search queries"

Output reports acquisition and extraction METADATA plus a bounded text
preview only - never the full retrieved document.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.acquisition.generic_web import ResponseTooLargeError, TooManyRedirectsError, UnsafeFetchTargetError
from app.extraction.dispatch import extract_document
from app.services.generic_web_fetch import RobotsDisallowedError, fetch_discovered_url
from app.services.snapshot_extraction import load_snapshot_for_extraction

_DEFAULT_PREVIEW_CHARS = 500


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="The single Commander-selected candidate URL to fetch and preserve. Exactly one.")
    parser.add_argument(
        "--database", required=True, help="SQLite database path (e.g. data/runway_safe.db). No default - explicit only."
    )
    parser.add_argument(
        "--allow-live-network", action="store_true",
        help="Required. Without it, this script refuses before any network or database activity.",
    )
    parser.add_argument(
        "--allow-database-write", action="store_true",
        help="Required. fetch_discovered_url() writes PublishingSource/AcquisitionSource/"
        "AcquisitionRun/Snapshot rows - this flag is the explicit human acknowledgement of that.",
    )
    parser.add_argument(
        "--preview-chars", type=int, default=_DEFAULT_PREVIEW_CHARS,
        help=f"Bounded extraction text preview length in characters (default {_DEFAULT_PREVIEW_CHARS}).",
    )
    parser.add_argument(
        "--candidate-title", default=None,
        help="For the report only - the candidate title as it appeared in the Research Loop's own report. Never looked up by this script.",
    )
    parser.add_argument(
        "--candidate-dimension", action="append", default=[],
        help="For the report only - repeatable. A ResearchDimension name this candidate was surfaced under.",
    )
    parser.add_argument(
        "--candidate-query", default=None,
        help="For the report only - the search query text that surfaced this candidate.",
    )
    parser.add_argument(
        "--candidate-triage-band", default=None,
        help="For the report only - the triage priority band (e.g. HIGH/MEDIUM/LOW) from the Research Loop's own report.",
    )
    parser.add_argument(
        "--candidate-reason", action="append", default=[],
        help="For the report only - repeatable. One triage reason string from the Research Loop's own report.",
    )
    return parser


def _print_discovered_candidate(args: argparse.Namespace) -> None:
    print("=== DISCOVERED CANDIDATE (Commander-supplied, not looked up by this script) ===")
    print(f"Title: {args.candidate_title or '(not supplied)'}")
    print(f"URL: {args.url}")
    print(f"Dimension(s): {', '.join(args.candidate_dimension) if args.candidate_dimension else '(not supplied)'}")
    print(f"Originating query: {args.candidate_query or '(not supplied)'}")
    print(f"Triage band: {args.candidate_triage_band or '(not supplied)'}")
    if args.candidate_reason:
        print("Triage reasons:")
        for reason in args.candidate_reason:
            print(f"  - {reason}")
    else:
        print("Triage reasons: (not supplied)")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if not args.allow_live_network:
        print("Refusing fetch: --allow-live-network is required.", file=sys.stderr)
        return 2
    if not args.allow_database_write:
        print("Refusing fetch: --allow-database-write is required.", file=sys.stderr)
        return 2

    _print_discovered_candidate(args)

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            run = fetch_discovered_url(session, args.url)
        except (UnsafeFetchTargetError, ResponseTooLargeError, TooManyRedirectsError) as exc:
            print(f"\n=== FETCH ===\nFETCH BLOCKED (safety): {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        except RobotsDisallowedError as exc:
            print(f"\n=== FETCH ===\nFETCH BLOCKED (robots.txt): {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # AcquisitionService.acquire() re-raises on failure
            print(f"\n=== FETCH ===\nFETCH FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print("\n=== FETCH ===")
        print(f"Status: {run.status.value}")
        print(f"Final URL: {run.final_url}")
        print(f"HTTP status: {run.http_status}")
        print(f"Content type: {run.content_type}")
        print(f"Duration: {run.duration_seconds:.2f}s")

        if run.snapshot is None:
            print("\nNo Snapshot was produced - nothing to extract. STOP.")
            print(
                "\n=== GOVERNANCE ===\n"
                "No fact accepted. No Signal created. No same-effort conclusion. No publication.\n"
                "Nothing beyond the acquisition rows above was written to the database."
            )
            return 0

        print("\n=== SNAPSHOT ===")
        print(f"Snapshot id: {run.snapshot.id}")
        print(f"sha256: {run.snapshot.sha256}")
        print(f"byte_size: {run.snapshot.byte_size}")
        print(f"media_type: {run.snapshot.media_type}")
        print(f"retrieved_at: {run.snapshot.retrieved_at}")

        loaded = load_snapshot_for_extraction(session, run.snapshot.id)

    # Session closed above. Extraction is a pure, in-memory computation -
    # no database, no network needed for this step.
    document = extract_document(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)

    total_text = "".join(page.text for page in document.pages)
    preview = total_text[: args.preview_chars]

    print("\n=== EXTRACTION ===")
    print("Extraction ID: N/A - Extraction is never persisted as a database row in this pipeline;")
    print("it is a pure, deterministic recomputation from the Snapshot's own payload, identified only by:")
    print(f"  document_identity: {document.document_identity}")
    print(f"Extractor: {document.extractor_name} v{document.extractor_version}")
    print(f"Status: {document.status.value}")
    print(f"Page count: {document.page_count}")
    print(f"Total extracted text length: {len(total_text)} chars")
    if document.warnings:
        print("Warnings:")
        for warning in document.warnings:
            print(f"  - {warning}")
    print(f"\nBounded preview (first {args.preview_chars} chars of extracted text):")
    print("-" * 60)
    print(preview if preview else "(no text extracted)")
    print("-" * 60)

    print(
        "\n=== GOVERNANCE ===\n"
        "No fact accepted. No Signal created. No same-effort conclusion. No publication.\n"
        "Extracted text is a derived parser representation, not evidence or a verified claim.\n"
        "This script created only PublishingSource/AcquisitionSource/AcquisitionRun/Snapshot rows\n"
        "(via the existing, unmodified fetch_discovered_url()) - no Source, SourceAssertion,\n"
        "CandidateFragment, Signal, Installation, Airport, or ReviewerAction row was created."
    )
    print(
        "\n=== NEXT POSSIBLE STEP ===\n"
        "This preserved Snapshot may be reviewed by a human via the existing\n"
        f"scripts/review_fragment_selection.py --database {args.database} --snapshot-id {run.snapshot.id} ...\n"
        "to explicitly KEEP fragments, producing CandidateFragment records - which could then supply\n"
        "evidence_text for a NEW ResearchClue via app.services.research_question_planning.\n"
        "This script does not perform that step automatically."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
