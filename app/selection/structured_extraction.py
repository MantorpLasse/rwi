"""Minimal, deterministic literal airport-identity extraction from a
human-KEPT FragmentSelection (RWI Mission #14B).

Reuses the DESIGN DISCIPLINE (not the source-specific assumptions) of
app.acquisition.mac_granicus_extractor._extract_airport_names_from_title:
a generic "<Capitalized phrase> Airport" pattern, defensive against
greedily merging two consecutive airport names into one bogus combined
name and against matching a street name ending in "...Airport Road".
Independently reimplemented, not imported - matching this codebase's own
"independent copy, zero cross-import" convention already used between
MAC's own two extractor modules and between Discovery Triage and the
Selection package (Mission #13B).

SCOPE, DELIBERATELY NARROW (Mission #14B Part E/F):
  - Extracts ONLY airport_names, from the literal fragment text and,
    when real upstream metadata supplies one, the document title.
  - Does NOT extract airport_identifiers (IATA/ICAO/FAA codes). No
    existing repository-safe pattern for recognizing a bare code in free
    text was found during this mission's recon - a floating 3-4 letter
    code is not self-verifying the way a full "<Name> Airport" phrase is
    (MAC's own real precedent extracts names, never bare codes, from
    free text either). Inventing a new pattern now would be exactly the
    semantic guessing this mission's STOP conditions forbid.
    `airport_identifiers` therefore always remains an empty frozenset in
    V1 - a deliberate, honest scope limit, not an oversight.
  - Never reads AirportIdentityContext/search-seed data, never reads
    SelectionReason - a name is extracted if and only if it is literally,
    independently present in the text actually being scanned. This is
    what makes extraction safe for an airport RWI has never seen before
    (Mission #14B Part G): no known-airport dictionary or existing
    Airport row is consulted anywhere in this module.

No database, no network, no persistence import of any kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Independent copy of app.acquisition.mac_granicus_extractor's own
# _AIRPORT_NAME_IN_TITLE pattern and its two documented defenses:
#   1. the (?!Airport\b) lookahead prevents "Example Regional Airport and
#      Sample Municipal Airport" from being greedily merged into one
#      bogus combined name - the first "Airport" correctly terminates
#      the first match instead of being treated as filler.
#   2. the trailing negative lookahead prevents "... Airport Road/Drive/
#      Avenue/..." (a street name) from being mistaken for an airport
#      name.
#
# ONE deliberate change from MAC's own pattern, found necessary and
# fixed during this mission's own real-data testing (not present in
# MAC's original, which only ever runs against a single-line agenda-item
# title): the internal word separator is `[ \t]+` (horizontal whitespace
# ONLY), never bare `\s+`. Real pdfplumber-extracted page text is
# heavily newline-delimited (one PDF visual line per "\n", not one
# sentence/paragraph) - `\s+` would happily cross a line break and merge
# two visually-adjacent but semantically-unrelated lines into one bogus
# name (observed for real: "Proposal\nLondon City Airport" was produced
# against the real preserved CAA LCY document before this fix). Matching
# is therefore confined to a single physical line, which is exactly the
# literal-text discipline this module is required to keep - crossing a
# line break to "helpfully" complete a name would itself be a small act
# of interpretation, not literal extraction.
AIRPORT_NAME_PATTERN = re.compile(
    r"\b([A-Z][\w.'-]*(?:[ \t]+(?:(?!Airport\b)[A-Z][\w.'-]*|and(?![ \t]+Airport\b)))*[ \t]+Airport)\b"
    r"(?![ \t]+(?:Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Way|Street|St\.?|Avenue|Ave\.?)\b)"
)


def extract_airport_names(text: str | None) -> frozenset[str]:
    """Literal "<Capitalized phrase> Airport" matches only - no semantic
    inference, no dictionary of known airport names, no dependence on
    any caller-supplied identity. Works identically for an airport RWI
    already knows and one it has never seen."""
    if not text:
        return frozenset()
    return frozenset(match.group(1) for match in AIRPORT_NAME_PATTERN.finditer(text))


@dataclass(frozen=True)
class ExtractedIdentity:
    """The only two CandidateFragment identity fields V1 is authorized to
    populate (Mission #14B Part E) - both empty by default, matching
    CandidateFragment's own convention."""

    airport_names: frozenset[str] = field(default_factory=frozenset)
    airport_identifiers: frozenset[str] = field(default_factory=frozenset)


def extract_identity(*, fragment_text: str, document_title: str | None = None) -> ExtractedIdentity:
    """Priority 1 (Mission #14B Part F): literal names in the KEPT
    fragment's own text. Priority 2: literal names ALSO independently
    present in a real document title, when one is actually supplied by
    the caller from genuine upstream metadata (never fabricated here -
    see app.selection.candidate_fragment_adapter's own docstring for why
    no real document_title source exists anywhere in the current
    Snapshot/ExtractedDocument/FragmentSelection chain today). The two
    sources are merged as independently-true literal facts, never one
    replacing the other, and neither is ever populated from
    AirportIdentityContext or any SelectionReason.
    """
    names = set(extract_airport_names(fragment_text))
    if document_title:
        names |= extract_airport_names(document_title)
    return ExtractedIdentity(airport_names=frozenset(names), airport_identifiers=frozenset())
