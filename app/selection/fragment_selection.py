"""Deterministic Fragment Selection over ExtractedDocument (RWI Mission
#13B). Pure, DB-free, network-free: `select_fragments(document, ...)`
takes an already-produced app.extraction.generic_pdf.ExtractedDocument
plus a configurable vocabulary/window/merge/suppression configuration and
returns a DocumentSelection of runtime-only FragmentSelection objects.

CHAIN PRESERVED: evidence material -> literal trigger -> human attention
-> later judgment. This module supplies only the literal-trigger/
attention boundary - it never concludes anything about truth, project
state, vendor, or airport identity. A search-seed AirportIdentity-shaped
input is attention context only (see AirportIdentityContext below); it is
never itself evidence and is never required for a concept match to be
selected.

VOCABULARY RECONCILIATION (Mission #13A Part 7, Mission #13B Part F):
explicitly merges three real, independently-maintained vocabularies -
app.discovery.triage.STRONG_CONCEPT_TERMS, app.acquisition.
mac_granicus_extractor.RELEVANT_KEYWORDS, and Triage's WEAK_CONCEPT_TERMS
plus MAC's runway-work-activity terms - WITHOUT silently assuming
differently-phrased variants are equivalent. "engineered material
arresting" (MAC) and "engineered materials arresting system" (Triage) are
preserved as two distinct literal terms; "runway safety area" (MAC) and
"runway end safety area" (Triage) likewise. This module intentionally
does NOT import app.discovery at all (see AirportIdentityContext's own
docstring for why) and does NOT import app.acquisition.mac_granicus_
extractor either - the reconciled term tuples below are an independent,
explicitly-sourced copy, matching this codebase's own established
"independent copies, same reasoning, zero cross-import" convention
(already used between app.acquisition.mac_granicus and
app.acquisition.mac_granicus_extractor's own RELEVANT_KEYWORDS).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from app.extraction.generic_pdf import ExtractedDocument, ExtractedPage, ExtractionStatus

SELECTION_VERSION = "0.1"

# --- Vocabulary (Mission #13B Part F) ---------------------------------------

# Reconciled from app.discovery.triage.STRONG_CONCEPT_TERMS (EMAS,
# engineered materials arresting system, RESA, runway end safety area,
# arresting system, runway safety) plus app.acquisition.
# mac_granicus_extractor.RELEVANT_KEYWORDS' two additional distinct
# phrasings not present in Triage's own list ("engineered material
# arresting" - singular, no "system" - and "runway safety area", a
# genuinely different term from "runway end safety area", not a typo).
#
# "arrestor bed"/"arresting bed" (Mission #25J3): a real, empirically-
# confirmed vocabulary gap - Roland Garros airport-operator Snapshot 10
# describes its 2017 installation as "Installation of an arrestor bed at
# point 30..." and machine Selection missed it entirely (nominated an
# unrelated 2013 construction passage instead - see Mission #25J2's own
# manual-range workaround for that exact case). Both are compound,
# domain-specific phrases with negligible false-positive surface, unlike
# a bare "arrestor" (deliberately NOT added - too broad/noisy per Mission
# #25J3's own explicit instruction; a two-word compound phrase is far
# less likely to collide with an unrelated sense of the word than the
# bare noun would be). "aircraft arresting" was evaluated and NOT added:
# no real evidence text found anywhere this session (Roland Garros,
# Haneda, or elsewhere) actually uses that exact phrase - adding a term
# with no observed real-world grounding would be speculative vocabulary,
# against this repository's own established discipline of adding
# structure only once a real, demonstrated need exists.
STRONG_CONCEPT_TERMS: tuple[str, ...] = (
    "EMAS",
    "engineered materials arresting system",
    "engineered material arresting",
    "RESA",
    "runway end safety area",
    "runway safety area",
    "arresting system",
    "runway safety",
    "arrestor bed",
    "arresting bed",
)

# Reconciled from Triage's WEAK_CONCEPT_TERMS (procurement, construction,
# runway extension) plus MAC's own runway-work/activity vocabulary
# (rehabilitation/reconstruction/replacement/resurfacing/repair) - a
# third "activity" source folded into the same weak tier per Mission
# #13A's own recommendation, since neither set implies the runway-safety
# TECHNOLOGY concept on its own.
WEAK_ACTIVITY_TERMS: tuple[str, ...] = (
    "procurement",
    "construction",
    "runway extension",
    "runway rehabilitation",
    "runway reconstruction",
    "runway replacement",
    "runway resurfacing",
    "runway repair",
)


@dataclass(frozen=True)
class SelectionVocabulary:
    """Caller-supplied, configurable (Mission #13B Part F: "Selection
    logic must allow future caller-supplied vocabularies without changing
    its matching engine"). A future local-language vocabulary is a new
    SelectionVocabulary instance, never a change to this module's own
    matching code."""

    strong_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]


DEFAULT_VOCABULARY = SelectionVocabulary(strong_terms=STRONG_CONCEPT_TERMS, weak_terms=WEAK_ACTIVITY_TERMS)


@dataclass(frozen=True)
class AirportIdentityContext:
    """The smallest neutral input shape for airport-identity-based
    selection context (Mission #13B Part O). Deliberately NOT
    app.discovery.identity.AirportIdentity: Selection sits downstream of
    Extraction, which already (Mission #12B) forbids any dependency on
    app.discovery; importing AirportIdentity here would reintroduce
    exactly that cross-layer coupling one level up, in the wrong
    direction for this pipeline's intended one-way dependency flow. A
    caller that already holds a real AirportIdentity trivially builds
    this from it (name/iata_code/icao_code/aliases are a pure, lossless
    subset of the same plain string data) - Selection itself never
    imports or depends on Discovery's own module.

    This is attention context ONLY - a literal, page-local match against
    these terms produces an `airport_identity_match` SelectionReason and
    nothing more. It is never required for a concept-term match to be
    selected, and the search-seed identity itself is never evidence."""

    name: str
    iata_code: str | None = None
    icao_code: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


# --- Runtime result types (Mission #13B Part D) ------------------------------


class SelectionReasonKind(str, Enum):
    """Only the kinds justified by Mission #13A's real vocabulary
    findings - not proliferated. No vendor/incident/project-state kinds
    exist because no real vocabulary for them has been established yet."""

    STRONG_CONCEPT_TERM = "strong_concept_term"
    WEAK_ACTIVITY_TERM = "weak_activity_term"
    AIRPORT_IDENTITY_MATCH = "airport_identity_match"
    # Mission #25J2: a human, not select_fragments()'s own term-matching
    # engine, chose this exact range - never produced by select_fragments()
    # itself; only by app.selection.manual_range_selection.select_manual_range().
    # A different axis than the three term-matching kinds above (WHY a
    # fragment exists at all, not which vocabulary word matched) - adding
    # this is not a Selection VOCABULARY change (no new
    # STRONG_CONCEPT_TERMS/WEAK_ACTIVITY_TERMS string), and this member is
    # never read by select_fragments()'s own matching logic.
    HUMAN_MANUAL_RANGE = "human_manual_range"


@dataclass(frozen=True)
class SelectionReason:
    """`matched_text` is the literal substring exactly as it appeared in
    the page text (preserving original casing) - never the lowercased/
    normalized vocabulary term itself."""

    kind: SelectionReasonKind
    matched_text: str


@dataclass(frozen=True)
class FragmentSelection:
    """One bounded, exact-provenance passage worth human review.
    `text` is ALWAYS `page.text[start_offset:end_offset]` verbatim - no
    whitespace rewriting, no paraphrasing, no lowercasing. Carries no
    persistence identifier and constructs no CandidateFragment - see the
    package docstring for the governance boundary this stops at."""

    document_identity: str
    page_number: int
    start_offset: int
    end_offset: int
    text: str
    reasons: tuple[SelectionReason, ...]

    def __post_init__(self) -> None:
        if not self.document_identity or not self.document_identity.strip():
            raise ValueError("FragmentSelection.document_identity is required and cannot be empty")
        if self.page_number < 1:
            raise ValueError(f"FragmentSelection.page_number must be 1-based (>=1), got {self.page_number}")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError(
                f"FragmentSelection offsets are invalid: start={self.start_offset}, end={self.end_offset}"
            )
        if len(self.text) != self.end_offset - self.start_offset:
            raise ValueError(
                f"FragmentSelection.text length ({len(self.text)}) does not match "
                f"end_offset-start_offset ({self.end_offset - self.start_offset})"
            )
        if not self.reasons:
            raise ValueError("FragmentSelection must carry at least one SelectionReason")


@dataclass(frozen=True)
class DocumentSelection:
    """Top-level, versioned output of one select_fragments() call.
    `suppressed_lines` is audit-only (Mission #13B Part J/Q): the exact
    repeated-line strings detected and excluded from matching, sorted
    deterministically - never used to alter any ExtractedPage.text or any
    FragmentSelection.text."""

    document_identity: str
    selection_version: str
    fragments: tuple[FragmentSelection, ...]
    suppressed_lines: tuple[str, ...] = field(default_factory=tuple)


# --- Configuration (Mission #13B Part H/I/J - explicit, not magic) ---------

DEFAULT_WINDOW_CHARS = 300
DEFAULT_MERGE_GAP_CHARS = 100

# A line is treated as running header/footer material only if it repeats
# on MORE than this fraction of the document's pages (Mission #13A's own
# empirical recommendation: >50%) AND appears at least this many times in
# absolute terms - the absolute floor guards a small document (e.g. 2
# pages sharing one line = 100% but only 2 occurrences) from being
# over-suppressed on a coincidentally-shared short line rather than a
# genuine, substantial repeated pattern.
MIN_REPEATED_LINE_FRACTION = 0.5
MIN_REPEATED_LINE_ABSOLUTE_COUNT = 3

_SELECTABLE_STATUSES = (ExtractionStatus.SUCCESS, ExtractionStatus.PARTIAL)


# --- Matching (Mission #13B Part G) ------------------------------------------


def _find_phrase_matches(text: str, phrase: str) -> list[tuple[int, int, str]]:
    """Literal, case-insensitive, word/phrase-boundary-safe match -
    structurally identical behavior to app.discovery.triage's own
    _phrase_present (independently reimplemented, not imported - see
    module docstring for why), extended here to report every match
    position, not just presence. Returns (start, end, matched_text)
    tuples; matched_text is the literal, original-case substring of
    `text` (never the lowercased `phrase` itself)."""
    if not text or not phrase:
        return []
    pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
    return [(m.start(), m.end(), m.group(0)) for m in pattern.finditer(text)]


def _identity_terms(identity: AirportIdentityContext) -> list[str]:
    terms = [identity.name]
    if identity.iata_code:
        terms.append(identity.iata_code)
    if identity.icao_code:
        terms.append(identity.icao_code)
    terms.extend(identity.aliases)
    return terms


def _raw_matches(
    text: str, vocabulary: SelectionVocabulary, identity: AirportIdentityContext | None
) -> list[tuple[int, int, str, SelectionReasonKind]]:
    matches: list[tuple[int, int, str, SelectionReasonKind]] = []
    for term in vocabulary.strong_terms:
        for start, end, matched in _find_phrase_matches(text, term):
            matches.append((start, end, matched, SelectionReasonKind.STRONG_CONCEPT_TERM))
    for term in vocabulary.weak_terms:
        for start, end, matched in _find_phrase_matches(text, term):
            matches.append((start, end, matched, SelectionReasonKind.WEAK_ACTIVITY_TERM))
    if identity is not None:
        for term in _identity_terms(identity):
            for start, end, matched in _find_phrase_matches(text, term):
                matches.append((start, end, matched, SelectionReasonKind.AIRPORT_IDENTITY_MATCH))
    return matches


# --- Repeated header/footer suppression (Mission #13B Part J) --------------


def detect_repeated_lines(
    document: ExtractedDocument,
    *,
    min_page_fraction: float = MIN_REPEATED_LINE_FRACTION,
    min_absolute_count: int = MIN_REPEATED_LINE_ABSOLUTE_COUNT,
) -> frozenset[str]:
    """Generic, document-local, statistical - no source-specific strings
    anywhere. A line (a newline-delimited, stripped segment of a page's
    own text) that appears verbatim on more than `min_page_fraction` of
    the document's pages, AND at least `min_absolute_count` times, is
    treated as running header/footer material. Requires at least 2 pages
    - a 1-page document has no meaningful "repeated across pages"
    concept. Affects MATCHING ONLY (see _suppressed_char_ranges) - never
    used to alter ExtractedPage.text or any FragmentSelection.text."""
    if document.page_count < 2:
        return frozenset()
    line_page_counts: Counter[str] = Counter()
    for page in document.pages:
        lines_on_this_page = {line.strip() for line in page.text.split("\n") if line.strip()}
        line_page_counts.update(lines_on_this_page)
    threshold_pages = document.page_count * min_page_fraction
    return frozenset(
        line
        for line, count in line_page_counts.items()
        if count > threshold_pages and count >= min_absolute_count
    )


def _suppressed_char_ranges(page_text: str, suppressed_lines: frozenset[str]) -> list[tuple[int, int]]:
    """Character ranges within `page_text` matching a detected repeated
    line - used only to decide which literal matches are valid selection
    TRIGGERS. Never used to slice/alter `page_text` itself; a
    context window that happens to span a suppressed range still
    preserves that exact original text verbatim (Mission #13B Part J)."""
    if not suppressed_lines:
        return []
    ranges: list[tuple[int, int]] = []
    offset = 0
    for line in page_text.split("\n"):
        if line.strip() in suppressed_lines:
            ranges.append((offset, offset + len(line)))
        offset += len(line) + 1  # +1 for the newline character consumed by split
    return ranges


def _overlaps_any(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


# --- Windowing and merging (Mission #13B Part H/I) ---------------------------


def _select_from_page(
    document_identity: str,
    page: ExtractedPage,
    *,
    vocabulary: SelectionVocabulary,
    identity: AirportIdentityContext | None,
    window_chars: int,
    merge_gap_chars: int,
    suppressed_lines: frozenset[str],
) -> list[FragmentSelection]:
    text = page.text
    if not text:
        return []

    suppressed_ranges = _suppressed_char_ranges(text, suppressed_lines)
    candidates = [m for m in _raw_matches(text, vocabulary, identity) if not _overlaps_any(m[0], m[1], suppressed_ranges)]
    if not candidates:
        return []

    # Airport identity is attention context ONLY (Mission #13B Part E):
    # it never seeds a fragment by itself. Only a real strong/weak
    # concept-term match creates/extends a cluster; an identity match is
    # attached as an additional reason only when it falls within a
    # cluster's already-determined window - never used to expand that
    # window and never capable of producing an identity-only fragment.
    concept_matches = sorted(
        (m for m in candidates if m[3] != SelectionReasonKind.AIRPORT_IDENTITY_MATCH), key=lambda m: (m[0], m[1])
    )
    identity_matches = [m for m in candidates if m[3] == SelectionReasonKind.AIRPORT_IDENTITY_MATCH]
    if not concept_matches:
        return []

    text_len = len(text)
    clusters: list[dict] = []
    for start, end, matched_text, kind in concept_matches:
        window_start = max(0, start - window_chars)
        window_end = min(text_len, end + window_chars)
        if clusters and window_start <= clusters[-1]["end"] + merge_gap_chars:
            clusters[-1]["end"] = max(clusters[-1]["end"], window_end)
            clusters[-1]["reason_hits"].append((kind, matched_text))
        else:
            clusters.append({"start": window_start, "end": window_end, "reason_hits": [(kind, matched_text)]})

    for start, end, matched_text, kind in identity_matches:
        for cluster in clusters:
            if start < cluster["end"] and end > cluster["start"]:
                cluster["reason_hits"].append((kind, matched_text))

    fragments: list[FragmentSelection] = []
    for cluster in clusters:
        start, end = cluster["start"], cluster["end"]
        seen: set[tuple[SelectionReasonKind, str]] = set()
        reasons: list[SelectionReason] = []
        for kind, matched_text in cluster["reason_hits"]:
            key = (kind, matched_text)
            if key in seen:
                continue
            seen.add(key)
            reasons.append(SelectionReason(kind=kind, matched_text=matched_text))
        fragments.append(
            FragmentSelection(
                document_identity=document_identity,
                page_number=page.page_number,
                start_offset=start,
                end_offset=end,
                text=text[start:end],
                reasons=tuple(reasons),
            )
        )
    return fragments


# --- Top-level entry point ---------------------------------------------------


def select_fragments(
    document: ExtractedDocument,
    *,
    vocabulary: SelectionVocabulary = DEFAULT_VOCABULARY,
    airport_identity: AirportIdentityContext | None = None,
    window_chars: int = DEFAULT_WINDOW_CHARS,
    merge_gap_chars: int = DEFAULT_MERGE_GAP_CHARS,
    min_repeated_line_fraction: float = MIN_REPEATED_LINE_FRACTION,
    min_repeated_line_absolute_count: int = MIN_REPEATED_LINE_ABSOLUTE_COUNT,
) -> DocumentSelection:
    """Pure function: ExtractedDocument + configuration -> DocumentSelection.
    Deterministic - the same document/vocabulary/identity/configuration
    always produces the same fragments in the same order (Mission #13B
    Part N).

    FAILS CLOSED (Mission #13B Part M): only ExtractionStatus.SUCCESS and
    .PARTIAL are selectable. NO_TEXT, UNSUPPORTED_CONTENT, and
    PARSE_FAILURE all deterministically produce zero fragments - this
    module never invents a new extraction-status semantic and never
    silently selects from a document the extraction layer itself marked
    unsuitable.

    Page-local only (Mission #13B Part K): a page must contain its own
    literal selectable match. No cross-page inference of any kind.
    """
    if document.status not in _SELECTABLE_STATUSES:
        return DocumentSelection(
            document_identity=document.document_identity, selection_version=SELECTION_VERSION, fragments=()
        )

    suppressed_lines = detect_repeated_lines(
        document, min_page_fraction=min_repeated_line_fraction, min_absolute_count=min_repeated_line_absolute_count
    )

    all_fragments: list[FragmentSelection] = []
    for page in document.pages:
        all_fragments.extend(
            _select_from_page(
                document.document_identity,
                page,
                vocabulary=vocabulary,
                identity=airport_identity,
                window_chars=window_chars,
                merge_gap_chars=merge_gap_chars,
                suppressed_lines=suppressed_lines,
            )
        )

    return DocumentSelection(
        document_identity=document.document_identity,
        selection_version=SELECTION_VERSION,
        fragments=tuple(all_fragments),
        suppressed_lines=tuple(sorted(suppressed_lines)),
    )
