from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Document, PublishingSource


@pytest.fixture
def documents_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'documents-list.sqlite3'}",
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


def add_search_documents(session):
    alpha = Document(
        source=PublishingSource(name="Nordic Aviation Authority"),
        title="Alpha Runway Assessment",
        document_type="report",
        document_reference="REF-ALPHA-42",
        published_date=date(2026, 5, 1),
        accessed_date=date(2026, 6, 1),
        status="active",
    )
    bravo = Document(
        source=PublishingSource(name="Federal Publisher"),
        title="Bravo Safety Notice",
        document_type="notice",
        document_reference="NOTICE-9",
        published_date=date(2025, 1, 1),
        status="withdrawn",
    )
    session.add_all([alpha, bravo])
    session.commit()
    return alpha, bravo


def test_documents_list_renders_rows_links_metadata_and_counts(documents_app):
    client, session_factory = documents_app
    with session_factory() as session:
        full = Document(
            source=PublishingSource(name="Normalized Publisher"),
            title="Normalized report",
            document_type="report",
            document_reference="DOC-77",
            published_date=date(2026, 2, 3),
            accessed_date=date(2026, 7, 18),
            status="active",
        )
        minimal = Document(
            source=PublishingSource(name="Minimal Publisher"),
            title="Minimal record",
            status="unavailable",
        )
        session.add_all([full, minimal])
        session.commit()
        full_id, minimal_id = full.id, minimal.id

    response = client.get("/documents")

    assert response.status_code == 200
    for text in (
        "Dokument",
        "2 dokument",
        "Normalized report",
        "Normalized Publisher",
        "DOC-77",
        "2026-02-03",
        "2026-07-18",
        "Minimal record",
        "Minimal Publisher",
    ):
        assert text in response.text
    assert f'href="/documents/{full_id}"' in response.text
    assert f'href="/documents/{minimal_id}"' in response.text
    assert "Sammanfattning" not in response.text


def test_all_statuses_are_visible_and_incomplete_is_identified_first(documents_app):
    client, session_factory = documents_app
    statuses = ("active", "incomplete", "superseded", "withdrawn", "unavailable")
    with session_factory() as session:
        for status in statuses:
            session.add(
                Document(
                    source=PublishingSource(name=f"Publisher {status}"),
                    title=f"Document {status}",
                    status=status,
                )
            )
        session.commit()

    html = client.get("/documents").text

    for status in statuses:
        assert f">{status}</span>" in html
    assert "Kräver verifiering" in html
    assert html.index("Document incomplete") < html.index("Document active")


@pytest.mark.parametrize(
    ("query", "expected", "excluded"),
    [
        ("q=alpha+runway", "Alpha Runway Assessment", "Bravo Safety Notice"),
        ("q=ref-alpha-42", "Alpha Runway Assessment", "Bravo Safety Notice"),
        ("q=nordic+aviation", "Alpha Runway Assessment", "Bravo Safety Notice"),
        ("status=withdrawn", "Bravo Safety Notice", "Alpha Runway Assessment"),
        ("document_type=report", "Alpha Runway Assessment", "Bravo Safety Notice"),
    ],
)
def test_search_and_individual_filters(documents_app, query, expected, excluded):
    client, session_factory = documents_app
    with session_factory() as session:
        add_search_documents(session)

    html = client.get(f"/documents?{query}").text
    assert expected in html
    assert excluded not in html


def test_combined_filters_use_and_semantics_and_preserve_values(documents_app):
    client, session_factory = documents_app
    with session_factory() as session:
        add_search_documents(session)

    response = client.get("/documents?q=ALPHA&status=active&document_type=report")

    assert "Alpha Runway Assessment" in response.text
    assert "Bravo Safety Notice" not in response.text
    assert 'name="q" value="ALPHA"' in response.text
    assert '<option value="active" selected>active</option>' in response.text
    assert '<option value="report" selected>report</option>' in response.text
    assert 'href="/documents">Rensa filter</a>' in response.text

    no_match = client.get("/documents?q=ALPHA&status=withdrawn&document_type=report")
    assert "Inga dokument matchade den aktuella sökningen eller filtreringen." in no_match.text


def test_empty_parameters_are_ignored_and_unknown_filters_are_conservative(documents_app):
    client, session_factory = documents_app
    with session_factory() as session:
        add_search_documents(session)

    unfiltered = client.get("/documents?q=&status=&document_type=")
    assert "Alpha Runway Assessment" in unfiltered.text
    assert "Bravo Safety Notice" in unfiltered.text
    assert "Rensa filter" not in unfiltered.text

    unknown = client.get("/documents?status=not-a-status")
    assert unknown.status_code == 200
    assert "Inga dokument matchade den aktuella sökningen eller filtreringen." in unknown.text


def test_document_type_options_come_from_non_empty_data(documents_app):
    client, session_factory = documents_app
    with session_factory() as session:
        add_search_documents(session)
        session.add(
            Document(source=PublishingSource(name="No Type Publisher"), title="No type", status="active")
        )
        session.commit()

    html = client.get("/documents").text
    assert '<option value="notice"' in html
    assert '<option value="report"' in html
    assert '<option value="None"' not in html


def test_no_documents_state_and_navigation_link(documents_app):
    client, _session_factory = documents_app
    html = client.get("/documents").text
    assert "Inga normaliserade dokument finns ännu." in html
    assert 'class="nav-link" href="/documents">Dokument</a>' in html


def test_filtered_no_match_state_has_reset_link(documents_app):
    client, session_factory = documents_app
    with session_factory() as session:
        add_search_documents(session)

    html = client.get("/documents?q=no-such-document").text
    assert "Inga dokument matchade den aktuella sökningen eller filtreringen." in html
    assert 'href="/documents">Rensa filter</a>' in html


def test_existing_routes_still_work_alongside_documents(documents_app):
    client, _session_factory = documents_app
    assert client.get("/health").status_code == 200
    assert client.get("/airports").status_code == 200
    assert client.get("/signals").status_code == 200
    assert client.get("/api/signals").status_code == 200
