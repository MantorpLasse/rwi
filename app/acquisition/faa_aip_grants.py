from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import urljoin

import httpx
import pdfplumber


AIP_GRANTS_URL_TEMPLATE = "https://www.faa.gov/airports/aip/{year}_aip_grants"
# RWI HQ "FAA AIP Parser + Stage-Only Conversion": the table header is fixed
# in every column EXCEPT the discretionary-amount column, whose exact label
# text FAA has changed at least once ("Discretionary Amt" historically,
# "Non-comp Discretionary Amt" as of current FY2026 material - the same
# fiscal quantity, differently labeled). Modeled as an explicit, narrow
# alias set for that ONE column - never a fuzzy/partial header match - so a
# genuinely different table shape (wrong column count, wrong column order,
# an unrecognized discretionary label, a missing/renamed column anywhere
# else) still fails closed exactly as before.
_EXPECTED_HEADER_PREFIX = (
    "ST",
    "City",
    "Airport",
    "Loc ID",
    "Project Description",
    "Entitlement Amt",
)
_SUPPORTED_DISCRETIONARY_HEADERS = (
    "Discretionary Amt",  # historical
    "Non-comp Discretionary Amt",  # current FY2026 material
)
_EXPECTED_HEADER_SUFFIX = ("Total AIP",)
_EXPECTED_HEADER_LENGTH = len(_EXPECTED_HEADER_PREFIX) + 1 + len(_EXPECTED_HEADER_SUFFIX)
_PDF_HREF = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)


def _is_supported_header(normalized_header: tuple[str, ...]) -> bool:
    """Explicit structural check, never fuzzy: every column except the
    discretionary-amount one (index len(_EXPECTED_HEADER_PREFIX)) must match
    exactly; that one column must be exactly one of
    _SUPPORTED_DISCRETIONARY_HEADERS, verbatim."""
    if len(normalized_header) != _EXPECTED_HEADER_LENGTH:
        return False
    prefix_len = len(_EXPECTED_HEADER_PREFIX)
    if normalized_header[:prefix_len] != _EXPECTED_HEADER_PREFIX:
        return False
    if normalized_header[prefix_len] not in _SUPPORTED_DISCRETIONARY_HEADERS:
        return False
    if normalized_header[prefix_len + 1:] != _EXPECTED_HEADER_SUFFIX:
        return False
    return True


class AipGrantsError(ValueError):
    """Raised when the AIP grants listing page or a grant PDF has an unexpected shape."""


@dataclass(frozen=True)
class AipGrant:
    state: str
    city: str
    airport_name: str
    loc_id: str
    project_description: str
    entitlement_amt: Decimal | None
    discretionary_amt: Decimal | None
    total_aip_amt: Decimal | None
    source_pdf_url: str


def discover_grant_pdf_urls(
    year: int, *, client: httpx.Client, timeout: float = 30.0
) -> list[str]:
    """Find every per-quarter grant announcement PDF linked from the year's page."""

    url = AIP_GRANTS_URL_TEMPLATE.format(year=year)
    response = client.get(url, timeout=timeout)
    response.raise_for_status()
    hrefs = _PDF_HREF.findall(response.text)
    if not hrefs:
        raise AipGrantsError(f"No PDF links found on {url}.")

    resolved: list[str] = []
    for href in hrefs:
        absolute = urljoin(url, href)
        if absolute not in resolved:
            resolved.append(absolute)
    return resolved


def _parse_amount(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_grant_pdf(pdf_bytes: bytes, *, source_pdf_url: str) -> list[AipGrant]:
    """Extract every grant row from an AIP grants announcement PDF.

    The header repeats on every page; rows with a blank Loc ID are totals/
    subtotal rows, not real grants, and are skipped.
    """

    grants: list[AipGrant] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages):
            table = page.extract_table()
            if not table:
                continue
            header, *rows = table
            normalized_header = tuple((cell or "").strip() for cell in header)
            if not _is_supported_header(normalized_header):
                raise AipGrantsError(
                    f"Unexpected AIP grants table header on page {page_number}: "
                    f"{normalized_header!r}"
                )
            for row in rows:
                if len(row) != _EXPECTED_HEADER_LENGTH:
                    raise AipGrantsError(f"Unexpected AIP grants row shape: {row!r}")
                state, city, airport_name, loc_id, description, entitlement, discretionary, total = row
                loc_id = (loc_id or "").strip()
                if not loc_id:
                    continue  # totals/subtotal row, not a real grant
                grants.append(
                    AipGrant(
                        state=(state or "").strip(),
                        city=(city or "").strip(),
                        airport_name=(airport_name or "").strip(),
                        loc_id=loc_id,
                        project_description=" ".join((description or "").split()),
                        entitlement_amt=_parse_amount(entitlement),
                        discretionary_amt=_parse_amount(discretionary),
                        total_aip_amt=_parse_amount(total),
                        source_pdf_url=source_pdf_url,
                    )
                )
    return grants
