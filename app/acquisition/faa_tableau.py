from __future__ import annotations

import html
import json
from enum import Enum
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import urljoin

import httpx

from app.acquisition.faa import AcquisitionPayload, sanitized_headers
from app.config import settings


PROVIDER_VERSION = "faa-tableau-vizql/1"
_SUPPORTED_BOOTSTRAP_MEDIA_TYPES = {
    "application/octet-stream",
    "application/json",
    "text/plain",
}


class TableauAcquisitionErrorCode(str, Enum):
    CONFIGURATION_MISSING = "tableau_configuration_missing"
    SESSION_CREATION_FAILED = "tableau_session_creation_failed"
    BOOTSTRAP_RETRIEVAL_FAILED = "tableau_bootstrap_retrieval_failed"
    UNEXPECTED_MEDIA_TYPE = "tableau_unexpected_media_type"
    UNSUPPORTED_RESPONSE = "unsupported_tableau_response"


class TableauAcquisitionError(ValueError):
    def __init__(self, code: TableauAcquisitionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code.value


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tableau_view_url: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "iframe" or self.tableau_view_url is not None:
            return
        source = dict(attrs).get("src")
        if source and "EMASIncidentsandInstallations" in source:
            self.tableau_view_url = source


class _TableauConfigParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_config = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag.lower() == "textarea" and values.get("id") == "tsConfig":
            self._inside_config = True

    def handle_data(self, data: str) -> None:
        if self._inside_config:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "textarea" and self._inside_config:
            self._inside_config = False

    @property
    def raw_config(self) -> str:
        return "".join(self._parts).strip()


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
    ) -> None:
        self.source_url = article_url or settings.faa_emas_article_url
        self.tableau_view_url = tableau_view_url
        self.timeout_seconds = timeout_seconds or settings.acquisition_timeout_seconds
        self.user_agent = user_agent or settings.acquisition_user_agent
        self._client = client

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
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.CONFIGURATION_MISSING,
                "FAA EMAS article could not be retrieved.",
            ) from exc

        view_url = self.tableau_view_url or self._discover_view_url(article)
        if view_url is None:
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.CONFIGURATION_MISSING,
                "FAA article does not contain an EMAS Tableau view configuration.",
            )
        view_url = urljoin(str(article.url), html.unescape(view_url))

        try:
            view = client.get(view_url, headers=headers, timeout=self.timeout_seconds)
            view.raise_for_status()
            bootstrap_url, form = self._bootstrap_configuration(view)
        except TableauAcquisitionError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.SESSION_CREATION_FAILED,
                "FAA Tableau viewing session could not be established.",
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
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.BOOTSTRAP_RETRIEVAL_FAILED,
                "FAA Tableau bootstrap payload retrieval failed.",
            ) from exc

        content_type = bootstrap.headers.get("content-type")
        media_type = content_type.partition(";")[0].strip().lower() if content_type else ""
        if media_type not in _SUPPORTED_BOOTSTRAP_MEDIA_TYPES:
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.UNEXPECTED_MEDIA_TYPE,
                f"Unexpected FAA Tableau bootstrap media type: {media_type or 'missing'}.",
            )
        payload = bootstrap.content
        if not payload or payload.lstrip().lower().startswith((b"<html", b"<!doctype html")):
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.UNSUPPORTED_RESPONSE,
                "FAA Tableau bootstrap response is empty or contains HTML.",
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

    @staticmethod
    def _discover_view_url(response: httpx.Response) -> str | None:
        parser = _ArticleParser()
        parser.feed(response.text)
        return parser.tableau_view_url

    @staticmethod
    def _bootstrap_configuration(
        response: httpx.Response,
    ) -> tuple[str, dict[str, str]]:
        parser = _TableauConfigParser()
        parser.feed(response.text)
        if not parser.raw_config:
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.SESSION_CREATION_FAILED,
                "FAA Tableau tsConfig is missing.",
            )
        try:
            config = json.loads(parser.raw_config)
        except json.JSONDecodeError as exc:
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.SESSION_CREATION_FAILED,
                "FAA Tableau tsConfig is malformed.",
            ) from exc
        session_id = config.get("sessionid")
        endpoint = config.get("bootstrapSessionUrl")
        sheet_id = config.get("sheetId")
        if not session_id or not sheet_id:
            raise TableauAcquisitionError(
                TableauAcquisitionErrorCode.SESSION_CREATION_FAILED,
                "FAA Tableau session or sheet configuration is missing.",
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
