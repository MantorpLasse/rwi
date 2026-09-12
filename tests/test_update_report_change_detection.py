"""RWI HQ "Update & Report V1" mission - tests for
app/services/update_report_change_detection.py (Part 16 items 4-16 and 29)."""
from __future__ import annotations

import itertools

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, Signal, Source, SourceAssertion
from app.services.update_report_change_detection import (
    CandidateEvidenceInput,
    UpdateReportChangeDetectionError,
    classify_candidate,
)
from app.services.update_report_vocabulary import MaterialChangeClassification, ReportPriority


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_airport(session, **overrides) -> Airport:
    defaults = dict(name="Test Airport", iata_code="TST", country="USA")
    defaults.update(overrides)
    airport = Airport(**defaults)
    session.add(airport)
    session.flush()
    return airport


def make_runway(session, airport, **overrides) -> Runway:
    defaults = dict(airport=airport, designation="17L-35R")
    defaults.update(overrides)
    runway = Runway(**defaults)
    session.add(runway)
    session.flush()
    return runway


def make_source(session, **overrides) -> Source:
    defaults = dict(title="Test Source", source_type="news", reliability_level="official", url="https://example.test/a")
    defaults.update(overrides)
    source = Source(**defaults)
    session.add(source)
    session.flush()
    return source


def make_signal(session, airport, **overrides) -> Signal:
    defaults = dict(airport=airport, title="Test Signal", category="new_installation", confidence="medium", status="funded")
    defaults.update(overrides)
    signal = Signal(**defaults)
    session.add(signal)
    session.flush()
    return signal


_assertion_seq = itertools.count(1)


def make_assertion(session, source, **overrides) -> SourceAssertion:
    defaults = dict(
        source=source, assertion_type="project_construction",
        source_record_identifier=f"rec-{next(_assertion_seq)}",
        raw_relevant_text="Some evidence text.",
    )
    defaults.update(overrides)
    assertion = SourceAssertion(**defaults)
    session.add(assertion)
    session.flush()
    return assertion


def _dirty_and_new_counts(session):
    return len(session.new), len(session.dirty), len(session.deleted)


# --- CHANGE DETECTION ---------------------------------------------------


def test_exact_field_change_candidate():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2028)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(
        session,
        CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2030}),
    )

    assert result.classification == MaterialChangeClassification.FIELD_CHANGE_CANDIDATE
    assert result.existing_signal_id == signal.id
    assert result.old_values == {"target_year": 2028}
    assert result.proposed_changes == {"target_year": 2030}
    assert result.requires_human_review is True
    assert result.report_priority == ReportPriority.MEDIUM


def test_high_priority_field_change_candidate():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(
        session,
        CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"confirmed_vendor": "Acme EMAS Co"}),
    )

    assert result.classification == MaterialChangeClassification.FIELD_CHANGE_CANDIDATE
    assert result.report_priority == ReportPriority.HIGH


def test_no_op_evidence_becomes_no_material_change():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2030)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(
        session,
        CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2030}),
    )

    assert result.classification == MaterialChangeClassification.NO_MATERIAL_CHANGE
    assert result.requires_human_review is False
    assert result.report_priority == ReportPriority.NONE


def test_corroboration_only_when_anchored_with_no_proposed_change():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.classification == MaterialChangeClassification.CORROBORATION_ONLY
    assert result.existing_signal_id == signal.id
    assert result.report_priority == ReportPriority.LOW
    assert result.requires_human_review is False


def test_duplicate_evidence_already_linked_to_a_signal():
    session = make_session()
    airport = make_airport(session)
    signal = make_signal(session, airport)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, signal_id=signal.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.classification == MaterialChangeClassification.DUPLICATE
    assert result.report_priority == ReportPriority.NONE


def test_duplicate_evidence_resubmitted_within_the_same_batch():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id), is_duplicate_in_batch=True)

    assert result.classification == MaterialChangeClassification.DUPLICATE


def test_contradiction_or_correction_when_explicitly_flagged():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, status="funded")
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(
        session,
        CandidateEvidenceInput(
            source_assertion_id=assertion.id, proposed_changes={"status": "cancelled"}, correction_flag=True,
        ),
    )

    assert result.classification == MaterialChangeClassification.CONTRADICTION_OR_CORRECTION
    assert result.report_priority == ReportPriority.HIGH


