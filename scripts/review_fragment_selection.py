"""Human KEEP/INSPECT Fragment Selection Review CLI (RWI Mission #14B).

    Snapshot -> ExtractedDocument -> FragmentSelection -> human KEEP
    -> structured airport-identity extraction -> CandidateFragment
    -> (optional) read-only IdentityGuard evaluation -> STOP

Read-only throughout. Never writes to the database under any flag. Never
creates Source/SourceAssertion/Signal/Installation. KEEP means only
"continue processing this fragment" - never evidence accepted, airport
confirmed, or EMAS confirmed; see app.selection.review's own module
docstring for the full contract.

Invocation (matches this repository's existing script convention):

    # Step 1: see the numbered fragment list, decide nothing yet.
    python -m scripts.review_fragment_selection --database data/runway_safe.db \\
        --snapshot-id 7 --identity-name "London City Airport" --identity-iata LCY --identity-icao EGLC

    # Step 2: explicitly KEEP a human-chosen subset (1-based positions
    # from the list above) to see the resulting CandidateFragment(s).
    python -m scripts.review_fragment_selection --database data/runway_safe.db \\
        --snapshot-id 7 --identity-name "London City Airport" --identity-iata LCY --identity-icao EGLC \\
        --keep 1,4,12

    # Optional: also run the existing, unmodified IdentityGuard
    # read-only against one real Airport row, for demonstration only.
    python -m scripts.review_fragment_selection --database data/runway_safe.db \\
        --snapshot-id 7 --identity-name "London City Airport" --identity-iata LCY --identity-icao EGLC \\
        --keep 1,4,12 --evaluate-against-airport-id 42
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.extraction.generic_pdf import extract_pdf
from app.selection.fragment_selection import AirportIdentityContext, select_fragments
from app.selection.identity_guard_demo import evaluate_candidate_fragment_identity
from app.selection.review import ReviewDecision, apply_keep_decisions
from app.services.evidence_attachment_guard import candidate_airport_from_airport_like
from app.services.snapshot_extraction import load_snapshot_for_extraction

_DISCLAIMER = (
    "KEEP means only 'continue processing this fragment' - not evidence accepted, "
    "not an airport/EMAS confirmation, not a claim. Nothing here is written to the database."
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="SQLite database path (e.g. data/runway_safe.db).")
    parser.add_argument("--snapshot-id", required=True, type=int, help="Snapshot.id to select fragments from.")
    parser.add_argument("--identity-name", default=None, help="Optional search-seed airport name (attention context only).")
    parser.add_argument("--identity-iata", default=None)
    parser.add_argument("--identity-icao", default=None)
    parser.add_argument(
        "--keep",
        default=None,
        help="Comma-separated 1-based fragment positions to KEEP (e.g. '1,4,12'). "
        "Omit to only print the numbered list - no CandidateFragment is created without this.",
    )
    parser.add_argument(
        "--evaluate-against-airport-id",
        type=int,
        default=None,
        help="Optional: read-only query one real Airport row and run the existing, unmodified "
        "IdentityGuard against each KEPT CandidateFragment, for demonstration only.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    identity = None
    if args.identity_name:
        identity = AirportIdentityContext(name=args.identity_name, iata_code=args.identity_iata, icao_code=args.identity_icao)

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            loaded = load_snapshot_for_extraction(session, args.snapshot_id)
        except ValueError as exc:
            print(f"Could not load Snapshot: {exc}", file=sys.stderr)
            return 2

        document = extract_pdf(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)
        selection = select_fragments(document, airport_identity=identity)

        print(_DISCLAIMER)
        print()
        print(f"Document identity: {selection.document_identity}")
        print(f"Fragments: {len(selection.fragments)}")
        if selection.suppressed_lines:
            print(f"Suppressed repeated lines: {list(selection.suppressed_lines)}")
        print()

        for i, fragment in enumerate(selection.fragments, start=1):
            reasons = ", ".join(f"{r.kind.value}:{r.matched_text!r}" for r in fragment.reasons)
            excerpt = fragment.text[:150].replace("\n", " ")
            print(f"[{i}] page {fragment.page_number}  offsets={fragment.start_offset}-{fragment.end_offset}")
            print(f"    reasons: {reasons}")
            print(f"    excerpt: {excerpt!r}")

        if not args.keep:
            print("\nNo --keep given: nothing was authorized for further processing. No CandidateFragment created.")
            return 0

        try:
            keep_indices = frozenset(int(x) for x in args.keep.split(","))
        except ValueError:
            print(f"--keep must be comma-separated integers, got: {args.keep!r}", file=sys.stderr)
            return 2

        reviews = apply_keep_decisions(selection, keep_indices=keep_indices)
        kept = [r for r in reviews if r.decision == ReviewDecision.KEEP]

        print(f"\n=== {len(kept)} fragment(s) KEPT ===")
        candidate = None
        if args.evaluate_against_airport_id is not None:
            from app.models import Airport

            airport = session.get(Airport, args.evaluate_against_airport_id)
            if airport is None:
                print(f"No Airport with id={args.evaluate_against_airport_id}", file=sys.stderr)
                return 2
            candidate = candidate_airport_from_airport_like(airport)

        for review in kept:
            cf = review.candidate_fragment
            assert cf is not None
            print(f"\n  source_locator: {cf.source_locator}")
            print(f"  artifact_identity: {cf.artifact_identity}")
            print(f"  airport_names (extracted): {sorted(cf.airport_names)}")
            print(f"  airport_identifiers (extracted): {sorted(cf.airport_identifiers)}")
            if candidate is not None:
                decisions = evaluate_candidate_fragment_identity(cf, [candidate])
                for _cid, decision in decisions.items():
                    print(f"  IdentityGuard (read-only demo): {decision.outcome.value} - {decision.reason}")

        print("\nNothing above was written to the database. No Source/SourceAssertion was created.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
