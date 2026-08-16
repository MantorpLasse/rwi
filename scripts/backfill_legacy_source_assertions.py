"""Backfill only recoverable legacy upstream records into SourceAssertion.

Default operation is dry-run.  ``--apply`` writes only after the caller has
separately approved the real database target and backup under the operational
safety procedure; this command deliberately does not create a backup itself.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Signal, Source, SourceAssertion

FAA_CSV = Path("emas_airports_usa.csv")
FAA_TITLE = "FAA EMAS Incidents and Installations map (verified CSV export)"
PROJECT_SOURCE_TYPES = {"usaspending_grant", "iija_grant", "faa_construction_report"}


@dataclass(frozen=True)
class Candidate:
    values: dict
    source_type: str


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _airport(session: Session, code: str | None) -> Airport | None:
    if not code:
        return None
    return session.scalar(select(Airport).where((Airport.faa_code == code) | (Airport.iata_code == code) | (Airport.icao_code == code)))


def candidates(session: Session, csv_path: Path = FAA_CSV) -> tuple[list[Candidate], list[str]]:
    result: list[Candidate] = []
    skipped: list[str] = []
    faa = session.scalar(select(Source).where(Source.title == FAA_TITLE, Source.source_type == "faa_tableau"))
    if faa is None:
        skipped.append("FAA CSV source row is missing")
    elif not csv_path.is_file():
        skipped.append(f"FAA CSV artifact is missing: {csv_path}")
    else:
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            for line, row in enumerate(csv.DictReader(handle), start=2):
                code = row.get("ARPT_ID", "").strip()
                product = row.get("TYPE", "").strip()
                if not code or not product:
                    skipped.append(f"FAA CSV line {line}: missing airport or product")
                    continue
                raw = {key: value for key, value in row.items() if value not in (None, "")}
                result.append(Candidate({
                    "source_id": faa.id, "airport_id": (_airport(session, code).id if _airport(session, code) else None),
                    "assertion_type": "airport_inventory", "raw_airport_identifier": code,
                    "raw_airport_name": row.get("ATTR(ARPT_NAME)") or row.get("ARPT_NAME"),
                    "raw_product_type": product, "raw_relevant_text": json.dumps(raw, sort_keys=True),
                    "artifact_identity": "emas_airports_usa.csv", "source_locator": f"emas_airports_usa.csv:line={line}",
                    "raw_fragment_hash": _hash(raw), "parser_identifier": "import_faa_csv.py/legacy-csv-backfill-v1",
                    "evidence_quality": "partial", "review_state": "reviewed",
                }, "faa_tableau"))
    for source in session.scalars(select(Source).where(Source.source_type.in_(PROJECT_SOURCE_TYPES), Source.external_id.is_not(None))).all():
        raw = source.summary or source.title
        if not raw:
            skipped.append(f"Source {source.id}: no recoverable project text")
            continue
        # The external ID is the upstream record identity. Airport is only set
        # when an existing Signal links that same source unambiguously.
        airport_ids = {signal.airport_id for signal in session.scalars(select(Signal).where(Signal.source_id == source.id)).all()}
        result.append(Candidate({
            "source_id": source.id, "airport_id": next(iter(airport_ids)) if len(airport_ids) == 1 else None,
            "assertion_type": "project_construction", "raw_product_type": "EMAS" if "EMAS" in raw.upper() else None,
            "raw_relevant_text": raw, "source_record_identifier": source.external_id,
            "source_locator": f"source.external_id={source.external_id}", "raw_fragment_hash": _hash(raw),
            "artifact_identity": source.url or source.external_id, "parser_identifier": "legacy-source-backfill-v1",
            "evidence_quality": "direct_strong", "review_state": "unreviewed",
        }, source.source_type))
    return result, skipped


def _exists(session: Session, values: dict) -> bool:
    if values.get("source_record_identifier"):
        return session.scalar(select(SourceAssertion.id).where(SourceAssertion.source_id == values["source_id"], SourceAssertion.source_record_identifier == values["source_record_identifier"])) is not None
    return session.scalar(select(SourceAssertion.id).where(SourceAssertion.source_id == values["source_id"], SourceAssertion.artifact_identity == values.get("artifact_identity"), SourceAssertion.source_locator == values["source_locator"], SourceAssertion.raw_fragment_hash == values["raw_fragment_hash"])) is not None


def run(session: Session, *, apply: bool = False, csv_path: Path = FAA_CSV) -> dict:
    items, skipped = candidates(session, csv_path)
    stats = Counter(candidate_source_records=len(items), skipped=len(skipped))
    by_source, by_type, by_quality, coverage = Counter(), Counter(), Counter(), Counter()
    for installation in session.scalars(select(Installation)).all():
        coverage["partial" if installation.source and installation.source.source_type == "faa_tableau" else "ambiguous"] += 1
    for item in items:
        by_source[item.source_type] += 1; by_type[item.values["assertion_type"]] += 1; by_quality[item.values["evidence_quality"]] += 1
        if _exists(session, item.values): stats["already_present"] += 1
        else:
            stats["would_create"] += 1
            if apply: session.add(SourceAssertion(**item.values))
    if apply: session.commit()
    return {"stats": dict(stats), "by_source_type": dict(by_source), "by_assertion_type": dict(by_type), "by_evidence_quality": dict(by_quality), "installation_coverage": dict(coverage), "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--csv", type=Path, default=FAA_CSV)
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        print(json.dumps(run(session, apply=args.apply, csv_path=args.csv), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
