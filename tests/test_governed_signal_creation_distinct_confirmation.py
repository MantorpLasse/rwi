"""Tests for the R4C stale-safe CONFIRM_DISTINCT_SIGNAL integration into
app/services/governed_signal_creation.py::create_signal_from_approved_review()
(docs/architecture/existing-signal-reconciliation-r4c-stale-safe-creation-report.md,
R4C of docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md's
own S20 roadmap). Every test uses an isolated in-memory SQLite database.

Deliberately a separate file from tests/test_governed_signal_creation.py and
tests/test_governed_signal_creation_reconciliation.py - both files' own
pre-existing tests are the regression net proving this slice changed no
prior APPROVE_SIGNAL/CLEAR_TO_CREATE/POSSIBLE_EXISTING_SIGNAL_MATCH/
ALREADY_LINKED behavior; this file is additive, CONFIRM_DISTINCT_SIGNAL-
specific coverage only, mirroring the same "separate file = clean regression
signal" discipline every R4 slice in this project has used.
"""
from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    ReviewerAction,
    Runway,
    Signal,
    Source,
    SourceAssertion,
)
from app.services import governed_signal_creation as gsc
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationOutcome,
    evaluate_existing_signal_reconciliation,
)
from app.services.existing_signal_reconciliation_candidates import (
    build_reconciliation_subject,
    find_reconciliation_candidates,
)
from app.services.existing_signal_reconciliation_review import (
    build_reconciliation_review_plan,
    compute_reconciliation_fingerprint,
)
from app.services.governed_signal_creation import (
    ExistingSignalPossibleMatchError,
    StaleReconciliationConfirmationError,
    create_signal_from_approved_review,
)
from app.services.reviewer_action_persistence import get_latest_reviewer_action, record_reviewer_action

CLEAR = ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE
POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH
LINKED = ExistingSignalReconciliationOutcome.ALREADY_LINKED


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _airport(session, name="Test Airport", code="ZZZ") -> Airport:
    airport = Airport(name=name, iata_code=code, country="USA")
    session.add(airport)
    session.commit()
    return airport


def _runway(session, airport, designation="09/27") -> Runway:
    runway = Runway(airport_id=airport.id, designation=designation)
    session.add(runway)
    session.commit()
    return runway


def _source(session, title="Test source") -> Source:
    source = Source(title=title, source_type="web_discovery")
    session.add(source)
    session.commit()
    return source


def _signal(session, airport, **kwargs) -> Signal:
    kwargs.setdefault("title", "Existing signal")
    kwargs.setdefault("category", "replacement")
    kwargs.setdefault("confidence", "medium")
    signal = Signal(airport_id=airport.id, **kwargs)
    session.add(signal)
    session.commit()
    return signal


def _installation_identity(session, airport) -> PhysicalInstallationIdentity:
    identity = PhysicalInstallationIdentity(airport_id=airport.id)
    session.add(identity)
    session.commit()
    return identity


def _link(session, assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION", supersedes_link_id=None) -> InstallationAssertionLink:
    link = InstallationAssertionLink(
        assertion_id=assertion.id,
        physical_installation_id=identity.id if identity else None,
        outcome=outcome,
        reason="test fixture",
        actor="test",
        supersedes_link_id=supersedes_link_id,
    )
    session.add(link)
    session.commit()
    return link


def _governed_assertion(session, source, airport, *, approved=True, **kwargs) -> SourceAssertion:
    kwargs.setdefault("assertion_type", "project_construction")
    kwargs.setdefault("source_record_identifier", f"rec-{id(kwargs)}-{source.id}")
    kwargs.setdefault("identity_guard_decision", "ATTACH_CONFIRMED")
    kwargs.setdefault("intelligence_review_decision", "REVIEW_REQUIRED")
    kwargs.setdefault("promotion_policy_decision", "HUMAN_REVIEW_REQUIRED")
    assertion = SourceAssertion(source=source, airport=airport, **kwargs)
    session.add(assertion)
    session.commit()
    if approved:
        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="test approval", reviewer="tester@example.test",
        )
        session.commit()
    return assertion


_BASE_FIELDS = dict(title="Proposed signal", category="replacement", confidence="medium")


