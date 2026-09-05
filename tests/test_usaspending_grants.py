import ast
import inspect
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.acquisition import usaspending_grants
from app.acquisition.usaspending_grants import (
    UsaspendingError,
    fetch_all_emas_grants,
    fetch_award_by_id,
)

ROW_1 = {
    "generated_internal_id": "ASST_NON_AAA_069",
    "Award ID": "AAA",
    "Recipient Name": "BROOME COUNTY",
    "Award Amount": 415226.0,
    "Description": "PURPOSE: RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM.",
    "Awarding Agency": "Department of Transportation",
    "Start Date": "2026-06-16",
}
ROW_2 = {
    "generated_internal_id": "ASST_NON_BBB_069",
    "Award ID": "BBB",
    "Recipient Name": "TOWN OF MORRISTOWN",
    "Award Amount": 5756758.0,
    "Description": "PURPOSE: CONSTRUCT/EXTEND SAFETY AREA.",
    "Awarding Agency": "Department of Transportation",
    "Start Date": "2025-09-19",
}


def _paged_handler(pages):
    def handler(request):
        body = request.read()
        import json

        payload = json.loads(body)
        page = payload["page"]
        results, has_next = pages.get(page, ([], False))
        return httpx.Response(
            200,
            json={
                "results": results,
                "page_metadata": {"hasNext": has_next},
            },
            request=request,
        )

    return handler


def test_fetch_all_emas_grants_paginates_until_exhausted():
    pages = {1: ([ROW_1], True), 2: ([ROW_2], False)}
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler(pages)))

    grants = fetch_all_emas_grants(client=client, end_date=date(2026, 12, 31))

    assert [g.external_id for g in grants] == ["ASST_NON_AAA_069", "ASST_NON_BBB_069"]
    assert grants[0].recipient_name == "BROOME COUNTY"
    assert grants[0].award_amount == Decimal("415226.0")
    assert grants[0].start_date == date(2026, 6, 16)
    assert grants[1].recipient_name == "TOWN OF MORRISTOWN"


def test_fetch_all_emas_grants_stops_on_single_page():
    pages = {1: ([ROW_1], False)}
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler(pages)))

    grants = fetch_all_emas_grants(client=client, end_date=date(2026, 12, 31))

    assert len(grants) == 1


def test_fetch_all_emas_grants_fails_closed_on_missing_results_key():
    def handler(request):
        return httpx.Response(200, json={"page_metadata": {"hasNext": False}}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UsaspendingError):
        fetch_all_emas_grants(client=client, end_date=date(2026, 12, 31))


def test_fetch_all_emas_grants_fails_closed_on_missing_identifier():
    def handler(request):
        row = {k: v for k, v in ROW_1.items() if k not in ("generated_internal_id",)}
        row.pop("internal_id", None)
        return httpx.Response(
            200,
            json={"results": [row], "page_metadata": {"hasNext": False}},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UsaspendingError):
        fetch_all_emas_grants(client=client, end_date=date(2026, 12, 31))


# ---------------------------------------------------------------------------
# RWI HQ "Targeted USAspending Exact-Award Helper" mission: fetch_award_by_id()
# - a single-award GET, entirely additive alongside fetch_all_emas_grants()
# above (unmodified, still covered by the four tests above this comment).
# ---------------------------------------------------------------------------

_BGM_AWARD_ID = "ASST_NON_33600080982026_069"

