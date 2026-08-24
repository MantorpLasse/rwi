"""MAC-Granicus-document extraction boundary
(docs/product/msp-authoritative-discovery-provider-pilot.md).

Sits between app.acquisition.mac_granicus (acquisition boundary - raw
bytes) and app.services.discovery_candidate_fragment.CandidateFragment
(the already-committed extraction envelope). Produces a CandidateFragment
from one acquired PDF document's bytes, or None if the document's own
text contains no runway-safety/EMAS-relevant terminology - never a
Signal, never an airport identity decision, never database access.

Rule-based, not AI-assisted, in this slice - regexes match structural,
generic MAC memo phrasing ("sole source procurement with X", "Purchase
Order to X in the amount of $Y", "Runway NNL", item/date headers), never
a literal search for "Runway Safe" (task instruction S9) - the same
patterns would extract a different vendor/runway/amount from a
differently-worded MAC memo using the same boilerplate structure, which
is exactly what MAC memos of this shape do (see
docs/product/msp-authoritative-discovery-provider-pilot.md for other
observed titles following the identical "Consent Item N.N.N" template).

Deliberately split into a PDF-bytes layer (`_extract_text`, the only part
that needs a real PDF fixture to test) and a pure-text layer
(`_fragment_from_text`, directly testable with plain strings) - mirrors
the pdfplumber-wrapper-vs-pure-regex split already established in
app/acquisition/faa_construction_report.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from io import BytesIO

import pdfplumber

from app.services.discovery_candidate_fragment import CandidateFragment, DiscoveryContext, ExtractedDate, ExtractedMoney

__all__ = [
    "MACGranicusExtractionErrorCode",
    "MACGranicusExtractionError",
    "RELEVANT_KEYWORDS",
    "is_relevant_text",
    "extract_candidate_fragment",
]

PARSER_VERSION = "mac-granicus-emas-memo/0.1"

# Identical set to app.acquisition.mac_granicus.RELEVANT_KEYWORDS - kept as
# its own copy (not imported) so this module has zero dependency on the
# acquisition-boundary module, matching the existing repository convention
# of extraction modules never importing their own provider module (e.g.
# faa_emas_parser.py does not import faa.py). Kept as the full, unchanged
# historical vocabulary (informational/back-compat) - see
# app.acquisition.mac_granicus's own identical comment for why the actual
# matching logic below (also an independent copy, same reasoning) splits
# it into two differently-matched vocabularies.
RELEVANT_KEYWORDS = (
    "emas",
    "engineered material arresting",
    "arresting system",
    "runway safety area",
    "runway rehabilitation",
    "runway reconstruction",
    "runway replacement",
    "runway resurfacing",
    "runway repair",
)

_STANDALONE_RELEVANCE_PHRASES = (
    "emas",
    "engineered material arresting",
    "arresting system",
    "runway safety area",
)

# 5D fix (Controlled Live Pilot 5C/5D) - see app.acquisition.mac_granicus's
# identical constant for the full rationale (independent copy, same
# reasoning, matching this module's own zero-cross-import convention).
_RUNWAY_WORK_CONCEPTS = frozenset({"reconstruction", "rehabilitation", "replacement", "resurfacing", "repair"})
_DESIGNATION_TOKEN = re.compile(r"^\d{1,2}[lrc]?$")
_MAX_RUNWAY_WORK_GAP_TOKENS = 8
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class MACGranicusExtractionErrorCode(str, Enum):
    EMPTY_PAYLOAD = "empty_payload"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    UNREADABLE_PDF = "unreadable_pdf"


class MACGranicusExtractionError(ValueError):
    def __init__(self, code: MACGranicusExtractionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code.value


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _has_proximate_runway_work_mention(tokens: list[str]) -> bool:
    """See app.acquisition.mac_granicus._has_proximate_runway_work_mention()
    for the full rationale (independent copy, identical logic, same
    zero-cross-import convention this module already follows for
    RELEVANT_KEYWORDS)."""
    for index, token in enumerate(tokens):
        if token != "runway":
            continue
        if index + 1 >= len(tokens):
            continue
        next_token = tokens[index + 1]
        if next_token in _RUNWAY_WORK_CONCEPTS:
            return True
        if not _DESIGNATION_TOKEN.match(next_token):
            continue
        window = tokens[index + 1 : index + 2 + _MAX_RUNWAY_WORK_GAP_TOKENS]
        if any(candidate in _RUNWAY_WORK_CONCEPTS for candidate in window):
            return True
    return False


def is_relevant_text(text: str) -> bool:
    """5D: also recognizes a "runway" mention structurally near (not just
    contiguous with) a runway-work concept - see
    _has_proximate_runway_work_mention()."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in _STANDALONE_RELEVANCE_PHRASES):
        return True
    return _has_proximate_runway_work_mention(_tokenize(text))


