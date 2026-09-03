"""RWI Mission #26G - offline tests for app.services.airport_coordinate
(the AirportCoordinate write gate).

Every test builds its own isolated temp-file SQLite database via
Base.metadata.create_all(); no network, no LLM, never touches
data/runway_safe.db."""

from __future__ import annotations

import math

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    AirportCoordinate,
    Installation,
    ReviewerAction,
    Signal,
    Source,
    SourceAssertion,
    SourceAssertionEvidenceBag,
    UnknownAirportCandidate,
)
from app.services.airport_coordinate import (
    CoordinateOutOfRangeError,
    EmptyAnalystError,
    EmptyEvidenceExcerptError,
    ExcerptNotInPreservedEvidenceError,
    InvalidAssertionTypeForCoordinateError,
    LiveProjectionDivergenceError,
    MissingCoordinateError,
    NonFiniteCoordinateError,
    SourceAssertionAirportMismatchError,
    SourceAssertionNotFoundError,
    StaleCurrentCoordinateError,
    UnexplainedLiveCoordinateError,
    accept_airport_coordinate,
    check_airport_coordinate_acceptance_eligibility,
    get_current_airport_coordinate,
)
from app.services.airport_coordinate import AirportNotFoundError as CoordAirportNotFoundError

EXCERPT = "ICAO: FMEE IATA: RUN\nLatitude\n20° 53' 13.56\" S\nLongitude\n55° 30' 37.08\" E"


@pytest.fixture()
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Roland Garros Airport", country="France", iata_code="RUN", icao_code="FMEE")
        other_airport = Airport(name="Other Airport", country="Elsewhere")
        s.add_all([airport, other_airport])
        s.commit()
        source = Source(title="OpenNav", source_type="web_discovery", reliability_level="unverified")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text=EXCERPT, source_record_identifier="coord-svc-1",
        )
        other_airport_assertion = SourceAssertion(
            source_id=source.id, airport_id=other_airport.id, assertion_type="airport_inventory",
            raw_relevant_text="Other airport text", source_record_identifier="coord-svc-2",
        )
        wrong_type_assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text=EXCERPT, source_record_identifier="coord-svc-3",
        )
        s.add_all([assertion, other_airport_assertion, wrong_type_assertion])
        s.commit()
        ids = {
            "airport_id": airport.id, "other_airport_id": other_airport.id, "source_id": source.id,
            "assertion_id": assertion.id, "other_airport_assertion_id": other_airport_assertion.id,
            "wrong_type_assertion_id": wrong_type_assertion.id,
        }
    return engine, db_path, ids


def _counts(engine) -> dict:
    with Session(engine) as s:
        return {
            "AirportCoordinate": s.scalar(select(func.count(AirportCoordinate.id))),
            "SourceAssertionEvidenceBag": s.scalar(select(func.count(SourceAssertionEvidenceBag.id))),
            "UnknownAirportCandidate": s.scalar(select(func.count(UnknownAirportCandidate.id))),
            "ReviewerAction": s.scalar(select(func.count(ReviewerAction.id))),
            "Installation": s.scalar(select(func.count(Installation.id))),
            "Signal": s.scalar(select(func.count(Signal.id))),
        }


LAT, LON = -20.8871, 55.5103


# --- 14-27: first acceptance -------------------------------------------------


def test_first_acceptance_full_field_verification(db):
    engine, db_path, ids = db
    before = _counts(engine)
    with Session(engine) as s:
        result = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="jane-analyst",
            expected_current_coordinate_id=None,
        )
        s.commit()
        assert result.coordinate_created is True
        assert result.airport_columns_written is True
        assert result.airport_id == ids["airport_id"]  # 15
        assert result.source_assertion_id == ids["assertion_id"]  # 16
        assert result.source_id == ids["source_id"]  # 17
        assert result.status == "ADMITTED"  # 18
        assert result.supersedes_coordinate_id is None

        row = s.get(AirportCoordinate, result.coordinate_id)
        assert row.evidence_excerpt == EXCERPT  # 19
        assert row.analyst == "jane-analyst"  # 20

        airport = s.get(Airport, ids["airport_id"])
        assert airport.latitude == LAT  # 21
        assert airport.longitude == LON  # 22

    after = _counts(engine)
    assert after["AirportCoordinate"] == before["AirportCoordinate"] + 1
    assert after["SourceAssertionEvidenceBag"] == before["SourceAssertionEvidenceBag"]  # 24
    assert after["UnknownAirportCandidate"] == before["UnknownAirportCandidate"]  # 25
    assert after["ReviewerAction"] == before["ReviewerAction"]  # 23
    assert after["Signal"] == before["Signal"]  # 26
    assert after["Installation"] == before["Installation"]  # 27


# --- 28-45: fail-closed conditions -------------------------------------------