def _current_blocking_decision(session, assertion, *, claims=(), category="replacement", reference_year=None):
    """Mirrors exactly what create_signal_from_approved_review() computes
    internally - used by test fixtures to build a genuine human confirmation
    for the CURRENT state, never a fabricated one."""
    reconciliation_subject = build_reconciliation_subject(assertion, claims, category=category, reference_year=reference_year)
    reconciliation_candidates = find_reconciliation_candidates(session, assertion)
    decision = evaluate_existing_signal_reconciliation(reconciliation_subject, reconciliation_candidates)
    return reconciliation_subject, decision


def _confirm_current_state(session, assertion, *, claims=(), category="replacement", reference_year=None, reviewer="human:confirmer"):
    """Builds the genuine current R4A plan/fingerprint for `assertion` (which
    must currently be POSSIBLE_EXISTING_SIGNAL_MATCH) and records
    CONFIRM_DISTINCT_SIGNAL with it, superseding the current latest action.
    Returns the fingerprint recorded, so a test can later mutate state and
    confirm the SAME fingerprint no longer matches."""
    subject, decision = _current_blocking_decision(session, assertion, claims=claims, category=category, reference_year=reference_year)
    assert decision.outcome == POSSIBLE, f"fixture precondition failed: {decision.outcome!r}"
    plan = build_reconciliation_review_plan(source_assertion_id=assertion.id, subject=subject, decision=decision)
    fingerprint = compute_reconciliation_fingerprint(plan)
    latest = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="Reviewed; genuinely distinct project.",
        reviewer=reviewer, supersedes_action_id=latest.id, reconciliation_fingerprint=fingerprint,
    )
    session.commit()
    return fingerprint


# ---------------------------------------------------------------------------
# 1-2. Non-regression: APPROVE_SIGNAL path unchanged (light touch - the full
# regression net lives in test_governed_signal_creation_reconciliation.py).
# ---------------------------------------------------------------------------


def test_approve_signal_clear_to_create_still_creates():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport)

    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.created is True
    assert result.reconciliation_decision.outcome == CLEAR
    session.close(); engine.dispose()


def test_approve_signal_possible_match_still_blocks():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    with pytest.raises(ExistingSignalPossibleMatchError):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 3-4/13/23. Synthetic golden case: valid confirmation, exact fingerprint
# match, multiple-candidate full-set match, published=False.
# ---------------------------------------------------------------------------


def test_golden_case_valid_confirmation_of_current_state_creates_signal():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    existing = _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    # Retry the blocked attempt to confirm it is indeed POSSIBLE first.
    with pytest.raises(ExistingSignalPossibleMatchError):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)

    _confirm_current_state(session, assertion)

    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()

    assert result.created is True
    assert result.signal.published is False
    assert result.reconciliation_decision.outcome == POSSIBLE
    assert result.reconciliation_decision.candidate_signal_ids == (existing.id,)
    assert session.query(Signal).count() == 2
    session.close(); engine.dispose()


def test_golden_case_multiple_candidates_full_set_match_creates():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    signal_a = _signal(session, airport, runway_id=runway.id)

    identity = _installation_identity(session, airport)
    signal_b = _signal(session, airport)
    supporting_source = _source(session)
    supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=signal_b.id)
    _link(session, supporting_assertion, identity)

    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    _link(session, assertion, identity)

    _confirm_current_state(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()

    assert result.created is True
    assert result.reconciliation_decision.candidate_signal_ids == tuple(sorted([signal_a.id, signal_b.id]))
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 5-6. Stale: candidate added / removed after confirmation.
# ---------------------------------------------------------------------------


def test_stale_when_a_new_blocking_candidate_appears_after_confirmation():
    """Mission's central negative case: human confirms candidate #10;
    before retry, a second candidate becomes anchor-backed too."""
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    signal_10 = _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    fingerprint = _confirm_current_state(session, assertion)

    identity = _installation_identity(session, airport)
    signal_20 = _signal(session, airport)
    supporting_source = _source(session)
    supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=signal_20.id)
    _link(session, supporting_assertion, identity)
    _link(session, assertion, identity)  # assertion now also anchors to signal_20

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint
    assert excinfo.value.current_fingerprint != fingerprint
    assert set(excinfo.value.current_reconciliation_decision.candidate_signal_ids) == {signal_10.id, signal_20.id}
    assert session.query(Signal).count() == 2  # no new Signal
    session.close(); engine.dispose()


