import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, Project, PublishingSource, Source
from app.models.document import DOCUMENT_STATUSES, project_documents


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


def _project(title: str) -> Project:
    from app.models import Airport

    return Project(
        airport=Airport(name=f"{title} Airport", country="USA"),
        title=title,
        project_type="safety",
        status="planned",
        confidence_level="planned",
    )


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


def test_project_document_association_is_minimal_and_unique(engine):
    assert list(project_documents.c.keys()) == ["project_id", "document_id"]
    assert tuple(column.name for column in project_documents.primary_key.columns) == (
        "project_id",
        "document_id",
    )

    with Session(engine) as session:
        project = _project("Alpha")
        document = Document(source=PublishingSource(name="FAA"), title="Report")
        session.add_all([project, document])
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                project_documents.insert(),
                [
                    {"project_id": project.id, "document_id": document.id},
                    {"project_id": project.id, "document_id": document.id},
                ],
            )


def test_source_document_and_project_relationships_work(engine):
    with Session(engine) as session:
        source = PublishingSource(name="FAA")
        document = Document(source=source, title="Airport report")
        first_project = _project("Alpha")
        second_project = _project("Bravo")
        document.projects.extend([first_project, second_project])
        session.add(document)
        session.commit()
        session.expire_all()

        loaded = session.scalar(select(Document).where(Document.id == document.id))
        assert loaded.source.name == "FAA"
        assert loaded in loaded.source.documents
        assert {project.title for project in loaded.projects} == {"Alpha", "Bravo"}
        assert all(loaded in project.documents for project in loaded.projects)


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


def test_normalized_source_is_independent_of_project(engine):
    with Session(engine) as session:
        source = PublishingSource(name="Independent Publisher")
        session.add(source)
        session.commit()

        assert source.id is not None
        assert source.documents == []
        assert "project_id" not in PublishingSource.__table__.c


def test_project_does_not_own_sources_or_documents_with_delete_orphan():
    project_relationships = inspect(Project).relationships
    source_relationships = inspect(PublishingSource).relationships

    assert "delete-orphan" in project_relationships.sources.cascade
    assert project_relationships.sources.mapper.class_ is Source
    assert "delete-orphan" not in project_relationships.documents.cascade
    assert "delete-orphan" not in source_relationships.documents.cascade
