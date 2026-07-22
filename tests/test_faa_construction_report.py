from datetime import date
from pathlib import Path

import httpx
import pytest

from app.acquisition.faa_construction_report import (
    ConstructionReportError,
    discover_latest_report,
    parse_report,
)

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "faa_construction_report_sample.pdf").read_bytes()

INDEX_HTML = """
<html><body>
<a href="/.../slot_administration/data/doc/Q3_2026_508_Airport_Construction_Impact_Report.pdf">Q3 2026</a>
<a href="/.../sys_cap_eval/Q2_2026_508_Airport_Construction_Impact_Report.pdf">Q2 2026</a>
<a href="/.../sys_cap_eval/Q1_2026_Airport_Construction_Impact_Report.pdf">Q1 2026</a>
<a href="/.../sys_cap_eval/media/Q4_2023_Airport_Constuction_Impact_Report.pdf">Q4 2023 (typo'd filename)</a>
</body></html>
"""


def test_discover_latest_report_picks_the_highest_year_and_quarter():
    def handler(request):
        return httpx.Response(200, content=INDEX_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    url, quarter, year = discover_latest_report(client=client)

    assert year == 2026
    assert quarter == 3
    assert url.endswith("Q3_2026_508_Airport_Construction_Impact_Report.pdf")


def test_discover_latest_report_handles_the_typo_and_varied_subdirectories():
    """Q4_2023's filename says 'Constuction' and lives under sys_cap_eval/media/,
    not sys_cap_eval/ - it must still be found (just not picked, since it's old)."""

    def handler(request):
        return httpx.Response(200, content=INDEX_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # Should not raise, and should not accidentally pick the older/typo'd file.
    _url, quarter, year = discover_latest_report(client=client)
    assert (year, quarter) != (2023, 4)


def test_discover_latest_report_fails_closed_on_no_links():
    def handler(request):
        return httpx.Response(200, content=b"<html><body>nothing here</body></html>", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ConstructionReportError):
        discover_latest_report(client=client)


def test_parse_report_extracts_bos_and_sfo_emas_matches():
    matches = parse_report(FIXTURE_PDF)

    assert len(matches) == 2
    by_airport = {m.airport_code: m for m in matches}

    bos = by_airport["BOS"]
    assert bos.project_id == "D"
    assert bos.project_name == "RWY 27 RSA (Phase 2)"
    assert bos.start_date == date(2026, 8, 31)
    assert bos.end_date == date(2026, 11, 15)
    assert bos.status == "Upcoming"
    assert bos.matched_keyword == "EMAS"
    assert "RWY 9 EMAS installation" in bos.notes

    sfo = by_airport["SFO"]
    assert sfo.project_id == "A"
    assert sfo.start_date == date(2026, 3, 30)
    assert sfo.end_date == date(2026, 10, 3)
    assert sfo.status == "In Progress"
    assert "EMAS seam replacement" in sfo.description


def test_parse_report_skips_non_matching_projects_on_the_same_page():
    # The BOS fixture page also has "TWY B Rehabilitation" and "North Service
    # Area Construction" (from the summary table shared with the detail
    # table's page) - neither mentions EMAS/arresting system and must not
    # show up as a match.
    matches = parse_report(FIXTURE_PDF)
    project_names = {m.project_name for m in matches}
    assert "TWY B Rehabilitation Project" not in project_names
    assert "North Service Area Construction" not in project_names
