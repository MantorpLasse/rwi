"""Explainable Source Triage V1 (Mission #10B).

Pure, deterministic ranking of already-discovered DedupedResult rows into
reviewer-facing priority bands with literal, explainable reasons. Operates
ONLY on SearchResult/SearchQuery metadata already in hand before any
fetch - no network access, no database session, no persistence.

CORE SEMANTIC CONTRACT (Mission #10B, re-stated so it cannot be missed by
a future reader of this file alone):

    Source Triage answers "which discovered URLs are most worth a human
    inspecting first, and why?" It does NOT answer what is true, whether
    EMAS/RESA is confirmed, whether a source is accepted evidence, or
    whether any Airport/Signal/Installation should exist.

    SearchResult != Evidence. HIGH PRIORITY != VERIFIED.

    This module RANKS and EXPLAINS. It never CONCLUDES. Nothing here
    creates, mutates, or even imports anything from app.models or
    app.services governance/persistence code - see
    tests/test_discovery_architectural_safety.py, which enforces this by
    AST inspection, not just convention.

HIGH-BAND SAFETY INVARIANT (Mission #10A's empirical finding, Mission
#10B Part I - the reason this file's banding logic is rule-based, not a
bare numeric threshold): a real live LCY search surfaced several
irrelevant .gov/.gov.uk pages (US weather-service pages, a generic
company registry entry) that matched "official-looking domain + exact
airport identity in title" just as strongly as blu-3's real, relevant
contract announcement - domain authority plus identity alone cannot
distinguish a relevant safety-topic source from institutional noise about
the same airport. Therefore:

    HIGH requires a STRONG concept-term-in-TITLE match (this result is
    demonstrably about EMAS/RESA/runway-safety technology specifically,
    not just about the airport in general) AND at least one of {curated
    domain match, airport identity match}. Domain authority alone is
    never sufficient for HIGH. A bare concept/acronym match alone (no
    domain, no identity) is never sufficient for HIGH either - it stays
    MEDIUM at most. This also means the design remains safe for Global
    Discovery, where no AirportIdentity may exist at all: HIGH is still
    reachable there via strong-concept-in-title + curated domain, never
    via concept alone.

MISSION #10B.1 REFINEMENT (real live LCY acceptance testing under #10B's
original single concept-vocabulary): 4 of 8 HIGH results were generic
"£300-500m airport expansion" business-press coverage that qualified
purely via "Exact airport name in title" + a bare "procurement" or
"construction" title match - both real V1 concept terms, but generic
business-activity vocabulary that says nothing about EMAS/RESA/runway-
safety technology specifically. STRONG_CONCEPT_TERMS/WEAK_CONCEPT_TERMS
below split the vocabulary so only a STRONG term in the title can satisfy
HIGH's concept requirement; WEAK terms still produce reasons and still
affect deterministic ordering within a band, but can never by themselves
turn "airport identity + generic activity word" into HIGH.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from app.discovery.dedup import DedupedResult
from app.discovery.identity import AirportIdentity

# --- Curated domain seed (Mission #10B Part F) ------------------------------
#
# Deliberately SMALL, explicit, and version-controlled. Every entry below
# is justified by an already-governed/researched RWI case (Missions #8B-
# #8D's real LCY ingestion; Mission #9A/#9F.1's real YTZ RESA research) -
# never a broad TLD pattern (*.gov, *.gov.uk are explicitly NOT used - see
# Mission #10A Part 7's empirical false-positive finding above) and never
# a guessed airport/operator domain (Mission #10A Part 4: "UNSAFE without
# a registry" - AirportIdentity carries no website field to check against,
# so operator domains are intentionally left unclassified in V1, not
# guessed from the airport name).
#
# A domain classification RAISES REVIEW PRIORITY ONLY. It never
# represents evidence reliability or truth - see PriorityBand's own
# docstring below for the wording discipline this implies.
REGULATOR_DOMAINS: frozenset[str] = frozenset(
    {
        "caa.co.uk",  # UK Civil Aviation Authority - real LCY ACP-2022-090 primary source (Mission #8B/#8D)
        "tc.canada.ca",  # Transport Canada - real YTZ RESA project source (Mission #9A/#9F.1)
        "toronto.ca",  # City of Toronto - real YTZ RESA project source (Mission #9A/#9F.1)
    }
)
VENDOR_CONTRACTOR_DOMAINS: frozenset[str] = frozenset(
    {
        "runwaysafe.com",  # real LCY vendor primary source (Mission #8B/#8D)
        "blu-3.co.uk",  # real LCY contractor primary source (Mission #8B/#8D)
    }
)
# Deliberately NOT added, to keep the seed minimal (Mission #10B Part F:
# "keep additions minimal and explain each"): www.canada.ca (broader than
# tc.canada.ca) and iaac-aeic.gc.ca (Canadian Impact Assessment Agency)
# both appeared as materially relevant in Mission #10A's real live YTZ
# data, but neither was named in this mission's authorized example list -
# a plausible future addition, not made unilaterally here. Airport/
# operator domains (e.g. billybishopairport.com, londoncityairport.com)
# are deliberately excluded for the same "no guessing" reason.


class DomainCategory(str, Enum):
    REGULATOR = "REGULATOR"
    VENDOR_CONTRACTOR = "VENDOR_CONTRACTOR"
    UNKNOWN = "UNKNOWN"


def classify_domain(url: str) -> DomainCategory:
    """Small, explicit lookup only - never a pattern/heuristic. An unknown
    domain is a normal, unpenalized state (Mission #10B Part F)."""
    host = urlsplit(url).netloc.lower().split(":")[0]
    for entry in REGULATOR_DOMAINS:
        if host == entry or host.endswith("." + entry):
            return DomainCategory.REGULATOR
    for entry in VENDOR_CONTRACTOR_DOMAINS:
        if host == entry or host.endswith("." + entry):
            return DomainCategory.VENDOR_CONTRACTOR
    return DomainCategory.UNKNOWN


# --- Concept vocabulary (Mission #10B Part G, split by Mission #10B.1 Part B) -
#
# Reuses app.discovery.query's real V1 concept phrases (EMAS, RESA,
# arresting system, runway safety, procurement, construction, runway
# extension - see query.py's _CONCEPT_PLAN) plus two literal acronym
# expansions Mission #10B's brief named explicitly ("engineered materials
# arresting system", "runway end safety area"), which real documents often
# spell out in full. "civil aviation regulator" - the one _CONCEPT_PLAN
# phrase NOT reused here - is intentionally excluded: that concept already
# lives at the domain-classification layer above, not as literal
# page-content text to search for. query.py itself is untouched by this
# mission - these are independently-defined, explicitly-sourced literal
# strings, not an import of query.py's private constant.
#
# Mission #10B.1's smallest coherent split of that same 9-term vocabulary,
# using the mission's own two category descriptions:
#
# STRONG - specifically about runway-safety/arrestor technology; only a
# STRONG title match can satisfy HIGH's concept requirement. Includes
# "runway safety" itself: unlike "procurement"/"construction"/"runway
# extension" (generic activity words applicable to any infrastructure
# project - the real, demonstrated source of #10B's false positives),
# "runway safety" is a direct, specific match to RWI's actual research
# domain, not a generic contextual-activity word - so it is classified
# STRONG, even though the mission's own example list did not name it
# explicitly either way.
STRONG_CONCEPT_TERMS: tuple[str, ...] = (
    "EMAS",
    "engineered materials arresting system",
    "RESA",
    "runway end safety area",
    "arresting system",
    "runway safety",
)
# WEAK - real, useful triage signals (still produce reasons, still affect
# deterministic ordering within a band) but generic enough, on their own,
# to describe any infrastructure project - never sufficient alone to
# satisfy HIGH's concept requirement.
WEAK_CONCEPT_TERMS: tuple[str, ...] = (
    "procurement",
    "construction",
    "runway extension",
)
CONCEPT_TERMS: tuple[str, ...] = STRONG_CONCEPT_TERMS + WEAK_CONCEPT_TERMS

_DOCUMENT_PATH_MARKERS = ("/documents/", "/document/", "/download")


def _phrase_present(text: str | None, phrase: str) -> bool:
    """Literal, case-insensitive, word/phrase-boundary-safe match. No
    semantic inference - a phrase either literally appears, bounded by
    non-word characters (or the string edges) on both sides, or it
    doesn't. This is what makes IATA/ICAO matching safe (Mission #10B
    Part H: "LCY should not match arbitrary characters inside an
    unrelated word") and is applied uniformly to every literal signal in
    this module for the same reason."""
    if not text or not phrase:
        return False
    pattern = r"\b" + re.escape(phrase) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


class PriorityBand(str, Enum):
    """A band means ONLY how early a human should look, never anything
    about truth:

        HIGH   - inspect early
        MEDIUM - potentially useful
        LOW    - lower immediate inspection priority

    Never described (here or in any reviewer-facing text built from this
    module) using words like confirmed/verified/accepted/true/proven.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


_BAND_SORT_ORDER = {PriorityBand.HIGH: 0, PriorityBand.MEDIUM: 1, PriorityBand.LOW: 2}


@dataclass(frozen=True)
class TriagedResult:
    """One DedupedResult plus its priority band and literal, ordered
    reasons. Runtime-only, immutable, carries no DB identifier and no
    persistence semantics. Deliberately does NOT carry the internal
    numeric weight used to order results within a band - that weight is
    local to triage_results() and cannot leak through this object."""

    deduped: DedupedResult
    band: PriorityBand
    reasons: tuple[str, ...]
    domain_category: DomainCategory


def _score_one(
    deduped: DedupedResult, identity: AirportIdentity | None
) -> tuple[int, PriorityBand, tuple[str, ...], DomainCategory]:
    result = deduped.result
    reasons: list[str] = []
    points = 0

    domain_category = classify_domain(result.url)
    if domain_category is DomainCategory.REGULATOR:
        reasons.append("Regulator domain")
        points += 3
    elif domain_category is DomainCategory.VENDOR_CONTRACTOR:
        reasons.append("Vendor/contractor domain")
        points += 2

    identity_matched = False
    if identity is not None:
        if _phrase_present(result.title, identity.name):
            reasons.append("Exact airport name in title")
            points += 2
            identity_matched = True
        if identity.iata_code and _phrase_present(result.title, identity.iata_code):
            reasons.append("IATA in title")
            points += 2
            identity_matched = True
        if identity.icao_code and _phrase_present(result.title, identity.icao_code):
            reasons.append("ICAO in title")
            points += 2
            identity_matched = True

    # Mission #10B.1 Part D: only a STRONG concept term in the TITLE can
    # satisfy HIGH's concept requirement. WEAK terms still produce a
    # reason and still contribute (smaller) points for ordering - they
    # just never set `strong_concept_title_matched`.
    strong_concept_title_matched = False
    title_matched_terms: set[str] = set()
    for term in STRONG_CONCEPT_TERMS:
        if _phrase_present(result.title, term):
            reasons.append(f"{term} in title")
            points += 2
            strong_concept_title_matched = True
            title_matched_terms.add(term)
    for term in WEAK_CONCEPT_TERMS:
        if _phrase_present(result.title, term):
            reasons.append(f"{term} in title")
            points += 1
            title_matched_terms.add(term)
    # Snippet matches are weaker still (Part E: "lower importance") and
    # only reported for a term not already credited via the title, so a
    # result is never given two reasons for the same concept term. A
    # snippet-only match - strong or weak - never sets
    # strong_concept_title_matched either way (title-only, by design).
    for term in CONCEPT_TERMS:
        if term in title_matched_terms:
            continue
        if _phrase_present(result.snippet, term):
            reasons.append(f"{term} in snippet")
            points += 1

    path = urlsplit(result.url).path.lower()
    if path.endswith(".pdf") or any(marker in path for marker in _DOCUMENT_PATH_MARKERS):
        reasons.append("PDF/document result")
        points += 1

    query_count = len(deduped.found_by)
    if query_count >= 2:
        reasons.append(f"Surfaced by {query_count} search queries")
        points += 1

    # HIGH-band safety invariant (Mission #10A/#10B Part I, tightened by
    # #10B.1 Part D): domain authority alone, a bare concept match alone,
    # or airport identity + only a WEAK/generic concept term, is never
    # enough - HIGH requires a STRONG concept-in-title match plus at
    # least one of {curated domain, airport identity}.
    if strong_concept_title_matched and (domain_category is not DomainCategory.UNKNOWN or identity_matched):
        band = PriorityBand.HIGH
    elif reasons:
        band = PriorityBand.MEDIUM
    else:
        band = PriorityBand.LOW

    return points, band, tuple(reasons), domain_category


def triage_results(
    deduped_results: list[DedupedResult],
    *,
    identity: AirportIdentity | None = None,
) -> list[TriagedResult]:
    """Pure, deterministic: list[DedupedResult] -> ordered list[TriagedResult].

    `identity` is optional (Mission #10B Part H / future Global Discovery:
    triage must function with no known AirportIdentity, using only
    concept/domain/document/provenance signals). Same input always
    produces the same output in the same order - internal ordering uses
    (band, -points, provider rank, url) as a fully deterministic tiebreak
    chain; `points` itself is never attached to the returned objects.
    """
    scored = [(*_score_one(d, identity), d) for d in deduped_results]
    scored.sort(
        key=lambda item: (
            _BAND_SORT_ORDER[item[1]],
            -item[0],
            item[4].result.rank,
            item[4].result.url,
        )
    )
    return [
        TriagedResult(deduped=deduped, band=band, reasons=reasons, domain_category=domain_category)
        for _points, band, reasons, domain_category, deduped in scored
    ]
