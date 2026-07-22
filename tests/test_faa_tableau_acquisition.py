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
    TableauClientBootstrapRequiredError,
    TableauConfigurationError,
    TableauResponseError,
    TableauSessionError,
    discover_prebootstrap_configuration,
    sanitize_tableau_diagnostic_html,
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


def container_view_html() -> bytes:
    config = json.dumps(
        {
            "sessionid": "transient-123",
            "sheetId": "Main",
            "bootstrapSessionUrl": BOOTSTRAP,
        }
    )
    return (
        '<html><script src="/vizql/version/PreBootstrap.min.js"></script>'
        f'<textarea id="tsConfigContainer">{config}</textarea>'
        '<textarea id="staticConfigContainer">{"vizqlPrefix":"vizql"}</textarea>'
        "</html>"
    ).encode()


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

    with pytest.raises(TableauConfigurationError) as caught:
        provider(handler, view_url=None).retrieve()
    assert caught.value.code == "tableau_configuration_error"
    assert "does not contain an EMAS Tableau view configuration" in str(caught.value)


def test_failed_session_creation_has_governed_error():
    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        return httpx.Response(200, content=b"<html></html>", request=request)

    with pytest.raises(TableauSessionError) as caught:
        provider(handler).retrieve()
    assert caught.value.code == "tableau_session_error"
    assert "session configuration is missing" in str(caught.value)