def test_stale_when_a_previously_blocking_candidate_is_removed():
    engine, session = make_session()
    airport = _airport(session)
    identity = _installation_identity(session, airport)
    signal_a = _signal(session, airport)
    supporting_source_a = _source(session)
    supporting_assertion_a = _governed_assertion(session, supporting_source_a, airport, approved=False, signal_id=signal_a.id)
    link_a = _link(session, supporting_assertion_a, identity)

    signal_b = _signal(session, airport, title="second")
    supporting_source_b = _source(session)
    supporting_assertion_b = _governed_assertion(session, supporting_source_b, airport, approved=False, signal_id=signal_b.id)
    _link(session, supporting_assertion_b, identity)

    source = _source(session)
    assertion = _governed_assertion(session, source, airport)
    _link(session, assertion, identity)

    fingerprint = _confirm_current_state(session, assertion)

    # Retract signal_a's own link - it no longer anchors, shrinking the
    # blocking set from {signal_a, signal_b} to {signal_b} only.
    _link(session, supporting_assertion_a, identity=None, outcome="UNRESOLVED", supersedes_link_id=link_a.id)

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint
    assert excinfo.value.current_reconciliation_decision.candidate_signal_ids == (signal_b.id,)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 7. Stale: same candidate, anchor reason changed (runway_id -> provenance).
# ---------------------------------------------------------------------------


def test_stale_when_anchor_reason_changes_for_the_same_candidate():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    existing = _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    fingerprint = _confirm_current_state(session, assertion)

    # Change the SAME candidate's anchor reason: drop the runway match, add a
    # provenance anchor instead (same source document already supports it).
    # candidate_signal_ids stays == (existing.id,) but anchor_reasons differs.
    supporting_assertion = SourceAssertion(
        source_id=assertion.source_id, airport=airport, assertion_type="project_construction",
        source_record_identifier="supporting-doc", signal_id=existing.id,
        identity_guard_decision="ATTACH_CONFIRMED", intelligence_review_decision="REVIEW_REQUIRED",
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )
    session.add(supporting_assertion)
    assertion.runway_id = None  # remove the runway anchor
    session.commit()

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint
    assert excinfo.value.current_reconciliation_decision.candidate_signal_ids == (existing.id,)
    assert any("provenance" in r for r in excinfo.value.current_reconciliation_decision.reasons)
    assert not any("runway_id" in r for r in excinfo.value.current_reconciliation_decision.reasons)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 8. Stale: subject structural identity changed after confirmation.
# ---------------------------------------------------------------------------


def test_stale_when_subject_runway_id_changes_after_confirmation():
    engine, session = make_session()
    airport = _airport(session)
    runway_a = _runway(session, airport, designation="09/27")
    runway_b = _runway(session, airport, designation="04/22")
    _signal(session, airport, runway_id=runway_a.id)
    _signal(session, airport, runway_id=runway_b.id, title="other")
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway_a.id)

    fingerprint = _confirm_current_state(session, assertion)

    # The evidence's own resolved runway changes - now anchors to a
    # DIFFERENT existing Signal than the one originally reviewed.
    assertion.runway_id = runway_b.id
    session.commit()

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint
    session.close(); engine.dispose()


def test_stale_when_subject_physical_installation_ids_change_after_confirmation():
    """Review-checkpoint addition: the mission explicitly lists
    physical_installation_ids as one of the subject-identity fields that
    must invalidate a stored fingerprint if it changes - not exercised by
    any pre-existing test in this file, which only covered runway_id."""
    engine, session = make_session()
    airport = _airport(session)
    identity_a = _installation_identity(session, airport)
    identity_b = _installation_identity(session, airport)
    signal_via_a = _signal(session, airport)
    supporting_source = _source(session)
    supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=signal_via_a.id)
    _link(session, supporting_assertion, identity_a)
    # A second, otherwise-uninvolved Signal anchored via identity_b - present
    # from the start so the fresh candidate set after the mutation below is
    # non-empty and still POSSIBLE_EXISTING_SIGNAL_MATCH, not CLEAR_TO_CREATE
    # (which is a different, separately-tested scenario).
    signal_via_b = _signal(session, airport, title="other")
    supporting_source_b = _source(session)
    supporting_assertion_b = _governed_assertion(session, supporting_source_b, airport, approved=False, signal_id=signal_via_b.id)
    _link(session, supporting_assertion_b, identity_b)

    source = _source(session)
    assertion = _governed_assertion(session, source, airport)
    original_link = _link(session, assertion, identity_a)

    fingerprint = _confirm_current_state(session, assertion)

    # The evidence's own physical-installation identity link is retracted
    # and replaced with a DIFFERENT identity - anchors to a different Signal.
    _link(session, assertion, identity=identity_b, supersedes_link_id=original_link.id)

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint
    assert excinfo.value.current_reconciliation_decision.candidate_signal_ids == (signal_via_b.id,)
    session.close(); engine.dispose()


