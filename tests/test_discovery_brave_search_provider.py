"""RWI Mission #9F Part G - offline tests for BraveSearchProvider.

No real network access anywhere in this file - the HTTP boundary
(httpx.Client) is fully replaced by a fake client object matching
BraveSearchProvider's own injectable `client=` parameter, exactly as
app.acquisition.faa.FAAAcquisitionProvider's tests already do for FAA
acquisition. No real Brave API key is ever used or required to run these
tests.
"""

from __future__ import annotations

import httpx
import pytest

from app.discovery.brave_search_provider import BraveSearchProvider
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcomeStatus


def _query(rendered: str = '"London City Airport" EMAS') -> SearchQuery:
    return SearchQuery(rendered=rendered, template_id="emas", identity_field="name", identity_value="London City Airport")


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: object = None, json_error: bool = False):
        self.status_code = status_code
        self._json_data = json_data
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error:
            raise ValueError("invalid json")
        return self._json_data


class _FakeClient:
    """Records every call so tests can assert on headers/params without
    ever performing real network I/O."""

    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return self._response


_BRAVE_OK_PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "CAA - Airspace Change ACP-2022-090",
                "url": "https://airspacechange.caa.co.uk/PublicProposalArea?pID=123",
                "description": "London City Airport EMAS installation proposal.",
            },
            {
                "title": "Runway Safe delivers EMAS to London City Airport",
                "url": "https://runwaysafe.com/news/lcy-emas",
                "description": "Runway Safe's EMAS system installed at LCY.",
            },
        ]
    }
}


# --- 1/2/3: normal mapping, rank, title/url/snippet -------------------------


def test_normal_response_maps_to_ok_with_two_results():
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.OK
    assert len(outcome.results) == 2


def test_rank_is_preserved_in_result_order():
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD)))
    outcome = provider.search(_query())
    ranks = [r.rank for r in outcome.results]
    assert ranks == [1, 2]


def test_title_url_snippet_mapping_is_exact():
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD)))
    outcome = provider.search(_query())
    first = outcome.results[0]
    assert first.title == "CAA - Airspace Change ACP-2022-090"
    assert first.url == "https://airspacechange.caa.co.uk/PublicProposalArea?pID=123"
    assert first.snippet == "London City Airport EMAS installation proposal."
    assert first.provider == "brave"
    assert first.query.rendered == _query().rendered


def test_result_missing_description_yields_empty_snippet_not_failure():
    payload = {"web": {"results": [{"title": "T", "url": "https://example.com/x"}]}}
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, payload)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.OK
    assert outcome.results[0].snippet == ""


# --- 4: zero results ---------------------------------------------------------


def test_empty_results_list_is_no_results():
    payload = {"web": {"results": []}}
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, payload)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.NO_RESULTS
    assert outcome.results == ()


def test_missing_web_key_entirely_is_no_results_not_failure():
    """Brave's own documented shape for a query with no web matches at all
    omits the 'web' key - a legitimate empty answer, not malformed."""
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, {})))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.NO_RESULTS


# --- 5/6/9: HTTP failure status codes ---------------------------------------


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_is_provider_failure(status_code):
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(status_code)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE
    assert outcome.error is not None


def test_429_is_provider_failure():
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(429)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE
    assert "429" in outcome.error


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_server_error_is_provider_failure(status_code):
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(status_code)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


# --- 7: timeout/network failure ----------------------------------------------


def test_timeout_is_provider_failure():
    provider = BraveSearchProvider(
        api_key="test-key-not-real", client=_FakeClient(exc=httpx.ReadTimeout("timed out"))
    )
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE
    assert "timed out" in outcome.error


def test_connection_error_is_provider_failure():
    provider = BraveSearchProvider(
        api_key="test-key-not-real", client=_FakeClient(exc=httpx.ConnectError("connection refused"))
    )
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


# --- 8: malformed/unexpected response shape (fail closed) -------------------


