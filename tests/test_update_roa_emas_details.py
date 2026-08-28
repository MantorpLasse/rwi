from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Installation, Runway, Source
from scripts.update_roa_emas_details import (
    BRANCH_URL,
    WSLS_URL,
    UnexpectedStateError,
    apply,
    plan,
)


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_roa(session, *, with_current_bed_row=True):
    """Mirrors the real ROA shape exactly: airport, runway 16/34, and two
    Installation rows - one from a dated 2016 Fact Sheet (install_year=2004,
    no runway linkage) and one from the generic bulk FAA Tableau map
    (runway_id/runway_end set, no year at all)."""
    fact_sheet_source = Source(title="FAA Fact Sheet", source_type="faa_fact_sheet", url="https://old.test/fact-sheet-2016")
    session.add(fact_sheet_source)
    session.flush()
    airport = Airport(iata_code="ROA", icao_code="KROA", name="Roanoke/Blacksburg Regional", country="USA")
    session.add(airport)
    session.flush()
    runway = Runway(airport=airport, designation="16/34")
    session.add(runway)
    session.flush()

    historical = Installation(
        airport=airport,
        source=fact_sheet_source,
        type="EMASMAX",
        status="active",
        install_year=2004,
        notes="1 system, enligt bade 2011- och 2016-Fact Sheet, ingen andring.",
    )
    session.add(historical)

    if with_current_bed_row:
        tableau_source = Source(title="FAA map", source_type="faa_tableau", url="https://old.test/faa-map")
        session.add(tableau_source)
        session.flush()
        current = Installation(
            airport=airport, source=tableau_source, runway=runway, runway_end="34",
            type="EMASMAX", status="active", notes="FAA map region: Map - Main",
        )
        session.add(current)

    session.commit()
    return {"historical_id": historical.id, "old_source_id": fact_sheet_source.id}


def test_apply_sets_replacement_year_vendor_and_repoints_source():
    Session = session_factory()
    with Session() as session:
        ids = _seed_roa(session)

        result = apply(session, today=date(2026, 8, 28))

        assert result.updated is True
        assert result.installation_id == ids["historical_id"]
        installation = session.get(Installation, ids["historical_id"])
        assert installation.install_year == 2004  # unchanged
        assert installation.replacement_year == 2024
        assert installation.confirmed_vendor == "Runway Safe"
        assert installation.source.url == WSLS_URL
        assert installation.source_id != ids["old_source_id"]
        # old note preserved, new note appended
        assert "2016-Fact Sheet" in installation.notes
        assert "2024 replacement confirmed" in installation.notes
        assert "$12M" in installation.notes
        # old source row left in place, just unlinked
        assert session.get(Source, ids["old_source_id"]) is not None
        # both new evidence sources exist, even though only one is Installation.source_id
        branch = session.scalar(select(Source).where(Source.url == BRANCH_URL))
        assert branch is not None


def test_apply_never_touches_the_other_installation_row():
    Session = session_factory()
    with Session() as session:
        _seed_roa(session)
        current_before = session.scalar(select(Installation).where(Installation.install_year.is_(None)))
        before_snapshot = (current_before.runway_id, current_before.runway_end, current_before.source_id, current_before.notes)

        apply(session, today=date(2026, 8, 28))

        current_after = session.get(Installation, current_before.id)
        after_snapshot = (current_after.runway_id, current_after.runway_end, current_after.source_id, current_after.notes)
        assert before_snapshot == after_snapshot
        assert current_after.replacement_year is None
        assert current_after.install_year is None


def test_apply_is_idempotent_and_does_not_duplicate_sources():
    Session = session_factory()
    with Session() as session:
        _seed_roa(session)

        apply(session, today=date(2026, 8, 28))
        result_second = apply(session, today=date(2026, 8, 29))

        assert result_second.updated is False
        wsls_sources = session.scalars(select(Source).where(Source.url == WSLS_URL)).all()
        assert len(wsls_sources) == 1
        branch_sources = session.scalars(select(Source).where(Source.url == BRANCH_URL)).all()
        assert len(branch_sources) == 1


def test_plan_performs_no_write():
    Session = session_factory()
    with Session() as session:
        ids = _seed_roa(session)

        result = plan(session)

        assert result.updated is True
        installation = session.get(Installation, ids["historical_id"])
        assert installation.replacement_year is None  # unchanged - plan() never writes
        sources = session.scalars(select(Source)).all()
        assert len(sources) == 2  # only the two originally-seeded sources


def test_apply_raises_if_installation_count_is_not_exactly_two():
    Session = session_factory()
    with Session() as session:
        _seed_roa(session, with_current_bed_row=False)  # only 1 installation
        try:
            apply(session)
            assert False, "expected UnexpectedStateError"
        except UnexpectedStateError:
            pass


def test_apply_raises_if_before_state_does_not_match_expected():
    Session = session_factory()
    with Session() as session:
        ids = _seed_roa(session)
        # Simulate drift: someone already set a different replacement_year.
        installation = session.get(Installation, ids["historical_id"])
        installation.replacement_year = 1999
        session.commit()

        try:
            apply(session)
            assert False, "expected UnexpectedStateError"
        except UnexpectedStateError:
            pass


def test_apply_raises_if_roa_airport_is_missing():
    Session = session_factory()
    with Session() as session:
        try:
            apply(session)
            assert False, "expected UnexpectedStateError"
        except UnexpectedStateError:
            pass
