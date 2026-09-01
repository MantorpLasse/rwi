"""Human-authorized Generic Web Fetch CLI (RWI Mission #11B).

    human FETCH authorization -> Generic Web Acquisition -> AcquisitionRun
    -> immutable Snapshot -> STOP

Exactly one explicit URL argument = exactly one human FETCH authorization.
This script NEVER fetches automatically, never accepts a list/batch, and
never auto-fetches Discovery/Triage HIGH-band results - a human names the
one URL they mean, every time.

FETCH != evidence, verification, claim acceptance, airport-identity
approval, EMAS presence, a Signal, or an Installation. This script writes
ONLY PublishingSource/AcquisitionSource/AcquisitionRun/Snapshot rows - see
app.services.generic_web_fetch's own module docstring for the exact
governance boundary, enforced by test, that this stops at.

Invocation (matches this repository's existing script convention):

    python -m scripts.fetch_discovered_url --database data/runway_safe.db \\
        "https://airspacechange.caa.co.uk/documents/download/5487"

Output reports acquisition METADATA only (status, final URL, content
type, byte size, content hash) - it never dumps the retrieved document
bytes to the terminal.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.acquisition.generic_web import ResponseTooLargeError, TooManyRedirectsError, UnsafeFetchTargetError
from app.services.generic_web_fetch import RobotsDisallowedError, fetch_discovered_url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="The single human-approved URL to fetch and preserve. Exactly one.")
    parser.add_argument(
        "--database", required=True, help="SQLite database path (e.g. data/runway_safe.db). No default - explicit only."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    print(f"Requested URL: {args.url}")

    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        try:
            run = fetch_discovered_url(session, args.url)
        except (UnsafeFetchTargetError, ResponseTooLargeError, TooManyRedirectsError) as exc:
            print(f"FETCH BLOCKED (safety): {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        except RobotsDisallowedError as exc:
            print(f"FETCH BLOCKED (robots.txt): {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # AcquisitionService.acquire() re-raises on failure
            print(f"FETCH FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print(f"\nStatus: {run.status.value}")
        print(f"Final URL: {run.final_url}")
        print(f"HTTP status: {run.http_status}")
        print(f"Content type: {run.content_type}")
        print(f"Duration: {run.duration_seconds:.2f}s")
        if run.snapshot is not None:
            print(f"Snapshot id: {run.snapshot.id}")
            print(f"Snapshot sha256: {run.snapshot.sha256}")
            print(f"Byte size: {run.snapshot.byte_size}")
        print(
            "\nThis is an acquisition record only - not evidence, not a verified claim, "
            "not an airport-identity decision. Nothing else was written to the database."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
