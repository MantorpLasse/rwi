import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Source, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink, PhysicalInstallationIdentity
from scripts.apply_mdw_current_presence_pilot import NASR_EXTERNAL_ID, dry_run, run


def session_with_mdw(*, malformed=False):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    airport = Airport(id=12, name="Midway", country="USA", faa_code="MDW")
    nasr = Source(title="NASR", source_type="faa_nasr_apt_ars", external_id=NASR_EXTERNAL_ID)
    other = Source(title="Other", source_type="official")
    session.add_all((airport, nasr, other)); session.flush()
    for number, end in enumerate(("04R", "22L", "13L", "31R"), start=145):
        raw = {"ARPT_ID": "MDW", "ARREST_DEVICE_CODE": "EMAS", "EFF_DATE": "2026/08/06", "RWY_END_ID": end}
        session.add(SourceAssertion(
            id=number, source=nasr, airport=airport, assertion_type="runway_end",
            raw_runway_end_value=end, raw_relevant_text="bad" if malformed and end == "04R" else json.dumps(raw),
            source_record_identifier=f"nasr-{end}",
        ))
    session.add_all((
        SourceAssertion(id=103, source=other, airport=airport, assertion_type="runway_end", raw_runway_end_value="22L", source_record_identifier="historical"),
        SourceAssertion(id=26, source=other, airport=airport, assertion_type="airport_inventory", source_record_identifier="aggregate"),
    ))
    legacy = Installation(airport=airport, runway_end="22L", install_year=2014, type="greenEMAS")
    session.add(legacy); session.commit()
    return engine, session, legacy


def test_mdw_dry_run_resolves_four_current_nasr_ends_without_writes():
    engine, session, legacy = session_with_mdw()
    result = dry_run(session)
    assert result["blockers"] == []
    assert [item["assertion_id"] for item in result["evidence"]] == [145, 146, 147, 148]
    assert [item["runway_end"] for item in result["identities_would_create"]] == ["04R", "22L", "13L", "31R"]
    assert session.query(PhysicalInstallationIdentity).count() == session.query(InstallationAssertionLink).count() == 0
    assert legacy.install_year == 2014
    session.close(); engine.dispose()


def test_mdw_apply_links_only_current_assertions_and_is_idempotent():
    engine, session, legacy = session_with_mdw()
    run(session, apply=True)
    assert [(x.runway_id, x.runway_end) for x in session.query(PhysicalInstallationIdentity).order_by(PhysicalInstallationIdentity.runway_end)] == [
        (None, "04R"), (None, "13L"), (None, "22L"), (None, "31R")
    ]
    links = session.query(InstallationAssertionLink).all()
    assert {link.assertion_id for link in links} == {145, 146, 147, 148}
    assert all(link.outcome == "SAME_PHYSICAL_INSTALLATION" and link.actor == "human:rwi-owner" and link.reason for link in links)
    assert session.query(InstallationAssertionLink).filter(InstallationAssertionLink.assertion_id.in_((26, 103))).count() == 0
    assert legacy.install_year == 2014
    run(session, apply=True)
    assert session.query(PhysicalInstallationIdentity).count() == 4
    assert session.query(InstallationAssertionLink).count() == 4
    session.close(); engine.dispose()


def test_mdw_malformed_nasr_evidence_fails_closed():
    engine, session, _ = session_with_mdw(malformed=True)
    result = dry_run(session)
    assert result["blockers"]
    assert result["identities_would_create"] == []
    session.close(); engine.dispose()

