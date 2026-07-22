from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from urllib.parse import urljoin

import httpx
import pdfplumber

INDEX_URL = (
    "https://www.faa.gov/about/office_org/headquarters_offices/ato/service_units/"
    "systemops/perf_analysis/sys_cap_eval/"
)
# The URL isn't a stable, constructible pattern: different quarters live under
# different subdirectories (sys_cap_eval/, sys_cap_eval/media/,
# slot_administration/data/doc/), "508" is sometimes in the filename and
# sometimes not, and one quarter's file even has "Constuction" misspelled - so
# the current report has to be discovered from the index page, not guessed.
_REPORT_HREF = re.compile(r'href="([^"]*Q(\d)_(\d{4})[^"]*\.pdf)"', re.IGNORECASE)
_AIRPORT_HEADER = re.compile(r"^([A-Z]{2,4})\s*\(\d+ of \d+\)")
_EXACT_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_KEYWORDS = ("EMAS", "arresting system")


class ConstructionReportError(ValueError):
    """Raised when the FAA construction report index or PDF has an unexpected shape."""


@dataclass(frozen=True)
class ConstructionReportMatch:
    airport_code: str
    project_id: str | None
    project_name: str
    description: str
    estimated_dates_raw: str
    start_date: date | None
    end_date: date | None
    status: str
    impact: str
    notes: str
    matched_keyword: str


def discover_latest_report(*, client: httpx.Client, timeout: float = 30.0) -> tuple[str, int, int]:
    """Returns (url, quarter, year) for the most recent report on the index page."""

    response = client.get(INDEX_URL, timeout=timeout)
    response.raise_for_status()
    candidates = [
        (int(year), int(quarter), href) for href, quarter, year in _REPORT_HREF.findall(response.text)
    ]
    if not candidates:
        raise ConstructionReportError(f"No construction report PDF links found on {INDEX_URL}.")
    candidates.sort()
    year, quarter, href = candidates[-1]
    return urljoin(INDEX_URL, href), quarter, year


def _parse_dates(raw: str) -> tuple[date | None, date | None]:
    tokens = _EXACT_DATE.findall(raw or "")
    parsed = [date(int(y), int(m), int(d)) for m, d, y in tokens]
    start = parsed[0] if len(parsed) >= 1 else None
    end = parsed[1] if len(parsed) >= 2 else None
    return start, end


def _matched_keyword(haystack: str) -> str | None:
    if "EMAS" in haystack:
        return "EMAS"
    if "arresting system" in haystack.lower():
        return "arresting system"
    return None


def _find_detail_table(tables: list[list[list[str | None]]]) -> list[list[str | None]] | None:
    for table in tables:
        if table and table[0] and any(cell and "Description of Work" in cell for cell in table[0]):
            return table
    return None


def parse_report(pdf_bytes: bytes) -> list[ConstructionReportMatch]:
    """Search every per-airport section for EMAS/arresting-system project rows.

    Uses pdfplumber's extract_tables() rather than raw text: this report's
    multi-column layout (a monthly Gantt-style summary table plus a detail
    table) doesn't linearize into readable per-project lines via plain text
    extraction, but extract_tables() reconstructs the real grid cleanly.
    """

    matches: list[ConstructionReportMatch] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        current_airport: str | None = None
        for page in pdf.pages:
            text = page.extract_text() or ""
            header_match = _AIRPORT_HEADER.match(text.lstrip())
            if header_match:
                current_airport = header_match.group(1)
            if current_airport is None:
                continue

            detail_table = _find_detail_table(page.extract_tables())
            if detail_table is None:
                continue

            current_project_id: str | None = None
            current_project_name: str | None = None
            for row in detail_table[1:]:
                if len(row) < 7:
                    continue
                project_id, project_name, description, estimated_dates, status, impact, notes = row[:7]
                if project_id:
                    current_project_id = project_id
                if project_name:
                    current_project_name = project_name.replace("\n", " ").strip()

                description = (description or "").replace("\n", " ").strip()
                notes = (notes or "").replace("\n", " ").strip()
                keyword = _matched_keyword(f"{description} {notes}")
                if not keyword:
                    continue

                start_date, end_date = _parse_dates(estimated_dates)
                matches.append(
                    ConstructionReportMatch(
                        airport_code=current_airport,
                        project_id=current_project_id,
                        project_name=current_project_name or "",
                        description=description,
                        estimated_dates_raw=(estimated_dates or "").strip(),
                        start_date=start_date,
                        end_date=end_date,
                        status=(status or "").strip(),
                        impact=(impact or "").strip(),
                        notes=notes,
                        matched_keyword=keyword,
                    )
                )
    return matches


def fetch_current_report_matches(
    *, client: httpx.Client, timeout: float = 60.0
) -> tuple[str, int, int, list[ConstructionReportMatch]]:
    url, quarter, year = discover_latest_report(client=client, timeout=timeout)
    response = client.get(url, timeout=timeout)
    response.raise_for_status()
    return url, quarter, year, parse_report(response.content)
