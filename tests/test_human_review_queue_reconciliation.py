"""Tests for app.services.human_review_reconciliation and the R4D extension
of scripts/list_human_review_queue.py
(docs/architecture/existing-signal-reconciliation-r4d-review-queue-report.md,
R4D of docs/architecture/existing-signal-reconciliation-r4-human-resolution-
design.md's own S20 roadmap).

Deliberately a separate file from tests/test_human_review_queue.py - that
file's own 74 pre-existing tests are the regression net proving this slice
changed no prior Slice 8/9D behavior; this file is additive,
reconciliation-specific coverage only. Every test uses an isolated,
disposable SQLite database - the real data/runway_safe.db is never opened.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - registers all metadata
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
from app.services import human_review_reconciliation as hrr
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
from app.services.governed_signal_creation import VALID_LATEST_ACTIONS_FOR_CREATION
from app.services.human_review_queue import list_review_workflow_items
from app.services.human_review_reconciliation import (
    ReconciliationReviewItem,
    ReconciliationReviewState,
    list_reconciliation_review_items,
)
from app.services.reviewer_action_persistence import get_latest_reviewer_action, record_reviewer_action
from scripts import list_human_review_queue as cli
from scripts.migrate_reconciliation_confirmation_slice_r4b import downgrade as downgrade_r4b_migration

CLEAR = ExistingSignalReconciliationOutcome.CLEAR_TO_CREATE
POSSIBLE = ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _full_schema_database(tmp_path: Path, name: str = "full.db") -> Path:
    db = tmp_path / name
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return db


@pytest.fixture()
def session(tmp_path):
    db = _full_schema_database(tmp_path)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s
    engine.dispose()


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


def _link(session, assertion, identity, outcome="SAME_PHYSICAL_INSTALLATION", supersedes_link_id=None):
    link = InstallationAssertionLink(
        assertion_id=assertion.id,
        physical_installation_id=identity.id if identity else None,
        outcome=outcome, reason="test fixture", actor="test", supersedes_link_id=supersedes_link_id,
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


def _confirm_current_state(session, assertion, *, reviewer="human:confirmer"):
    """Builds the genuine current R4A plan/fingerprint for `assertion`
    (which must currently be POSSIBLE_EXISTING_SIGNAL_MATCH) and records
    CONFIRM_DISTINCT_SIGNAL with it, superseding the current latest action."""
    subject = build_reconciliation_subject(assertion, (), category=None, reference_year=None)
    candidates = find_reconciliation_candidates(session, assertion)
    decision = evaluate_existing_signal_reconciliation(subject, candidates)
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


def _only_item(session):
    items = list_reconciliation_review_items(session)
    assert len(items) == 1
    return items[0]


# ---------------------------------------------------------------------------
# 1-4. APPROVE + blocking match: candidate IDs, anchor reasons, fingerprint.
# ---------------------------------------------------------------------------


class TestBlockingReviewRequired:
    def test_approve_plus_anchor_is_reconciliation_review_required(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)

        entry = _only_item(session)
        assert entry.reconciliation_outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH"
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert entry.reconciliation_candidate_signal_ids == (existing.id,)
        assert any("identity_anchor:runway_id" in r for r in entry.reconciliation_anchor_reasons)
        assert entry.reconciliation_fingerprint is not None
        assert len(entry.reconciliation_fingerprint) == 64
        assert entry.stored_reconciliation_fingerprint is None  # no confirmation recorded yet
        assert entry.item.source_assertion_id == assertion.id


# ---------------------------------------------------------------------------
# 5. APPROVE + CLEAR is not blocked.
# ---------------------------------------------------------------------------


class TestClearNotBlocked:
    def test_approve_plus_no_anchor_is_clear(self, session):
        airport = _airport(session)
        source = _source(session)
        _governed_assertion(session, source, airport)

        entry = _only_item(session)
        assert entry.reconciliation_outcome == "CLEAR_TO_CREATE"
        assert entry.reconciliation_review_state is None
        assert entry.reconciliation_candidate_signal_ids == ()
        assert entry.reconciliation_anchor_reasons == ()
        assert entry.reconciliation_fingerprint is None


# ---------------------------------------------------------------------------
# 6. Advisory/compatibility evidence never blocks - structural proof.
# ---------------------------------------------------------------------------


class TestAdvisoryNeverBlocks:
    def test_compatibility_shaped_existing_signal_never_appears_as_blocking(self, session):
        """This module deliberately never passes category/claims/reference_year
        (the human-selected-context boundary - see the module's own
        docstring) - so no compatibility axis can ever fire in its own
        subject construction, and a Signal that would only ever be
        compatibility-matched (never anchor-matched) never appears in
        reconciliation_candidate_signal_ids/reconciliation_anchor_reasons at
        all, regardless of how similar its category happens to be."""
        airport = _airport(session)
        _signal(session, airport, category="replacement", confirmed_vendor="Acme EMAS")
        source = _source(session)
        _governed_assertion(session, source, airport)

        entry = _only_item(session)
        assert entry.reconciliation_outcome == "CLEAR_TO_CREATE"
        assert entry.reconciliation_candidate_signal_ids == ()
        assert entry.reconciliation_anchor_reasons == ()


# ---------------------------------------------------------------------------
# 7. Valid, current distinct confirmation.
# ---------------------------------------------------------------------------


class TestDistinctConfirmedCurrent:
    def test_matching_confirmation_is_distinct_confirmed_pending_signal(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        fingerprint = _confirm_current_state(session, assertion)

        entry = _only_item(session)
        assert entry.reconciliation_review_state == ReconciliationReviewState.DISTINCT_CONFIRMED_PENDING_SIGNAL.value
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_candidate_signal_ids == (existing.id,)
        assert entry.reconciliation_warnings == ()
        assert entry.item.linked_signal_id is None  # no Signal created yet


# ---------------------------------------------------------------------------
# 8-11. Stale confirmation: general, candidate added, candidate removed,
# anchor changed.
# ---------------------------------------------------------------------------


class TestStaleConfirmation:
    def test_stale_confirmation_is_reconciliation_review_required_with_warning(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        fingerprint = _confirm_current_state(session, assertion)

        identity = _installation_identity(session, airport)
        signal_b = _signal(session, airport, title="second")
        supporting_source = _source(session)
        supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=signal_b.id)
        _link(session, supporting_assertion, identity)
        _link(session, assertion, identity)  # a new candidate becomes anchor-backed too

        entry = _only_item(session)
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint != fingerprint
        assert len(entry.reconciliation_warnings) == 1
        assert "STALE_RECONCILIATION_CONFIRMATION" in entry.reconciliation_warnings[0]
        assert str(signal_b.id) in entry.reconciliation_warnings[0]

    def test_candidate_removed_is_stale(self, session):
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

        _link(session, supporting_assertion_a, identity=None, outcome="UNRESOLVED", supersedes_link_id=link_a.id)

        entry = _only_item(session)
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert entry.reconciliation_candidate_signal_ids == (signal_b.id,)
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint != fingerprint

    def test_anchor_reason_change_is_stale(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        fingerprint = _confirm_current_state(session, assertion)

        supporting_assertion = SourceAssertion(
            source_id=assertion.source_id, airport=airport, assertion_type="project_construction",
            source_record_identifier="supporting-doc", signal_id=existing.id,
            identity_guard_decision="ATTACH_CONFIRMED", intelligence_review_decision="REVIEW_REQUIRED",
            promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
        )
        session.add(supporting_assertion)
        assertion.runway_id = None
        session.commit()

        entry = _only_item(session)
        assert entry.reconciliation_candidate_signal_ids == (existing.id,)
        assert any("provenance" in r for r in entry.reconciliation_anchor_reasons)
        assert not any("runway_id" in r for r in entry.reconciliation_anchor_reasons)
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint != fingerprint
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value

    def test_subject_runway_change_to_a_different_candidate_is_stale(self, session):
        """Review-checkpoint addition: mission Section 7 attack D - distinct
        from the anchor-reason-change test above, which keeps the SAME
        candidate but changes why it anchors. Here the subject's own
        runway_id changes to point at a DIFFERENT existing Signal entirely."""
        airport = _airport(session)
        runway_a = _runway(session, airport, designation="09/27")
        runway_b = _runway(session, airport, designation="04/22")
        _signal(session, airport, runway_id=runway_a.id)
        _signal(session, airport, runway_id=runway_b.id, title="other")
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway_a.id)
        fingerprint = _confirm_current_state(session, assertion)

        assertion.runway_id = runway_b.id
        session.commit()

        entry = _only_item(session)
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint != fingerprint
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value

    def test_subject_physical_installation_identity_change_is_stale(self, session):
        """Mission Section 7 attack E. A second, independently runway-
        anchored candidate is kept constant throughout so the outcome stays
        POSSIBLE both before and after the mutation - isolating the
        physical-installation-identity change as the only variable."""
        airport = _airport(session)
        runway = _runway(session, airport)
        stable_signal = _signal(session, airport, runway_id=runway.id, title="stable anchor")
        identity_a = _installation_identity(session, airport)
        identity_b = _installation_identity(session, airport)
        signal_via_a = _signal(session, airport, title="via identity a")
        supporting_source_a = _source(session)
        supporting_assertion_a = _governed_assertion(session, supporting_source_a, airport, approved=False, signal_id=signal_via_a.id)
        _link(session, supporting_assertion_a, identity_a)
        signal_via_b = _signal(session, airport, title="via identity b")
        supporting_source_b = _source(session)
        supporting_assertion_b = _governed_assertion(session, supporting_source_b, airport, approved=False, signal_id=signal_via_b.id)
        _link(session, supporting_assertion_b, identity_b)

        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        original_link = _link(session, assertion, identity_a)
        fingerprint = _confirm_current_state(session, assertion)

        _link(session, assertion, identity=identity_b, supersedes_link_id=original_link.id)

        entry = _only_item(session)
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint != fingerprint
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert set(entry.reconciliation_candidate_signal_ids) == {stable_signal.id, signal_via_b.id}

    def test_subject_governed_provenance_change_is_stale(self, session):
        """Mission Section 7 attack F. A second, independently runway-
        anchored candidate is kept constant throughout for the identical
        isolation reason as the physical-installation test above."""
        airport = _airport(session)
        runway = _runway(session, airport)
        stable_signal = _signal(session, airport, runway_id=runway.id, title="stable anchor")

        governed_source = _source(session, title="Governed supporting doc")
        existing = _signal(session, airport, title="provenance anchor")
        _governed_assertion(session, governed_source, airport, approved=False, signal_id=existing.id)

        assertion = _governed_assertion(session, governed_source, airport, runway_id=runway.id)
        fingerprint = _confirm_current_state(session, assertion)

        unrelated_source = _source(session, title="Unrelated document")
        assertion.source_id = unrelated_source.id
        session.commit()

        entry = _only_item(session)
        assert entry.stored_reconciliation_fingerprint == fingerprint
        assert entry.reconciliation_fingerprint != fingerprint
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert entry.reconciliation_candidate_signal_ids == (stable_signal.id,)


# ---------------------------------------------------------------------------
# 12. Unrelated data churn does not invalidate a confirmation.
# ---------------------------------------------------------------------------


class TestUnrelatedChurnNotStale:
    def test_unrelated_signal_field_changes_do_not_invalidate_confirmation(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        fingerprint = _confirm_current_state(session, assertion)

        existing.title = "A completely different title now"
        existing.confirmed_vendor = "Some Other Vendor"
        session.commit()

        entry = _only_item(session)
        assert entry.reconciliation_review_state == ReconciliationReviewState.DISTINCT_CONFIRMED_PENDING_SIGNAL.value
        assert entry.reconciliation_fingerprint == fingerprint


# ---------------------------------------------------------------------------
# 13. CLEAR after confirmation - moot, not stale.
# ---------------------------------------------------------------------------


class TestClearAfterConfirmationIsMoot:
    def test_anchor_disappears_entirely_after_confirmation_yields_clear_not_stale(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        fingerprint = _confirm_current_state(session, assertion)

        assertion.runway_id = None
        session.commit()

        entry = _only_item(session)
        assert entry.reconciliation_outcome == "CLEAR_TO_CREATE"
        assert entry.reconciliation_review_state is None  # moot, not a stale/blocking state
        assert entry.reconciliation_warnings == ()
        assert entry.stored_reconciliation_fingerprint == fingerprint  # still shown, for context
        assert entry.item.latest_reviewer_action == "CONFIRM_DISTINCT_SIGNAL"  # honest, not hidden


# ---------------------------------------------------------------------------
# 14-16. ALREADY_LINKED / MARK_DUPLICATE / resolved created Signal - excluded
# entirely, base 9D state stands unchanged.
# ---------------------------------------------------------------------------


class TestResolvedStatesExcludedEntirely:
    def test_already_linked_excluded(self, session):
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        target = _signal(session, airport)
        assertion.signal_id = target.id
        session.commit()

        assert list_reconciliation_review_items(session) == ()
        base = list_review_workflow_items(session)[0]
        assert base.review_workflow_state == "RESOLVED_SIGNAL_CREATED"

    def test_mark_duplicate_excluded_even_though_reconciliation_could_discover_the_target(self, session):
        """Mandatory (mission Section 13): MARK_DUPLICATE must not be
        re-opened simply because reconciliation could independently discover
        the same target Signal via a genuine anchor."""
        airport = _airport(session)
        runway = _runway(session, airport)
        target = _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        latest = get_latest_reviewer_action(session, assertion.id)
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            supersedes_action_id=latest.id, duplicate_of_signal_id=target.id,
        )
        assertion.signal_id = target.id
        session.commit()

        assert list_reconciliation_review_items(session) == ()
        base = list_review_workflow_items(session)[0]
        assert base.review_workflow_state == "RESOLVED_DUPLICATE"

    def test_resolved_signal_created_excluded(self, session):
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        target = _signal(session, airport)
        assertion.signal_id = target.id
        session.commit()

        assert list_reconciliation_review_items(session) == ()


# ---------------------------------------------------------------------------
# 17-18. Superseded confirmation: DEFER / NEEDS_MORE_EVIDENCE.
# ---------------------------------------------------------------------------


class TestSupersededConfirmation:
    def test_defer_supersedes_confirmation_and_excludes_from_reconciliation_view(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        _confirm_current_state(session, assertion)
        confirm = get_latest_reviewer_action(session, assertion.id)
        record_reviewer_action(
            session, assertion, action="DEFER", reason="Second thoughts.", reviewer="human:z",
            supersedes_action_id=confirm.id,
        )
        session.commit()

        assert list_reconciliation_review_items(session) == ()
        base = list_review_workflow_items(session)[0]
        assert base.review_workflow_state == "DEFERRED"

    def test_needs_more_evidence_supersedes_confirmation(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        _confirm_current_state(session, assertion)
        confirm = get_latest_reviewer_action(session, assertion.id)
        record_reviewer_action(
            session, assertion, action="NEEDS_MORE_EVIDENCE", reason="Need more.", reviewer="human:z",
            supersedes_action_id=confirm.id,
        )
        session.commit()

        assert list_reconciliation_review_items(session) == ()
        base = list_review_workflow_items(session)[0]
        assert base.review_workflow_state == "NEEDS_MORE_EVIDENCE"


# ---------------------------------------------------------------------------
# 19. Latest-action tiebreak controls, no timeless/historical lookup.
# ---------------------------------------------------------------------------


class TestLatestActionOnly:
    def test_historical_matching_confirmation_ignored_once_superseded(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        _confirm_current_state(session, assertion)  # historically valid, matching
        confirm = get_latest_reviewer_action(session, assertion.id)
        record_reviewer_action(
            session, assertion, action="REJECT_SIGNAL", reason="Rejected anyway.", reviewer="human:z",
            supersedes_action_id=confirm.id,
        )
        session.commit()

        assert list_reconciliation_review_items(session) == ()

    def test_eligibility_actions_match_r4c_valid_latest_actions_exactly(self):
        """Drift guard: this module's own _RECONCILIATION_ELIGIBLE_ACTIONS
        must always agree with governed_signal_creation's
        VALID_LATEST_ACTIONS_FOR_CREATION (plus "never reviewed yet") -
        reconciliation must never be computed for a latest action R4C itself
        would refuse before ever reaching reconciliation."""
        assert set(hrr._RECONCILIATION_ELIGIBLE_ACTIONS) - {None} == set(VALID_LATEST_ACTIONS_FOR_CREATION)


# ---------------------------------------------------------------------------
# 20. Multiple candidates, deterministic.
# ---------------------------------------------------------------------------


class TestMultipleCandidatesDeterministic:
    def test_two_independently_anchored_candidates_both_surfaced_sorted(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        signal_via_runway = _signal(session, airport, runway_id=runway.id)
        identity = _installation_identity(session, airport)
        signal_via_installation = _signal(session, airport, title="second")
        supporting_source = _source(session)
        supporting_assertion = _governed_assertion(session, supporting_source, airport, approved=False, signal_id=signal_via_installation.id)
        _link(session, supporting_assertion, identity)

        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        _link(session, assertion, identity)

        entry_first = _only_item(session)
        entry_second = _only_item(session)
        expected = tuple(sorted([signal_via_runway.id, signal_via_installation.id]))
        assert entry_first.reconciliation_candidate_signal_ids == expected
        assert entry_second.reconciliation_candidate_signal_ids == expected  # deterministic across repeated calls
        assert entry_first == entry_second  # full dataclass equality, including fingerprint - not just IDs

        # No ranking, no "best candidate," no truncation: both candidates'
        # own anchor reasons are present, not just one of them.
        assert any(f"signal {signal_via_runway.id}:" in r and "runway_id" in r for r in entry_first.reconciliation_anchor_reasons)
        assert any(f"signal {signal_via_installation.id}:" in r and "physical_installation_identity" in r for r in entry_first.reconciliation_anchor_reasons)

    def test_cli_reconciliation_output_is_identical_across_repeated_reads(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            identity = _installation_identity(s, airport)
            second_signal = _signal(s, airport, title="second")
            supporting_source = _source(s)
            supporting_assertion = _governed_assertion(s, supporting_source, airport, approved=False, signal_id=second_signal.id)
            _link(s, supporting_assertion, identity)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            _link(s, assertion, identity)
        engine.dispose()

        first = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation"))
        second = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation"))
        assert first.items == second.items
        assert cli.render_report(first, state="reconciliation") == cli.render_report(second, state="reconciliation")


# ---------------------------------------------------------------------------
# 21. No ranking/scoring anywhere in the structured output.
# ---------------------------------------------------------------------------


class TestNoRanking:
    def test_no_score_confidence_or_rank_field_on_reconciliation_review_item(self):
        fields = {f for f in ReconciliationReviewItem.__dataclass_fields__}
        for forbidden in ("score", "confidence", "rank", "weight", "priority"):
            assert not any(forbidden in f for f in fields), f"unexpected {forbidden!r}-shaped field in {fields!r}"


# ---------------------------------------------------------------------------
# 22. No local fingerprint implementation - R4A is the only authority.
# ---------------------------------------------------------------------------


class TestR4AIsTheOnlyAuthority:
    def test_no_local_hashing_or_json_canonicalization(self):
        # AST-based, not a whole-file text scan: the module's own docstring
        # legitimately says "no local hashlib, no local json" in prose to
        # explain this exact boundary - a naive substring scan would
        # false-positive on that explanatory text itself (the same class of
        # issue this project's own R1-R4C review checkpoints repeatedly
        # found and fixed). The real guarantee checked here is that the
        # module never actually imports or calls either.
        tree = ast.parse(inspect.getsource(hrr))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("hashlib", "json")
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("hashlib", "json")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("sha256", "dumps", "loads")

    def test_no_acquisition_import(self):
        # Mirrors test_queue_structure_has_no_us_specific_dependency in
        # tests/test_human_review_queue.py - this module must stay just as
        # provider-agnostic as the base queue it composes with.
        tree = ast.parse(inspect.getsource(hrr))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.acquisition")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.acquisition")


# ---------------------------------------------------------------------------
# 23. Malformed confirmation - evaluated, found unreachable, documented.
# ---------------------------------------------------------------------------


class TestMalformedConfirmationEvaluated:
    def test_null_fingerprint_confirmation_is_rejected_at_the_database_level(self, session):
        """The R4D mission asks that this scenario be evaluated. It cannot
        be constructed at all, in any database: app.models.reviewer_action.
        ReviewerAction's own DB CHECK constraint
        (ck_reviewer_actions_fingerprint_required) enforces NOT NULL for
        CONFIRM_DISTINCT_SIGNAL's reconciliation_fingerprint - even a direct
        ORM bypass of record_reviewer_action()'s own Python-level validation
        is refused by SQLite itself. No warning-generating code exists for
        this case in human_review_reconciliation.py because it is provably
        dead code, not because it was overlooked."""
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport)
        latest = get_latest_reviewer_action(session, assertion.id)
        malformed = ReviewerAction(
            source_assertion_id=assertion.id, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
            supersedes_action_id=latest.id, reconciliation_fingerprint=None,
        )
        session.add(malformed)
        with pytest.raises(Exception, match="CHECK constraint failed"):
            session.commit()
        session.rollback()

    def test_syntactically_malformed_non_null_fingerprint_is_treated_as_ordinary_stale(self, session):
        """The DB CHECK only enforces the NULL-pairing, never the 64-hex-char
        shape (R4B's own documented trust boundary) - a non-NULL but
        malformed fingerprint (e.g. bypassing record_reviewer_action()'s own
        Python-level shape validation directly via the ORM) is therefore
        constructible, and is correctly treated as an ordinary, human-
        actionable stale confirmation - never a special "corruption" warning,
        matching the mission's own "do not classify ordinary stale
        confirmation as database corruption" instruction."""
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        assertion = _governed_assertion(session, source, airport, runway_id=runway.id)
        latest = get_latest_reviewer_action(session, assertion.id)
        malformed = ReviewerAction(
            source_assertion_id=assertion.id, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:z",
            supersedes_action_id=latest.id, reconciliation_fingerprint="not-a-real-fingerprint",
        )
        session.add(malformed)
        session.commit()

        entry = _only_item(session)
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert entry.stored_reconciliation_fingerprint == "not-a-real-fingerprint"
        assert len(entry.reconciliation_warnings) == 1
        assert "STALE_RECONCILIATION_CONFIRMATION" in entry.reconciliation_warnings[0]


# ---------------------------------------------------------------------------
# 24. Existing 9D invariant warnings preserved, unaffected.
# ---------------------------------------------------------------------------


class TestExisting9DInvariantWarningsPreserved:
    def test_base_item_invariant_warnings_still_reachable_through_the_wrapper(self, session):
        airport = _airport(session)
        source = _source(session)
        assertion = _governed_assertion(
            session, source, airport, approved=False, identity_guard_decision="ATTACH_PROVISIONAL",
        )
        # An assertion with no ReviewerAction at all and a governance
        # mismatch - the base 9D invariant warning must still surface,
        # unaffected by this module wrapping it.
        entry = _only_item(session)
        assert entry.item.invariant_warnings != ()
        assert "identity_guard_decision" in entry.item.invariant_warnings[0]


# ---------------------------------------------------------------------------
# 25-27. CLI filter semantics: reconciliation / all / resolved.
# ---------------------------------------------------------------------------


class TestCLIStateFilterSemantics:
    def test_reconciliation_state_shows_only_attention_states(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            blocked_source = _source(s)
            _governed_assertion(s, blocked_source, airport, runway_id=runway.id)

            clear_source = _source(s)
            _governed_assertion(s, clear_source, airport)
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation"))
        assert len(report.items) == 1
        assert isinstance(report.items[0], ReconciliationReviewItem)
        assert report.items[0].reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value

    def test_all_and_resolved_states_unaffected_by_r4d(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport)
            record_reviewer_action(
                s, assertion, action="REJECT_SIGNAL", reason="x", reviewer="human:t",
                supersedes_action_id=get_latest_reviewer_action(s, assertion.id).id,
            )
            s.commit()
        engine.dispose()

        all_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="all"))
        resolved_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="resolved"))
        assert len(all_report.items) == 1
        assert len(resolved_report.items) == 1
        assert not isinstance(all_report.items[0], ReconciliationReviewItem)
        assert not isinstance(resolved_report.items[0], ReconciliationReviewItem)


