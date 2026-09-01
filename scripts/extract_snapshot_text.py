"""Read-only Generic PDF Extraction CLI (RWI Mission #12B).

    Snapshot -> GenericPdfExtractor -> ExtractedDocument -> STOP

Reads one already-preserved Snapshot's bytes, runs the generic PDF
extractor, and prints a safe summary. Never writes to the database, never
creates a CandidateFragment, never fetches anything over the network.

Invocation (matches this repository's existing script convention):

    python -m scripts.extract_snapshot_text --database data/runway_safe.db --snapshot-id 7
    python -m scripts.extract_snapshot_text --database data/runway_safe.db --snapshot-id 7 --page 3

Extracted text is a derived parser representation, not evidence or a
verified claim - printed on every invocation so this is never missed.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.extraction.generic_pdf import extract_pdf
from app.services.snapshot_extraction import load_snapshot_for_extraction

_DISCLAIMER = "Extracted text is a derived parser representation, not evidence or a verified claim."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="SQLite database path (e.g. data/runway_safe.db).")
    parser.add_argument("--snapshot-id", required=True, type=int, help="Snapshot.id to extract text from.")
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help="Print this 1-based page's full extracted text. Omit to print only the summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            loaded = load_snapshot_for_extraction(session, args.snapshot_id)
        except ValueError as exc:
            print(f"Could not load Snapshot: {exc}", file=sys.stderr)
            return 2

        document = extract_pdf(
            loaded.payload,
            document_identity=loaded.document_identity,
            media_type=loaded.media_type,
        )

    print(_DISCLAIMER)
    print()
    print(f"Snapshot id: {loaded.snapshot_id}")
    print(f"Document identity: {document.document_identity}")
    print(f"Media type: {document.media_type}")
    print(f"Extractor: {document.extractor_name} v{document.extractor_version}"
          f" (pdfplumber {document.parser_library_version or 'unknown'})")
    print(f"Status: {document.status.value}")
    print(f"Page count (extracted): {document.page_count}")
    if document.warnings:
        print("Warnings:")
        for warning in document.warnings:
            print(f"  - {warning}")
    page_warning_count = sum(1 for p in document.pages if p.warnings)
    if page_warning_count:
        print(f"Pages with their own warnings: {page_warning_count}")

    if args.page is not None:
        matching = next((p for p in document.pages if p.page_number == args.page), None)
        if matching is None:
            print(f"\nPage {args.page} was not extracted (out of range or not reached).", file=sys.stderr)
            return 3
        print(f"\n--- Page {matching.page_number} text ---")
        print(matching.text)
        if matching.warnings:
            print(f"(page warnings: {', '.join(matching.warnings)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
