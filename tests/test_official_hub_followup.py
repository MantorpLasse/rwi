"""RWI HQ "Bounded Official-Hub Follow-Up Discovery" mission - offline
tests for app.services.official_hub_followup. No real network access
anywhere in this file - fake httpx-shaped client objects only, mirroring
tests/test_generic_web_provider.py's own _FakeStreamClient/
_FakeStreamResponse convention (independent copy, not imported - this
repo's own established "independent copy, same reasoning" discipline)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.discovery.dedup import DedupedResult
from app.discovery.query import SearchQuery
from app.discovery.search import SearchResult
from app.discovery.triage import PriorityBand, TriagedResult
from app.services.official_hub_followup import (
    MAX_HUB_FETCHES,
    MAX_LINKS_PER_HUB,
    ExtractedHubLink,
    HubFollowUpOutcome,
    discover_official_hub_followups,
    find_eligible_hub_candidates,
)

_NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
_DOMAIN = "flylouisville.com"


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """Mission's own explicit "NO live network dependency in unit tests"
    requirement: every hostname used anywhere in this file (flylouisville.com,
    external-domain.example.com, ...) resolves through this fake, offline
    getaddrinfo - never a real DNS lookup - matching
    tests/test_generic_web_provider.py's own established convention."""
    import socket

    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# --- Fakes: no real network --------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, status_code, *, headers=None, content=b"", url="https://flylouisville.com/hub"):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._content = content
        self.url = httpx.URL(url)

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=httpx.Request("GET", str(self.url)), response=self)

    def iter_bytes(self):
        chunk = 4096
        for i in range(0, len(self._content), chunk):
            yield self._content[i : i + chunk]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeGetResponse:
    def __init__(self, status_code=404, text=""):
        self.status_code = status_code
        self.text = text


class _FakeHubClient:
    """Supports BOTH .stream() (the hub-page fetch itself) and .get()
    (app.services.generic_web_fetch.check_robots_txt_allows's own robots.txt
    check) - both are called with the SAME injected client object."""

    def __init__(self, *, stream_responses=None, get_response=None, get_exc=None):
        self._stream_responses = list(stream_responses or [])
        self._get_response = get_response or _FakeGetResponse(404)  # 404 -> robots absent -> default allow
        self._get_exc = get_exc
        self.stream_calls = []
        self.get_calls = []
        self.closed = False

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        return self._stream_responses.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self._get_exc is not None:
            raise self._get_exc
        return self._get_response

    def close(self):
        self.closed = True


def _html_response(url, html, *, status_code=200):
    return _FakeStreamResponse(status_code, headers={"content-type": "text/html; charset=utf-8"}, content=html.encode("utf-8"), url=url)


def _pdf_response(url, *, status_code=200):
    return _FakeStreamResponse(status_code, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4 fake", url=url)


def _redirect_response(location, *, url="https://flylouisville.com/hub"):
    return _FakeStreamResponse(302, headers={"location": location}, url=url)


def _query(rendered="hub query"):
    return SearchQuery(rendered=rendered, template_id="t", identity_field="name", identity_value=rendered)


def _search_result(*, url, title="A hub page", rank=1, query=None):
    return SearchResult(query=query or _query(url), rank=rank, title=title, url=url, snippet="", discovered_at=_NOW, provider="fake")


def _triaged(*, url, band, title="A hub page"):
    result = _search_result(url=url, title=title)
    deduped = DedupedResult(result=result, found_by=(result.query,))
    return TriagedResult(deduped=deduped, band=band, reasons=("test",), domain_category=None)


_HUB_HTML = """
<html><body>
<a href="/wp-content/uploads/2026/02/February-18-2026-LRAA-Meeting_APPROVED.pdf">Feb 18 2026 minutes</a>
<a href="/wp-content/uploads/2026/08/LRAA-July-15-2026-Regular-Meeting_UNAPPROVED-DRAFT.pdf">Jul 15 2026 minutes</a>
<a href="/we-love-airplanes-marketing-page/">Fun airplane facts</a>
<a href="https://external-domain.example.com/some-other-board-minutes.pdf">Off-domain PDF</a>
</body></html>
"""


# --- 1/2. Eligibility -------------------------------------------------------


def test_eligible_hub_requires_official_domain_band_and_hub_shape():
    eligible_hub = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.MEDIUM)
    wrong_domain = _triaged(url="https://faa.gov/board-minutes/", band=PriorityBand.MEDIUM)
    low_band = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.LOW)
    already_document = _triaged(url="https://flylouisville.com/wp-content/uploads/x.pdf", band=PriorityBand.HIGH)
    no_hub_keyword = _triaged(url="https://flylouisville.com/amenities/", band=PriorityBand.MEDIUM)

    eligible = find_eligible_hub_candidates(
        [eligible_hub, wrong_domain, low_band, already_document, no_hub_keyword], _DOMAIN,
    )
    assert eligible == [eligible_hub]