_RUNWAY_PAIR = re.compile(r"\bRunway\s+(\d{1,2}[LRC]?)\s*-\s*(\d{1,2}[LRC]?)\b", re.IGNORECASE)
_RUNWAY_END = re.compile(r"\bR(?:unway|WY|/W)\.?\s+(\d{1,2}[LRC]?)\b(?!\s*-\s*\d)", re.IGNORECASE)

# 5F (RWI Controlled Live Pilot 5F): a generic, structural "<capitalized
# name phrase> Airport" pattern, matched ONLY against `document_title` -
# never against the PDF body text (see the module-level note above
# extract_candidate_fragment() for exactly why the body is deliberately
# excluded: a single real MAC memo, independently inspected during 5F,
# was found to mention THREE distinct airports for three different,
# non-identity reasons - the document's own subject ("The Anoka
# County-Blaine Airport (ANE) is part of..."), background/purpose context
# ("...to reduce congestion at the Minneapolis-St. Paul International
# Airport..."), and an unrelated funding cross-reference ("...funds from
# the 2026 Flying Cloud Airport (FCM) ... project be reallocated...").
# Naively scanning the body would extract all three (plus a newline-
# corrupted PDF-extraction artifact) as if they were equally valid subject
# claims - exactly the kind of contamination the guard's own contradiction
# machinery exists to arbitrate, not something this regex can safely
# pre-judge. The agenda TITLE, by contrast, is MAC's own short, single-
# subject, structured description of what this ONE specific consent item
# concerns - every real title observed across Pilot 5C/5E's own
# reconnaissance names at most one airport, never more.
#
# Structural, not airport-specific: matches capitalized tokens (allowing
# internal periods/apostrophes/hyphens, e.g. "St." or "County-Blaine"),
# plus the single lowercase connector "and" (5F review fix - see below),
# immediately followed by the singular word "Airport" - `\bAirport\b`
# already excludes the plural "Airports" (no word boundary between
# "Airport" and a directly-following "s"), which is what keeps
# "Metropolitan Airports Commission" (the issuer, not an airport name)
# from ever matching. The trailing negative lookahead rejects "Airport"
# immediately followed by a common street-suffix word (e.g. "Example
# Airport Road") - a real street naming convention near many airports,
# not itself an airport identity claim.
#
# 5F REVIEW FIX: the original 5F implementation used a pure
# `(?:[A-Z][\w.'-]*\s+){1,5}` repetition, which truncated real,
# legitimate dual-named airports containing the lowercase connector
# "and" - e.g. "Bill and Hillary Clinton National Airport" matched only
# as "Hillary Clinton National Airport" (silently dropping "Bill and ").
# Fixed by allowing "and" as an interior connector, but ONLY when NOT
# immediately followed by "Airport" itself (`and(?!\s+Airport\b)`) - this
# is what keeps a genuinely two-airport title ("Roads and Airport
# Improvements", or a title naming two distinct real airports joined by
# "and") from being misread as "and" being part of a single airport's
# name. Separately, "Airport" is explicitly excluded from matching the
# generic capitalized-filler alternative (`(?!Airport\b)[A-Z][\w.'-]*`) -
# without this, a title naming two airports back-to-back ("Example
# Regional Airport and Sample Municipal Airport") would greedily merge
# them into one bogus combined name instead of two independent claims,
# since the regex would otherwise treat the FIRST "Airport" as just
# another filler word on the way to the LAST one. Both defects were
# found and closed during the 5F adversarial review, before commit -
# neither was present in the originally-implemented, pre-review version.
_AIRPORT_NAME_IN_TITLE = re.compile(
    r"\b([A-Z][\w.'-]*(?:\s+(?:(?!Airport\b)[A-Z][\w.'-]*|and(?!\s+Airport\b)))*\s+Airport)\b"
    r"(?!\s+(?:Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Way|Street|St\.?|Avenue|Ave\.?)\b)"
)


def _extract_airport_names_from_title(document_title: "str | None") -> frozenset[str]:
    if not document_title:
        return frozenset()
    return frozenset(match.group(1) for match in _AIRPORT_NAME_IN_TITLE.finditer(document_title))


