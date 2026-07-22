import io
import zipfile
from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.acquisition.faa_runway_ends import (
    ArrestingSystemRow,
    RunwayEndsSourceError,
    discover_apt_csv_url,
    fetch_emas_arresting_system_rows,
)
from app.database import Base
from app.models import Airport, Installation, Runway
from scripts.import_faa_runway_ends import enrich_installations, run

INDEX_HTML = """
<html><body>
<a href="./../NASR_Subscription/2026-08-06">2026-08-06</a>
<a href="./../NASR_Subscription/2026-07-09">2026-07-09</a>
</body></html>
"""
CYCLE_HTML = """
<html><body>
<a href="https://nfdc.faa.gov/webContent/28DaySub/extra/09_Jul_2026_APT_CSV.zip">APT CSV</a>
</body></html>
"""
APT_ARS_CSV = (
    "EFF_DATE,SITE_NO,SITE_TYPE_CODE,STATE_CODE,ARPT_ID,CITY,COUNTRY_CODE,RWY_ID,RWY_END_ID,ARREST_DEVICE_CODE\n"
    "2026/07/09,00447.,A,NH,MHT,MANCHESTER,US,6/24,6,EMAS\n"
    "2026/07/09,00448.,A,NY,BGM,BINGHAMTON,US,16/34,16,EMAS\n"
    "2026/07/09,00449.,A,AL,MGM,MONTGOMERY,US,10/28,10,BAK-12B\n"
    "2026/07/09,00450.,A,CA,XXX,MULTI,US,1/19,1,EMAS\n"
    "2026/07/09,00450.,A,CA,XXX,MULTI,US,1/19,19,EMAS\n"
)


