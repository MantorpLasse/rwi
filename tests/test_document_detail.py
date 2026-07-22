from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Document, PublishingSource


@pytest.fixture
def document_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-detail.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, testing_session
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_document_detail_renders_metadata_and_safe_external_links(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(
                name="European Aviation Publisher",
                source_type="authority",
                homepage_url="https://publisher.example/home",
            ),
            title="Runway safety publication",
            document_type="technical report",
            url="https://documents.example/report.pdf",
            published_date=date(2026, 1, 5),
            accessed_date=date(2026, 7, 18),
            revision="Revision B",
            document_reference="EAP-42",
            summary="Technical findings for the runway project.",
            status="active",
        )
        session.add(document)
        session.commit()
        document_id = document.id

    response = client.get(f"/documents/{document_id}")

    assert response.status_code == 200
    for text in (
        "Runway safety publication",
        "European Aviation Publisher",
        "authority",
        "technical report",
        "2026-01-05",
        "2026-07-18",
        "Revision B",
        "EAP-42",
        "Technical findings for the runway project.",
        "active",
    ):
        assert text in response.text
    assert 'href="https://documents.example/report.pdf" target="_blank" rel="noopener"' in response.text
    assert 'href="https://publisher.example/home" target="_blank" rel="noopener"' in response.text
    assert 'aria-label="Arbetsflöde"' in response.text
    assert 'href="/documents">↑ Upp: Dokument</a>' in response.text


def test_missing_optional_metadata_has_no_empty_labels(document_app):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(name="Minimal Publisher"),
            title="Minimal document",
            status="active",
        )
        session.add(document)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text

    assert "Minimal document" in html
    for label in (
        "Dokumenttyp",
        "Publicerad",
        "Hämtad",
        "Revision",
        "Dokumentreferens",
        "Sammanfattning",
        "Öppna originaldokument",
        "Utgivarens webbplats",
    ):
        assert label not in html


@pytest.mark.parametrize(
    ("status", "badge_class"),
    [
        ("active", "text-bg-success"),
        ("incomplete", "text-bg-warning"),
        ("superseded", "text-bg-secondary"),
        ("withdrawn", "text-bg-danger"),
        ("unavailable", "text-bg-dark"),
    ],
)
def test_allowed_statuses_render_as_visible_badges(document_app, status, badge_class):
    client, session_factory = document_app
    with session_factory() as session:
        document = Document(
            source=PublishingSource(name="Status Publisher"),
            title=f"Document {status}",
            status=status,
        )
        session.add(document)
        session.commit()
        document_id = document.id

    html = client.get(f"/documents/{document_id}").text

    assert f">{status}</span>" in html
    assert badge_class in html
    if status == "incomplete":
        assert "Länken kan leda till utgivarens startsida" in html
        assert "Verifiera titel, dokumentreferens och revision" in html


def test_missing_document_returns_404_and_existing_routes_still_work(document_app):
    client, _session_factory = document_app
    assert client.get("/documents/999999").status_code == 404
    assert client.get("/health").status_code == 200
    assert client.get("/airports").status_code == 200
    assert client.get("/signals").status_code == 200
