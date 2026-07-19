from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, StatementError
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


def make_verification(
    session: Session,
    key: str = "airport.emas.product",
    status: VerificationStatus = VerificationStatus.ACCEPTED,
) -> Verification:
    observation = Observation(
        document=Document(source=PublishingSource(name="FAA"), title=f"Evidence {key}"),
        observation_type=ObservationType(
            key=key,
            display_label=key,
            description="Test claim",
            value_type="raw_text",
        ),
        raw_value="EMASMAX",
    )
    verification = Verification(observation=observation, status=status)
    session.add(verification)
    session.commit()
    return verification


def make_fact(verification: Verification, **values) -> Fact:
    return Fact(
        fact_type_key=values.pop("fact_type_key", "airport.emas.product"),
        subject_type=values.pop("subject_type", "airport"),
        subject_identifier=values.pop("subject_identifier", "FAA:JFK"),
        accepted_value=values.pop("accepted_value", "EMASMAX"),
        status=values.pop("status", FactStatus.ACCEPTED),
        supporting_verifications=[verification],
        **values,
    )


def test_fact_model_preserves_atomic_statement_and_support(engine):
    with Session(engine) as session:
        first_support = make_verification(session)
        second_support = make_verification(
            session, key="airport.emas.product.corroborating"
        )
        fact = make_fact(
            first_support,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
        fact.supporting_verifications.append(second_support)
        session.add(fact)
        session.commit()

        assert fact.id is not None
        assert fact.status is FactStatus.ACCEPTED
        assert fact.fact_type_key == "airport.emas.product"
        assert fact.subject_type == "airport"
        assert fact.subject_identifier == "FAA:JFK"
        assert fact.accepted_value == "EMASMAX"
        assert fact.supporting_verifications == [first_support, second_support]


def test_repository_create_get_and_historical_list_order(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        repository = FactRepository(session)
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        later = repository.create(
            make_fact(
                verification,
                accepted_value="later independent",
                subject_identifier="FAA:LAX",
                created_at=instant + timedelta(days=1),
            )
        )
        first = repository.create(make_fact(verification, created_at=instant))
        second = repository.create(
            make_fact(
                verification,
                accepted_value="same timestamp",
                subject_identifier="FAA:SFO",
                created_at=instant,
            )
        )
        session.commit()

        loaded = repository.get_by_id(first.id)
        assert loaded is first
        assert loaded.supporting_verifications == [verification]
        assert repository.get_by_id(999999) is None
        assert repository.list() == [first, second, later]


def test_version_lineage_is_bidirectional_and_preserves_history(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        original = make_fact(verification, accepted_value="two systems")
        replacement = make_fact(
            verification,
            accepted_value="three systems",
            supersedes=original,
        )
        session.add_all([original, replacement])
        session.commit()

        assert replacement.supersedes is original
        assert original.superseded_by == [replacement]
        assert FactRepository(session).list() == [original, replacement]


def test_retirement_is_terminal_append_only_version(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        original = make_fact(verification)
        retirement = make_fact(
            verification,
            status=FactStatus.RETIRED,
            supersedes=original,
        )
        session.add_all([original, retirement])
        session.commit()

        assert retirement.status is FactStatus.RETIRED
        assert retirement.supersedes is original
        assert FactRepository(session).list_current(date(2026, 7, 19)) == []
        assert FactRepository(session).list() == [original, retirement]


def test_current_query_returns_only_applicable_terminal_accepted_versions(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        original = make_fact(verification, accepted_value="old")
        current = make_fact(
            verification,
            accepted_value="current",
            supersedes=original,
            valid_from=date(2026, 1, 1),
        )
        future = make_fact(
            verification,
            subject_identifier="FAA:FUT",
            valid_from=date(2027, 1, 1),
        )
        expired = make_fact(
            verification,
            subject_identifier="FAA:OLD",
            valid_to=date(2025, 12, 31),
        )
        independent = make_fact(
            verification,
            subject_identifier="FAA:ATL",
        )
        session.add_all([original, current, future, expired, independent])
        session.commit()

        assert FactRepository(session).list_current(date(2026, 7, 19)) == [
            independent,
            current,
        ]


def test_future_successor_does_not_hide_current_version_early(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        original = make_fact(verification, accepted_value="current in 2026")
        future = make_fact(
            verification,
            accepted_value="replacement in 2027",
            valid_from=date(2027, 1, 1),
            supersedes=original,
        )
        session.add_all([original, future])
        session.commit()

        repository = FactRepository(session)
        assert repository.list_current(date(2026, 7, 19)) == [original]
        assert repository.list_current(date(2027, 7, 19)) == [future]


def test_fact_requires_at_least_one_accepted_verification(engine):
    with Session(engine) as session:
        rejected = make_verification(
            session,
            status=VerificationStatus.REJECTED,
        )
        unsupported = Fact(
            fact_type_key="airport.emas.product",
            subject_type="airport",
            subject_identifier="FAA:JFK",
            accepted_value="EMASMAX",
            status=FactStatus.ACCEPTED,
        )
        session.add(unsupported)
        with pytest.raises(ValueError, match="at least one"):
            session.commit()
        session.rollback()

        session.add(make_fact(rejected))
        with pytest.raises(ValueError, match="accepted Verifications"):
            session.commit()


def test_fact_columns_and_support_set_are_immutable(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        other_support = make_verification(session, key="airport.emas.other-support")
        fact = make_fact(verification)
        session.add(fact)
        session.commit()

        fact.accepted_value = "changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

        with pytest.raises(ValueError, match="support is immutable"):
            fact.supporting_verifications.append(other_support)
        with pytest.raises(ValueError, match="support is immutable"):
            fact.supporting_verifications.remove(verification)

        session.delete(fact)
        with pytest.raises(ValueError, match="cannot be deleted"):
            session.commit()


def test_invalid_status_date_range_and_retirement_shape_are_rejected(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        session.add(make_fact(verification, status="current"))
        with pytest.raises(StatementError):
            session.commit()
        session.rollback()

        session.add(
            make_fact(
                verification,
                valid_from=date(2026, 2, 1),
                valid_to=date(2026, 1, 1),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(make_fact(verification, status=FactStatus.RETIRED))
        with pytest.raises(IntegrityError):
            session.commit()


def test_self_supersession_and_branching_are_rejected(engine):
    with Session(engine) as session:
        verification = make_verification(session)
        original = make_fact(verification)
        session.add(original)
        session.commit()
        original.supersedes_fact_id = original.id
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

        first = make_fact(verification, accepted_value="first", supersedes=original)
        second = make_fact(verification, accepted_value="second", supersedes=original)
        session.add_all([first, second])
        with pytest.raises(IntegrityError):
            session.commit()
