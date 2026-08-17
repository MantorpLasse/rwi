"""Discover the current FAA NASR APT CSV cycle/archive and, only with
--acquire, download-validate-preserve it under data/raw/nasr/<cycle>/ with
a sidecar JSON. Default is dry-run/discovery only: two small HTML page
fetches (index + cycle page), no archive download, no file write, no
database access at all - app.acquisition.nasr_apt_csv never imports
SessionLocal or any Session type.

See docs/domain/nasr-acquisition-preserve-design.md and
docs/domain/nasr-acquisition-preserve-slice-report.md.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.acquisition.nasr_apt_csv import (
    DEFAULT_RAW_DIR,
    USER_AGENT,
    acquire_and_preserve_nasr_apt,
    discover_nasr_apt_archive,
)


def dry_run(*, raw_dir: Path = DEFAULT_RAW_DIR, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT})
    try:
        location = discover_nasr_apt_archive(client=client, today=datetime.now(UTC).date())
    finally:
        if owns_client:
            client.close()

    archive_filename = location.final_archive_url.rsplit("/", 1)[-1]
    cycle_dir = raw_dir / location.nasr_cycle
    archive_path = cycle_dir / archive_filename
    sidecar_path = cycle_dir / f"{archive_filename}.metadata.json"

    return {
        "source_index_url": location.source_index_url,
        "resolved_cycle": location.nasr_cycle,
        "cycle_page_url": location.cycle_page_url,
        "final_archive_url": location.final_archive_url,
        "intended_destination_directory": str(cycle_dir),
        "intended_archive_path": str(archive_path),
        "intended_sidecar_path": str(sidecar_path),
        "already_preserved_locally": archive_path.is_file() and sidecar_path.is_file(),
    }


def acquire(*, raw_dir: Path = DEFAULT_RAW_DIR, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT})
    try:
        result = acquire_and_preserve_nasr_apt(client=client, raw_dir=raw_dir)
    finally:
        if owns_client:
            client.close()
    return {
        "status": result.status,
        "archive_path": str(result.archive_path),
        "sidecar_path": str(result.sidecar_path),
        "sha256": result.sha256,
        "byte_size": result.byte_size,
        "nasr_cycle": result.nasr_cycle,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acquire", action="store_true", help="actually download and preserve (default is dry run/discovery only)"
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args(argv)

    result = acquire(raw_dir=args.raw_dir) if args.acquire else dry_run(raw_dir=args.raw_dir)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