def test_eligibility_preserves_existing_triage_order_and_caps_at_max_hub_fetches():
    candidates = [
        _triaged(url=f"https://flylouisville.com/board-meeting-{i}/", band=PriorityBand.MEDIUM)
        for i in range(5)
    ]
    eligible = find_eligible_hub_candidates(candidates, _DOMAIN)
    assert eligible == candidates[:MAX_HUB_FETCHES]
    assert len(eligible) == 2


def test_no_eligible_hub_returns_empty_list():
    only_low = [_triaged(url="https://flylouisville.com/board-meeting/", band=PriorityBand.LOW)]
    assert find_eligible_hub_candidates(only_low, _DOMAIN) == []


# --- Fixture 1: mixed link shapes on one real-shaped hub page ---------------


def test_fixture1_official_pdfs_kept_marketing_and_offdomain_rejected():
    hub = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response("https://flylouisville.com/corporate/lraa-board-meeting-minutes/", _HUB_HTML)])

    results, outcomes = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)

    urls = {r.url for r in results}
    assert "https://flylouisville.com/wp-content/uploads/2026/02/February-18-2026-LRAA-Meeting_APPROVED.pdf" in urls
    assert "https://flylouisville.com/wp-content/uploads/2026/08/LRAA-July-15-2026-Regular-Meeting_UNAPPROVED-DRAFT.pdf" in urls
    assert "https://external-domain.example.com/some-other-board-minutes.pdf" not in urls
    assert "https://flylouisville.com/we-love-airplanes-marketing-page/" not in urls  # no hub/document shape
    assert len(outcomes) == 1
    assert outcomes[0].fetched is True
    assert outcomes[0].candidate_count == len(results)
    assert client.closed is False or client.closed is True  # caller-owned client: never asserted closed by us


def test_fixture1_no_recursive_fetch_only_one_stream_call():
    hub = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response("https://flylouisville.com/corporate/lraa-board-meeting-minutes/", _HUB_HTML)])
    discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)
    assert len(client.stream_calls) == 1  # the hub page itself only - extracted PDFs are never fetched


# --- Fixture 2: hard cap on extracted links ---------------------------------


def test_fixture2_fifty_links_capped_at_max_links_per_hub():
    links_html = "".join(f'<a href="/wp-content/uploads/doc-{i}.pdf">Doc {i}</a>' for i in range(50))
    hub_html = f"<html><body>{links_html}</body></html>"
    hub = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response("https://flylouisville.com/corporate/lraa-board-meeting-minutes/", hub_html)])

    results, outcomes = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)
    assert len(results) == MAX_LINKS_PER_HUB == 20
    assert outcomes[0].candidate_count == 20


# --- Fixture 3: off-domain final redirect -----------------------------------


def test_fixture3_offdomain_redirect_fails_closed_zero_candidates():
    hub = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.MEDIUM)
    # the hub URL itself redirects off-domain
    client = _FakeHubClient(
        stream_responses=[
            _redirect_response("https://external-domain.example.com/moved-hub/", url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/"),
            _html_response("https://external-domain.example.com/moved-hub/", _HUB_HTML),
        ]
    )
    results, outcomes = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)
    assert results == []
    assert outcomes[0].fetched is True
    assert outcomes[0].candidate_count == 0
    assert "off" in outcomes[0].reason.lower() or "domain" in outcomes[0].reason.lower()


# --- Fixture 4: binary/non-HTML response ------------------------------------


