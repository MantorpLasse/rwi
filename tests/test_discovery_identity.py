"""RWI Mission #9D Part K - AirportIdentity validation tests."""

import pytest

from app.discovery.identity import AirportIdentity


def test_name_only_is_valid():
    identity = AirportIdentity(name="London City Airport")
    assert identity.name == "London City Airport"
    assert identity.iata_code is None
    assert identity.icao_code is None
    assert identity.city is None
    assert identity.country is None
    assert identity.aliases == ()


def test_full_identity_round_trips():
    identity = AirportIdentity(
        name="London City Airport",
        iata_code="LCY",
        icao_code="EGLC",
        city="London",
        country="United Kingdom",
        aliases=("LCA",),
    )
    assert identity.iata_code == "LCY"
    assert identity.icao_code == "EGLC"
    assert identity.city == "London"
    assert identity.country == "United Kingdom"
    assert identity.aliases == ("LCA",)


def test_blank_name_rejected():
    with pytest.raises(ValueError):
        AirportIdentity(name="")


def test_whitespace_only_name_rejected():
    with pytest.raises(ValueError):
        AirportIdentity(name="   ")


def test_fields_are_stripped():
    identity = AirportIdentity(name="  London City Airport  ", iata_code=" LCY ")
    assert identity.name == "London City Airport"
    assert identity.iata_code == "LCY"


def test_blank_optional_fields_normalize_to_none():
    identity = AirportIdentity(name="LCY", iata_code="   ", city="")
    assert identity.iata_code is None
    assert identity.city is None


def test_blank_aliases_are_dropped():
    identity = AirportIdentity(name="LCY", aliases=("LCA", "   ", ""))
    assert identity.aliases == ("LCA",)


def test_identity_is_frozen():
    identity = AirportIdentity(name="LCY")
    with pytest.raises(Exception):
        identity.name = "Something else"  # type: ignore[misc]


class _FakeAirport:
    def __init__(self, id_, name, iata_code, icao_code, city, country):
        self.id = id_
        self.name = name
        self.iata_code = iata_code
        self.icao_code = icao_code
        self.city = city
        self.country = country


def test_from_airport_without_session_has_no_aliases():
    fake = _FakeAirport(1, "Boston Logan International Airport", "BOS", "KBOS", "Boston", "United States")
    identity = AirportIdentity.from_airport(fake)
    assert identity.name == "Boston Logan International Airport"
    assert identity.iata_code == "BOS"
    assert identity.icao_code == "KBOS"
    assert identity.city == "Boston"
    assert identity.country == "United States"
    assert identity.aliases == ()


def test_from_airport_with_session_includes_admitted_aliases():
    """Real synthetic-DB test (this repo's own convention): with a session,
    from_airport() includes currently-ADMITTED governed AirportAlias
    strings via the existing read-only get_admitted_airport_aliases()
    helper - and never writes anything itself."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.database import Base
    from app.models import Airport, Source, SourceAssertion
    from app.models.airport_alias import AirportAlias

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        airport = Airport(name="Test Airport", country="Nowhere")
        session.add(airport)
        session.flush()

        source = Source(title="Alias registry", source_type="Authority", reliability_level="official")
        session.add(source)
        session.flush()

        assertion = SourceAssertion(
            source_id=source.id,
            airport_id=airport.id,
            assertion_type="airport_inventory",
            raw_relevant_text="Test Airport, also known as TSTA, official register entry.",
            source_record_identifier="rec-1",
            evidence_quality="direct_strong",
        )
        session.add(assertion)
        session.flush()

        alias = AirportAlias(
            airport_id=airport.id,
            alias="TSTA",
            source_id=source.id,
            source_assertion_id=assertion.id,
            evidence_excerpt="Test Airport, also known as TSTA, official register entry.",
            analyst="human:tester",
            evidence_class="AUTHORITATIVE_DIRECT",
            status="ADMITTED",
        )
        session.add(alias)
        session.commit()

        before_count = session.query(Airport).count()
        identity = AirportIdentity.from_airport(airport, session=session)
        after_count = session.query(Airport).count()

        assert identity.aliases == ("TSTA",)
        # from_airport is a pure read: it must not create/modify any rows.
        assert before_count == after_count
        assert len(session.new) == 0
        assert len(session.dirty) == 0
