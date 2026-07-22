from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx

BASE_URL = "https://api.usaspending.gov"
KEYWORD = "Engineered Material Arresting System"
GRANT_AWARD_TYPE_CODES = ["02", "03", "04", "05"]
EARLIEST_SUPPORTED_DATE = "2007-10-01"
FIELDS = [
    "Award ID",
    "Recipient Name",
    "Award Amount",
    "Description",
    "Awarding Agency",
    "Start Date",
]
PAGE_LIMIT = 100


class UsaspendingError(ValueError):
    """Raised when the USAspending API returns an unexpected response shape."""


@dataclass(frozen=True)
class UsaspendingGrant:
    external_id: str
    award_id: str
    recipient_name: str
    award_amount: Decimal | None
    description: str
    awarding_agency: str | None
    start_date: date | None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def _parse_amount(raw: object) -> Decimal | None:
    if raw is None:
        return None
    return Decimal(str(raw))


def fetch_all_emas_grants(
    *, client: httpx.Client, end_date: date, timeout: float = 30.0
) -> list[UsaspendingGrant]:
    """Fetch every historical grant mentioning EMAS, paginating through all pages.

    Only GRANT_AWARD_TYPE_CODES (federal assistance to an airport sponsor) -
    CONTRACT-type awards never mention EMAS since the FAA doesn't procure it
    directly, and the sponsor's own purchase from the vendor isn't a federal
    award at all (see PLAN_FORENKLING.md's "USAspending.gov" section).
    """

    grants: list[UsaspendingGrant] = []
    page = 1
    while True:
        response = client.post(
            f"{BASE_URL}/api/v2/search/spending_by_award/",
            json={
                "filters": {
                    "keywords": [KEYWORD],
                    "time_period": [
                        {"start_date": EARLIEST_SUPPORTED_DATE, "end_date": end_date.isoformat()}
                    ],
                    "award_type_codes": GRANT_AWARD_TYPE_CODES,
                },
                "fields": FIELDS,
                "page": page,
                "limit": PAGE_LIMIT,
                "sort": "Award Amount",
                "order": "desc",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results")
        if results is None:
            raise UsaspendingError(f"Unexpected response shape on page {page}: {payload!r}")

        for row in results:
            external_id = row.get("generated_internal_id") or row.get("internal_id")
            if not external_id:
                raise UsaspendingError(f"Award row has no stable identifier: {row!r}")
            grants.append(
                UsaspendingGrant(
                    external_id=str(external_id),
                    award_id=row.get("Award ID") or "",
                    recipient_name=(row.get("Recipient Name") or "").strip(),
                    award_amount=_parse_amount(row.get("Award Amount")),
                    description=" ".join((row.get("Description") or "").split()),
                    awarding_agency=row.get("Awarding Agency"),
                    start_date=_parse_date(row.get("Start Date")),
                )
            )

        if not payload.get("page_metadata", {}).get("hasNext"):
            break
        page += 1

    return grants