def _zip_bytes(name: str, content: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


def test_discover_apt_csv_url_picks_the_effective_past_cycle_and_finds_the_link():
    def handler(request):
        if str(request.url) == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/":
            return httpx.Response(200, content=INDEX_HTML, request=request)
        if str(request.url) == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-07-09/":
            return httpx.Response(200, content=CYCLE_HTML, request=request)
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    url = discover_apt_csv_url(client=client, today=date(2026, 7, 22))

    assert url == "https://nfdc.faa.gov/webContent/28DaySub/extra/09_Jul_2026_APT_CSV.zip"


def test_discover_apt_csv_url_fails_closed_when_no_cycle_is_effective_yet():
    def handler(request):
        return httpx.Response(200, content=INDEX_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RunwayEndsSourceError):
        discover_apt_csv_url(client=client, today=date(2026, 1, 1))


def test_fetch_emas_arresting_system_rows_filters_to_emas_only():
    zip_bytes = _zip_bytes("APT_ARS.csv", APT_ARS_CSV)

    def handler(request):
        return httpx.Response(200, content=zip_bytes, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = fetch_emas_arresting_system_rows(
        client=client, apt_csv_url="https://nfdc.faa.example/APT_CSV.zip"
    )

    arpt_ids = {row.arpt_id for row in rows}
    assert arpt_ids == {"MHT", "BGM", "XXX"}  # MGM's BAK-12B is excluded
    assert all(row.arrest_device_code == "EMAS" for row in rows)


def test_fetch_emas_arresting_system_rows_fails_closed_without_ars_file():
    zip_bytes = _zip_bytes("OTHER.csv", "a,b\n1,2\n")

    def handler(request):
        return httpx.Response(200, content=zip_bytes, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RunwayEndsSourceError):
        fetch_emas_arresting_system_rows(client=client, apt_csv_url="https://nfdc.faa.example/x.zip")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_enrich_installations_sets_runway_and_runway_end_on_single_match(session_factory):
    with session_factory() as session:
        airport = Airport(name="Manchester-Boston Regional Airport", faa_code="MHT", country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(airport=airport, type="EMASMAX", status="active")
        session.add(installation)
        session.commit()

        rows = [ArrestingSystemRow(arpt_id="MHT", rwy_id="6/24", rwy_end_id="6", arrest_device_code="EMAS")]
        stats = enrich_installations(session, {"MHT": rows})
        session.commit()

        assert stats["enriched"] == 1
        assert installation.runway_end == "6"
        assert installation.runway.designation == "6/24"
        assert session.scalar(select(Runway).where(Runway.airport_id == airport.id)) is installation.runway


def test_enrich_installations_reuses_existing_runway_instead_of_duplicating(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="MHT", country="USA")
        existing_runway = Runway(airport=airport, designation="6/24")
        session.add_all([airport, existing_runway])
        session.flush()
        installation = Installation(airport=airport, type="EMASMAX", status="active")
        session.add(installation)
        session.commit()

        rows = [ArrestingSystemRow(arpt_id="MHT", rwy_id="6/24", rwy_end_id="6", arrest_device_code="EMAS")]
        enrich_installations(session, {"MHT": rows})
        session.commit()

        assert installation.runway_id == existing_runway.id
        assert len(session.scalars(select(Runway)).all()) == 1


def test_enrich_installations_is_idempotent(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="MHT", country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(airport=airport, type="EMASMAX", status="active")
        session.add(installation)
        session.commit()

        rows = [ArrestingSystemRow(arpt_id="MHT", rwy_id="6/24", rwy_end_id="6", arrest_device_code="EMAS")]
        first = enrich_installations(session, {"MHT": rows})
        session.commit()
        second = enrich_installations(session, {"MHT": rows})
        session.commit()

        assert first["enriched"] == 1
        assert second["enriched"] == 0
        assert second["already_enriched"] == 1


def test_enrich_installations_notes_ambiguity_without_guessing(session_factory):
    with session_factory() as session:
        airport = Airport(name="Multi Runway Airport", faa_code="XXX", country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(airport=airport, type="EMASMAX", status="active")
        session.add(installation)
        session.commit()

        rows = [
            ArrestingSystemRow(arpt_id="XXX", rwy_id="1/19", rwy_end_id="1", arrest_device_code="EMAS"),
            ArrestingSystemRow(arpt_id="XXX", rwy_id="1/19", rwy_end_id="19", arrest_device_code="EMAS"),
        ]
        stats = enrich_installations(session, {"XXX": rows})
        session.commit()

        assert stats["ambiguous_multiple_ends"] == 1
        assert installation.runway_end is None
        assert installation.runway_id is None
        assert "1/19/1" in installation.notes  # mentions both candidate ends
        assert "1/19/19" in installation.notes


def test_enrich_installations_reports_no_match(session_factory):
    with session_factory() as session:
        airport = Airport(name="Unmatched Airport", faa_code="ZZZ", country="USA")
        session.add(airport)
        session.flush()
        session.add(Installation(airport=airport, type="EMASMAX", status="active"))
        session.commit()

        stats = enrich_installations(session, {})
        assert stats["no_faa_arresting_system_match"] == 1


def test_run_end_to_end_with_mocked_network(session_factory):
    zip_bytes = _zip_bytes("APT_ARS.csv", APT_ARS_CSV)

    def handler(request):
        url = str(request.url)
        if url.endswith("NASR_Subscription/"):
            return httpx.Response(200, content=INDEX_HTML, request=request)
        if url.endswith("NASR_Subscription/2026-07-09/"):
            return httpx.Response(200, content=CYCLE_HTML, request=request)
        if url.endswith("APT_CSV.zip"):
            return httpx.Response(200, content=zip_bytes, request=request)
        return httpx.Response(404, request=request)

    with session_factory() as session:
        session.add(Airport(name="Manchester-Boston Regional Airport", faa_code="MHT", country="USA"))
        session.add(Installation(
            airport=session.scalars(select(Airport)).one(), type="EMASMAX", status="active"
        ))
        session.commit()

    client = httpx.Client(transport=httpx.MockTransport(handler))
    stats = run(session_factory=session_factory, client=client, today=date(2026, 7, 22))

    assert stats["faa_emas_rows_fetched"] == 4  # MHT + BGM + XXX (both ends) = 4 EMAS rows
    assert stats["enriched"] == 1

    with session_factory() as session:
        installation = session.scalar(select(Installation))
        assert installation.runway_end == "6"
