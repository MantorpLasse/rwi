import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, PublishingSource
from app.models.document import DOCUMENT_STATUSES


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        test_engine.dispose()


def test_normalized_source_and_document_table_contracts():
    source = PublishingSource.__table__
    document = Document.__table__

    assert source.name == "publishing_sources"
    assert [(column.name, str(column.type), column.nullable) for column in source.columns] == [
        ("id", "INTEGER", False),
        ("name", "VARCHAR(200)", False),
        ("source_type", "VARCHAR(50)", True),
        ("homepage_url", "VARCHAR(1000)", True),
        ("country_code", "VARCHAR(2)", True),
        ("reliability_level", "VARCHAR(30)", True),
        ("notes", "TEXT", True),
    ]
    assert document.name == "documents"
    assert document.c.source_id.nullable is False
    assert document.c.title.nullable is False
    assert document.c.url.nullable is True
    assert document.c.revision.nullable is True
    assert document.c.content_hash.nullable is True
    assert document.c.status.nullable is False
    assert document.c.status.default.arg == "active"
    assert {foreign_key.target_fullname for foreign_key in document.c.source_id.foreign_keys} == {
        "publishing_sources.id"
    }
    assert {index.name for index in document.indexes} == {"ix_documents_source_id"}


@pytest.mark.parametrize("status", sorted(DOCUMENT_STATUSES))
def test_each_approved_document_status_is_allowed(engine, status):
    with Session(engine) as session:
        session.add(Document(source=PublishingSource(name="Publisher"), title=status, status=status))
        session.commit()


def test_document_status_defaults_to_active(engine):
    with Session(engine) as session:
        document = Document(source=PublishingSource(name="Publisher"), title="Report")
        session.add(document)
        session.commit()

        assert document.status == "active"


def test_unapproved_document_status_fails(engine):
    with Session(engine) as session:
        session.add(Document(source=PublishingSource(name="Publisher"), title="Report", status="draft"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_documents_belong_only_to_a_publishing_source(engine):
    with Session(engine) as session:
        source = PublishingSource(name="Independent Publisher")
        session.add(source)
        session.commit()

        assert source.id is not None
        assert source.documents == []
        assert "project_id" not in PublishingSource.__table__.c
