"""Report-only: list every Airport row with no iata_code/icao_code, and for
each, fuzzy-match its name against every coded Airport row (one that has
iata_code and/or icao_code set) to surface likely duplicate pairs.

Does NOT modify the database. Prints a report; merging (moving
Installation/Signal rows to the coded row and deleting the codeless one) is
a separate, manual decision once a human has reviewed the candidates.

Usage:
    python -m scripts.find_duplicate_airport_candidates [--threshold 0.55]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, Signal


def _normalize(name: str) -> str:
    lowered = name.lower()
    for junk in (
        "international airport", "international", "regional airport", "regional",
        "airport", "municipal", "county", "field", "downtown", "executive",
    ):
        lowered = lowered.replace(junk, " ")
    return " ".join(lowered.split())


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


@dataclass
class Candidate:
    codeless: Airport
    coded: Airport
    score: float
    reason: str


def find_candidates(session: Session, *, threshold: float) -> list[Candidate]:
    codeless = session.scalars(
        select(Airport).where(Airport.iata_code.is_(None), Airport.icao_code.is_(None))
    ).all()
    coded = session.scalars(
        select(Airport).where(
            (Airport.iata_code.is_not(None)) | (Airport.icao_code.is_not(None))
        )
    ).all()

    candidates: list[Candidate] = []
    for cl in codeless:
        best: Candidate | None = None
        for c in coded:
            # A codeless row's faa_code equalling a coded row's own faa_code/
            # iata_code/icao_code root is a near-certain match, regardless of
            # how different the two names read (e.g. "Bob Hope" vs "Burbank").
            code_match = cl.faa_code is not None and cl.faa_code in filter(
                None, (c.iata_code, c.icao_code, c.faa_code, (c.icao_code or "")[1:] or None)
            )
            score = name_similarity(cl.name, c.name)
            if cl.city and c.city:
                score = max(score, name_similarity(f"{cl.name} {cl.city}", f"{c.name} {c.city}"))

            if code_match:
                reason = f"faa_code {cl.faa_code!r} matches {c.iata_code or c.icao_code or c.faa_code!r}"
                score = max(score, 0.99)
            elif score >= threshold:
                reason = f"name similarity {score:.2f}"
            else:
                continue

            if best is None or score > best.score:
                best = Candidate(codeless=cl, coded=c, score=score, reason=reason)
        if best is not None:
            candidates.append(best)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _counts(session: Session, airport_id: int) -> tuple[int, int]:
    installations = len(session.scalars(select(Installation).where(Installation.airport_id == airport_id)).all())
    signals = len(session.scalars(select(Signal).where(Signal.airport_id == airport_id)).all())
    return installations, signals


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.55)
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        codeless_all = session.scalars(
            select(Airport).where(Airport.iata_code.is_(None), Airport.icao_code.is_(None))
        ).all()
        print(f"Airport rows with no iata_code AND no icao_code: {len(codeless_all)}")
        for a in codeless_all:
            installs, signals = _counts(session, a.id)
            print(
                f"  id={a.id:<4} faa_code={a.faa_code or '-':<6} name={a.name!r:<45} "
                f"city={a.city!r} installations={installs} signals={signals}"
            )
        print()

        candidates = find_candidates(session, threshold=args.threshold)
        print(f"Fuzzy-matched candidate duplicate pairs (threshold={args.threshold}): {len(candidates)}")
        for cand in candidates:
            cl_installs, cl_signals = _counts(session, cand.codeless.id)
            print(
                f"  [{cand.score:.2f}] codeless id={cand.codeless.id} {cand.codeless.name!r} "
                f"(faa_code={cand.codeless.faa_code}, {cl_installs} installations, {cl_signals} signals)"
                f"\n         <-> coded    id={cand.coded.id} {cand.coded.name!r} "
                f"(iata={cand.coded.iata_code}, icao={cand.coded.icao_code}, faa={cand.coded.faa_code})"
                f"\n         reason: {cand.reason}"
            )
        if not candidates:
            print("  (none found)")


if __name__ == "__main__":
    main()