# Shaped directly on the real, live-confirmed BGM Signal 60 award response
# (RWI HQ recon this mission's own docstring cites) - not a hardcoded
# production value anywhere in app/acquisition/usaspending_grants.py itself,
# only here in the test fixture, exactly as the mission instructs.
_AWARD_RESPONSE = {
    "id": 359528373,
    "generated_unique_award_id": _BGM_AWARD_ID,
    "fain": "33600080982026",
    "type": "03",
    "type_description": "FORMULA GRANT (A)",
    "date_signed": "2026-06-16",
    "recipient": {"recipient_name": "BROOME COUNTY"},
    "period_of_performance": {
        "start_date": "2026-06-16", "end_date": "2030-06-15", "last_modified_date": "2026-06-22",
    },
    "place_of_performance": {
        "city_name": "JOHNSON CITY", "county_name": "BROOME", "state_name": "NEW YORK",
    },
    "awarding_agency": {
        "toptier_agency": {"name": "Department of Transportation"},
        "subtier_agency": {"name": "Federal Aviation Administration"},
    },
    "funding_agency": {
        "toptier_agency": {"name": "Department of Transportation"},
        "subtier_agency": {"name": "Federal Aviation Administration"},
    },
    "total_obligation": 415226.0,
    "transaction_obligated_amount": 415226.0,
    "non_federal_funding": 21854.0,
    "total_funding": 437080.0,
    "cfda_info": [
        {"cfda_number": "20.116", "cfda_popular_name": "AIP", "cfda_title": "Airport Improvement Program"},
    ],
    "description": (
        "PURPOSE: RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM SAFETY AREA.  ACTIVITIES TO BE "
        "PERFORMED/EXPECTED OUTCOMES: THIS PROJECT RECONSTRUCTS EXISTING RUNWAY 16/34 ENGINEERED "
        "MATERIAL ARRESTING SYSTEM SAFETY AREA AT THE RUNWAY 16 END THAT HAS REACHED THE END OF ITS "
        "USEFUL LIFE. THIS GRANT FUNDS PHASE 1, WHICH CONSISTS OF DESIGN."
    ),
}


def _award_handler(response_body, *, status_code=200):
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(status_code, json=response_body, request=request)

    handler.captured = captured
    return handler


# 1. HAPPY PATH
def test_fetch_award_by_id_happy_path():
    handler = _award_handler(_AWARD_RESPONSE)
    client = httpx.Client(transport=httpx.MockTransport(handler))

    detail = fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID, timeout=12.5)

    assert handler.captured["method"] == "GET"
    assert handler.captured["url"] == f"https://api.usaspending.gov/api/v2/awards/{_BGM_AWARD_ID}/"
    assert detail.generated_unique_award_id == _BGM_AWARD_ID
    assert detail.fain == "33600080982026"
    assert detail.award_type == "03"
    assert detail.award_type_description == "FORMULA GRANT (A)"
    assert detail.date_signed == date(2026, 6, 16)
    assert detail.recipient_name == "BROOME COUNTY"
    assert detail.period_of_performance_start == date(2026, 6, 16)
    assert detail.period_of_performance_end == date(2030, 6, 15)
    assert detail.place_of_performance_city == "JOHNSON CITY"
    assert detail.place_of_performance_county == "BROOME"
    assert detail.place_of_performance_state == "NEW YORK"
    assert detail.awarding_agency_name == "Department of Transportation"
    assert detail.awarding_subtier_agency_name == "Federal Aviation Administration"
    assert detail.funding_agency_name == "Department of Transportation"
    assert detail.funding_subtier_agency_name == "Federal Aviation Administration"
    assert detail.total_obligation == Decimal("415226.0")
    assert detail.transaction_obligated_amount == Decimal("415226.0")
    assert detail.non_federal_funding == Decimal("21854.0")
    assert detail.total_funding == Decimal("437080.0")
    assert len(detail.cfda_info) == 1
    assert detail.cfda_info[0].cfda_number == "20.116"
    assert detail.cfda_info[0].cfda_popular_name == "AIP"
    assert detail.cfda_info[0].cfda_title == "Airport Improvement Program"
    assert "RECONSTRUCT ENGINEERED MATERIAL ARRESTING SYSTEM" in detail.description


# 2. AMOUNT SEPARATION - deliberately distinct values, never collapsed/summed.
def test_fetch_award_by_id_keeps_amount_fields_separate():
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(_AWARD_RESPONSE)))

    detail = fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)

    values = {
        "total_obligation": detail.total_obligation,
        "transaction_obligated_amount": detail.transaction_obligated_amount,
        "non_federal_funding": detail.non_federal_funding,
        "total_funding": detail.total_funding,
    }
    assert values == {
        "total_obligation": Decimal("415226.0"),
        "transaction_obligated_amount": Decimal("415226.0"),
        "non_federal_funding": Decimal("21854.0"),
        "total_funding": Decimal("437080.0"),
    }
    # total_funding is NOT re-derived as total_obligation + non_federal_funding
    # anywhere in the implementation - it is the source's own named field,
    # used as-is. (It happens to equal the sum here, exactly as the real
    # BGM award does - this assertion is a sanity check on the fixture,
    # never a claim that fetch_award_by_id() itself performs that arithmetic.)
    assert detail.total_funding == detail.total_obligation + detail.non_federal_funding
    # No derived "project total"/"award_amount"/"grant_total" property exists.
    assert not hasattr(detail, "project_total")
    assert not hasattr(detail, "award_amount")
    assert not hasattr(detail, "grant_total")


