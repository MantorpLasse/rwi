"""Isolated tests for scripts/correct_morristown_airport_identity.py
(docs/domain/morristown-airport-74-investigation.md,
docs/domain/morristown-airport-74-correction-report.md).

Never touches the real development database - builds isolated in-memory
databases seeded to mirror the real Airport id 74 row's exact shape,
including its Runway/RunwayEnd inventory."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, Runway, RunwayEnd
from scripts.correct_morristown_airport_identity import (
    EXPECTED_CURRENT,
    PROPOSED_NEW,
    TARGET_AIRPORT_ID,
    MorristownCorrectionError,
    apply,
    dry_run,
)

import pytest


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_matching_airport_74(session: Session) -> Airport:
    """Reproduces the real airport 74 row's exact current shape - name,
    identifiers, city/state, and stale notes - plus its real Runway/
    RunwayEnd inventory (5/23, 13/31), with a real id (autoincrement
    won't reliably land on 74 in an isolated DB, so the id is set
    explicitly)."""
    airport = Airport(
        id=TARGET_AIRPORT_ID,
        name="Town Of Morristown",
        faa_code="MMU",
        iata_code="MMU",
        icao_code="KMMU",
        city="Morristown",
        state_region="New Jersey",
        country="USA",
        notes=(
            "Name approximated from the USAspending grant recipient; no FAA "
            "Loc ID was available in the award description. Verify/correct "
            "manually if you find the airport's real identifiers."
        ),
    )
    session.add(airport)
    session.flush()
    runway_523 = Runway(airport_id=airport.id, designation="5/23")
    runway_1331 = Runway(airport_id=airport.id, designation="13/31")
    session.add_all([runway_523, runway_1331])
    session.flush()
    session.add_all([
        RunwayEnd(runway_id=runway_523.id, designation="5"),
        RunwayEnd(runway_id=runway_523.id, designation="23"),
        RunwayEnd(runway_id=runway_1331.id, designation="13"),
        RunwayEnd(runway_id=runway_1331.id, designation="31"),
    ])
    session.commit()
    return airport


def test_dry_run_reports_plan_when_preconditions_match():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)

        result = dry_run(session)

        assert result["preconditions_passed"] is True
        assert result["target_airport_id"] == TARGET_AIRPORT_ID
        assert result["old_values"]["name"] == "Town Of Morristown"
        assert result["old_values"]["faa_code"] == "MMU"
        assert result["proposed_new_values"] == {
            "name": "Morristown Municipal Airport",
            "notes": (
                "Identity confirmed via FAA NASR (ARPT_ID=MMU, ICAO_ID=KMMU, "
                "ARPT_NAME=MORRISTOWN MUNI) and the FAA IIJA Announcement 6 "
                "FY2026 grant PDF."
            ),
        }
        assert result["rows_that_would_change"] == 1


def test_proposed_values_are_exact():
    assert PROPOSED_NEW == {
        "name": "Morristown Municipal Airport",
        "notes": (
            "Identity confirmed via FAA NASR (ARPT_ID=MMU, ICAO_ID=KMMU, "
            "ARPT_NAME=MORRISTOWN MUNI) and the FAA IIJA Announcement 6 "
            "FY2026 grant PDF."
        ),
    }


def test_identifiers_and_location_are_not_in_the_proposed_change():
    assert "faa_code" not in PROPOSED_NEW
    assert "iata_code" not in PROPOSED_NEW
    assert "icao_code" not in PROPOSED_NEW
    assert "city" not in PROPOSED_NEW
    assert "state_region" not in PROPOSED_NEW


def test_dry_run_performs_no_db_mutation():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)

        dry_run(session)

        airport = session.get(Airport, TARGET_AIRPORT_ID)
        assert airport.name == "Town Of Morristown"
        assert airport.notes.startswith("Name approximated")
        assert len(session.new) == 0 and len(session.dirty) == 0


def test_apply_changes_exactly_one_airport_row():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)

        result = apply(session)

        assert result["rows_changed"] == 1
        airport = session.get(Airport, TARGET_AIRPORT_ID)
        assert airport.name == "Morristown Municipal Airport"
        assert airport.notes == PROPOSED_NEW["notes"]
        assert len(session.scalars(select(Airport)).all()) == 1  # no other Airport row created


def test_apply_leaves_identifiers_and_location_unchanged():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)

        apply(session)

        airport = session.get(Airport, TARGET_AIRPORT_ID)
        assert airport.faa_code == "MMU"
        assert airport.iata_code == "MMU"
        assert airport.icao_code == "KMMU"
        assert airport.city == "Morristown"
        assert airport.state_region == "New Jersey"
        assert airport.country == "USA"


def test_apply_leaves_runway_and_runway_end_rows_unchanged():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)
        runways_before = {(r.id, r.designation) for r in session.scalars(select(Runway)).all()}
        ends_before = {(e.id, e.designation) for e in session.scalars(select(RunwayEnd)).all()}

        apply(session)

        runways_after = {(r.id, r.designation) for r in session.scalars(select(Runway)).all()}
        ends_after = {(e.id, e.designation) for e in session.scalars(select(RunwayEnd)).all()}
        assert runways_after == runways_before
        assert ends_after == ends_before


def test_apply_leaves_unrelated_airport_rows_unchanged():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)
        other = Airport(id=999, name="Some Other Airport", faa_code="XYZ", country="USA")
        session.add(other)
        session.commit()

        apply(session)

        unrelated = session.get(Airport, 999)
        assert unrelated.name == "Some Other Airport"
        assert unrelated.faa_code == "XYZ"


def test_dry_run_fails_closed_on_drifted_name():
    with Session(_engine()) as session:
        airport = _seed_matching_airport_74(session)
        airport.name = "Something Else Entirely"
        session.commit()

        with pytest.raises(MorristownCorrectionError, match="Precondition failed"):
            dry_run(session)


def test_dry_run_fails_closed_on_drifted_identifier():
    """A different faa_code means the row's identity picture has already
    changed since the investigation - never overwrite based on stale
    assumptions."""
    with Session(_engine()) as session:
        airport = _seed_matching_airport_74(session)
        airport.faa_code = "XYZ"
        session.commit()

        with pytest.raises(MorristownCorrectionError, match="Precondition failed"):
            dry_run(session)


def test_dry_run_fails_closed_on_drifted_notes():
    with Session(_engine()) as session:
        airport = _seed_matching_airport_74(session)
        airport.notes = "Someone already edited this."
        session.commit()

        with pytest.raises(MorristownCorrectionError, match="Precondition failed"):
            dry_run(session)


def test_dry_run_fails_closed_on_faa_code_collision():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)
        session.add(Airport(name="Some Other MMU Claimant", faa_code="MMU", country="USA"))
        session.commit()

        with pytest.raises(MorristownCorrectionError, match="Collision.*MMU"):
            dry_run(session)


def test_dry_run_fails_closed_on_icao_code_collision():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)
        session.add(Airport(name="Some Other KMMU Claimant", icao_code="KMMU", country="USA"))
        session.commit()

        with pytest.raises(MorristownCorrectionError, match="Collision.*KMMU"):
            dry_run(session)


def test_dry_run_fails_closed_when_target_airport_does_not_exist():
    """The script only ever considers exactly airport id 74 - a
    coincidentally-matching row under a different id must not be used."""
    with Session(_engine()) as session:
        session.add(
            Airport(
                id=999,
                name="Town Of Morristown",
                faa_code="MMU",
                iata_code="MMU",
                icao_code="KMMU",
                city="Morristown",
                state_region="New Jersey",
                country="USA",
                notes=EXPECTED_CURRENT["notes"],
            )
        )
        session.commit()

        with pytest.raises(MorristownCorrectionError, match="does not exist"):
            dry_run(session)


def test_repeated_dry_run_is_deterministic():
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)

        first = dry_run(session)
        second = dry_run(session)

        assert first == second


def test_second_apply_fails_closed_not_idempotent_by_design():
    """This is a one-off correction, not a repeatable batch - a second
    run must fail closed (the row no longer matches EXPECTED_CURRENT,
    since name/notes already changed) rather than silently re-applying or
    silently succeeding as a no-op."""
    with Session(_engine()) as session:
        _seed_matching_airport_74(session)
        apply(session)

        with pytest.raises(MorristownCorrectionError, match="Precondition failed"):
            dry_run(session)
