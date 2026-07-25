from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Airport
from scripts.backfill_airport_codes import VERIFIED_CODES, backfill


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_backfill_sets_iata_and_icao_for_verified_airports():
    Session = session_factory()
    with Session() as session:
        session.add_all(
            [
                Airport(name="Chicago O'Hare International", faa_code="ORD", country="USA"),
                Airport(name="Kodiak", faa_code="ADQ", country="USA"),  # Alaska - "PA" ICAO prefix
            ]
        )
        session.commit()

        stats = backfill(session)

        assert stats["updated"] == 2
        assert stats["skipped_not_verified"] == []

        ord_ = session.scalar(select(Airport).where(Airport.faa_code == "ORD"))
        assert ord_.iata_code == "ORD"
        assert ord_.icao_code == "KORD"

        adq = session.scalar(select(Airport).where(Airport.faa_code == "ADQ"))
        assert adq.iata_code == "ADQ"
        assert adq.icao_code == "PADQ"


def test_backfill_skips_unverified_faa_codes_without_guessing():
    Session = session_factory()
    with Session() as session:
        session.add(Airport(name="Cartersville", faa_code="VPC", country="USA"))
        session.commit()

        stats = backfill(session)

        assert stats["updated"] == 0
        assert stats["skipped_not_verified"] == ["VPC (Cartersville)"]

        airport = session.scalar(select(Airport).where(Airport.faa_code == "VPC"))
        assert airport.iata_code is None
        assert airport.icao_code is None


def test_backfill_handles_renamed_djt_as_palm_beach_international():
    """DJT is Palm Beach International renamed - FAA LID changed, but the
    real iata_code/icao_code (PBI/KPBI) predate the rename and don't."""
    Session = session_factory()
    with Session() as session:
        session.add(
            Airport(name="President Donald J. Trump International", faa_code="DJT", country="USA")
        )
        session.commit()

        stats = backfill(session)

        assert stats["updated"] == 1
        airport = session.scalar(select(Airport).where(Airport.faa_code == "DJT"))
        assert airport.iata_code == "PBI"
        assert airport.icao_code == "KPBI"


def test_backfill_never_touches_already_coded_airports():
    Session = session_factory()
    with Session() as session:
        session.add(
            Airport(name="Test", iata_code="ZZZ", icao_code="KZZZ", faa_code="ZZZ", country="USA")
        )
        session.commit()

        stats = backfill(session)

        assert stats == {"updated": 0, "skipped_not_verified": []}


def test_backfill_is_idempotent():
    Session = session_factory()
    with Session() as session:
        session.add(Airport(name="Newark Liberty International", faa_code="EWR", country="USA"))
        session.commit()

        backfill(session)
        stats_second_run = backfill(session)

        assert stats_second_run == {"updated": 0, "skipped_not_verified": []}


def test_verified_codes_are_all_three_and_four_letter_codes():
    # DJT is the one documented exception: a post-rename FAA LID that no
    # longer matches the airport's real (unchanged) iata_code.
    exceptions_to_faa_code_equals_iata_code = {"DJT"}
    for faa_code, (iata_code, icao_code) in VERIFIED_CODES.items():
        assert len(faa_code) == 3
        assert len(iata_code) == 3
        assert len(icao_code) == 4
        if faa_code not in exceptions_to_faa_code_equals_iata_code:
            assert faa_code == iata_code
