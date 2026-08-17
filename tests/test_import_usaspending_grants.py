from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.usaspending_grants import UsaspendingGrant
from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from scripts.import_usaspending_grants import (
    RESOLVED_EXISTING,
    RESOLVED_NEW,
    UNRESOLVED,
    _format_amount,
    classify_category,
    ensure_source_external_id_column,
    import_all,
    resolve_airport,
    signal_title,
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
# Structurally identical to the real Allegheny County Airport Authority case
# (docs/domain/allegheny-unresolved-airport-investigation.md,
# docs/domain/usaspending-airport-resolution-fail-closed-report.md): no
# embedded Loc ID, a recipient that is an operating AUTHORITY rather than
# the airport itself, and a beneficiary city/state with no existing match -
# but using a fictional city/authority so it never touches production
# Airport id 75.
RIVERTON_AUTHORITY_DESCRIPTION = (
    "PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA. THIS PROJECT CONSTRUCTS "
    "THE RUNWAY 9/27 SAFETY AREA to enhance and improve the level of safety "
    "of operations at the airport. THIS GRANT FUNDS THE PROCUREMENT OF "
    "ENGINEERED MATERIAL ARRESTING SYSTEM. INTENDED BENEFICIARY: THIS GRANT "
    "WILL PROVIDE FEDERAL FUNDING FOR AIRPORTS ASSOCIATED WITH RIVERTON, "
    "WYOMING."
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


def test_format_amount_scales_to_millions_thousands_or_raw():
    assert _format_amount(Decimal("56187750")) == "$56.2M"
    assert _format_amount(Decimal("9360499")) == "$9.4M"
    assert _format_amount(Decimal("60311.22")) == "$60K"
    assert _format_amount(Decimal("500")) == "$500"
    assert _format_amount(None) == "okänt belopp"


def test_signal_title_is_unique_per_grant_not_per_recipient():
    grant_a = make_grant(BGM_DESCRIPTION, recipient_name="Massachusetts Port Authority", amount="4100000", start="2023-05-01")
    grant_b = make_grant(BGM_DESCRIPTION, recipient_name="Massachusetts Port Authority", amount="9600000", start="2025-03-01")

    title_a = signal_title(grant_a)
    title_b = signal_title(grant_b)

    assert title_a == "USAspending grant — $4.1M, FY2023"
    assert title_b == "USAspending grant — $9.6M, FY2025"
    assert title_a != title_b  # same recipient, different grants - titles must differ


def test_signal_title_without_start_date_omits_fiscal_year():
    grant = make_grant(BGM_DESCRIPTION, amount="1000000", start=None)
    assert signal_title(grant) == "USAspending grant — $1.0M"


def test_classify_category_detects_replacement_keywords():
    assert classify_category("RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM") == "replacement"
    assert classify_category("REPLACE THE ENGINEERED MATERIAL ARRESTING SYSTEM") == "replacement"
    assert classify_category("CONSTRUCT A NEW ENGINEERED MATERIAL ARRESTING SYSTEM") == "new_installation"


def test_resolve_airport_matches_existing_via_embedded_loc_id(session_factory):
    with session_factory() as session:
        airport = Airport(name="Cartersville", faa_code="VPC", country="USA")
        session.add(airport)
        session.commit()

        resolution = resolve_airport(session, make_grant(GEORGIA_DOT_DESCRIPTION))

        assert resolution.status == RESOLVED_EXISTING
        assert resolution.airport.id == airport.id


def test_resolve_airport_matches_existing_via_beneficiary_city_state(session_factory):
    with session_factory() as session:
        airport = Airport(name="Greater Binghamton Airport", faa_code="BGM", city="Binghamton", state_region="New York", country="USA")
        session.add(airport)
        session.commit()

        resolution = resolve_airport(session, make_grant(BGM_DESCRIPTION))

        assert resolution.status == RESOLVED_EXISTING
        assert resolution.airport.id == airport.id


def test_resolve_airport_fails_closed_when_beneficiary_city_state_has_no_existing_airport(session_factory):
    """The historical bad fallback: no embedded Loc ID, a beneficiary
    city/state with no existing Airport. Previously created a new Airport
    named after the grant recipient (the Allegheny County Airport Authority
    bug); must now fail closed instead - see
    docs/domain/usaspending-airport-resolution-fail-closed-report.md."""
    with session_factory() as session:
        resolution = resolve_airport(session, make_grant(MORRISTOWN_DESCRIPTION, recipient_name="Town of Morristown"))

        assert resolution.status == UNRESOLVED
        assert resolution.airport is None
        assert resolution.raw_identifier == "Morristown, New Jersey"
        assert resolution.raw_name == "Town Of Morristown"
        assert len(session.scalars(select(Airport)).all()) == 0  # no Airport fabricated


def test_resolve_airport_fails_closed_for_allegheny_shaped_authority_recipient(session_factory):
    """Reproduces the exact shape of the real Allegheny County Airport
    Authority bug (recipient is an operating AUTHORITY, not the airport
    itself) with a fictional airport/city so it never depends on production
    Airport id 75. An authority can operate more than one facility, so its
    name is never sufficient airport identity on its own."""
    with session_factory() as session:
        grant = make_grant(
            RIVERTON_AUTHORITY_DESCRIPTION,
            recipient_name="Fremont County Airport Authority",
            external_id="ASST_NON_TEST_RIVERTON_1",
        )
        resolution = resolve_airport(session, grant)

        assert resolution.status == UNRESOLVED
        assert resolution.airport is None
        assert resolution.raw_name == "Fremont County Airport Authority"
        assert resolution.raw_identifier == "Riverton, Wyoming"
        assert "recipient organization name alone is not sufficient" in resolution.reason
        assert len(session.scalars(select(Airport)).all()) == 0


def test_resolve_airport_creates_new_airport_from_embedded_loc_id_when_unknown(session_factory):
    with session_factory() as session:
        resolution = resolve_airport(session, make_grant(GEORGIA_DOT_DESCRIPTION))

        assert resolution.status == RESOLVED_NEW
        assert resolution.airport.faa_code == "VPC"


def test_resolve_airport_fails_closed_when_ambiguous(session_factory):
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
        resolution = resolve_airport(session, grant)

        assert resolution.status == UNRESOLVED
        assert resolution.airport is None
        assert "ambiguous" in resolution.reason
        assert len(session.scalars(select(Airport)).all()) == 2  # no duplicate created


def test_resolve_airport_fails_closed_when_unattributable(session_factory):
    with session_factory() as session:
        resolution = resolve_airport(session, make_grant(MICHIGAN_DOT_DESCRIPTION))

        assert resolution.status == UNRESOLVED
        assert resolution.airport is None
        assert resolution.raw_identifier is None  # no beneficiary sentence to extract at all
        assert "no beneficiary city/state sentence" in resolution.reason


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


def test_import_all_fails_closed_and_preserves_evidence_for_beneficiary_only_match(session_factory):
    """The historical bad case: no Loc ID, a beneficiary city/state with no
    existing Airport. Previously fabricated a new Airport named after the
    recipient (Allegheny bug); must now stay UNRESOLVED while still keeping
    the grant's evidence - see
    docs/domain/usaspending-airport-resolution-fail-closed-report.md."""
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

    assert stats["airports_created"] == 0
    assert stats["signals_created"] == 0
    assert stats["unattributable"] == 1

    with session_factory() as session:
        assert session.scalars(select(Airport)).all() == []  # no fabricated Airport
        assert session.scalars(select(Signal)).all() == []  # Signal.airport_id is required

        source = session.scalar(select(Source))
        assert source is not None
        assert source.title == "USAspending grant: Town Of Morristown"
        assert source.summary == MORRISTOWN_DESCRIPTION
        assert source.document_reference == "MORRIS-1"
        assert source.url == "https://www.usaspending.gov/award/ASST_NON_MORRIS_1"
        assert source.external_id == "usaspending:ASST_NON_MORRIS_1"

        assertion = session.scalar(select(SourceAssertion))
        assert assertion is not None
        assert assertion.source_id == source.id
        assert assertion.airport_id is None
        assert assertion.assertion_type == "project_construction"
        assert assertion.raw_airport_name == "Town Of Morristown"
        assert assertion.raw_airport_identifier == "Morristown, New Jersey"
        assert assertion.raw_relevant_text == MORRISTOWN_DESCRIPTION
        assert assertion.evidence_quality == "unverified_candidate"
        assert assertion.review_state == "unreviewed"


def test_import_all_fails_closed_for_allegheny_shaped_authority_recipient(session_factory):
    """Full-pipeline reproduction of the real Allegheny County Airport
    Authority bug, using a fictional airport/city so it never depends on
    production Airport id 75. The grant's own text (recipient, runway
    designation, city/state) must remain preserved and reviewable."""
    row = {
        "generated_internal_id": "ASST_NON_TEST_RIVERTON_1",
        "Award ID": "TEST-RIVERTON-AWARD",
        "Recipient Name": "Fremont County Airport Authority",
        "Award Amount": 2832935.0,
        "Description": RIVERTON_AUTHORITY_DESCRIPTION,
        "Awarding Agency": "Department of Transportation",
        "Start Date": "2023-08-24",
    }
    stats = import_all(
        end_date=date(2026, 12, 31),
        session_factory=session_factory,
        client=_client_with([row]),
    )

    assert stats["airports_created"] == 0
    assert stats["signals_created"] == 0
    assert stats["unattributable"] == 1

    with session_factory() as session:
        assert session.scalars(select(Airport)).all() == []  # no "Fremont County Airport Authority" Airport row

        assertion = session.scalar(select(SourceAssertion))
        assert assertion.raw_airport_name == "Fremont County Airport Authority"
        assert assertion.raw_airport_identifier == "Riverton, Wyoming"
        assert "RUNWAY 9/27" in assertion.raw_relevant_text


def test_import_all_preserves_evidence_when_totally_unattributable(session_factory):
    """No Loc ID and no beneficiary sentence at all (a pure state
    block-grant-administration record) - previously left zero trace in the
    database. The Source (and an unresolved SourceAssertion) must now be
    preserved even though nothing about the airport could be extracted."""
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
        assert session.scalars(select(Airport)).all() == []

        source = session.scalar(select(Source))
        assert source is not None  # evidence preserved, not silently dropped
        assert source.summary == MICHIGAN_DOT_DESCRIPTION

        assertion = session.scalar(select(SourceAssertion))
        assert assertion.airport_id is None
        assert assertion.raw_airport_identifier is None  # nothing to extract
        assert assertion.raw_airport_name == "Transportation, Michigan Department Of"


def test_import_all_does_not_manufacture_duplicate_unresolved_rows_on_rerun(session_factory):
    row = {
        "generated_internal_id": "ASST_NON_MORRIS_1",
        "Award ID": "MORRIS-1",
        "Recipient Name": "TOWN OF MORRISTOWN",
        "Award Amount": 5756758.0,
        "Description": MORRISTOWN_DESCRIPTION,
        "Awarding Agency": "Department of Transportation",
        "Start Date": "2025-09-19",
    }
    first = import_all(end_date=date(2026, 12, 31), session_factory=session_factory, client=_client_with([row]))
    second = import_all(end_date=date(2026, 12, 31), session_factory=session_factory, client=_client_with([row]))

    assert first["unattributable"] == 1
    assert second["unattributable"] == 0
    assert second["already_imported"] == 1

    with session_factory() as session:
        assert session.scalars(select(Airport)).all() == []
        assert len(session.scalars(select(Source)).all()) == 1
        assert len(session.scalars(select(SourceAssertion)).all()) == 1


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