def test_stale_when_subject_source_provenance_changes_after_confirmation():
    """Review-checkpoint addition: subject.source_id/artifact_identity are
    also part of the R4A plan (subject_source_id/subject_artifact_identity)
    but were not exercised by any pre-existing test in this file.

    A second, independently-anchored (via runway) candidate is kept
    constant throughout so the outcome stays POSSIBLE_EXISTING_SIGNAL_MATCH
    both before and after the mutation - proving the fingerprint changed
    because the provenance-anchored candidate's membership changed, not
    because the whole decision degenerated into CLEAR_TO_CREATE (a
    different, separately-tested scenario)."""
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    stable_signal = _signal(session, airport, runway_id=runway.id, title="stable anchor")

    governed_source = _source(session, title="Governed supporting doc")
    existing = _signal(session, airport, title="provenance anchor")
    _governed_assertion(session, governed_source, airport, approved=False, signal_id=existing.id)

    assertion = _governed_assertion(session, governed_source, airport, runway_id=runway.id)
    fingerprint = _confirm_current_state(session, assertion)

    # Retarget the assertion's own source away from the governed document -
    # the provenance anchor to `existing` disappears; `stable_signal` still
    # blocks via the unrelated, unchanged runway anchor.
    unrelated_source = _source(session, title="Unrelated document")
    assertion.source_id = unrelated_source.id
    session.commit()

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint
    assert excinfo.value.current_reconciliation_decision.candidate_signal_ids == (stable_signal.id,)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Review-checkpoint addition (mission Section 14): the central, explicitly-
# flagged design edge - a human confirms a blocking plan, then the anchor
# disappears entirely and fresh reconciliation returns CLEAR_TO_CREATE. Per
# the R4 design doc's own Section 14 step 6 ("the stored confirmation
# becomes moot, not consulted"), creation must proceed via the ordinary
# unblocked path - not raise StaleReconciliationConfirmationError.
# ---------------------------------------------------------------------------


def test_clear_to_create_after_confirmation_is_moot_but_creation_still_proceeds():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    _confirm_current_state(session, assertion)

    # The anchor disappears entirely - no replacement anchor, and the
    # existing candidate shares no compatibility axis either, so fresh
    # reconciliation must now return CLEAR_TO_CREATE with no metadata at all.
    assertion.runway_id = None
    session.commit()

    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.created is True
    assert result.reconciliation_decision.outcome == CLEAR
    assert result.reconciliation_decision.candidate_signal_ids == ()
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Review-checkpoint addition (mission Section 13): a malformed/None stored
# fingerprint on the latest CONFIRM_DISTINCT_SIGNAL row (constructed via
# direct ORM, bypassing record_reviewer_action()'s own R4B validation - the
# same "belt-and-suspenders" bypass tests/test_reviewer_action_confirm_distinct_signal.py
# already uses) must fail closed as stale, not crash or silently authorize.
# ---------------------------------------------------------------------------


def test_malformed_stored_fingerprint_fails_closed_as_stale():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    latest = get_latest_reviewer_action(session, assertion.id)
    # Direct ORM construction bypasses record_reviewer_action()'s own R4B
    # fingerprint-shape validation entirely - the DB CHECK constraint only
    # enforces NULL-pairing with the action, never the 64-hex-char shape
    # (see docs/architecture/existing-signal-reconciliation-r4b-reviewer-
    # action-report.md's own documented trust boundary).
    malformed = ReviewerAction(
        source_assertion_id=assertion.id, action="CONFIRM_DISTINCT_SIGNAL",
        reason="x", reviewer="human:z", supersedes_action_id=latest.id,
        reconciliation_fingerprint="not-a-real-fingerprint",
    )
    session.add(malformed)
    session.commit()

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == "not-a-real-fingerprint"
    assert session.query(Signal).count() == 1  # no new Signal
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# Review-checkpoint addition (mission Section 29): the pre-existing R3 drift
# check (source_assertion.signal_id already set, but the requested Signal
# fields differ from the linked Signal's own) must still fire even when the
# latest action is CONFIRM_DISTINCT_SIGNAL, not just APPROVE_SIGNAL -
# ALREADY_LINKED must not be treated as "nothing left to check."
# ---------------------------------------------------------------------------


