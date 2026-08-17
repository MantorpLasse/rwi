"""Isolated tests for scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py.

Never touches the real development database - builds a synthetic NASR zip
(reusing the same MDW/CGF fixture CSVs as Slice 1) and an isolated
in-memory database shaped like the real one before this pilot."""
import hashlib
import json
import zipfile
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd
from scripts.apply_canonical_runway_inventory_mdw_cgf_pilot import dry_run, run

FIXTURES = Path("tests/fixtures")


def _synthetic_zip(tmp_path: Path) -> tuple[Path, Path]:
    zip_path = tmp_path / "apt_csv.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("APT_RWY.csv", FIXTURES.joinpath("nasr_apt_rwy_sample.csv").read_text(encoding="utf-8"))
        archive.writestr(
            "APT_RWY_END.csv", FIXTURES.joinpath("nasr_apt_rwy_end_sample.csv").read_text(encoding="utf-8")
        )
    metadata_path = tmp_path / "apt_csv.zip.metadata.json"
    metadata_path.write_text(
        json.dumps({"sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(), "nasr_cycle": "2026-08-06-test"}),
        encoding="utf-8",
    )
    return zip_path, metadata_path


def _seed_real_shaped_state(session: Session) -> None:
    """Mirrors the real database's actual pre-pilot MDW/CGF state exactly:
    one legacy Runway each, four/two unlinked reviewed identities."""
    mdw = Airport(name="Chicago Midway International Airport", faa_code="MDW", iata_code="MDW", icao_code="KMDW", country="USA")
    cgf = Airport(name="Cuyahoga", faa_code="CGF", country="USA")
    session.add_all([mdw, cgf])
    session.flush()
    session.add(Runway(airport=mdw, designation="13L/31R", length_m=1988, width_m=46, surface="Asphalt/Concrete"))
    session.add(Runway(airport=cgf, designation="6/24"))  # no length/width/surface, matching the real legacy row
    for end in ("04R", "13L", "22L", "31R"):
        session.add(PhysicalInstallationIdentity(airport_id=mdw.id, runway_end=end))
    for end in ("06", "24"):
        session.add(PhysicalInstallationIdentity(airport_id=cgf.id, runway_end=end))
    session.commit()


def _engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    return engine


def test_dry_run_matches_the_real_database_investigation(tmp_path):
    zip_path, _ = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_real_shaped_state(session)
        report = dry_run(session, zip_path=zip_path)

    assert report["blockers"] == []
    mdw = report["airports"]["MDW"]
    assert (mdw["runways_would_create"], mdw["runways_would_enrich"], mdw["runways_unchanged"]) == (3, 0, 1)
    assert mdw["runway_ends_would_create"] == 8
    assert mdw["unresolved_identity_ids"] == []
    assert mdw["ambiguous_identity_ids"] == []
    assert mdw["duplicate_identity_targets"] == {}
    assert len(mdw["identity_mapping_proposals"]) == 4
    assert {p["reviewed_end"] for p in mdw["identity_mapping_proposals"]} == {"04R", "13L", "22L", "31R"}

    cgf = report["airports"]["CGF"]
    assert (cgf["runways_would_create"], cgf["runways_would_enrich"], cgf["runways_unchanged"]) == (0, 1, 0)
    assert cgf["runway_ends_would_create"] == 2
    assert cgf["unresolved_identity_ids"] == []
    assert cgf["ambiguous_identity_ids"] == []
    assert len(cgf["identity_mapping_proposals"]) == 2


def test_dry_run_never_writes_anything(tmp_path):
    zip_path, _ = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_real_shaped_state(session)
        dry_run(session, zip_path=zip_path)
        assert session.scalar(select(Runway)) is not None  # still just the 2 seeded rows
        assert len(session.scalars(select(Runway)).all()) == 2
        assert len(session.scalars(select(RunwayEnd)).all()) == 0
        for identity in session.scalars(select(PhysicalInstallationIdentity)).all():
            assert identity.runway_end_id is None


def test_apply_creates_expected_inventory_and_never_touches_identities(tmp_path):
    zip_path, _ = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_real_shaped_state(session)
        result = run(session, apply=True, zip_path=zip_path)

    assert result["airports"]["MDW"]["runways_would_create"] == 0  # re-dry-run after apply: nothing left to do
    assert result["airports"]["CGF"]["runways_would_enrich"] == 0

    with Session(engine) as session:
        mdw = session.scalar(select(Airport).where(Airport.faa_code == "MDW"))
        cgf = session.scalar(select(Airport).where(Airport.faa_code == "CGF"))
        assert len(session.scalars(select(Runway).where(Runway.airport_id == mdw.id)).all()) == 4
        assert len(session.scalars(select(RunwayEnd).join(Runway).where(Runway.airport_id == mdw.id)).all()) == 8
        cgf_runway = session.scalar(select(Runway).where(Runway.airport_id == cgf.id))
        assert (cgf_runway.length_m, cgf_runway.width_m, cgf_runway.surface) == (1677, 30, "ASPH")
        assert len(session.scalars(select(RunwayEnd).where(RunwayEnd.runway_id == cgf_runway.id)).all()) == 2

        # absolutely no identity was linked
        for identity in session.scalars(select(PhysicalInstallationIdentity)).all():
            assert identity.runway_end_id is None
            assert identity.runway_id is None

        fk_violations = session.execute(text("PRAGMA foreign_key_check")).fetchall()
        assert fk_violations == []


def test_second_apply_is_idempotent_zero_duplicates(tmp_path):
    zip_path, _ = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_real_shaped_state(session)
        run(session, apply=True, zip_path=zip_path)

    # Fresh session - simulates re-running the script as a separate process.
    with Session(engine) as session:
        second = run(session, apply=True, zip_path=zip_path)

    for code in ("MDW", "CGF"):
        report = second["airports"][code]
        assert report["runways_would_create"] == 0
        assert report["runways_would_enrich"] == 0
        assert report["runway_ends_would_create"] == 0

    with Session(engine) as session:
        mdw = session.scalar(select(Airport).where(Airport.faa_code == "MDW"))
        cgf = session.scalar(select(Airport).where(Airport.faa_code == "CGF"))
        assert len(session.scalars(select(Runway).where(Runway.airport_id == mdw.id)).all()) == 4
        assert len(session.scalars(select(Runway).where(Runway.airport_id == cgf.id)).all()) == 1
        assert len(session.scalars(select(RunwayEnd).join(Runway).where(Runway.airport_id == mdw.id)).all()) == 8
        assert len(session.scalars(select(RunwayEnd).join(Runway).where(Runway.airport_id == cgf.id)).all()) == 2


def test_run_without_apply_flag_only_reports(tmp_path):
    zip_path, _ = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_real_shaped_state(session)
        run(session, apply=False, zip_path=zip_path)
        assert len(session.scalars(select(Runway)).all()) == 2  # unchanged
        assert len(session.scalars(select(RunwayEnd)).all()) == 0
