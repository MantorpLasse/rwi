import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    Document,
    Fact,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import FactRepository
from app.services import FactPromotionService


@pytest.fixture
def promotion_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manual-fact-promotion.sqlite3'}",
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


def add_type(session, key="airport.emas.product"):
    observation_type = ObservationType(
        key=key,
        display_label=key,
        description="Promotion UI test",
        value_type="raw_text",
    )
    session.add(observation_type)
    session.flush()
    return observation_type


def add_verification(
    session,
    observation_type,
    *,
    status=VerificationStatus.ACCEPTED,
    raw_value="EMASMAX",
    normalized_value=None,
    reviewer=None,
    suffix="one",
):
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {suffix}"),
                title=f"Document {suffix}",
            ),
            observation_type=observation_type,
            raw_value=raw_value,
            normalized_value=normalized_value,
        ),
        status=status,
        reviewed_by=reviewer,
    )
    session.add(verification)
    session.commit()
    return verification


def valid_data(ids, **overrides):
    data = {
        "verification_ids": [str(item) for item in ids],
        "subject_type": "airport",
        "subject_identifier": "FAA:JFK",
        "accepted_value": "EMASMAX",
    }
    data.update(overrides)
    return data


def fact_count(session_factory):
    with session_factory() as session:
        return session.scalar(select(func.count(Fact.id))) or 0


def test_get_form_displays_only_eligible_accepted_verifications(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        accepted = add_verification(
            session,
            observation_type,
            reviewer="Eligible reviewer",
            suffix="accepted",
        )
        rejected = add_verification(
            session,
            observation_type,
            status=VerificationStatus.REJECTED,
            raw_value="REJECTED VALUE MUST STAY HIDDEN",
            suffix="rejected",
        )

    response = client.get("/facts/promote")
    html = response.text
    assert response.status_code == 200
    assert f"Verification #{accepted.id}" in html
    assert f"Observation #{accepted.observation_id}" in html
    assert "airport.emas.product" in html
    assert "EMASMAX" in html
    assert "Eligible reviewer" in html
    assert "ACCEPTED" in html
    assert f"Verification #{rejected.id}" not in html
    assert "REJECTED VALUE MUST STAY HIDDEN" not in html


def test_valid_preselection_checks_verification_and_prefills_value(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        verification = add_verification(
            session,
            observation_type,
            normalized_value="EMASMAX",
            raw_value="source spelling",
        )

    html = client.get(f"/facts/promote?verification_id={verification.id}").text
    assert f'value="{verification.id}" checked' in html
    assert ">EMASMAX</textarea>" in html


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.PENDING,
        VerificationStatus.REJECTED,
        VerificationStatus.UNDECIDED,
    ],
)
def test_invalid_or_nonaccepted_preselection_returns_404(promotion_app, status):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type, status=status)

    assert (
        client.get(f"/facts/promote?verification_id={verification.id}").status_code
        == 404
    )
    assert client.get("/facts/promote?verification_id=999999").status_code == 404


