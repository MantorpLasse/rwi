import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import FindingType
from app.repositories import FindingTypeRepository


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


def finding_type(key: str, *, active: bool = True) -> FindingType:
    return FindingType(
        key=key,
        name=key.replace("_", " ").title(),
        description=f"Governed definition for {key}",
        category="STATUS",
        is_active=active,
    )


def test_model_construction_and_upper_snake_case_validation(engine):
    with Session(engine) as session:
        item = finding_type("CURRENT_EMAS")
        session.add(item)
        session.commit()

        assert item.id is not None
        assert item.key == "CURRENT_EMAS"
        assert item.name == "Current Emas"
        assert item.category == "STATUS"
        assert item.is_active is True
        assert item.created_at is not None

    for invalid_key in ("current_emas", "CURRENT-EMAS", "_CURRENT_EMAS", "CURRENT__EMAS", ""):
        with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
            finding_type(invalid_key)


def test_keys_are_unique_and_immutable(engine):
    with Session(engine) as session:
        first = finding_type("CURRENT_EMAS")
        session.add(first)
        session.commit()

        with pytest.raises(ValueError, match="immutable"):
            first.key = "NO_VERIFIED_EMAS"

        session.add(finding_type("CURRENT_EMAS"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_repository_create_lookup_list_and_active_filter(engine):
    with Session(engine) as session:
        repository = FindingTypeRepository(session)
        active = repository.create(finding_type("CURRENT_EMAS"))
        inactive = repository.create(
            finding_type("NO_VERIFIED_EMAS", active=False)
        )
        conflict = repository.create(finding_type("CONFLICTING_EMAS"))
        session.commit()

        assert repository.get_by_key("CURRENT_EMAS") is active
        assert repository.get_by_key("UNKNOWN") is None
        assert repository.get_by_id(inactive.id) is inactive
        assert repository.get_by_id(999999) is None
        assert repository.list() == [conflict, active, inactive]
        assert repository.list_active() == [conflict, active]
