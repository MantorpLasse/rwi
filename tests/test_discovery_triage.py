"""RWI Mission #10B Part O - offline tests for app.discovery.triage.

No network, no database. All fixtures are hand-built SearchResult/
SearchQuery/DedupedResult objects, plus the exact real domains/titles
observed in Mission #10A/#9F.1's live LCY/YTZ runs, frozen here as
regression fixtures (never re-fetched)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.discovery.dedup import DedupedResult
from app.discovery.identity import AirportIdentity
from app.discovery.query import SearchQuery
from app.discovery.search import SearchResult
from app.discovery.triage import (
    CONCEPT_TERMS,
    STRONG_CONCEPT_TERMS,
    WEAK_CONCEPT_TERMS,
    DomainCategory,
    PriorityBand,
    classify_domain,
    triage_results,
)

_NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)

_LCY_IDENTITY = AirportIdentity(name="London City Airport", iata_code="LCY", icao_code="EGLC")


def _q(rendered: str, template_id: str = "emas", identity_field: str = "name") -> SearchQuery:
    return SearchQuery(rendered=rendered, template_id=template_id, identity_field=identity_field, identity_value=rendered)


def _result(*, title: str, url: str, snippet: str = "", rank: int = 1, query: SearchQuery | None = None) -> SearchResult:
    return SearchResult(
        query=query or _q(title),
        rank=rank,
        title=title,
        url=url,
        snippet=snippet,
        discovered_at=_NOW,
        provider="brave",
    )


def _deduped(result: SearchResult, found_by: tuple[SearchQuery, ...] | None = None) -> DedupedResult:
    return DedupedResult(result=result, found_by=found_by or (result.query,))


# --- 1/2: regulator domain + relevance vs domain alone -----------------------


def test_regulator_domain_plus_identity_and_concept_ranks_high():
    r = _result(
        title="Page 1 of 16 LCY EMAS ACP ACP-2022-090 London City Airport",
        url="https://airspacechange.caa.co.uk/documents/download/5487",
    )
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band == PriorityBand.HIGH
    assert "Regulator domain" in triaged[0].reasons


def test_regulator_domain_alone_cannot_rank_high():
    """The exact real Mission #10A false-positive shape: a .gov-ish page
    that mentions the airport but has no safety-concept relevance."""
    r = _result(
        title="Current Weather Conditions: London City Airport",
        url="https://tgftp.nws.noaa.gov/weather/current/EGLC.html",
    )
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band != PriorityBand.HIGH


def test_regulator_domain_with_identity_but_no_concept_cannot_rank_high():
    r = _result(
        title="LONDON CITY AIRPORT LIMITED overview - company information",
        url="https://find-and-update.company-information.service.gov.uk/company/01963361",
    )
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band != PriorityBand.HIGH


# --- 3: broad .gov/.gov.uk pattern is not authority --------------------------


def test_broad_gov_uk_domain_not_in_curated_registry():
    assert classify_domain("https://www.gov.uk/government/news/example") == DomainCategory.UNKNOWN
    assert classify_domain("https://tgftp.nws.noaa.gov/weather/current/EGLC.html") == DomainCategory.UNKNOWN
    assert classify_domain("https://find-and-update.company-information.service.gov.uk/x") == DomainCategory.UNKNOWN


def test_curated_regulator_domains_recognized():
    assert classify_domain("https://airspacechange.caa.co.uk/documents/download/5487") == DomainCategory.REGULATOR
    assert classify_domain("https://www.caa.co.uk/our-work/publications/x") == DomainCategory.REGULATOR
    assert classify_domain("https://tc.canada.ca/en/binder/x") == DomainCategory.REGULATOR
    assert classify_domain("https://www.toronto.ca/city-government/x") == DomainCategory.REGULATOR


def test_curated_vendor_contractor_domains_recognized():
    assert classify_domain("https://runwaysafe.com/london-city-airport-emas") == DomainCategory.VENDOR_CONTRACTOR
    assert classify_domain("https://www.blu-3.co.uk/blu-3-secures-x") == DomainCategory.VENDOR_CONTRACTOR


# --- 4/5/6: identity matching, token-safe -------------------------------------


def test_exact_airport_name_in_title_is_a_reason():
    r = _result(title="London City Airport deploys EMAS", url="https://example.com/a")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "Exact airport name in title" in triaged[0].reasons


def test_iata_token_safe_matching_rejects_embedded_substring():
    r = _result(title="OLCYX unrelated product page", url="https://example.com/olcyx")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "IATA in title" not in triaged[0].reasons


def test_iata_token_safe_matching_accepts_bounded_token():
    r = _result(title="London City Airport (LCY) EMAS update", url="https://example.com/lcy-emas")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "IATA in title" in triaged[0].reasons


def test_icao_token_safe_matching_rejects_embedded_substring():
    r = _result(title="XEGLCX unrelated string", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "ICAO in title" not in triaged[0].reasons


def test_icao_token_safe_matching_accepts_bounded_token():
    r = _result(title="EGLC EMAS installation notice", url="https://example.com/eglc")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "ICAO in title" in triaged[0].reasons


# --- 7/8: EMAS / RESA title match --------------------------------------------


def test_emas_in_title_is_a_reason():
    r = _result(title="London City Airport EMAS installed", url="https://example.com/a")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "EMAS in title" in triaged[0].reasons


def test_resa_in_title_is_a_reason():
    r = _result(
        title="Runway End Safety Area Project - Billy Bishop Toronto City Airport",
        url="https://tc.canada.ca/en/binder/x",
    )
    identity = AirportIdentity(name="Billy Bishop Toronto City Airport", iata_code="YTZ", icao_code="CYTZ")
    triaged = triage_results([_deduped(r)], identity=identity)
    assert any("RESA" == reason.split(" in ")[0] or "runway end safety area" in reason.lower() for reason in triaged[0].reasons)


# --- 9: concept alone without context does not auto-rank HIGH ---------------


def test_bare_concept_match_alone_cannot_rank_high():
    """Generic Wikipedia-style page: concept term present, no identity, no
    curated domain. Must not reach HIGH even with multi-query provenance."""
    q1, q2, q3 = _q("EMAS airport", "emas"), _q("arresting system airport", "arresting_system"), _q("RESA airport", "resa")
    r = _result(title="Engineered materials arrestor system - Wikipedia", url="https://en.wikipedia.org/wiki/Engineered_materials_arrestor_system", query=q1)
    triaged = triage_results([_deduped(r, found_by=(q1, q2, q3))], identity=None)
    assert triaged[0].band != PriorityBand.HIGH
    assert triaged[0].band == PriorityBand.MEDIUM


def test_concept_plus_identity_no_domain_can_still_rank_high():
    r = _result(title="London City Airport EMAS project", url="https://example.com/independent-blog")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band == PriorityBand.HIGH


# --- 10: snippet weaker than title -------------------------------------------


def test_snippet_only_concept_match_scores_lower_than_title_match():
    title_hit = _result(title="London City Airport EMAS", url="https://a.example.com/x")
    snippet_hit = _result(title="London City Airport safety news", snippet="Discusses EMAS briefly.", url="https://b.example.com/y")
    triaged = triage_results([_deduped(title_hit), _deduped(snippet_hit)], identity=_LCY_IDENTITY)
    # Title-match result must be ranked strictly ahead of the snippet-only one.
    urls_in_order = [t.deduped.result.url for t in triaged]
    assert urls_in_order.index("https://a.example.com/x") < urls_in_order.index("https://b.example.com/y")
    snippet_result = next(t for t in triaged if t.deduped.result.url == "https://b.example.com/y")
    assert "EMAS in snippet" in snippet_result.reasons
    assert "EMAS in title" not in snippet_result.reasons


def test_term_matched_in_title_is_not_also_reported_for_snippet():
    r = _result(title="London City Airport EMAS", snippet="More about EMAS here.", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].reasons.count("EMAS in title") == 1
    assert "EMAS in snippet" not in triaged[0].reasons


# --- 11: PDF/document hint ----------------------------------------------------


def test_pdf_url_is_a_reason():
    r = _result(title="Runway End Safety Areas (RESA)", url="https://www.toronto.ca/legdocs/mmis/2024/ex/bgrd/backgroundfile-248778.pdf")
    triaged = triage_results([_deduped(r)], identity=None)
    assert "PDF/document result" in triaged[0].reasons


def test_documents_path_is_a_reason():
    r = _result(title="LCY EMAS ACP", url="https://airspacechange.caa.co.uk/documents/download/5487")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "PDF/document result" in triaged[0].reasons


# --- 12: multi-query provenance reason ---------------------------------------


def test_multi_query_provenance_reason_present_at_two_or_more():
    q1, q2 = _q("a"), _q("b")
    r = _result(title="London City Airport EMAS", url="https://example.com/x", query=q1)
    triaged = triage_results([_deduped(r, found_by=(q1, q2))], identity=_LCY_IDENTITY)
    assert "Surfaced by 2 search queries" in triaged[0].reasons


def test_single_query_provenance_has_no_multi_query_reason():
    r = _result(title="London City Airport EMAS", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert not any(reason.startswith("Surfaced by") for reason in triaged[0].reasons)


# --- 13: unknown domain not penalized ----------------------------------------


def test_unknown_domain_still_reaches_high_via_identity_and_concept():
    r = _result(title="London City Airport EMAS coverage", url="https://randomnewsblog.example/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].domain_category == DomainCategory.UNKNOWN
    assert triaged[0].band == PriorityBand.HIGH  # domain unknown did not block HIGH; identity+concept sufficed


# --- 14: same-title different URLs stay separate ------------------------------


def test_same_title_different_urls_remain_two_separate_triaged_results():
    r1 = _result(title="London City Airport EMAS installation", url="https://a.example.com/x")
    r2 = _result(title="London City Airport EMAS installation", url="https://b.example.com/y")
    triaged = triage_results([_deduped(r1), _deduped(r2)], identity=_LCY_IDENTITY)
    assert len(triaged) == 2
    assert {t.deduped.result.url for t in triaged} == {"https://a.example.com/x", "https://b.example.com/y"}


# --- 15: query provenance preserved ------------------------------------------


def test_query_provenance_preserved_through_triage():
    q1, q2 = _q("query one"), _q("query two")
    r = _result(title="London City Airport EMAS", url="https://example.com/x", query=q1)
    triaged = triage_results([_deduped(r, found_by=(q1, q2))], identity=_LCY_IDENTITY)
    assert triaged[0].deduped.found_by == (q1, q2)


# --- 16: deterministic ordering -----------------------------------------------


def test_ordering_is_deterministic_across_repeated_calls():
    results = [
        _deduped(_result(title="London City Airport EMAS A", url="https://a.example.com/1")),
        _deduped(_result(title="Weather London City Airport", url="https://weather.example.gov/2")),
        _deduped(_result(title="London City Airport RESA B", url="https://b.example.com/3")),
    ]
    first = triage_results(results, identity=_LCY_IDENTITY)
    second = triage_results(results, identity=_LCY_IDENTITY)
    assert [t.deduped.result.url for t in first] == [t.deduped.result.url for t in second]


def test_high_band_always_sorted_before_medium_and_low():
    results = [
        _deduped(_result(title="Irrelevant page", url="https://x.example.com/noise")),
        _deduped(_result(title="London City Airport EMAS installed", url="https://airspacechange.caa.co.uk/documents/download/5487")),
    ]
    triaged = triage_results(results, identity=_LCY_IDENTITY)
    bands = [t.band for t in triaged]
    assert bands == sorted(bands, key=lambda b: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[b.value])


# --- 17/18: no numeric score leaks -------------------------------------------


def test_triaged_result_dataclass_has_no_score_field():
    r = _result(title="London City Airport EMAS", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    field_names = {f for f in vars(triaged[0]).keys()}
    assert "score" not in field_names
    assert "points" not in field_names
    assert "weight" not in field_names


def test_cli_human_triage_output_never_prints_a_bare_numeric_score(capsys):
    import scripts.discover_airport_sources as cli

    class _FakeProvider:
        name = "fake"

        def search(self, query):
            from app.discovery.search import SearchOutcome, SearchOutcomeStatus

            return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)

    identity = AirportIdentity(name="Test Airport")
    plan, outcomes, deduped = cli.run(identity, provider=_FakeProvider())
    triaged = triage_results(deduped, identity=identity)
    cli._print_human_triage(identity, plan, outcomes, triaged, "fake")
    out = capsys.readouterr().out
    for forbidden in ("score", "points", "weight", "confidence", "probability"):
        assert forbidden not in out.lower()


def test_cli_json_triage_output_has_no_score_field(capsys):
    import json

    import scripts.discover_airport_sources as cli

    q = _q("London City Airport EMAS")
    r = _result(title="London City Airport EMAS", url="https://airspacechange.caa.co.uk/documents/download/5487", query=q)

    class _FakeProvider:
        name = "fake"

        def search(self, query):
            from app.discovery.search import SearchOutcome, SearchOutcomeStatus

            if query.rendered == q.rendered:
                return SearchOutcome(query=query, status=SearchOutcomeStatus.OK, results=(r,))
            return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)

    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    cli._print_json(_LCY_IDENTITY, [q], [], [], "fake", triaged)
    out = capsys.readouterr().out
    payload = json.loads(out)
    for entry in payload["triage"]["HIGH"] + payload["triage"]["MEDIUM"] + payload["triage"]["LOW"]:
        assert "score" not in entry
        assert "points" not in entry
        assert "weight" not in entry


# --- 19: HIGH wording never implies verification ------------------------------


def test_reason_strings_never_use_verification_wording():
    r = _result(title="London City Airport EMAS", url="https://airspacechange.caa.co.uk/documents/download/5487")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    forbidden_words = ("confirmed", "verified", "accepted", "true", "proven")
    for reason in triaged[0].reasons:
        lowered = reason.lower()
        for word in forbidden_words:
            assert word not in lowered


def test_priority_band_values_never_use_verification_wording():
    for band in PriorityBand:
        lowered = band.value.lower()
        for word in ("confirmed", "verified", "accepted", "true", "proven"):
            assert word not in lowered


# --- 20: no DB/domain-governance imports (module-level smoke check) ----------


def test_triage_module_imports_no_database_or_governance_code():
    """Real check on actual import statements (AST, not prose) - the
    module's own docstring legitimately discusses app.models/app.services
    in plain English, which a substring check would wrongly flag. The
    authoritative, structural version of this check is
    test_discovery_architectural_safety.py, which already covers
    triage.py automatically (it globs app/discovery/*.py)."""
    import ast

    import app.discovery.triage as triage_module

    tree = ast.parse(open(triage_module.__file__, encoding="utf-8").read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_substrings = ("sqlalchemy", "app.database", "app.models", "app.services")
    for name in imported:
        for forbidden in forbidden_substrings:
            assert forbidden not in name, f"triage.py imports forbidden module: {name}"


# --- concept vocabulary sanity ------------------------------------------------


def test_concept_terms_do_not_include_civil_aviation_regulator():
    """That concept lives at the domain-classification layer, not as
    literal page-content text (Mission #10B design decision, documented in
    triage.py's own module docstring)."""
    assert "civil aviation regulator" not in [t.lower() for t in CONCEPT_TERMS]


# ==============================================================================
# Mission #10B.1 Part F - tightened HIGH-qualification rule (strong vs weak
# concept terms). Numbered to match the mission's own 15-item list.
# ==============================================================================


def test_vocabulary_split_is_disjoint_and_covers_the_original_nine_terms():
    assert set(STRONG_CONCEPT_TERMS) & set(WEAK_CONCEPT_TERMS) == set()
    assert set(STRONG_CONCEPT_TERMS) | set(WEAK_CONCEPT_TERMS) == set(CONCEPT_TERMS)
    assert set(WEAK_CONCEPT_TERMS) == {"procurement", "construction", "runway extension"}


# 1. airport identity + construction alone -> not HIGH
def test_10b1_identity_plus_construction_alone_not_high():
    """Matches the real #10B false-positive shape exactly (Construction
    News' "£500m expansion cleared" headline)."""
    r = _result(title="London City Airport's £500m expansion construction cleared", url="https://www.constructionnews.co.uk/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band != PriorityBand.HIGH
    assert "construction in title" in triaged[0].reasons


# 2. airport identity + procurement alone -> not HIGH
def test_10b1_identity_plus_procurement_alone_not_high():
    r = _result(title="London City Airport procurement notice", url="https://www.tenderlake.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band != PriorityBand.HIGH
    assert "procurement in title" in triaged[0].reasons


# 3. curated regulator domain + construction alone -> not HIGH
def test_10b1_regulator_domain_plus_construction_alone_not_high():
    r = _result(title="Billy Bishop Toronto City Airport construction update", url="https://tc.canada.ca/en/binder/construction-update")
    triaged = triage_results([_deduped(r)], identity=None)
    assert triaged[0].band != PriorityBand.HIGH


# 4. curated regulator domain + procurement alone -> not HIGH
def test_10b1_regulator_domain_plus_procurement_alone_not_high():
    r = _result(title="Billy Bishop Toronto City Airport procurement notice", url="https://tc.canada.ca/en/binder/procurement-notice")
    triaged = triage_results([_deduped(r)], identity=None)
    assert triaged[0].band != PriorityBand.HIGH


# 5. airport identity + EMAS title -> HIGH
def test_10b1_identity_plus_emas_title_is_high():
    r = _result(title="London City Airport EMAS installed", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band == PriorityBand.HIGH


# 6. regulator domain + EMAS title -> HIGH
def test_10b1_regulator_domain_plus_emas_title_is_high():
    r = _result(title="EMAS installation announcement", url="https://tc.canada.ca/en/binder/emas-announcement")
    triaged = triage_results([_deduped(r)], identity=None)
    assert triaged[0].band == PriorityBand.HIGH


# 7. airport identity + RESA title -> HIGH
def test_10b1_identity_plus_resa_title_is_high():
    r = _result(title="London City Airport RESA project", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert triaged[0].band == PriorityBand.HIGH


# 8. regulator domain + RESA title -> HIGH
def test_10b1_regulator_domain_plus_resa_title_is_high():
    r = _result(title="RESA project announcement", url="https://tc.canada.ca/en/binder/resa-announcement")
    triaged = triage_results([_deduped(r)], identity=None)
    assert triaged[0].band == PriorityBand.HIGH


# 9. weak concept still contributes a reason
def test_10b1_weak_concept_still_produces_a_visible_reason():
    r = _result(title="London City Airport construction", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "construction in title" in triaged[0].reasons
    for word in ("weak", "low confidence", "unreliable"):
        assert word not in " ".join(triaged[0].reasons).lower()


# 10. weak concept may influence deterministic MEDIUM ordering
def test_10b1_weak_concept_affects_ordering_within_medium():
    with_construction = _result(title="London City Airport construction notice", url="https://a.example.com/x")
    without_anything = _result(title="London City Airport general news", url="https://b.example.com/y")
    triaged = triage_results([_deduped(without_anything), _deduped(with_construction)], identity=_LCY_IDENTITY)
    assert all(t.band == PriorityBand.MEDIUM for t in triaged)
    urls_in_order = [t.deduped.result.url for t in triaged]
    assert urls_in_order.index("https://a.example.com/x") < urls_in_order.index("https://b.example.com/y")


# 11. domain alone still never HIGH
def test_10b1_domain_alone_still_never_high():
    r = _result(title="Billy Bishop Toronto City Airport general notice", url="https://tc.canada.ca/en/binder/general-notice")
    triaged = triage_results([_deduped(r)], identity=None)
    assert triaged[0].band != PriorityBand.HIGH


# 12. bare strong concept alone still never HIGH
def test_10b1_bare_strong_concept_alone_still_never_high():
    r = _result(title="What is EMAS? A general explainer", url="https://unrelated-blog.example/emas-explainer")
    triaged = triage_results([_deduped(r)], identity=None)
    assert triaged[0].band != PriorityBand.HIGH
    assert triaged[0].band == PriorityBand.MEDIUM


# 13. existing numeric-leak protections remain green (re-asserted here for
#     Part F traceability; the authoritative tests are #17/#18 above)
def test_10b1_triaged_result_still_has_no_score_field():
    r = _result(title="London City Airport EMAS", url="https://example.com/x")
    triaged = triage_results([_deduped(r)], identity=_LCY_IDENTITY)
    assert "score" not in vars(triaged[0])
    assert "points" not in vars(triaged[0])


# 14. architectural-safety tests remain green (re-asserted here for Part F
#     traceability; the authoritative test lives in
#     test_discovery_architectural_safety.py, which globs app/discovery/*.py
#     and therefore already covers this refined triage.py automatically)
def test_10b1_triage_module_still_has_no_forbidden_imports():
    import ast

    import app.discovery.triage as triage_module

    tree = ast.parse(open(triage_module.__file__, encoding="utf-8").read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for name in imported:
        for forbidden in ("sqlalchemy", "app.database", "app.models", "app.services"):
            assert forbidden not in name


# 15. normal non-triage CLI remains unchanged (re-asserted here for Part F
#     traceability; the authoritative tests live in test_discovery_cli.py)
def test_10b1_non_triage_cli_output_unaffected_by_vocabulary_split(capsys):
    import scripts.discover_airport_sources as cli

    exit_code = cli.main(["--name", "London City Airport", "--iata", "LCY", "--icao", "EGLC"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Generated 12 deterministic queries" in out
    assert "NO SEARCH PROVIDER CONFIGURED" in out
