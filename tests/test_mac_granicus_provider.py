"""Tests for app/acquisition/mac_granicus.py
(docs/product/msp-authoritative-discovery-provider-pilot.md).

No live network - every HTTP call is mocked via httpx.MockTransport, using
small synthetic HTML snippets that mirror the REAL structural shape
confirmed by direct inspection of metroairports.granicus.com during this
pilot's research (ViewPublisher.php row shape, the AgendaViewer.php ->
GeneratedAgendaViewer.php 302 redirect, the numbered-item/MetaViewer.php
link shape) - not the full multi-megabyte real pages, which would bloat
the test suite without adding coverage the small snippets don't already
provide.
"""
from __future__ import annotations

import inspect

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.acquisition.mac_granicus import (
    BASE_URL,
    MACGranicusAcquisitionProvider,
    MACGranicusMeetingListing,
    discover_agenda_items,
    discover_recent_meetings,
    is_relevant_title,
)
from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot
from app.services.acquisition import AcquisitionService

MEETING_LIST_HTML = """
<table>
<tr><td class="listItem" headers="Name" id="Planning,-Development-and-Environment-Committee" scope="row">
Planning, Development and Environment Committee
</td>
<td class="listItem" headers="Date Planning,-Development-and-Environment-Committee">Sep  3, 2024</td>
<td class="listItem"> 00h&nbsp;49m</td>
<td class="listItem"><a href="//metroairports.granicus.com/AgendaViewer.php?view_id=4&clip_id=2349" target="_blank">Agenda</a></td>
</tr>
<tr><td class="listItem" headers="Name" id="Operations,-Finance-and-Administration-Committee" scope="row">
Operations, Finance and Administration Committee
</td>
<td class="listItem" headers="Date Operations,-Finance-and-Administration-Committee">Sep  3, 2024</td>
<td class="listItem"> 01h&nbsp;03m</td>
<td class="listItem"><a href="//metroairports.granicus.com/AgendaViewer.php?view_id=4&clip_id=2350" target="_blank">Agenda</a></td>
</tr>
</table>
"""

AGENDA_HTML = """
<html><body>
<table>
<tr><td class = "numberspace">2.3.1.</td>
<td valign="top">Reliever Radio Purchase - Troy Tomlinson
<blockquote dir="ltr"><div><a href="https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105404" name="document105404">2.3.1. Reliever Radio Purchase</a></div></blockquote>
</td></tr>
<tr><td class = "numberspace">2.3.2.</td>
<td valign="top">Engineered Material Arresting Systems (EMAS) Procurement Advance Deposit - Angela Enroth
<blockquote dir="ltr"><div><a href="https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105406" name="document105406">2.3.2. EMAS Procurement Advance Deposit</a></div></blockquote>
</td></tr>
<tr><td class = "numberspace">3.1.</td>
<td valign="top">Project Budget Adjustment - 2024 Terminal 1 Gatehold Improvements
<blockquote dir="ltr"><div><a href="https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105409" name="document105409">3.1. Project Budget Adjustment</a></div></blockquote>
</td></tr>
</table>
</body></html>
"""


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# --- 1/2. provider source key determinism / stable document identity ---


def test_agenda_item_source_key_is_deterministic_and_url_derived():
    listing = MACGranicusMeetingListing(view_id=4, clip_id=2349, committee_name="PD&E", meeting_date_raw="Sep 3, 2024")

    def handler(request):
        return httpx.Response(200, text=AGENDA_HTML, request=request)

    items_a = discover_agenda_items(_client(handler), listing)
    items_b = discover_agenda_items(_client(handler), listing)
    relevant_a = next(i for i in items_a if i.is_relevant)
    relevant_b = next(i for i in items_b if i.is_relevant)

    assert relevant_a.acquisition_source_key == relevant_b.acquisition_source_key
    assert relevant_a.acquisition_source_key == "mac.granicus.document.4.2349.105406"
    assert relevant_a.document_url == relevant_b.document_url
    assert relevant_a.document_url.startswith(BASE_URL)


def test_discover_recent_meetings_parses_committee_rows():
    def handler(request):
        return httpx.Response(200, text=MEETING_LIST_HTML, request=request)

    listings = discover_recent_meetings(_client(handler), view_id=4, max_meetings=5)
    assert len(listings) == 2
    assert listings[0].committee_name == "Planning, Development and Environment Committee"
    assert listings[0].clip_id == 2349
    assert listings[0].meeting_date_raw == "Sep 3, 2024"
    assert listings[0].agenda_url == f"{BASE_URL}/AgendaViewer.php?view_id=4&clip_id=2349"


def test_discover_recent_meetings_respects_max_meetings_bound():
    def handler(request):
        return httpx.Response(200, text=MEETING_LIST_HTML, request=request)

    listings = discover_recent_meetings(_client(handler), view_id=4, max_meetings=1)
    assert len(listings) == 1


