import hashlib
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.acquisition.faa_emas_parser import FAAEmasParseError, FAAEmasSnapshotParser
from app.acquisition.faa_tableau import (
    FAATableauAcquisitionProvider,
    TableauAcquisitionError,
)
from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot
from app.services.acquisition import AcquisitionService


ARTICLE = "https://faa.example/article"
VIEW = "https://tableau.example/t/FAA/views/EMASIncidentsandInstallations/Main"
BOOTSTRAP = "https://tableau.example/vizql/w/EMAS/v/Main/bootstrapSession/sessions/transient-123"
BOOTSTRAP_BYTES = b'123;{"secondaryInfo":{"presModelMap":{}}}'


def article_html(view_url: str = VIEW) -> bytes:
    return f'<html><iframe src="{view_url}"></iframe></html>'.encode()


def view_html() -> bytes:
    config = json.dumps(
        {
            "sessionid": "transient-123",
            "sheetId": "Main",
            "bootstrapSessionUrl": BOOTSTRAP,
        }
    )
    return f'<html><textarea id="tsConfig">{config}</textarea></html>'.encode()


def successful_handler(request: httpx.Request) -> httpx.Response:
    if str(request.url) == ARTICLE:
        return httpx.Response(200, content=article_html(), request=request)
    if str(request.url) == VIEW:
        return httpx.Response(200, content=view_html(), request=request)
    if str(request.url) == BOOTSTRAP and request.method == "POST":
        return httpx.Response(
            200,
            content=BOOTSTRAP_BYTES,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "ETag": '"bootstrap-1"',
                "Set-Cookie": "tableau=secret",
            },
            request=request,
        )
    return httpx.Response(404, request=request)


def provider(handler=successful_handler, *, view_url=VIEW):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    return FAATableauAcquisitionProvider(
        ARTICLE,
        tableau_view_url=view_url,
        client=client,
        timeout_seconds=1,
        user_agent="RWI-Test/1",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def source(session: Session) -> AcquisitionSource:
    item = AcquisitionSource(
        publishing_source=PublishingSource(name="FAA"),
        key="faa.emas.tableau",
        display_name="FAA EMAS Tableau",
        acquisition_type="tableau",
        canonical_url=ARTICLE,
        expected_media_type="text/plain",
        active=True,
    )
    session.add(item)
    session.commit()
    return item


def test_successful_bootstrap_acquisition_preserves_only_opaque_payload(session):
    run = AcquisitionService(session, provider()).acquire(source(session))
    assert run.status is AcquisitionRunStatus.SUCCESS
    assert run.snapshot.payload == BOOTSTRAP_BYTES
    assert run.snapshot.payload != article_html()
    assert run.snapshot.payload != view_html()
    assert run.snapshot.sha256 == hashlib.sha256(BOOTSTRAP_BYTES).hexdigest()
    assert run.snapshot.byte_size == len(BOOTSTRAP_BYTES)
    assert run.snapshot.media_type == "text/plain; charset=utf-8"


def test_configuration_discovery_and_user_agent():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return successful_handler(request)

    result = provider(handler, view_url=None).retrieve()
    assert result.content == BOOTSTRAP_BYTES
    assert [request.method for request in requests] == ["GET", "GET", "POST"]
    assert all(request.headers["user-agent"] == "RWI-Test/1" for request in requests)


def test_redirect_handling_preserves_bootstrap_final_url():
    redirected = "https://tableau.example/bootstrap-final"

    def handler(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(307, headers={"Location": redirected}, request=request)
        if str(request.url) == redirected:
            return httpx.Response(
                200,
                content=BOOTSTRAP_BYTES,
                headers={"Content-Type": "text/plain"},
                request=request,
            )
        return successful_handler(request)

    result = provider(handler).retrieve()
    assert result.request_url == BOOTSTRAP
    assert result.final_url == redirected


def test_missing_configuration_has_governed_error():
    def handler(request):
        return httpx.Response(200, content=b"<html></html>", request=request)

    with pytest.raises(TableauAcquisitionError) as caught:
        provider(handler, view_url=None).retrieve()
    assert caught.value.code == "tableau_configuration_missing"


def test_failed_session_creation_has_governed_error():
    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        return httpx.Response(200, content=b"<html></html>", request=request)

    with pytest.raises(TableauAcquisitionError) as caught:
        provider(handler).retrieve()
    assert caught.value.code == "tableau_session_creation_failed"


def test_bootstrap_failure_has_governed_error():
    def handler(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(503, request=request)
        return successful_handler(request)

    with pytest.raises(TableauAcquisitionError) as caught:
        provider(handler).retrieve()
    assert caught.value.code == "tableau_bootstrap_retrieval_failed"


def test_unexpected_media_type_and_html_response_are_rejected():
    def wrong_media(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(
                200, content=b"data", headers={"Content-Type": "application/pdf"}, request=request
            )
        return successful_handler(request)

    with pytest.raises(TableauAcquisitionError) as caught:
        provider(wrong_media).retrieve()
    assert caught.value.code == "tableau_unexpected_media_type"

    def html_response(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(
                200, content=b"<html></html>", headers={"Content-Type": "text/plain"}, request=request
            )
        return successful_handler(request)

    with pytest.raises(TableauAcquisitionError) as caught:
        provider(html_response).retrieve()
    assert caught.value.code == "unsupported_tableau_response"


def test_metadata_and_session_transport_state_are_preserved_safely(session):
    run = AcquisitionService(session, provider()).acquire(source(session))
    assert run.request_url == BOOTSTRAP
    assert run.final_url == BOOTSTRAP
    assert run.http_status == 200
    assert run.content_type == "text/plain; charset=utf-8"
    assert run.provider_version == "faa-tableau-vizql/1"
    assert run.duration_seconds >= 0
    headers = json.loads(run.response_headers)
    assert headers["etag"] == '"bootstrap-1"'
    assert "set-cookie" not in headers
    assert not hasattr(run.snapshot, "session_id")


def test_deduplication_reuses_snapshot_but_logs_every_run(session):
    item = source(session)
    service = AcquisitionService(session, provider())
    first = service.acquire(item)
    second = service.acquire(item)
    assert first.snapshot_id == second.snapshot_id
    assert second.status is AcquisitionRunStatus.NO_CHANGE
    assert len(session.scalars(select(Snapshot)).all()) == 1
    assert len(session.scalars(select(AcquisitionRun)).all()) == 2


def test_bootstrap_snapshot_is_replay_compatible_with_parser_boundary(session):
    run = AcquisitionService(session, provider()).acquire(source(session))
    persisted = session.get(Snapshot, run.snapshot_id)
    with pytest.raises(FAAEmasParseError) as caught:
        FAAEmasSnapshotParser().parse(persisted.payload, persisted.media_type)
    assert caught.value.code == "unsupported_payload"