# ---------------------------------------------------------------------------
# 28. Limit applies AFTER derived reconciliation filtering, not before.
# ---------------------------------------------------------------------------


class TestLimitAppliesAfterDerivedFiltering:
    def test_reconciliation_limit_does_not_truncate_when_newer_rows_are_clear(self, tmp_path):
        """Mirrors the exact Slice 8/9D bug shape this project has already
        fixed twice (list_human_review_items() and the CLI's own "resolved"
        state) - a small limit must not consume newer, non-attention-needing
        rows before the attention-needing filter ever runs."""
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            blocking_signal = _signal(s, airport, runway_id=runway.id)
            blocked_source = _source(s)
            blocked_assertion = _governed_assertion(s, blocked_source, airport, runway_id=runway.id)
            blocked_assertion_id = blocked_assertion.id

            # Three NEWER, CLEAR (non-attention) items created after the
            # blocking one - if limit were applied before filtering, these
            # would consume the limit and hide the blocking item.
            for i in range(3):
                clear_source = _source(s, title=f"clear-{i}")
                _governed_assertion(s, clear_source, airport)
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation", limit=1))
        assert len(report.items) == 1
        assert report.items[0].item.source_assertion_id == blocked_assertion_id


# ---------------------------------------------------------------------------
# 29-31. CLI text output.
# ---------------------------------------------------------------------------


