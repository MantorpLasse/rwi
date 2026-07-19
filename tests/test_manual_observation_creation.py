import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Document, Observation, ObservationType, PublishingSource


@pytest.fixture
def creation_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manual-observation.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def add_document_and_types(session):
    document = Document(
        source=PublishingSource(name="FAA Publisher"),
        title="FAA EMAS evidence",
    )
    types = [
        ObservationType(
            key="test.zulu",
            display_label="Zulu type",
            description="Zulu",
            value_type="raw_text",
        ),
        ObservationType(
            key="test.alpha.second",
            display_label="Alpha type",
            description="Alpha second",
            value_type="raw_text",
        ),
        ObservationType(
            key="test.alpha.first",
            display_label="Alpha type",
            description="Alpha first",
            value_type="raw_text",
        ),
        ObservationType(
            key="test.inactive",
            display_label="Inactive type",
            description="Inactive",
            value_type="raw_text",
            active=False,
        ),
    ]
    session.add_all([document, *types])
    session.commit()
    return document, types


def valid_data(type_id, **overrides):
    data = {
        "observation_type_id": str(type_id),
        "raw_value": "  EMASMAX\nsecond line  ",
        "normalized_value": "EMASMAX candidate",
        "extraction_confidence": "0.75",
        "evidence_locator": "Map popup 4",
        "extraction_method": "manual",
        "extractor_version": "analyst-v1",
    }
    data.update(overrides)
    return data


def observation_count(session_factory):
    with session_factory() as session:
        return session.scalar(select(func.count(Observation.id)))