def test_discover_agenda_items_finds_only_the_genuinely_relevant_item():
    listing = MACGranicusMeetingListing(view_id=4, clip_id=2349, committee_name="PD&E", meeting_date_raw="Sep 3, 2024")

    def handler(request):
        return httpx.Response(200, text=AGENDA_HTML, request=request)

    items = discover_agenda_items(_client(handler), listing)
    relevant = [i for i in items if i.is_relevant]
    assert len(relevant) == 1
    assert "EMAS" in relevant[0].item_title
    assert relevant[0].item_number == "2.3.2"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Engineered Material Arresting Systems (EMAS) Procurement Advance Deposit", True),
        ("Runway Safety Area Improvement - Design Phase", True),
        ("Runway Rehabilitation - Taxiway Reconstruction", True),
        ("Reliever Radio Purchase", False),
        ("2024 Restroom Upgrade Program Phase 2", False),
        ("Preliminary 2025-2031 Capital Improvement Program", False),
        ("Runway Safe EMAS bed replacement", True),  # matches on topical "EMAS" term, not the vendor name itself
        ("Runway Safe holiday party sponsorship", False),  # vendor name alone, no topical term - correctly NOT flagged
    ],
)
def test_is_relevant_title_uses_topical_not_vendor_keywords(title, expected):
    assert is_relevant_title(title) is expected


# --- 14. search-query firewall ---


def test_discovery_functions_accept_no_search_query_parameter():
    """Structural proof, not just behavioral: neither discovery function's
    signature has any search-query-shaped parameter at all - discovery is
    driven entirely by the archive's own committee/meeting addressing
    (view_id/clip_id), never by search context (docs/architecture/
    ai-discovery-evidence-attachment-guard.md S2/S23 invariant)."""
    for func in (discover_recent_meetings, discover_agenda_items):
        params = set(inspect.signature(func).parameters)
        assert not any("query" in p or "search" in p for p in params)


def test_acquisition_source_key_has_no_search_context_component():
    listing = MACGranicusMeetingListing(view_id=4, clip_id=2349, committee_name="PD&E", meeting_date_raw="Sep 3, 2024")

    def handler(request):
        return httpx.Response(200, text=AGENDA_HTML, request=request)

    relevant = next(i for i in discover_agenda_items(_client(handler), listing) if i.is_relevant)
    # The key is built purely from view_id/clip_id/meta_id - none of which
    # vary with whatever search/seed-airport context an orchestration loop
    # might supply.
    assert relevant.acquisition_source_key == f"mac.granicus.document.{relevant.view_id}.{relevant.clip_id}.{relevant.meta_id}"


# --- 20. provider performs no DB write itself ---


def test_provider_module_imports_no_database_layer():
    import app.acquisition.mac_granicus as module

    source = inspect.getsource(module)
    assert "SessionLocal" not in source
    assert "app.database" not in source
    assert "app.models" not in source


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _acquisition_source(session: Session, url: str) -> AcquisitionSource:
    publisher = PublishingSource(name="Metropolitan Airports Commission", homepage_url="https://metroairports.org")
    source = AcquisitionSource(
        publishing_source=publisher,
        key="mac.granicus.document.4.2349.105406",
        display_name="MAC EMAS procurement memo",
        acquisition_type="http",
        canonical_url=url,
        active=True,
    )
    session.add(source)
    session.commit()
    return source


def test_provider_plugs_into_existing_unmodified_acquisition_service(session):
    """Proves MACGranicusAcquisitionProvider matches FAAAcquisitionProvider's
    protocol closely enough to be used with the SAME, unmodified
    AcquisitionService - no subclassing, no service change."""
    url = "https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105406"
    source = _acquisition_source(session, url)

    def handler(request):
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes", headers={"Content-Type": "application/pdf"}, request=request)

    provider = MACGranicusAcquisitionProvider(url, client=_client(handler), timeout_seconds=1)
    run = AcquisitionService(session, provider).acquire(source)

    assert run.status is AcquisitionRunStatus.SUCCESS
    assert run.snapshot.payload == b"%PDF-1.4 fake pdf bytes"
    assert run.provider_version == "mac-granicus-http/1"


def test_repeated_identical_fetch_is_deduped_by_content_hash(session):
    url = "https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105406"
    source = _acquisition_source(session, url)

    def handler(request):
        return httpx.Response(200, content=b"same bytes", headers={"Content-Type": "application/pdf"}, request=request)

    first = AcquisitionService(session, MACGranicusAcquisitionProvider(url, client=_client(handler), timeout_seconds=1)).acquire(source)
    second = AcquisitionService(session, MACGranicusAcquisitionProvider(url, client=_client(handler), timeout_seconds=1)).acquire(source)

    assert first.snapshot_id == second.snapshot_id
    assert second.status is AcquisitionRunStatus.NO_CHANGE
    assert len(session.scalars(select(Snapshot)).all()) == 1
    assert len(session.scalars(select(AcquisitionRun)).all()) == 2


def test_changed_content_produces_a_new_snapshot(session):
    url = "https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105406"
    source = _acquisition_source(session, url)

    def handler_v1(request):
        return httpx.Response(200, content=b"version one", headers={"Content-Type": "application/pdf"}, request=request)

    def handler_v2(request):
        return httpx.Response(200, content=b"version two - revised", headers={"Content-Type": "application/pdf"}, request=request)

    first = AcquisitionService(session, MACGranicusAcquisitionProvider(url, client=_client(handler_v1), timeout_seconds=1)).acquire(source)
    second = AcquisitionService(session, MACGranicusAcquisitionProvider(url, client=_client(handler_v2), timeout_seconds=1)).acquire(source)

    assert first.snapshot_id != second.snapshot_id
    assert len(session.scalars(select(Snapshot)).all()) == 2
