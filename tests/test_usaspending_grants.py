from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.acquisition.usaspending_grants import (
    UsaspendingError,
    fetch_all_emas_grants,
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