class TestCLITextOutput:
    def test_blocking_item_renders_candidates_reasons_and_fingerprint(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            existing = _signal(s, airport, runway_id=runway.id)
            existing_id = existing.id
            source = _source(s)
            _governed_assertion(s, source, airport, runway_id=runway.id)
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation"))
        text = cli.render_report(report, state="reconciliation")
        assert "POSSIBLE_EXISTING_SIGNAL_MATCH" in text
        assert f"Blocking Signals: {existing_id}" in text
        assert "Anchor reasons:" in text
        assert "identity_anchor:runway_id" in text
        assert "Current fingerprint:" in text

    def test_current_confirmation_renders_current(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            _confirm_current_state(s, assertion)
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation"))
        text = cli.render_report(report, state="reconciliation")
        assert "Stored fingerprint:" in text
        assert "Confirmation: CURRENT" in text

    def test_stale_confirmation_renders_stale_re_review_required(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            _confirm_current_state(s, assertion)
            identity = _installation_identity(s, airport)
            signal_b = _signal(s, airport, title="second")
            supporting_source = _source(s)
            supporting_assertion = _governed_assertion(s, supporting_source, airport, approved=False, signal_id=signal_b.id)
            _link(s, supporting_assertion, identity)
            _link(s, assertion, identity)
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="reconciliation"))
        text = cli.render_report(report, state="reconciliation")
        assert "Confirmation: STALE - RE-REVIEW REQUIRED" in text
        assert "RECONCILIATION WARNINGS" in text


# ---------------------------------------------------------------------------
# 32-33. Read-only guarantee: no mutation, no commit.
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_module_never_mutates_session(self):
        source_text = inspect.getsource(hrr)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "add", "flush", "commit", "delete", "add_all",
            ):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "session":
                    raise AssertionError(f"forbidden session.{node.func.attr}() call found")

    def test_calling_the_function_leaves_no_pending_orm_changes(self, session):
        airport = _airport(session)
        runway = _runway(session, airport)
        _signal(session, airport, runway_id=runway.id)
        source = _source(session)
        _governed_assertion(session, source, airport, runway_id=runway.id)
        session.commit()

        list_reconciliation_review_items(session)
        assert session.new == set() or len(session.new) == 0
        assert session.dirty == set() or len(session.dirty) == 0

    def test_only_the_target_database_is_touched(self, tmp_path):
        import hashlib

        def _sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        target = _full_schema_database(tmp_path, "target.db")
        protected = _full_schema_database(tmp_path, "protected.db")
        engine = create_engine(f"sqlite:///{target}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            source = _source(s)
            _governed_assertion(s, source, airport, runway_id=runway.id)
        engine.dispose()

        target_before, protected_before = _sha(target), _sha(protected)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=target, state="reconciliation"))
        assert len(report.items) == 1
        assert _sha(target) == target_before
        assert _sha(protected) == protected_before


