"""RWI Mission #9D Part K - SearchResult provenance, provider failure
semantics, and conservative deduplication tests."""

from datetime import datetime, timezone

import pytest

from app.discovery.dedup import DedupedResult, deduplicate_results, normalize_url
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult

_NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


def _q(template_id: str, rendered: str, identity_field: str = "name") -> SearchQuery:
    return SearchQuery(
        rendered=rendered, template_id=template_id, identity_field=identity_field, identity_value=rendered
    )


def _r(query: SearchQuery, url: str, title: str = "Title", rank: int = 1, provider: str = "fake") -> SearchResult:
    return SearchResult(
        query=query, rank=rank, title=title, url=url, snippet="snippet", discovered_at=_NOW, provider=provider
    )


# --- SearchOutcome / provider failure semantics (Part H) -------------------


def test_no_results_outcome_is_valid_and_carries_no_error():
    query = _q("emas", "Nowhere Airport EMAS")
    outcome = SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)
    assert outcome.results == ()
    assert outcome.error is None


def test_provider_failure_requires_error_message():
    query = _q("emas", "Nowhere Airport EMAS")
    with pytest.raises(ValueError):
        SearchOutcome(query=query, status=SearchOutcomeStatus.PROVIDER_FAILURE)


def test_provider_failure_with_error_is_valid_and_distinct_from_no_results():
    query = _q("emas", "Nowhere Airport EMAS")
    outcome = SearchOutcome(
        query=query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="timed out after 30s"
    )
    assert outcome.status != SearchOutcomeStatus.NO_RESULTS
    assert outcome.error == "timed out after 30s"


def test_ok_outcome_requires_at_least_one_result():
    query = _q("emas", "Nowhere Airport EMAS")
    with pytest.raises(ValueError):
        SearchOutcome(query=query, status=SearchOutcomeStatus.OK, results=())


def test_no_results_outcome_must_not_carry_results():
    query = _q("emas", "Nowhere Airport EMAS")
    result = _r(query, "https://example.com/a")
    with pytest.raises(ValueError):
        SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS, results=(result,))


# --- normalize_url -----------------------------------------------------------


def test_normalize_url_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/path") == normalize_url("https://example.com/path")


def test_normalize_url_strips_trailing_slash_on_non_root_path():
    assert normalize_url("https://example.com/path/") == normalize_url("https://example.com/path")


def test_normalize_url_preserves_root_slash():
    assert normalize_url("https://example.com/") == normalize_url("https://example.com")


def test_normalize_url_sorts_query_params():
    assert normalize_url("https://example.com/p?b=2&a=1") == normalize_url(
        "https://example.com/p?a=1&b=2"
    )


def test_normalize_url_drops_fragment():
    assert normalize_url("https://example.com/p#section") == normalize_url("https://example.com/p")


def test_normalize_url_does_not_conflate_different_query_values():
    assert normalize_url("https://example.com/p?id=1") != normalize_url("https://example.com/p?id=2")


# --- deduplicate_results (Part I) -------------------------------------------


def test_exact_url_duplicate_is_merged():
    q1 = _q("emas", "LCY EMAS")
    q2 = _q("resa", "LCY RESA")
    r1 = _r(q1, "https://caa.co.uk/lcy-emas", title="First")
    r2 = _r(q2, "https://caa.co.uk/lcy-emas", title="First (again)")
    deduped = deduplicate_results([r1, r2])
    assert len(deduped) == 1
    assert deduped[0].result.title == "First"  # first-seen wins


def test_normalized_url_duplicate_is_merged():
    q1 = _q("emas", "LCY EMAS")
    q2 = _q("resa", "LCY RESA")
    r1 = _r(q1, "https://caa.co.uk/lcy-emas/")
    r2 = _r(q2, "HTTPS://CAA.CO.UK/lcy-emas")
    deduped = deduplicate_results([r1, r2])
    assert len(deduped) == 1


def test_same_title_different_url_is_never_merged():
    q1 = _q("emas", "LCY EMAS")
    q2 = _q("emas", "EGLC EMAS")
    r1 = _r(q1, "https://caa.co.uk/lcy-emas", title="LCY EMAS installation")
    r2 = _r(q2, "https://runwaysafe.com/lcy-emas", title="LCY EMAS installation")
    deduped = deduplicate_results([r1, r2])
    assert len(deduped) == 2


def test_query_provenance_preserved_across_multiple_discoveries():
    q1 = _q("emas", "LCY EMAS")
    q2 = _q("resa", "LCY RESA")
    q3 = _q("construction", "London City Airport construction")
    r1 = _r(q1, "https://caa.co.uk/lcy-emas")
    r2 = _r(q2, "https://caa.co.uk/lcy-emas")
    r3 = _r(q3, "https://caa.co.uk/lcy-emas")
    deduped = deduplicate_results([r1, r2, r3])
    assert len(deduped) == 1
    assert deduped[0].found_by == (q1, q2, q3)


def test_query_provenance_does_not_duplicate_same_query_twice():
    q1 = _q("emas", "LCY EMAS")
    r1 = _r(q1, "https://caa.co.uk/lcy-emas", rank=1)
    r2 = _r(q1, "https://caa.co.uk/lcy-emas", rank=2)
    deduped = deduplicate_results([r1, r2])
    assert len(deduped) == 1
    assert deduped[0].found_by == (q1,)


def test_unrelated_urls_are_not_merged():
    q1 = _q("emas", "LCY EMAS")
    r1 = _r(q1, "https://caa.co.uk/lcy-emas")
    r2 = _r(q1, "https://runwaysafe.com/lcy-emas")
    deduped = deduplicate_results([r1, r2])
    assert len(deduped) == 2


def test_deduplicate_empty_input_returns_empty_list():
    assert deduplicate_results([]) == []
