"""Exploratory script for PLAN_FORENKLING.md - USAspending.gov as a new source.

Read-only: makes live requests to api.usaspending.gov and prints what comes
back. Writes NOTHING to the database. Run it, read the output, and decide
whether a real integration is worth building - see the "USAspending.gov
(utforskat)" section in PLAN_FORENKLING.md for what a first exploration found.
"""

from __future__ import annotations

import json

import httpx

BASE_URL = "https://api.usaspending.gov"
TIME_PERIOD = [{"start_date": "2007-10-01", "end_date": "2026-12-31"}]

CONTRACT_TYPES = ["A", "B", "C", "D"]
GRANT_TYPES = ["02", "03", "04", "05"]

FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Description", "Awarding Agency", "Start Date"]


def search_awards(client: httpx.Client, keyword: str, award_type_codes: list[str], limit: int = 10) -> dict:
    response = client.post(
        f"{BASE_URL}/api/v2/search/spending_by_award/",
        json={
            "filters": {
                "keywords": [keyword],
                "time_period": TIME_PERIOD,
                "award_type_codes": award_type_codes,
            },
            "fields": FIELDS,
            "page": 1,
            "limit": limit,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def award_count_by_type(client: httpx.Client, keyword: str) -> dict:
    response = client.post(
        f"{BASE_URL}/api/v2/search/spending_by_award_count/",
        json={"filters": {"keywords": [keyword], "time_period": TIME_PERIOD}},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def recipient_search(client: httpx.Client, name: str) -> dict:
    response = client.post(
        f"{BASE_URL}/api/v2/autocomplete/recipient/",
        json={"search_text": name, "limit": 10},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def _print_awards(payload: dict, *, label: str) -> None:
    results = payload.get("results", [])
    print(f"--- {label}: {len(results)} shown (hasNext={payload.get('page_metadata', {}).get('hasNext')}) ---")
    for row in results:
        print(
            f"  {row.get('Recipient Name', '?'):<40} "
            f"${row.get('Award Amount', 0):>12,.0f}  "
            f"{row.get('Start Date', '?')}  "
            f"{(row.get('Description') or '')[:90]}"
        )
    print()


def main() -> None:
    with httpx.Client(headers={"User-Agent": "RunwaySafeIntelligence-Explore/1.0"}) as client:
        print("=" * 100)
        print("1. Award count by type for keyword 'arresting system'")
        print("=" * 100)
        counts = award_count_by_type(client, "arresting system")
        print(json.dumps(counts["results"], indent=2))
        print()

        print("=" * 100)
        print("2. CONTRACTS search for 'Engineered Material Arresting System' (exact phrase)")
        print("=" * 100)
        _print_awards(
            search_awards(client, "Engineered Material Arresting System", CONTRACT_TYPES),
            label="contracts / EMAS exact phrase",
        )

        print("=" * 100)
        print("3. GRANTS search for 'Engineered Material Arresting System' (exact phrase)")
        print("=" * 100)
        _print_awards(
            search_awards(client, "Engineered Material Arresting System", GRANT_TYPES, limit=20),
            label="grants / EMAS exact phrase",
        )

        print("=" * 100)
        print("4. GRANTS search for 'arresting system' (broader - includes military noise)")
        print("=" * 100)
        _print_awards(
            search_awards(client, "arresting system", GRANT_TYPES, limit=20),
            label="grants / arresting system (broad)",
        )

        print("=" * 100)
        print("5. Recipient search for 'Runway Safe' (is the vendor itself trackable?)")
        print("=" * 100)
        recipients = recipient_search(client, "Runway Safe")
        print(json.dumps(recipients, indent=2))
        print()

        print("=" * 100)
        print("6. CONTRACTS search for keyword 'EMAS' alone (sanity check - too short/ambiguous?)")
        print("=" * 100)
        _print_awards(
            search_awards(client, "EMAS", CONTRACT_TYPES, limit=5),
            label="contracts / bare 'EMAS'",
        )


if __name__ == "__main__":
    main()