# ---------------------------------------------------------------------------
# 34. Financial/title/raw-text irrelevance.
# ---------------------------------------------------------------------------


class TestFinancialTitleFirewall:
    def test_money_and_title_changes_never_affect_fingerprint_or_outcome(self, session):
        from decimal import Decimal

        airport = _airport(session)
        runway = _runway(session, airport)
        existing = _signal(session, airport, runway_id=runway.id, estimated_total_value_usd=Decimal("1000.00"))
        source = _source(session)
        _governed_assertion(session, source, airport, runway_id=runway.id)

        before = _only_item(session)
        existing.estimated_total_value_usd = Decimal("99999999.99")
        existing.title = "A completely different title"
        session.commit()
        after = _only_item(session)

        assert before.reconciliation_fingerprint == after.reconciliation_fingerprint
        assert before.reconciliation_outcome == after.reconciliation_outcome

    def test_no_financial_or_title_field_reaches_the_reconciliation_calls(self):
        source_text = inspect.getsource(hrr)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                "build_reconciliation_subject", "find_reconciliation_candidates",
                "build_reconciliation_review_plan", "compute_reconciliation_fingerprint",
            ):
                call_source = ast.get_source_segment(source_text, node) or ""
                for forbidden in ("estimated_total_value_usd", "estimated_emas_value_usd", "title", "notes"):
                    assert forbidden not in call_source, f"forbidden token {forbidden!r} in reconciliation call"


