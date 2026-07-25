from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.acquisition.faa_iija_grants import (
    ANNOUNCEMENTS_PER_YEAR,
    discover_iija_grant_pdf_urls,
    iija_grant_pdf_url,
    parse_grant_pdf,
)
from app.database import Base
from app.models import Airport, Signal, Source
from scripts.import_faa_iija_grants import import_year

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "aip_grant_sample.pdf").read_bytes()


def test_iija_grant_pdf_url_matches_the_faa_pattern():
    assert iija_grant_pdf_url(2026, 4) == (
        "https://www.faa.gov/iija/iija-airport-infrastructure-grant-funding-amounts/"
        "AIG-FY2026-A4.pdf"
    )


def test_discover_iija_grant_pdf_urls_builds_six_urls_without_network():
    urls = discover_iija_grant_pdf_urls(2026)

    assert len(urls) == ANNOUNCEMENTS_PER_YEAR == 6
    assert urls[0] == iija_grant_pdf_url(2026, 1)
    assert urls[-1] == iija_grant_pdf_url(2026, 6)


def test_parse_grant_pdf_is_reused_unmodified_from_aip_module():
    grants = parse_grant_pdf(FIXTURE_PDF, source_pdf_url="https://faa.example/AIG-FY2026-A4.pdf")

    assert len(grants) == 2
    assert grants[0].loc_id == "DCA"
    assert grants[0].total_aip_amt == Decimal("15000000.00")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _client_serving(urls_to_pdfs: dict[str, bytes]):
    def handler(request):
        pdf_bytes = urls_to_pdfs.get(str(request.url))
        if pdf_bytes is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=pdf_bytes, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_import_year_fetches_all_six_announcements(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    urls = {url: FIXTURE_PDF for url in discover_iija_grant_pdf_urls(2026)}
    stats = import_year(2026, session_factory=session_factory, client=_client_serving(urls))

    assert stats["pdfs"] == 6
    assert stats["grants"] == 12  # 2 grant rows per PDF x 6 PDFs
    assert stats["matched"] == 6  # DCA appears in every PDF
    assert stats["signals_created"] == 0  # "Construct Taxiway" has no EMAS/RSA keyword
    assert set(stats["unmatched_loc_ids"]) == {"ORD"}

    with session_factory() as session:
        sources = session.scalars(select(Source).where(Source.source_type == "iija_grant")).all()
        assert len(sources) == 6
        assert {s.external_id for s in sources} == {f"iija:2026:{n}:DCA" for n in range(1, 7)}


def test_import_year_creates_signal_when_project_description_matches_keyword(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Greater Binghamton", faa_code="BGM", country="USA"))
        session.commit()

    pdf_url = iija_grant_pdf_url(2026, 4)

    def fake_parse(pdf_bytes, *, source_pdf_url):
        from app.acquisition.faa_iija_grants import AipGrant

        return [
            AipGrant(
                state="NY",
                city="Binghamton",
                airport_name="Greater Binghamton",
                loc_id="BGM",
                project_description="Reconstruct Engineered Material Arresting System Safety Area",
                entitlement_amt=Decimal("5100000.00"),
                discretionary_amt=None,
                total_aip_amt=Decimal("5100000.00"),
                source_pdf_url=source_pdf_url,
            )
        ]

    import scripts.import_faa_iija_grants as module

    original = module.parse_grant_pdf
    module.parse_grant_pdf = fake_parse
    try:
        urls = {url: b"fake-pdf-bytes" for url in discover_iija_grant_pdf_urls(2026)}
        stats = import_year(2026, session_factory=session_factory, client=_client_serving(urls))
    finally:
        module.parse_grant_pdf = original

    assert stats["matched"] == 6
    assert stats["signals_created"] == 6

    with session_factory() as session:
        signals = session.scalars(select(Signal)).all()
        assert len(signals) == 6
        assert all(s.confidence == "low" for s in signals)


def test_import_year_is_idempotent_on_rerun(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    urls = {url: FIXTURE_PDF for url in discover_iija_grant_pdf_urls(2026)}
    import_year(2026, session_factory=session_factory, client=_client_serving(urls))
    stats_second_run = import_year(2026, session_factory=session_factory, client=_client_serving(urls))

    assert stats_second_run["already_imported"] == 6
    assert stats_second_run["matched"] == 0

    with session_factory() as session:
        assert len(session.scalars(select(Source).where(Source.source_type == "iija_grant")).all()) == 6
