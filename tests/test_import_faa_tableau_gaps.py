from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport, Installation, Source
from scripts.import_faa_tableau_gaps import (
    BCT_URL,
    CUYAHOGA_URL,
    HXD_URL,
    PDK_URL,
    PHL_URL,
    SIMPLEFLYING_URL,
    seed,
)

CODES = ["OXC", "CGF", "HXD", "PHL", "PDK", "BCT", "VNC"]


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_prerequisites(session: Session) -> dict:
    faa_source = Source(title="FAA map", source_type="faa_tableau", url="https://old.test/faa-map")
    session.add(faa_source)
    session.flush()

    ids = {}
    for code in CODES:
        airport = Airport(iata_code=code, icao_code=f"K{code}"[:4], faa_code=code, name=f"{code} airport", country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(
            airport=airport, source=faa_source, type="EMASMAX", status="active",
            notes="FAA map region: Map - Main",
        )
        session.add(installation)
        session.flush()
        ids[code] = {"airport_id": airport.id, "installation_id": installation.id}

    session.commit()
    return ids


def test_seed_adds_one_new_dated_row_for_most_airports_leaving_old_row_untouched():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        stats = seed(session)

        assert stats["installations_created"] == 7  # 6 airports, CGF gets 2 rows
        for code in ["OXC", "HXD", "PHL", "PDK", "BCT"]:
            installations = session.scalars(
                select(Installation).where(Installation.airport_id == ids[code]["airport_id"])
            ).all()
            assert len(installations) == 2
            old = session.get(Installation, ids[code]["installation_id"])
            assert old.install_year is None  # untouched


def test_cgf_gets_two_rows_one_per_runway_end():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        installations = session.scalars(
            select(Installation).where(Installation.airport_id == ids["CGF"]["airport_id"])
        ).all()
        assert len(installations) == 3  # old generic + 2 new dated
        new_rows = [i for i in installations if i.install_year == 2018]
        assert len(new_rows) == 2
        runway_ends = {i.runway_end for i in new_rows}
        assert runway_ends == {"06", "24"}
        for row in new_rows:
            assert row.source.url == CUYAHOGA_URL


def test_phl_and_pdk_get_confirmed_vendors():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        phl = next(
            i for i in session.scalars(
                select(Installation).where(Installation.airport_id == ids["PHL"]["airport_id"])
            ).all()
            if i.install_year == 2025
        )
        assert phl.confirmed_vendor == "Runway Safe"
        assert phl.source.url == PHL_URL

        pdk = next(
            i for i in session.scalars(
                select(Installation).where(Installation.airport_id == ids["PDK"]["airport_id"])
            ).all()
            if i.install_year == 2018
        )
        assert pdk.confirmed_vendor == "Zodiac Aerospace"
        assert pdk.source.url == PDK_URL


def test_bct_gets_2016_and_hxd_notes_the_2019_discrepancy():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        bct = next(
            i for i in session.scalars(
                select(Installation).where(Installation.airport_id == ids["BCT"]["airport_id"])
            ).all()
            if i.install_year == 2016
        )
        assert bct.confirmed_vendor == "Runway Safe"
        assert bct.source.url == BCT_URL

        hxd = next(
            i for i in session.scalars(
                select(Installation).where(Installation.airport_id == ids["HXD"]["airport_id"])
            ).all()
            if i.install_year == 2018
        )
        assert "2019" in hxd.notes
        assert hxd.source.url == HXD_URL


def test_oxc_uses_simpleflying_source():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        oxc = next(
            i for i in session.scalars(
                select(Installation).where(Installation.airport_id == ids["OXC"]["airport_id"])
            ).all()
            if i.install_year == 2018
        )
        assert oxc.source.url == SIMPLEFLYING_URL


def test_vnc_is_left_completely_untouched():
    Session = session_factory()
    with Session() as session:
        ids = _seed_prerequisites(session)

        seed(session)

        installations = session.scalars(
            select(Installation).where(Installation.airport_id == ids["VNC"]["airport_id"])
        ).all()
        assert len(installations) == 1
        assert installations[0].install_year is None
        assert installations[0].notes == "FAA map region: Map - Main"


def test_seed_is_idempotent():
    Session = session_factory()
    with Session() as session:
        _seed_prerequisites(session)

        seed(session)
        stats_second_run = seed(session)

        assert stats_second_run == {"sources_created": 0, "installations_created": 0}
