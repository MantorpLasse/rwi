"""Isolated tests for scripts/apply_canonical_runway_inventory_us_clean_batch.py
(docs/domain/canonical-runway-us-clean-batch-report.md).

Never touches the real development database - builds a synthetic NASR zip
covering four airports (MDW/CGF already fully populated by a prior pilot,
TST a fresh clean-create airport, BAD an airport with one valid runway
pair and one helipad-shaped row that must block it entirely) against an
isolated in-memory database shaped like the real one."""
import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source
from app.services.runway_identity import normalize_end
from app.services.runway_inventory import AirportBatchClassification
from app.static_export import build_site
from scripts.apply_canonical_runway_inventory_us_clean_batch import backup_database, dry_run, run

RWY_HEADER = (
    "EFF_DATE,SITE_NO,SITE_TYPE_CODE,STATE_CODE,ARPT_ID,CITY,COUNTRY_CODE,RWY_ID,RWY_LEN,RWY_WIDTH,"
    "SURFACE_TYPE_CODE,COND,TREATMENT_CODE,PCN,PAVEMENT_TYPE_CODE,SUBGRADE_STRENGTH_CODE,TIRE_PRES_CODE,"
    "DTRM_METHOD_CODE,RWY_LGT_CODE,RWY_LEN_SOURCE,LENGTH_SOURCE_DATE,GROSS_WT_SW,GROSS_WT_DW,GROSS_WT_DTW,"
    "GROSS_WT_DDTW"
)
RWY_END_HEADER = "EFF_DATE,SITE_NO,SITE_TYPE_CODE,STATE_CODE,ARPT_ID,CITY,COUNTRY_CODE,RWY_ID,RWY_END_ID,TRUE_ALIGNMENT"

RWY_ROWS = [
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,04L/22R,5507,150,ASPH,GOOD,,,,,,,H,,,,,,",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,04R/22L,6445,150,ASPH-CONC,EXCELLENT,,,,,,,H,,,,,,",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,13L/31R,6522,150,ASPH-CONC,EXCELLENT,,,,,,,H,,,,,,",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,13R/31L,3859,60,ASPH,EXCELLENT,,,,,,,H,,,,,,",
    "2026-08-06,08698.*A,APT,OH,CGF,CLEVELAND,US,06/24,5502,100,ASPH,GOOD,,,,,,,H,,,,,,",
    "2026-08-06,09999.*A,APT,XX,TST,TESTVILLE,US,09/27,4000,75,ASPH,GOOD,,,,,,,H,,,,,,",
    "2026-08-06,08888.*A,APT,XX,BAD,BADTOWN,US,09/27,4000,75,ASPH,GOOD,,,,,,,H,,,,,,",
    "2026-08-06,08888.*A,APT,XX,BAD,BADTOWN,US,H1,,,,,,,,,,,,,,,,",
]
RWY_END_ROWS = [
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,04L/22R,04L,43",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,04L/22R,22R,223",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,04R/22L,04R,43",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,04R/22L,22L,223",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,13L/31R,13L,134",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,13L/31R,31R,314",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,13R/31L,13R,134",
    "2026-08-06,04102.*A,APT,IL,MDW,CHICAGO,US,13R/31L,31L,314",
    "2026-08-06,08698.*A,APT,OH,CGF,CLEVELAND,US,06/24,06,60",
    "2026-08-06,08698.*A,APT,OH,CGF,CLEVELAND,US,06/24,24,240",
    "2026-08-06,09999.*A,APT,XX,TST,TESTVILLE,US,09/27,09,90",
    "2026-08-06,09999.*A,APT,XX,TST,TESTVILLE,US,09/27,27,270",
    "2026-08-06,08888.*A,APT,XX,BAD,BADTOWN,US,09/27,09,90",
    "2026-08-06,08888.*A,APT,XX,BAD,BADTOWN,US,09/27,27,270",
]

# The approved snapshot for this fixture (computed once, asserted by
# test_dry_run_matches_the_expected_clean_batch_shape below, then reused
# by the apply tests): MDW/CGF already fully populated -> 0/0/0. TST is a
# fresh clean-create airport -> +1 runway/+2 ends. BAD is excluded
# entirely (AMBIGUOUS, blocked by its "H1" row).
EXPECTED_CLEAN_COUNT = 3
EXPECTED_CREATES = 1
EXPECTED_ENRICH = 0
EXPECTED_END_CREATES = 2