# The full name and the bare "MAC" self-reference are both genuinely
# observed in real MAC memos - this document itself never spells out
# "Metropolitan Airports Commission," only ever referring to itself as
# "MAC" ("MAC's purpose", "MAC has broad powers") - confirmed during this
# pilot's own real-document inspection, not assumed. "MAC" is matched
# case-sensitively (all-caps only) specifically to avoid false-positiving
# on the common lowercase word.
_ISSUER_FULL_NAME = re.compile(r"\bMetropolitan Airports Commission\b", re.IGNORECASE)
_ISSUER_ABBREVIATION = re.compile(r"\bMAC\b")
_KNOWN_MAC_COMMITTEES = (
    "Planning, Development and Environment Committee",
    "Operations, Finance and Administration Committee",
    "Management and Operations Committee",
    "Finance and Administration Committee",
)

# Deliberately structural/positional - matches ANY vendor name in this
# phrasing shape, not a literal "Runway Safe" search (task S9).
_VENDOR_SOLE_SOURCE = re.compile(
    r"sole source procurement with\s+([A-Z][\w&,.'\- ]*?)\s+for\b", re.IGNORECASE,
)
_VENDOR_PURCHASE_ORDER = re.compile(
    r"Purchase Order to\s+([A-Z][\w&,.'\- ]*?)\s+in the amount of", re.IGNORECASE,
)

_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
_DATE_LONG = re.compile(r"\b([A-Z][a-z]+ \d{1,2},\s*\d{4})\b")
_CONSENT_ITEM = re.compile(r"\bConsent Item\s+([0-9]+(?:\.[0-9]+)+)\.?", re.IGNORECASE)
_CIP_REFERENCE = re.compile(r"\b(\d{4}-\d{4}\s+CIP)\b", re.IGNORECASE)

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_LONG_DATE_PARTS = re.compile(r"([A-Za-z]+) (\d{1,2}), *(\d{4})")


def _parse_long_date(raw: str) -> date | None:
    match = _LONG_DATE_PARTS.match(raw)
    if not match:
        return None
    month_name, day, year = match.groups()
    month = _MONTH_NAMES.get(month_name.lower())
    if month is None:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _money_context_label(text: str, position: int) -> str | None:
    window = text[max(0, position - 120):position].lower()
    if "deposit" in window:
        return "advance_deposit"
    if "cip" in window:
        return "cip_project_ceiling"
    return None


def _date_context_role(text: str, position: int) -> str | None:
    before = text[max(0, position - 80):position].lower()
    after = text[position:position + 60].lower()
    if "date:" in before:
        return "memo_date"
    if "prior related actions" in before or "approved" in before or "approved" in after:
        return "prior_approval_date"
    return None


def _extract_text(pdf_bytes: bytes, media_type: str | None) -> str:
    if not pdf_bytes:
        raise MACGranicusExtractionError(
            MACGranicusExtractionErrorCode.EMPTY_PAYLOAD, "MAC Granicus document payload is empty.",
        )
    normalized_media_type = (media_type or "").partition(";")[0].strip().lower()
    if normalized_media_type and normalized_media_type != "application/pdf":
        raise MACGranicusExtractionError(
            MACGranicusExtractionErrorCode.UNSUPPORTED_MEDIA_TYPE,
            f"MAC Granicus extractor only supports application/pdf, got {normalized_media_type!r}.",
        )
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:  # pdfplumber/pdfminer raise assorted exception types on malformed PDFs
        raise MACGranicusExtractionError(
            MACGranicusExtractionErrorCode.UNREADABLE_PDF, f"MAC Granicus PDF could not be read: {exc}",
        ) from exc


