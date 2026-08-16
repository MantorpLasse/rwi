"""Narrow, dry-run-first MDW current FAA NASR presence pilot."""
from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, PhysicalInstallationIdentity, Source, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink
from app.services.physical_installation_reconciliation import (
    create_physical_installation_identity,
    record_reconciliation_decision,
)

ACTOR = "human:rwi-owner"
NASR_EXTERNAL_ID = "faa_nasr:airport_csv:2026-08-06:06_Aug_2026_APT_CSV.zip"
EXPECTED_ENDS = ("04R", "22L", "13L", "31R")


def _resolve_evidence(session: Session) -> tuple[Airport | None, dict[str, SourceAssertion], list[str]]:
    blockers: list[str] = []
    airport = session.scalar(select(Airport).where(Airport.faa_code == "MDW"))
    source = session.scalar(select(Source).where(Source.external_id == NASR_EXTERNAL_ID))
    if airport is None or source is None:
        return airport, {}, ["MDW airport or governed NASR source is missing."]
    rows = session.scalars(select(SourceAssertion).where(
        SourceAssertion.airport_id == airport.id,
        SourceAssertion.source_id == source.id,
        SourceAssertion.assertion_type == "runway_end",
    )).all()
    found: dict[str, SourceAssertion] = {}
    for row in rows:
        try:
            raw = json.loads(row.raw_relevant_text or "")
        except json.JSONDecodeError:
            continue
        end = row.raw_runway_end_value
        if raw.get("ARPT_ID") != "MDW" or raw.get("ARREST_DEVICE_CODE") != "EMAS":
            continue
        if raw.get("RWY_END_ID") != end or raw.get("EFF_DATE") != "2026/08/06":
            continue
        if end in found:
            blockers.append(f"Duplicate valid NASR evidence for MDW end {end}.")
        else:
            found[end] = row
    if tuple(sorted(found)) != tuple(sorted(EXPECTED_ENDS)):
        blockers.append(f"Expected exactly {EXPECTED_ENDS}; resolved {tuple(sorted(found))}.")
    return airport, found, blockers


def _existing_identity(session: Session, assertion_id: int) -> PhysicalInstallationIdentity | None:
    links = session.scalars(select(InstallationAssertionLink).where(
        InstallationAssertionLink.assertion_id == assertion_id,
        InstallationAssertionLink.outcome == "SAME_PHYSICAL_INSTALLATION",
    )).all()
    if len(links) > 1:
        raise ValueError(f"Assertion {assertion_id} has multiple SAME links.")
    return session.get(PhysicalInstallationIdentity, links[0].physical_installation_id) if links else None


def dry_run(session: Session) -> dict[str, Any]:
    airport, evidence, blockers = _resolve_evidence(session)
    report: dict[str, Any] = {
        "airport_id": airport.id if airport else None, "evidence": [],
        "identities_would_create": [], "links_would_create": [],
        "already_present": [], "skipped_unresolved": [], "blockers": blockers, "actor": ACTOR,
    }
    if blockers or airport is None:
        return report
    for end in EXPECTED_ENDS:
        assertion = evidence[end]
        report["evidence"].append({"assertion_id": assertion.id, "runway_end": end, "locator": assertion.source_locator})
        existing = _existing_identity(session, assertion.id)
        if existing:
            if (existing.airport_id, existing.runway_id, existing.runway_end) != (airport.id, None, end):
                report["blockers"].append(f"Assertion {assertion.id} has incompatible existing identity.")
            else:
                report["already_present"].append({"assertion_id": assertion.id, "identity_id": existing.id, "runway_end": end})
            continue
        report["identities_would_create"].append({"assertion_id": assertion.id, "airport_id": airport.id, "runway_id": None, "runway_end": end})
        report["links_would_create"].append({
            "assertion_id": assertion.id, "runway_end": end, "outcome": "SAME_PHYSICAL_INSTALLATION",
            "actor": ACTOR, "reason": f"FAA NASR 2026-08-06 explicitly reports EMAS at MDW runway end {end}; current-presence only, no historical continuity claim.",
            "supersedes_link_id": None,
        })
    return report


def run(session: Session, *, apply: bool = False) -> dict[str, Any]:
    report = dry_run(session)
    if not apply or report["blockers"]:
        return report
    airport, evidence, blockers = _resolve_evidence(session)
    assert airport is not None and not blockers
    for end in EXPECTED_ENDS:
        assertion = evidence[end]
        if _existing_identity(session, assertion.id):
            continue
        identity = create_physical_installation_identity(session, airport_id=airport.id, runway_id=None, runway_end=end)
        session.flush()
        record_reconciliation_decision(
            session, assertion_id=assertion.id, physical_installation_id=identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION",
            reason=f"FAA NASR 2026-08-06 explicitly reports EMAS at MDW runway end {end}; current-presence only, no historical continuity claim.",
            actor=ACTOR,
        )
    session.commit()
    return dry_run(session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        print(run(session, apply=args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

