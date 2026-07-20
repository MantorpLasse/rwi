import hashlib
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.acquisition.faa import FAAAcquisitionProvider
from app.database import Base
from app.models import (
    AcquisitionRun,
    AcquisitionRunStatus,
    AcquisitionSource,
    PublishingSource,
    Snapshot,
)
from app.services.acquisition import AcquisitionService


SOURCE_URL = "https://faa.example/emas"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def add_source(session: Session) -> AcquisitionSource:
    publisher = PublishingSource(name="FAA", homepage_url="https://www.faa.gov")
    source = AcquisitionSource(
        publishing_source=publisher,
        key="faa.emas.installations",
        display_name="FAA EMAS installations",
        acquisition_type="http",
        canonical_url=SOURCE_URL,
        expected_media_type="text/csv",
        active=True,
    )
    session.add(source)
    session.commit()
    return source


def provider(handler) -> FAAAcquisitionProvider:
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    return FAAAcquisitionProvider(SOURCE_URL, client=client, timeout_seconds=1)


def success_handler(content=b"airport,product\nSTP,EMASMAX\n"):
    def handler(request):
        return httpx.Response(
            200,
            content=content,
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "ETag": '"version-1"',
                "Set-Cookie": "secret=value",
            },
            request=request,
        )

    return handler


def test_successful_acquisition_preserves_exact_payload_and_metadata(session):
    source = add_source(session)
    run = AcquisitionService(session, provider(success_handler())).acquire(source)

    assert run.status is AcquisitionRunStatus.SUCCESS
    assert run.is_new_snapshot is True
    assert run.request_url == SOURCE_URL
    assert run.final_url == SOURCE_URL
    assert run.http_status == 200
    assert run.content_type == "text/csv; charset=utf-8"
    assert run.provider_version == "faa-http/1"
    assert run.duration_seconds >= 0
    headers = json.loads(run.response_headers)
    assert headers["etag"] == '"version-1"'
    assert "set-cookie" not in headers
    assert run.snapshot.payload == b"airport,product\nSTP,EMASMAX\n"
    assert run.snapshot.byte_size == len(run.snapshot.payload)
    assert run.snapshot.sha256 == hashlib.sha256(run.snapshot.payload).hexdigest()
    assert run.snapshot.retrieved_at is not None


def test_redirect_preserves_request_and_final_urls(session):
    source = add_source(session)

    def handler(request):
        if request.url.path == "/emas":
            return httpx.Response(302, headers={"Location": "/export"}, request=request)
        return httpx.Response(
            200,
            content=b"payload",
            headers={"Content-Type": "text/csv"},
            request=request,
        )

    run = AcquisitionService(session, provider(handler)).acquire(source)
    assert run.request_url == SOURCE_URL
    assert run.final_url == "https://faa.example/export"


@pytest.mark.parametrize("status", [404, 500])
def test_http_failure_creates_failed_run_without_snapshot(session, status):
    source = add_source(session)

    def handler(request):
        return httpx.Response(status, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        AcquisitionService(session, provider(handler)).acquire(source)

    run = session.scalar(select(AcquisitionRun))
    assert run.status is AcquisitionRunStatus.FAILED
    assert run.snapshot is None
    assert run.error_category == "HTTPStatusError"
    assert session.scalar(select(Snapshot)) is None


def test_timeout_creates_unavailable_run(session):
    source = add_source(session)

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(httpx.ReadTimeout):
        AcquisitionService(session, provider(handler)).acquire(source)
    assert session.scalar(select(AcquisitionRun)).status is AcquisitionRunStatus.UNAVAILABLE


def test_duplicate_payload_creates_new_run_and_reuses_snapshot(session):
    source = add_source(session)
    service = AcquisitionService(session, provider(success_handler(b"same bytes")))
    first = service.acquire(source)
    second = service.acquire(source)

    assert first.id != second.id
    assert first.snapshot_id == second.snapshot_id
    assert first.status is AcquisitionRunStatus.SUCCESS
    assert second.status is AcquisitionRunStatus.NO_CHANGE
    assert second.is_new_snapshot is False
    assert len(session.scalars(select(Snapshot)).all()) == 1
    assert len(session.scalars(select(AcquisitionRun)).all()) == 2


def test_changed_payload_creates_a_new_snapshot(session):
    source = add_source(session)
    first = AcquisitionService(session, provider(success_handler(b"first"))).acquire(source)
    second = AcquisitionService(session, provider(success_handler(b"second"))).acquire(source)
    assert first.snapshot_id != second.snapshot_id
    assert len(session.scalars(select(Snapshot)).all()) == 2


def test_snapshot_and_completed_run_are_immutable(session):
    source = add_source(session)
    run = AcquisitionService(session, provider(success_handler())).acquire(source)
    run.snapshot.payload = b"changed"
    with pytest.raises(ValueError, match="Snapshot is immutable"):
        session.commit()
    session.rollback()

    persisted_run = session.get(AcquisitionRun, run.id)
    persisted_run.error_detail = "changed"
    with pytest.raises(ValueError, match="Completed AcquisitionRun is immutable"):
        session.commit()


def test_empty_payload_is_invalid_and_not_preserved(session):
    source = add_source(session)
    with pytest.raises(ValueError, match="empty payload"):
        AcquisitionService(session, provider(success_handler(b""))).acquire(source)
    assert session.scalar(select(AcquisitionRun)).status is AcquisitionRunStatus.INVALID_RESPONSE
    assert session.scalar(select(Snapshot)) is None
