import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import ObservationType
from app.observation_type_vocabulary import (
    FAA_EMAS_OBSERVATION_TYPES,
    seed_observation_types,
)
from app.repositories import ObservationTypeRepository


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        test_engine.dispose()


def observation_type(key: str) -> ObservationType:
    return ObservationType(
        key=key,
        display_label="Test type",
        description="Test description",
        value_type="raw_text",
    )


def test_observation_type_keys_are_unique(engine):
    with Session(engine) as session:
        session.add_all([observation_type("test.claim"), observation_type("test.claim")])
        with pytest.raises(IntegrityError):
            session.commit()


def test_observation_type_key_is_immutable(engine):
    with Session(engine) as session:
        item = observation_type("test.claim")
        session.add(item)
        session.commit()

        with pytest.raises(ValueError, match="immutable"):
            item.key = "test.changed"


def test_repository_looks_up_by_key(engine):
    with Session(engine) as session:
        item = observation_type("test.lookup")
        session.add(item)
        session.commit()

        repository = ObservationTypeRepository(session)
        assert repository.get_by_key("test.lookup") is item
        assert repository.get_by_key("test.missing") is None


def test_seed_loading_is_complete_and_idempotent(engine):
    with Session(engine) as session:
        first_load = seed_observation_types(session)
        second_load = seed_observation_types(session)
        session.commit()

        stored = session.scalars(select(ObservationType).order_by(ObservationType.key)).all()
        assert len(first_load) == len(FAA_EMAS_OBSERVATION_TYPES) == 3
        assert [item.id for item in second_load] == [item.id for item in first_load]
        assert [item.key for item in stored] == sorted(
            definition.key for definition in FAA_EMAS_OBSERVATION_TYPES
        )
        assert {item.value_type for item in stored} == {
            "enumeration",
            "integer",
            "raw_text",
        }

