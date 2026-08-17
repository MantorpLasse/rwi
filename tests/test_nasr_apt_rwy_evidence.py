"""app.evidence.nasr_apt_rwy tests.

No network dependency anywhere in this file: the isolated tests build a
synthetic zip in memory from checked-in fixture CSVs; the "real archive"
tests only open the already-downloaded, checked-in
data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip from local disk.
"""
import csv
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.evidence.nasr_apt_rwy import runway_end_rows, runway_rows

FIXTURES = Path("tests/fixtures")
REAL_ZIP = Path("data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip")
REAL_METADATA = Path(str(REAL_ZIP) + ".metadata.json")


def _build_synthetic_zip(tmp_path: Path, *, corrupt_rwy_header: bool = False) -> tuple[Path, Path]:
    zip_path = tmp_path / "synthetic_apt_csv.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        rwy_text = FIXTURES.joinpath("nasr_apt_rwy_sample.csv").read_text(encoding="utf-8")
        if corrupt_rwy_header:
            rwy_text = rwy_text.replace("RWY_LEN", "RUNWAY_LENGTH_FT")
        archive.writestr("APT_RWY.csv", rwy_text)
        archive.writestr("APT_RWY_END.csv", FIXTURES.joinpath("nasr_apt_rwy_end_sample.csv").read_text(encoding="utf-8"))

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    metadata_path = tmp_path / "synthetic_apt_csv.zip.metadata.json"
    metadata_path.write_text(json.dumps({"sha256": sha256, "nasr_cycle": "2026-08-06-test"}), encoding="utf-8")
    return zip_path, metadata_path


def test_authentic_rwy_fixture_has_the_expected_schema():
    rows = list(csv.DictReader(FIXTURES.joinpath("nasr_apt_rwy_sample.csv").open(encoding="utf-8")))
    assert {r["ARPT_ID"] for r in rows} == {"MDW", "CGF"}
    assert len([r for r in rows if r["ARPT_ID"] == "MDW"]) == 4
    assert len([r for r in rows if r["ARPT_ID"] == "CGF"]) == 1


def test_authentic_rwy_end_fixture_has_the_expected_schema():
    rows = list(csv.DictReader(FIXTURES.joinpath("nasr_apt_rwy_end_sample.csv").open(encoding="utf-8")))
    assert len([r for r in rows if r["ARPT_ID"] == "MDW"]) == 8
    assert len([r for r in rows if r["ARPT_ID"] == "CGF"]) == 2


def test_runway_rows_reads_a_verified_synthetic_zip(tmp_path):
    zip_path, metadata_path = _build_synthetic_zip(tmp_path)
    rows = list(runway_rows(zip_path, metadata_path))
    assert len(rows) == 5
    mdw = [r for r in rows if r.values["ARPT_ID"] == "MDW"]
    assert {r.values["RWY_ID"] for r in mdw} == {"04L/22R", "04R/22L", "13L/31R", "13R/31L"}
    assert all(r.locator().startswith("APT_RWY.csv:line=") for r in rows)
    assert all(len(r.hash()) == 64 for r in rows)


def test_runway_end_rows_reads_a_verified_synthetic_zip(tmp_path):
    zip_path, metadata_path = _build_synthetic_zip(tmp_path)
    rows = list(runway_end_rows(zip_path, metadata_path))
    assert len(rows) == 10
    cgf = [r for r in rows if r.values["ARPT_ID"] == "CGF"]
    assert {r.values["RWY_END_ID"] for r in cgf} == {"06", "24"}
    assert all(r.locator().startswith("APT_RWY_END.csv:line=") for r in rows)


def test_runway_rows_fails_closed_on_artifact_sha_mismatch(tmp_path):
    zip_path, metadata_path = _build_synthetic_zip(tmp_path)
    metadata_path.write_text(json.dumps({"sha256": "0" * 64, "nasr_cycle": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        list(runway_rows(zip_path, metadata_path))


def test_runway_rows_fails_closed_on_missing_required_column(tmp_path):
    zip_path, metadata_path = _build_synthetic_zip(tmp_path, corrupt_rwy_header=True)
    with pytest.raises(ValueError, match="missing a required column"):
        list(runway_rows(zip_path, metadata_path))


@pytest.mark.skipif(not REAL_ZIP.exists(), reason="preserved NASR artifact not present")
def test_real_preserved_artifact_gives_mdw_four_runways_eight_ends():
    """Regression check against the actual preserved artifact (no network -
    local file only), pinning the exact facts the design investigation and
    the real-DB dry run both depend on."""
    mdw_runways = [r for r in runway_rows(REAL_ZIP, REAL_METADATA) if r.values["ARPT_ID"] == "MDW"]
    mdw_ends = [r for r in runway_end_rows(REAL_ZIP, REAL_METADATA) if r.values["ARPT_ID"] == "MDW"]
    assert len(mdw_runways) == 4
    assert len(mdw_ends) == 8
    assert {r.values["RWY_ID"] for r in mdw_runways} == {"04L/22R", "04R/22L", "13L/31R", "13R/31L"}
    assert {r.values["RWY_END_ID"] for r in mdw_ends} == {
        "04L", "22R", "04R", "22L", "13L", "31R", "13R", "31L"
    }


@pytest.mark.skipif(not REAL_ZIP.exists(), reason="preserved NASR artifact not present")
def test_real_preserved_artifact_gives_cgf_one_runway_two_ends():
    cgf_runways = [r for r in runway_rows(REAL_ZIP, REAL_METADATA) if r.values["ARPT_ID"] == "CGF"]
    cgf_ends = [r for r in runway_end_rows(REAL_ZIP, REAL_METADATA) if r.values["ARPT_ID"] == "CGF"]
    assert len(cgf_runways) == 1
    assert cgf_runways[0].values["RWY_ID"] == "06/24"
    assert len(cgf_ends) == 2
    assert {r.values["RWY_END_ID"] for r in cgf_ends} == {"06", "24"}


@pytest.mark.skipif(not REAL_ZIP.exists(), reason="preserved NASR artifact not present")
def test_real_preserved_artifact_ars_emas_ends_are_a_subset_of_rwy_end_ends():
    """Cross-check the design doc's core claim: EMAS presence (APT_ARS.csv,
    already used elsewhere) is a subset of the physical inventory
    (APT_RWY_END.csv, read here) - never the other way around."""
    from app.evidence.nasr_apt_ars import rows as emas_rows

    mdw_ends = {r.values["RWY_END_ID"] for r in runway_end_rows(REAL_ZIP, REAL_METADATA) if r.values["ARPT_ID"] == "MDW"}
    mdw_emas_ends = {r.values["RWY_END_ID"] for r in emas_rows(REAL_ZIP, REAL_METADATA) if r.values["ARPT_ID"] == "MDW"}
    assert mdw_emas_ends == {"04R", "22L", "13L", "31R"}
    assert mdw_emas_ends <= mdw_ends
