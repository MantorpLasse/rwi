"""Isolated tests for scripts/analyze_nasr_emas_runway_end_resolution.py
(docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md).

Never touches the real development database - builds isolated in-memory
databases per test. This module has no --apply/write path at all; these
tests additionally prove that classify_all()/run() never mutate the
session (no pending new/dirty objects after classification)."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import (
    Airport,
    Installation,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    Runway,
    RunwayEnd,
    Signal,
    Source,
    SourceAssertion,
)
from scripts.analyze_nasr_emas_runway_end_resolution import (
    ALREADY_LINKED,
    AMBIGUOUS,
    AUTO_RESOLVABLE,
    CONFLICT,
    INSUFFICIENT_EVIDENCE,
    REVIEW_REQUIRED,
    classify_all,
    run,
    summarize,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport_with_runway(session, *, name, code, pair, country="USA"):
    airport = Airport(name=name, faa_code=code, country=country)
    session.add(airport)
    session.flush()
    runway = Runway(airport_id=airport.id, designation=pair)
    session.add(runway)
    session.flush()
    end_a, end_b = pair.split("/")
    ra = RunwayEnd(runway_id=runway.id, designation=end_a)
    rb = RunwayEnd(runway_id=runway.id, designation=end_b)
    session.add_all([ra, rb])
    session.flush()
    return airport, runway, ra, rb


def _seed_nasr_assertion(session, *, airport_id, raw_pair, raw_end, source_title="NASR test cycle"):
    source = Source(
        title=source_title, source_type="faa_nasr_apt_ars",
        url="https://example.test/nasr",
        external_id=f"faa_nasr:airport_csv:test:{raw_pair}:{raw_end}:{source_title}",
    )
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport_id, assertion_type="runway_end",
        raw_airport_identifier="TST", raw_runway_value=raw_pair, raw_runway_end_value=raw_end,
        raw_relevant_text="{}", source_record_identifier=f"test:{raw_pair}:{raw_end}:{source.id}",
        evidence_quality="direct_strong", review_state="unreviewed",
    )
    session.add(assertion)
    session.commit()
    return assertion


def test_deterministic_physical_end_mapping_is_auto_resolvable():
    with Session(_engine()) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway(
            session, name="Test Field", code="TST", pair="9/27"
        )
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        results = classify_all(session)
        assert len(results) == 1
        result = results[0]
        assert result.classification == AUTO_RESOLVABLE
        assert result.candidate_runway_end_id == end_9.id
        assert result.candidate_designation == "9"


def test_reciprocal_derivation_uses_topology_not_designation_arithmetic():
    """The reciprocal of a physical end is derived as "the other RunwayEnd
    on the same canonical Runway" - proven here with a non-numeric-simple
    designation pair where naive heading arithmetic (e.g. +180 degrees)
    would not trivially apply, but topology still resolves it correctly."""
    with Session(_engine()) as session:
        airport, runway, end_4l, end_22r = _seed_airport_with_runway(
            session, name="Test Field", code="TST", pair="4L/22R"
        )
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="04L/22R", raw_end="04L")

        results = classify_all(session)
        result = results[0]
        assert result.candidate_designation == "4L"
        assert result.reciprocal_runway_end_id == end_22r.id
        assert result.reciprocal_designation == "22R"


def test_classify_all_performs_no_db_mutation():
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        classify_all(session)

        assert len(session.new) == 0 and len(session.dirty) == 0 and len(session.deleted) == 0


def test_run_performs_no_db_mutation_and_matches_direct_classify_all():
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")
        session.commit()

        report = run(session)

        assert report["summary"]["assertions_total"] == 1
        assert report["summary"]["by_classification"] == {AUTO_RESOLVABLE: 1}
        assert len(session.new) == 0 and len(session.dirty) == 0


def test_already_linked_when_reviewed_identity_exists_at_the_same_physical_end():
    with Session(_engine()) as session:
        airport, runway, end_6, end_24 = _seed_airport_with_runway(
            session, name="Test Field", code="TST", pair="6/24"
        )
        identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_id=runway.id, runway_end_id=end_6.id)
        session.add(identity)
        session.flush()
        prior_source = Source(title="Prior evidence", source_type="faa_tableau", url="https://example.test/prior")
        session.add(prior_source)
        session.flush()
        prior_assertion = SourceAssertion(
            source_id=prior_source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_end_value="06", source_record_identifier="prior-1",
            evidence_quality="direct_strong", review_state="reviewed",
        )
        session.add(prior_assertion)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=prior_assertion.id, physical_installation_id=identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION", reason="test", actor="human:test",
        ))
        session.commit()

        new_assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="06/24", raw_end="06")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[new_assertion.id].classification == ALREADY_LINKED


def test_duplicate_cycle_assertions_for_the_same_end_are_each_classified_consistently():
    """Two NASR cycles both reporting EMAS at the same physical end must
    not be treated as competing/ambiguous claims - both classify the same
    way (docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md
    S16)."""
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        a1 = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09", source_title="Cycle 1")
        a2 = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09", source_title="Cycle 2")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[a1.id].classification == AUTO_RESOLVABLE
        assert results[a2.id].classification == AUTO_RESOLVABLE
        assert results[a1.id].candidate_runway_end_id == results[a2.id].candidate_runway_end_id

        summary = summarize(list(results.values()))
        assert summary["duplicate_assertions_for_same_physical_end"]


def test_ambiguous_when_end_matches_more_than_one_candidate():
    """Two different canonical Runways at the same airport whose ends both
    normalize to the same token - a deliberately malformed fixture to
    prove the classifier fails closed (AMBIGUOUS) rather than picking one
    arbitrarily."""
    with Session(_engine()) as session:
        airport = Airport(name="Odd Field", faa_code="ODD", country="USA")
        session.add(airport)
        session.flush()
        runway_a = Runway(airport_id=airport.id, designation="9/27")
        runway_b = Runway(airport_id=airport.id, designation="9L/27R")
        session.add_all([runway_a, runway_b])
        session.flush()
        session.add_all([
            RunwayEnd(runway_id=runway_a.id, designation="9"),
            RunwayEnd(runway_id=runway_a.id, designation="27"),
        ])
        # Deliberately give runway_b an end that ALSO normalizes to "9"
        # (a malformed/duplicate designation across two runways) to force
        # the multi-candidate path.
        session.add_all([
            RunwayEnd(runway_id=runway_b.id, designation="9"),
            RunwayEnd(runway_id=runway_b.id, designation="27R"),
        ])
        session.commit()
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair=None, raw_end="09")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[assertion.id].classification == AMBIGUOUS


def test_conflict_when_a_different_physical_installation_decision_exists():
    with Session(_engine()) as session:
        airport, runway, end_6, end_24 = _seed_airport_with_runway(
            session, name="Test Field", code="TST", pair="6/24"
        )
        identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_id=runway.id, runway_end_id=end_6.id)
        session.add(identity)
        session.flush()
        prior_source = Source(title="Prior evidence", source_type="faa_tableau", url="https://example.test/prior")
        session.add(prior_source)
        session.flush()
        prior_assertion = SourceAssertion(
            source_id=prior_source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_end_value="06", source_record_identifier="prior-1",
            evidence_quality="direct_strong", review_state="reviewed",
        )
        session.add(prior_assertion)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=prior_assertion.id, physical_installation_id=identity.id,
            outcome="DIFFERENT_PHYSICAL_INSTALLATION", reason="test - deliberately rejected", actor="human:test",
        ))
        session.commit()

        new_assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="06/24", raw_end="06")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[new_assertion.id].classification == CONFLICT


def test_insufficient_evidence_when_no_matching_canonical_runway_exists():
    with Session(_engine()) as session:
        airport = Airport(name="No Runway Data Field", faa_code="NRD", country="USA")
        session.add(airport)
        session.flush()
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="9/27", raw_end="09")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[assertion.id].classification == INSUFFICIENT_EVIDENCE


def test_insufficient_evidence_when_assertion_has_no_airport():
    with Session(_engine()) as session:
        source = Source(title="Orphan NASR row", source_type="faa_nasr_apt_ars", url="https://example.test/orphan")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=None, assertion_type="runway_end",
            raw_runway_value="9/27", raw_runway_end_value="09", source_record_identifier="orphan-1",
            evidence_quality="direct_strong", review_state="unreviewed",
        )
        session.add(assertion)
        session.commit()

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[assertion.id].classification == INSUFFICIENT_EVIDENCE


def test_review_required_for_bos_shaped_dual_naming_evidence():
    """Reproduces the exact BOS pattern that motivated this analysis: NASR
    reports the physical bed at 4L, but a Signal's source_notes explicitly
    names "Runway 22R" (the reciprocal, public/operator naming) in an EMAS
    context - the mapping is still deterministic, but this must be flagged
    for human review, not silently auto-resolved."""
    with Session(_engine()) as session:
        airport, runway, end_4l, end_22r = _seed_airport_with_runway(
            session, name="Boston-Shaped Field", code="BST", pair="4L/22R"
        )
        source = Source(title="USAspending grant", source_type="usaspending_grant", url="https://example.test/g")
        session.add(source)
        session.flush()
        session.add(Signal(
            airport_id=airport.id, source_id=source.id, title="Phase 2", category="new_installation",
            confidence="confirmed", status="under construction",
            source_notes="Boston-Shaped Field already has EMAS in operation: Runway 22R and Runway 33L.",
        ))
        session.commit()
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="04L/22R", raw_end="04L")

        results = {r.assertion_id: r for r in classify_all(session)}
        result = results[assertion.id]
        assert result.classification == REVIEW_REQUIRED
        assert result.candidate_designation == "4L"
        assert "22R" in result.dual_naming_evidence


def test_auto_resolvable_for_orh_shaped_symmetric_case_without_dual_naming_text():
    """ORH's real evidence (docs/product/bos-orh-authoritative-web-research-pilot.md)
    does contain dual-naming text for its own assertions (tested via the
    real DB, not reproduced here) - this fixture instead proves the
    baseline case: a two-ended-EMAS airport with no such free text present
    classifies both ends as AUTO_RESOLVABLE, not flagged."""
    with Session(_engine()) as session:
        airport, runway, end_11, end_29 = _seed_airport_with_runway(
            session, name="Worcester-Shaped Field", code="ORS", pair="11/29"
        )
        a1 = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="11/29", raw_end="11", source_title="c1")
        a2 = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="11/29", raw_end="29", source_title="c2")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[a1.id].classification == AUTO_RESOLVABLE
        assert results[a2.id].classification == AUTO_RESOLVABLE
        assert {results[a1.id].candidate_designation, results[a2.id].candidate_designation} == {"11", "29"}


def test_mdw_cgf_shaped_existing_links_are_compatible_with_the_physical_semantic():
    """Reproduces the real MDW/CGF InstallationAssertionLink convention
    (all 8 real rows use the NASR RWY_END_ID unchanged, e.g. reason text
    "...explicitly reports EMAS at MDW runway end 04R...") - proves a new
    assertion at that same already-reviewed physical end classifies
    ALREADY_LINKED, and the classifier's own physical-identity semantic is
    consistent with (not contradicted by) the existing, already-approved
    real links."""
    with Session(_engine()) as session:
        airport, runway, end_04r, end_22l = _seed_airport_with_runway(
            session, name="Midway-Shaped Field", code="MDS", pair="4R/22L"
        )
        identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_id=runway.id, runway_end_id=end_04r.id)
        session.add(identity)
        session.flush()
        prior_source = Source(title="FAA NASR (prior cycle)", source_type="faa_nasr_apt_ars", url="https://example.test/prior")
        session.add(prior_source)
        session.flush()
        prior_assertion = SourceAssertion(
            source_id=prior_source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_end_value="04R", source_record_identifier="prior-mdw-1",
            evidence_quality="direct_strong", review_state="reviewed",
        )
        session.add(prior_assertion)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=prior_assertion.id, physical_installation_id=identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION",
            reason="FAA NASR explicitly reports EMAS at MDS runway end 04R; current-presence only.",
            actor="human:rwi-owner",
        ))
        session.commit()

        new_cycle_assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="04R/22L", raw_end="04R")

        results = {r.assertion_id: r for r in classify_all(session)}
        assert results[new_cycle_assertion.id].classification == ALREADY_LINKED
        assert results[new_cycle_assertion.id].candidate_runway_end_id == end_04r.id
