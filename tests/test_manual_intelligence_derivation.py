from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    Document,
    Fact,
    FactStatus,
    FindingType,
    Intelligence,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import IntelligenceRepository
from app.services import IntelligenceDerivationService


@pytest.fixture
def derivation_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manual-intelligence.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def add_finding_type(session, key="CURRENT_EMAS", *, active=True):
    item = FindingType(
        key=key,
        name=key.replace("_", " ").title(),
        description=f"Governed description for {key}",
        category="STATUS",
        is_active=active,
    )
    session.add(item)
    session.commit()
    return item


def add_fact(
    session,
    key="airport.emas.product",
    value="EMASMAX",
    *,
    valid_from=None,
):
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {key}"),
                title=f"Document {key}",
            ),
            observation_type=ObservationType(
                key=key,
                display_label=key,
                description="Manual derivation evidence",
                value_type="raw_text",
            ),
            raw_value=value,
        ),
        status=VerificationStatus.ACCEPTED,
    )
    fact = Fact(
        fact_type_key=key,
        subject_type="airport",
        subject_identifier="FAA:JFK",
        accepted_value=value,
        valid_from=valid_from,
        status=FactStatus.ACCEPTED,
        supporting_verifications=[verification],
    )
    session.add(fact)
    session.commit()
    return fact


def retire_fact(session, original):
    retired = Fact(
        fact_type_key=original.fact_type_key,
        subject_type=original.subject_type,
        subject_identifier=original.subject_identifier,
        accepted_value=original.accepted_value,
        status=FactStatus.RETIRED,
        supersedes=original,
        supporting_verifications=list(original.supporting_verifications),
    )
    session.add(retired)
    session.commit()
    return retired


def supersede_fact(session, original):
    successor = Fact(
        fact_type_key=original.fact_type_key,
        subject_type=original.subject_type,
        subject_identifier=original.subject_identifier,
        accepted_value="greenEMAS",
        status=FactStatus.ACCEPTED,
        supersedes=original,
        supporting_verifications=list(original.supporting_verifications),
    )
    session.add(successor)
    session.commit()
    return successor


def valid_data(ids, **overrides):
    data = {
        "fact_ids": [str(item) for item in ids],
        "finding_type_key": "CURRENT_EMAS",
        "title": "  Current EMAS established  ",
        "summary": "First summary line.\nSecond summary line.",
    }
    data.update(overrides)
    return data


def intelligence_count(session_factory):
    with session_factory() as session:
        return session.scalar(select(func.count(Intelligence.id))) or 0


