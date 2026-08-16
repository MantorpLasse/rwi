"""Explicit, human-reviewed CGF physical-installation pilot.

Default behavior is dry-run and this module contains no automatic matching.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, PhysicalInstallationIdentity, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink
from app.services.physical_installation_reconciliation import (
    create_physical_installation_identity,
    record_reconciliation_decision,
)

ACTOR = "human:rwi-owner"
CGF_CODE = "CGF"


@dataclass(frozen=True)
class PilotIdentity:
    key: str
    runway_end: str
    assertion_ids: tuple[int, int]
    reason: str


PILOT_IDENTITIES = (
    PilotIdentity(
        "cgf-reviewed-end-06-v1", "06", (101, 198),
        "Cuyahoga County completion evidence and FAA NASR each explicitly identify CGF runway end 06 as EMAS evidence.",
    ),
    PilotIdentity(
        "cgf-reviewed-end-24-v1", "24", (102, 199),
        "Cuyahoga County completion evidence and FAA NASR each explicitly identify CGF runway end 24 as EMAS evidence.",
    ),
)


def _cgf_airport(session: Session) -> Airport | None:
    return session.scalar(select(Airport).where(Airport.faa_code == CGF_CODE))


def _linked_identity(session: Session, assertion_ids: tuple[int, int]) -> PhysicalInstallationIdentity | None:
    rows = session.scalars(
        select(InstallationAssertionLink).where(
            InstallationAssertionLink.assertion_id.in_(assertion_ids),
            InstallationAssertionLink.outcome == "SAME_PHYSICAL_INSTALLATION",
        )
    ).all()
    targets = {row.physical_installation_id for row in rows}
    if len(rows) == len(assertion_ids) and len(targets) == 1 and None not in targets:
        return session.get(PhysicalInstallationIdentity, targets.pop())
    return None


def dry_run(session: Session) -> dict[str, Any]:
    airport = _cgf_airport(session)
    report: dict[str, Any] = {
        "identities_would_create": [],
        "links_would_create": [],
        "already_present": [],
        "blockers": [],
        "actor": ACTOR,
    }
    if airport is None:
        report["blockers"].append("CGF airport does not exist.")
        return report
    aggregate = session.get(SourceAssertion, 54)
    if aggregate is None or aggregate.assertion_type != "airport_inventory":
        report["blockers"].append("CGF aggregate assertion 54 is absent or not aggregate-only.")
    if session.scalar(select(InstallationAssertionLink.id).where(InstallationAssertionLink.assertion_id == 54)) is not None:
        report["blockers"].append("CGF aggregate assertion 54 is unexpectedly linked.")
    for entry in PILOT_IDENTITIES:
        assertions = [session.get(SourceAssertion, assertion_id) for assertion_id in entry.assertion_ids]
        if any(item is None or item.airport_id != airport.id or item.assertion_type != "runway_end" for item in assertions):
            report["blockers"].append(f"{entry.key}: expected discrete CGF assertions are unavailable.")
            continue
        existing = _linked_identity(session, entry.assertion_ids)
        placement = {"airport_id": airport.id, "runway_id": None, "runway_end": entry.runway_end}
        if existing is not None:
            if (existing.airport_id, existing.runway_id, existing.runway_end) != (airport.id, None, entry.runway_end):
                report["blockers"].append(f"{entry.key}: existing reviewed identity has incompatible placement.")
            else:
                report["already_present"].append({"key": entry.key, "identity_id": existing.id, "assertion_ids": entry.assertion_ids})
            continue
        report["identities_would_create"].append({"key": entry.key, **placement})
        for assertion_id in entry.assertion_ids:
            report["links_would_create"].append({
                "assertion_id": assertion_id, "key": entry.key, "outcome": "SAME_PHYSICAL_INSTALLATION",
                "actor": ACTOR, "reason": entry.reason, "supersedes_link_id": None,
            })
    return report


def run(session: Session, *, apply: bool = False) -> dict[str, Any]:
    report = dry_run(session)
    if not apply or report["blockers"]:
        return report
    airport = _cgf_airport(session)
    assert airport is not None
    for entry in PILOT_IDENTITIES:
        if _linked_identity(session, entry.assertion_ids) is not None:
            continue
        identity = create_physical_installation_identity(
            session, airport_id=airport.id, runway_id=None, runway_end=entry.runway_end
        )
        session.flush()
        for assertion_id in entry.assertion_ids:
            record_reconciliation_decision(
                session, assertion_id=assertion_id, physical_installation_id=identity.id,
                outcome="SAME_PHYSICAL_INSTALLATION", reason=entry.reason, actor=ACTOR,
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
