"""IIJA (Infrastructure Investment and Jobs Act) airport grant announcements.

Separate, earmarked funding pot from the regular AIP entitlement/discretionary
grants - same "Announcement" PDF table format FAA publishes for AIP though,
so this reuses app.acquisition.faa_aip_grants.parse_grant_pdf as-is rather
than reimplementing a table parser.

Unlike AIP grants (discovered by scraping an HTML listing page whose PDF
links vary year to year), IIJA announcement PDFs live at a predictable,
constructed URL: six per fiscal year, numbered Announcement 1-6.
"""
from __future__ import annotations

from app.acquisition.faa_aip_grants import AipGrant, AipGrantsError, parse_grant_pdf

IIJA_GRANT_PDF_URL_TEMPLATE = (
    "https://www.faa.gov/iija/iija-airport-infrastructure-grant-funding-amounts/"
    "AIG-FY{year}-A{announcement}.pdf"
)
ANNOUNCEMENTS_PER_YEAR = 6

__all__ = [
    "AipGrant",
    "AipGrantsError",
    "parse_grant_pdf",
    "ANNOUNCEMENTS_PER_YEAR",
    "iija_grant_pdf_url",
    "discover_iija_grant_pdf_urls",
]


def iija_grant_pdf_url(year: int, announcement: int) -> str:
    return IIJA_GRANT_PDF_URL_TEMPLATE.format(year=year, announcement=announcement)


def discover_iija_grant_pdf_urls(year: int) -> list[str]:
    """The six annual IIJA announcement PDF URLs for a fiscal year.

    No network call needed - unlike discover_grant_pdf_urls() for AIP, the
    URL pattern is fixed and the announcement count per year is fixed at 6.
    """

    return [iija_grant_pdf_url(year, announcement) for announcement in range(1, ANNOUNCEMENTS_PER_YEAR + 1)]
