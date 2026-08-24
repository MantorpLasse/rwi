"""Metropolitan Airports Commission (MAC) Granicus meeting-archive
acquisition provider and discovery (docs/product/msp-authoritative-
discovery-provider-pilot.md).

Source family: metroairports.granicus.com - MAC's official committee
meeting-management platform (agendas, itemized consent items, linked
memo/PDF documents). Chosen over metroairports.org/documents because
that site's full-text search (`?search_api_fulltext=`) does not filter
server-side over plain HTTP (verified during this pilot's research: an
unfiltered fetch and a `search_api_fulltext=EMAS` fetch return
byte-identical document listings) - it requires JS/AJAX. The Granicus
archive's own pagination (`ViewPublisher.php?view_id=N`, one committee's
full meeting history) and per-meeting agenda itemization
(`AgendaViewer.php` -> `GeneratedAgendaViewer.php`, one row per agenda
item with its own `MetaViewer.php` document link) both work over plain,
unauthenticated HTTP GET - no cookies, no JS, confirmed by direct `curl`
during this pilot's research.

This module performs the ACQUISITION boundary only (raw bytes in,
AcquisitionPayload/discovery candidates out) - no airport identity
decision, no CandidateFragment construction (see
app.acquisition.mac_granicus_extractor for that), no database access.
`MACGranicusAcquisitionProvider` matches the same `.source_url`/
`.version`/`.retrieve() -> AcquisitionPayload` protocol
app.acquisition.faa.FAAAcquisitionProvider already establishes, so it
plugs into the existing, unmodified app.services.acquisition.AcquisitionService
directly - no change to that generic machinery (docs/architecture/
ai-discovery-candidate-envelope-lifecycle.md S2a: "the provider interface
is already generic").
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from time import perf_counter

import httpx

from app.acquisition.faa import AcquisitionPayload, sanitized_headers
from app.config import settings

PROVIDER_VERSION = "mac-granicus-http/1"
BASE_URL = "https://metroairports.granicus.com"

# Deliberately topical, not vendor-specific (task instruction: must not be
# hardcoded to "find Runway Safe"). Reused identically by the extractor's
# own document-level relevance check
# (app.acquisition.mac_granicus_extractor.RELEVANT_KEYWORDS) so an agenda
# item judged relevant here is judged the same way there. Kept as the full,
# unchanged historical vocabulary (informational/back-compat) - the actual
# matching logic below splits it into the two vocabularies immediately
# following, which are matched differently (see is_relevant_title()).
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

# The four phrases above that are precise enough to match as a standalone
# substring anywhere in the text, with no further structural check needed -
# unchanged behavior from before Controlled Live Pilot 5D.
_STANDALONE_RELEVANCE_PHRASES = (
    "emas",
    "engineered material arresting",
    "arresting system",
    "runway safety area",
)

# 5D fix (Controlled Live Pilot 5C/5D): the five single-word work concepts
# from RELEVANT_KEYWORDS's remaining "runway <concept>" phrases. Matched
# structurally via _has_proximate_runway_work_mention() below, rather than
# as an exact contiguous substring - real MAC agenda titles observed live
# during Pilot 5C consistently place a runway designation (and sometimes a
# short equipment/modifier phrase) between "runway" and the work concept,
# e.g. "Runway 14-32 Reconstruction", "Runway 9-27 Edge Lighting and PAPI
# Replacement" - neither of which contains "runway reconstruction" or
# "runway replacement" as a contiguous substring, so the old exact-phrase
# check missed 100% of 18 real, clearly runway-relevant titles found in a
# 20-meeting/436-item live sample.
_RUNWAY_WORK_CONCEPTS = frozenset({"reconstruction", "rehabilitation", "replacement", "resurfacing", "repair"})

# A runway DESIGNATION token shape - one or two digits, optionally with a
# single L/R/C side letter (e.g. "14", "09", "9", "32", "14l", "9r").
# Structural only - never a specific hardcoded runway number - this is what
# lets "Runway 14-32 Reconstruction" and "Runway 09-27 Replacement" match
# the same rule without hardcoding either designation.
_DESIGNATION_TOKEN = re.compile(r"^\d{1,2}[lrc]?$")

# How many tokens of "designation + short modifier phrase" (e.g.
# "9-27 Edge Lighting and PAPI") are allowed between "runway" and a
# qualifying work concept before the two are considered structurally
# unrelated, rather than the same runway-work mention. Justified directly
# by Controlled Live Pilot 5C's own real-world MAC title corpus: the
# widest real gap observed there ("Runway 9-27 Edge Lighting and PAPI
# Replacement") is 6 intervening tokens; 8 keeps a small, explicit margin
# without becoming an unbounded "runway...anywhere...work-word...anywhere"
# match (see tests/test_mac_granicus_provider.py's boundary test, and the
# adversarial false-positive tests this exact bound is checked against).
_MAX_RUNWAY_WORK_GAP_TOKENS = 8

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _has_proximate_runway_work_mention(tokens: list[str]) -> bool:
    """True if some occurrence of the token "runway" is immediately
    followed by EITHER a runway-work concept word directly (the original,
    unchanged "runway reconstruction"-style exact-adjacency case, at zero
    gap) OR a designation-shaped token (e.g. "14-32", tokenized as "14",
    "32"), with a qualifying work concept then found within
    _MAX_RUNWAY_WORK_GAP_TOKENS tokens after that.

    Requiring the token IMMEDIATELY after "runway" to be either the work
    concept itself or a designation shape - rather than simply searching
    for both words within N tokens of each other anywhere in the text - is
    what keeps this from matching a false positive like "parking-ramp
    reconstruction with an unrelated runway reference elsewhere" (there,
    "runway" is followed by "reference": neither a designation nor a work
    concept). A bounded distance check ALONE would incorrectly match that
    case; this additional adjacency requirement is why it does not."""
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


def is_relevant_title(title: str) -> bool:
    """Coarse, topical relevance judgment on an agenda item's own title -
    the extraction-layer 'is this even worth looking at' decision
    (docs/architecture/ai-discovery-candidate-envelope-lifecycle.md S7),
    kept separate from the guard's airport-identity decision. Deliberately
    keyword/structure-based and inspectable, not AI-scored.

    5D: also recognizes a "runway" mention structurally near (not just
    contiguous with) a runway-work concept - see
    _has_proximate_runway_work_mention()."""
    lowered = title.lower()
    if any(phrase in lowered for phrase in _STANDALONE_RELEVANCE_PHRASES):
        return True
    return _has_proximate_runway_work_mention(_tokenize(title))


class MACGranicusAcquisitionError(ValueError):
    pass


@dataclass(frozen=True)
class MACGranicusMeetingListing:
    """One row from a committee's ViewPublisher.php meeting archive."""

    view_id: int
    clip_id: int
    committee_name: str
    meeting_date_raw: str

    @property
    def agenda_url(self) -> str:
        return f"{BASE_URL}/AgendaViewer.php?view_id={self.view_id}&clip_id={self.clip_id}"