def test_unknown_airport_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(CoordAirportNotFoundError):
            accept_airport_coordinate(
                s, airport_id=999999, source_assertion_id=ids["assertion_id"], latitude=LAT, longitude=LON,
                evidence_excerpt=EXCERPT, analyst="a", expected_current_coordinate_id=None,
            )


def test_unknown_source_assertion_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(SourceAssertionNotFoundError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=999999, latitude=LAT, longitude=LON,
                evidence_excerpt=EXCERPT, analyst="a", expected_current_coordinate_id=None,
            )


def test_source_assertion_different_airport_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(SourceAssertionAirportMismatchError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["other_airport_assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt="Other airport text", analyst="a",
                expected_current_coordinate_id=None,
            )


def test_wrong_assertion_type_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(InvalidAssertionTypeForCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["wrong_type_assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )


def test_excerpt_not_exact_substring_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(ExcerptNotInPreservedEvidenceError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt="This text is not in the assertion at all",
                analyst="a", expected_current_coordinate_id=None,
            )


def test_blank_excerpt_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(EmptyEvidenceExcerptError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt="   ", analyst="a",
                expected_current_coordinate_id=None,
            )


def test_blank_analyst_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(EmptyAnalystError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="  ",
                expected_current_coordinate_id=None,
            )


@pytest.mark.parametrize("bad_lat", [-90.001, 90.001])
def test_latitude_out_of_range_fails(db, bad_lat):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(CoordinateOutOfRangeError) as exc_info:
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=bad_lat, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )
        assert exc_info.value.field == "latitude"


@pytest.mark.parametrize("bad_lon", [-180.001, 180.001])
def test_longitude_out_of_range_fails(db, bad_lon):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(CoordinateOutOfRangeError) as exc_info:
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=bad_lon, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )
        assert exc_info.value.field == "longitude"


def test_nan_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(NonFiniteCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=float("nan"), longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )


def test_positive_infinity_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(NonFiniteCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=math.inf, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )


def test_negative_infinity_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(NonFiniteCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=-math.inf, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )


def test_missing_latitude_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(MissingCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=None, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )


def test_missing_longitude_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(MissingCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=None, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )


def test_first_write_expected_current_mismatch_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        with pytest.raises(StaleCurrentCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=999999,
            )


def test_legacy_populated_airport_without_history_fails_closed(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        airport = s.get(Airport, ids["airport_id"])
        airport.latitude, airport.longitude = 12.0, 34.0  # simulate legacy FAA-populated value
        s.commit()
    with Session(engine) as s:
        with pytest.raises(UnexplainedLiveCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,
            )
    # confirm the legacy value itself was never touched
    with Session(engine) as s:
        airport = s.get(Airport, ids["airport_id"])
        assert (airport.latitude, airport.longitude) == (12.0, 34.0)


# --- 46-49: idempotency -------------------------------------------------------


def test_exact_replay_creates_no_duplicate(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        first = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    before = _counts(engine)["AirportCoordinate"]

    with Session(engine) as s:
        second = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=first.coordinate_id,
        )
        s.commit()
        assert second.coordinate_created is False  # 46
        assert second.coordinate_id == first.coordinate_id  # 47
        assert second.airport_columns_written is False

        airport = s.get(Airport, ids["airport_id"])
        assert airport.latitude == LAT and airport.longitude == LON  # 48

    assert _counts(engine)["AirportCoordinate"] == before  # no duplicate


def test_replay_with_wrong_expected_current_still_gated(db):
    """A replay must still supply the correct expected_current_coordinate_id
    - idempotency does not bypass the concurrency gate."""
    engine, db_path, ids = db
    with Session(engine) as s:
        accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    with Session(engine) as s:
        with pytest.raises(StaleCurrentCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=None,  # wrong - a current row now exists
            )


def test_history_live_divergence_fails_closed(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        result = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    with Session(engine) as s:
        # Simulate an out-of-band divergence: someone changed Airport.latitude
        # directly without going through the write gate.
        airport = s.get(Airport, ids["airport_id"])
        airport.latitude = LAT + 5.0
        s.commit()
    with Session(engine) as s:
        with pytest.raises(LiveProjectionDivergenceError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=result.coordinate_id,
            )


# --- 50-59: replacement -------------------------------------------------------


def test_replacement_full_behavior(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        first = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    before = _counts(engine)["AirportCoordinate"]

    new_lat, new_lon = LAT + 0.01, LON + 0.01
    with Session(engine) as s:
        second = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=new_lat, longitude=new_lon, evidence_excerpt=EXCERPT, analyst="b",
            expected_current_coordinate_id=first.coordinate_id,
        )
        s.commit()
        assert second.coordinate_created is True  # 50
        assert second.coordinate_id != first.coordinate_id
        assert second.supersedes_coordinate_id == first.coordinate_id  # 52

        airport = s.get(Airport, ids["airport_id"])
        assert airport.latitude == new_lat and airport.longitude == new_lon  # 55

        old_row = s.get(AirportCoordinate, first.coordinate_id)
        assert old_row.status == "ADMITTED"  # 53 - never edited, never RETIRED
        assert old_row.latitude == LAT and old_row.longitude == LON

    after = _counts(engine)["AirportCoordinate"]
    assert after == before + 1  # 51 - exactly one new row, no companion RETIRED row (54)

    with Session(engine) as s:
        current = get_current_airport_coordinate(s, ids["airport_id"])
        assert current.id == second.coordinate_id


def test_replacement_stale_expected_current_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    with Session(engine) as s:
        with pytest.raises(StaleCurrentCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT + 1, longitude=LON + 1, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=999999,  # 56 - stale/wrong id
            )


def test_cross_airport_supersession_fails(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        first = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    with Session(engine) as s:
        # Attempting to "replace" a DIFFERENT Airport's coordinate using
        # this Airport's own row id as expected_current_coordinate_id.
        with pytest.raises(StaleCurrentCoordinateError):
            accept_airport_coordinate(
                s, airport_id=ids["other_airport_id"], source_assertion_id=ids["other_airport_assertion_id"],
                latitude=1.0, longitude=1.0, evidence_excerpt="Other airport text", analyst="a",
                expected_current_coordinate_id=first.coordinate_id,  # 57 - belongs to a different Airport
            )


def test_replacement_evidence_must_belong_to_same_airport(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        first = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    with Session(engine) as s:
        with pytest.raises(SourceAssertionAirportMismatchError):
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["other_airport_assertion_id"],
                latitude=LAT, longitude=LON, evidence_excerpt="Other airport text", analyst="a",
                expected_current_coordinate_id=first.coordinate_id,  # 58
            )


def test_replacement_rollback_leaves_history_and_live_unchanged(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        first = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    before_counts = _counts(engine)["AirportCoordinate"]
    with Session(engine) as s:
        airport = s.get(Airport, ids["airport_id"])
        before_lat, before_lon = airport.latitude, airport.longitude

    with Session(engine) as s:
        # A stale expected_current_coordinate_id fails inside
        # check_airport_coordinate_acceptance_eligibility() - before any
        # AirportCoordinate row is added. A caller-triggered rollback after
        # a caught failure must still leave BOTH the history table and the
        # live Airport projection exactly as they were (Mission #26G Part
        # AE.59) - proven here for the earliest, most common real failure
        # mode a reviewer will actually hit.
        try:
            accept_airport_coordinate(
                s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
                latitude=LAT + 9, longitude=LON + 9, evidence_excerpt=EXCERPT, analyst="a",
                expected_current_coordinate_id=999999,
            )
        except StaleCurrentCoordinateError:
            s.rollback()  # 59

    with Session(engine) as s:
        assert _counts(engine)["AirportCoordinate"] == before_counts
        airport = s.get(Airport, ids["airport_id"])
        assert (airport.latitude, airport.longitude) == (before_lat, before_lon)


# --- 60-62: current-head semantics -------------------------------------------


def test_current_head_returns_first_then_second_after_replacement(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        assert get_current_airport_coordinate(s, ids["airport_id"]) is None
        first = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()
    with Session(engine) as s:
        current = get_current_airport_coordinate(s, ids["airport_id"])
        assert current.id == first.coordinate_id  # 60

    with Session(engine) as s:
        second = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT + 0.5, longitude=LON + 0.5, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=first.coordinate_id,
        )
        s.commit()
    with Session(engine) as s:
        current = get_current_airport_coordinate(s, ids["airport_id"])
        assert current.id == second.coordinate_id  # 61
        old = s.get(AirportCoordinate, first.coordinate_id)
        assert old.status == "ADMITTED"  # 62 - remains historical, immutable, never edited


# --- preview eligibility function --------------------------------------------


def test_preview_would_be_idempotent_replay_flag(db):
    engine, db_path, ids = db
    with Session(engine) as s:
        eligibility_first = check_airport_coordinate_acceptance_eligibility(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        assert eligibility_first.would_be_idempotent_replay is False
        assert eligibility_first.current_coordinate_id is None

        result = accept_airport_coordinate(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=None,
        )
        s.commit()

    with Session(engine) as s:
        eligibility_second = check_airport_coordinate_acceptance_eligibility(
            s, airport_id=ids["airport_id"], source_assertion_id=ids["assertion_id"],
            latitude=LAT, longitude=LON, evidence_excerpt=EXCERPT, analyst="a",
            expected_current_coordinate_id=result.coordinate_id,
        )
        assert eligibility_second.would_be_idempotent_replay is True
        assert eligibility_second.current_coordinate_id == result.coordinate_id