def test_successful_single_promotion_uses_service_and_redirects_to_reachable_fact(
    promotion_app, monkeypatch
):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)

    calls = []
    original_promote = FactPromotionService.promote

    def tracking_promote(service, verification_ids, **values):
        calls.append((tuple(verification_ids), values))
        return original_promote(service, verification_ids, **values)

    monkeypatch.setattr(FactPromotionService, "promote", tracking_promote)
    response = client.post(
        "/facts/promote",
        data=valid_data([verification.id]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls[0][0] == (verification.id,)
    assert calls[0][1]["subject_type"] == "airport"
    assert fact_count(session_factory) == 1
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "FAA:JFK" in detail.text
    assert "EMASMAX" in detail.text


def test_successful_multi_verification_promotion_creates_one_fact(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        first = add_verification(session, observation_type, suffix="first")
        second = add_verification(
            session,
            observation_type,
            raw_value="source spelling",
            normalized_value="EMASMAX",
            suffix="second",
        )

    response = client.post(
        "/facts/promote",
        data=valid_data([first.id, second.id]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        fact = session.scalar(select(Fact))
        assert {item.id for item in fact.supporting_verifications} == {
            first.id,
            second.id,
        }


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        ([], "At least one Verification ID is required."),
        ([1, 1], "Verification IDs must be unique."),
        ([999999], "Verification 999999 does not exist."),
    ],
)
def test_invalid_id_selection_redisplays_form_without_fact(
    promotion_app, ids, expected
):
    client, session_factory = promotion_app
    if ids == [1, 1]:
        with session_factory() as session:
            observation_type = add_type(session)
            verification = add_verification(session, observation_type)
            ids = [verification.id, verification.id]

    response = client.post("/facts/promote", data=valid_data(ids))
    assert response.status_code == 422
    assert expected in response.text
    assert "FAA:JFK" in response.text
    assert "EMASMAX" in response.text
    assert fact_count(session_factory) == 0


def test_nonaccepted_verification_posted_manually_is_rejected(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        rejected = add_verification(
            session, observation_type, status=VerificationStatus.REJECTED
        )

    response = client.post(
        "/facts/promote", data=valid_data([rejected.id])
    )
    assert response.status_code == 422
    assert f"Verification {rejected.id} is not accepted." in response.text
    assert fact_count(session_factory) == 0


def test_conflicting_types_and_values_are_presented_as_service_errors(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        first_type = add_type(session)
        first = add_verification(session, first_type, suffix="first")
        other_type = add_type(session, key="airport.emas.other")
        other = add_verification(session, other_type, suffix="other")

    type_response = client.post(
        "/facts/promote", data=valid_data([first.id, other.id])
    )
    assert type_response.status_code == 422
    assert "do not support one Fact type" in type_response.text

    with session_factory() as session:
        same_type = session.get(ObservationType, first_type.id)
        conflict = add_verification(
            session, same_type, raw_value="greenEMAS", suffix="conflict"
        )
    value_response = client.post(
        "/facts/promote", data=valid_data([first.id, conflict.id])
    )
    assert value_response.status_code == 422
    assert "conflicting Observation values" in value_response.text
    assert fact_count(session_factory) == 0


def test_mismatched_value_and_empty_subjects_preserve_entered_fields(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)

    mismatch = client.post(
        "/facts/promote",
        data=valid_data(
            [verification.id],
            subject_type="  airport  ",
            subject_identifier="  FAA:JFK  ",
            accepted_value="Different candidate",
        ),
    )
    assert mismatch.status_code == 422
    assert "does not match" in mismatch.text
    assert 'value="airport"' in mismatch.text
    assert 'value="FAA:JFK"' in mismatch.text
    assert "Different candidate" in mismatch.text
    assert f'value="{verification.id}" checked' in mismatch.text

    for field in ("subject_type", "subject_identifier"):
        response = client.post(
            "/facts/promote",
            data=valid_data([verification.id], **{field: "   "}),
        )
        assert response.status_code == 422
        assert f"{field} must contain" in response.text
    assert fact_count(session_factory) == 0


def test_promotion_service_rollback_remains_authoritative(promotion_app, monkeypatch):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)

    def fail_create(_repository, _fact):
        raise RuntimeError("controlled persistence failure")

    monkeypatch.setattr(FactRepository, "create", fail_create)
    with pytest.raises(RuntimeError, match="controlled persistence failure"):
        client.post("/facts/promote", data=valid_data([verification.id]))
    assert fact_count(session_factory) == 0


def test_promotion_navigation_respects_verification_status(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        accepted = add_verification(session, observation_type, suffix="accepted")
        pending = add_verification(
            session,
            observation_type,
            status=VerificationStatus.PENDING,
            suffix="pending",
        )

    accepted_html = client.get(f"/verifications/{accepted.id}").text
    pending_html = client.get(f"/verifications/{pending.id}").text
    assert f'/facts/promote?verification_id={accepted.id}' in accepted_html
    assert "/facts/promote" not in pending_html
    assert 'href="/facts/promote"' in client.get("/facts").text


def test_existing_fact_and_verification_pages_remain_available(promotion_app):
    client, session_factory = promotion_app
    with session_factory() as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)

    promoted = client.post(
        "/facts/promote",
        data=valid_data([verification.id]),
        follow_redirects=False,
    )
    assert client.get("/facts").status_code == 200
    assert client.get(promoted.headers["location"]).status_code == 200
    assert client.get(f"/verifications/{verification.id}").status_code == 200
