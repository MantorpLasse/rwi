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


@dataclass(frozen=True)
class UsaspendingCfdaInfo:
    """One entry of the award-detail response's own `cfda_info` list -
    preserved as a tuple on UsaspendingAwardDetail (RWI HQ "Targeted
    USAspending Exact-Award Helper" mission), never collapsed to a single
    row: nothing in the confirmed live responses this mission inspected
    proves the API guarantees exactly one CFDA program per award, so
    picking "the first" would be an unproven, silent assumption."""

    cfda_number: str | None
    cfda_popular_name: str | None
    cfda_title: str | None


@dataclass(frozen=True)
class UsaspendingAwardDetail:
    """One exact USAspending award, fetched by its own id (RWI HQ "Targeted
    USAspending Exact-Award Helper" mission) - the single-award counterpart
    to UsaspendingGrant's broad-search row shape, never a replacement for it.

    AMOUNT SAFETY (the reason this is a separate dataclass rather than a
    reuse of UsaspendingGrant's single `award_amount` field): the BGM
    Funding Amount Semantics Audit proved that RWI's own legacy
    Signal.estimated_total_value_usd field maps EXACTLY to this award's
    `total_obligation` for every one of five real BGM Signals - never to
    `total_funding` (which additionally includes `non_federal_funding`,
    the local/state match). Collapsing these into one generic amount would
    silently reproduce the exact ambiguity that audit exists to flag.
    `total_obligation`, `transaction_obligated_amount`,
    `non_federal_funding`, and `total_funding` are therefore kept as four
    separate, distinctly-named fields, exactly as USAspending itself
    reports them - never summed, never renamed to `award_amount`/
    `project_value`/`total_value`/`grant_total`, and never presented as an
    EMAS contract value, a supplier's revenue, or a project total. No
    property on this dataclass derives one amount from the others."""

    generated_unique_award_id: str
    fain: str
    award_type: str | None
    award_type_description: str | None
    date_signed: date | None
    recipient_name: str | None
    period_of_performance_start: date | None
    period_of_performance_end: date | None
    place_of_performance_city: str | None
    place_of_performance_county: str | None
    place_of_performance_state: str | None
    awarding_agency_name: str | None
    awarding_subtier_agency_name: str | None
    funding_agency_name: str | None
    funding_subtier_agency_name: str | None
    total_obligation: Decimal
    transaction_obligated_amount: Decimal | None
    non_federal_funding: Decimal | None
    total_funding: Decimal | None
    cfda_info: tuple[UsaspendingCfdaInfo, ...]
    description: str


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


def fetch_award_by_id(*, client: httpx.Client, award_id: str, timeout: float = 30.0) -> UsaspendingAwardDetail:
    """Fetch exactly ONE USAspending award by its own bare id - RWI HQ
    "Targeted USAspending Exact-Award Helper" mission. A single GET, no
    pagination, no keyword search, no persistence, no side effects -
    entirely additive alongside fetch_all_emas_grants() above, which this
    function does not call, modify, or share any behavior with beyond the
    same BASE_URL and the same caller-injected httpx.Client convention.

    `award_id` is the bare USAspending `generated_unique_award_id` (e.g.
    "ASST_NON_33600080982026_069") - never a Source.external_id. Namespace
    resolution (stripping a "usaspending:" scheme prefix, if a caller's
    value carries one) is deliberately left to the caller; this module has
    no persistence-layer knowledge and imports nothing from app.models or
    app.services, so it cannot and does not decide what counts as a
    funding-provenance namespace.

    FAILS CLOSED: raises UsaspendingError if the response lacks
    `generated_unique_award_id`, `total_obligation`, `fain`, or
    `description`, or if the response's own `generated_unique_award_id`
    does not exactly equal the requested `award_id` - never silently
    accepts a mismatched award. A non-2xx HTTP status raises
    httpx.HTTPStatusError unwrapped, exactly matching
    fetch_all_emas_grants()'s own existing, unmodified convention.

    See UsaspendingAwardDetail's own docstring for why total_obligation/
    transaction_obligated_amount/non_federal_funding/total_funding are
    four separate fields, never collapsed or summed here or anywhere else
    in this function.
    """
    response = client.get(f"{BASE_URL}/api/v2/awards/{award_id}/", timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    returned_id = payload.get("generated_unique_award_id")
    if not returned_id:
        raise UsaspendingError(f"Award response has no generated_unique_award_id: {payload!r}")
    if returned_id != award_id:
        raise UsaspendingError(
            f"Requested award_id={award_id!r} but the response identifies itself as "
            f"{returned_id!r} - refusing a mismatched award."
        )

    total_obligation = _parse_amount(payload.get("total_obligation"))
    if total_obligation is None:
        raise UsaspendingError(f"Award {award_id!r} response has no total_obligation: {payload!r}")

    fain = payload.get("fain")
    if not fain:
        raise UsaspendingError(f"Award {award_id!r} response has no fain: {payload!r}")

    description = payload.get("description")
    if not description:
        raise UsaspendingError(f"Award {award_id!r} response has no description: {payload!r}")

    # Nested structures parsed defensively throughout - a real award may
    # legitimately omit place_of_performance, a subtier agency, or CFDA
    # info entirely; only the four fields checked above are required.
    recipient = payload.get("recipient") or {}
    period = payload.get("period_of_performance") or {}
    place = payload.get("place_of_performance") or {}
    awarding_agency = payload.get("awarding_agency") or {}
    funding_agency = payload.get("funding_agency") or {}
    awarding_toptier = awarding_agency.get("toptier_agency") or {}
    awarding_subtier = awarding_agency.get("subtier_agency") or {}
    funding_toptier = funding_agency.get("toptier_agency") or {}
    funding_subtier = funding_agency.get("subtier_agency") or {}
    cfda_rows = payload.get("cfda_info") or []

    return UsaspendingAwardDetail(
        generated_unique_award_id=returned_id,
        fain=str(fain),
        award_type=payload.get("type"),
        award_type_description=payload.get("type_description"),
        date_signed=_parse_date(payload.get("date_signed")),
        recipient_name=(recipient.get("recipient_name") or "").strip() or None,
        period_of_performance_start=_parse_date(period.get("start_date")),
        period_of_performance_end=_parse_date(period.get("end_date")),
        place_of_performance_city=place.get("city_name"),
        place_of_performance_county=place.get("county_name"),
        place_of_performance_state=place.get("state_name"),
        awarding_agency_name=awarding_toptier.get("name"),
        awarding_subtier_agency_name=awarding_subtier.get("name"),
        funding_agency_name=funding_toptier.get("name"),
        funding_subtier_agency_name=funding_subtier.get("name"),
        total_obligation=total_obligation,
        transaction_obligated_amount=_parse_amount(payload.get("transaction_obligated_amount")),
        non_federal_funding=_parse_amount(payload.get("non_federal_funding")),
        total_funding=_parse_amount(payload.get("total_funding")),
        cfda_info=tuple(
            UsaspendingCfdaInfo(
                cfda_number=row.get("cfda_number"),
                cfda_popular_name=row.get("cfda_popular_name"),
                cfda_title=row.get("cfda_title"),
            )
            for row in cfda_rows
        ),
        description=" ".join(description.split()),
    )
