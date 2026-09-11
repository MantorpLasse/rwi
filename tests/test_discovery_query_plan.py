"""RWI Mission #9D Part K - deterministic Search Plan tests."""

import pytest

from app.discovery.identity import AirportIdentity
from app.discovery.query import build_search_plan, plan_official_domain_document_queries


def test_name_only_identity_produces_only_name_field_queries():
    identity = AirportIdentity(name="Billy Bishop Toronto City Airport")
    plan = build_search_plan(identity)
    assert plan  # non-empty
    assert all(q.identity_field == "name" for q in plan)
    # every one of the 8 required V1 concepts is present
    template_ids = {q.template_id for q in plan}
    assert template_ids == {
        "emas",
        "runway_safety",
        "resa",
        "arresting_system",
        "runway_extension",
        "procurement",
        "construction",
        "regulator",
    }


def test_iata_and_icao_only_narrow_technical_concepts():
    identity = AirportIdentity(name="LCY", iata_code="LCY", icao_code="EGLC")
    plan = build_search_plan(identity)
    non_name_template_ids = {q.template_id for q in plan if q.identity_field != "name"}
    # Only emas/resa use code variants (Part E: avoid Cartesian-product noise)
    assert non_name_template_ids == {"emas", "resa"}


def test_full_identity_query_count_is_twelve_not_twentyfour():
    identity = AirportIdentity(
        name="London City Airport", iata_code="LCY", icao_code="EGLC", country="United Kingdom"
    )
    plan = build_search_plan(identity)
    # 8 concepts total; 2 of them (emas, resa) additionally use iata+icao =>
    # 6 name-only concepts x1 + 2 code-eligible concepts x3 = 6 + 6 = 12
    assert len(plan) == 12


def test_multi_word_name_is_quoted_short_code_is_not():
    identity = AirportIdentity(name="London City Airport", iata_code="LCY")
    plan = build_search_plan(identity)
    name_query = next(q for q in plan if q.template_id == "emas" and q.identity_field == "name")
    code_query = next(q for q in plan if q.template_id == "emas" and q.identity_field == "iata_code")
    assert name_query.rendered == '"London City Airport" EMAS'
    assert code_query.rendered == "LCY EMAS"


def test_plan_is_deterministic_across_calls():
    identity = AirportIdentity(
        name="London City Airport", iata_code="LCY", icao_code="EGLC", country="United Kingdom"
    )
    plan_a = build_search_plan(identity)
    plan_b = build_search_plan(identity)
    assert plan_a == plan_b
    assert [q.rendered for q in plan_a] == [q.rendered for q in plan_b]


def test_plan_ordering_follows_fixed_concept_then_field_order():
    identity = AirportIdentity(name="London City Airport", iata_code="LCY", icao_code="EGLC")
    plan = build_search_plan(identity)
    template_order = [q.template_id for q in plan]
    # emas block (name, iata, icao) comes before resa block, in that field order
    assert template_order[:3] == ["emas", "emas", "emas"]
    emas_fields = [q.identity_field for q in plan if q.template_id == "emas"]
    assert emas_fields == ["name", "iata_code", "icao_code"]


def test_no_accidental_duplicate_queries_when_codes_collide():
    # Degenerate case: iata_code happens to equal the (single-word) name.
    identity = AirportIdentity(name="LCY", iata_code="LCY")
    plan = build_search_plan(identity)
    rendered = [q.rendered for q in plan]
    assert len(rendered) == len(set(rendered))


def test_missing_optional_fields_are_simply_skipped_not_none_queries():
    identity = AirportIdentity(name="Test Airport")
    plan = build_search_plan(identity)
    assert all(q.identity_value for q in plan)
    assert all(q.identity_field == "name" for q in plan)


def test_query_provenance_is_fully_reconstructable():
    identity = AirportIdentity(name="London City Airport", iata_code="LCY")
    plan = build_search_plan(identity)
    query = next(q for q in plan if q.template_id == "resa" and q.identity_field == "iata_code")
    assert query.identity_value == "LCY"
    assert query.rendered == "LCY RESA"


# --- Official-domain document queries (RWI HQ "Official-Domain Document
# Discovery Pass" mission - SDF/flylouisville.com benchmark) --------------


def test_official_domain_document_queries_exact_sdf_benchmark():
    """The SDF/flylouisville.com benchmark case: given the exact
    already-governed official domain the design recon found (SDF's own
    LRAA board minutes source), the planner must emit exactly these 4
    queries, in this fixed order - never more, never fewer, never
    reordered."""
    plan = plan_official_domain_document_queries("flylouisville.com")
    assert [q.rendered for q in plan] == [
        "site:flylouisville.com EMAS",
        "site:flylouisville.com EMAS presentation",
        "site:flylouisville.com EMAS capital program",
        "site:flylouisville.com EMAS board",
    ]


def test_official_domain_document_queries_hard_cap_is_four():
    plan = plan_official_domain_document_queries("flylouisville.com")
    assert len(plan) == 4


def test_official_domain_document_queries_deterministic_across_calls():
    first = [q.rendered for q in plan_official_domain_document_queries("flylouisville.com")]
    second = [q.rendered for q in plan_official_domain_document_queries("flylouisville.com")]
    assert first == second


def test_official_domain_document_queries_normalizes_case_and_whitespace():
    plan = plan_official_domain_document_queries("  FlyLouisville.COM  ")
    assert plan[0].rendered == "site:flylouisville.com EMAS"


def test_official_domain_document_queries_provenance_reconstructable():
    plan = plan_official_domain_document_queries("flylouisville.com")
    for query in plan:
        assert query.identity_field == "official_domain"
        assert query.identity_value == "flylouisville.com"
        assert query.template_id.startswith("official_domain_document_")


def test_official_domain_document_queries_rejects_blank_domain():
    with pytest.raises(ValueError):
        plan_official_domain_document_queries("")
    with pytest.raises(ValueError):
        plan_official_domain_document_queries("   ")


def test_official_domain_document_queries_never_multiplies_per_dimension_or_anchor():
    """No dimension/anchor concept appears anywhere in this planner's
    output - it is a fixed, dimension-independent set (module docstring's
    own "no dimension multiplication" instruction)."""
    plan = plan_official_domain_document_queries("flylouisville.com")
    for query in plan:
        assert "runway" not in query.rendered.lower()
        assert "phase" not in query.rendered.lower()
        assert "supplier" not in query.rendered.lower()
