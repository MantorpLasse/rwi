"""RWI HQ "Airport Official-Domain Discovery Query Pass" mission - offline
tests for app.discovery.official_domain_candidates. No network, no
database - hand-built SearchResult/DedupedResult/TriagedResult objects
only, matching tests/test_discovery_triage.py's own established
convention (independent copy, not imported, per this repo's own
"independent copy, same reasoning" discipline)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.discovery.dedup import DedupedResult
from app.discovery.official_domain_candidates import (
    KNOWN_NOISE_DOMAINS,
    OfficialDomainCandidate,
    group_candidates_by_hostname,
)
from app.discovery.query import SearchQuery
from app.discovery.search import SearchResult
from app.discovery.triage import DomainCategory, PriorityBand, TriagedResult

_NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def _q(rendered: str, template_id: str = "airport_official_domain_discovery_name") -> SearchQuery:
    return SearchQuery(rendered=rendered, template_id=template_id, identity_field="name", identity_value=rendered)


def _result(*, title: str, url: str, snippet: str = "", rank: int = 1, query: "SearchQuery | None" = None) -> SearchResult:
    return SearchResult(query=query or _q(title), rank=rank, title=title, url=url, snippet=snippet, discovered_at=_NOW, provider="brave")


def _triaged(*, url: str, band: PriorityBand, title: str = "A result", found_by=None, reasons=("test reason",)) -> TriagedResult:
    result = _result(title=title, url=url)
    deduped = DedupedResult(result=result, found_by=found_by or (result.query,))
    return TriagedResult(deduped=deduped, band=band, reasons=reasons, domain_category=DomainCategory.UNKNOWN)


# --- 5/6. Hostname extraction: only from the literal returned URL, www.
# normalization ---------------------------------------------------------


def test_hostname_comes_only_from_returned_url():
    t = _triaged(url="https://mspairport.com/some/path", band=PriorityBand.MEDIUM)
    candidates, _suppressed = group_candidates_by_hostname([t])
    assert candidates[0].hostname == "mspairport.com"
    assert candidates[0].best_url == "https://mspairport.com/some/path"


def test_www_prefix_stripped():
    t = _triaged(url="https://www.cltairport.com/", band=PriorityBand.MEDIUM)
    candidates, _ = group_candidates_by_hostname([t])
    assert candidates[0].hostname == "cltairport.com"


# 7. Duplicate hostnames group deterministically.
def test_duplicate_hostnames_group_deterministically():
    t1 = _triaged(url="https://cltairport.com/a", band=PriorityBand.MEDIUM, title="Page A")
    t2 = _triaged(url="https://www.cltairport.com/b", band=PriorityBand.HIGH, title="Page B")
    candidates, _ = group_candidates_by_hostname([t1, t2])
    assert len(candidates) == 1
    assert candidates[0].hostname == "cltairport.com"
    # the best (highest) band among the group wins, regardless of input order
    assert candidates[0].priority_band == PriorityBand.HIGH
    assert candidates[0].best_title == "Page B"

    # Deterministic regardless of input order.
    candidates_reversed, _ = group_candidates_by_hostname([t2, t1])
    assert candidates_reversed == candidates


# 8. Known noise domains are suppressed.
def test_known_noise_domains_are_suppressed():
    wiki = _triaged(url="https://en.wikipedia.org/wiki/Some_Airport", band=PriorityBand.HIGH)
    fb = _triaged(url="https://facebook.com/SomeAirport", band=PriorityBand.HIGH)
    flightaware = _triaged(url="https://flightaware.com/live/airport/XXX", band=PriorityBand.HIGH)
    real = _triaged(url="https://mspairport.com/", band=PriorityBand.MEDIUM)

    candidates, suppressed = group_candidates_by_hostname([wiki, fb, flightaware, real])
    assert {c.hostname for c in candidates} == {"mspairport.com"}
    assert set(suppressed) == {"en.wikipedia.org", "facebook.com", "flightaware.com"}


def test_all_named_noise_categories_are_present_in_the_curated_set():
    """Mission's own explicit named categories - proves each is actually
    represented, not just documented in prose."""
    expected_categories = {
        "en.wikipedia.org", "facebook.com", "x.com", "linkedin.com",
        "flightaware.com", "airnav.com", "flightradar24.com", "aopa.org",
        "airport.guide", "aa.com",
    }
    assert expected_categories <= KNOWN_NOISE_DOMAINS


# 9. Unknown domains are NOT automatically suppressed.
def test_unknown_domain_is_not_suppressed():
    t = _triaged(url="https://never-seen-before.example.net/x", band=PriorityBand.MEDIUM)
    candidates, suppressed = group_candidates_by_hostname([t])
    assert len(candidates) == 1
    assert candidates[0].hostname == "never-seen-before.example.net"
    assert suppressed == ()


# 10. Airport/operator-style candidate survives.
def test_airport_operator_style_candidate_survives():
    t = _triaged(url="https://binghamtonairport.com/", band=PriorityBand.MEDIUM)
    candidates, suppressed = group_candidates_by_hostname([t])
    assert candidates[0].hostname == "binghamtonairport.com"
    assert suppressed == ()


# 11. Local/regional government-style candidate survives.
def test_local_government_style_candidate_survives():
    t = _triaged(url="https://fultoncountyga.gov/", band=PriorityBand.MEDIUM)
    candidates, suppressed = group_candidates_by_hostname([t])
    assert candidates[0].hostname == "fultoncountyga.gov"
    assert suppressed == ()


# 12. FAA-style candidate is not blanket-suppressed.
def test_faa_style_candidate_is_not_suppressed():
    t = _triaged(url="https://faa.gov/airports/some-page", band=PriorityBand.MEDIUM)
    candidates, suppressed = group_candidates_by_hostname([t])
    assert candidates[0].hostname == "faa.gov"
    assert suppressed == ()
    assert "faa.gov" not in KNOWN_NOISE_DOMAINS


# 14. SearchResult remains non-evidence - candidate carries no
# evidence/acceptance-shaped field.
def test_candidate_carries_no_evidence_or_acceptance_field():
    t = _triaged(url="https://mspairport.com/", band=PriorityBand.MEDIUM)
    candidates, _ = group_candidates_by_hostname([t])
    candidate = candidates[0]
    for forbidden in ("evidence", "accepted", "confirmed", "governed", "is_official"):
        assert not hasattr(candidate, forbidden)
    assert isinstance(candidate, OfficialDomainCandidate)


# Query convergence is a secondary sort key only, never proof - a noise
# domain found by many queries is still suppressed entirely, and among
# survivors, band always outranks raw convergence count.
def test_convergence_is_secondary_never_overrides_band_or_rescues_noise():
    q1, q2, q3 = _q("query one"), _q("query two"), _q("query three")
    noisy_but_convergent = TriagedResult(
        deduped=DedupedResult(result=_result(title="Wikipedia page", url="https://en.wikipedia.org/wiki/X", query=q1), found_by=(q1, q2, q3)),
        band=PriorityBand.HIGH, reasons=("test",), domain_category=DomainCategory.UNKNOWN,
    )
    weak_convergence_real = _triaged(url="https://cltairport.com/", band=PriorityBand.MEDIUM, found_by=(q1,))
    strong_band_real = _triaged(url="https://faa.gov/x", band=PriorityBand.HIGH, found_by=(q1,))

    candidates, suppressed = group_candidates_by_hostname([noisy_but_convergent, weak_convergence_real, strong_band_real])
    assert "en.wikipedia.org" in suppressed
    hostnames_in_order = [c.hostname for c in candidates]
    assert hostnames_in_order[0] == "faa.gov"  # HIGH band beats MEDIUM regardless of convergence


# 15. No persistence occurs - this module never imports a Session/engine
# or any model, verified both by direct AST inspection and by proving the
# grouping function can run against zero rows fine.
def test_no_persistence_imports_in_module():
    import ast
    import inspect

    import app.discovery.official_domain_candidates as module

    tree = ast.parse(inspect.getsource(module))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for forbidden in ("app.models", "app.database", "sqlalchemy", "app.services"):
        assert not any(forbidden in name for name in imported), f"forbidden import matching {forbidden!r}: {imported}"


def test_empty_input_produces_empty_output():
    candidates, suppressed = group_candidates_by_hostname([])
    assert candidates == ()
    assert suppressed == ()


def test_supporting_result_count_counts_distinct_queries():
    q1, q2 = _q("query one"), _q("query two")
    r1 = _result(title="Page 1", url="https://cltairport.com/a", query=q1)
    r2 = _result(title="Page 2", url="https://cltairport.com/b", query=q2)
    t1 = TriagedResult(deduped=DedupedResult(result=r1, found_by=(q1,)), band=PriorityBand.MEDIUM, reasons=(), domain_category=DomainCategory.UNKNOWN)
    t2 = TriagedResult(deduped=DedupedResult(result=r2, found_by=(q2,)), band=PriorityBand.MEDIUM, reasons=(), domain_category=DomainCategory.UNKNOWN)
    candidates, _ = group_candidates_by_hostname([t1, t2])
    assert candidates[0].supporting_result_count == 2
