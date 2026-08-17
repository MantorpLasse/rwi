"""app.acquisition.nasr_apt_csv and scripts/acquire_nasr_apt_csv.py tests.

No live FAA network calls anywhere in this file - every HTTP interaction
is mocked via httpx.MockTransport, matching tests/test_faa_runway_ends.py's
existing convention. Filesystem assertions use tmp_path exclusively; the
one exception (marked skipif) reads the real, already-preserved
data/raw/nasr/2026-08-06 archive/sidecar read-only, for format
compatibility only - it is never written to.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.acquisition.nasr_apt_csv import (
    ALLOWED_HOSTS,
    USER_AGENT,
    NasrAcquisitionError,
    _download_to_temp,
    _sha256_of_file,
    _validate_official_url,
    _validate_zip,
    acquire_and_preserve_nasr_apt,
    discover_nasr_apt_archive,
    verify_preserved_artifact,
)
from scripts.acquire_nasr_apt_csv import acquire as cli_acquire
from scripts.acquire_nasr_apt_csv import dry_run as cli_dry_run

INDEX_HTML = """
<html><body>
<a href="./../NASR_Subscription/2026-09-03">2026-09-03</a>
<a href="./../NASR_Subscription/2026-08-06">2026-08-06</a>
</body></html>
"""
CYCLE_HTML = """
<html><body>
<a href="https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip">Airports and Other Landing Facilities (APT)</a>
</body></html>
"""
ARCHIVE_URL = "https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip"
TODAY = date(2026, 8, 17)


def _valid_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("APT_ARS.csv", "a,b\n1,2\n")
        archive.writestr("APT_RWY.csv", "a,b\n1,2\n")
        archive.writestr("APT_RWY_END.csv", "a,b\n1,2\n")
    return buffer.getvalue()


def _zip_bytes_missing(missing: str) -> bytes:
    members = {"APT_ARS.csv", "APT_RWY.csv", "APT_RWY_END.csv"} - {missing}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in members:
            archive.writestr(name, "a,b\n1,2\n")
    return buffer.getvalue()


def _discovery_and_archive_handler(archive_bytes: bytes | Exception, *, archive_url: str = ARCHIVE_URL):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/":
            return httpx.Response(200, content=INDEX_HTML, request=request)
        if url == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-08-06/":
            return httpx.Response(200, content=CYCLE_HTML, request=request)
        if url == archive_url:
            if isinstance(archive_bytes, Exception):
                raise archive_bytes
            return httpx.Response(200, content=archive_bytes, request=request)
        return httpx.Response(404, request=request)

    return handler


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT})


# ---------------------------------------------------------------------------
# Discovery reuse
# ---------------------------------------------------------------------------


def test_discover_nasr_apt_archive_reuses_existing_discovery_logic():
    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        location = discover_nasr_apt_archive(client=client, today=TODAY)

    assert location.nasr_cycle == "2026-08-06"
    assert location.source_index_url == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/"
    assert location.cycle_page_url == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-08-06/"
    assert location.final_archive_url == ARCHIVE_URL


# ---------------------------------------------------------------------------
# Host allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", sorted(ALLOWED_HOSTS))
def test_validate_official_url_accepts_approved_hosts(host):
    _validate_official_url(f"https://{host}/some/path")  # must not raise


def test_validate_official_url_rejects_unapproved_host():
    with pytest.raises(NasrAcquisitionError, match="unapproved host"):
        _validate_official_url("https://evil.example/06_Aug_2026_APT_CSV.zip")


def test_validate_official_url_rejects_non_https():
    with pytest.raises(NasrAcquisitionError, match="non-HTTPS"):
        _validate_official_url("http://nfdc.faa.gov/webContent/28DaySub/extra/x.zip")


def test_download_to_temp_rejects_redirect_to_unapproved_host(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ARCHIVE_URL:
            return httpx.Response(302, headers={"location": "https://evil.example/x.zip"}, request=request)
        return httpx.Response(404, request=request)

    dest_dir = tmp_path / "2026-08-06"
    with _client(handler) as client:
        with pytest.raises(NasrAcquisitionError, match="unapproved host"):
            _download_to_temp(client, ARCHIVE_URL, dest_dir=dest_dir, timeout=10.0)

    assert list(dest_dir.glob("*")) == []  # no stray temp file left behind


# ---------------------------------------------------------------------------
# HTTP / User-Agent behavior
# ---------------------------------------------------------------------------


def test_acquisition_requests_send_the_expected_user_agent(tmp_path):
    seen_user_agents = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_user_agents.append(request.headers.get("user-agent"))
        return _discovery_and_archive_handler(_valid_zip_bytes())(request)

    with _client(handler) as client:
        acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    assert seen_user_agents  # at least one request was made
    assert all(ua == USER_AGENT for ua in seen_user_agents)
    assert "Mozilla/5.0" in USER_AGENT
    assert "RunwaySafeIntelligence" in USER_AGENT


# ---------------------------------------------------------------------------
# Temp download / hashing / size
# ---------------------------------------------------------------------------


def test_download_to_temp_writes_exact_bytes(tmp_path):
    content = _valid_zip_bytes()
    handler = _discovery_and_archive_handler(content)
    dest_dir = tmp_path / "2026-08-06"
    with _client(handler) as client:
        temp_path = _download_to_temp(client, ARCHIVE_URL, dest_dir=dest_dir, timeout=10.0)
        assert temp_path.parent == dest_dir
        assert temp_path.read_bytes() == content
        temp_path.unlink()  # this test only checks download behavior, not preservation


def test_sha256_of_file_matches_hashlib_reference(tmp_path):
    import hashlib

    path = tmp_path / "x.bin"
    path.write_bytes(b"some deterministic content")
    assert _sha256_of_file(path) == hashlib.sha256(b"some deterministic content").hexdigest()


# ---------------------------------------------------------------------------
# ZIP validation
# ---------------------------------------------------------------------------


def test_validate_zip_accepts_valid_archive(tmp_path):
    path = tmp_path / "valid.zip"
    path.write_bytes(_valid_zip_bytes())
    _validate_zip(path)  # must not raise


def test_validate_zip_rejects_corrupt_archive(tmp_path):
    path = tmp_path / "corrupt.zip"
    path.write_bytes(b"this is not a zip file at all")
    with pytest.raises(NasrAcquisitionError, match="not a valid ZIP"):
        _validate_zip(path)


@pytest.mark.parametrize("missing", ["APT_ARS.csv", "APT_RWY.csv", "APT_RWY_END.csv"])
def test_validate_zip_rejects_missing_required_member(tmp_path, missing):
    path = tmp_path / "incomplete.zip"
    path.write_bytes(_zip_bytes_missing(missing))
    with pytest.raises(NasrAcquisitionError, match="missing required member"):
        _validate_zip(path)


# ---------------------------------------------------------------------------
# Sidecar content
# ---------------------------------------------------------------------------


def test_sidecar_content_correctness(tmp_path):
    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        result = acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    sidecar = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 1
    assert sidecar["publisher"] == "Federal Aviation Administration"
    assert sidecar["source_index_url"] == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/"
    assert sidecar["cycle_page_url"] == "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-08-06/"
    assert sidecar["final_archive_url"] == ARCHIVE_URL
    assert sidecar["nasr_cycle"] == "2026-08-06"
    assert sidecar["sha256"] == result.sha256
    assert sidecar["byte_size"] == result.byte_size
    assert sidecar["local_archive_filename"] == "06_Aug_2026_APT_CSV.zip"
    assert sidecar["acquisition_method"] == "app.acquisition.nasr_apt_csv.acquire_and_preserve_nasr_apt"


def test_retrieved_at_is_utc_with_explicit_z_suffix(tmp_path):
    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        result = acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    sidecar = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
    retrieved_at = sidecar["retrieved_at"]
    assert retrieved_at.endswith("Z")
    from datetime import datetime

    datetime.strptime(retrieved_at, "%Y-%m-%dT%H:%M:%S.%fZ")  # must not raise


# ---------------------------------------------------------------------------
# First preservation / idempotency / collisions
# ---------------------------------------------------------------------------


def test_first_preservation_succeeds_and_writes_expected_files(tmp_path):
    content = _valid_zip_bytes()
    with _client(_discovery_and_archive_handler(content)) as client:
        result = acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    assert result.status == "preserved"
    assert result.archive_path == tmp_path / "2026-08-06" / "06_Aug_2026_APT_CSV.zip"
    assert result.sidecar_path == tmp_path / "2026-08-06" / "06_Aug_2026_APT_CSV.zip.metadata.json"
    assert result.archive_path.read_bytes() == content
    assert result.sidecar_path.is_file()
    # no stray temp/partial files left behind
    remaining = {p.name for p in result.archive_path.parent.glob("*")}
    assert remaining == {"06_Aug_2026_APT_CSV.zip", "06_Aug_2026_APT_CSV.zip.metadata.json"}


def test_second_acquisition_with_same_hash_is_idempotent(tmp_path):
    content = _valid_zip_bytes()
    with _client(_discovery_and_archive_handler(content)) as client:
        first = acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)
    archive_bytes_after_first = first.archive_path.read_bytes()
    sidecar_bytes_after_first = first.sidecar_path.read_bytes()

    with _client(_discovery_and_archive_handler(content)) as client:
        second = acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    assert second.status == "already_preserved"
    assert second.sha256 == first.sha256
    assert first.archive_path.read_bytes() == archive_bytes_after_first  # untouched
    assert first.sidecar_path.read_bytes() == sidecar_bytes_after_first  # untouched, not rewritten


def test_acquisition_with_different_hash_raises_collision_and_preserves_original(tmp_path):
    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        first = acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)
    archive_bytes_after_first = first.archive_path.read_bytes()
    sidecar_bytes_after_first = first.sidecar_path.read_bytes()

    # A genuinely different, still-valid archive - not just corrupted bytes.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("APT_ARS.csv", "a,b\n9,9\n")
        archive.writestr("APT_RWY.csv", "a,b\n9,9\n")
        archive.writestr("APT_RWY_END.csv", "a,b\n9,9\n")
    different_content = buffer.getvalue()

    with _client(_discovery_and_archive_handler(different_content)) as client:
        with pytest.raises(NasrAcquisitionError, match="[Cc]ollision"):
            acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    assert first.archive_path.read_bytes() == archive_bytes_after_first  # untouched
    assert first.sidecar_path.read_bytes() == sidecar_bytes_after_first  # untouched


def test_archive_without_sidecar_is_rejected(tmp_path):
    cycle_dir = tmp_path / "2026-08-06"
    cycle_dir.mkdir(parents=True)
    archive_path = cycle_dir / "06_Aug_2026_APT_CSV.zip"
    archive_path.write_bytes(_valid_zip_bytes())
    original_bytes = archive_path.read_bytes()

    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        with pytest.raises(NasrAcquisitionError, match="missing"):
            acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    assert archive_path.read_bytes() == original_bytes  # untouched
    assert not (cycle_dir / "06_Aug_2026_APT_CSV.zip.metadata.json").exists()


def test_sidecar_without_archive_is_rejected(tmp_path):
    cycle_dir = tmp_path / "2026-08-06"
    cycle_dir.mkdir(parents=True)
    sidecar_path = cycle_dir / "06_Aug_2026_APT_CSV.zip.metadata.json"
    sidecar_path.write_text(json.dumps({"sha256": "0" * 64, "byte_size": 1}), encoding="utf-8")
    original_bytes = sidecar_path.read_bytes()

    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        with pytest.raises(NasrAcquisitionError, match="missing"):
            acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    assert sidecar_path.read_bytes() == original_bytes  # untouched
    assert not (cycle_dir / "06_Aug_2026_APT_CSV.zip").exists()


def test_mismatched_sidecar_hash_is_rejected(tmp_path):
    cycle_dir = tmp_path / "2026-08-06"
    cycle_dir.mkdir(parents=True)
    archive_path = cycle_dir / "06_Aug_2026_APT_CSV.zip"
    content = _valid_zip_bytes()
    archive_path.write_bytes(content)
    sidecar_path = cycle_dir / "06_Aug_2026_APT_CSV.zip.metadata.json"
    sidecar_path.write_text(
        json.dumps({"sha256": "0" * 64, "byte_size": len(content)}), encoding="utf-8"
    )

    with pytest.raises(NasrAcquisitionError, match="sha256"):
        verify_preserved_artifact(archive_path, sidecar_path)


def test_mismatched_sidecar_size_is_rejected(tmp_path):
    cycle_dir = tmp_path / "2026-08-06"
    cycle_dir.mkdir(parents=True)
    archive_path = cycle_dir / "06_Aug_2026_APT_CSV.zip"
    content = _valid_zip_bytes()
    archive_path.write_bytes(content)
    import hashlib

    sidecar_path = cycle_dir / "06_Aug_2026_APT_CSV.zip.metadata.json"
    sidecar_path.write_text(
        json.dumps({"sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content) + 1}),
        encoding="utf-8",
    )

    with pytest.raises(NasrAcquisitionError, match="byte_size"):
        verify_preserved_artifact(archive_path, sidecar_path)


# ---------------------------------------------------------------------------
# Failure modes leave no final artifact
# ---------------------------------------------------------------------------


def test_network_failure_leaves_no_final_artifact(tmp_path):
    handler = _discovery_and_archive_handler(httpx.ConnectError("simulated connection failure"))
    with _client(handler) as client:
        with pytest.raises(NasrAcquisitionError):
            acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    cycle_dir = tmp_path / "2026-08-06"
    assert not (cycle_dir / "06_Aug_2026_APT_CSV.zip").exists()
    assert not (cycle_dir / "06_Aug_2026_APT_CSV.zip.metadata.json").exists()
    assert list(cycle_dir.glob("*")) == []  # no stray temp file either


def test_interrupted_corrupt_download_leaves_no_final_artifact(tmp_path):
    handler = _discovery_and_archive_handler(b"truncated garbage, not a real zip")
    with _client(handler) as client:
        with pytest.raises(NasrAcquisitionError, match="not a valid ZIP"):
            acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)

    cycle_dir = tmp_path / "2026-08-06"
    assert not (cycle_dir / "06_Aug_2026_APT_CSV.zip").exists()
    assert not (cycle_dir / "06_Aug_2026_APT_CSV.zip.metadata.json").exists()
    assert list(cycle_dir.glob("*")) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_performs_discovery_only_and_writes_nothing(tmp_path):
    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        report = cli_dry_run(raw_dir=tmp_path, client=client)

    assert report["resolved_cycle"] == "2026-08-06"
    assert report["final_archive_url"] == ARCHIVE_URL
    assert report["intended_archive_path"] == str(tmp_path / "2026-08-06" / "06_Aug_2026_APT_CSV.zip")
    assert report["already_preserved_locally"] is False
    assert list(tmp_path.glob("**/*")) == []  # absolutely nothing written


def test_cli_requires_explicit_acquire_flag_for_a_real_download(tmp_path):
    import scripts.acquire_nasr_apt_csv as cli_module

    calls = {"dry_run": 0, "acquire": 0}
    original_dry_run = cli_module.dry_run
    original_acquire = cli_module.acquire

    def spy_dry_run(**kwargs):
        calls["dry_run"] += 1
        return original_dry_run(**kwargs)

    def spy_acquire(**kwargs):
        calls["acquire"] += 1
        return original_acquire(**kwargs)

    cli_module.dry_run = spy_dry_run
    cli_module.acquire = spy_acquire
    try:
        with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
            # Simulates main()'s branching without hitting real network/argv.
            args_acquire = False
            (cli_module.acquire if args_acquire else cli_module.dry_run)(raw_dir=tmp_path, client=client)
    finally:
        cli_module.dry_run = original_dry_run
        cli_module.acquire = original_acquire

    assert calls == {"dry_run": 1, "acquire": 0}
    assert list(tmp_path.glob("**/*")) == []


def test_cli_acquire_flag_performs_real_preservation(tmp_path):
    with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
        report = cli_acquire(raw_dir=tmp_path, client=client)

    assert report["status"] == "preserved"
    assert Path(report["archive_path"]).is_file()
    assert Path(report["sidecar_path"]).is_file()


# ---------------------------------------------------------------------------
# No database dependency
# ---------------------------------------------------------------------------


def test_module_has_no_database_imports():
    import ast

    source = Path("app/acquisition/nasr_apt_csv.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            for alias in node.names:
                imported_names.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)

    forbidden_substrings = ("app.database", "SessionLocal", "sqlalchemy")
    for name in imported_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name, f"unexpected DB-layer import: {name}"


def test_full_acquisition_makes_no_database_writes(tmp_path):
    """Belt-and-suspenders: even though the module has no DB import at
    all (see test_module_has_no_database_imports), also prove a full
    acquisition run against a real isolated session leaves it untouched
    if one happens to be open concurrently - the acquisition call itself
    never receives or could receive a session."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with _client(_discovery_and_archive_handler(_valid_zip_bytes())) as client:
            acquire_and_preserve_nasr_apt(client=client, today=TODAY, raw_dir=tmp_path)
        assert len(session.new) == 0 and len(session.dirty) == 0


# ---------------------------------------------------------------------------
# Compatibility with the existing, real, already-preserved 2026-08-06 artifact
# ---------------------------------------------------------------------------

REAL_ZIP = Path("data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip")
REAL_SIDECAR = Path(str(REAL_ZIP) + ".metadata.json")


@pytest.mark.skipif(not REAL_ZIP.exists(), reason="preserved NASR artifact not present")
def test_verify_preserved_artifact_understands_the_real_existing_sidecar():
    """Read-only: proves this module's sidecar reader/validator can
    understand the actual on-disk format (including its UTF-8 BOM) of the
    artifact acquired outside this module, without modifying it."""
    original_archive_bytes = REAL_ZIP.read_bytes()
    original_sidecar_bytes = REAL_SIDECAR.read_bytes()

    sidecar = verify_preserved_artifact(REAL_ZIP, REAL_SIDECAR)

    assert sidecar["nasr_cycle"] == "2026-08-06"
    assert sidecar["sha256"] == "dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1"
    assert sidecar["byte_size"] == 8034151
    # Confirms this test truly was read-only.
    assert REAL_ZIP.read_bytes() == original_archive_bytes
    assert REAL_SIDECAR.read_bytes() == original_sidecar_bytes
