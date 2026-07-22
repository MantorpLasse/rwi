from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.usaspending_grants import UsaspendingGrant
from app.database import Base
from app.models import Airport, Signal, Source
from scripts.import_usaspending_grants import (
    classify_category,
    ensure_source_external_id_column,
    import_all,
    resolve_airport,
)

GEORGIA_DOT_DESCRIPTION = (
    "PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA. THIS PROJECT AWARDS A STATE "
    "BLOCK GRANT PROGRAM GRANT TO THE STATE OF GEORGIA TO ADMINISTER AIRPORT "
    "IMPROVEMENT PROGRAM SUB-AWARDS FOR NONPRIMARY AIRPORTS WITHIN THE STATE. "
    "THIS GRANT INCLUDES DISCRETIONARY FUNDING FOR THE CARTERSVILLE-BARTOW "
    "COUNTY AIRPORT (VPC), LOCATED IN THE 11TH CONGRESSIONAL DISTRICT IN "
    "GEORGIA TO INSTALL ENGINEERED MATERIAL ARRESTING SYSTEM FOR RUNWAY 19. "
    "INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR "
    "AIRPORTS IN THE GEORGIA STATE BLOCK GRANT PROGRAM."
)
MICHIGAN_DOT_DESCRIPTION = (
    "PURPOSE: CONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA. THIS "
    "PROJECT AWARDS A STATE BLOCK GRANT PROGRAM GRANT TO THE STATE OF MICHIGAN "
    "TO ADMINISTER AIRPORT IMPROVEMENT PROGRAM SUB-AWARDS FOR NONPRIMARY "
    "AIRPORTS WITHIN THE STATE. INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE "
    "FEDERAL FUNDING FOR AIRPORTS IN THE MICHIGAN STATE BLOCK GRANT PROGRAM."
)
BGM_DESCRIPTION = (
    "PURPOSE: RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA. "
    "INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR "
    "AIRPORTS ASSOCIATED WITH BINGHAMTON, NEW YORK."
)
MORRISTOWN_DESCRIPTION = (
    "PURPOSE: CONSTRUCT/EXTEND SAFETY AREA. THIS PROJECT CONSTRUCTS A RUNWAY "
    "5/23 SAFETY AREA. INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL "
    "FUNDING FOR AIRPORTS ASSOCIATED WITH MORRISTOWN, NEW JERSEY."
)


def make_grant(description, *, external_id="ASST_NON_TEST_1", recipient_name="TEST RECIPIENT", amount="1000000.00", start="2026-06-16"):
    return UsaspendingGrant(
        external_id=external_id,
        award_id="TEST-AWARD",
        recipient_name=recipient_name,
        award_amount=Decimal(amount) if amount else None,
        description=description,
        awarding_agency="Department of Transportation",
        start_date=date.fromisoformat(start) if start else None,
    )


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_classify_category_detects_replacement_keywords():
    assert classify_category("RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM") == "replacement"
    assert classify_category("REPLACE THE ENGINEERED MATERIAL ARRESTING SYSTEM") == "replacement"
    assert classify_category("CONSTRUCT A NEW ENGINEERED MATERIAL ARRESTING SYSTEM") == "new_installation"


def test_resolve_airport_matches_existing_via_embedded_loc_id(session_factory):
    with session_factory() as session:
        airport = Airport(name="Cartersville", faa_code="VPC", country="USA")
        session.add(airport)
        session.commit()

        resolved, created = resolve_airport(session, make_grant(GEORGIA_DOT_DESCRIPTION))

        assert created is False
        assert resolved.id == airport.id


def test_resolve_airport_matches_existing_via_beneficiary_city_state(session_factory):
    with session_factory() as session:
        airport = Airport(name="Greater Binghamton Airport", faa_code="BGM", city="Binghamton", state_region="New York", country="USA")
        session.add(airport)
        session.commit()

        resolved, created = resolve_airport(session, make_grant(BGM_DESCRIPTION))

        assert created is False
        assert resolved.id == airport.id


def test_resolve_airport_creates_new_airport_from_beneficiary_sentence(session_factory):
    with session_factory() as session:
        resolved, created = resolve_airport(session, make_grant(MORRISTOWN_DESCRIPTION))

        assert created is True
        assert resolved is not None
        assert resolved.city == "Morristown"
        assert resolved.state_region == "New Jersey"
        assert resolved.faa_code is None


def test_resolve_airport_creates_new_airport_from_embedded_loc_id_when_unknown(session_factory):
    with session_factory() as session:
        resolved, created = resolve_airport(session, make_grant(GEORGIA_DOT_DESCRIPTION))

        assert created is True
        assert resolved.faa_code == "VPC"


def test_resolve_airport_returns_none_when_ambiguous(session_factory):
    with session_factory() as session:
        session.add_all([
            Airport(name="Airport A", city="Springfield", state_region="Illinois", country="USA"),
            Airport(name="Airport B", city="Springfield", state_region="Illinois", country="USA"),
        ])
        session.commit()

        grant = make_grant(
            "INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR "
            "AIRPORTS ASSOCIATED WITH SPRINGFIELD, ILLINOIS."
        )
        resolved, created = resolve_airport(session, grant)

        assert resolved is None
        assert created is False
        assert len(session.scalars(select(Airport)).all()) == 2  # no duplicate created


