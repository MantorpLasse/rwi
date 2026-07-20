from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import httpx

from app.config import settings


PROVIDER_VERSION = "faa-http/1"
_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "set-cookie"}


def sanitized_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in response.headers.items()
        if key.lower() not in _SENSITIVE_HEADERS
    }


@dataclass(frozen=True)
class AcquisitionPayload:
    content: bytes
    request_url: str
    final_url: str
    retrieved_headers: dict[str, str]
    http_status: int
    content_type: str | None
    duration_seconds: float
    provider_version: str


class FAAAcquisitionProvider:
    version = PROVIDER_VERSION

    def __init__(
        self,
        source_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.source_url = source_url or settings.faa_emas_source_url
        self.timeout_seconds = timeout_seconds or settings.acquisition_timeout_seconds
        self._client = client

    def retrieve(self) -> AcquisitionPayload:
        started = perf_counter()
        if self._client is not None:
            response = self._client.get(self.source_url, timeout=self.timeout_seconds)
        else:
            with httpx.Client(follow_redirects=True) as client:
                response = client.get(self.source_url, timeout=self.timeout_seconds)
        response.raise_for_status()
        content = response.content
        if not content:
            raise ValueError("FAA acquisition returned an empty payload")
        return AcquisitionPayload(
            content=content,
            request_url=self.source_url,
            final_url=str(response.url),
            retrieved_headers=sanitized_headers(response),
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            duration_seconds=perf_counter() - started,
            provider_version=self.version,
        )