def test_bootstrap_failure_has_governed_error():
    def handler(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(503, request=request)
        return successful_handler(request)

    with pytest.raises(TableauSessionError) as caught:
        provider(handler).retrieve()
    assert caught.value.code == "tableau_session_error"
    assert "bootstrap payload retrieval failed" in str(caught.value)


def test_unexpected_media_type_and_html_response_are_rejected():
    def wrong_media(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(
                200, content=b"data", headers={"Content-Type": "application/pdf"}, request=request
            )
        return successful_handler(request)

    with pytest.raises(TableauResponseError) as caught:
        provider(wrong_media).retrieve()
    assert caught.value.code == "tableau_response_error"
    assert "Unexpected FAA Tableau bootstrap media type" in str(caught.value)

    def html_response(request):
        if str(request.url) == BOOTSTRAP:
            return httpx.Response(
                200, content=b"<html></html>", headers={"Content-Type": "text/plain"}, request=request
            )
        return successful_handler(request)

    with pytest.raises(TableauResponseError) as caught:
        provider(html_response).retrieve()
    assert caught.value.code == "tableau_response_error"
    assert "empty or contains HTML" in str(caught.value)


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


def test_diagnostic_configuration_failure_writes_only_sanitized_html(tmp_path):
    sensitive = (
        '<html><script>sessionid="secret-session"; csrfToken="secret-csrf"; '
        'request_id="secret-request"; url="/sessions/path-session";</script></html>'
    ).encode()

    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        return httpx.Response(
            200,
            content=sensitive,
            headers={"Content-Type": "text/html"},
            request=request,
        )

    diagnostic_directory = tmp_path / "diagnostics"
    item = provider(handler)
    item.diagnostic_directory = diagnostic_directory
    with pytest.raises(TableauSessionError) as caught:
        item.retrieve()
    diagnostic = caught.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.path.parent == diagnostic_directory.resolve()
    assert diagnostic.http_status == 200
    assert diagnostic.response_byte_size == len(sensitive)
    saved = diagnostic.path.read_text(encoding="utf-8")
    assert "secret-session" not in saved
    assert "secret-csrf" not in saved
    assert "secret-request" not in saved
    assert "path-session" not in saved
    assert saved.count("[redacted]") == 4
    assert sessionless_html_marker(saved)


def sessionless_html_marker(value: str) -> bool:
    return value.startswith("<html><script>") and value.endswith("</script></html>")


def test_diagnostic_sanitizer_preserves_configuration_structure():
    value = sanitize_tableau_diagnostic_html(
        b'<script type="application/json">{"sheetId":"Main","sessionid":"abc"}</script>'
    )
    assert 'type="application/json"' in value
    assert '"sheetId":"Main"' in value
    assert '"sessionid":"[redacted]"' in value


def test_diagnostic_sanitizer_removes_ephemeral_telemetry_but_keeps_prebootstrap():
    payload = (
        b'<script src="/vizql/version/PreBootstrap.min.js"></script>'
        b'<script>window.BOOMR={request_id:"secret",client_ip:"192.0.2.1"}</script>'
        b'<script type="text/javascript" src="/akam/challenge"></script>'
        b'<noscript><img src="/akam/pixel?request_id=secret"></noscript>'
        b'<textarea id="tsConfigContainer"></textarea>'
    )
    saved = sanitize_tableau_diagnostic_html(payload)
    assert "PreBootstrap.min.js" in saved
    assert "tsConfigContainer" in saved
    assert "BOOMR" not in saved
    assert "192.0.2.1" not in saved
    assert "/akam/" not in saved


def test_authentic_populated_tsconfig_container_strategy_is_supported():
    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        if str(request.url) == VIEW:
            return httpx.Response(200, content=container_view_html(), request=request)
        return successful_handler(request)

    assert provider(handler).retrieve().content == BOOTSTRAP_BYTES


def test_legacy_strategy_precedes_empty_authentic_container():
    combined = view_html().replace(
        b"</html>", b'<textarea id="tsConfigContainer"></textarea></html>'
    )

    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        if str(request.url) == VIEW:
            return httpx.Response(200, content=combined, request=request)
        return successful_handler(request)

    assert provider(handler).retrieve().content == BOOTSTRAP_BYTES


def test_prebootstrap_only_authentic_shape_has_governed_blocker():
    authentic_shape = (
        b'<html><script src="/vizql/v_2025/javascripts/PreBootstrap.min.js"></script>'
        b'<textarea id="tsConfigContainer"></textarea>'
        b'<textarea id="staticConfigContainer">{"vizqlPrefix":"vizql"}</textarea>'
        b"</html>"
    )

    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        return httpx.Response(200, content=authentic_shape, request=request)

    with pytest.raises(TableauClientBootstrapRequiredError) as caught:
        provider(handler).retrieve()
    assert caught.value.code == "tableau_client_bootstrap_required"
    assert isinstance(caught.value, TableauSessionError)


def test_authentic_prebootstrap_asset_and_static_config_are_discovered():
    request = httpx.Request("GET", VIEW)
    response = httpx.Response(
        200,
        content=(
            b'<script src="/vizql/v_2025/javascripts/PreBootstrap.min.js"></script>'
            b'<textarea id="staticConfigContainer">'
            b'{"vizqlPrefix":"vizql","isAuthoring":false}'
            b'</textarea>'
        ),
        request=request,
    )
    discovery = discover_prebootstrap_configuration(response)
    assert discovery.asset_url == (
        "https://tableau.example/vizql/v_2025/javascripts/PreBootstrap.min.js"
    )
    assert discovery.static_config == {
        "vizqlPrefix": "vizql",
        "isAuthoring": False,
    }


def test_prebootstrap_discovery_rejects_missing_required_values():
    response = httpx.Response(
        200,
        content=b'<textarea id="staticConfigContainer">{}</textarea>',
        request=httpx.Request("GET", VIEW),
    )
    with pytest.raises(TableauConfigurationError) as caught:
        discover_prebootstrap_configuration(response)
    assert caught.value.code == "tableau_configuration_error"
    assert "PreBootstrap asset or static configuration is missing" in str(caught.value)


def test_prebootstrap_discovery_rejects_ambiguous_assets():
    response = httpx.Response(
        200,
        content=(
            b'<script src="/vizql/a/PreBootstrap.min.js"></script>'
            b'<script src="/vizql/b/PreBootstrap.min.js"></script>'
            b'<textarea id="staticConfigContainer">{}</textarea>'
        ),
        request=httpx.Request("GET", VIEW),
    )
    with pytest.raises(TableauConfigurationError) as caught:
        discover_prebootstrap_configuration(response)
    assert caught.value.code == "tableau_configuration_error"
    assert "multiple PreBootstrap asset candidates" in str(caught.value)


def test_prebootstrap_discovery_repr_omits_values_and_query_secrets():
    response = httpx.Response(
        200,
        content=(
            b'<script src="/vizql/PreBootstrap.min.js?requestId=secret"></script>'
            b'<textarea id="staticConfigContainer">'
            b'{"csrfToken":"secret-token","vizqlPrefix":"vizql"}'
            b'</textarea>'
        ),
        request=httpx.Request("GET", VIEW),
    )
    rendered = repr(discover_prebootstrap_configuration(response))
    assert "secret" not in rendered
    assert "csrfToken" in rendered
    assert "requestId" not in rendered


@pytest.mark.parametrize(
    "second_config, expected_detail",
    [
        (None, "duplicate (ambiguous)"),
        (
            '{"sessionid":"other","sheetId":"Main"}',
            "conflicting",
        ),
    ],
)
def test_multiple_configuration_candidates_fail_closed(second_config, expected_detail):
    legacy = view_html().decode().removesuffix("</html>")
    first_raw = legacy.split('<textarea id="tsConfig">', 1)[1].split("</textarea>", 1)[0]
    other = second_config or first_raw
    payload = (
        legacy + f'<textarea id="tsConfigContainer">{other}</textarea></html>'
    ).encode()

    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(200, content=article_html(), request=request)
        return httpx.Response(200, content=payload, request=request)

    with pytest.raises(TableauConfigurationError) as caught:
        provider(handler).retrieve()
    assert caught.value.code == "tableau_configuration_error"
    assert expected_detail in str(caught.value)
