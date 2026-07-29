from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Runway, Signal, Source
from scripts.update_fty_emas_details import DRAFT_EA_URL, update_fty_signal


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_fty_with_signal(session):
    old_source = Source(
        title="Fulton County Master Plan Technical Report (Draft, dec 2022)",
        source_type="master_plan",
        url="https://old.test/fty-master-plan.pdf",
    )
    session.add(old_source)
    session.flush()
    airport = Airport(iata_code="FTY", icao_code="KFTY", name="Fulton County Executive Airport", country="USA")
    runway = Runway(airport=airport, designation="8/26", length_m=1767)
    signal = Signal(
        airport=airport,
        runway=runway,
        source=old_source,
        title="Runway 8/26 EMAS safety improvements",
        category="new_installation",
        confidence="programmed",
        status="design",
        planning_year=2026,
        procurement_year=2026,
        estimated_total_value_usd=13_400_000,
        source_notes="Runway safety improvements with planned EMAS at both ends.",
    )
    session.add(signal)
    session.commit()
    return {"signal_id": signal.id, "old_source_id": old_source.id}


def test_update_repoints_source_and_corrects_cost_estimate():
    Session = session_factory()
    with Session() as session:
        ids = _seed_fty_with_signal(session)

        signal, updated = update_fty_signal(session, today=date(2026, 7, 29))

        assert updated is True
        assert signal.source is not None
        assert signal.source.url == DRAFT_EA_URL
        assert signal.source_id != ids["old_source_id"]
        assert signal.estimated_total_value_usd == 32_000_000
        # old source row left in place, just unlinked
        assert session.get(Source, ids["old_source_id"]) is not None


def test_update_adds_both_notes_with_concrete_detail():
    Session = session_factory()
    with Session() as session:
        _seed_fty_with_signal(session)

        signal, _ = update_fty_signal(session, today=date(2026, 7, 29))

        # written to source_notes (public), not notes (private)
        assert signal.notes is None
        # old note preserved
        assert "Runway safety improvements with planned EMAS at both ends." in signal.source_notes
        # new concrete detail from the Draft EA
        assert "160" in signal.source_notes and "311" in signal.source_notes  # EMAS bed footprint
        assert "121,5" in signal.source_notes and "276" in signal.source_notes  # Table 2.1 bed size
        assert "nonstandard EMAS" in signal.source_notes
        assert "augusti 2026" in signal.source_notes
        assert "150 dagars" in signal.source_notes
        assert "32 MUSD" in signal.source_notes
        assert "Ingen entreprenör namngiven" in signal.source_notes
        # new RSA-deficiency detail from the GA statewide plan
        assert "690" in signal.source_notes and "150" in signal.source_notes
        assert "430" in signal.source_notes and "110" in signal.source_notes
        assert "1000" in signal.source_notes and "500" in signal.source_notes
        assert "Georgia Statewide Aviation System Plan" in signal.source_notes


def test_update_creates_ga_plan_source_without_a_fabricated_url():
    Session = session_factory()
    with Session() as session:
        _seed_fty_with_signal(session)

        update_fty_signal(session, today=date(2026, 7, 29))

        ga_source = session.scalar(select(Source).where(Source.title == "Georgia Statewide Aviation System Plan"))
        assert ga_source is not None
        assert ga_source.url is None
        assert ga_source.source_type == "state_aviation_system_plan"


def test_update_is_idempotent_and_does_not_duplicate_sources_or_notes():
    Session = session_factory()
    with Session() as session:
        _seed_fty_with_signal(session)

        update_fty_signal(session, today=date(2026, 7, 29))
        signal, updated_second_run = update_fty_signal(session, today=date(2026, 7, 30))

        assert updated_second_run is False
        ea_sources = session.scalars(select(Source).where(Source.url == DRAFT_EA_URL)).all()
        assert len(ea_sources) == 1
        ga_sources = session.scalars(
            select(Source).where(Source.title == "Georgia Statewide Aviation System Plan")
        ).all()
        assert len(ga_sources) == 1
        assert signal.source_notes.count(DRAFT_EA_URL) == 1
        assert signal.source_notes.count("Georgia Statewide Aviation System Plan") == 1


def test_update_raises_if_fty_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            update_fty_signal(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_update_raises_if_signal_is_missing():
    Session = session_factory()
    with Session() as session:
        session.add(Airport(iata_code="FTY", icao_code="KFTY", name="Fulton County Executive Airport", country="USA"))
        session.commit()
        try:
            update_fty_signal(session)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