def test_already_linked_drift_check_still_fires_with_latest_confirm_distinct_signal():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    linked_signal = _signal(session, airport, title="linked")
    _signal(session, airport, runway_id=runway.id, title="unrelated anchor-backed")

    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id, signal_id=linked_signal.id)
    latest = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
        supersedes_action_id=latest.id, reconciliation_fingerprint="a" * 64,
    )
    session.commit()

    with pytest.raises(ValueError) as excinfo:
        create_signal_from_approved_review(
            session, assertion, title="DIFFERENT TITLE", category="replacement", confidence="high",
        )
    assert not isinstance(excinfo.value, (ExistingSignalPossibleMatchError, StaleReconciliationConfirmationError))
    assert "different core fields" in str(excinfo.value)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 9. Stale: fingerprint copied from another SourceAssertion (R4B ownership
# boundary attack, resolved here at R4C).
# ---------------------------------------------------------------------------


def test_stale_when_fingerprint_copied_from_another_assertion():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)

    source_a = _source(session)
    assertion_a = _governed_assertion(session, source_a, airport, runway_id=runway.id)
    fingerprint_from_a = _confirm_current_state(session, assertion_a)

    # A second, distinct SourceAssertion reaches the exact same blocking
    # state (same airport, same runway) - its own genuine fingerprint would
    # legitimately differ only by source_assertion_id, but here we simulate
    # a human/tooling error: assertion B's confirmation carries A's
    # fingerprint verbatim, never recomputed for B itself.
    source_b = _source(session)
    assertion_b = _governed_assertion(session, source_b, airport, runway_id=runway.id)
    latest_b = get_latest_reviewer_action(session, assertion_b.id)
    record_reviewer_action(
        session, assertion_b, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
        supersedes_action_id=latest_b.id, reconciliation_fingerprint=fingerprint_from_a,
    )
    session.commit()

    with pytest.raises(StaleReconciliationConfirmationError) as excinfo:
        create_signal_from_approved_review(session, assertion_b, **_BASE_FIELDS)
    assert excinfo.value.stored_fingerprint == fingerprint_from_a
    assert excinfo.value.current_fingerprint != fingerprint_from_a
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 10. Advisory churn: blocking set/anchors unchanged, only compatibility
# metadata on OTHER, non-blocking candidates changed - confirmation stays
# valid.
# ---------------------------------------------------------------------------


def test_advisory_only_churn_does_not_invalidate_confirmation():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    anchor_signal = _signal(session, airport, runway_id=runway.id)
    compatibility_signal = _signal(session, airport, category="replacement", confirmed_vendor="Acme")
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    fingerprint = _confirm_current_state(session, assertion)

    # Advisory churn only: a NEW compatibility-only signal appears (matches
    # category, never an anchor) - candidate_signal_ids/anchor_reasons for
    # the actual blocking candidate are unaffected.
    _signal(session, airport, category="replacement", confirmed_vendor="Beta")

    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.created is True
    assert result.reconciliation_decision.candidate_signal_ids == (anchor_signal.id,)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 11-12. Candidate ordering / duplicate-entry ordering invariance, proven at
# the R4A layer and then confirmed accepted through the real R4C integration
# point (find_reconciliation_candidates always returns Signal.id-ascending
# order in practice, so ordering is only exercisable directly against R4A's
# own pure functions; acceptance through create_signal_from_approved_review()
# is what "regression-tested through R4C" means here).
# ---------------------------------------------------------------------------


