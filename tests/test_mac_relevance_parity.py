"""5D adversarial-review addition (RWI Controlled Live Pilot 5D review
checkpoint): a single shared corpus run through BOTH independent relevance
implementations - app.acquisition.mac_granicus.is_relevant_title() and
app.acquisition.mac_granicus_extractor.is_relevant_text() - asserting both
agree with each other AND with the expected value on every case.

The two implementations are deliberately independent, zero-cross-import
copies (see both modules' own comments) - a fix applied by hand to each
could silently drift. This file exists specifically to prove they have not
drifted, on a corpus wide enough to matter: the exact designation-suffix,
intervening-modifier, false-positive, strong-phrase, tokenization-
robustness, and taxiway-boundary attacks named explicitly in the 5D review
mission, plus the real MAC titles observed live during Controlled Live
Pilot 5C's own reconnaissance (marked "5C real title" below - not
fabricated).

Never touches the real database or the network.
"""
from __future__ import annotations

import pytest

from app.acquisition.mac_granicus import is_relevant_title
from app.acquisition.mac_granicus_extractor import is_relevant_text

NBSP = " "

CASES: list[tuple[str, bool]] = [
    # --- designation-format attacks: plain digits, leading zero, slash vs
    # hyphen, and L/R/C side-letter suffixes (single AND paired) ---
    ("Runway 14-32 Reconstruction", True),
    ("Runway 14/32 Reconstruction", True),
    ("Runway 09-27 Reconstruction", True),
    ("Runway 9/27 Reconstruction", True),
    ("Runway 4L-22R Reconstruction", True),
    ("Runway 04L/22R Reconstruction", True),
    ("Runway 18R Reconstruction", True),
    ("Runway 36L Rehabilitation", True),
    # --- intervening-modifier attacks: real 5C shapes plus deliberately
    # heavier modifier phrases, all still within the 8-token bound ---
    ("Runway 18-36 Pavement Reconstruction", True),  # 5C real title (shortened)
    ("Runway 9-27 Edge Lighting and PAPI Replacement", True),  # 5C real title (shortened)
    ("Runway 14-32 Pavement and Lighting Reconstruction", True),
    ("Runway 14-32 North End Pavement Reconstruction", True),
    ("Runway 14-32 Final Phase Reconstruction", True),
    # --- false-positive attacks: runway and a work concept both present,
    # sometimes even fairly close together, but structurally unrelated, or
    # a runway mention paired with a word outside the 5 approved concepts ---
    ("Terminal Reconstruction Program - runway operations remain unaffected", False),
    ("Parking Ramp Reconstruction adjacent to Runway 12", False),
    ("Road Reconstruction near Airport Runway 4", False),
    ("Building Rehabilitation with access via Runway Road", False),
    ("Equipment Replacement for terminal systems; runway inspection noted separately", False),
    ("Runway 14-32 annual inspection", False),
    ("Runway 14-32 snow removal equipment procurement", False),
    ("Runway 14-32 lighting maintenance", False),  # "maintenance" is deliberately not an approved work concept
    ("Parking-Ramp Reconstruction With an Unrelated Runway Reference Elsewhere", False),
    # --- real 5C positive corpus (verbatim, live-observed titles) ---
    ("2026 Anoka County-Blaine Airport Runway 18-36 Pavement Reconstruction and Electrical Vault Improvements", True),  # 5C real title
    ("STP Runway 14-32 Reconstruction", True),  # 5C real title
    ("St. Paul Downtown Airport: Runway 14/32 Reconstruction Project", True),  # 5C real title
    ("2026 Anoka County-Blaine Airport Runway 9-27 Edge Lighting and PAPI Replacement", True),  # 5C real title
    # --- strong standalone phrase preservation: every one of the 4
    # multi-word phrases, plus a plural-noun variant and capitalization ---
    ("Engineered Material Arresting Systems (EMAS) Procurement Advance Deposit", True),
    ("engineered materials arresting system upgrade", True),  # plural "materials" - still contains "engineered material" as a prefix substring
    ("ARRESTING SYSTEM REPLACEMENT PROGRAM", True),
    ("Runway Safety Area - Design Phase", True),
    # --- every one of the 5 work concepts, bare "Runway <concept>" form
    # (the original, zero-gap, pre-5D exact-phrase case - must still work) ---
    ("Runway Reconstruction", True),
    ("Runway Rehabilitation Program", True),
    ("Runway Replacement Initiative", True),
    ("Runway Resurfacing Project", True),
    ("Runway Repair Contract", True),
    # --- tokenization robustness: dashes, whitespace, punctuation, case ---
    ("Runway–14-32—Reconstruction", True),  # en dash / em dash around the designation
    ("Runway  14-32   Reconstruction", True),  # repeated whitespace
    ("Runway (14-32) Reconstruction", True),  # parentheses
    ("Runway: 14-32 & Taxiway Reconstruction", True),  # colon and ampersand
    ("RUNWAY 14-32 reconstruction", True),  # mixed case
    (f"Runway{NBSP}14-32{NBSP}Reconstruction", True),  # non-breaking space around the designation
    # --- taxiway boundary: deliberately out of the current relevance
    # vocabulary - a taxiway-only mention (even paired with a real work
    # concept) must not become relevant as a side effect of this fix ---
    ("Taxiway R Pavement Reconstruction", False),
    ("2026 Airside Roadway Pavement Restoration, Taxiway R Pavement Reconstruction, and Bituminous Shoulder Reconstruction", False),  # 5C real title
]


@pytest.mark.parametrize("text,expected", CASES)
def test_relevance_parity_across_both_independent_implementations(text, expected):
    title_result = is_relevant_title(text)
    text_result = is_relevant_text(text)
    assert title_result is expected, f"is_relevant_title disagreed with expectation for {text!r}"
    assert text_result is expected, f"is_relevant_text disagreed with expectation for {text!r}"
    assert title_result == text_result, f"the two independent implementations disagreed for {text!r}"


def test_parity_corpus_covers_both_polarities():
    """Sanity check on the corpus itself, not the implementations -
    guards against someone silently deleting all the negative (or all the
    positive) cases over time and the parity test still passing vacuously."""
    positives = sum(1 for _, expected in CASES if expected)
    negatives = sum(1 for _, expected in CASES if not expected)
    assert positives >= 15
    assert negatives >= 10
