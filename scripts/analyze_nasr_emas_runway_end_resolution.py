"""Read-only classification of NASR current-EMAS-presence SourceAssertions
against canonical physical RunwayEnd identity.

docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md.

This module performs NO writes anywhere - it has no --apply flag, never
calls session.add()/session.commit(), and never mutates an ORM object's
attributes. It exists solely to produce a deterministic, per-assertion
classification and a nationwide summary, as a design/analysis input for a
future, separate promotion-writer slice (not implemented here).

Semantic contract this classifier assumes (see the design doc, S9):
`SourceAssertion.runway_end`/`PhysicalInstallationIdentity.runway_end_id`
mean the PHYSICAL runway end where the FAA reports the arresting system -
exactly NASR's own RWY_END_ID, taken at face value, with no reciprocal
reinterpretation. This is not a new invention: every one of the 8 existing
MDW/CGF InstallationAssertionLink rows already uses this exact semantic
(reason text literally says "explicitly reports EMAS at runway end X",
quoting the NASR value unchanged) - this classifier only makes that
existing, already-approved convention explicit and repeatable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, SourceAssertion
from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end, normalize_pair

# Classification classes (docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md S9).
AUTO_RESOLVABLE = "AUTO_RESOLVABLE"
ALREADY_LINKED = "ALREADY_LINKED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
AMBIGUOUS = "AMBIGUOUS"
CONFLICT = "CONFLICT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

NASR_RUNWAY_END_ASSERTION_TYPE = "runway_end"
EMAS_KEYWORDS = ("EMAS", "arrest", "ARREST")


@dataclass(frozen=True)
class AssertionClassification:
    assertion_id: int
    airport_id: Optional[int]
    airport_code: Optional[str]
    airport_name: Optional[str]
    raw_runway_value: Optional[str]
    raw_runway_end_value: Optional[str]
    candidate_runway_end_id: Optional[int]
    candidate_designation: Optional[str]
    reciprocal_runway_end_id: Optional[int]
    reciprocal_designation: Optional[str]
    classification: str
    reason: str
    dual_naming_evidence: Optional[str] = None


def _normalize_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_end(value)
    except AmbiguousRunwayDesignationError:
        return None


def _normalize_pair_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_pair(value)
    except AmbiguousRunwayDesignationError:
        return None


def _find_candidate_runway_end(
    session: Session, airport_id: int, raw_pair: str | None, raw_end: str | None
) -> tuple[RunwayEnd | None, str]:
    """Resolve one NASR (pair, end) claim to a specific canonical RunwayEnd,
    using topology (Runway -> its own RunwayEnd rows) rather than designation
    arithmetic anywhere beyond the single, already-proven normalize_end()/
    normalize_pair() helpers every canonical-runway script already uses.
    Returns (RunwayEnd | None, human-readable reason for a None result)."""
    normalized_end = _normalize_or_none(raw_end)
    if normalized_end is None:
        return None, f"raw_runway_end_value {raw_end!r} does not normalize to a valid end token"

    normalized_pair = _normalize_pair_or_none(raw_pair)
    runways = session.scalars(select(Runway).where(Runway.airport_id == airport_id)).all()

    matching_runways = (
        [r for r in runways if _normalize_pair_or_none(r.designation) == normalized_pair]
        if normalized_pair
        else runways
    )
    if not matching_runways:
        return None, f"no canonical Runway at this airport matches pair {raw_pair!r}"

    candidates: list[RunwayEnd] = []
    for runway in matching_runways:
        for end in runway.runway_ends:
            if _normalize_or_none(end.designation) == normalized_end:
                candidates.append(end)

    if not candidates:
        return None, f"no canonical RunwayEnd at this airport matches end {raw_end!r}"
    if len(candidates) > 1:
        return None, f"end {raw_end!r} matches more than one canonical RunwayEnd at this airport"
    return candidates[0], ""


def _reciprocal_end(runway_end: RunwayEnd) -> RunwayEnd:
    """The other RunwayEnd on the same canonical Runway - pure topology
    (every governed Runway has exactly two RunwayEnd rows, verified
    nationwide with zero exceptions - see the design doc S5), never
    designation arithmetic."""
    siblings = [e for e in runway_end.runway.runway_ends if e.id != runway_end.id]
    assert len(siblings) == 1, "every canonical Runway must have exactly two RunwayEnd rows"
    return siblings[0]


def _mentions_runway_phrase(text: str, designation: str) -> bool:
    """True only for an explicit, standalone "Runway {designation}" /
    "bana {designation}" phrase - NOT a bare substring match, and NOT a
    "Runway {pair}" fragment. Two distinct templated Installation.notes
    patterns exist in the real data that both embed the reciprocal
    designation as a substring without meaning anything about dual
    naming: "...lists multiple EMAS-equipped ends here: 04L/22R/04L,
    15R/33L/15R." and "...lists EMAS at multiple ends of runway 10R/28L
    (10R, 28L); exact end not recorded." A naive substring check matched
    83/115 assertions via the first pattern; requiring "runway"/"bana"
    immediately before the token still matched the second pattern's
    "runway 10R/28L" fragment. The negative lookahead below additionally
    excludes any match immediately followed by "/" - i.e. still part of
    a "X/Y" pair token, not a genuine standalone single-end reference.
    Genuine evidence (e.g. BOS's "...Runway 22R och Runway 33L...", ORH's
    USAspending grant text "...ENGINEERED MATERIAL ARRESTING SYSTEM FOR
    RUNWAY 29 DEPARTURE END") never has "/" immediately after the token."""
    return bool(re.search(rf"\b(runway|bana)\s+{re.escape(designation)}(?!/)\b", text, re.IGNORECASE))


def _dual_naming_evidence(session: Session, airport_id: int, reciprocal_designation: str) -> str | None:
    """Generic (not airport-specific) check: does any other free-text
    evidence at this airport name the RECIPROCAL end, as an explicit
    "Runway {end}" phrase, in an EMAS context? If so, the physical mapping
    is still deterministic, but a human should see and document the dual
    naming during promotion - exactly the situation this classifier found
    at BOS (Massport's own public language names 22R/33L for beds NASR
    reports as physically 04L/15R; see the design doc S6). Scans
    Installation.notes and Signal.source_notes - the only free-text fields
    already used for this kind of provenance detail elsewhere in the
    codebase."""
    for installation in session.scalars(select(Installation).where(Installation.airport_id == airport_id)).all():
        text = installation.notes or ""
        if _mentions_runway_phrase(text, reciprocal_designation) and any(k in text for k in EMAS_KEYWORDS):
            return f"Installation {installation.id}.notes mentions runway {reciprocal_designation}"
    for signal in session.scalars(select(Signal).where(Signal.airport_id == airport_id)).all():
        text = signal.source_notes or ""
        if _mentions_runway_phrase(text, reciprocal_designation) and any(k in text for k in EMAS_KEYWORDS):
            return f"Signal {signal.id}.source_notes mentions runway {reciprocal_designation}"
    return None


def _existing_identity_for_end(
    session: Session, airport_id: int, runway_end_id: int
) -> PhysicalInstallationIdentity | None:
    return session.scalar(
        select(PhysicalInstallationIdentity).where(
            PhysicalInstallationIdentity.airport_id == airport_id,
            PhysicalInstallationIdentity.runway_end_id == runway_end_id,
        )
    )


def classify_assertion(session: Session, assertion: SourceAssertion) -> AssertionClassification:
    airport = session.get(Airport, assertion.airport_id) if assertion.airport_id else None
    airport_code = (airport.faa_code or airport.iata_code or airport.icao_code) if airport else None
    airport_name = airport.name if airport else None

    if airport is None:
        return AssertionClassification(
            assertion_id=assertion.id, airport_id=assertion.airport_id, airport_code=None, airport_name=None,
            raw_runway_value=assertion.raw_runway_value, raw_runway_end_value=assertion.raw_runway_end_value,
            candidate_runway_end_id=None, candidate_designation=None, reciprocal_runway_end_id=None,
            reciprocal_designation=None, classification=INSUFFICIENT_EVIDENCE,
            reason="assertion has no airport_id - cannot resolve physical identity",
        )

    candidate, reason = _find_candidate_runway_end(
        session, airport.id, assertion.raw_runway_value, assertion.raw_runway_end_value
    )
    if candidate is None:
        classification = AMBIGUOUS if "more than one" in reason else INSUFFICIENT_EVIDENCE
        return AssertionClassification(
            assertion_id=assertion.id, airport_id=airport.id, airport_code=airport_code, airport_name=airport_name,
            raw_runway_value=assertion.raw_runway_value, raw_runway_end_value=assertion.raw_runway_end_value,
            candidate_runway_end_id=None, candidate_designation=None, reciprocal_runway_end_id=None,
            reciprocal_designation=None, classification=classification, reason=reason,
        )

    reciprocal = _reciprocal_end(candidate)

    existing = _existing_identity_for_end(session, airport.id, candidate.id)
    if existing is not None:
        reviewed = any(link.outcome == "SAME_PHYSICAL_INSTALLATION" for link in existing.assertion_links)
        conflicting = any(link.outcome == "DIFFERENT_PHYSICAL_INSTALLATION" for link in existing.assertion_links)
        if conflicting and not reviewed:
            return AssertionClassification(
                assertion_id=assertion.id, airport_id=airport.id, airport_code=airport_code,
                airport_name=airport_name, raw_runway_value=assertion.raw_runway_value,
                raw_runway_end_value=assertion.raw_runway_end_value, candidate_runway_end_id=candidate.id,
                candidate_designation=candidate.designation, reciprocal_runway_end_id=reciprocal.id,
                reciprocal_designation=reciprocal.designation, classification=CONFLICT,
                reason=f"PhysicalInstallationIdentity {existing.id} at this RunwayEnd already has a "
                       "DIFFERENT_PHYSICAL_INSTALLATION decision recorded",
            )
        if reviewed:
            return AssertionClassification(
                assertion_id=assertion.id, airport_id=airport.id, airport_code=airport_code,
                airport_name=airport_name, raw_runway_value=assertion.raw_runway_value,
                raw_runway_end_value=assertion.raw_runway_end_value, candidate_runway_end_id=candidate.id,
                candidate_designation=candidate.designation, reciprocal_runway_end_id=reciprocal.id,
                reciprocal_designation=reciprocal.designation, classification=ALREADY_LINKED,
                reason=f"PhysicalInstallationIdentity {existing.id} already reviewed and linked at this RunwayEnd",
            )

    dual_naming = _dual_naming_evidence(session, airport.id, reciprocal.designation)
    if dual_naming:
        return AssertionClassification(
            assertion_id=assertion.id, airport_id=airport.id, airport_code=airport_code, airport_name=airport_name,
            raw_runway_value=assertion.raw_runway_value, raw_runway_end_value=assertion.raw_runway_end_value,
            candidate_runway_end_id=candidate.id, candidate_designation=candidate.designation,
            reciprocal_runway_end_id=reciprocal.id, reciprocal_designation=reciprocal.designation,
            classification=REVIEW_REQUIRED,
            reason="physical mapping is deterministic, but other evidence at this airport names the reciprocal "
                   "end in an EMAS context - human review recommended to document the dual naming",
            dual_naming_evidence=dual_naming,
        )

    return AssertionClassification(
        assertion_id=assertion.id, airport_id=airport.id, airport_code=airport_code, airport_name=airport_name,
        raw_runway_value=assertion.raw_runway_value, raw_runway_end_value=assertion.raw_runway_end_value,
        candidate_runway_end_id=candidate.id, candidate_designation=candidate.designation,
        reciprocal_runway_end_id=reciprocal.id, reciprocal_designation=reciprocal.designation,
        classification=AUTO_RESOLVABLE,
        reason="raw NASR (pair, end) maps to exactly one canonical RunwayEnd; no conflicting identity evidence",
    )


def classify_all(session: Session) -> list[AssertionClassification]:
    assertions = session.scalars(
        select(SourceAssertion).where(SourceAssertion.assertion_type == NASR_RUNWAY_END_ASSERTION_TYPE)
    ).all()
    return [classify_assertion(session, a) for a in assertions]


def summarize(results: list[AssertionClassification]) -> dict:
    from collections import Counter

    by_class = Counter(r.classification for r in results)
    airports = {r.airport_id for r in results if r.airport_id is not None}
    ends_implicated = {(r.airport_id, r.candidate_runway_end_id) for r in results if r.candidate_runway_end_id}
    end_counts = Counter((r.airport_id, r.candidate_runway_end_id) for r in results if r.candidate_runway_end_id)
    duplicate_ends = {f"airport={k[0]},runway_end={k[1]}": v for k, v in end_counts.items() if v > 1}
    airports_by_class: dict[int, set[str]] = {}
    for r in results:
        if r.airport_id is not None:
            airports_by_class.setdefault(r.airport_id, set()).add(r.classification)
    mixed_airports = {aid: sorted(classes) for aid, classes in airports_by_class.items() if len(classes) > 1}

    return {
        "assertions_total": len(results),
        "airports_total": len(airports),
        "by_classification": dict(by_class),
        "unique_physical_ends_implicated": len(ends_implicated),
        "duplicate_assertions_for_same_physical_end": duplicate_ends,
        "airports_with_mixed_classifications": mixed_airports,
    }


def run(session: Session | None = None) -> dict:
    owns_session = session is None
    session = session or SessionLocal()
    try:
        results = classify_all(session)
        return {
            "summary": summarize(results),
            "results": [
                {
                    "assertion_id": r.assertion_id, "airport_id": r.airport_id, "airport_code": r.airport_code,
                    "airport_name": r.airport_name, "raw_runway_value": r.raw_runway_value,
                    "raw_runway_end_value": r.raw_runway_end_value,
                    "candidate_runway_end_id": r.candidate_runway_end_id,
                    "candidate_designation": r.candidate_designation,
                    "reciprocal_runway_end_id": r.reciprocal_runway_end_id,
                    "reciprocal_designation": r.reciprocal_designation, "classification": r.classification,
                    "reason": r.reason, "dual_naming_evidence": r.dual_naming_evidence,
                }
                for r in results
            ],
        }
    finally:
        if owns_session:
            session.close()


def main() -> int:
    report = run()
    print(json.dumps(report["summary"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