def test_candidate_and_reason_ordering_does_not_change_the_fingerprint():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    signal_a = _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    subject, decision = _current_blocking_decision(session, assertion)

    reordered_decision = decision.__class__(
        outcome=decision.outcome,
        candidate_signal_ids=tuple(reversed(decision.candidate_signal_ids)),
        reasons=tuple(reversed(decision.reasons)),
    )
    duplicated_decision = decision.__class__(
        outcome=decision.outcome,
        candidate_signal_ids=decision.candidate_signal_ids + decision.candidate_signal_ids,
        reasons=decision.reasons + decision.reasons,
    )

    plan_original = build_reconciliation_review_plan(source_assertion_id=assertion.id, subject=subject, decision=decision)
    plan_reordered = build_reconciliation_review_plan(source_assertion_id=assertion.id, subject=subject, decision=reordered_decision)
    plan_duplicated = build_reconciliation_review_plan(source_assertion_id=assertion.id, subject=subject, decision=duplicated_decision)

    fp_original = compute_reconciliation_fingerprint(plan_original)
    assert compute_reconciliation_fingerprint(plan_reordered) == fp_original
    assert compute_reconciliation_fingerprint(plan_duplicated) == fp_original

    # And that fingerprint, however it was computed, is accepted through the
    # real integration point against the real (ascending-order) DB query.
    latest = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
        supersedes_action_id=latest.id, reconciliation_fingerprint=fp_original,
    )
    session.commit()
    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.created is True
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 14-18. Supersession: latest action controls, no timeless override.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action,extra", [("DEFER", {}), ("NEEDS_MORE_EVIDENCE", {}), ("REJECT_SIGNAL", {})])
def test_later_action_supersedes_a_valid_confirmation_and_blocks(action, extra):
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    _confirm_current_state(session, assertion)  # would authorize creation if it were latest
    confirm = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action=action, reason="Changed my mind.", reviewer="human:z",
        supersedes_action_id=confirm.id, **extra,
    )
    session.commit()

    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert session.query(Signal).count() == 1  # only the pre-existing candidate
    session.close(); engine.dispose()


def test_later_mark_duplicate_supersedes_a_valid_confirmation_and_blocks_creation():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    existing = _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    _confirm_current_state(session, assertion)
    confirm = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="Actually, it is a duplicate.", reviewer="human:z",
        supersedes_action_id=confirm.id, duplicate_of_signal_id=existing.id,
    )
    session.commit()

    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


def test_historical_confirmation_ignored_when_a_later_action_is_latest():
    """No 'find most recent confirmation' shortcut independent of latest
    action - an old, perfectly-matching CONFIRM_DISTINCT_SIGNAL earlier in
    history must not be consulted once a later action supersedes it."""
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

    _confirm_current_state(session, assertion)  # historically valid, matching
    confirm = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="DEFER", reason="Want a second look anyway.", reviewer="human:z",
        supersedes_action_id=confirm.id,
    )
    session.commit()

    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 19-20. ALREADY_LINKED safety.
# ---------------------------------------------------------------------------


def test_already_linked_prevents_second_creation_even_with_latest_confirm_distinct_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport)
    first = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert assertion.signal_id == first.signal.id

    # A CONFIRM_DISTINCT_SIGNAL recorded after the assertion is already
    # linked - structurally odd, but must not be able to force a second
    # Signal into existence.
    latest = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
        supersedes_action_id=latest.id, reconciliation_fingerprint="a" * 64,
    )
    session.commit()

    second = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert second.created is False
    assert second.signal.id == first.signal.id
    assert second.reconciliation_decision.outcome == LINKED
    assert session.query(Signal).count() == 1
    session.close(); engine.dispose()


def test_dangling_signal_id_fails_closed_with_latest_confirm_distinct_signal():
    engine, session = make_session()
    airport = _airport(session)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport)
    assertion.signal_id = 999999
    session.commit()
    latest = get_latest_reviewer_action(session, assertion.id)
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
        supersedes_action_id=latest.id, reconciliation_fingerprint="a" * 64,
    )
    session.commit()

    with pytest.raises(ValueError, match="no longer exists"):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 21/25. Governance checked before reconciliation/fingerprint; error
# ordering; malformed upstream state pre-empts fingerprint comparison.
# ---------------------------------------------------------------------------