def test_get_form_displays_only_eligible_facts_and_active_finding_types(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        active_type = add_finding_type(session)
        inactive_type = add_finding_type(session, "NO_VERIFIED_EMAS", active=False)
        eligible = add_fact(session)
        superseded = add_fact(session, "airport.emas.superseded")
        supersede_fact(session, superseded)
        retired_original = add_fact(session, "airport.emas.retired")
        retired = retire_fact(session, retired_original)
        future = add_fact(
            session,
            "airport.emas.future",
            valid_from=date(2027, 1, 1),
        )

    response = client.get("/intelligence/derive")
    html = response.text
    assert response.status_code == 200
    assert f"Fact #{eligible.id}" in html
    assert "airport.emas.product" in html
    assert "FAA:JFK" in html
    assert "EMASMAX" in html
    for excluded in (superseded, retired, future):
        assert f"Fact #{excluded.id} ·" not in html
    assert active_type.key in html
    assert active_type.name in html
    assert active_type.category in html
    assert active_type.description in html
    assert inactive_type.key not in html


def test_fact_and_finding_type_preselection_are_governed(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        active_type = add_finding_type(session)
        inactive_type = add_finding_type(session, "NO_VERIFIED_EMAS", active=False)
        eligible = add_fact(session)
        historical = add_fact(session, "airport.emas.historical")
        supersede_fact(session, historical)

    selected = client.get(
        f"/intelligence/derive?fact_id={eligible.id}&finding_type={active_type.key}"
    )
    assert selected.status_code == 200
    assert f'value="{eligible.id}" checked' in selected.text
    assert f'value="{active_type.key}" required checked' in selected.text

    for url in (
        "/intelligence/derive?fact_id=999999",
        f"/intelligence/derive?fact_id={historical.id}",
        "/intelligence/derive?finding_type=UNKNOWN_TYPE",
        f"/intelligence/derive?finding_type={inactive_type.key}",
    ):
        assert client.get(url).status_code == 404


def test_single_derivation_uses_service_redirects_and_reaches_detail(derivation_app, monkeypatch):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        fact = add_fact(session)

    calls = []
    original_derive = IntelligenceDerivationService.derive

    def tracking_derive(service, finding_type_key, fact_ids, **values):
        calls.append((finding_type_key, tuple(fact_ids), values))
        return original_derive(service, finding_type_key, fact_ids, **values)

    monkeypatch.setattr(IntelligenceDerivationService, "derive", tracking_derive)
    response = client.post(
        "/intelligence/derive",
        data=valid_data([fact.id]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls == [
        (
            "CURRENT_EMAS",
            (fact.id,),
            {
                "title": "Current EMAS established",
                "summary": "First summary line.\nSecond summary line.",
            },
        )
    ]
    assert intelligence_count(session_factory) == 1
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "Current EMAS established" in detail.text
    assert "First summary line.\nSecond summary line." in detail.text


def test_multi_fact_derivation_creates_exactly_one_intelligence(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        first = add_fact(session)
        second = add_fact(session, "airport.emas.system_count", "2")

    response = client.post(
        "/intelligence/derive",
        data=valid_data([first.id, second.id]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        item = session.scalar(select(Intelligence))
        assert {fact.id for fact in item.supporting_facts} == {first.id, second.id}
    assert intelligence_count(session_factory) == 1


def test_validation_preserves_eligible_selections_title_and_multiline_summary(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        finding_type = add_finding_type(session)
        fact = add_fact(session)

    response = client.post(
        "/intelligence/derive",
        data=valid_data([fact.id], title="   "),
    )
    assert response.status_code == 422
    assert "title must contain non-whitespace text." in response.text
    assert f'value="{fact.id}" checked' in response.text
    assert f'value="{finding_type.key}" required checked' in response.text
    assert "First summary line.\nSecond summary line." in response.text
    assert intelligence_count(session_factory) == 0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (valid_data([]), "At least one Fact ID is required."),
        (valid_data([999999]), "Fact 999999 does not exist."),
    ],
)
def test_service_validation_errors_are_presented_without_creation(derivation_app, data, message):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)

    response = client.post("/intelligence/derive", data=data)
    assert response.status_code == 422
    assert message in response.text
    assert intelligence_count(session_factory) == 0


def test_empty_summary_and_unknown_finding_type_are_presented(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        fact = add_fact(session)

    empty_summary = client.post(
        "/intelligence/derive", data=valid_data([fact.id], summary="  \n")
    )
    assert empty_summary.status_code == 422
    assert "summary must contain non-whitespace text." in empty_summary.text
    assert 'value="Current EMAS established"' in empty_summary.text

    unknown_type = client.post(
        "/intelligence/derive",
        data=valid_data([fact.id], finding_type_key="UNKNOWN_TYPE"),
    )
    assert unknown_type.status_code == 422
    assert "FindingType" in unknown_type.text
    assert "UNKNOWN_TYPE" in unknown_type.text
    assert "does not exist." in unknown_type.text
    assert intelligence_count(session_factory) == 0


def test_duplicate_and_malformed_fact_ids_are_rejected(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        fact = add_fact(session)

    duplicate = client.post(
        "/intelligence/derive", data=valid_data([fact.id, fact.id])
    )
    assert duplicate.status_code == 422
    assert "Fact IDs must be unique." in duplicate.text

    malformed = client.post(
        "/intelligence/derive", data=valid_data([fact.id, "not-an-id"])
    )
    assert malformed.status_code == 422
    assert "Fact IDs must be valid integers." in malformed.text
    assert f'value="{fact.id}" checked' in malformed.text
    assert intelligence_count(session_factory) == 0


def test_manually_posted_ineligible_facts_and_inactive_type_are_rejected(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        inactive_type = add_finding_type(session, "NO_VERIFIED_EMAS", active=False)
        historical = add_fact(session, "airport.emas.historical")
        current = supersede_fact(session, historical)
        retired_original = add_fact(session, "airport.emas.retired")
        retired = retire_fact(session, retired_original)

    noncurrent = client.post(
        "/intelligence/derive", data=valid_data([historical.id])
    )
    assert noncurrent.status_code == 422
    assert f"Fact {historical.id} is not current." in noncurrent.text

    nonaccepted = client.post(
        "/intelligence/derive", data=valid_data([retired.id])
    )
    assert nonaccepted.status_code == 422
    assert f"Fact {retired.id} is not accepted." in nonaccepted.text

    inactive = client.post(
        "/intelligence/derive",
        data=valid_data([current.id], finding_type_key=inactive_type.key),
    )
    assert inactive.status_code == 422
    assert "is inactive." in inactive.text
    assert intelligence_count(session_factory) == 0


def test_route_delegates_without_direct_repository_or_transaction_calls(derivation_app, monkeypatch):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        fact = add_fact(session)

    calls = []

    def fake_derive(_service, finding_type_key, fact_ids, **values):
        calls.append((finding_type_key, tuple(fact_ids), values))
        return SimpleNamespace(id=42)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("route crossed the service transaction boundary")

    monkeypatch.setattr(IntelligenceDerivationService, "derive", fake_derive)
    monkeypatch.setattr(IntelligenceRepository, "create", forbidden)
    monkeypatch.setattr(Session, "commit", forbidden)
    monkeypatch.setattr(Session, "rollback", forbidden)

    response = client.post(
        "/intelligence/derive",
        data=valid_data([fact.id]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/intelligence/42"
    assert len(calls) == 1


def test_service_rollback_remains_authoritative(derivation_app, monkeypatch):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        fact = add_fact(session)

    def fail_create(_repository, _intelligence):
        raise RuntimeError("controlled persistence failure")

    monkeypatch.setattr(IntelligenceRepository, "create", fail_create)
    with pytest.raises(RuntimeError, match="controlled persistence failure"):
        client.post("/intelligence/derive", data=valid_data([fact.id]))
    assert intelligence_count(session_factory) == 0


def test_fact_navigation_is_enabled_only_for_eligible_current_facts(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        eligible = add_fact(session)
        historical = add_fact(session, "airport.emas.historical")
        successor = supersede_fact(session, historical)
        future = add_fact(
            session,
            "airport.emas.future",
            valid_from=date(2027, 1, 1),
        )

    eligible_html = client.get(f"/facts/{eligible.id}").text
    successor_html = client.get(f"/facts/{successor.id}").text
    assert f'/intelligence/derive?fact_id={eligible.id}' in eligible_html
    assert f'/intelligence/derive?fact_id={successor.id}' in successor_html
    for ineligible in (historical, future):
        html = client.get(f"/facts/{ineligible.id}").text
        assert f'/intelligence/derive?fact_id={ineligible.id}' not in html


def test_existing_intelligence_fact_and_verification_views_remain_available(derivation_app):
    client, session_factory = derivation_app
    with session_factory() as session:
        add_finding_type(session)
        fact = add_fact(session)
        verification_id = fact.supporting_verifications[0].id

    derived = client.post(
        "/intelligence/derive",
        data=valid_data([fact.id]),
        follow_redirects=False,
    )
    assert client.get("/intelligence").status_code == 200
    assert client.get(derived.headers["location"]).status_code == 200
    assert client.get("/facts").status_code == 200
    assert client.get(f"/facts/{fact.id}").status_code == 200
    assert client.get(f"/verifications/{verification_id}").status_code == 200