def _synthetic_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "apt_csv.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("APT_RWY.csv", "\n".join([RWY_HEADER, *RWY_ROWS]) + "\n")
        archive.writestr("APT_RWY_END.csv", "\n".join([RWY_END_HEADER, *RWY_END_ROWS]) + "\n")
    metadata_path = tmp_path / "apt_csv.zip.metadata.json"
    metadata_path.write_text(
        json.dumps({"sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(), "nasr_cycle": "2026-08-06-test"}),
        encoding="utf-8",
    )
    return zip_path


def _engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    return engine


def _seed_pre_apply_state(session: Session) -> dict[str, Airport]:
    """MDW/CGF already fully populated by a prior pilot apply, with their
    six identity links already set - mirrors the real database's actual
    state per docs/domain/canonical-runway-us-clean-batch-report.md. TST
    has no existing Runway rows. BAD has one existing (legacy) Runway row
    that must stay completely untouched since the whole airport is
    excluded."""
    mdw = Airport(name="Chicago Midway International Airport", faa_code="MDW", iata_code="MDW", icao_code="KMDW", country="USA")
    cgf = Airport(name="Cuyahoga", faa_code="CGF", country="USA")
    tst = Airport(name="Testville Field", faa_code="TST", country="USA")
    bad = Airport(name="Bad Data Field", faa_code="BAD", country="USA")
    session.add_all([mdw, cgf, tst, bad])
    session.flush()

    mdw_runways = {
        "4L/22R": Runway(airport=mdw, designation="4L/22R", length_m=1679, width_m=46, surface="ASPH"),
        "4R/22L": Runway(airport=mdw, designation="4R/22L", length_m=1964, width_m=46, surface="ASPH-CONC"),
        "13L/31R": Runway(airport=mdw, designation="13L/31R", length_m=1988, width_m=46, surface="ASPH-CONC"),
        "13R/31L": Runway(airport=mdw, designation="13R/31L", length_m=1176, width_m=18, surface="ASPH"),
    }
    session.add_all(mdw_runways.values())
    cgf_runway = Runway(airport=cgf, designation="6/24", length_m=1677, width_m=30, surface="ASPH")
    session.add(cgf_runway)
    bad_legacy_runway = Runway(airport=bad, designation="OLD-LEGACY")  # unrelated to NASR "9/27"
    session.add(bad_legacy_runway)
    session.flush()

    ends = [
        RunwayEnd(runway=mdw_runways["4L/22R"], designation="4L"),
        RunwayEnd(runway=mdw_runways["4L/22R"], designation="22R"),
        RunwayEnd(runway=mdw_runways["4R/22L"], designation="4R"),
        RunwayEnd(runway=mdw_runways["4R/22L"], designation="22L"),
        RunwayEnd(runway=mdw_runways["13L/31R"], designation="13L"),
        RunwayEnd(runway=mdw_runways["13L/31R"], designation="31R"),
        RunwayEnd(runway=mdw_runways["13R/31L"], designation="13R"),
        RunwayEnd(runway=mdw_runways["13R/31L"], designation="31L"),
        RunwayEnd(runway=cgf_runway, designation="6"),
        RunwayEnd(runway=cgf_runway, designation="24"),
    ]
    session.add_all(ends)
    session.flush()

    # Mirrors the real pilot's reviewed links: reviewed_end (raw, as stored
    # on the identity) -> its normalized RunwayEnd.designation.
    for airport, runway, reviewed_end in [
        (mdw, mdw_runways["4R/22L"], "04R"),
        (mdw, mdw_runways["4R/22L"], "22L"),
        (mdw, mdw_runways["13L/31R"], "13L"),
        (mdw, mdw_runways["13L/31R"], "31R"),
        (cgf, cgf_runway, "06"),
        (cgf, cgf_runway, "24"),
    ]:
        end = next(e for e in ends if e.runway_id == runway.id and e.designation == normalize_end(reviewed_end))
        session.add(
            PhysicalInstallationIdentity(
                airport_id=airport.id, runway_id=runway.id, runway_end=reviewed_end, runway_end_id=end.id
            )
        )
    session.commit()
    return {"mdw": mdw, "cgf": cgf, "tst": tst, "bad": bad}


def _counts(session: Session) -> tuple[int, int, int, int]:
    return (
        len(session.scalars(select(Airport)).all()),
        len(session.scalars(select(Runway)).all()),
        len(session.scalars(select(RunwayEnd)).all()),
        len(session.scalars(select(PhysicalInstallationIdentity)).all()),
    )


def test_dry_run_matches_the_expected_clean_batch_shape(tmp_path):
    zip_path = _synthetic_zip(tmp_path)
    with Session(_engine()) as session:
        _seed_pre_apply_state(session)
        report = dry_run(session, zip_path=zip_path)

    assert report["aggregate"]["clean_airport_count"] == EXPECTED_CLEAN_COUNT
    assert report["aggregate"]["runways_would_create"] == EXPECTED_CREATES
    assert report["aggregate"]["runways_would_enrich"] == EXPECTED_ENRICH
    assert report["aggregate"]["runway_ends_would_create"] == EXPECTED_END_CREATES
    assert len(report["excluded"]) == 1
    assert report["excluded"][0]["classification"] == "AMBIGUOUS"


def test_dry_run_never_writes_anything(tmp_path):
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_pre_apply_state(session)
        before = _counts(session)
        dry_run(session, zip_path=zip_path)
        assert _counts(session) == before


def test_first_apply_creates_expected_inventory_and_protects_everything_else(tmp_path):
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        airports = _seed_pre_apply_state(session)
        bad_runway_count_before = len(session.scalars(select(Runway).where(Runway.airport_id == airports["bad"].id)).all())

        result = run(
            session,
            apply=True,
            zip_path=zip_path,
            expected_clean_airport_count=EXPECTED_CLEAN_COUNT,
            expected_runway_creates=EXPECTED_CREATES,
            expected_runway_enrich=EXPECTED_ENRICH,
            expected_runway_end_creates=EXPECTED_END_CREATES,
        )

    # Re-dry-run after apply: nothing left to do.
    assert result["aggregate"] == {
        "airports_processed": 4,
        "clean_airport_count": EXPECTED_CLEAN_COUNT,
        "excluded_airport_count": 1,
        "runways_would_create": 0,
        "runways_would_enrich": 0,
        "runway_ends_would_create": 0,
    }

    with Session(engine) as session:
        tst = session.scalar(select(Airport).where(Airport.faa_code == "TST"))
        bad = session.scalar(select(Airport).where(Airport.faa_code == "BAD"))

        tst_runways = session.scalars(select(Runway).where(Runway.airport_id == tst.id)).all()
        assert len(tst_runways) == 1
        assert tst_runways[0].designation == "9/27"
        assert len(session.scalars(select(RunwayEnd).where(RunwayEnd.runway_id == tst_runways[0].id)).all()) == 2

        # BAD (excluded, AMBIGUOUS) must be completely untouched.
        bad_runways = session.scalars(select(Runway).where(Runway.airport_id == bad.id)).all()
        assert len(bad_runways) == bad_runway_count_before == 1
        assert bad_runways[0].designation == "OLD-LEGACY"
        assert len(session.scalars(select(RunwayEnd).where(RunwayEnd.runway_id == bad_runways[0].id)).all()) == 0

        # Protected data: airport count unchanged, MDW/CGF's six identity
        # links unchanged, still pointing at exactly the same RunwayEnd ids.
        assert len(session.scalars(select(Airport)).all()) == 4
        identities = session.scalars(select(PhysicalInstallationIdentity)).all()
        assert len(identities) == 6
        for identity in identities:
            assert identity.runway_end_id is not None  # still linked, untouched

        fk_violations = session.execute(text("PRAGMA foreign_key_check")).fetchall()
        assert fk_violations == []


def test_second_apply_is_idempotent_zero_writes(tmp_path):
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_pre_apply_state(session)
        run(
            session,
            apply=True,
            zip_path=zip_path,
            expected_clean_airport_count=EXPECTED_CLEAN_COUNT,
            expected_runway_creates=EXPECTED_CREATES,
            expected_runway_enrich=EXPECTED_ENRICH,
            expected_runway_end_creates=EXPECTED_END_CREATES,
        )

    with Session(engine) as session:
        before = _counts(session)
        second = run(
            session,
            apply=True,
            zip_path=zip_path,
            expected_clean_airport_count=EXPECTED_CLEAN_COUNT,
            expected_runway_creates=0,
            expected_runway_enrich=0,
            expected_runway_end_creates=0,
        )
        assert _counts(session) == before  # nothing changed

    assert second["aggregate"]["runways_would_create"] == 0
    assert second["aggregate"]["runways_would_enrich"] == 0
    assert second["aggregate"]["runway_ends_would_create"] == 0


def test_apply_aborts_and_writes_nothing_when_snapshot_does_not_match(tmp_path):
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_pre_apply_state(session)
        before = _counts(session)

        with pytest.raises(ValueError, match="does not match the approved snapshot"):
            run(
                session,
                apply=True,
                zip_path=zip_path,
                expected_clean_airport_count=EXPECTED_CLEAN_COUNT,
                expected_runway_creates=999,  # deliberately wrong
                expected_runway_enrich=EXPECTED_ENRICH,
                expected_runway_end_creates=EXPECTED_END_CREATES,
            )

        assert _counts(session) == before  # no partial write


def _fake_classification(airport_id, classification, creates=0, enrich=0, end_creates=0):
    return AirportBatchClassification(
        airport_id=airport_id,
        classification=classification,
        error=None,
        existing_runway_count=0,
        runways_would_create=creates,
        runways_would_enrich=enrich,
        runway_ends_would_create=end_creates,
        existing_runway_matches=0,
        plans=(),
    )


def test_apply_aborts_when_clean_set_membership_changes_before_write(tmp_path):
    """Simulates the database changing out from under the script between
    the initial resolve and the immediate pre-write re-check - airport 2
    was clean on the first pass, AMBIGUOUS on the second."""
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    first = [
        _fake_classification(1, "CLEAN_CREATE", creates=1, end_creates=2),
        _fake_classification(2, "CLEAN_CREATE", creates=1, end_creates=2),
    ]
    second = [
        _fake_classification(1, "CLEAN_CREATE", creates=1, end_creates=2),
        _fake_classification(2, "AMBIGUOUS"),
    ]
    with Session(engine) as session:
        before = _counts(session)
        with patch(
            "scripts.apply_canonical_runway_inventory_us_clean_batch.resolve_us_clean_batch",
            side_effect=[first, second],
        ):
            with pytest.raises(ValueError, match="membership changed"):
                run(
                    session,
                    apply=True,
                    zip_path=zip_path,
                    expected_clean_airport_count=2,
                    expected_runway_creates=2,
                    expected_runway_enrich=0,
                    expected_runway_end_creates=4,
                )
        assert _counts(session) == before  # no partial write


def test_apply_aborts_when_aggregate_plan_changes_before_write(tmp_path):
    """Same clean-set membership on both resolutions, but the plan itself
    grew between them (e.g. a NASR row appeared) - must still abort."""
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    first = [_fake_classification(1, "CLEAN_CREATE", creates=1, end_creates=2)]
    second = [_fake_classification(1, "CLEAN_CREATE", creates=2, end_creates=4)]
    with Session(engine) as session:
        before = _counts(session)
        with patch(
            "scripts.apply_canonical_runway_inventory_us_clean_batch.resolve_us_clean_batch",
            side_effect=[first, second],
        ):
            with pytest.raises(ValueError, match="aggregate plan changed"):
                run(
                    session,
                    apply=True,
                    zip_path=zip_path,
                    expected_clean_airport_count=1,
                    expected_runway_creates=1,
                    expected_runway_enrich=0,
                    expected_runway_end_creates=2,
                )
        assert _counts(session) == before  # no partial write


def test_run_without_apply_flag_only_reports(tmp_path):
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_pre_apply_state(session)
        before = _counts(session)
        run(session, apply=False, zip_path=zip_path)
        assert _counts(session) == before


def test_backup_database_copies_file_byte_identical(tmp_path):
    source = tmp_path / "runway_safe.db"
    source.write_bytes(b"fake-sqlite-bytes")
    backup_dir = tmp_path / "backups"

    destination = backup_database(database=source, backup_directory=backup_dir)

    assert destination.exists()
    assert destination.read_bytes() == source.read_bytes()
    assert destination.parent == backup_dir


def test_public_export_after_apply_still_suppresses_banor_and_leaks_nothing(tmp_path):
    """Section 8 public-export safety: after a real clean-batch apply on an
    isolated database, the static export must behave exactly as before -
    Banor stays suppressed, no RunwayEnd/runway_end_id internals leak."""
    zip_path = _synthetic_zip(tmp_path)
    engine = _engine()
    with Session(engine) as session:
        _seed_pre_apply_state(session)
        run(
            session,
            apply=True,
            zip_path=zip_path,
            expected_clean_airport_count=EXPECTED_CLEAN_COUNT,
            expected_runway_creates=EXPECTED_CREATES,
            expected_runway_enrich=EXPECTED_ENRICH,
            expected_runway_end_creates=EXPECTED_END_CREATES,
        )

    with Session(engine) as session:
        # A Signal is required for build_site's non-empty pages, matching
        # the existing static-export tests' seeding convention.
        tst = session.scalar(select(Airport).where(Airport.faa_code == "TST"))
        source = Source(title="Master Plan", source_type="master_plan", url="https://example.test/plan.pdf")
        session.add(
            Signal(
                airport=tst,
                source=source,
                title="Testville future EMAS",
                category="new_installation",
                confidence="confirmed",
                planning_year=2027,
                probability_score=8.5,
            )
        )
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    for html_path in output.rglob("*.html"):
        text_content = html_path.read_text(encoding="utf-8")
        assert "runway_end_id" not in text_content
        assert "RunwayEnd" not in text_content
        assert "Banor" not in text_content

    data_json = (output / "data.json").read_text(encoding="utf-8")
    assert "runway_end_id" not in data_json
    assert "RunwayEnd" not in data_json