def test_upstream_governance_failure_precedes_fingerprint_comparison():
    """A CONFIRM_DISTINCT_SIGNAL cannot be recorded in the first place
    against an already-malformed governance state - record_reviewer_action()'s
    own gate (R4B) already refuses that. The realistic way this ordering
    question arises is a valid confirmation recorded while governance was
    still sound, with the governance state later degraded (e.g. a later,
    separate re-review) - create_signal_from_approved_review() must still
    fail on governance FIRST, never reaching reconciliation or fingerprint
    comparison at all."""
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    _confirm_current_state(session, assertion)  # valid at the time it was recorded

    assertion.identity_guard_decision = "ATTACH_PROVISIONAL"
    session.commit()

    with pytest.raises(ValueError) as excinfo:
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert not isinstance(excinfo.value, (ExistingSignalPossibleMatchError, StaleReconciliationConfirmationError))
    assert "identity_guard_decision" in str(excinfo.value)
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 24. No commit; 23. Rollback; 25. No ReviewerAction mutation.
# ---------------------------------------------------------------------------


def test_service_still_never_calls_session_commit():
    source_text = inspect.getsource(gsc)
    tree = ast.parse(source_text)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "session"
        ):
            raise AssertionError("forbidden session.commit() call found")


def test_valid_confirmation_creation_rolled_back_leaves_no_signal():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    _confirm_current_state(session, assertion)

    create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.rollback()
    assert session.query(Signal).count() == 1  # only the pre-existing candidate
    session.close(); engine.dispose()


def test_stale_confirmation_leaves_no_pending_signal_and_no_reviewer_action_mutation():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    fingerprint = _confirm_current_state(session, assertion)
    stored_action_count_before = session.query(ReviewerAction).count()
    confirm_row = get_latest_reviewer_action(session, assertion.id)
    confirm_snapshot = (confirm_row.action, confirm_row.reconciliation_fingerprint, confirm_row.reason, confirm_row.reviewer)

    identity = _installation_identity(session, airport)
    other_signal = _signal(session, airport)
    supporting_source = _source(session)
    supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=other_signal.id)
    _link(session, supporting_assertion, identity)
    _link(session, assertion, identity)

    with pytest.raises(StaleReconciliationConfirmationError):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)

    assert session.new == set() or len(session.new) == 0
    assert session.query(ReviewerAction).count() == stored_action_count_before
    reloaded = get_latest_reviewer_action(session, assertion.id)
    assert (reloaded.action, reloaded.reconciliation_fingerprint, reloaded.reason, reloaded.reviewer) == confirm_snapshot
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 22. published=False preserved on the confirmed-distinct creation path.
# ---------------------------------------------------------------------------


def test_confirmed_distinct_creation_is_still_unpublished():
    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    _signal(session, airport, runway_id=runway.id)
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    _confirm_current_state(session, assertion)

    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.signal.published is False
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 26. Money/title irrelevant to fingerprint validity.
# ---------------------------------------------------------------------------


def test_financial_and_title_changes_never_invalidate_a_confirmation():
    from decimal import Decimal

    engine, session = make_session()
    airport = _airport(session)
    runway = _runway(session, airport)
    existing = _signal(session, airport, runway_id=runway.id, estimated_total_value_usd=Decimal("1000.00"))
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    _confirm_current_state(session, assertion)

    # Change money and title on the existing candidate after confirmation -
    # neither is part of R1/R4A's dataclasses, so neither can affect the
    # fingerprint at all.
    existing.estimated_total_value_usd = Decimal("99999999.99")
    existing.title = "A completely different title now"
    session.commit()

    result = create_signal_from_approved_review(
        session, assertion, title="Also a totally different proposed title", category="replacement", confidence="high",
    )
    session.commit()
    assert result.created is True
    session.close(); engine.dispose()


def test_r4c_fingerprint_calls_never_pass_financial_or_title_fields():
    """AST-level firewall, extended to the two new R4A call sites this slice
    adds - mirrors test_no_financial_field_accepted_by_create_call and
    test_reconciliation_gate_never_reads_title_or_notes_fields in
    tests/test_governed_signal_creation_reconciliation.py, which only cover
    the R3 call sites, not R4C's new ones."""
    source_text = inspect.getsource(gsc)
    tree = ast.parse(source_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
            "build_reconciliation_review_plan", "compute_reconciliation_fingerprint",
        ):
            call_source = ast.get_source_segment(source_text, node) or ""
            for forbidden in ("estimated_total_value_usd", "estimated_emas_value_usd", "title", "notes", "source_notes"):
                assert forbidden not in call_source, f"forbidden token {forbidden!r} found in R4C fingerprint call"


