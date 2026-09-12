"""RWI HQ "Update & Report V1" mission - tests for
app/services/update_report_apply.py and app/services/update_report_engine.py
(Part 16 items 17-28)."""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, Signal, Source, SourceAssertion
from app.services.signal_amendment_history import list_signal_amendments
from app.services.update_report_apply import UpdateReportApplyError, apply_field_change_candidate
from app.services.update_report_change_detection import CandidateEvidenceInput, classify_candidate
from app.services.update_report_engine import generate_update_report, render_report_text
from app.services.update_report_vocabulary import MaterialChangeClassification
from app.services.update_report_watchset import WatchItem


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


def _field_change_candidate(session, *, correction=False):
    airport = make_airport(session)
    runway = make_runway(session, airport)
    signal = make_signal(session, airport, runway=runway, target_year=2028)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    session.commit()
    result = classify_candidate(
        session,
        CandidateEvidenceInput(
            source_assertion_id=assertion.id, proposed_changes={"target_year": 2031}, correction_flag=correction,
        ),
    )
    return signal, assertion, result


# --- APPLY / WRITE SAFETY ----------------------------------------------------


def test_apply_uses_amend_signal_and_creates_audit_history():
    session = make_session()
    signal, assertion, candidate = _field_change_candidate(session)

    apply_result = apply_field_change_candidate(session, candidate, reviewer="reviewer@example.test", reason="explicit correction")
    session.commit()

    session.refresh(signal)
    assert signal.target_year == 2031
    history = list_signal_amendments(session, signal.id)
    assert len(history) == 1
    assert history[0].field_changes[0].field_name == "target_year"
    assert apply_result.signal_id == signal.id


def test_apply_refuses_corroboration_only_classification():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    session.commit()
    candidate = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))
    assert candidate.classification == MaterialChangeClassification.CORROBORATION_ONLY

    with pytest.raises(UpdateReportApplyError):
        apply_field_change_candidate(session, candidate, reviewer="reviewer@example.test", reason="x")


def test_apply_refuses_unresolved_identity():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)
    session.commit()
    candidate = classify_candidate(
        session, CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2030}),
    )
    assert candidate.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE

    with pytest.raises(UpdateReportApplyError):
        apply_field_change_candidate(session, candidate, reviewer="reviewer@example.test", reason="x")


def test_apply_refuses_empty_proposed_changes():
    session = make_session()
    airport = make_airport(session)
    runway = make_runway(session, airport)
    make_signal(session, airport, runway=runway)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
    session.commit()
    candidate = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))

    with pytest.raises(UpdateReportApplyError):
        apply_field_change_candidate(session, candidate, reviewer="reviewer@example.test", reason="x")


def test_apply_never_touches_publication():
    session = make_session()
    signal, assertion, candidate = _field_change_candidate(session)
    signal.published = False
    session.commit()

    apply_field_change_candidate(session, candidate, reviewer="reviewer@example.test", reason="explicit correction")
    session.commit()

    session.refresh(signal)
    assert signal.published is False


def test_contradiction_classification_is_also_applicable():
    session = make_session()
    signal, assertion, candidate = _field_change_candidate(session, correction=True)
    assert candidate.classification == MaterialChangeClassification.CONTRADICTION_OR_CORRECTION

    apply_field_change_candidate(session, candidate, reviewer="reviewer@example.test", reason="explicit correction")
    session.commit()

    session.refresh(signal)
    assert signal.target_year == 2031


# --- REPORT ENGINE ------------------------------------------------------


def _watch_item(airport_id=1, signal_id=None, source_assertion_id=None, reason="X", detail="detail"):
    return WatchItem(
        airport_id=airport_id, signal_id=signal_id, source_assertion_id=source_assertion_id,
        watch_reason=reason, detail=detail,
    )


def _make_candidate(session, classification, **overrides):
    """Builds a real ChangeCandidateResult of the requested classification by
    driving classify_candidate() with the right fixture shape, rather than
    hand-constructing the dataclass (keeps the engine tests honest about
    what classify_candidate() actually produces)."""
    airport = make_airport(session, name=f"Airport {next(_assertion_seq)}", iata_code=None)
    source = make_source(session)
    if classification == MaterialChangeClassification.FIELD_CHANGE_CANDIDATE:
        runway = make_runway(session, airport)
        signal = make_signal(session, airport, runway=runway, target_year=2028)
        assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
        session.commit()
        return classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2031}))
    if classification == MaterialChangeClassification.CORROBORATION_ONLY:
        runway = make_runway(session, airport)
        make_signal(session, airport, runway=runway)
        assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
        session.commit()
        return classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))
    if classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE:
        assertion = make_assertion(session, source, airport_id=airport.id)
        session.commit()
        return classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))
    if classification == MaterialChangeClassification.DUPLICATE:
        assertion = make_assertion(session, source, airport_id=airport.id)
        session.commit()
        return classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id), is_duplicate_in_batch=True)
    if classification == MaterialChangeClassification.NO_MATERIAL_CHANGE:
        runway = make_runway(session, airport)
        make_signal(session, airport, runway=runway, target_year=2030)
        assertion = make_assertion(session, source, airport_id=airport.id, runway_id=runway.id)
        session.commit()
        return classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id, proposed_changes={"target_year": 2030}))
    raise AssertionError(classification)


