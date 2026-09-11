"""Bounded Official-Hub Follow-Up Discovery (RWI HQ "Bounded Official-Hub
Follow-Up Discovery" mission, following that mission's own design recon,
"Bounded Official-Document Follow-Up Discovery").

    already-triaged discovery candidates + one governed official domain
        -> find_eligible_hub_candidates() (pure, no network)
        -> _fetch_hub_page_html() (real, bounded, SSRF-safe GET; at most
           MAX_HUB_FETCHES times)
        -> _extract_same_domain_links() (stdlib HTMLParser only)
        -> _filter_and_rank_links() (document-shaped links prioritized,
           hard-capped)
        -> ordinary SearchResult objects, indistinguishable in shape from
           any Brave-discovered result
        -> STOP

THIS IS NOT A CRAWLER: depth is always exactly 1 (extracted links are
never themselves fetched in the same run - see discover_official_hub_
followups()'s own docstring), at most MAX_HUB_FETCHES pages are ever
fetched per call, only the SAME already-governed official domain is ever
followed or accepted as a link target, and nothing here ever re-invokes
itself. No sitemap fetching, no JS execution (stdlib html.parser.HTMLParser
never executes anything), no browser automation, no recursive graph
traversal.

FETCH != EVIDENCE, exactly like app.acquisition.generic_web's own module
docstring establishes for the identical reason: this module preserves
nothing and persists nothing. It calls no Session, no AcquisitionService,
no Snapshot/Source/SourceAssertion/Signal/Airport/ReviewerAction/
publication code of any kind (enforced by
tests/test_official_hub_followup_architectural_safety.py, by AST
inspection, not just convention) - a followed hub page's bytes live only
in this function call's own local variables and are discarded the moment
it returns. Every artifact this module produces is an ordinary,
non-persisted, non-evidentiary `SearchResult` - the exact same runtime
type every Brave query result already is - so it flows into the existing
deduplicate_results()/triage_results() pipeline with zero special-casing
anywhere else in Discovery.

WHY THIS MODULE LIVES IN app/services/, NOT app/discovery/ (a deliberate,
documented deviation from this mission's own suggested file path): the
app/discovery/ package's own architectural-safety test
(tests/test_discovery_architectural_safety.py) enforces that package's
long-standing "upstream and read-only... never imports a database
Session/engine constructor" purity guarantee by inspecting each file's
own literal import statements - a real network-fetch module placed there
would technically slip past that check's specific named-substring list
(it doesn't ban "app.acquisition" or "httpx" by name) while still
violating its actual intent: a module that performs a real, bounded HTTP
GET is categorically different from every existing pure app/discovery/*.py
file, which the module docstrings there repeatedly emphasize perform
"zero network access" themselves (delegating all of it to an injected
SearchProvider). Placing this module at the SAME tier as
app.services.generic_web_fetch (which also legitimately needs the real,
safe HTTP transport) - never inside app/discovery/ - keeps that
package's own guarantee genuinely true, not merely test-passing.

REUSES, NEVER FORKS, the existing safe transport
(app.acquisition.generic_web.validate_fetch_target /
app.acquisition.generic_web.build_safe_client - the same SSRF/DNS-
rebinding-safe, scheme-allowlisted primitives GenericWebAcquisitionProvider
itself is built from) and the existing robots.txt check
(app.services.generic_web_fetch.check_robots_txt_allows, a pure network
check with no persistence of its own). This module does NOT call
GenericWebAcquisitionProvider.retrieve() itself: that class enforces the
SHARED, larger MAX_RESPONSE_BYTES (25 MiB) cap only after buffering the
whole capped response, which is looser than a hub PAGE genuinely needs;
this module instead builds directly on the same public safe-client
primitives with its OWN tighter, truly-streaming MAX_HUB_PAGE_BYTES
abort, so an oversized hub response is aborted mid-transfer, never fully
downloaded first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.acquisition.generic_web import UnsafeFetchTargetError, build_safe_client, validate_fetch_target
from app.config import settings
from app.discovery.dedup import normalize_url
from app.discovery.query import SearchQuery
from app.discovery.search import SearchResult
from app.discovery.triage import PriorityBand, TriagedResult
from app.services.generic_web_fetch import check_robots_txt_allows

__all__ = [
    "MAX_HUB_FETCHES",
    "MAX_LINKS_PER_HUB",
    "MAX_HUB_PAGE_BYTES",
    "MAX_DEPTH",
    "ExtractedHubLink",
    "HubFollowUpOutcome",
    "find_eligible_hub_candidates",
    "discover_official_hub_followups",
]

# --- Hard budgets (RWI HQ mission's own explicit constants) -----------------

MAX_HUB_FETCHES = 2
MAX_LINKS_PER_HUB = 20
MAX_HUB_PAGE_BYTES = 5 * 1024 * 1024  # 5 MiB - tighter than the shared 25 MiB acquisition cap
MAX_DEPTH = 1  # a documented design invariant, not a runtime loop counter - this
# module contains no code path that re-fetches an extracted candidate; "depth"
# never advances past 1 because there is no second fetch round at all.

_HUB_FETCH_TIMEOUT_SECONDS = 15.0
_HUB_MAX_REDIRECT_HOPS = 5
_HUB_REQUEST_HEADERS = {"User-Agent": settings.acquisition_user_agent}

# --- Hub eligibility vocabulary (mission's own explicit keyword list) -------

_HUB_PATH_KEYWORDS = (
    "board", "meeting", "minutes", "agenda", "documents", "resources",
    "capital", "projects", "bids", "proposals",
)
_DOCUMENT_PATH_MARKERS = (".pdf", "/wp-content/uploads/", "/documents/", "/downloads/")
_REJECTED_LINK_SCHEMES = ("mailto:", "javascript:", "tel:")


def _hostname_of(url: str) -> str:
    return urlsplit(url).netloc.lower().split(":")[0]


def _matches_domain(hostname: str, official_domain: str) -> bool:
    domain = official_domain.strip().lower()
    return hostname == domain or hostname.endswith("." + domain)


def _is_document_shaped(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(".pdf") or any(marker in lowered for marker in _DOCUMENT_PATH_MARKERS)


def _is_hub_shaped(path: str) -> bool:
    lowered = path.lower()
    return any(keyword in lowered for keyword in _HUB_PATH_KEYWORDS)


def find_eligible_hub_candidates(
    triaged: "list[TriagedResult]", official_domain: str,
) -> "list[TriagedResult]":
    """Pure, no network. Returns, in the SAME order triage_results() already
    produced (never re-sorted), at most MAX_HUB_FETCHES candidates that
    satisfy every eligibility rule (mission Part 2):

      - hostname == the selected governed official domain
      - triage band is HIGH or MEDIUM (LOW excluded)
      - URL path is NOT already document-shaped (those are already
        document candidates - no need to open them as a hub)
      - URL path contains at least one hub/navigation keyword

    Returns an empty list (never raises) when nothing qualifies - the
    normal, expected outcome for most Research Loop runs.
    """
    eligible: "list[TriagedResult]" = []
    for candidate in triaged:
        if candidate.band not in (PriorityBand.HIGH, PriorityBand.MEDIUM):
            continue
        url = candidate.deduped.result.url
        if not _matches_domain(_hostname_of(url), official_domain):
            continue
        path = urlsplit(url).path
        if _is_document_shaped(path):
            continue
        if not _is_hub_shaped(path):
            continue
        eligible.append(candidate)
        if len(eligible) >= MAX_HUB_FETCHES:
            break
    return eligible


@dataclass(frozen=True)
class ExtractedHubLink:
    """One in-memory, non-persisted link found on a followed hub page.
    Never leaves this module in this shape - discover_official_hub_
    followups() converts each surviving one into an ordinary SearchResult
    before returning (see that function's own docstring)."""

    url: str
    anchor_text: "str | None"


class _AnchorLinkParser(HTMLParser):
    """Stdlib-only <a href="..."> extractor (matches
    app.extraction.generic_html's own "Python stdlib html.parser.HTMLParser
    only" convention/precedent - independent copy, not imported, since
    that module solves a different problem: reconstructing a document's
    full text from an already-preserved Snapshot, not scanning for
    hyperlinks in an unpersisted page). Never executes JavaScript, never
    loads external resources, never follows a link itself - it only
    records what `feed()` is handed, once."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: "list[tuple[str, str]]" = []
        self._current_href: "str | None" = None
        self._current_text_parts: "list[str]" = []

    def handle_starttag(self, tag: str, attrs: "list[tuple[str, str | None]]") -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._current_href = href
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        text = "".join(self._current_text_parts).strip()
        self.links.append((self._current_href, text))
        self._current_href = None
        self._current_text_parts = []


def _extract_same_domain_links(
    html_text: str, *, base_url: str, official_domain: str,
) -> "list[ExtractedHubLink]":
    """Pure, no network. Parses `html_text` with the stdlib-only parser
    above, resolves every href against `base_url` (the hub page's own
    FINAL url, post-redirect), and keeps only http(s) links whose
    hostname matches `official_domain` - external-domain, mailto:,
    javascript:, tel:, and fragment-only links are all dropped here,
    before any filtering/ranking. Deduplicates via the same
    app.discovery.dedup.normalize_url() every other Discovery URL
    comparison already uses. A malformed/unparseable HTML fragment
    produces an empty list, never an exception (HTMLParser.feed() itself
    is tolerant of malformed markup by design)."""
    parser = _AnchorLinkParser()
    try:
        parser.feed(html_text)
    except Exception:
        return []

    seen: "set[str]" = set()
    results: "list[ExtractedHubLink]" = []
    for href, text in parser.links:
        href = (href or "").strip()
        if not href or href.startswith("#"):
            continue
        if href.lower().startswith(_REJECTED_LINK_SCHEMES):
            continue
        resolved = urljoin(base_url, href)
        parts = urlsplit(resolved)
        if parts.scheme not in ("http", "https"):
            continue
        if not _matches_domain(_hostname_of(resolved), official_domain):
            continue
        key = normalize_url(resolved)
        if key in seen:
            continue
        seen.add(key)
        results.append(ExtractedHubLink(url=resolved, anchor_text=text or None))
    return results


def _filter_and_rank_links(links: "list[ExtractedHubLink]") -> "list[ExtractedHubLink]":
    """Pure. Document-shaped links (.pdf, /wp-content/uploads/,
    /documents/, /downloads/) are prioritized first, in original
    document order; same-domain HTML pages are kept only when their own
    path is ALSO hub/document-shaped (mission Part 6: "permit same-domain
    HTML links only when their path is strongly hub/document shaped");
    a plain marketing/navigation link with neither shape is rejected
    outright. The combined, ordered list is hard-capped at
    MAX_LINKS_PER_HUB."""
    document_shaped = [link for link in links if _is_document_shaped(urlsplit(link.url).path)]
    document_urls = {link.url for link in document_shaped}
    hub_shaped_html = [
        link for link in links
        if link.url not in document_urls and _is_hub_shaped(urlsplit(link.url).path)
    ]
    return (document_shaped + hub_shaped_html)[:MAX_LINKS_PER_HUB]


@dataclass(frozen=True)
class HubFetchResult:
    """Internal only - the safe-fetch step's own output before parsing."""

    final_url: str
    content_type: "str | None"
    text: str


def _fetch_hub_page_html(
    hub_url: str, *, client: "httpx.Client | None" = None,
) -> "HubFetchResult | None":
    """Safe, read-only, bounded fetch of ONE candidate hub page. Returns
    None (NEVER raises) for every ineligible/unsafe/oversized/non-HTML
    outcome - a normal, expected, fail-closed result, never an error that
    could abort the whole Research Loop. Reuses
    app.acquisition.generic_web.validate_fetch_target()/build_safe_client()
    directly - the same SSRF/DNS-rebinding-safe transport every other
    fetch in this repository uses - but enforces its OWN tighter,
    truly-streaming MAX_HUB_PAGE_BYTES cap (module docstring explains why
    this is not simply GenericWebAcquisitionProvider.retrieve()).

    GET only, no auth/cookies (the same fixed, honest User-Agent every
    other fetch in this repository sends), manual one-hop-at-a-time
    redirect following with the SAME safety re-validation on every hop,
    HTML-only accepted (any other content-type is rejected without
    attempting to parse it), 4xx/5xx rejected. Never calls
    AcquisitionService, never opens a Session, never creates a Snapshot -
    the returned text lives only in the caller's local variables.
    """
    try:
        validate_fetch_target(hub_url)
    except UnsafeFetchTargetError:
        return None

    owns_client = client is None
    active_client = client or build_safe_client()
    try:
        url = hub_url
        hops = 0
        while True:
            try:
                with active_client.stream(
                    "GET", url, timeout=_HUB_FETCH_TIMEOUT_SECONDS,
                    headers=_HUB_REQUEST_HEADERS, follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        hops += 1
                        if hops > _HUB_MAX_REDIRECT_HOPS:
                            return None
                        location = response.headers.get("location")
                        if not location:
                            return None
                        next_url = urljoin(str(response.url), location)
                        try:
                            validate_fetch_target(next_url)
                        except UnsafeFetchTargetError:
                            return None
                        url = next_url
                        continue

                    if response.status_code >= 400:
                        return None
                    content_type = response.headers.get("content-type") or ""
                    if "html" not in content_type.lower():
                        return None

                    total = 0
                    chunks: "list[bytes]" = []
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_HUB_PAGE_BYTES:
                            return None
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if not body:
                        return None
                    return HubFetchResult(
                        final_url=str(response.url), content_type=content_type,
                        text=body.decode("utf-8", errors="replace"),
                    )
            except httpx.HTTPError:
                return None
    finally:
        if owns_client:
            active_client.close()


def _search_result_from_link(
    link: "ExtractedHubLink", *, origin_hub_url: str, rank: int, discovered_at: "datetime",
) -> SearchResult:
    """Converts one ExtractedHubLink into an ORDINARY SearchResult -
    mission Part 7's own required provenance shape, reusing the existing
    SearchQuery type exactly as
    app.discovery.query.plan_official_domain_document_queries() already
    does for its own, differently-shaped non-search-term provenance need.
    `provider="official_hub_followup"` honestly distinguishes this
    candidate's origin from a real Brave search hit; `rendered`/
    `identity_value` make the origin hub URL fully reconstructable
    through the exact same "why does this candidate exist" mechanism
    every other query already uses."""
    title = link.anchor_text or (urlsplit(link.url).path.rstrip("/").rsplit("/", 1)[-1] or link.url)
    query = SearchQuery(
        rendered=f"(follow-up link from {origin_hub_url})",
        template_id="official_hub_followup",
        identity_field="official_hub_followup",
        identity_value=origin_hub_url,
    )
    return SearchResult(
        query=query, rank=rank, title=title, url=link.url, snippet="",
        discovered_at=discovered_at, provider="official_hub_followup",
    )


@dataclass(frozen=True)
class HubFollowUpOutcome:
    """Diagnostic-only record of one hub-fetch attempt - never persisted,
    never evidence, purely for CLI/report explainability (mirrors this
    codebase's own established "why did this happen" discipline)."""

    hub_url: str
    fetched: bool
    candidate_count: int
    reason: "str | None" = None


def discover_official_hub_followups(
    triaged: "list[TriagedResult]", official_domain: str, *,
    client: "httpx.Client | None" = None, now: "datetime | None" = None,
) -> "tuple[list[SearchResult], tuple[HubFollowUpOutcome, ...]]":
    """The single entry point (called by
    app.services.research_loop.run_research_loop() when
    `follow_up_official_hubs=True`). DEPTH IS ALWAYS EXACTLY 1: this
    function fetches at most MAX_HUB_FETCHES already-discovered,
    already-triaged candidates; the links extracted FROM those pages are
    returned as new SearchResult candidates and are never themselves
    fetched by this same call - there is no loop, no recursion, no second
    round anywhere in this function.

    Order: find_eligible_hub_candidates() (pure) -> for each, best-effort
    robots.txt check (app.services.generic_web_fetch.check_robots_txt_allows,
    a pure network check, no persistence) -> _fetch_hub_page_html() (safe,
    bounded) -> if the fetch's own final_url no longer matches
    official_domain (an off-domain redirect), FAIL CLOSED for that hub
    (zero candidates from it, recorded as a HubFollowUpOutcome with a
    reason) -> _extract_same_domain_links() -> _filter_and_rank_links()
    -> one SearchResult per surviving link.

    Never raises for a normal ineligible/ifetch-failed outcome - every
    failure mode degrades to "zero candidates from this hub," recorded
    honestly in the returned HubFollowUpOutcome tuple, exactly like
    SearchOutcomeStatus.NO_RESULTS already means "found nothing" rather
    than "something went wrong" elsewhere in Discovery.
    """
    now = now or datetime.now(timezone.utc)
    eligible = find_eligible_hub_candidates(triaged, official_domain)

    all_results: "list[SearchResult]" = []
    outcomes: "list[HubFollowUpOutcome]" = []

    for candidate in eligible:
        hub_url = candidate.deduped.result.url

        try:
            allowed = check_robots_txt_allows(hub_url, user_agent=settings.acquisition_user_agent, client=client)
        except Exception:
            allowed = True  # best-effort, matches check_robots_txt_allows()'s own default-allow discipline
        if not allowed:
            outcomes.append(HubFollowUpOutcome(hub_url=hub_url, fetched=False, candidate_count=0, reason="robots.txt disallows fetching this hub"))
            continue

        fetched = _fetch_hub_page_html(hub_url, client=client)
        if fetched is None:
            outcomes.append(HubFollowUpOutcome(hub_url=hub_url, fetched=False, candidate_count=0, reason="fetch failed, unsafe target, oversized, or non-HTML response"))
            continue

        if not _matches_domain(_hostname_of(fetched.final_url), official_domain):
            outcomes.append(HubFollowUpOutcome(hub_url=hub_url, fetched=True, candidate_count=0, reason="final URL redirected off the official domain - failed closed"))
            continue

        links = _extract_same_domain_links(fetched.text, base_url=fetched.final_url, official_domain=official_domain)
        links = _filter_and_rank_links(links)
        results = [
            _search_result_from_link(link, origin_hub_url=hub_url, rank=rank, discovered_at=now)
            for rank, link in enumerate(links, start=1)
        ]
        all_results.extend(results)
        outcomes.append(HubFollowUpOutcome(hub_url=hub_url, fetched=True, candidate_count=len(results)))

    return all_results, tuple(outcomes)