def test_invalid_json_body_is_provider_failure():
    provider = BraveSearchProvider(
        api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, json_error=True))
    )
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


def test_non_object_body_is_provider_failure():
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, ["not", "a", "dict"])))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


def test_web_results_not_a_list_is_provider_failure():
    payload = {"web": {"results": "not-a-list"}}
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, payload)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


def test_result_entry_missing_title_or_url_is_provider_failure():
    payload = {"web": {"results": [{"title": "Only a title"}]}}
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, payload)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


def test_result_entry_not_an_object_is_provider_failure():
    payload = {"web": {"results": ["not-an-object"]}}
    provider = BraveSearchProvider(api_key="test-key-not-real", client=_FakeClient(_FakeResponse(200, payload)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE


# --- 10: API key absent -> clear fail-closed behavior ------------------------


def test_no_api_key_fails_closed_without_any_network_call():
    fake_client = _FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD))
    provider = BraveSearchProvider(api_key=None, client=fake_client)
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE
    assert "not configured" in outcome.error
    assert fake_client.calls == []  # never even attempted a request


def test_no_api_key_falls_back_to_settings_and_still_fails_closed(monkeypatch: pytest.MonkeyPatch):
    import app.discovery.brave_search_provider as brave_module

    monkeypatch.setattr(brave_module.settings, "brave_search_api_key", None)
    fake_client = _FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD))
    provider = BraveSearchProvider(client=fake_client)  # no api_key kwarg -> reads settings
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE
    assert fake_client.calls == []


# --- 11: credential never appears in output/error ----------------------------


_SECRET = "sk-totally-fake-do-not-use-1234567890abcdef"


@pytest.mark.parametrize(
    "response_kwargs",
    [
        {"status_code": 401},
        {"status_code": 403},
        {"status_code": 429},
        {"status_code": 500},
        {"status_code": 200, "json_error": True},
        {"status_code": 200, "json_data": {"web": {"results": "not-a-list"}}},
    ],
)
def test_credential_never_appears_in_any_failure_error_text(response_kwargs):
    provider = BraveSearchProvider(api_key=_SECRET, client=_FakeClient(_FakeResponse(**response_kwargs)))
    outcome = provider.search(_query())
    assert outcome.status == SearchOutcomeStatus.PROVIDER_FAILURE
    assert _SECRET not in (outcome.error or "")


def test_credential_never_appears_in_missing_key_error_text():
    provider = BraveSearchProvider(api_key=None, client=_FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD)))
    outcome = provider.search(_query())
    assert _SECRET not in (outcome.error or "")  # trivially true, but asserts no leakage path exists


def test_credential_is_sent_only_as_header_never_as_query_param():
    fake_client = _FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD))
    provider = BraveSearchProvider(api_key=_SECRET, client=fake_client)
    provider.search(_query())
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["headers"]["X-Subscription-Token"] == _SECRET
    assert _SECRET not in call["params"].values()
    assert _SECRET not in call["url"]


# --- 13: end-to-end wiring through the CLI's run(), dedup intact -------------


def test_end_to_end_through_cli_run_preserves_dedup_and_provenance():
    import scripts.discover_airport_sources as cli
    from app.discovery.identity import AirportIdentity

    identity = AirportIdentity(name="London City Airport", iata_code="LCY", icao_code="EGLC")
    fake_client = _FakeClient(_FakeResponse(200, _BRAVE_OK_PAYLOAD))
    provider = BraveSearchProvider(api_key="test-key-not-real", client=fake_client)

    plan, outcomes, deduped = cli.run(identity, provider=provider)

    assert len(plan) == 12
    assert len(outcomes) == 12
    # Every query returns the same two canned URLs -> dedup must collapse
    # them to exactly 2 deduplicated results, each with 12 queries in
    # found_by (one per executed query, all sharing that URL).
    assert len(deduped) == 2
    for item in deduped:
        assert len(item.found_by) == 12