def _fragment_from_text(
    text: str,
    *,
    artifact_identity: str,
    source_locator: str,
    document_title: str | None = None,
    url: str | None = None,
    publication_date: date | None = None,
    parser_identifier: str = PARSER_VERSION,
    extracted_at: datetime | None = None,
    discovery_context: DiscoveryContext | None = None,
) -> tuple[CandidateFragment, tuple[str, ...]] | None:
    """Pure, text-only extraction - never touches a database or network,
    never reads discovery_context for anything but pass-through audit
    storage. Returns None when the text contains no runway-safety/EMAS
    terminology at all (task test: non-relevant document produces no
    CandidateFragment)."""
    if not text or not text.strip():
        return None
    if not is_relevant_text(text):
        return None

    runway_ends: set[str] = set()
    runway_pairs: set[str] = set()
    for match in _RUNWAY_PAIR.finditer(text):
        first, second = match.group(1).upper(), match.group(2).upper()
        runway_ends.add(first)
        runway_ends.add(second)
        runway_pairs.add(f"{first}/{second}")
    for match in _RUNWAY_END.finditer(text):
        runway_ends.add(match.group(1).upper())

    # 5F: source-provided airport-name evidence from the agenda title only
    # (never the PDF body - see _AIRPORT_NAME_IN_TITLE's own docstring for
    # why). Deliberately no dedup/single-pick logic here: if the title
    # happens to name more than one airport, all are preserved as
    # independent name claims and the existing, unmodified guard/UAC3
    # machinery (which already has its own explicit "exactly one name is
    # formable, zero or many is not" rule) is left to arbitrate - this
    # extractor never chooses one.
    airport_names = _extract_airport_names_from_title(document_title)

    is_mac_issuer = bool(
        _ISSUER_FULL_NAME.search(text)
        or _ISSUER_ABBREVIATION.search(text)
        or any(committee in text for committee in _KNOWN_MAC_COMMITTEES)
    )
    issuers = frozenset({"Metropolitan Airports Commission"}) if is_mac_issuer else frozenset()

    terminology_hits = frozenset(
        keyword for keyword in RELEVANT_KEYWORDS if keyword in text.lower()
    )

    money_values: list[ExtractedMoney] = []
    for match in _MONEY.finditer(text):
        raw_amount = match.group(0)
        numeric = Decimal(match.group(1).replace(",", ""))
        money_values.append(
            ExtractedMoney(
                raw_text=raw_amount, numeric_value=numeric, currency="USD",
                context_label=_money_context_label(text, match.start()),
            )
        )

    dates: list[ExtractedDate] = []
    for match in _DATE_LONG.finditer(text):
        raw = match.group(1)
        dates.append(
            ExtractedDate(
                raw_text=raw, normalized_date=_parse_long_date(raw),
                semantic_role=_date_context_role(text, match.start()),
            )
        )

    contract_identifiers = frozenset(
        f"Consent Item {m.group(1)}" for m in _CONSENT_ITEM.finditer(text)
    )
    project_identifiers = frozenset(
        m.group(1) for m in _CIP_REFERENCE.finditer(text)
    )

    vendors: set[str] = set()
    for pattern in (_VENDOR_SOLE_SOURCE, _VENDOR_PURCHASE_ORDER):
        for match in pattern.finditer(text):
            vendor = match.group(1).strip().rstrip(",.")
            if vendor:
                vendors.add(vendor)

    return CandidateFragment(
        artifact_identity=artifact_identity,
        source_locator=source_locator,
        raw_text=text,
        issuers=issuers,
        airport_names=airport_names,
        runway_ends=frozenset(runway_ends),
        runway_pairs=frozenset(runway_pairs),
        # Vendor/organization names found in explicit procurement-action
        # text are carried as `issuers`-adjacent extraction facts via
        # terminology_hits/contract text only - CandidateFragment has no
        # dedicated vendor field (by its own design, "organizations" was
        # deliberately not added as a field separate from issuers - see
        # its own docstring), so real vendor findings are preserved in
        # `document_title`-adjacent raw text and reported separately by
        # this pilot's own report rather than invented as a new field here.
        project_identifiers=project_identifiers,
        contract_identifiers=contract_identifiers,
        money_values=tuple(money_values),
        dates=tuple(dates),
        terminology_hits=terminology_hits,
        language="en",
        document_title=document_title,
        url=url,
        publication_date=publication_date,
        parser_identifier=parser_identifier,
        extracted_at=extracted_at,
        discovery_context=discovery_context,
    ), tuple(sorted(vendors))


def extract_candidate_fragment(
    pdf_bytes: bytes,
    media_type: str | None,
    *,
    artifact_identity: str,
    source_locator: str,
    document_title: str | None = None,
    url: str | None = None,
    publication_date: date | None = None,
    parser_identifier: str = PARSER_VERSION,
    extracted_at: datetime | None = None,
    discovery_context: DiscoveryContext | None = None,
) -> tuple[CandidateFragment, tuple[str, ...]] | None:
    """Full pipeline: PDF bytes -> text (pdfplumber) -> CandidateFragment,
    or None if the document is not topically relevant. Returns the
    fragment paired with any vendor names found in explicit
    procurement-action text (audit/reporting use only - never fed into
    the guard, which has no vendor concept)."""
    text = _extract_text(pdf_bytes, media_type)
    result = _fragment_from_text(
        text,
        artifact_identity=artifact_identity,
        source_locator=source_locator,
        document_title=document_title,
        url=url,
        publication_date=publication_date,
        parser_identifier=parser_identifier,
        extracted_at=extracted_at,
        discovery_context=discovery_context,
    )
    return result
