"""app.services.runway_inventory clean-batch classification tests
(docs/domain/canonical-runway-us-wide-dry-run-report.md S6,
docs/domain/canonical-runway-us-clean-batch-report.md).

Classification is report-only orchestration on top of the unmodified
plan_airport_inventory()/apply_plan() - these tests build synthetic
RunwayRow/RunwayEndRow objects directly (no zip/CSV needed) against
isolated in-memory databases, never the real development database."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.evidence.nasr_apt_rwy import RunwayEndRow, RunwayRow
from app.models import Airport, Runway
import pytest

from app.services.runway_inventory import (
    ALREADY_COMPLETE,
    AMBIGUOUS,
    CLEAN_BATCH_CLASSIFICATIONS,
    CLEAN_CREATE,
    CLEAN_ENRICH,
    CONFLICT,
    UNRESOLVED,
    apply_plan,
    classify_airport_batch,
    clean_batch_aggregate,
    is_canonical_runway_candidate,
    plan_airport_inventory,
    resolve_us_clean_batch,
)


def _rwy(rwy_id: str, arpt_id: str = "TST", length: str = "5000", width: str = "100", surface: str = "ASPH") -> RunwayRow:
    return RunwayRow(
        line=2,
        values={
            "EFF_DATE": "08/06/2026",
            "SITE_NO": "1",
            "ARPT_ID": arpt_id,
            "CITY": "Test",
            "RWY_ID": rwy_id,
            "RWY_LEN": length,
            "RWY_WIDTH": width,
            "SURFACE_TYPE_CODE": surface,
        },
        artifact_sha256="test",
        cycle="test-cycle",
    )


def _end(rwy_id: str, end_id: str, arpt_id: str = "TST") -> RunwayEndRow:
    return RunwayEndRow(
        line=2,
        values={
            "EFF_DATE": "08/06/2026",
            "SITE_NO": "1",
            "ARPT_ID": arpt_id,
            "CITY": "Test",
            "RWY_ID": rwy_id,
            "RWY_END_ID": end_id,
            "TRUE_ALIGNMENT": "040",
        },
        artifact_sha256="test",
        cycle="test-cycle",
    )


def engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


# ---------------------------------------------------------------------------
# is_canonical_runway_candidate() - the NASR-input eligibility gate applied
# inside classify_airport_batch() before any row reaches
# plan_airport_inventory(). Structural only - no "H"/"B"/"X" prefix/suffix
# check anywhere.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("accepted", ["04R/22L", "13L/31R", "6/24"])
def test_is_canonical_runway_candidate_accepts_real_runway_pairs(accepted):
    assert is_canonical_runway_candidate(accepted) is True


@pytest.mark.parametrize(
    "rejected",
    ["H1", "H-A", "B1", "00X", "10X", "19X", "", "   ", None, "/22L", "04R/", "04R//22L", "04R/22L/XX"],
)
def test_is_canonical_runway_candidate_rejects_special_and_malformed_records(rejected):
    assert is_canonical_runway_candidate(rejected) is False


def test_classify_clean_create_when_airport_has_no_existing_runways():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        result = classify_airport_batch(session, airport, [_rwy("4/22")], [_end("4/22", "4"), _end("4/22", "22")])

        assert result.classification == CLEAN_CREATE
        assert result.error is None
        assert (result.runways_would_create, result.runways_would_enrich, result.runway_ends_would_create) == (1, 0, 2)
        assert result.existing_runway_count == 0
        assert len(result.plans) == 1


def test_classify_clean_enrich_when_existing_runway_would_be_enriched():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.flush()
        session.add(Runway(airport=airport, designation="4/22"))  # no dims yet
        session.commit()

        result = classify_airport_batch(session, airport, [_rwy("4/22")], [_end("4/22", "4"), _end("4/22", "22")])

        assert result.classification == CLEAN_ENRICH
        assert (result.runways_would_create, result.runways_would_enrich, result.runway_ends_would_create) == (0, 1, 2)


def test_classify_already_complete_after_apply_then_replan():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        rwy, ends = [_rwy("4/22")], [_end("4/22", "4"), _end("4/22", "22")]
        plans = plan_airport_inventory(session, airport, rwy, ends)
        apply_plan(session, airport, plans)
        session.commit()

        result = classify_airport_batch(session, airport, rwy, ends)

        assert result.classification == ALREADY_COMPLETE
        assert (result.runways_would_create, result.runways_would_enrich, result.runway_ends_would_create) == (0, 0, 0)
        assert result.existing_runway_matches == 1


def test_classify_skips_a_non_canonical_row_and_still_plans_the_valid_pair():
    """Mirrors the real NASR pattern found in the U.S.-wide dry run: a
    helipad ("H1") mixed into APT_RWY.csv has no "/" and is not a
    canonical-runway candidate - it must be excluded from planning input,
    not abort the whole airport's plan
    (docs/domain/nasr-special-record-classification-investigation.md)."""
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        result = classify_airport_batch(session, airport, [_rwy("4/22"), _rwy("H1")], [_end("4/22", "4"), _end("4/22", "22"), _end("H1", "H1")])

        assert result.classification == CLEAN_CREATE
        assert result.error is None
        assert len(result.plans) == 1
        assert result.plans[0].normalized_designation == "4/22"


def test_classify_unresolved_when_every_row_is_non_canonical():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        result = classify_airport_batch(session, airport, [_rwy("H1"), _rwy("00X")], [_end("H1", "H1")])

        assert result.classification == UNRESOLVED
        assert "only special/non-runway NASR records" in result.error


def test_classify_ambiguous_still_fires_for_a_genuine_non_numeric_heading():
    """A row that DOES have the two-ended pair shape (so it's a canonical
    candidate) but fails deeper normalization - e.g. a non-numeric heading
    - must still reach plan_airport_inventory() and fail closed there.
    is_canonical_runway_candidate() only screens shape, not full validity."""
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        result = classify_airport_batch(session, airport, [_rwy("AB/CD")], [_end("AB/CD", "AB"), _end("AB/CD", "CD")])

        assert result.classification == AMBIGUOUS
        assert "no numeric heading" in result.error


def test_classify_conflict_when_pair_and_end_rows_disagree():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        # Pair says 4/22, but APT_RWY_END.csv reports ends 5/23 - a genuine
        # source disagreement, not a formatting issue.
        result = classify_airport_batch(session, airport, [_rwy("4/22")], [_end("4/22", "5"), _end("4/22", "23")])

        assert result.classification == CONFLICT
        assert "do not match the pair designation" in result.error


def test_classify_conflict_when_two_rows_normalize_to_the_same_pair():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        rwy = [_rwy("4/22"), _rwy("04/22")]  # both normalize to "4/22"
        ends = [_end("4/22", "4"), _end("4/22", "22"), _end("04/22", "4"), _end("04/22", "22")]
        result = classify_airport_batch(session, airport, rwy, ends)

        assert result.classification == CONFLICT
        assert "normalize to the same runway pair" in result.error


def test_classify_unresolved_when_no_nasr_rows_match():
    with Session(engine()) as session:
        airport = Airport(name="Test", faa_code="TST", country="USA")
        session.add(airport)
        session.commit()

        result = classify_airport_batch(session, airport, [], [])

        assert result.classification == UNRESOLVED
        assert "no NASR runway rows matched" in result.error


def test_resolve_us_clean_batch_only_considers_the_given_country():
    with Session(engine()) as session:
        us = Airport(name="US Field", faa_code="TST", country="USA")
        foreign = Airport(name="Foreign Field", faa_code="TST", country="Canada")
        session.add_all([us, foreign])
        session.commit()

        rwy = [_rwy("4/22")]
        ends = [_end("4/22", "4"), _end("4/22", "22")]
        results = resolve_us_clean_batch(session, rwy, ends)

        assert {r.airport_id for r in results} == {us.id}  # Canada excluded entirely
        assert results[0].classification == CLEAN_CREATE


def test_resolve_us_clean_batch_marks_no_identifier_airport_unresolved():
    with Session(engine()) as session:
        airport = Airport(name="No Identifier", country="USA")  # faa/iata/icao all None
        session.add(airport)
        session.commit()

        results = resolve_us_clean_batch(session, [_rwy("4/22")], [_end("4/22", "4"), _end("4/22", "22")])

        assert len(results) == 1
        assert results[0].classification == UNRESOLVED
        assert "no FAA/IATA/ICAO identifier" in results[0].error


def test_resolve_us_clean_batch_flags_arpt_id_collision_as_conflict_for_both_airports():
    with Session(engine()) as session:
        a = Airport(name="A", faa_code="DUP", country="USA")
        b = Airport(name="B", iata_code="DUP", country="USA")  # same code, different field
        session.add_all([a, b])
        session.commit()

        results = resolve_us_clean_batch(session, [_rwy("4/22", arpt_id="DUP")], [_end("4/22", "4", arpt_id="DUP"), _end("4/22", "22", arpt_id="DUP")])

        assert {r.classification for r in results} == {CONFLICT}
        assert all("claimed by another airport" in r.error for r in results)


def test_clean_batch_aggregate_sums_only_clean_classifications():
    with Session(engine()) as session:
        clean = Airport(name="Clean", faa_code="CLN", country="USA")
        blocked = Airport(name="Blocked", faa_code="BLK", country="USA")
        session.add_all([clean, blocked])
        session.commit()

        rwy = [_rwy("4/22", arpt_id="CLN"), _rwy("H1", arpt_id="BLK")]
        ends = [_end("4/22", "4", arpt_id="CLN"), _end("4/22", "22", arpt_id="CLN")]
        results = resolve_us_clean_batch(session, rwy, ends)
        aggregate = clean_batch_aggregate(results)

        assert aggregate["airports_processed"] == 2
        assert aggregate["clean_airport_count"] == 1
        assert aggregate["excluded_airport_count"] == 1
        assert aggregate["runways_would_create"] == 1
        assert aggregate["runway_ends_would_create"] == 2

        blocked_result = next(r for r in results if r.airport_id == blocked.id)
        assert blocked_result.classification == UNRESOLVED  # BLK's only row (H1) has no canonical candidate
        assert blocked_result.classification not in CLEAN_BATCH_CLASSIFICATIONS
