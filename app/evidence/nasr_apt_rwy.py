"""Preserved-artifact-only NASR runway/runway-end inventory reader.

Reads APT_RWY.csv (one row per runway pair) and APT_RWY_END.csv (one row
per runway end) from the already-downloaded, SHA-256-verified NASR APT CSV
archive. This is the CANONICAL RUNWAY INVENTORY source and is deliberately
kept separate from app/evidence/nasr_apt_ars.py, which is EMAS PRESENCE
EVIDENCE only (docs/domain/canonical-runway-runway-end-design.md S4) - a
runway/end physically existing and a runway/end currently reporting EMAS
are different claims, even though both come from the same NASR cycle.

No network access. Reads only the local zip already used by
app/evidence/nasr_apt_ars.py.
"""
import csv, hashlib, json, zipfile
from dataclasses import dataclass
from pathlib import Path

# The columns this reader actually depends on. APT_RWY.csv/APT_RWY_END.csv
# carry many more columns (geometry, lighting, pavement condition, etc.) -
# unlike app/evidence/nasr_apt_ars.py's exact-tuple check (reasonable there
# since APT_ARS.csv IS those 10 columns), a subset check is used here so an
# unrelated FAA layout addition to APT_RWY_END.csv's ~76 columns doesn't
# break this reader; a *missing* required column still fails closed.
REQUIRED_RWY = ("EFF_DATE", "SITE_NO", "ARPT_ID", "CITY", "RWY_ID", "RWY_LEN", "RWY_WIDTH", "SURFACE_TYPE_CODE")
REQUIRED_RWY_END = ("EFF_DATE", "SITE_NO", "ARPT_ID", "CITY", "RWY_ID", "RWY_END_ID", "TRUE_ALIGNMENT")


@dataclass(frozen=True)
class RunwayRow:
    """One APT_RWY.csv record: one physical runway pair."""

    line: int
    values: dict
    artifact_sha256: str
    cycle: str

    def raw(self) -> str:
        return json.dumps(self.values, sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.raw().encode()).hexdigest()

    def locator(self) -> str:
        return f"APT_RWY.csv:line={self.line}"


@dataclass(frozen=True)
class RunwayEndRow:
    """One APT_RWY_END.csv record: one runway end."""

    line: int
    values: dict
    artifact_sha256: str
    cycle: str

    def raw(self) -> str:
        return json.dumps(self.values, sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.raw().encode()).hexdigest()

    def locator(self) -> str:
        return f"APT_RWY_END.csv:line={self.line}"


def _verify_artifact(zip_path: Path, metadata_path: Path) -> tuple[str, dict]:
    meta = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if actual != meta["sha256"]:
        raise ValueError("artifact SHA-256 mismatch")
    return actual, meta


def _read_csv_member(zip_path: Path, member_suffix: str):
    with zipfile.ZipFile(zip_path) as archive:
        name = next(n for n in archive.namelist() if n.upper().endswith(member_suffix))
        with archive.open(name) as handle:
            yield from csv.DictReader((line.decode("utf-8-sig") for line in handle))


def runway_rows(zip_path: Path, metadata_path: Path):
    """Yield one RunwayRow per APT_RWY.csv record - every runway pair, not
    filtered to any airport or to EMAS presence."""
    artifact_sha256, meta = _verify_artifact(zip_path, metadata_path)
    for line, row in enumerate(_read_csv_member(zip_path, "APT_RWY.CSV"), 2):
        if not set(REQUIRED_RWY) <= set(row):
            raise ValueError("APT_RWY.csv is missing a required column")
        clean = {key: row[key].strip() for key in REQUIRED_RWY}
        yield RunwayRow(line, clean, artifact_sha256, meta["nasr_cycle"])


def runway_end_rows(zip_path: Path, metadata_path: Path):
    """Yield one RunwayEndRow per APT_RWY_END.csv record - every runway
    end, not filtered to any airport or to EMAS presence."""
    artifact_sha256, meta = _verify_artifact(zip_path, metadata_path)
    for line, row in enumerate(_read_csv_member(zip_path, "APT_RWY_END.CSV"), 2):
        if not set(REQUIRED_RWY_END) <= set(row):
            raise ValueError("APT_RWY_END.csv is missing a required column")
        clean = {key: row[key].strip() for key in REQUIRED_RWY_END}
        yield RunwayEndRow(line, clean, artifact_sha256, meta["nasr_cycle"])
