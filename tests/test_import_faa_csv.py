from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Incident, Installation, Signal, Source
from scripts.import_faa_csv import (
    _parse_month_year,
    ensure_airport_coordinate_columns,
    find_airport,
    get_or_create_airport,
    get_or_create_incident,
    get_or_create_installation,
    get_or_create_source,
    import_all,
)

AIRPORTS_HEADER = "ARPT_ID,ATTR(ARPT_NAME),ATTR(CITY),ATTR(STATE),TYPE,Latitud (genererad),Longitud (genererad),MAP_REGION\n"
INCIDENTS_HEADER = "ARPT_ID,ARPT_NAME,CITY,STATE,NUM_INCIDENTS,INCIDENT_DATES,TOTAL_CREW_AND_PASSENGERS_SAVED,LATITUDE,LONGITUDE\n"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_parse_month_year_accepts_full_month_names():
    assert _parse_month_year("April 2017") == date(2017, 4, 1)
    assert _parse_month_year("December 2018") == date(2018, 12, 1)


def test_parse_month_year_rejects_unrecognized_format():
    with pytest.raises(ValueError):
        _parse_month_year("2017-04")
    with pytest.raises(ValueError):
        _parse_month_year("Aprilfool 2017")


def test_get_or_create_airport_creates_new_airport_with_coordinates(session_factory):
    with session_factory() as session:
        airport, created = get_or_create_airport(
            session,
            {
                "ARPT_ID": "ADQ",
                "ATTR(ARPT_NAME)": "Kodiak (ADQ)",
                "ATTR(CITY)": "Kodiak",
                "ATTR(STATE)": "ALASKA",
                "Latitud (genererad)": "57.74979391645144",
                "Longitud (genererad)": "-152.49394362826038",
            },
        )
        session.commit()

        assert created is True
        assert airport.faa_code == "ADQ"
        assert airport.name == "Kodiak"
        assert airport.city == "Kodiak"
        assert airport.state_region == "Alaska"
        assert airport.country == "USA"
        assert airport.latitude == pytest.approx(57.74979391645144)
        assert airport.longitude == pytest.approx(-152.49394362826038)
        assert airport.iata_code is None


def test_get_or_create_airport_updates_matched_airport_without_clobbering(session_factory):
    with session_factory() as session:
        existing = Airport(
            iata_code="MHT",
            icao_code="KMHT",
            name="Manchester-Boston Regional Airport",
            city="Manchester",
            state_region="New Hampshire",
            country="USA",
        )
        session.add(existing)
        session.commit()
        existing_id = existing.id

        airport, created = get_or_create_airport(
            session,
            {
                "ARPT_ID": "MHT",
                "ATTR(ARPT_NAME)": "Manchester Boston Regional (MHT)",
                "ATTR(CITY)": "Manchester",
                "ATTR(STATE)": "NEW HAMPSHIRE",
                "Latitud (genererad)": "42.93280555277896",
                "Longitud (genererad)": "-71.43575002385991",
            },
        )
        session.commit()

        assert created is False
        assert airport.id == existing_id
        # Existing richer name/city/state must not be clobbered by the CSV's version.
        assert airport.name == "Manchester-Boston Regional Airport"
        assert airport.state_region == "New Hampshire"
        # faa_code and coordinates were missing, so they get filled in.
        assert airport.faa_code == "MHT"
        assert airport.latitude == pytest.approx(42.93280555277896)

        assert session.scalar(select(Airport).where(Airport.iata_code == "MHT")) is airport


def test_get_or_create_installation_is_idempotent_by_type(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="XYZ", country="USA")
        session.add(airport)
        session.flush()
        source = get_or_create_source(session)

        first, created_first = get_or_create_installation(
            session, airport, "EMASMAX", source=source, notes="FAA map region: Map - Main"
        )
        session.commit()
        second, created_second = get_or_create_installation(
            session, airport, "EMASMAX", source=source, notes="FAA map region: Map - Main"
        )
        session.commit()

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert session.scalars(select(Installation)).all() == [first]


def test_get_or_create_incident_creates_and_triggers_signal_rule(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="XYZ", country="USA")
        session.add(airport)
        session.flush()
        source = get_or_create_source(session)

        incident, created = get_or_create_incident(
            session, airport, date(2017, 4, 1), source=source, summary="test summary"
        )
        session.commit()

        assert created is True
        signals = session.scalars(select(Signal).where(Signal.airport_id == airport.id)).all()
        assert len(signals) == 1
        assert signals[0].category == "replacement_after_incident"
        assert signals[0].confidence == "high"


def test_get_or_create_incident_is_idempotent_by_airport_and_date(session_factory):
    with session_factory() as session:
        airport = Airport(name="Test", faa_code="XYZ", country="USA")
        session.add(airport)
        session.flush()
        source = get_or_create_source(session)

        get_or_create_incident(session, airport, date(2017, 4, 1), source=source, summary="a")
        session.commit()
        _incident, created_again = get_or_create_incident(
            session, airport, date(2017, 4, 1), source=source, summary="b"
        )
        session.commit()

        assert created_again is False
        assert len(session.scalars(select(Incident)).all()) == 1
        # No second Signal should have been created for the duplicate.
        assert len(session.scalars(select(Signal)).all()) == 1


def test_ensure_airport_coordinate_columns_is_idempotent(session_factory):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    ensure_airport_coordinate_columns(engine)
    ensure_airport_coordinate_columns(engine)  # must not raise on the second call

    with Session(engine) as session:
        session.add(Airport(name="Test", faa_code="XYZ", country="USA", latitude=1.0, longitude=2.0))
        session.commit()
        airport = session.scalar(select(Airport))
        assert airport.latitude == 1.0
        assert airport.longitude == 2.0


def test_import_all_end_to_end_with_small_csv_fixtures(tmp_path, session_factory):
    airports_csv = tmp_path / "airports.csv"
    airports_csv.write_text(
        AIRPORTS_HEADER
        + "MHT,Manchester Boston Regional (MHT),Manchester,NEW HAMPSHIRE,EMASMAX,42.93,-71.43,Map - Main\n"
        + "ADQ,Kodiak (ADQ),Kodiak,ALASKA,EMASMAX,57.74,-152.49,Map - Alaska\n",
        encoding="utf-8",
    )
    incidents_csv = tmp_path / "incidents.csv"
    incidents_csv.write_text(
        INCIDENTS_HEADER
        + 'MHT,Manchester Boston Regional (MHT),Manchester,NEW HAMPSHIRE,2,"April 2017; December 2018",10,42.93,-71.43\n'
        + "UNKNOWN,Unknown Field,Nowhere,NOWHERE,1,July 2020,1,0,0\n",
        encoding="utf-8",
    )

    with session_factory() as session:
        session.add(Airport(iata_code="MHT", name="Manchester-Boston Regional Airport", country="USA"))
        session.commit()

    stats = import_all(
        airports_csv=airports_csv, incidents_csv=incidents_csv, session_factory=session_factory
    )

    assert stats["airports_created"] == 1  # ADQ
    assert stats["airports_updated"] == 1  # MHT, matched by iata_code
    assert stats["installations_created"] == 2
    assert stats["incidents_created"] == 2  # both MHT dates
    assert stats["airports_not_found"] == ["UNKNOWN"]

    with session_factory() as session:
        mht = find_airport(session, "MHT")
        assert len(mht.incidents) == 2
        assert len(session.scalars(select(Signal).where(Signal.airport_id == mht.id)).all()) == 2
        assert session.scalar(select(Source).where(Source.source_type == "faa_tableau")) is not None
