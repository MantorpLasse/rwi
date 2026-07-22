from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.faa_aip_grants import (
    AipGrant,
    AipGrantsError,
    discover_grant_pdf_urls,
    parse_grant_pdf,
)
from app.database import Base
from app.models import Airport, Signal, Source
from scripts.import_faa_aip_grants import import_year

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "aip_grant_sample.pdf").read_bytes()
LISTING_HTML = """
<html><body>
<a href="/airports/aip/2026_aip_grants/AIP_FY26_1.pdf">Announcement 1</a>
<a href="/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf">Announcement 1A</a>
<a href="/airports/aip/2026_aip_grants/AIP_FY26_1.pdf">duplicate link</a>
</body></html>
"""


def test_discover_grant_pdf_urls_resolves_and_deduplicates_links():
    def handler(request):
        assert str(request.url) == "https://www.faa.gov/airports/aip/2026_aip_grants"
        return httpx.Response(200, content=LISTING_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = discover_grant_pdf_urls(2026, client=client)

    assert urls == [
        "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1.pdf",
        "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf",
    ]


def test_discover_grant_pdf_urls_fails_closed_on_no_pdfs():
    def handler(request):
        return httpx.Response(200, content=b"<html><body>no links here</body></html>", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AipGrantsError):
        discover_grant_pdf_urls(2026, client=client)


def test_parse_grant_pdf_extracts_rows_and_skips_totals_row():
    grants = parse_grant_pdf(FIXTURE_PDF, source_pdf_url="https://faa.example/AIP_FY26_1A.pdf")

    assert len(grants) == 2
    dca, ord_ = grants
    assert dca == AipGrant(
        state="VA",
        city="Arlington",
        airport_name="Ronald Reagan Washington Ntl",
        loc_id="DCA",
        project_description="Construct Taxiway",
        entitlement_amt=Decimal("0.00"),
        discretionary_amt=Decimal("15000000.00"),
        total_aip_amt=Decimal("15000000.00"),
        source_pdf_url="https://faa.example/AIP_FY26_1A.pdf",
    )
    assert ord_.loc_id == "ORD"
    assert ord_.project_description == "Extend/Expand Runway"
    assert ord_.total_aip_amt == Decimal("20000000.00")
    # The trailing totals row (blank Loc ID) must not appear as a grant.
    assert all(g.loc_id for g in grants)


def test_parse_grant_pdf_fails_closed_on_unexpected_header():
    import pdfplumber

    class _FakePage:
        def extract_table(self):
            return [["Wrong", "Header"], ["a", "b"]]

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    original_open = pdfplumber.open
    pdfplumber.open = lambda _fp: _FakePdf()
    try:
        with pytest.raises(AipGrantsError):
            parse_grant_pdf(b"irrelevant", source_pdf_url="https://faa.example/x.pdf")
    finally:
        pdfplumber.open = original_open


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _client_serving(pdf_bytes: bytes, pdf_url: str):
    def handler(request):
        if str(request.url).endswith("_aip_grants"):
            return httpx.Response(
                200,
                content=f'<a href="{pdf_url}">grants</a>'.encode(),
                request=request,
            )
        if str(request.url) == pdf_url:
            return httpx.Response(200, content=pdf_bytes, request=request)
        return httpx.Response(404, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_import_year_creates_source_and_signal_for_tracked_airport_with_keyword(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf"
    stats = import_year(
        2026, session_factory=session_factory, client=_client_serving(FIXTURE_PDF, pdf_url)
    )

    assert stats["pdfs"] == 1
    assert stats["grants"] == 2
    assert stats["matched"] == 1  # only DCA is tracked, not ORD
    assert stats["signals_created"] == 0  # "Construct Taxiway" has no EMAS/RSA keyword
    assert stats["unmatched_loc_ids"] == ["ORD"]

    with session_factory() as session:
        sources = session.scalars(select(Source).where(Source.source_type == "aip_grant")).all()
        assert len(sources) == 1
        assert sources[0].document_reference == "DCA"
        assert sources[0].url == pdf_url
        assert session.scalars(select(Signal)).all() == []


def test_import_year_creates_signal_when_project_description_matches_keyword(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Greater Binghamton", faa_code="BGM", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/emas.pdf"

    def fake_parse(pdf_bytes, *, source_pdf_url):
        return [
            AipGrant(
                state="NY",
                city="Binghamton",
                airport_name="Greater Binghamton",
                loc_id="BGM",
                project_description="Reconstruct Engineered Material Arresting System Safety Area",
                entitlement_amt=Decimal("437081.00"),
                discretionary_amt=None,
                total_aip_amt=Decimal("437081.00"),
                source_pdf_url=source_pdf_url,
            )
        ]

    import scripts.import_faa_aip_grants as module

    original = module.parse_grant_pdf
    module.parse_grant_pdf = fake_parse
    try:
        stats = import_year(
            2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url)
        )
    finally:
        module.parse_grant_pdf = original

    assert stats["matched"] == 1
    assert stats["signals_created"] == 1

    with session_factory() as session:
        signal = session.scalar(select(Signal))
        assert signal is not None
        assert signal.confidence == "low"
        assert "arresting system" in signal.notes.lower()
