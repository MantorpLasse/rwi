"""Brave Search API adapter (RWI Mission #9F, Part C).

Implements the existing app.discovery.search.SearchProvider protocol
against Brave's official Web Search API
(https://api.search.brave.com/res/v1/web/search - see Mission #9E's
provider recon report). Uses ONLY the raw Web Search endpoint - never
Brave's separate Answers/AI-summary product - preserving the raw-results-
with-provenance boundary Mission #9E's Part H required. This adapter
carries no domain semantics: it only ever reads `query.rendered` and maps
Brave's JSON shape to SearchResult/SearchOutcome. It works identically for
an AirportIdentity-derived SearchQuery today or a future identity-free
Global Discovery SearchQuery - nothing here assumes an airport exists.

CREDENTIAL SAFETY: the API key is read once at construction (defaulting to
app.config.settings.brave_search_api_key, matching the existing
app.acquisition.faa.FAAAcquisitionProvider convention), sent ONLY as the
X-Subscription-Token request header, and never appears in any log, print,
or exception message this module raises - every error string here is built
from HTTP status codes and provider-supplied shape information, never from
the key itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.config import settings
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult

BRAVE_WEB_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
PROVIDER_NAME = "brave"

# Sentinel distinguishing "api_key not passed at all" (use settings) from
# "api_key explicitly passed as None" (force no key, e.g. for a test
# proving fail-closed behavior regardless of what is configured in the
# environment) - a bare `str | None = None` default cannot tell these
# apart, which is a real gap, not a style preference.
_UNSET = object()


class BraveSearchProvider:
    """Thin adapter: SearchQuery -> one Brave Web Search HTTP request ->
    SearchOutcome. See module docstring for the credential-safety and
    raw-results-only guarantees this class makes."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str | None = _UNSET,  # type: ignore[assignment]
        client: httpx.Client | None = None,
        timeout_seconds: float | None = None,
        country: str | None = None,
        search_lang: str | None = None,
        count: int = 20,
    ) -> None:
        self._api_key = settings.brave_search_api_key if api_key is _UNSET else api_key
        self._client = client
        self.timeout_seconds = timeout_seconds or settings.acquisition_timeout_seconds
        self.country = country
        self.search_lang = search_lang
        self.count = count

    def search(self, query: SearchQuery) -> SearchOutcome:
        if not self._api_key:
            return _failure(
                query, "Brave Search API key is not configured (BRAVE_SEARCH_API_KEY)."
            )

        params: dict[str, str] = {"q": query.rendered, "count": str(self.count)}
        if self.country:
            params["country"] = self.country
        if self.search_lang:
            params["search_lang"] = self.search_lang
        headers = {"Accept": "application/json", "X-Subscription-Token": self._api_key}

        try:
            response = self._get(params=params, headers=headers)
        except httpx.TimeoutException:
            return _failure(query, f"Brave Search request timed out after {self.timeout_seconds}s")
        except httpx.RequestError as exc:
            return _failure(query, f"Brave Search network error: {type(exc).__name__}")

        failure = _failure_for_status(query, response.status_code)
        if failure is not None:
            return failure

        try:
            payload = response.json()
        except ValueError:
            return _failure(query, "Brave Search returned a response that was not valid JSON")

        return _outcome_from_payload(query, payload, provider_name=self.name)

    def _get(self, *, params: dict[str, str], headers: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return self._client.get(
                BRAVE_WEB_SEARCH_ENDPOINT, params=params, headers=headers, timeout=self.timeout_seconds
            )
        with httpx.Client() as client:
            return client.get(
                BRAVE_WEB_SEARCH_ENDPOINT, params=params, headers=headers, timeout=self.timeout_seconds
            )


def _failure(query: SearchQuery, error: str) -> SearchOutcome:
    return SearchOutcome(query=query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error=error)


def _failure_for_status(query: SearchQuery, status_code: int) -> SearchOutcome | None:
    if status_code in (401, 403):
        return _failure(query, f"Brave Search authentication failed (HTTP {status_code})")
    if status_code == 429:
        return _failure(query, "Brave Search rate limit/quota exceeded (HTTP 429)")
    if status_code >= 500:
        return _failure(query, f"Brave Search server error (HTTP {status_code})")
    if status_code != 200:
        return _failure(query, f"Brave Search returned unexpected HTTP {status_code}")
    return None


def _outcome_from_payload(query: SearchQuery, payload: object, *, provider_name: str) -> SearchOutcome:
    if not isinstance(payload, dict):
        return _failure(query, "Brave Search response body was not a JSON object")

    web = payload.get("web")
    if web is None:
        # Brave's own documented shape for "no web results for this query"
        # omits the "web" key entirely - a legitimate empty answer, not a
        # malformed response.
        return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)
    if not isinstance(web, dict):
        return _failure(query, "Brave Search response 'web' field was not an object")

    raw_results = web.get("results")
    if raw_results is None:
        return _failure(query, "Brave Search response 'web' object had no 'results' field")
    if not isinstance(raw_results, list):
        return _failure(query, "Brave Search response 'web.results' was not a list")
    if not raw_results:
        return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)

    discovered_at = datetime.now(timezone.utc)
    results: list[SearchResult] = []
    for rank, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            return _failure(query, "Brave Search response contained a non-object result entry")
        title = item.get("title")
        url = item.get("url")
        if not title or not url:
            return _failure(query, "Brave Search result entry missing required 'title'/'url'")
        results.append(
            SearchResult(
                query=query,
                rank=rank,
                title=title,
                url=url,
                snippet=item.get("description") or "",
                discovered_at=discovered_at,
                provider=provider_name,
            )
        )

    return SearchOutcome(query=query, status=SearchOutcomeStatus.OK, results=tuple(results))
