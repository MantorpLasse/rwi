"""One-off legacy correction: Greater Binghamton (BGM) Signal 6's
`target_year` field was set to 2028 based on a misreading of BGM's own
Airport Master Plan Update (Snapshot 29, Source 54) - see the RWI HQ
"BGM 2028 Timeline Semantics" read-only recon mission's report. AMPU
Table 8-1 places "Runway 16 EMAS - Construction Phase I" and
"...Construction Phase II" inside a shared "Phase II: (2023-2028)"
capital-program bucket alongside eight unrelated projects (taxiway
lighting, taxiway rehab, guidance signs, snow-removal equipment,
passenger boarding bridge, ARFF building, REILs, land acquisition); the
document never assigns an EMAS-specific exact target, construction,
procurement, or completion year anywhere - 2028 is only that bucket's
closing boundary. `target_year` outranks `planning_year` in every
downstream year-fallback (signal_lifecycle.py's `_best_year`,
governed_signal_creation.py's `_resolve_reference_year`,
existing_signal_reconciliation_candidates.py, and the static-export
timeline/trend views), so this one field alone was responsible for the
public site showing a bare, unqualified "2028" for this Signal.

Correction (Commander-approved, RWI HQ "BGM Signal 6 - Remove
misleading target_year=2028" mission): clear target_year to NULL,
leave planning_year=2026 untouched (already a defensible single-year
proxy - this Signal's own existing source_notes cross-reference it to a
specific FY2026 USAspending grant tranche believed to correspond to
Construction Phase II), and append a new dated entry to source_notes
explaining the correction - never overwriting the Signal's existing
research history.

This is a legacy, pre-SourceAssertion/ReviewerAction Signal (id 6 has
zero SourceAssertion or ReviewerAction rows - confirmed by the recon
mission), so this follows the established one-off correction-script
pattern (scripts/update_lex_emas_details.py,
scripts/update_ase_runway_relocation_note.py) rather than the governed
Signal-creation pipeline, which this script never touches.

Targets Signal id 6 only. Fails closed (raises SystemExit, no partial
write) if the Signal does not exist, or if any of its
title/category/status/planning_year/target_year/published/source_id/
runway_id/manual_year_estimate/source_notes fields differ from the
exact expected before-state this correction was verified against - a
changed Signal is refused rather than silently corrected. Safe to
re-run: if target_year is already None and the correction note is
already present in source_notes, this is a no-op (updated=False).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Signal
from scripts.annotate_signal import append_note

SIGNAL_ID = 6

_EXPECTED_TITLE = "Runway 16 departure EMAS project"
_EXPECTED_CATEGORY = "new_installation"
_EXPECTED_STATUS = "funded"
_EXPECTED_PLANNING_YEAR = 2026
_EXPECTED_TARGET_YEAR = 2028
_EXPECTED_PUBLISHED = True
_EXPECTED_SOURCE_ID = 54
_EXPECTED_RUNWAY_ID = 6

CORRECTION_NOTE = (
    "Timeline correction: BGM AMPU Table 8-1 places the Runway 16 EMAS "
    "construction line items within the broader Phase II capital-program "
    "window 2023-2028. The source does not establish 2028 as an "
    "EMAS-specific target, construction, procurement, or completion year. "
    "target_year was therefore cleared; planning_year=2026 remains the "
    "structured reference year supported by the existing research record."
)

# Guard substring for idempotency - specific enough not to collide with any
# earlier, unrelated note text already present in this Signal's source_notes.
_ALREADY_APPLIED_MARKER = "Timeline correction: BGM AMPU Table 8-1"


def correct_bgm_signal6_target_year(session: Session, *, today: date | None = None) -> tuple[Signal, bool]:
    today = today or date.today()

    signal = session.get(Signal, SIGNAL_ID)
    if signal is None:
        raise SystemExit(f"No Signal with id={SIGNAL_ID}.")

    if signal.target_year is None and _ALREADY_APPLIED_MARKER in (signal.source_notes or ""):
        return signal, False

    mismatches = []
    if signal.title != _EXPECTED_TITLE:
        mismatches.append(f"title={signal.title!r} (expected {_EXPECTED_TITLE!r})")
    if signal.category != _EXPECTED_CATEGORY:
        mismatches.append(f"category={signal.category!r} (expected {_EXPECTED_CATEGORY!r})")
    if signal.status != _EXPECTED_STATUS:
        mismatches.append(f"status={signal.status!r} (expected {_EXPECTED_STATUS!r})")
    if signal.planning_year != _EXPECTED_PLANNING_YEAR:
        mismatches.append(f"planning_year={signal.planning_year!r} (expected {_EXPECTED_PLANNING_YEAR!r})")
    if signal.target_year != _EXPECTED_TARGET_YEAR:
        mismatches.append(f"target_year={signal.target_year!r} (expected {_EXPECTED_TARGET_YEAR!r})")
    if bool(signal.published) != _EXPECTED_PUBLISHED:
        mismatches.append(f"published={signal.published!r} (expected {_EXPECTED_PUBLISHED!r})")
    if signal.source_id != _EXPECTED_SOURCE_ID:
        mismatches.append(f"source_id={signal.source_id!r} (expected {_EXPECTED_SOURCE_ID!r})")
    if signal.runway_id != _EXPECTED_RUNWAY_ID:
        mismatches.append(f"runway_id={signal.runway_id!r} (expected {_EXPECTED_RUNWAY_ID!r})")
    if signal.manual_year_estimate is not None:
        mismatches.append(f"manual_year_estimate={signal.manual_year_estimate!r} (expected NULL)")
    if not signal.source_notes:
        mismatches.append("source_notes is empty (expected pre-existing research text)")

    if mismatches:
        raise SystemExit(
            f"Signal {SIGNAL_ID} preconditions do not match the expected before-state - refusing to write: "
            + "; ".join(mismatches)
        )

    signal.target_year = None
    signal.source_notes = append_note(signal.source_notes, CORRECTION_NOTE, on=today)
    session.commit()
    session.refresh(signal)
    return signal, True


def main() -> None:
    with SessionLocal() as session:
        signal, updated = correct_bgm_signal6_target_year(session)
    print(
        f"Signal {signal.id} ({signal.title!r}) updated={updated}. "
        f"target_year={signal.target_year}, planning_year={signal.planning_year}"
    )


if __name__ == "__main__":
    main()