# ---------------------------------------------------------------------------
# 27. Provider agnostic - the R4C-added code itself never names a source
# family, mirroring TestProviderAgnostic in
# tests/test_governed_signal_creation_reconciliation.py but scoped to the
# StaleReconciliationConfirmationError class and the CONFIRM_DISTINCT_SIGNAL
# branch specifically.
# ---------------------------------------------------------------------------


def test_stale_error_and_confirm_branch_never_name_a_source_family():
    source_text = inspect.getsource(gsc)
    start = source_text.index("class StaleReconciliationConfirmationError")
    end = source_text.index("def create_signal_from_approved_review")
    new_code = source_text[start:end]
    for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
        assert token not in new_code, f"forbidden token found in R4C code: {token!r}"


# ---------------------------------------------------------------------------
# 28. International synthetic case.
# ---------------------------------------------------------------------------


def test_non_us_airport_confirmed_distinct_creation():
    engine, session = make_session()
    airport = _airport(session, name="Haneda Airport", code="HND")
    runway = _runway(session, airport, designation="05/23")
    _signal(session, airport, runway_id=runway.id, confirmed_vendor="Taiyo Safety Materials KK")
    source = _source(session)
    assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
    _confirm_current_state(session, assertion)

    result = create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    session.commit()
    assert result.created is True
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# 29-30. MSP regression - synthetic reproductions only, never the real DB.
# ---------------------------------------------------------------------------


def test_msp_historical_pre_resolution_shape_still_creates_via_r4c_modified_code():
    """The pre-R4B/R4C historical shape (latest APPROVE_SIGNAL, no anchor,
    CLEAR_TO_CREATE) must remain exactly as under R3 - a light regression
    touchpoint through the now-R4C-modified function; the full case lives in
    TestMSPHistoricalPreResolution (tests/test_governed_signal_creation_reconciliation.py),
    unmodified by this slice."""
    engine, session = make_session()
    airport = _airport(session, name="Minneapolis St. Paul International", code="MSP")
    source = _source(session, title="Procurement memo")
    assertion = _governed_assertion(session, source, airport)

    result = create_signal_from_approved_review(
        session, assertion, title="MSP Runway 30L EMAS replacement", category="replacement", confidence="medium",
    )
    session.commit()
    assert result.created is True
    assert result.reconciliation_decision.outcome == CLEAR
    session.close(); engine.dispose()


def test_msp_current_real_shape_latest_mark_duplicate_signal_67_no_creation_path():
    """Reproduces the REAL current MSP SourceAssertion #222 shape (verified
    read-only against data/runway_safe.db, never written to): latest
    ReviewerAction is MARK_DUPLICATE -> Signal #67, source_assertion.signal_id
    already = that Signal. No CONFIRM_DISTINCT_SIGNAL was ever recorded in
    reality (R4B/R4C never wrote to the real database). Expected: no
    creation path opens up - MARK_DUPLICATE is not in
    VALID_LATEST_ACTIONS_FOR_CREATION, so this fails exactly as it did
    before R4C, and R1's read-only re-evaluation (if it were ever reached)
    would independently confirm ALREADY_LINKED(67) via source_assertion.signal_id."""
    engine, session = make_session()
    airport = _airport(session, name="Minneapolis St. Paul International", code="MSP")
    signal_67 = _signal(
        session, airport, title="EMAS order (vendor confirmed)", category="replacement",
        confidence="high", confirmed_vendor="Runway Safe",
    )
    source = _source(session, title="EMAS Procurement Advance Deposit memo")
    assertion = _governed_assertion(session, source, airport, source_record_identifier="msp-222", approved=False)
    approve = record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:a")
    session.commit()
    record_reviewer_action(
        session, assertion, action="MARK_DUPLICATE", reason="Corroborates existing signal.",
        reviewer="human:b", supersedes_action_id=approve.id, duplicate_of_signal_id=signal_67.id,
    )
    session.commit()
    assertion.signal_id = signal_67.id
    session.commit()

    with pytest.raises(ValueError, match="latest ReviewerAction"):
        create_signal_from_approved_review(session, assertion, **_BASE_FIELDS)
    assert session.query(Signal).count() == 1  # only signal_67 - no creation path
    session.close(); engine.dispose()