@dataclass(frozen=True)
class MACGranicusAgendaItemCandidate:
    """One itemized agenda entry with its own linked document - the
    fragment-discovery unit this source family naturally exposes. Document
    identity (`document_url`) is derived entirely from the archive's own
    stable clip_id/meta_id addressing, never from a search query."""

    view_id: int
    clip_id: int
    meta_id: str
    item_number: str
    item_title: str
    document_url: str
    committee_name: str
    meeting_date_raw: str

    @property
    def is_relevant(self) -> bool:
        return is_relevant_title(self.item_title)

    @property
    def acquisition_source_key(self) -> str:
        """Deterministic, URL-derived - never derived from item_title or
        any search context (docs/architecture/ai-discovery-evidence-
        attachment-guard.md S2, S23 invariant, carried through here)."""
        return f"mac.granicus.document.{self.view_id}.{self.clip_id}.{self.meta_id}"


_MEETING_ROW = re.compile(
    r'id="(?P<committee_id>[^"]+)"\s+scope="row">\s*'
    r'(?P<committee_name>[^<]+?)\s*</td>\s*'
    r'<td class="listItem" headers="Date [^"]+">(?P<date>[^<]+)</td>',
    re.DOTALL,
)
_CLIP_ID_NEAR_ROW = re.compile(r"AgendaViewer\.php\?view_id=(?P<view_id>\d+)&clip_id=(?P<clip_id>\d+)")

_AGENDA_ITEM = re.compile(
    r'<td\s*class\s*=\s*"numberspace">(?P<item_number>[0-9.]+)\.?</td>\s*'
    r'<td[^>]*>(?P<item_title>.+?)(?:<blockquote|</td>)',
    re.DOTALL,
)
_META_LINK = re.compile(
    r'MetaViewer\.php\?view_id=(?P<view_id>\d+)&clip_id=(?P<clip_id>\d+)&meta_id=(?P<meta_id>\d+)"'
)
_TAG_STRIP = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", _TAG_STRIP.sub(" ", text)).strip())