def test_resolve_airport_returns_none_when_unattributable(session_factory):
    with session_factory() as session:
        resolved, created = resolve_airport(session, make_grant(MICHIGAN_DOT_DESCRIPTION))

        assert resolved is None
        assert created is False


def test_ensure_source_external_id_column_adds_it_to_an_old_style_table():
    """Simulates the real production DB: a sources table predating external_id."""
    from sqlalchemy import text

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE sources ("
                "id INTEGER PRIMARY KEY, title VARCHAR(300) NOT NULL, "
                "source_type VARCHAR(50) NOT NULL, url VARCHAR(1000) NOT NULL"
                ")"
            )
        )

    ensure_source_external_id_column(engine)
    ensure_source_external_id_column(engine)  # must not raise the second time

    with Session(engine) as session:
        session.execute(
            text("INSERT INTO sources (title, source_type, url, external_id) VALUES ('t', 'x', 'https://x', 'a')")
        )
        session.commit()
        with pytest.raises(Exception):
            session.execute(
                text("INSERT INTO sources (title, source_type, url, external_id) VALUES ('t2', 'x', 'https://y', 'a')")
            )
            session.commit()


def _client_with(grants_json):
    def handler(request):
        return httpx.Response(
            200,
            json={"results": grants_json, "page_metadata": {"hasNext": False}},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_import_all_creates_signal_for_matched_airport(session_factory):
    with session_factory() as session:
        session.add(Airport(name="Greater Binghamton Airport", faa_code="BGM", city="Binghamton", state_region="New York", country="USA"))
        session.commit()

    row = {
        "generated_internal_id": "ASST_NON_BGM_1",
        "Award ID": "BGM-1",
        "Recipient Name": "BROOME COUNTY",
        "Award Amount": 415226.0,
        "Description": BGM_DESCRIPTION,
        "Awarding Agency": "Department of Transportation",
        "Start Date": "2026-06-16",
    }
    stats = import_all(
        end_date=date(2026, 12, 31),
        session_factory=session_factory,
        client=_client_with([row]),
    )

    assert stats["grants_fetched"] == 1
    assert stats["signals_created"] == 1
    assert stats["airports_created"] == 0
    assert stats["unattributable"] == 0

    with session_factory() as session:
        signal = session.scalar(select(Signal))
        assert signal.confidence == "high"
        assert signal.status == "identified"
        assert signal.probability_score == 8.0
        assert signal.category == "replacement"
        assert signal.planning_year == 2026
        assert signal.estimated_total_value_usd == Decimal("415226.0")

        source = session.scalar(select(Source))
        assert source.source_type == "usaspending_grant"
        assert source.external_id == "usaspending:ASST_NON_BGM_1"


def test_import_all_creates_new_airport_when_unmatched(session_factory):
    row = {
        "generated_internal_id": "ASST_NON_MORRIS_1",
        "Award ID": "MORRIS-1",
        "Recipient Name": "TOWN OF MORRISTOWN",
        "Award Amount": 5756758.0,
        "Description": MORRISTOWN_DESCRIPTION,
        "Awarding Agency": "Department of Transportation",
        "Start Date": "2025-09-19",
    }
    stats = import_all(
        end_date=date(2026, 12, 31),
        session_factory=session_factory,
        client=_client_with([row]),
    )

    assert stats["airports_created"] == 1
    assert stats["signals_created"] == 1

    with session_factory() as session:
        airport = session.scalar(select(Airport))
        assert airport.city == "Morristown"


def test_import_all_skips_unattributable_grants(session_factory):
    row = {
        "generated_internal_id": "ASST_NON_MI_1",
        "Award ID": "MI-1",
        "Recipient Name": "TRANSPORTATION, MICHIGAN DEPARTMENT OF",
        "Award Amount": 473277.0,
        "Description": MICHIGAN_DOT_DESCRIPTION,
        "Awarding Agency": "Department of Transportation",
        "Start Date": "2025-09-09",
    }
    stats = import_all(
        end_date=date(2026, 12, 31),
        session_factory=session_factory,
        client=_client_with([row]),
    )

    assert stats["unattributable"] == 1
    assert stats["signals_created"] == 0

    with session_factory() as session:
        assert session.scalars(select(Signal)).all() == []
        assert session.scalars(select(Source)).all() == []


def test_import_all_is_idempotent_on_rerun(session_factory):
    row = {
        "generated_internal_id": "ASST_NON_BGM_1",
        "Award ID": "BGM-1",
        "Recipient Name": "BROOME COUNTY",
        "Award Amount": 415226.0,
        "Description": BGM_DESCRIPTION,
        "Awarding Agency": "Department of Transportation",
        "Start Date": "2026-06-16",
    }
    with session_factory() as session:
        session.add(Airport(name="Greater Binghamton Airport", faa_code="BGM", city="Binghamton", state_region="New York", country="USA"))
        session.commit()

    first = import_all(end_date=date(2026, 12, 31), session_factory=session_factory, client=_client_with([row]))
    second = import_all(end_date=date(2026, 12, 31), session_factory=session_factory, client=_client_with([row]))

    assert first["signals_created"] == 1
    assert second["signals_created"] == 0
    assert second["already_imported"] == 1

    with session_factory() as session:
        assert len(session.scalars(select(Signal)).all()) == 1
        assert len(session.scalars(select(Source)).all()) == 1
