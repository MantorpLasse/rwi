"""app.services.runway_inventory tests: deterministic planning, idempotent
upsert, and identity-link proposal - all against isolated in-memory
databases, never the real development database."""
import csv
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd
from app.evidence.nasr_apt_rwy import runway_end_rows, runway_rows
from app.services.runway_inventory import (
    apply_plan,
    evaluate_identity_links,
    evaluate_identity_links_from_raw,
    plan_airport_inventory,
)

FIXTURES = Path("tests/fixtures")


def _synthetic_zip(tmp_path: Path) -> tuple[Path, Path]:
    import hashlib
    import json

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


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _mdw_source_rows(tmp_path):
    zip_path, metadata_path = _synthetic_zip(tmp_path)
    rwy = [r for r in runway_rows(zip_path, metadata_path) if r.values["ARPT_ID"] == "MDW"]
    ends = [r for r in runway_end_rows(zip_path, metadata_path) if r.values["ARPT_ID"] == "MDW"]
    return rwy, ends


def _cgf_source_rows(tmp_path):
    zip_path, metadata_path = _synthetic_zip(tmp_path)
    rwy = [r for r in runway_rows(zip_path, metadata_path) if r.values["ARPT_ID"] == "CGF"]
    ends = [r for r in runway_end_rows(zip_path, metadata_path) if r.values["ARPT_ID"] == "CGF"]
    return rwy, ends


def test_mdw_plan_produces_four_runways_eight_ends_all_new(engine, tmp_path):
    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.commit()

        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)

        assert len(plans) == 4
        assert {p.normalized_designation for p in plans} == {"4L/22R", "4R/22L", "13L/31R", "13R/31L"}
        assert sum(1 for p in plans for _e in p.ends) == 8
        assert all(p.existing_id is None for p in plans)
        assert all(e.existing_id is None for p in plans for e in p.ends)


def test_plan_reuses_existing_legacy_runway_via_normalization_not_duplicate(engine, tmp_path):
    """CGF's real legacy row is designation="6/24" with no length/width -
    NASR gives "06/24". The plan must recognize these as the same runway
    (reuse) and mark it for enrichment, not propose a duplicate create."""
    with Session(engine) as session:
        airport = Airport(name="Cuyahoga", faa_code="CGF", country="USA")
        session.add(airport)
        session.flush()
        legacy = Runway(airport=airport, designation="6/24")
        session.add(legacy)
        session.commit()

        rwy, ends = _cgf_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)

        assert len(plans) == 1
        plan = plans[0]
        assert plan.existing_id == legacy.id
        assert plan.would_enrich is True
        assert plan.length_m == 1677  # 5502 ft -> m
        assert {e.designation for e in plan.ends} == {"6", "24"}
        assert all(e.existing_id is None for e in plan.ends)  # no RunwayEnd rows exist yet


def test_apply_plan_then_replan_is_idempotent(engine, tmp_path):
    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.commit()
        airport_id = airport.id

        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)
        stats = apply_plan(session, airport, plans)
        session.commit()
        assert stats == {"runways_created": 4, "runways_enriched": 0, "runway_ends_created": 8}

    # Fresh session, fresh query - simulates re-running the dry run as a
    # separate process against an already-populated database.
    with Session(engine) as session:
        airport = session.get(Airport, airport_id)
        plans2 = plan_airport_inventory(session, airport, rwy, ends)

        assert sum(1 for p in plans2 if p.existing_id is None) == 0
        assert sum(1 for p in plans2 for e in p.ends if e.existing_id is None) == 0
        assert session.scalar(select(Runway).where(Runway.airport_id == airport_id).limit(1)) is not None
        assert len(session.scalars(select(Runway).where(Runway.airport_id == airport_id)).all()) == 4
        assert len(session.scalars(select(RunwayEnd).join(Runway).where(Runway.airport_id == airport_id)).all()) == 8


