"""RWI Mission #9D Part K - CLI tests, offline only (fake providers, no
subprocess, no network, no database)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import scripts.discover_airport_sources as cli
from app.discovery.identity import AirportIdentity
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult

_NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


class _FakeProvider:
    name = "fake"

    def __init__(self, canned: dict[str, SearchOutcome]):
        self._canned = canned

    def search(self, query: SearchQuery) -> SearchOutcome:
        return self._canned.get(
            query.rendered, SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)
        )


def test_run_with_no_provider_only_returns_the_plan():
    identity = AirportIdentity(name="London City Airport", iata_code="LCY", icao_code="EGLC")
    plan, outcomes, deduped = cli.run(identity, provider=None)
    assert len(plan) == 12
    assert outcomes == []
    assert deduped == []


def test_run_with_fake_provider_executes_every_query_and_dedupes():
    identity = AirportIdentity(name="London City Airport", iata_code="LCY")
    plan = None

    def make_result(query: SearchQuery, url: str) -> SearchResult:
        return SearchResult(
            query=query, rank=1, title="A result", url=url, snippet="s", discovered_at=_NOW, provider="fake"
        )

    # Build canned answers after generating the plan once, keyed by rendered text.
    from app.discovery.query import build_search_plan

    plan = build_search_plan(identity)
    emas_name_q = next(q for q in plan if q.template_id == "emas" and q.identity_field == "name")
    emas_iata_q = next(q for q in plan if q.template_id == "emas" and q.identity_field == "iata_code")

    canned = {
        emas_name_q.rendered: SearchOutcome(
            query=emas_name_q,
            status=SearchOutcomeStatus.OK,
            results=(make_result(emas_name_q, "https://caa.co.uk/lcy-emas"),),
        ),
        emas_iata_q.rendered: SearchOutcome(
            query=emas_iata_q,
            status=SearchOutcomeStatus.OK,
            results=(make_result(emas_iata_q, "https://caa.co.uk/lcy-emas"),),  # same URL
        ),
    }
    provider = _FakeProvider(canned)

    ran_plan, outcomes, deduped = cli.run(identity, provider=provider)
    assert ran_plan == plan
    assert len(outcomes) == len(plan)  # every query executed
    assert len(deduped) == 1  # the two OK outcomes shared one URL
    assert deduped[0].found_by == (emas_name_q, emas_iata_q)


def test_run_distinguishes_provider_failure_from_no_results():
    identity = AirportIdentity(name="Nowhere Airport")
    from app.discovery.query import build_search_plan

    plan = build_search_plan(identity)
    failing_query = plan[0]
    canned = {
        failing_query.rendered: SearchOutcome(
            query=failing_query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="HTTP 503"
        )
    }
    provider = _FakeProvider(canned)
    _, outcomes, _ = cli.run(identity, provider=provider)
    statuses = {o.query.rendered: o.status for o in outcomes}
    assert statuses[failing_query.rendered] == SearchOutcomeStatus.PROVIDER_FAILURE
    other = next(q for q in plan if q.rendered != failing_query.rendered)
    assert statuses[other.rendered] == SearchOutcomeStatus.NO_RESULTS


def test_main_with_no_provider_prints_queries_and_no_network_notice(capsys: pytest.CaptureFixture):
    exit_code = cli.main(["--name", "London City Airport", "--iata", "LCY", "--icao", "EGLC"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Generated 12 deterministic queries" in out
    assert "NO SEARCH PROVIDER CONFIGURED" in out
    assert "EMAS" in out


def test_main_with_unregistered_provider_name_fails_closed(capsys: pytest.CaptureFixture):
    exit_code = cli.main(["--name", "Test Airport", "--provider", "not-a-real-provider"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "Unknown --provider" in err
    assert "not-a-real-provider" in err


def test_provider_registry_has_exactly_brave():
    """Mission #9F: Brave is RWI's first real, deliberately-registered
    search provider (see the Mission #9E provider recon and #9F HQ
    report). Any future entry must be added deliberately, never invented
    to make this test pass - updated from #9D's original "empty registry"
    assertion because BLOCKED BY SEARCH PROVIDER is no longer RWI's real
    state."""
    assert set(cli.PROVIDER_REGISTRY) == {"brave"}
    assert cli.PROVIDER_REGISTRY["brave"].name == "brave"


def test_main_json_output_is_valid_json(capsys: pytest.CaptureFixture):
    import json

    exit_code = cli.main(["--name", "Test Airport", "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["identity"]["name"] == "Test Airport"
    assert len(payload["queries"]) == 8  # name-only identity -> one query per concept
    assert payload["provider"] is None
    assert payload["outcomes"] == []
    assert payload["deduplicated_results"] == []


def test_main_missing_required_name_errors(capsys: pytest.CaptureFixture):
    with pytest.raises(SystemExit):
        cli.main([])


def test_json_output_has_no_triage_key_when_triage_not_requested(capsys: pytest.CaptureFixture):
    """Mission #10B Part O #21: normal non-triage CLI behavior is unchanged."""
    import json

    exit_code = cli.main(["--name", "Test Airport", "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert "triage" not in payload


def test_triage_flag_without_provider_behaves_identically_to_no_triage_flag(capsys: pytest.CaptureFixture):
    """--triage has no effect without --provider (Mission #10B): output
    must be byte-identical to the pre-existing no-provider behavior."""
    baseline_code = cli.main(["--name", "London City Airport", "--iata", "LCY", "--icao", "EGLC"])
    baseline_out = capsys.readouterr().out

    triage_code = cli.main(["--name", "London City Airport", "--iata", "LCY", "--icao", "EGLC", "--triage"])
    triage_out = capsys.readouterr().out

    assert baseline_code == triage_code == 0
    assert baseline_out == triage_out
