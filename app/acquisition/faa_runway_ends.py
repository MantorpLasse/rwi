from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date

import httpx


NASR_INDEX_URL = "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/"
NASR_CYCLE_URL_TEMPLATE = (
    "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/{cycle}/"
)
_CYCLE_DATE = re.compile(r"NASR_Subscription/(\d{4}-\d{2}-\d{2})")
_APT_CSV_HREF = re.compile(
    r'href="(https://nfdc\.faa\.gov/webContent/28DaySub/extra/[^"]*_APT_CSV\.zip)"'
)
EMAS_ARREST_DEVICE_CODE = "EMAS"


class RunwayEndsSourceError(ValueError):
    """Raised when the NASR subscription pages or APT CSV package have an unexpected shape."""


@dataclass(frozen=True)
class ArrestingSystemRow:
    arpt_id: str
    rwy_id: str
    rwy_end_id: str
    arrest_device_code: str


def _select_effective_cycle(index_html: str, today: date) -> str:
    """Pick the latest NASR cycle date (from the index page's own listing)
    that is already effective as of `today`. Shared with
    app.acquisition.nasr_apt_csv, which needs the cycle string itself (not
    just the final archive URL discover_apt_csv_url() below returns)."""
    cycles = sorted(set(_CYCLE_DATE.findall(index_html)))
    effective = [c for c in cycles if date.fromisoformat(c) <= today]
    if not effective:
        raise RunwayEndsSourceError(
            f"No effective NASR cycle found on or before {today} among {cycles!r}."
        )
    return effective[-1]


def _select_apt_csv_url(cycle_html: str, cycle: str) -> str:
    """Extract the APT CSV package link from one cycle's page. Shared with
    app.acquisition.nasr_apt_csv - see _select_effective_cycle above."""
    match = _APT_CSV_HREF.search(cycle_html)
    if not match:
        raise RunwayEndsSourceError(f"No APT CSV package link found for NASR cycle {cycle}.")
    return match.group(1)


def discover_apt_csv_url(*, client: httpx.Client, today: date, timeout: float = 30.0) -> str:
    """Find the current 28-day cycle's APT CSV package URL.

    The FAA's subscription index only lists the current and next cycle, each as
    its own dated page; the download link's exact date-stamped filename lives
    on that cycle's page, not the index.
    """

    response = client.get(NASR_INDEX_URL, timeout=timeout)
    response.raise_for_status()
    cycle = _select_effective_cycle(response.text, today)

    cycle_response = client.get(NASR_CYCLE_URL_TEMPLATE.format(cycle=cycle), timeout=timeout)
    cycle_response.raise_for_status()
    return _select_apt_csv_url(cycle_response.text, cycle)


def fetch_emas_arresting_system_rows(
    *, client: httpx.Client, apt_csv_url: str, timeout: float = 60.0
) -> list[ArrestingSystemRow]:
    """Download the NASR APT CSV package and extract EMAS-equipped runway ends.

    APT_ARS.csv also lists older cable arresting gear (BAK-12, MA-1A, ...);
    only EMAS_ARREST_DEVICE_CODE rows are returned since those are what our
    Installation table tracks.
    """

    response = client.get(apt_csv_url, timeout=timeout)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name = next((n for n in archive.namelist() if n.upper().endswith("APT_ARS.CSV")), None)
        if name is None:
            raise RunwayEndsSourceError("APT_ARS.csv not found in the NASR APT CSV package.")
        with archive.open(name) as handle:
            reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
            return [
                ArrestingSystemRow(
                    arpt_id=row["ARPT_ID"].strip(),
                    rwy_id=row["RWY_ID"].strip(),
                    rwy_end_id=row["RWY_END_ID"].strip(),
                    arrest_device_code=row["ARREST_DEVICE_CODE"].strip(),
                )
                for row in reader
                if row["ARREST_DEVICE_CODE"].strip() == EMAS_ARREST_DEVICE_CODE
            ]