def test_unresolved_identity_becomes_needs_more_evidence_when_multiple_anchors():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    other_source = make_source(session, title="Shared inventory source")
    signal_a = make_signal(session, airport, runway=runway, source=other_source)
    signal_b_assertion_source = make_source(session, title="Signal B source")
    signal_b = make_signal(session, airport, runway=runway, source=signal_b_assertion_source)
    # Two existing Signals anchor on the SAME runway - the new evidence
    # cannot be safely resolved to exactly one of them.
    new_source = make_source(session)
    assertion = make_assertion(session, new_source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE
    assert result.identity_resolved is False
    assert result.requires_human_review is True
    assert result.unresolved_identity_notes


def test_same_airport_does_not_imply_same_project():
    session = make_session()
    airport = make_airport(session)
    # An existing Signal at the SAME airport but on a DIFFERENT runway, and
    # no other structural anchor (no shared runway, no shared source, no
    # shared physical installation) - must never be treated as a match.
    other_runway = make_runway(session, airport, designation="09-27")
    make_signal(session, airport, runway=other_runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE
    assert result.existing_signal_id is None
    assert result.reconciliation_outcome == "CLEAR_TO_CREATE"


def test_same_runway_does_not_imply_same_runway_end():
    """runway_end is never read by this module (the canonical identity is
    runway_id, per app.services.existing_signal_reconciliation's own
    design) - two assertions naming opposite runway_end text but the same
    canonical runway_id still anchor to the same Signal, proving
    `runway_end` never factors into the decision either way."""
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id, runway_end="SOUTH")

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.classification == MaterialChangeClassification.CORROBORATION_ONLY
    assert result.existing_signal_id == signal.id


def test_new_signal_candidate_requires_explicit_operator_assertion():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)

    result = classify_candidate(
        session, CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_new_signal=True),
    )

    assert result.classification == MaterialChangeClassification.NEW_SIGNAL_CANDIDATE
    assert result.report_priority == ReportPriority.HIGH
    assert result.requires_human_review is True


def test_unknown_source_assertion_raises():
    session = make_session()
    try:
        classify_candidate(session, CandidateEvidenceInput(source_assertion_id=999999))
        assert False, "expected UpdateReportChangeDetectionError"
    except UpdateReportChangeDetectionError:
        pass


def test_invalid_proposed_field_is_refused_as_needs_more_evidence():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(
        session,
        CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"published": True}),
    )

    assert result.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE
    assert result.unresolved_identity_notes


# --- FUNDING INVARIANTS ---------------------------------------------------


def test_grant_amount_never_mapped_to_project_total_without_explicit_instruction():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, estimated_total_value_usd=None)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.proposed_changes == {}
    assert result.existing_signal_snapshot["estimated_total_value_usd"] is None


def test_multiple_grant_signals_are_never_summed():
    session = make_session()
    airport = make_airport(session)
    from decimal import Decimal

    make_signal(session, airport, title="Grant 1", category="replacement", estimated_total_value_usd=Decimal("100.00"))
    make_signal(session, airport, title="Grant 2", category="replacement", estimated_total_value_usd=Decimal("200.00"))
    source = make_source(session)
    # No runway/anchor shared with either grant Signal - a 3rd grant record
    # with no structural anchor must never be silently rolled into either.
    assertion = make_assertion(session, source, airport_id=airport.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.existing_signal_id is None
    assert result.proposed_changes == {}
    assert result.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE


# --- HUMAN REVIEW REPRESENTATION -------------------------------------------


def test_candidate_exposes_current_and_proposed_values():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2028, status="funded")
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)

    result = classify_candidate(
        session,
        CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2031}),
    )

    assert result.existing_signal_snapshot["target_year"] == 2028
    assert result.existing_signal_snapshot["status"] == "funded"
    assert result.old_values["target_year"] == 2028
    assert result.proposed_changes["target_year"] == 2031


def test_evidence_provenance_is_visible():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session, title="FAA CIP Document", url="https://faa.example.test/cip.pdf")
    assertion = make_assertion(session, source, airport_id=airport.id, raw_relevant_text="Construction begins Q1 2027.")

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    assert result.source_title == "FAA CIP Document"
    assert result.source_url == "https://faa.example.test/cip.pdf"
    assert result.supporting_evidence_excerpt == "Construction begins Q1 2027."


def test_unsupported_inference_is_explicitly_absent_from_reasoning():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)

    result = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    joined = " ".join(result.reasoning)
    assert "project total" in joined
    assert "runway end" in joined
    assert "FH-D4" in joined


# --- WRITE SAFETY -----------------------------------------------------------


def test_detection_produces_zero_writes():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2028)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    session.commit()

    before = _dirty_and_new_counts(session)
    classify_candidate(
        session,
        CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2035}),
    )
    after = _dirty_and_new_counts(session)

    assert before == (0, 0, 0)
    assert after == (0, 0, 0)
    session.refresh(signal)
    assert signal.target_year == 2028
