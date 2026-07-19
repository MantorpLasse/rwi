from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, Observation, ObservationType, PublishingSource
from app.repositories import ObservationRepository


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def foundation(session: Session) -> tuple[Document, ObservationType]:
    source = PublishingSource(name="FAA")
    document = Document(source=source, title="EMAS map")
    observation_type = ObservationType(
        key="airport.emas.product",
        display_label="EMAS product",
        description="Product stated by the source",
        value_type="raw_text",
    )
    session.add_all([document, observation_type])
    session.flush()
    return document, observation_type


def observation(document: Document, observation_type: ObservationType, **values) -> Observation:
    return Observation(
        document=document,
        observation_type=observation_type,
        raw_value=values.pop("raw_value", " EMASMAX "),
        **values,
    )


def test_creation_preserves_raw_value_and_allows_optional_metadata(engine):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        item = observation(document, observation_type)
        ObservationRepository(session).create(item)
        session.commit()

        assert item.raw_value == " EMASMAX "
        assert item.normalized_value is None
        assert item.document is document
        assert item.observation_type is observation_type


def test_repository_create_get_and_deterministic_document_listing(engine):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        repository = ObservationRepository(session)
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        later = repository.create(observation(document, observation_type, raw_value="later", created_at=instant + timedelta(days=1)))
        first = repository.create(observation(document, observation_type, raw_value="first", created_at=instant))
        second = repository.create(observation(document, observation_type, raw_value="second", created_at=instant))

        assert repository.get_by_id(first.id) is first
        assert repository.get_by_id(999999) is None
        assert repository.list_by_document(document.id) == [first, second, later]


def test_supersession_is_bidirectional_and_preserves_both_rows(engine):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        original = observation(document, observation_type, raw_value="1999")
        correction = observation(document, observation_type, raw_value="2000", supersedes=original)
        session.add_all([original, correction])
        session.commit()

        assert correction.supersedes is original
        assert original.superseded_by == [correction]
        assert session.query(Observation).count() == 2


def test_persisted_observation_cannot_be_updated_or_deleted(engine):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        item = observation(document, observation_type)
        session.add(item)
        session.commit()

        item.raw_value = "changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

        session.delete(item)
        with pytest.raises(ValueError, match="cannot be deleted"):
            session.commit()


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_extraction_confidence_rejects_values_outside_inclusive_unit_range(engine, confidence):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        session.add(observation(document, observation_type, extraction_confidence=confidence))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("confidence", [None, 0.0, 0.5, 1.0])
def test_extraction_confidence_accepts_nullable_inclusive_unit_range(engine, confidence):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        session.add(observation(document, observation_type, extraction_confidence=confidence))
        session.commit()


def test_self_supersession_is_rejected(engine):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        item = observation(document, observation_type)
        session.add(item)
        session.flush()
        item.supersedes_observation_id = item.id
        with pytest.raises((IntegrityError, ValueError)):
            session.commit()


@pytest.mark.parametrize("parent", ["document", "observation_type"])
def test_parent_deletion_does_not_cascade_to_observation(engine, parent):
    with Session(engine) as session:
        document, observation_type = foundation(session)
        session.add(observation(document, observation_type))
        session.commit()
        session.delete(document if parent == "document" else observation_type)
        with pytest.raises(IntegrityError):
            session.commit()
