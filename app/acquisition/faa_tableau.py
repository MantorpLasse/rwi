from __future__ import annotations

# If this server-side session flow starts failing often because Tableau requires
# running its JavaScript client to produce a real session (see
# TableauClientBootstrapRequiredError below), there's an already-verified fallback:
# capture the bootstrap payload manually via the browser's DevTools Network tab
# (filter on "bootstrap") and parse it offline. We already have a working parser for
# that format (length-prefixed JSON, two segments, a dataDictionary with
# cstring/real/integer columns referenced by index from paneColumnsData). Ask for
# that parser to be described in detail if you want it added here as a fallback.

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from time import perf_counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.acquisition.faa import AcquisitionPayload, sanitized_headers
from app.config import settings


PROVIDER_VERSION = "faa-tableau-vizql/1"
_SUPPORTED_BOOTSTRAP_MEDIA_TYPES = {
    "application/octet-stream",
    "application/json",
    "text/plain",
}


class TableauAcquisitionError(ValueError):
    """Base for all FAA Tableau acquisition failures."""

    code = "tableau_acquisition_error"

    def __init__(self, message: str, *, diagnostic: "TableauDiagnostic | None" = None) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class TableauConfigurationError(TableauAcquisitionError):
    """Configuration (view URL, session config, PreBootstrap asset) could not be
    found in, or unambiguously parsed from, the FAA article/view HTML."""

    code = "tableau_configuration_error"


class TableauSessionError(TableauAcquisitionError):
    """The Tableau viewing session or bootstrap request could not be established."""

    code = "tableau_session_error"


class TableauClientBootstrapRequiredError(TableauSessionError):
    """The HTML says the real session can only be produced by running Tableau's
    JavaScript client - see the module comment above for the DevTools fallback."""

    code = "tableau_client_bootstrap_required"


class TableauResponseError(TableauAcquisitionError):
    """The bootstrap response had the wrong shape: empty, wrong media type, or HTML
    instead of the expected opaque payload."""

    code = "tableau_response_error"


@dataclass(frozen=True)
class TableauDiagnostic:
    path: Path
    http_status: int
    final_url: str
    content_type: str | None
    response_byte_size: int


@dataclass(frozen=True, repr=False)
class TableauPreBootstrapDiscovery:
    """Safe, transient facts discoverable without running Tableau JavaScript."""

    asset_url: str
    static_config: dict[str, object]

    def __repr__(self) -> str:
        fields = ", ".join(sorted(self.static_config))
        return (
            "TableauPreBootstrapDiscovery("
            f"asset_url={_safe_diagnostic_url(self.asset_url)!r}, "
            f"static_config_fields={fields!r})"
        )


_SENSITIVE_VALUE = re.compile(
    r'(?i)((?:sessionid|session_id|csrf(?:token)?|request[_-]?id|authorization|cookie)'
    r'\s*["\']?\s*[:=]\s*["\'])([^"\'&<>\s]+)'
)
_SESSION_URL = re.compile(r"(?i)(/sessions/)[^/?#\"'<>\s]+")
_SENSITIVE_QUERY = re.compile(
    r"(?i)((?:sessionid|session_id|:sid|csrf|request[_-]?id)=)[^&#\"'<>\s]+"
)
_SCRIPT_BLOCK = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
_NOSCRIPT_BLOCK = re.compile(r"(?is)<noscript\b[^>]*>.*?</noscript>")


def _safe_diagnostic_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _remove_telemetry_script(match: re.Match[str]) -> str:
    block = match.group(0)
    lowered = block.lower()
    if any(marker in lowered for marker in ("boomr", "go-mpulse", "/akam/", "bazadebez")):
        return "<!-- removed unrelated telemetry script -->"
    return block


