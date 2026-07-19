from datetime import UTC, datetime, timedelta

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
    Intelligence,
    IntelligenceStatus,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import IntelligenceRepository


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


def make_fact(session: Session, key: str = "airport.emas.product", *, status=FactStatus.ACCEPTED) -> Fact:
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {key}"),
                title=f"Evidence {key}",
            ),
            observation_type=ObservationType(
                key=key,
                display_label=key,
                description="Evidence for an Intelligence test",
                value_type="raw_text",
            ),
            raw_value="EMASMAX",
        ),
        status=VerificationStatus.ACCEPTED,
    )
    fact = Fact(
        fact_type_key=key,
        subject_type="airport",
        subject_identifier="FAA:JFK",
        accepted_value="EMASMAX",
        status=status,
        supporting_verifications=[verification],
    )
    session.add(fact)
    session.commit()
    return fact


def finding(fact: Fact, **values) -> Intelligence:
    return Intelligence(
        finding_type=values.pop("finding_type", "airport.emas.coverage"),
        title=values.pop("title", "EMAS coverage confirmed"),
        summary=values.pop("summary", "Accepted Facts establish EMAS coverage."),
        status=values.pop("status", IntelligenceStatus.ACTIVE),
        derived_at=values.pop("derived_at", datetime(2026, 7, 19, tzinfo=UTC)),
        supporting_facts=[fact],
        **values,
    )


def test_model_construction_with_multiple_supporting_facts(engine):
    with Session(engine) as session:
        first = make_fact(session)
        second = make_fact(session, "airport.emas.system_count")
        item = finding(first)
        item.supporting_facts.append(second)
        session.add(item)
        session.commit()

        assert item.id is not None
        assert item.status is IntelligenceStatus.ACTIVE
        assert item.supporting_facts == [first, second]
        assert item.created_at is not None


def test_repository_create_get_and_historical_list(engine):
    with Session(engine) as session:
        fact = make_fact(session)
        repository = IntelligenceRepository(session)
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        later = repository.create(finding(fact, title="later", created_at=instant + timedelta(days=1)))
        first = repository.create(finding(fact, title="first", created_at=instant))
        second = repository.create(finding(fact, title="second", created_at=instant))
        session.commit()

        loaded = repository.get_by_id(first.id)
        assert loaded is first
        assert loaded.supporting_facts == [fact]
        assert repository.get_by_id(999999) is None
        assert repository.list() == [first, second, later]


def test_supersession_is_bidirectional_and_current_is_terminal_active_row(engine):
    with Session(engine) as session:
        fact = make_fact(session)
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        original = finding(fact, title="Original", created_at=instant)
        replacement = finding(
            fact,
            title="Replacement",
            supersedes=original,
            created_at=instant + timedelta(days=1),
        )
        independent = finding(
            fact,
            finding_type="airport.emas.market",
            title="Independent",
            created_at=instant + timedelta(days=2),
        )
        session.add_all([original, replacement, independent])
        session.commit()

        repository = IntelligenceRepository(session)
        assert replacement.supersedes is original
        assert original.superseded_by == [replacement]
        assert repository.list() == [original, replacement, independent]
        assert repository.list_current() == [replacement, independent]


@pytest.mark.parametrize(
    "status", [IntelligenceStatus.SUPERSEDED, IntelligenceStatus.ARCHIVED]
)
def test_non_active_terminal_rows_preserve_history_but_are_not_current(engine, status):
    with Session(engine) as session:
        fact = make_fact(session)
        original = finding(fact)
        terminal = finding(fact, status=status, supersedes=original)
        session.add_all([original, terminal])
        session.commit()

        repository = IntelligenceRepository(session)
        assert repository.list() == [original, terminal]
        assert repository.list_current() == []


def test_requires_accepted_fact_support(engine):
    with Session(engine) as session:
        unsupported = Intelligence(
            finding_type="airport.emas.coverage",
            title="Unsupported",
            summary="No facts.",
            status=IntelligenceStatus.ACTIVE,
            derived_at=datetime.now(UTC),
        )
        session.add(unsupported)
        with pytest.raises(ValueError, match="at least one"):
            session.commit()
        session.rollback()

        original = make_fact(session, "airport.emas.retired")
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
        session.add(finding(retired))
        with pytest.raises(ValueError, match="accepted Facts"):
            session.commit()


def test_columns_support_relationship_and_delete_are_immutable(engine):
    with Session(engine) as session:
        fact = make_fact(session)
        other = make_fact(session, "airport.emas.other")
        item = finding(fact)
        session.add(item)
        session.commit()

        item.summary = "Changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

        with pytest.raises(ValueError, match="support is immutable"):
            item.supporting_facts.append(other)
        with pytest.raises(ValueError, match="support is immutable"):
            item.supporting_facts.remove(fact)

        session.delete(item)
        with pytest.raises(ValueError, match="cannot be deleted"):
            session.commit()


def test_invalid_status_lineage_shape_and_branching_are_rejected(engine):
    with Session(engine) as session:
        fact = make_fact(session)
        session.add(finding(fact, status="current"))
        with pytest.raises(StatementError):
            session.commit()
        session.rollback()

        session.add(finding(fact, status=IntelligenceStatus.ARCHIVED))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        original = finding(fact)
        session.add(original)
        session.commit()
        first = finding(fact, title="First branch", supersedes=original)
        second = finding(fact, title="Second branch", supersedes=original)
        session.add_all([first, second])
        with pytest.raises(IntegrityError):
            session.commit()
