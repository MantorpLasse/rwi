from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, PhysicalInstallationIdentity, Runway, Source, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink
from app.services.physical_installation_reconciliation import (
    create_physical_installation_identity,
    record_reconciliation_decision,
)
from scripts.migrate_evidence_identity_slice6c import downgrade, inspect, upgrade


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def fixture(session):
    airport = Airport(name="CGF-shaped", country="USA")
    runway = Runway(airport=airport, designation="06/24")
    source = Source(title="Official source", source_type="official")
    session.add_all((airport, runway, source))
    session.flush()
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="runway_end",
        source_record_identifier="CGF-06", raw_runway_end_value="06",
    )
    aggregate = SourceAssertion(
        source=source, airport=airport, assertion_type="airport_inventory",
        source_record_identifier="CGF-aggregate",
    )
    session.add_all((assertion, aggregate)); session.commit()
    return airport, runway, assertion, aggregate


def test_identity_placement_and_multiple_same_end_are_permitted():
    engine, session = make_session()
    airport, runway, _, _ = fixture(session)
    one = create_physical_installation_identity(session, airport_id=airport.id, runway_id=runway.id, runway_end="06")
    two = create_physical_installation_identity(session, airport_id=airport.id, runway_id=runway.id, runway_end="06")
    unknown = create_physical_installation_identity(session, airport_id=airport.id)
    session.commit()
    assert (one.runway_end, two.runway_end, unknown.runway_id) == ("06", "06", None)
    assert session.query(PhysicalInstallationIdentity).count() == 3
    session.close(); engine.dispose()


def test_identity_rejects_missing_airport_or_foreign_runway():
    engine, session = make_session()
    airport, runway, _, _ = fixture(session)
    other = Airport(name="Other", country="USA"); session.add(other); session.commit()
    with pytest.raises(ValueError, match="Airport"):
        create_physical_installation_identity(session, airport_id=999)
    with pytest.raises(ValueError, match="runway"):
        create_physical_installation_identity(session, airport_id=other.id, runway_id=runway.id)
    session.close(); engine.dispose()


def test_reviewed_outcomes_supersession_and_assertion_immutability():
    engine, session = make_session()
    airport, runway, assertion, _ = fixture(session)
    identity = create_physical_installation_identity(session, airport_id=airport.id, runway_id=runway.id, runway_end="06")
    session.flush()
    before = (assertion.raw_runway_end_value, assertion.source_record_identifier)
    same = record_reconciliation_decision(
        session, assertion_id=assertion.id, physical_installation_id=identity.id,
        outcome="SAME_PHYSICAL_INSTALLATION", reason="Direct end-specific evidence.", actor="human:researcher",
    )
    session.commit()
    unresolved = record_reconciliation_decision(
        session, assertion_id=assertion.id, physical_installation_id=None,
        outcome="UNRESOLVED", reason="Later conflicting evidence.", actor="human:researcher",
        supersedes_link_id=same.id,
    )
    session.commit()
    assert unresolved.supersedes_link_id == same.id
    assert (assertion.raw_runway_end_value, assertion.source_record_identifier) == before
    same.reason = "mutated"
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


def test_governed_target_and_no_automatic_aggregate_promotion():
    engine, session = make_session()
    airport, runway, assertion, aggregate = fixture(session)
    identity = create_physical_installation_identity(session, airport_id=airport.id, runway_id=runway.id, runway_end="06")
    session.flush()
    with pytest.raises(ValueError, match="governed"):
        record_reconciliation_decision(session, assertion_id=assertion.id, physical_installation_id=identity.id, outcome="bad", reason="x", actor="human:x")
    with pytest.raises(ValueError, match="requires"):
        record_reconciliation_decision(session, assertion_id=assertion.id, physical_installation_id=None, outcome="SAME_PHYSICAL_INSTALLATION", reason="x", actor="human:x")
    assert session.query(InstallationAssertionLink).filter_by(assertion_id=aggregate.id).count() == 0
    session.close(); engine.dispose()


def test_isolated_migration_upgrade_and_downgrade(tmp_path: Path):
    database = tmp_path / "slice6c.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine, tables=[
        Base.metadata.tables["airports"], Base.metadata.tables["runways"],
        Base.metadata.tables["sources"], Base.metadata.tables["source_assertions"],
    ])
    engine.dispose()
    upgrade(database)
    after = inspect(database)
    assert after["tables"] == {
        "physical_installation_identities": True, "installation_assertion_links": True,
    }
    assert after["counts"] == {
        "physical_installation_identities": 0, "installation_assertion_links": 0,
    }
    downgrade(database)
    assert all(not value for value in inspect(database)["tables"].values())

