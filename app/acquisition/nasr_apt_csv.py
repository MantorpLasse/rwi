"""FAA NASR APT CSV acquisition-and-preserve foundation.

See docs/domain/nasr-acquisition-preserve-design.md and
docs/domain/nasr-acquisition-preserve-slice-report.md. This module only
discovers, downloads, validates, hashes, and preserves the raw archive
plus its sidecar JSON under data/raw/nasr/<cycle>/ - it never parses CSV
content, never touches a database (no SessionLocal/Session import
anywhere in this file, by design - see the "no DB dependency" test), and
never ingests/reconciles anything. Reuses
app.acquisition.faa_runway_ends's exact cycle-selection and archive-link
extraction logic unchanged; only the orchestration needed to also capture
the cycle string and cycle page URL (not just the final archive URL) is
new.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.acquisition.faa_runway_ends import (
    NASR_CYCLE_URL_TEMPLATE,
    NASR_INDEX_URL,
    RunwayEndsSourceError,
    _select_apt_csv_url,
    _select_effective_cycle,
)

DEFAULT_RAW_DIR = Path("data/raw/nasr")
SCHEMA_VERSION = 1
PUBLISHER = "Federal Aviation Administration"
DATASET = "NASR 28-Day Subscription — Airport (APT) CSV"
ACQUISITION_METHOD = "app.acquisition.nasr_apt_csv.acquire_and_preserve_nasr_apt"
# Identifiable, deterministic - not a spoofed browser version. FAA's edge
# rejected a generic httpx/WebFetch signature during live verification
# (docs/domain/nasr-acquisition-preserve-design.md S1); this is the
# minimum needed to be treated as an ordinary browser request.
USER_AGENT = (
    "Mozilla/5.0 (compatible; RunwaySafeIntelligence/NASRAcquisition; "
    "+https://github.com/MantorpLasse/rwi)"
)
ALLOWED_HOSTS = frozenset({"www.faa.gov", "nfdc.faa.gov"})
REQUIRED_MEMBERS = ("APT_ARS.CSV", "APT_RWY.CSV", "APT_RWY_END.CSV")
MAX_REDIRECTS = 5


class NasrAcquisitionError(ValueError):
    """Raised for any acquisition/preservation failure: host rejection,
    download/network failure, ZIP validation failure, or a provenance
    integrity/collision problem. Deliberately its own type (not
    RunwayEndsSourceError) - this module's failures are about acquiring
    and preserving raw bytes, not about EMAS-specific parsing."""


@dataclass(frozen=True)
class NasrArchiveLocation:
    nasr_cycle: str
    source_index_url: str
    cycle_page_url: str
    final_archive_url: str


@dataclass(frozen=True)
class NasrAcquisitionResult:
    status: str  # "preserved" | "already_preserved"
    archive_path: Path
    sidecar_path: Path
    sha256: str
    byte_size: int
    nasr_cycle: str


def _validate_official_url(url: str, *, allowed_hosts: frozenset[str] = ALLOWED_HOSTS) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise NasrAcquisitionError(f"Refusing non-HTTPS URL: {url!r}")
    if parts.hostname not in allowed_hosts:
        raise NasrAcquisitionError(f"Refusing URL on unapproved host {parts.hostname!r}: {url!r}")


def discover_nasr_apt_archive(*, client: httpx.Client, today: date, timeout: float = 30.0) -> NasrArchiveLocation:
    """Read-only discovery. Reuses app.acquisition.faa_runway_ends's exact
    cycle-selection (_select_effective_cycle) and archive-link extraction
    (_select_apt_csv_url) unchanged - this function only adds capturing the
    cycle string and cycle page URL alongside the final archive URL, which
    discover_apt_csv_url() itself discards."""
    _validate_official_url(NASR_INDEX_URL)
    try:
        index_response = client.get(NASR_INDEX_URL, timeout=timeout)
        index_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NasrAcquisitionError(f"Failed to fetch NASR index {NASR_INDEX_URL!r}: {exc}") from exc

    try:
        cycle = _select_effective_cycle(index_response.text, today)
    except RunwayEndsSourceError as exc:
        raise NasrAcquisitionError(str(exc)) from exc

    cycle_page_url = NASR_CYCLE_URL_TEMPLATE.format(cycle=cycle)
    _validate_official_url(cycle_page_url)
    try:
        cycle_response = client.get(cycle_page_url, timeout=timeout)
        cycle_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NasrAcquisitionError(f"Failed to fetch NASR cycle page {cycle_page_url!r}: {exc}") from exc

    try:
        final_archive_url = _select_apt_csv_url(cycle_response.text, cycle)
    except RunwayEndsSourceError as exc:
        raise NasrAcquisitionError(str(exc)) from exc
    _validate_official_url(final_archive_url)

    return NasrArchiveLocation(
        nasr_cycle=cycle,
        source_index_url=NASR_INDEX_URL,
        cycle_page_url=cycle_page_url,
        final_archive_url=final_archive_url,
    )


def _download_to_temp(client: httpx.Client, url: str, *, dest_dir: Path, timeout: float) -> Path:
    """Streams the response body to a temp file in dest_dir (same
    filesystem as the eventual destination, so the later move is an atomic
    rename). Manually resolves redirects one hop at a time - never reads a
    redirect response's body, and validates every hop's host/scheme before
    following it. Cleans up the temp file on any failure."""
    _validate_official_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=dest_dir, prefix=".nasr-download-", suffix=".partial")
    temp_path = Path(temp_name)
    os.close(fd)

    try:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            request = client.build_request("GET", current_url, timeout=timeout)
            try:
                response = client.send(request, stream=True, follow_redirects=False)
            except httpx.HTTPError as exc:
                raise NasrAcquisitionError(f"Failed to download {current_url!r}: {exc}") from exc
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise NasrAcquisitionError(f"Redirect from {current_url!r} had no Location header")
                    current_url = str(httpx.URL(current_url).join(location))
                    _validate_official_url(current_url)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise NasrAcquisitionError(f"Non-2xx response downloading {current_url!r}: {exc}") from exc
                with temp_path.open("wb") as handle:
                    try:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                    except httpx.HTTPError as exc:
                        raise NasrAcquisitionError(f"Download interrupted for {current_url!r}: {exc}") from exc
                return temp_path
            finally:
                response.close()
        raise NasrAcquisitionError(f"Too many redirects starting from {url!r}")
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _sha256_of_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_zip(path: Path) -> None:
    """Rejects a corrupt/truncated archive and one missing any required
    member. Never reads CSV content - only inspects the ZIP's own
    structure and member names."""
    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise NasrAcquisitionError(f"Corrupt member in downloaded archive: {bad_member!r}")
            names_upper = {name.upper() for name in archive.namelist()}
            missing = [
                member for member in REQUIRED_MEMBERS if not any(name.endswith(member) for name in names_upper)
            ]
            if missing:
                raise NasrAcquisitionError(f"Downloaded archive is missing required member(s): {missing!r}")
    except zipfile.BadZipFile as exc:
        raise NasrAcquisitionError(f"Downloaded file is not a valid ZIP archive: {exc}") from exc


def verify_preserved_artifact(archive_path: Path, sidecar_path: Path) -> dict:
    """Standalone, no-network integrity check of an already-preserved
    archive against its sidecar - usable independent of any acquisition
    (e.g. periodic re-verification). Returns the parsed sidecar dict on
    success; raises NasrAcquisitionError on any mismatch."""
    if archive_path.is_file() != sidecar_path.is_file():
        missing = "sidecar" if archive_path.is_file() else "archive"
        raise NasrAcquisitionError(
            f"Integrity error: {missing} is missing its counterpart - {archive_path} / {sidecar_path}"
        )
    if not archive_path.is_file():
        raise NasrAcquisitionError(f"Neither archive nor sidecar exists: {archive_path} / {sidecar_path}")

    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NasrAcquisitionError(f"Existing sidecar {sidecar_path} is unreadable/invalid: {exc}") from exc

    actual_size = archive_path.stat().st_size
    if sidecar.get("byte_size") != actual_size:
        raise NasrAcquisitionError(
            f"Integrity error: sidecar {sidecar_path} records byte_size={sidecar.get('byte_size')!r} "
            f"but the archive is actually {actual_size} bytes."
        )
    actual_sha256 = _sha256_of_file(archive_path)
    if sidecar.get("sha256") != actual_sha256:
        raise NasrAcquisitionError(
            f"Integrity error: sidecar {sidecar_path} records sha256={sidecar.get('sha256')!r} "
            f"but the archive's actual hash is {actual_sha256!r}."
        )
    return sidecar


def acquire_and_preserve_nasr_apt(
    *,
    client: httpx.Client,
    today: date | None = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
    timeout: float = 60.0,
) -> NasrAcquisitionResult:
    """Discover -> download to temp -> hash -> validate ZIP/members ->
    preserve atomically -> write sidecar. Never touches a database. Fails
    closed on any provenance collision or integrity problem - never
    overwrites an existing preserved archive or sidecar."""
    resolved_today = today if today is not None else datetime.now(UTC).date()
    location = discover_nasr_apt_archive(client=client, today=resolved_today, timeout=timeout)

    archive_filename = location.final_archive_url.rsplit("/", 1)[-1]
    if not archive_filename or "/" in archive_filename:
        raise NasrAcquisitionError(f"Could not determine archive filename from {location.final_archive_url!r}")

    cycle_dir = raw_dir / location.nasr_cycle
    final_archive_path = cycle_dir / archive_filename
    final_sidecar_path = cycle_dir / f"{archive_filename}.metadata.json"

    existing_archive = final_archive_path.is_file()
    existing_sidecar = final_sidecar_path.is_file()
    if existing_archive != existing_sidecar:
        missing = "sidecar" if existing_archive else "archive"
        raise NasrAcquisitionError(
            f"Integrity error: {missing} is missing for already-present cycle {location.nasr_cycle} "
            f"- refusing to touch either file ({final_archive_path} / {final_sidecar_path})."
        )
    if existing_archive and existing_sidecar:
        verify_preserved_artifact(final_archive_path, final_sidecar_path)

    temp_path = _download_to_temp(client, location.final_archive_url, dest_dir=cycle_dir, timeout=timeout)
    try:
        byte_size = temp_path.stat().st_size
        sha256 = _sha256_of_file(temp_path)
        _validate_zip(temp_path)

        if existing_archive:
            existing_sha256 = _sha256_of_file(final_archive_path)
            if existing_sha256 == sha256:
                return NasrAcquisitionResult(
                    status="already_preserved",
                    archive_path=final_archive_path,
                    sidecar_path=final_sidecar_path,
                    sha256=sha256,
                    byte_size=byte_size,
                    nasr_cycle=location.nasr_cycle,
                )
            raise NasrAcquisitionError(
                f"Provenance collision: newly downloaded {archive_filename!r} (sha256={sha256}) differs "
                f"from the already-preserved archive (sha256={existing_sha256}) for cycle "
                f"{location.nasr_cycle!r}. Neither file has been touched."
            )

        retrieved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        sidecar = {
            "schema_version": SCHEMA_VERSION,
            "publisher": PUBLISHER,
            "dataset": DATASET,
            "source_index_url": location.source_index_url,
            "cycle_page_url": location.cycle_page_url,
            "final_archive_url": location.final_archive_url,
            "nasr_cycle": location.nasr_cycle,
            "retrieved_at": retrieved_at,
            "sha256": sha256,
            "byte_size": byte_size,
            "local_archive_filename": archive_filename,
            "acquisition_method": ACQUISITION_METHOD,
        }
        temp_sidecar_path = cycle_dir / f".{final_sidecar_path.name}.tmp"
        temp_sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

        # Archive moves into place first, sidecar second. A crash between
        # these two renames leaves an archive-without-sidecar state, which
        # the "existing_archive != existing_sidecar" check above already
        # detects and refuses to proceed past on the next run - the window
        # is unavoidable with two files but never silently corrupts either.
        temp_path.replace(final_archive_path)
        temp_sidecar_path.replace(final_sidecar_path)

        return NasrAcquisitionResult(
            status="preserved",
            archive_path=final_archive_path,
            sidecar_path=final_sidecar_path,
            sha256=sha256,
            byte_size=byte_size,
            nasr_cycle=location.nasr_cycle,
        )
    finally:
        temp_path.unlink(missing_ok=True)