def test_get_form_identifies_document_and_orders_active_types(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        document_id = document.id

    response = client.get(f"/documents/{document_id}/observations/new")
    html = response.text
    assert response.status_code == 200
    assert "FAA EMAS evidence" in html
    assert f'href="/documents/{document_id}"' in html
    assert html.index(f'value="{types[1].id}"') < html.index(f'value="{types[2].id}"')
    assert html.index(f'value="{types[2].id}"') < html.index(f'value="{types[0].id}"')
    assert "Inactive type" not in html


def test_valid_post_creates_one_observation_and_redirects(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        document_id, type_id = document.id, types[0].id

    response = client.post(
        f"/documents/{document_id}/observations/new",
        data=valid_data(type_id),
        follow_redirects=False,
    )
    assert response.status_code == 303

    with session_factory() as session:
        items = session.scalars(select(Observation)).all()
        assert len(items) == 1
        item = items[0]
        assert response.headers["location"] == f"/observations/{item.id}"
        assert item.document_id == document_id
        assert item.raw_value == "  EMASMAX\nsecond line  "
        assert item.normalized_value == "EMASMAX candidate"
        assert item.extraction_confidence == 0.75
        assert item.evidence_locator == "Map popup 4"
        assert item.extraction_method == "manual"
        assert item.extractor_version == "analyst-v1"


def test_empty_optional_fields_become_null(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        document_id, type_id = document.id, types[0].id

    response = client.post(
        f"/documents/{document_id}/observations/new",
        data=valid_data(
            type_id,
            normalized_value="",
            extraction_confidence="",
            evidence_locator=" ",
            extraction_method="",
            extractor_version="",
        ),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        item = session.scalar(select(Observation))
        assert item.normalized_value is None
        assert item.extraction_confidence is None
        assert item.evidence_locator is None
        assert item.extraction_method is None
        assert item.extractor_version is None


@pytest.mark.parametrize("confidence", ["0.0", "1.0"])
def test_confidence_boundaries_are_accepted(creation_app, confidence):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        document_id, type_id = document.id, types[0].id

    response = client.post(
        f"/documents/{document_id}/observations/new",
        data=valid_data(type_id, extraction_confidence=confidence),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        assert session.scalar(select(Observation.extraction_confidence)) == float(confidence)


@pytest.mark.parametrize(
    ("overrides", "expected_error", "retained_value"),
    [
        ({"extraction_confidence": "-0.01"}, "måste vara mellan", "-0.01"),
        ({"extraction_confidence": "1.01"}, "måste vara mellan", "1.01"),
        ({"extraction_confidence": "not-a-number"}, "måste vara ett decimaltal", "not-a-number"),
        ({"raw_value": ""}, "Rått observerat värde krävs", None),
        ({"raw_value": "   \n  "}, "Rått observerat värde krävs", None),
        ({"observation_type_id": ""}, "Välj en giltig observationstyp", None),
        ({"observation_type_id": "999999"}, "Välj en giltig observationstyp", None),
    ],
)
def test_invalid_posts_show_errors_retain_values_and_create_nothing(
    creation_app, overrides, expected_error, retained_value
):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        document_id, type_id = document.id, types[0].id

    response = client.post(
        f"/documents/{document_id}/observations/new",
        data=valid_data(type_id, **overrides),
    )
    assert response.status_code == 422
    assert expected_error in response.text
    if retained_value:
        assert f'value="{retained_value}"' in response.text
    assert "EMASMAX candidate" in response.text
    assert observation_count(session_factory) == 0


def test_inactive_observation_type_is_rejected(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        document_id, inactive_id = document.id, types[3].id

    response = client.post(
        f"/documents/{document_id}/observations/new",
        data=valid_data(inactive_id),
    )
    assert response.status_code == 422
    assert observation_count(session_factory) == 0


def test_posted_document_id_cannot_override_route_document(creation_app):
    client, session_factory = creation_app
    with session_factory() as session:
        route_document, types = add_document_and_types(session)
        other_document = Document(
            source=PublishingSource(name="Other"), title="Other document"
        )
        session.add(other_document)
        session.commit()
        route_id, other_id, type_id = route_document.id, other_document.id, types[0].id

    data = valid_data(type_id)
    data["document_id"] = str(other_id)
    response = client.post(
        f"/documents/{route_id}/observations/new",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    with session_factory() as session:
        assert session.scalar(select(Observation.document_id)) == route_id


def test_missing_document_returns_404_for_get_and_post(creation_app):
    client, _session_factory = creation_app
    assert client.get("/documents/999999/observations/new").status_code == 404
    assert client.post("/documents/999999/observations/new", data={}).status_code == 404


def test_form_escapes_source_and_retained_submitted_html(creation_app):
    client, session_factory = creation_app
    attack = '<script>alert("unsafe")</script>'
    with session_factory() as session:
        document = Document(source=PublishingSource(name="FAA"), title=attack)
        observation_type = ObservationType(
            key="test.html",
            display_label=attack,
            description="HTML test",
            value_type="raw_text",
        )
        session.add_all([document, observation_type])
        session.commit()
        document_id, type_id = document.id, observation_type.id

    get_html = client.get(f"/documents/{document_id}/observations/new").text
    post_html = client.post(
        f"/documents/{document_id}/observations/new",
        data=valid_data(type_id, raw_value="", normalized_value=attack),
    ).text
    for html in (get_html, post_html):
        assert attack not in html
        assert "&lt;script&gt;" in html


def test_document_has_creation_action_but_read_only_pages_have_no_mutation_controls(
    creation_app,
):
    client, session_factory = creation_app
    with session_factory() as session:
        document, types = add_document_and_types(session)
        item = Observation(
            document=document,
            observation_type=types[0],
            raw_value="Read-only evidence",
        )
        session.add(item)
        session.commit()
        document_id, item_id = document.id, item.id

    document_html = client.get(f"/documents/{document_id}").text
    assert f'href="/documents/{document_id}/observations/new"' in document_html
    assert "Lägg till observation" in document_html

    for path in ("/observations", f"/observations/{item_id}"):
        html = client.get(path).text
        assert "/observations/new" not in html
        assert "Redigera" not in html
        assert "Radera" not in html
        assert "Verifiera" not in html
        assert "Promovera" not in html