def test_report_generation_produces_zero_writes():
    session = make_session()
    candidate = _make_candidate(session, MaterialChangeClassification.FIELD_CHANGE_CANDIDATE)
    before = (len(session.new), len(session.dirty), len(session.deleted))

    report = generate_update_report(watch_items=(), candidates=(candidate,))

    after = (len(session.new), len(session.dirty), len(session.deleted))
    assert before == (0, 0, 0)
    assert after == (0, 0, 0)
    assert report.material_changes == (candidate,)


def test_material_changes_grouped_correctly():
    session = make_session()
    field_change = _make_candidate(session, MaterialChangeClassification.FIELD_CHANGE_CANDIDATE)
    corroboration = _make_candidate(session, MaterialChangeClassification.CORROBORATION_ONLY)

    report = generate_update_report(watch_items=(), candidates=(field_change, corroboration))

    assert report.material_changes == (field_change,)
    assert corroboration not in report.material_changes


def test_needs_human_review_grouped_correctly():
    session = make_session()
    needs_evidence = _make_candidate(session, MaterialChangeClassification.NEEDS_MORE_EVIDENCE)
    corroboration = _make_candidate(session, MaterialChangeClassification.CORROBORATION_ONLY)

    report = generate_update_report(watch_items=(), candidates=(needs_evidence, corroboration))

    assert report.needs_human_review == (needs_evidence,)


def test_corroboration_grouped_correctly():
    session = make_session()
    corroboration = _make_candidate(session, MaterialChangeClassification.CORROBORATION_ONLY)
    duplicate = _make_candidate(session, MaterialChangeClassification.DUPLICATE)

    report = generate_update_report(watch_items=(), candidates=(corroboration, duplicate))

    assert report.corroboration_only == (corroboration,)


def test_no_material_change_grouped_correctly_includes_duplicate():
    session = make_session()
    duplicate = _make_candidate(session, MaterialChangeClassification.DUPLICATE)
    no_change = _make_candidate(session, MaterialChangeClassification.NO_MATERIAL_CHANGE)

    report = generate_update_report(watch_items=(), candidates=(duplicate, no_change))

    assert duplicate in report.no_material_change
    assert no_change in report.no_material_change
    assert len(report.no_material_change) == 2


def test_watch_next_grouped_for_unaddressed_watch_items():
    watch_item = _watch_item(airport_id=42, source_assertion_id=None, signal_id=7)

    report = generate_update_report(watch_items=(watch_item,), candidates=())

    assert len(report.watch_next) == 1
    assert report.watch_next[0].watch_item == watch_item
    assert report.watch_next[0].note == "no new evidence gathered this run"


def test_watch_next_uses_acquisition_note_when_supplied():
    watch_item = _watch_item(airport_id=42, signal_id=7)

    report = generate_update_report(
        watch_items=(watch_item,), candidates=(), acquisition_notes={42: "Cloudflare-blocked - could not verify"},
    )

    assert report.watch_next[0].note == "Cloudflare-blocked - could not verify"


def test_watch_item_addressed_by_a_candidate_is_not_in_watch_next():
    session = make_session()
    candidate = _make_candidate(session, MaterialChangeClassification.FIELD_CHANGE_CANDIDATE)
    watch_item = _watch_item(
        airport_id=candidate.airport_id, source_assertion_id=candidate.source_assertion_id,
    )

    report = generate_update_report(watch_items=(watch_item,), candidates=(candidate,))

    assert report.watch_next == ()


def test_report_ordering_is_deterministic():
    session = make_session()
    a = _make_candidate(session, MaterialChangeClassification.FIELD_CHANGE_CANDIDATE)
    b = _make_candidate(session, MaterialChangeClassification.FIELD_CHANGE_CANDIDATE)

    report_1 = generate_update_report(watch_items=(), candidates=(b, a))
    report_2 = generate_update_report(watch_items=(), candidates=(a, b))

    assert report_1.material_changes == report_2.material_changes
    assert render_report_text(report_1).split("\n", 1)[1:] == render_report_text(report_2).split("\n", 1)[1:]