def discover_recent_meetings(
    client: httpx.Client, *, view_id: int, max_meetings: int = 5, timeout: float = 30.0,
) -> list[MACGranicusMeetingListing]:
    """Fetches ONE committee's meeting archive listing (a single GET to
    `ViewPublisher.php?view_id=view_id`) and returns its `max_meetings`
    most recent rows. This is the source family's own listing/archive
    mechanism (task instruction: discover, never hardcode a document URL).
    Rows are already in reverse-chronological (most-recent-first) order on
    the page itself - no sorting/pagination needed for a bounded recent
    window.
    """
    response = client.get(f"{BASE_URL}/ViewPublisher.php", params={"view_id": view_id}, timeout=timeout)
    response.raise_for_status()
    html = response.text

    listings: list[MACGranicusMeetingListing] = []
    for match in _MEETING_ROW.finditer(html):
        tail = html[match.end():match.end() + 2000]
        clip_match = _CLIP_ID_NEAR_ROW.search(tail)
        if clip_match is None:
            continue
        listings.append(
            MACGranicusMeetingListing(
                view_id=int(clip_match.group("view_id")),
                clip_id=int(clip_match.group("clip_id")),
                committee_name=_clean(match.group("committee_name")),
                meeting_date_raw=_clean(match.group("date")),
            )
        )
        if len(listings) >= max_meetings:
            break
    return listings


def discover_agenda_items(
    client: httpx.Client, listing: MACGranicusMeetingListing, *, timeout: float = 30.0,
) -> list[MACGranicusAgendaItemCandidate]:
    """Fetches one meeting's itemized agenda (AgendaViewer.php, which
    302-redirects to GeneratedAgendaViewer.php - httpx follows this
    automatically) and returns every numbered item that carries its own
    linked document, regardless of topical relevance (relevance is a
    separate, explicit judgment - `.is_relevant` / `is_relevant_title()` -
    never conflated with discovery itself)."""
    response = client.get(listing.agenda_url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    html = response.text

    items: list[MACGranicusAgendaItemCandidate] = []
    for match in _AGENDA_ITEM.finditer(html):
        window = html[match.end():match.end() + 800]
        meta_match = _META_LINK.search(window)
        if meta_match is None:
            continue
        items.append(
            MACGranicusAgendaItemCandidate(
                view_id=int(meta_match.group("view_id")),
                clip_id=int(meta_match.group("clip_id")),
                meta_id=meta_match.group("meta_id"),
                item_number=match.group("item_number").strip().rstrip("."),
                item_title=_clean(match.group("item_title")),
                document_url=(
                    f"{BASE_URL}/MetaViewer.php?view_id={meta_match.group('view_id')}"
                    f"&clip_id={meta_match.group('clip_id')}&meta_id={meta_match.group('meta_id')}"
                ),
                committee_name=listing.committee_name,
                meeting_date_raw=listing.meeting_date_raw,
            )
        )
    return items


class MACGranicusAcquisitionProvider:
    """Read-only HTTP fetch of ONE already-discovered document URL. Matches
    app.acquisition.faa.FAAAcquisitionProvider's protocol exactly so it can
    be passed directly to the existing, unmodified
    app.services.acquisition.AcquisitionService - no DB write, no
    Source/SourceAssertion logic, no guard logic, no Signal logic lives
    here (task instruction)."""

    version = PROVIDER_VERSION

    def __init__(
        self, source_url: str, *, client: httpx.Client | None = None, timeout_seconds: float | None = None,
    ) -> None:
        self.source_url = source_url
        self.timeout_seconds = timeout_seconds or settings.acquisition_timeout_seconds
        self._client = client

    def retrieve(self) -> AcquisitionPayload:
        started = perf_counter()
        headers = {"User-Agent": settings.acquisition_user_agent}
        if self._client is not None:
            response = self._client.get(
                self.source_url, timeout=self.timeout_seconds, follow_redirects=True, headers=headers,
            )
        else:
            with httpx.Client(follow_redirects=True) as client:
                response = client.get(self.source_url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()
        content = response.content
        if not content:
            raise MACGranicusAcquisitionError("MAC Granicus acquisition returned an empty payload")
        return AcquisitionPayload(
            content=content,
            request_url=self.source_url,
            final_url=str(response.url),
            retrieved_headers=sanitized_headers(response),
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            duration_seconds=perf_counter() - started,
            provider_version=self.version,
        )
