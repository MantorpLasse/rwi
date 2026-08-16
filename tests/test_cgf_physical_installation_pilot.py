from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Runway, Source, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink, PhysicalInstallationIdentity
from scripts.apply_cgf_physical_installation_pilot import dry_run, run


def seeded_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    airport = Airport(id=57, name="Cuyahoga", country="USA", faa_code="CGF")
    runway = Runway(id=58, airport=airport, designation="6/24")
    source = Source(title="CGF evidence", source_type="official")
    session.add_all((airport, runway, source))
    session.flush()
    session.add_all((
        SourceAssertion(id=54, source=source, airport=airport, assertion_type="airport_inventory", source_record_identifier="aggregate"),
        SourceAssertion(id=101, source=source, airport=airport, assertion_type="runway_end", source_record_identifier="end-06", raw_runway_value="6/24", raw_runway_end_value="06"),
        SourceAssertion(id=102, source=source, airport=airport, assertion_type="runway_end", source_record_identifier="end-24", raw_runway_value="6/24", raw_runway_end_value="24"),
        SourceAssertion(id=198, source=source, airport=airport, assertion_type="runway_end", source_record_identifier="nasr-06", raw_runway_value="06/24", raw_runway_end_value="06"),
        SourceAssertion(id=199, source=source, airport=airport, assertion_type="runway_end", source_record_identifier="nasr-24", raw_runway_value="06/24", raw_runway_end_value="24"),
    ))
    legacy = Installation(airport=airport, runway=runway, runway_end="06", notes="untouched legacy")
    session.add(legacy); session.commit()
    return engine, session, legacy


def test_cgf_dry_run_is_explicit_and_non_mutating():
    engine, session, legacy = seeded_session()
    before = (legacy.runway_end, legacy.notes)
    result = dry_run(session)
    assert result["blockers"] == []
    assert [item["runway_end"] for item in result["identities_would_create"]] == ["06", "24"]
    assert [item["assertion_id"] for item in result["links_would_create"]] == [101, 198, 102, 199]
    assert all(item["actor"] == "human:rwi-owner" and item["reason"] for item in result["links_would_create"])
    assert session.query(PhysicalInstallationIdentity).count() == session.query(InstallationAssertionLink).count() == 0
    assert (legacy.runway_end, legacy.notes) == before
    session.close(); engine.dispose()


def test_cgf_apply_is_idempotent_and_leaves_aggregate_unlinked():
    engine, session, legacy = seeded_session()
    run(session, apply=True)
    identities = session.query(PhysicalInstallationIdentity).order_by(PhysicalInstallationIdentity.runway_end).all()
    assert [(item.runway_id, item.runway_end) for item in identities] == [(None, "06"), (None, "24")]
    links = session.query(InstallationAssertionLink).all()
    by_assertion = {item.assertion_id: item.physical_installation.runway_end for item in links}
    assert by_assertion == {101: "06", 198: "06", 102: "24", 199: "24"}
    assert session.query(InstallationAssertionLink).filter_by(assertion_id=54).count() == 0
    assert (legacy.runway_end, legacy.notes) == ("06", "untouched legacy")
    run(session, apply=True)
    assert session.query(PhysicalInstallationIdentity).count() == 2
    assert session.query(InstallationAssertionLink).count() == 4
    session.close(); engine.dispose()