# ---------------------------------------------------------------------------
# 35. International / provider independence.
# ---------------------------------------------------------------------------


class TestInternationalIndependence:
    def test_non_us_airport_classifies_identically(self, session):
        airport = _airport(session, name="Haneda Airport", code="HND")
        runway = _runway(session, airport, designation="05/23")
        existing = _signal(session, airport, runway_id=runway.id, confirmed_vendor="Taiyo Safety Materials KK")
        source = _source(session)
        _governed_assertion(session, source, airport, runway_id=runway.id)

        entry = _only_item(session)
        assert entry.reconciliation_review_state == ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value
        assert entry.reconciliation_candidate_signal_ids == (existing.id,)

    def test_module_source_names_no_provider_or_source_family(self):
        source_text = inspect.getsource(hrr)
        for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
            assert token not in source_text, f"forbidden provider token found: {token!r}"


# ---------------------------------------------------------------------------
# 36. MSP synthetic resolved duplicate (never the real DB in this test).
# ---------------------------------------------------------------------------


class TestMSPSyntheticResolvedDuplicate:
    def test_msp_shaped_resolved_duplicate_excluded_from_reconciliation_view(self, session):
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

        assert list_reconciliation_review_items(session) == ()
        base = list_review_workflow_items(session)[0]
        assert base.review_workflow_state == "RESOLVED_DUPLICATE"
        assert base.linked_signal_id == signal_67.id


