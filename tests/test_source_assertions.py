from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Runway, Source, SourceAssertion
from app.models.source_assertion import ASSERTION_TYPES
from scripts.migrate_evidence_identity_slice1 import investigate, migrate


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def source_with_location(session):
    airport = Airport(name="Evidence Airport", country="USA")
    runway = Runway(airport=airport, designation="06/24")
    source = Source(title="FAA record", source_type="faa_tableau")
    session.add_all((airport, runway, source))
    session.commit()
    return airport, runway, source


def test_source_assertion_preserves_unknowns_and_source_relationship():
    engine, session = session_factory()
    airport, runway, source = source_with_location(session)
    assertion = SourceAssertion(
        source=source,
        airport=airport,
        runway=runway,
        assertion_type="runway_end",
        runway_end="06",
        raw_year_date_wording="1996(1999)/2007(2014)",
        source_record_identifier="mark-06",
        evidence_quality="direct_strong",
        review_state="reviewed",
    )
    session.add(assertion)
    session.commit()
    assert assertion.source is source
    assert assertion.airport is airport
    assert assertion.runway is runway
    assert assertion.raw_vendor_manufacturer_wording is None
    assert assertion.raw_year_date_wording == "1996(1999)/2007(2014)"
    assert session.query(Installation).count() == 0
    session.close(); engine.dispose()


def test_assertion_type_is_governed():
    assert ASSERTION_TYPES == (
        "airport_inventory", "runway", "runway_end", "physical_system", "historical", "project_construction"
    )
    engine, session = session_factory()
    _, _, source = source_with_location(session)
    session.add(SourceAssertion(source=source, assertion_type="invented", source_record_identifier="x"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.close(); engine.dispose()


def test_upstream_record_identity_rejects_duplicate_but_not_same_airport_type_year():
    engine, session = session_factory()
    airport, _, source = source_with_location(session)
    session.add_all((
        SourceAssertion(source=source, airport=airport, assertion_type="airport_inventory", raw_product_type="EMASMAX", raw_year_date_wording="2018", source_record_identifier="row-1"),
        SourceAssertion(source=source, airport=airport, assertion_type="airport_inventory", raw_product_type="EMASMAX", raw_year_date_wording="2018", source_record_identifier="row-2"),
    ))
    session.commit()
    assert session.query(SourceAssertion).count() == 2
    session.add(SourceAssertion(source=source, airport=airport, assertion_type="airport_inventory", source_record_identifier="row-1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.close(); engine.dispose()


def test_locator_and_fragment_hash_form_fallback_identity():
    engine, session = session_factory()
    _, _, source = source_with_location(session)
    fields = dict(source=source, assertion_type="airport_inventory", artifact_identity="sha256:artifact", source_locator="sheet=Main;mark=1", raw_fragment_hash="sha256:fragment")
    session.add(SourceAssertion(**fields))
    session.commit()
    session.add(SourceAssertion(**fields))
    with pytest.raises(IntegrityError):
        session.commit()
    session.close(); engine.dispose()


def test_identity_requires_record_id_or_locator_and_fragment_hash():
    engine, session = session_factory()
    _, _, source = source_with_location(session)
    session.add(SourceAssertion(source=source, assertion_type="airport_inventory"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.close(); engine.dispose()


def test_migration_preserves_core_rows_and_repairs_foreign_keys(tmp_path: Path):
    database = tmp_path / "slice1.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO airports (name, country, created_at, updated_at) VALUES ('A', 'USA', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO sources (title, source_type, reliability_level) VALUES ('S', 'news', 'official')"))
        connection.execute(text("INSERT INTO installations (airport_id, source_id) VALUES (1, 1)"))
    engine.dispose()
    before = investigate(database)
    migrate(database)
    after = investigate(database)
    assert before["counts"]["installations"] == after["counts"]["installations"] == 1
    assert after["foreign_key_check"] == []
    assert all(any(fk[2] == "sources" for fk in fks) for fks in after["foreign_key_lists"].values())
