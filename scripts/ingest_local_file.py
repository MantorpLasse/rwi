"""Manual File Ingestion CLI (RWI HQ "Manual File Acquisition Provider +
CLI" mission).

    human manually downloads an exact official document in a normal
    browser (e.g. because the publisher's own site blocks RWI's governed
    HTTP acquisition client - confirmed for flylouisville.com, HTTP 403)
        -> this CLI -> app.acquisition.manual_file.ingest_local_file()
           [existing, unmodified ManualFileAcquisitionProvider +
            AcquisitionService.acquire()]
        -> AcquisitionRun + immutable Snapshot
        -> STOP

This is a thin wrapper only - it implements no acquisition logic of its
own; see app.acquisition.manual_file's own module docstring for the full
provenance-honesty contract (no fabricated HTTP status/headers/duration,
manual runs distinguishable from network runs via
AcquisitionRun.provider_version="manual-file/1").

Writes ONLY acquisition metadata (PublishingSource/AcquisitionSource/
AcquisitionRun) and an immutable Snapshot - never Source, SourceAssertion,
Signal, ReviewerAction, or any governance row. Extraction remains a
separate, later, existing step (e.g.
python -m scripts.extract_snapshot_text --database <db> --snapshot-id <id>) -
this script never extracts automatically.

Invocation (matches this repository's existing script convention):

    python -m scripts.ingest_local_file --database data/runway_safe.db \\
        --url "https://www.flylouisville.com/wp-content/uploads/2026/03/LRAA-December-3-2025-Special-Meeting_Approved-UNSIGNED.pdf" \\
        --file "C:\\path\\to\\manually-downloaded-LRAA-meeting.pdf" \\
        --content-type application/pdf

--url is the EXACT original official URL the file was downloaded from -
never a mirror, cache, or reconstructed guess. --content-type is always
required explicitly (never sniffed from the filename) - see
ManualFileAcquisitionProvider's own docstring for why.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.acquisition.manual_file import ManualFileAcquisitionError, ingest_local_file

_DISCLAIMER = (
    "MANUAL FILE INGEST - not a fetch, not a bypass, not evidence. This preserves exactly the bytes "
    "already present in the given local file (which the human already legitimately downloaded from the "
    "given official URL in a normal browser) into the same governed Snapshot pipeline every network "
    "fetch uses. No Source, SourceAssertion, Signal, or ReviewerAction is created."
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="SQLite database path (e.g. data/runway_safe.db).")
    parser.add_argument(
        "--url", required=True,
        help="The EXACT original official URL this file was downloaded from - never a mirror/cache/guess.",
    )
    parser.add_argument("--file", required=True, type=Path, help="Local path to the manually-downloaded file.")
    parser.add_argument(
        "--content-type", required=True,
        help='Exact media type of the file, e.g. "application/pdf". Never sniffed from the filename.',
    )
    return parser


def main(argv: "Sequence[str] | None" = None) -> int:
    args = _parser().parse_args(argv)

    print(_DISCLAIMER)
    print()

    if not args.file.exists():
        print(f"Refused: no such file: {args.file}", file=sys.stderr)
        return 2
    if not args.file.is_file():
        print(f"Refused: not a regular file: {args.file}", file=sys.stderr)
        return 2

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            run = ingest_local_file(
                session, url=args.url, local_path=args.file, content_type=args.content_type,
            )
        except ManualFileAcquisitionError as exc:
            print(f"INGEST FAILED: {exc}", file=sys.stderr)
            return 1

        print("=== INGEST RESULT ===")
        print(f"acquisition_source_id: {run.acquisition_source_id}")
        print(f"acquisition_run_id: {run.id}")
        print(f"status: {run.status.value}")
        print(f"is_new_snapshot: {run.is_new_snapshot}")
        print(f"provider_version: {run.provider_version}")
        print(f"canonical/original URL: {run.request_url}")
        if run.snapshot is not None:
            print(f"snapshot_id: {run.snapshot.id}")
            print(f"sha256: {run.snapshot.sha256}")
            print(f"byte_size: {run.snapshot.byte_size}")
            print(f"media_type: {run.snapshot.media_type}")
        else:
            print("snapshot_id: (none - see status above)")

    print(
        "\n=== NEXT POSSIBLE STEP ===\n"
        "This preserved Snapshot may be extracted via the existing\n"
        f"python -m scripts.extract_snapshot_text --database {args.database} --snapshot-id {run.snapshot.id if run.snapshot else '<id>'}\n"
        "This script does not extract automatically. No Source, SourceAssertion, Signal, or "
        "ReviewerAction was created."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
