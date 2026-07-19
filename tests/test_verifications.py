from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Document,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import VerificationRepository


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


def make_observation(session: Session) -> Observation:
    document = Document(source=PublishingSource(name="FAA"), title="EMAS map")
    observation_type = ObservationType(
        key="airport.emas.product",
        display_label="Product",
        description="Reported product",
        value_type="raw_text",
    )
    observation = Observation(
        document=document,
        observation_type=observation_type,
        raw_value="EMASMAX",
    )
    session.add(observation)
    session.commit()
    return observation


def test_verification_model_and_optional_fields(engine):
    with Session(engine) as session:
        observation = make_observation(session)
        verification = Verification(
            observation=observation,
            status=VerificationStatus.PENDING,
        )
        session.add(verification)
        session.commit()

        assert verification.id is not None
        assert verification.status is VerificationStatus.PENDING
        assert verification.reviewed_by is None
        assert verification.comment is None
        assert verification.confidence is None
        assert verification.reviewed_at is not None
        assert verification.created_at is not None
        assert verification.observation is observation
        assert observation.verifications == [verification]


def test_repository_create_get_and_list_in_deterministic_order(engine):
    with Session(engine) as session:
        observation = make_observation(session)
        repository = VerificationRepository(session)
        instant = datetime(2026, 7, 19, tzinfo=UTC)
        later = repository.create(
            Verification(
                observation=observation,
                status=VerificationStatus.REJECTED,
                reviewed_at=instant + timedelta(days=1),
            )
        )
        first = repository.create(
            Verification(
                observation=observation,
                status=VerificationStatus.ACCEPTED,
                reviewed_at=instant,
                reviewed_by="reviewer-a",
                comment="Supported by source context",
                confidence=0.9,
            )
        )
        second = repository.create(
            Verification(
                observation=observation,
                status=VerificationStatus.UNDECIDED,
                reviewed_at=instant,
            )
        )
        session.commit()

        assert repository.get_by_id(first.id) is first
        assert repository.get_by_id(999999) is None
        assert repository.list_by_observation(observation.id) == [first, second, later]
        assert len(observation.verifications) == 3


@pytest.mark.parametrize("status", list(VerificationStatus))
def test_all_governed_statuses_are_accepted(engine, status):
    with Session(engine) as session:
        observation = make_observation(session)
        session.add(Verification(observation=observation, status=status))
        session.commit()


def test_invalid_status_is_rejected(engine):
    with Session(engine) as session:
        observation = make_observation(session)
        session.add(Verification(observation=observation, status="approved"))
        with pytest.raises(StatementError):
            session.commit()


@pytest.mark.parametrize("confidence", [None, 0.0, 0.5, 1.0])
def test_confidence_accepts_nullable_inclusive_unit_range(engine, confidence):
    with Session(engine) as session:
        observation = make_observation(session)
        session.add(
            Verification(
                observation=observation,
                status=VerificationStatus.ACCEPTED,
                confidence=confidence,
            )
        )
        session.commit()


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_outside_range_is_rejected(engine, confidence):
    with Session(engine) as session:
        observation = make_observation(session)
        session.add(
            Verification(
                observation=observation,
                status=VerificationStatus.UNDECIDED,
                confidence=confidence,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_missing_observation_is_rejected(engine):
    with Session(engine) as session:
        session.add(
            Verification(
                observation_id=999999,
                status=VerificationStatus.PENDING,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_verification_is_append_only_and_opinion_changes_are_new_rows(engine):
    with Session(engine) as session:
        observation = make_observation(session)
        original = Verification(
            observation=observation,
            status=VerificationStatus.UNDECIDED,
        )
        session.add(original)
        session.commit()

        original.status = VerificationStatus.ACCEPTED
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

        session.delete(original)
        with pytest.raises(ValueError, match="cannot be deleted"):
            session.commit()
        session.rollback()

        later = Verification(
            observation=observation,
            status=VerificationStatus.ACCEPTED,
        )
        session.add(later)
        session.commit()
        assert VerificationRepository(session).list_by_observation(observation.id) == [
            original,
            later,
        ]


def test_observation_deletion_does_not_cascade_review_history(engine):
    with Session(engine) as session:
        observation = make_observation(session)
        session.add(
            Verification(
                observation=observation,
                status=VerificationStatus.ACCEPTED,
            )
        )
        session.commit()
        session.delete(observation)
        with pytest.raises(ValueError, match="Observation cannot be deleted"):
            session.commit()