def test_fixture4_pdf_response_not_parsed_as_hub():
    hub = _triaged(url="https://flylouisville.com/corporate/lraa-board-meeting-minutes/", band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_pdf_response("https://flylouisville.com/corporate/lraa-board-meeting-minutes/")])
    results, outcomes = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)
    assert results == []
    assert outcomes[0].fetched is False
    assert len(client.stream_calls) == 1  # no second attempt, no crash


# --- Fixture 5: no eligible hub ----------------------------------------------


def test_fixture5_no_eligible_hub_produces_empty_output_no_network():
    only_low = [_triaged(url="https://flylouisville.com/board-meeting/", band=PriorityBand.LOW)]
    client = _FakeHubClient()
    results, outcomes = discover_official_hub_followups(only_low, _DOMAIN, client=client, now=_NOW)
    assert results == []
    assert outcomes == ()
    assert client.stream_calls == []  # zero fetch attempts - never even touches the network


# --- Provenance / representation --------------------------------------------


def test_extracted_candidates_are_ordinary_search_results_with_expected_provenance():
    hub_url = "https://flylouisville.com/corporate/lraa-board-meeting-minutes/"
    hub = _triaged(url=hub_url, band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response(hub_url, _HUB_HTML)])
    results, _outcomes = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)

    assert results, "expected at least one extracted candidate"
    r = results[0]
    assert isinstance(r, SearchResult)
    assert r.provider == "official_hub_followup"
    assert r.query.template_id == "official_hub_followup"
    assert r.query.identity_field == "official_hub_followup"
    assert r.query.identity_value == hub_url  # origin hub fully reconstructable
    assert hub_url in r.query.rendered
    assert r.title  # anchor text preserved (non-empty for our fixture's real anchors)


def test_anchor_text_preserved_and_fallback_title_used_when_absent():
    html = '<html><body><a href="/wp-content/uploads/named.pdf">Named Document</a><a href="/wp-content/uploads/unnamed.pdf"></a></body></html>'
    hub_url = "https://flylouisville.com/corporate/lraa-board-meeting-minutes/"
    hub = _triaged(url=hub_url, band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response(hub_url, html)])
    results, _ = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)

    by_url = {r.url: r for r in results}
    named = by_url["https://flylouisville.com/wp-content/uploads/named.pdf"]
    assert named.title == "Named Document"
    unnamed = by_url["https://flylouisville.com/wp-content/uploads/unnamed.pdf"]
    assert unnamed.title == "unnamed.pdf"  # fallback: last URL path segment


# --- Extracted candidates flow through normal dedup/triage unmodified -------


def test_extracted_candidates_are_regular_search_results_compatible_with_dedup(monkeypatch=None):
    from app.discovery.dedup import deduplicate_results
    from app.discovery.triage import triage_results

    hub_url = "https://flylouisville.com/corporate/lraa-board-meeting-minutes/"
    hub = _triaged(url=hub_url, band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response(hub_url, _HUB_HTML)])
    results, _ = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)

    deduped = deduplicate_results(results)
    triaged = triage_results(deduped, official_domains=frozenset({_DOMAIN}))
    assert len(triaged) == len(deduped) > 0
    for t in triaged:
        assert "Known official domain" in t.reasons


def test_extracted_pdf_candidate_never_reaches_high_without_strong_title_term():
    """HIGH-band invariant unchanged by this mission: a follow-up PDF link
    whose anchor text has no STRONG concept term stays MEDIUM at most,
    exactly like any other official-domain-only result."""
    from app.discovery.dedup import deduplicate_results
    from app.discovery.triage import PriorityBand, triage_results

    hub_url = "https://flylouisville.com/corporate/lraa-board-meeting-minutes/"
    hub = _triaged(url=hub_url, band=PriorityBand.MEDIUM)
    client = _FakeHubClient(stream_responses=[_html_response(hub_url, _HUB_HTML)])
    results, _ = discover_official_hub_followups([hub], _DOMAIN, client=client, now=_NOW)

    triaged = triage_results(deduplicate_results(results), official_domains=frozenset({_DOMAIN}))
    assert all(t.band != PriorityBand.HIGH for t in triaged)


# --- No persistence, no crawler behavior (authoritative checks live in
# tests/test_official_hub_followup_architectural_safety.py) -----------------


def test_max_hub_fetches_is_two_and_max_links_per_hub_is_twenty():
    assert MAX_HUB_FETCHES == 2
    assert MAX_LINKS_PER_HUB == 20