def test_apply_plan_never_identifies_runways_by_airport_alone(engine, tmp_path):
    """Two different airports must never be matched to each other's runways
    just because a plan is scoped by caller-provided rows - designation
    match is always within one specific airport's own existing rows."""
    with Session(engine) as session:
        airport_a = Airport(name="A", faa_code="AAA", country="USA")
        airport_b = Airport(name="B", faa_code="BBB", country="USA")
        session.add_all([airport_a, airport_b])
        session.flush()
        session.add(Runway(airport=airport_b, designation="6/24"))  # same designation, different airport
        session.commit()

        rwy, ends = _cgf_source_rows(tmp_path)  # designation "06/24" -> "6/24"
        plans = plan_airport_inventory(session, airport_a, rwy, ends)

        assert len(plans) == 1
        assert plans[0].existing_id is None  # must NOT match airport_b's runway


def test_evaluate_identity_links_proposes_all_four_mdw_matches_but_applies_none(engine, tmp_path):
    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.flush()
        for end in ("04R", "13L", "22L", "31R"):
            session.add(PhysicalInstallationIdentity(airport_id=airport.id, runway_end=end))
        session.commit()

        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)
        proposals = evaluate_identity_links(session, airport, plans)

        assert {p.current_runway_end for p in proposals} == {"04R", "13L", "22L", "31R"}
        assert {p.matched_runway_end_designation for p in proposals} == {"4R", "13L", "22L", "31R"}
        # no automatic reconciliation: nothing in the DB was actually linked
        for identity in session.scalars(select(PhysicalInstallationIdentity)).all():
            assert identity.runway_end_id is None
            assert identity.runway_id is None


def test_evaluate_identity_links_excludes_zero_and_ambiguous_matches(engine, tmp_path):
    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.flush()
        session.add(PhysicalInstallationIdentity(airport_id=airport.id, runway_end="99"))  # no such end anywhere
        session.add(PhysicalInstallationIdentity(airport_id=airport.id, runway_end=None))  # nothing to match
        session.commit()

        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)
        proposals = evaluate_identity_links(session, airport, plans)

        assert proposals == []


def test_evaluate_identity_links_already_linked_are_excluded(engine, tmp_path):
    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.flush()
        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)
        stats = apply_plan(session, airport, plans)
        session.flush()
        linked_end = session.scalar(select(RunwayEnd).where(RunwayEnd.designation == "4R"))
        session.add(
            PhysicalInstallationIdentity(airport_id=airport.id, runway_end="04R", runway_end_id=linked_end.id)
        )
        session.commit()

        proposals = evaluate_identity_links(session, airport, plans)
        assert proposals == []  # already linked - not re-proposed


def test_evaluate_identity_links_from_raw_matches_the_orm_version(engine, tmp_path):
    """The pre-migration (real, not-yet-migrated database) matching path
    must agree exactly with the ORM path once the schema exists."""
    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.flush()
        rows = [(1, airport.id, None, "04R"), (2, airport.id, None, "13L"), (3, airport.id, None, "99")]
        for _id, _airport_id, _runway_id, runway_end in rows:
            session.add(PhysicalInstallationIdentity(airport_id=airport.id, runway_end=runway_end))
        session.commit()

        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)

        from_orm = evaluate_identity_links(session, airport, plans)
        from_raw = evaluate_identity_links_from_raw(rows, plans)

        assert {p.current_runway_end for p in from_orm} == {"04R", "13L"}
        assert {p.current_runway_end for p in from_raw} == {"04R", "13L"}


def test_planner_works_against_a_database_missing_the_runway_ends_table(tmp_path):
    """Simulates a real, not-yet-migrated database: runways exists,
    runway_ends does not. The planner must degrade to "no existing ends"
    rather than raising."""
    pre_migration_tables = [Base.metadata.tables[name] for name in ("airports", "runways")]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=pre_migration_tables)

    with Session(engine) as session:
        airport = Airport(name="Chicago Midway International Airport", faa_code="MDW", country="USA")
        session.add(airport)
        session.add(Runway(airport=airport, designation="13L/31R", length_m=1988, width_m=46))
        session.commit()

        rwy, ends = _mdw_source_rows(tmp_path)
        plans = plan_airport_inventory(session, airport, rwy, ends)

        assert len(plans) == 4
        matched = next(p for p in plans if p.normalized_designation == "13L/31R")
        assert matched.existing_id is not None
        assert all(e.existing_id is None for e in matched.ends)  # can't know about ends - table doesn't exist