# ---------------------------------------------------------------------------
# 37. Real MSP compatibility-safe read-only inspection (schema gate proof).
#
# The real database is never opened by this test suite. This test instead
# reproduces the REAL database's own current, verified shape (Slice 4/7
# columns present, R4B's reconciliation_fingerprint column absent - the
# exact schema state a fresh, read-only sqlite3 inspection of
# data/runway_safe.db confirmed during this task, documented in the R4D
# report) and proves the CLI schema gate refuses gracefully rather than
# raising an uncaught sqlite3.OperationalError for EVERY state, not only
# "reconciliation" - the exact defect this slice found and fixed.
# ---------------------------------------------------------------------------


class TestRealMSPSchemaCompatibility:
    def test_database_missing_r4b_column_blocks_every_state_gracefully(self, tmp_path):
        db = _full_schema_database(tmp_path, "pre_r4b.db")
        downgrade_r4b_migration(db)  # reproduces the real DB's exact current shape

        for state in ("active", "all", "resolved", "reconciliation"):
            report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state=state))
            assert cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER in report.blockers
            assert report.items == ()
            assert report.schema_readiness["reconciliation_fingerprint_column_exists"] is False

    def test_fully_migrated_database_passes_the_widened_gate(self, tmp_path):
        db = _full_schema_database(tmp_path)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="active"))
        assert report.blockers == ()
        assert report.schema_readiness["reconciliation_fingerprint_column_exists"] is True
