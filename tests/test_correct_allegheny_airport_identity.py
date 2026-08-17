"""Isolated tests for scripts/correct_allegheny_airport_identity.py
(docs/domain/allegheny-unresolved-airport-investigation.md,
docs/domain/allegheny-deterministic-correction-dry-run.md).

Never touches the real development database - builds isolated in-memory
databases seeded to mirror the real Airport id 75 row's exact shape."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport
from scripts.correct_allegheny_airport_identity import (
    PROPOSED_NEW,
    TARGET_AIRPORT_ID,
    AlleghenyCorrectionError,
    apply,
    dry_run,
)

import pytest


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_matching_airport_75(session: Session) -> Airport:
    """Reproduces the real airport 75 row's exact current shape, with a
    real id (autoincrement won't reliably land on 75 in an isolated DB,
    so the id is set explicitly)."""
    airport = Airport(
        id=TARGET_AIRPORT_ID,
        name="Allegheny County Airport Authority",
        faa_code=None,
        iata_code=None,
        icao_code=None,
        city="West Mifflin",
        state_region="Pennsylvania",
        country="USA",
    )
    session.add(airport)
    session.commit()
    return airport


def test_dry_run_reports_plan_when_preconditions_match():
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)

        result = dry_run(session)

        assert result["preconditions_passed"] is True
        assert result["target_airport_id"] == TARGET_AIRPORT_ID
        assert result["old_values"]["name"] == "Allegheny County Airport Authority"
        assert result["old_values"]["faa_code"] is None
        assert result["proposed_new_values"] == {
            "name": "Allegheny County Airport",
            "faa_code": "AGC",
            "icao_code": "KAGC",
        }
        assert result["rows_that_would_change"] == 1


def test_proposed_values_are_exact():
    assert PROPOSED_NEW == {
        "name": "Allegheny County Airport",
        "faa_code": "AGC",
        "icao_code": "KAGC",
    }


def test_iata_city_state_are_not_in_the_proposed_change():
    assert "iata_code" not in PROPOSED_NEW
    assert "city" not in PROPOSED_NEW
    assert "state_region" not in PROPOSED_NEW


def test_dry_run_fails_closed_on_unexpected_current_name():
    with Session(_engine()) as session:
        airport = _seed_matching_airport_75(session)
        airport.name = "Something Else Entirely"
        session.commit()

        with pytest.raises(AlleghenyCorrectionError, match="Precondition failed"):
            dry_run(session)


def test_dry_run_fails_closed_when_faa_code_already_set():
    """A non-None faa_code means the row already changed since the
    investigation - never overwrite an already-corrected or
    already-different row."""
    with Session(_engine()) as session:
        airport = _seed_matching_airport_75(session)
        airport.faa_code = "XYZ"
        session.commit()

        with pytest.raises(AlleghenyCorrectionError, match="Precondition failed"):
            dry_run(session)


def test_dry_run_fails_closed_on_faa_code_collision():
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)
        session.add(Airport(name="Some Other AGC Claimant", faa_code="AGC", country="USA"))
        session.commit()

        with pytest.raises(AlleghenyCorrectionError, match="Collision.*AGC"):
            dry_run(session)


def test_dry_run_fails_closed_on_icao_code_collision():
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)
        session.add(Airport(name="Some Other KAGC Claimant", icao_code="KAGC", country="USA"))
        session.commit()

        with pytest.raises(AlleghenyCorrectionError, match="Collision.*KAGC"):
            dry_run(session)


def test_dry_run_fails_closed_when_target_airport_does_not_exist():
    """The script only ever considers exactly airport id 75 - a
    coincidentally-matching row under a different id must not be used."""
    with Session(_engine()) as session:
        session.add(
            Airport(
                id=999,
                name="Allegheny County Airport Authority",
                faa_code=None,
                iata_code=None,
                icao_code=None,
                city="West Mifflin",
                state_region="Pennsylvania",
                country="USA",
            )
        )
        session.commit()

        with pytest.raises(AlleghenyCorrectionError, match="does not exist"):
            dry_run(session)


def test_dry_run_performs_no_db_mutation():
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)

        dry_run(session)

        airport = session.get(Airport, TARGET_AIRPORT_ID)
        assert airport.name == "Allegheny County Airport Authority"
        assert airport.faa_code is None
        assert airport.icao_code is None
        assert len(session.new) == 0 and len(session.dirty) == 0


def test_repeated_dry_run_is_deterministic():
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)

        first = dry_run(session)
        second = dry_run(session)

        assert first == second


def test_isolated_apply_writes_exactly_the_three_fields():
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)

        result = apply(session)

        assert result["rows_changed"] == 1
        airport = session.get(Airport, TARGET_AIRPORT_ID)
        assert airport.name == "Allegheny County Airport"
        assert airport.faa_code == "AGC"
        assert airport.icao_code == "KAGC"
        # Unchanged fields.
        assert airport.iata_code is None
        assert airport.city == "West Mifflin"
        assert airport.state_region == "Pennsylvania"
        assert airport.country == "USA"

        # No other Airport row was touched or created.
        assert len(session.scalars(select(Airport)).all()) == 1


def test_isolated_second_apply_fails_closed_not_idempotent_by_design():
    """This is a one-off correction, not a repeatable batch - a second
    run must fail closed (the row no longer matches EXPECTED_CURRENT)
    rather than silently re-applying or silently succeeding as a no-op."""
    with Session(_engine()) as session:
        _seed_matching_airport_75(session)
        apply(session)

        with pytest.raises(AlleghenyCorrectionError, match="Precondition failed"):
            dry_run(session)