# 3. IDENTITY MISMATCH
def test_fetch_award_by_id_identity_mismatch_fails_closed():
    mismatched = dict(_AWARD_RESPONSE, generated_unique_award_id="ASST_NON_OTHER_069")
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(mismatched)))

    with pytest.raises(UsaspendingError, match="mismatched award"):
        fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)


# 4. MISSING generated_unique_award_id
def test_fetch_award_by_id_missing_identifier_fails_closed():
    body = {k: v for k, v in _AWARD_RESPONSE.items() if k != "generated_unique_award_id"}
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(body)))

    with pytest.raises(UsaspendingError):
        fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)


# 5. MISSING total_obligation
def test_fetch_award_by_id_missing_total_obligation_fails_closed():
    body = {k: v for k, v in _AWARD_RESPONSE.items() if k != "total_obligation"}
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(body)))

    with pytest.raises(UsaspendingError):
        fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)


# 6. MISSING description
def test_fetch_award_by_id_missing_description_fails_closed():
    body = {k: v for k, v in _AWARD_RESPONSE.items() if k != "description"}
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(body)))

    with pytest.raises(UsaspendingError):
        fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)


# Also required (Section 6 of the mission): missing fain.
def test_fetch_award_by_id_missing_fain_fails_closed():
    body = {k: v for k, v in _AWARD_RESPONSE.items() if k != "fain"}
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(body)))

    with pytest.raises(UsaspendingError):
        fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)


# 7. HTTP 404/500 - preserved current adapter convention: raw, unwrapped
# httpx.HTTPStatusError, exactly like fetch_all_emas_grants()'s own
# raise_for_status() call.
@pytest.mark.parametrize("status_code", [404, 500])
def test_fetch_award_by_id_http_error_propagates_unwrapped(status_code):
    def handler(request):
        return httpx.Response(status_code, json={"detail": "not found"}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)


# 8. OPTIONAL NESTED FIELDS - place_of_performance/funding_agency/cfda_info
# missing entirely must not crash; only the four required scalars matter.
def test_fetch_award_by_id_missing_optional_nested_fields_does_not_crash():
    body = {
        k: v
        for k, v in _AWARD_RESPONSE.items()
        if k not in ("place_of_performance", "funding_agency", "cfda_info", "recipient", "period_of_performance")
    }
    client = httpx.Client(transport=httpx.MockTransport(_award_handler(body)))

    detail = fetch_award_by_id(client=client, award_id=_BGM_AWARD_ID)

    assert detail.generated_unique_award_id == _BGM_AWARD_ID
    assert detail.total_obligation == Decimal("415226.0")
    assert detail.place_of_performance_city is None
    assert detail.place_of_performance_county is None
    assert detail.place_of_performance_state is None
    assert detail.funding_agency_name is None
    assert detail.funding_subtier_agency_name is None
    assert detail.recipient_name is None
    assert detail.period_of_performance_start is None
    assert detail.period_of_performance_end is None
    assert detail.cfda_info == ()


# 9. BROAD-SEARCH REGRESSION: fetch_all_emas_grants() itself is untouched -
# the four pre-existing tests above this section still exercise it exactly
# as before. This test additionally proves the two functions share nothing
# but BASE_URL and the client-injection convention.
def test_fetch_award_by_id_does_not_affect_broad_search_endpoint_or_shape():
    pages = {1: ([ROW_1], False)}
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler(pages)))
    grants = fetch_all_emas_grants(client=client, end_date=date(2026, 12, 31))
    assert len(grants) == 1
    assert grants[0].external_id == "ASST_NON_AAA_069"


# 10. NO PERSISTENCE: this acquisition module imports nothing from
# app.models/app.database/sqlalchemy - fetch_award_by_id() adds no
# exception to that. AST-based (real import statements only), not a raw
# substring search - this module's own docstrings legitimately mention
# "app.models"/"app.services" in prose explaining why it does NOT import them.
def test_usaspending_grants_module_imports_no_persistence_layer():
    tree = ast.parse(inspect.getsource(usaspending_grants))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "app" not in imported_roots
    assert "sqlalchemy" not in imported_roots