def sanitize_tableau_diagnostic_html(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    text = _SCRIPT_BLOCK.sub(_remove_telemetry_script, text)
    text = _NOSCRIPT_BLOCK.sub(
        lambda match: (
            "<!-- removed unrelated telemetry fallback -->"
            if "/akam/" in match.group(0).lower()
            else match.group(0)
        ),
        text,
    )
    text = _SENSITIVE_VALUE.sub(r"\1[redacted]", text)
    text = _SESSION_URL.sub(r"\1[redacted]", text)
    return _SENSITIVE_QUERY.sub(r"\1[redacted]", text)


class _TableauHtmlParser(HTMLParser):
    """Extracts everything we look for across the FAA article and Tableau view HTML.

    The article only ever contains the iframe, and the view only ever contains the
    textarea/script tags, so watching for all of them in one pass is safe.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tableau_view_url: str | None = None
        self._active_container: str | None = None
        self._parts: dict[str, list[str]] = {
            "tsConfig": [],
            "tsConfigContainer": [],
            "staticConfigContainer": [],
        }
        self.prebootstrap_scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        lowered_tag = tag.lower()
        if lowered_tag == "iframe" and self.tableau_view_url is None:
            source = values.get("src")
            if source and "EMASIncidentsandInstallations" in source:
                self.tableau_view_url = source
        elif lowered_tag == "textarea" and values.get("id") in self._parts:
            self._active_container = values["id"]
        elif lowered_tag == "script" and "PreBootstrap" in values.get("src", ""):
            self.prebootstrap_scripts.append(values["src"])

    def handle_data(self, data: str) -> None:
        if self._active_container is not None:
            self._parts[self._active_container].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "textarea":
            self._active_container = None

    def config(self, container: str) -> str:
        return "".join(self._parts[container]).strip()


def discover_prebootstrap_configuration(
    response: httpx.Response,
) -> TableauPreBootstrapDiscovery:
    """Return only configuration present in HTML; never infer runtime state."""

    parser = _TableauHtmlParser()
    parser.feed(response.text)
    distinct_assets = {urljoin(str(response.url), item) for item in parser.prebootstrap_scripts}
    if len(distinct_assets) > 1:
        raise TableauConfigurationError(
            "FAA Tableau response contains multiple PreBootstrap asset candidates."
        )
    raw_static_config = parser.config("staticConfigContainer")
    if not distinct_assets or not raw_static_config:
        raise TableauConfigurationError(
            "FAA Tableau PreBootstrap asset or static configuration is missing."
        )
    try:
        static_config = json.loads(raw_static_config)
    except json.JSONDecodeError as exc:
        raise TableauConfigurationError(
            "FAA Tableau static configuration is malformed."
        ) from exc
    if not isinstance(static_config, dict):
        raise TableauConfigurationError(
            "FAA Tableau static configuration is not an object."
        )
    return TableauPreBootstrapDiscovery(
        asset_url=distinct_assets.pop(),
        static_config=static_config,
    )


class FAATableauAcquisitionProvider:
    """Acquire one opaque FAA Tableau VizQL bootstrap response."""

    version = PROVIDER_VERSION

    def __init__(
        self,
        article_url: str | None = None,
        *,
        tableau_view_url: str | None = settings.faa_emas_tableau_view_url,
        client: httpx.Client | None = None,
        timeout_seconds: float | None = None,
        user_agent: str | None = None,
        diagnostic_directory: Path | None = None,
    ) -> None:
        self.source_url = article_url or settings.faa_emas_article_url
        self.tableau_view_url = tableau_view_url
        self.timeout_seconds = timeout_seconds or settings.acquisition_timeout_seconds
        self.user_agent = user_agent or settings.acquisition_user_agent
        self._client = client
        self.diagnostic_directory = diagnostic_directory

    def retrieve(self) -> AcquisitionPayload:
        started = perf_counter()
        if self._client is not None:
            return self._retrieve(self._client, started)
        with httpx.Client(follow_redirects=True) as client:
            return self._retrieve(client, started)

    def _retrieve(self, client: httpx.Client, started: float) -> AcquisitionPayload:
        headers = {"User-Agent": self.user_agent}
        try:
            article = client.get(
                self.source_url, headers=headers, timeout=self.timeout_seconds
            )
            article.raise_for_status()
        except httpx.HTTPError as exc:
            raise TableauConfigurationError(
                "FAA EMAS article could not be retrieved."
            ) from exc

        view_url = self.tableau_view_url or self._discover_view_url(article)
        if view_url is None:
            raise TableauConfigurationError(
                "FAA article does not contain an EMAS Tableau view configuration."
            )
        view_url = urljoin(str(article.url), html.unescape(view_url))

        try:
            view = client.get(view_url, headers=headers, timeout=self.timeout_seconds)
            view.raise_for_status()
            bootstrap_url, form = self._bootstrap_configuration(view)
        except TableauAcquisitionError as exc:
            if self.diagnostic_directory is not None:
                diagnostic = self._save_diagnostic(view)
                raise type(exc)(str(exc), diagnostic=diagnostic) from exc
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise TableauSessionError(
                "FAA Tableau viewing session could not be established."
            ) from exc

        try:
            bootstrap = client.post(
                bootstrap_url,
                data=form,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            bootstrap.raise_for_status()
        except httpx.HTTPError as exc:
            raise TableauSessionError(
                "FAA Tableau bootstrap payload retrieval failed."
            ) from exc

        content_type = bootstrap.headers.get("content-type")
        media_type = content_type.partition(";")[0].strip().lower() if content_type else ""
        if media_type not in _SUPPORTED_BOOTSTRAP_MEDIA_TYPES:
            raise TableauResponseError(
                f"Unexpected FAA Tableau bootstrap media type: {media_type or 'missing'}."
            )
        payload = bootstrap.content
        if not payload or payload.lstrip().lower().startswith((b"<html", b"<!doctype html")):
            raise TableauResponseError(
                "FAA Tableau bootstrap response is empty or contains HTML."
            )

        return AcquisitionPayload(
            content=payload,
            request_url=bootstrap_url,
            final_url=str(bootstrap.url),
            retrieved_headers=sanitized_headers(bootstrap),
            http_status=bootstrap.status_code,
            content_type=content_type,
            duration_seconds=perf_counter() - started,
            provider_version=self.version,
        )

    def _save_diagnostic(self, response: httpx.Response) -> TableauDiagnostic:
        directory = self.diagnostic_directory
        if directory is None:  # pragma: no cover - caller guards this path
            raise RuntimeError("Diagnostic directory is not configured.")
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"faa-tableau-view-{timestamp}.sanitized.html"
        sanitized = sanitize_tableau_diagnostic_html(response.content)
        path.write_text(sanitized, encoding="utf-8", newline="\n")
        return TableauDiagnostic(
            path=path.resolve(),
            http_status=response.status_code,
            final_url=str(response.url),
            content_type=response.headers.get("content-type"),
            response_byte_size=len(response.content),
        )

    @staticmethod
    def _discover_view_url(response: httpx.Response) -> str | None:
        parser = _TableauHtmlParser()
        parser.feed(response.text)
        return parser.tableau_view_url

    @staticmethod
    def _bootstrap_configuration(
        response: httpx.Response,
    ) -> tuple[str, dict[str, str]]:
        parser = _TableauHtmlParser()
        parser.feed(response.text)
        raw_candidates = [
            (name, parser.config(name))
            for name in ("tsConfig", "tsConfigContainer")
            if parser.config(name)
        ]
        if len(raw_candidates) > 1:
            distinct = {raw for _, raw in raw_candidates}
            detail = (
                "duplicate (ambiguous)"
                if len(distinct) == 1
                else "conflicting"
            )
            raise TableauConfigurationError(
                f"FAA Tableau response contains multiple {detail} session configurations."
            )
        if not raw_candidates:
            static_config = parser.config("staticConfigContainer")
            if parser.prebootstrap_scripts and static_config:
                raise TableauClientBootstrapRequiredError(
                    "FAA Tableau configuration requires client-side PreBootstrap execution."
                )
            raise TableauSessionError(
                "FAA Tableau session configuration is missing."
            )
        strategy, raw_config = raw_candidates[0]
        try:
            config = json.loads(raw_config)
        except json.JSONDecodeError as exc:
            raise TableauSessionError(
                f"FAA Tableau {strategy} is malformed."
            ) from exc
        session_id = config.get("sessionid")
        endpoint = config.get("bootstrapSessionUrl")
        sheet_id = config.get("sheetId")
        if not session_id or not sheet_id:
            raise TableauSessionError(
                "FAA Tableau session or sheet configuration is missing."
            )
        endpoint = endpoint or f"bootstrapSession/sessions/{session_id}"
        bootstrap_url = urljoin(str(response.url), endpoint)
        return bootstrap_url, {
            "sheetId": str(sheet_id),
            "clientDimension": '{"w":1280,"h":800,"d":1}',
            "renderMapsClientSide": "true",
            "isBrowserRenderingRequested": "true",
            "browserRenderingThreshold": "100",
            "apiID": "host0",
        }
