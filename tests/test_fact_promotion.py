import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Document,
    Fact,
    FactStatus,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import FactRepository
from app.services import FactPromotionError, FactPromotionService


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        test_engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


def add_type(session: Session, key="airport.emas.product") -> ObservationType:
    observation_type = ObservationType(
        key=key,
        display_label=key,
        description="Promotion test type",
        value_type="raw_text",
    )
    session.add(observation_type)
    session.flush()
    return observation_type


def add_verification(
    session: Session,
    observation_type: ObservationType,
    *,
    raw_value="EMASMAX",
    normalized_value=None,
    status=VerificationStatus.ACCEPTED,
    suffix="one",
) -> Verification:
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
    )
    session.add(verification)
    session.commit()
    return verification


def promote(service, ids, **overrides):
    values = {
        "subject_type": "airport",
        "subject_identifier": "FAA:JFK",
        "accepted_value": "EMASMAX",
    }
    values.update(overrides)
    return service.promote(ids, **values)


def fact_count(session: Session) -> int:
    return session.scalar(select(func.count(Fact.id))) or 0


def test_successful_promotion_creates_one_immutable_fact(engine):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)
        original_raw = verification.observation.raw_value
        original_status = verification.status

        fact = promote(FactPromotionService(session), [verification.id])

        assert fact.id is not None
        assert fact.fact_type_key == observation_type.key
        assert fact.subject_type == "airport"
        assert fact.subject_identifier == "FAA:JFK"
        assert fact.accepted_value == "EMASMAX"
        assert fact.status is FactStatus.ACCEPTED
        assert fact.supporting_verifications == [verification]
        assert fact_count(session) == 1
        assert verification.status is original_status
        assert verification.observation.raw_value == original_raw

        fact.accepted_value = "changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()


def test_multiple_accepted_verifications_support_one_fact(engine):
    with Session(engine) as session:
        observation_type = add_type(session)
        first = add_verification(session, observation_type, suffix="first")
        second = add_verification(
            session,
            observation_type,
            normalized_value="EMASMAX",
            raw_value="EMASMAX source spelling",
            suffix="second",
        )

        fact = promote(FactPromotionService(session), [first.id, second.id])
        assert fact.supporting_verifications == [first, second]
        assert fact_count(session) == 1


def test_duplicate_or_empty_verification_ids_are_rejected(engine):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)
        service = FactPromotionService(session)

        with pytest.raises(FactPromotionError) as duplicate:
            promote(service, [verification.id, verification.id])
        assert duplicate.value.code == "duplicate_verification_ids"

        with pytest.raises(FactPromotionError) as empty:
            promote(service, [])
        assert empty.value.code == "verification_ids_required"
        assert fact_count(session) == 0


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.REJECTED,
        VerificationStatus.PENDING,
        VerificationStatus.UNDECIDED,
    ],
)
def test_non_accepted_verification_is_rejected(engine, status):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type, status=status)

        with pytest.raises(FactPromotionError) as error:
            promote(FactPromotionService(session), [verification.id])
        assert error.value.code == "verification_not_accepted"
        assert fact_count(session) == 0


def test_missing_verification_is_rejected(engine):
    with Session(engine) as session:
        with pytest.raises(FactPromotionError) as error:
            promote(FactPromotionService(session), [999999])
        assert error.value.code == "verification_not_found"
        assert fact_count(session) == 0


def test_conflicting_values_are_rejected_without_resolution(engine):
    with Session(engine) as session:
        observation_type = add_type(session)
        first = add_verification(session, observation_type, suffix="first")
        second = add_verification(
            session,
            observation_type,
            raw_value="greenEMAS",
            suffix="second",
        )

        with pytest.raises(FactPromotionError) as error:
            promote(FactPromotionService(session), [first.id, second.id])
        assert error.value.code == "conflicting_verification_values"
        assert fact_count(session) == 0


def test_different_fact_types_are_rejected_as_non_atomic_support(engine):
    with Session(engine) as session:
        first_type = add_type(session)
        first = add_verification(session, first_type, suffix="first")
        second_type = add_type(session, key="airport.emas.product.other")
        second = add_verification(session, second_type, suffix="second")

        with pytest.raises(FactPromotionError) as error:
            promote(FactPromotionService(session), [first.id, second.id])
        assert error.value.code == "conflicting_verification_types"
        assert fact_count(session) == 0


def test_explicit_accepted_value_must_match_reviewed_value(engine):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)

        with pytest.raises(FactPromotionError) as error:
            promote(
                FactPromotionService(session),
                [verification.id],
                accepted_value="different",
            )
        assert error.value.code == "accepted_value_mismatch"
        assert fact_count(session) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_type", ""),
        ("subject_identifier", "  "),
        ("accepted_value", "\n"),
    ],
)
def test_required_fact_statement_inputs_are_validated(engine, field, value):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)

        with pytest.raises(FactPromotionError) as error:
            promote(
                FactPromotionService(session),
                [verification.id],
                **{field: value},
            )
        assert error.value.code == f"{field}_required"
        assert fact_count(session) == 0


def test_every_promotion_creates_a_new_fact_and_reuses_repository(engine, monkeypatch):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)
        calls = []
        original_create = FactRepository.create

        def tracking_create(repository, fact):
            calls.append(fact)
            return original_create(repository, fact)

        monkeypatch.setattr(FactRepository, "create", tracking_create)
        service = FactPromotionService(session)
        first = promote(service, [verification.id])
        second = promote(service, [verification.id])

        assert first.id != second.id
        assert len(calls) == 2
        assert fact_count(session) == 2


def test_repository_failure_rolls_back_and_does_not_modify_inputs(engine, monkeypatch):
    with Session(engine) as session:
        observation_type = add_type(session)
        verification = add_verification(session, observation_type)
        observation_id = verification.observation.id

        def fail_create(_repository, _fact):
            raise RuntimeError("persistence failure")

        monkeypatch.setattr(FactRepository, "create", fail_create)
        with pytest.raises(RuntimeError, match="persistence failure"):
            promote(FactPromotionService(session), [verification.id])

        assert fact_count(session) == 0
        assert session.get(Verification, verification.id).status is VerificationStatus.ACCEPTED
        assert session.get(Observation, observation_id).raw_value == "EMASMAX"
