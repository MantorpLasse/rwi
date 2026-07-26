from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.add_gadelius_greenemas_installations import GADELIUS_URL, PRWEB_URL, seed


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_prerequisite_airports(session: Session) -> dict:
    mdw = Airport(iata_code="MDW", icao_code="KMDW", name="Chicago Midway International Airport", country="USA")
    ord_ = Airport(iata_code="ORD", icao_code="KORD", name="Chicago O'Hare International", country="USA")
    cgh = Airport(iata_code="CGH", icao_code="SBSP", name="Congonhas Airport", country="Brazil")
    session.add_all([mdw, ord_, cgh])
    session.flush()

    faa_source = Source(title="FAA map", source_type="faa_tableau")
    session.add(faa_source)
    session.flush()

    mdw_faa_installation = Installation(
        airport=mdw, source=faa_source, type="greenEMAS", status="active",
        notes="FAA map region: Map - Main",
    )
    ord_faa_installation = Installation(
        airport=ord_, source=faa_source, type="EMASMAX", status="active",
        notes="FAA map region: Map - Main",
    )
    cgh_installation = Installation(
        airport=cgh, type="EMAS", install_year=2022, status="active",
        notes="Latinamerikas forsta EMAS.",
    )
    session.add_all([mdw_faa_installation, ord_faa_installation, cgh_installation])
    session.commit()
    return {
        "mdw_faa_installation_id": mdw_faa_installation.id,
        "ord_faa_installation_id": ord_faa_installation.id,
        "cgh_installation_id": cgh_installation.id,
    }


def test_seed_creates_six_new_airports_and_their_installations():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisite_airports(session)

        stats = seed(session, today=date(2026, 7, 27))

        assert stats["airports_created"] == 6
        assert stats["installations_created"] == 8  # MDW + ORD + 6 new airports
        assert stats["cgh_updated"] is True

        expected = {
            "ZRH": ("Switzerland", 2016),
            "RUN": ("France", 2017),
            "DZA": ("France", 2018),
            "SCN": ("Germany", 2019),
            "HND": ("Japan", 2019),
            "NHT": ("United Kingdom", 2019),
        }
        for code, (country, year) in expected.items():
            airport = session.scalar(select(Airport).where(Airport.iata_code == code))
            assert airport is not None and airport.country == country

            installation = session.scalar(
                select(Installation).where(Installation.airport_id == airport.id)
            )
            assert installation is not None
            assert installation.type == "greenEMAS"
            assert installation.install_year == year
            assert installation.confirmed_vendor == "Runway Safe"
            assert installation.source.url == GADELIUS_URL


def test_seed_adds_a_separate_mdw_installation_without_touching_the_faa_one():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisite_airports(session)

        seed(session, today=date(2026, 7, 27))

        mdw = session.scalar(select(Airport).where(Airport.iata_code == "MDW"))
        installations = session.scalars(
            select(Installation).where(Installation.airport_id == mdw.id)
        ).all()
        assert len(installations) == 2

        faa_installation = session.get(Installation, ids["mdw_faa_installation_id"])
        assert faa_installation.type == "greenEMAS"
        assert faa_installation.install_year is None  # untouched

        new_installation = next(i for i in installations if i.id != ids["mdw_faa_installation_id"])
        assert new_installation.install_year == 2014
        assert new_installation.runway_end == "22L"
        assert new_installation.confirmed_vendor == "Runway Safe"
        assert new_installation.source.url == GADELIUS_URL
        assert PRWEB_URL in new_installation.notes


def test_seed_adds_a_separate_ord_installation_sourced_from_prweb():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisite_airports(session)

        seed(session, today=date(2026, 7, 27))

        ord_ = session.scalar(select(Airport).where(Airport.iata_code == "ORD"))
        installations = session.scalars(
            select(Installation).where(Installation.airport_id == ord_.id)
        ).all()
        assert len(installations) == 2

        faa_installation = session.get(Installation, ids["ord_faa_installation_id"])
        assert faa_installation.type == "EMASMAX"  # untouched

        new_installation = next(i for i in installations if i.id != ids["ord_faa_installation_id"])
        assert new_installation.type == "greenEMAS"
        assert new_installation.install_year is None
        assert new_installation.source.url == PRWEB_URL


def test_seed_corrects_cgh_type_to_greenemas_and_appends_note():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisite_airports(session)

        seed(session, today=date(2026, 7, 27))

        cgh_installation = session.get(Installation, ids["cgh_installation_id"])
        assert cgh_installation.type == "greenEMAS"
        assert "Latinamerikas forsta EMAS" in cgh_installation.notes  # old note preserved
        assert GADELIUS_URL in cgh_installation.notes
        assert cgh_installation.install_year == 2022  # unchanged


def test_seed_only_creates_two_sources_total():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisite_airports(session)

        seed(session, today=date(2026, 7, 27))

        gadelius_sources = session.scalars(select(Source).where(Source.url == GADELIUS_URL)).all()
        prweb_sources = session.scalars(select(Source).where(Source.url == PRWEB_URL)).all()
        assert len(gadelius_sources) == 1
        assert len(prweb_sources) == 1


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisite_airports(session)

        seed(session, today=date(2026, 7, 27))
        stats_second_run = seed(session, today=date(2026, 7, 28))

        assert stats_second_run == {
            "airports_created": 0,
            "sources_created": 0,
            "installations_created": 0,
            "cgh_updated": False,
        }
        assert len(session.scalars(select(Airport)).all()) == 9  # 3 prerequisite + 6 new
        assert len(session.scalars(select(Source)).all()) == 3  # faa + gadelius + prweb


def test_seed_raises_if_mdw_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            seed(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
