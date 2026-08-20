"""Tests for the R4B CONFIRM_DISTINCT_SIGNAL extension of
app/models/reviewer_action.py and app/services/reviewer_action_persistence.py
(docs/architecture/existing-signal-reconciliation-r4b-reviewer-action-report.md).

A deliberately separate file from tests/test_reviewer_action_persistence.py -
that file's own tests must keep passing completely unmodified as evidence
this slice never weakened the pre-existing APPROVE_SIGNAL/REJECT_SIGNAL/
DEFER/NEEDS_MORE_EVIDENCE/MARK_DUPLICATE behavior, mirroring the same
"R1's own file is unchanged" review-signal discipline
tests/test_existing_signal_reconciliation_review_plan.py already established
for R4A relative to R1.

Every test uses an isolated in-memory SQLite database, following the
established pattern in tests/test_reviewer_action_persistence.py.
"""
from __future__ import annotations

import ast
import inspect as inspect_module

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.reviewer_action import REVIEWER_ACTIONS, ReviewerAction
from app.services.reviewer_action_persistence import record_reviewer_action

# A real, valid-shaped 64-char lowercase hex SHA-256 digest (not computed
# from any real ReconciliationReviewPlan - R4B never recomputes or validates
# semantic ownership of a fingerprint, only its syntactic shape; see the
# "R4B/R4C boundary" tests near the bottom of this file).
VALID_FINGERPRINT = "a" * 64
OTHER_VALID_FINGERPRINT = "b" * 64


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _governed_assertion(
    session,
    *,
    identity_guard_decision="ATTACH_CONFIRMED",
    intelligence_review_decision="REVIEW_REQUIRED",
    promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    source_record_identifier="msp-222",
) -> SourceAssertion:
    airport = Airport(name="Minneapolis St. Paul International", iata_code="MSP", country="USA")
    source = Source(title="EMAS Procurement Advance Deposit memo", source_type="web_discovery")
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        source_record_identifier=source_record_identifier,
        identity_guard_decision=identity_guard_decision,
        intelligence_review_decision=intelligence_review_decision,
        promotion_policy_decision=promotion_policy_decision,
    )
    session.add_all([airport, source, assertion])
    session.commit()
    return assertion


# ---------------------------------------------------------------------------
# ACTION VOCABULARY
# ---------------------------------------------------------------------------


def test_confirm_distinct_signal_is_in_the_vocabulary():
    assert REVIEWER_ACTIONS == (
        "APPROVE_SIGNAL", "REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE",
        "MARK_DUPLICATE", "CONFIRM_DISTINCT_SIGNAL",
    )


def test_db_check_constraint_action_vocabulary_matches_python_vocabulary():
    engine, session = make_session()
    create_sql = session.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='reviewer_actions'")
    ).scalar()
    for action in REVIEWER_ACTIONS:
        assert f"'{action}'" in create_sql
    session.close(); engine.dispose()


def _approved(session, assertion, reviewer="human:approver"):
    approve = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Approved.", reviewer=reviewer,
    )
    session.commit()
    return approve


def test_confirm_distinct_signal_recorded_successfully():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    action = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL",
        reason="Reviewed the blocking candidate; this is a genuinely distinct project.",
        reviewer="human:reviewer", reconciliation_fingerprint=VALID_FINGERPRINT,
        supersedes_action_id=approve.id,
    )
    session.commit()
    assert action.action == "CONFIRM_DISTINCT_SIGNAL"
    assert action.reconciliation_fingerprint == VALID_FINGERPRINT
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# FINGERPRINT VALIDATION
# ---------------------------------------------------------------------------


def test_fingerprint_none_rejected_for_confirm_distinct_signal():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    with pytest.raises(ValueError, match="requires reconciliation_fingerprint"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        )
    session.close(); engine.dispose()


@pytest.mark.parametrize(
    "bad_fingerprint",
    [
        "",  # empty
        "a" * 63,  # too short
        "a" * 65,  # too long
        "A" * 64,  # uppercase
        "a" * 63 + "G",  # non-hex character
        " " + "a" * 63,  # leading whitespace
        "a" * 63 + " ",  # trailing whitespace
        "a" * 32 + " " + "a" * 31,  # internal whitespace
        "g" * 64,  # entirely non-hex
        "a" * 64 + "\n",  # trailing newline
        "a" * 32 + "\t" + "a" * 31,  # internal tab
        "\t" + "a" * 63,  # leading tab
    ],
)
def test_malformed_fingerprint_rejected(bad_fingerprint):
    engine, session = make_session()
    assertion = _governed_assertion(session)
    with pytest.raises(ValueError, match="reconciliation_fingerprint"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=bad_fingerprint,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_bytes_fingerprint_rejected_not_coerced():
    """bytes is not str - must be rejected outright, never silently
    decoded/coerced even if it happens to contain a valid-looking hex
    payload."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    with pytest.raises(ValueError, match="reconciliation_fingerprint"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT.encode("ascii"),
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_fingerprint_not_silently_lowercased_or_trimmed():
    """Uppercase input must be rejected outright, never coerced to a
    lowercase value and accepted - a silent normalization would let a
    value the human never actually saw (case-folded) become the byte-for-
    byte persisted record."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    with pytest.raises(ValueError):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT.upper(),
        )
    session.close(); engine.dispose()


def test_valid_fingerprint_persisted_byte_for_byte():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    distinctive = "0123456789abcdef" * 4
    assert len(distinctive) == 64
    action = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=distinctive, supersedes_action_id=approve.id,
    )
    session.commit()
    session.expire_all()
    reloaded = session.get(ReviewerAction, action.id)
    assert reloaded.reconciliation_fingerprint == distinctive
    session.close(); engine.dispose()


@pytest.mark.parametrize(
    "action,extra",
    [
        ("APPROVE_SIGNAL", {}),
        ("REJECT_SIGNAL", {}),
        ("DEFER", {}),
        ("NEEDS_MORE_EVIDENCE", {}),
    ],
)
def test_fingerprint_rejected_for_every_non_confirm_action(action, extra):
    engine, session = make_session()
    assertion = _governed_assertion(session)
    with pytest.raises(ValueError, match="only valid when action == CONFIRM_DISTINCT_SIGNAL"):
        record_reviewer_action(
            session, assertion, action=action, reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT, **extra,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_fingerprint_rejected_for_mark_duplicate_attack_A_variant():
    """Attack A (fingerprint supplied to an action other than
    CONFIRM_DISTINCT_SIGNAL) against MARK_DUPLICATE specifically, since that
    action already has its own required machine-target field
    (duplicate_of_signal_id) and must not accept a second, irrelevant one."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    existing_signal = Signal(airport=assertion.airport, title="Existing signal", category="replacement", confidence="high")
    session.add(existing_signal)
    session.commit()
    with pytest.raises(ValueError, match="only valid when action == CONFIRM_DISTINCT_SIGNAL"):
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:reviewer",
            duplicate_of_signal_id=existing_signal.id, reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# DUPLICATE TARGET INTERACTION (attack E)
# ---------------------------------------------------------------------------


def test_confirm_distinct_signal_with_duplicate_target_rejected():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    existing_signal = Signal(airport=assertion.airport, title="Existing signal", category="replacement", confidence="high")
    session.add(existing_signal)
    session.commit()
    with pytest.raises(ValueError, match="only valid when action == MARK_DUPLICATE"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT, duplicate_of_signal_id=existing_signal.id,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_db_check_constraints_independently_enforce_both_relationships_bypassing_the_service():
    """Belt-and-suspenders, matching
    test_duplicate_check_constraint_enforced_at_db_level_bypassing_the_service
    in tests/test_reviewer_action_persistence.py: even a direct ORM
    construction that skips record_reviewer_action() entirely is refused by
    the database itself."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    existing_signal = Signal(airport=assertion.airport, title="Existing signal", category="replacement", confidence="high")
    session.add(existing_signal)
    session.commit()

    bad_fingerprint_missing = ReviewerAction(
        source_assertion_id=assertion.id, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
    )
    session.add(bad_fingerprint_missing)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

    bad_fingerprint_on_defer = ReviewerAction(
        source_assertion_id=assertion.id, action="DEFER", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT,
    )
    session.add(bad_fingerprint_on_defer)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

    bad_both_fingerprint_and_duplicate = ReviewerAction(
        source_assertion_id=assertion.id, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT, duplicate_of_signal_id=existing_signal.id,
    )
    session.add(bad_both_fingerprint_and_duplicate)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# SUPERSESSION
# ---------------------------------------------------------------------------


def test_confirm_distinct_signal_without_supersedes_action_id_rejected():
    """Review-checkpoint correction: create_signal_from_approved_review()
    (unmodified by R4B) only ever raises the reconciliation block this
    action resolves when the latest ReviewerAction is already
    APPROVE_SIGNAL - so every legitimate CONFIRM_DISTINCT_SIGNAL necessarily
    supersedes some prior row. A rootless confirmation with no predecessor
    at all is refused rather than left as ambiguous, disconnected history."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    with pytest.raises(ValueError, match="requires supersedes_action_id"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_confirm_distinct_signal_may_supersede_needs_more_evidence_not_only_approve_signal():
    """The design's own Section 13 worked example
    (#3 NEEDS_MORE_EVIDENCE supersedes #2, #4 CONFIRM_DISTINCT_SIGNAL
    supersedes #3) shows CONFIRM_DISTINCT_SIGNAL superseding a row that is
    not itself APPROVE_SIGNAL - R4B must not narrow the requirement to
    "must supersede specifically an APPROVE_SIGNAL row"."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    deferred = record_reviewer_action(
        session, assertion, action="NEEDS_MORE_EVIDENCE", reason="Need more.",
        reviewer="human:b", supersedes_action_id=approve.id,
    )
    session.commit()

    confirm = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="Reviewed; distinct.",
        reviewer="human:c", supersedes_action_id=deferred.id, reconciliation_fingerprint=VALID_FINGERPRINT,
    )
    session.commit()
    assert confirm.supersedes_action_id == deferred.id
    session.close(); engine.dispose()


def test_approve_signal_then_confirm_distinct_signal_history():
    engine, session = make_session()
    assertion = _governed_assertion(session)

    approve = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL", reason="Approved.", reviewer="human:a",
    )
    session.commit()

    confirm = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="Reviewed the block; distinct project.",
        reviewer="human:b", supersedes_action_id=approve.id, reconciliation_fingerprint=VALID_FINGERPRINT,
    )
    session.commit()

    assert session.query(ReviewerAction).count() == 2
    assert approve.action == "APPROVE_SIGNAL"  # old row untouched
    assert confirm.supersedes_action_id == approve.id

    from app.services.reviewer_action_persistence import get_latest_reviewer_action
    latest = get_latest_reviewer_action(session, assertion.id)
    assert latest.id == confirm.id
    assert latest.action == "CONFIRM_DISTINCT_SIGNAL"
    session.close(); engine.dispose()


def test_confirm_distinct_signal_cross_assertion_supersession_rejected_attack_F():
    engine, session = make_session()
    assertion_a = _governed_assertion(session, source_record_identifier="a-1")
    assertion_b = _governed_assertion(session, source_record_identifier="b-1")

    action_on_a = record_reviewer_action(
        session, assertion_a, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer",
    )
    session.commit()

    with pytest.raises(ValueError, match="same SourceAssertion"):
        record_reviewer_action(
            session, assertion_b, action="CONFIRM_DISTINCT_SIGNAL", reason="y", reviewer="human:reviewer",
            supersedes_action_id=action_on_a.id, reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    session.close(); engine.dispose()


def test_confirm_distinct_signal_update_blocked_attack_G():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    action = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT, supersedes_action_id=approve.id,
    )
    session.commit()

    action.reconciliation_fingerprint = OTHER_VALID_FINGERPRINT
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


def test_confirm_distinct_signal_delete_blocked_attack_H():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    action = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT, supersedes_action_id=approve.id,
    )
    session.commit()

    session.delete(action)
    with pytest.raises(ValueError, match="auditable and cannot be deleted"):
        session.commit()
    session.rollback()
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# GOVERNANCE
# ---------------------------------------------------------------------------


def test_confirm_distinct_signal_requires_attach_confirmed_identity():
    engine, session = make_session()
    assertion = _governed_assertion(session, identity_guard_decision="ATTACH_PROVISIONAL")
    with pytest.raises(ValueError, match="identity_guard_decision"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


@pytest.mark.parametrize(
    "intelligence_review_decision",
    ["INSUFFICIENT_MATERIALITY", "IDENTITY_NOT_CONFIRMED", "CONTRADICTED", None, "GARBAGE_VALUE"],
)
def test_confirm_distinct_signal_requires_review_required_intelligence(intelligence_review_decision):
    engine, session = make_session()
    assertion = _governed_assertion(session, intelligence_review_decision=intelligence_review_decision)
    with pytest.raises(ValueError, match="intelligence_review_decision"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_confirm_distinct_signal_rejected_for_auto_eligible_attack_J():
    engine, session = make_session()
    assertion = _governed_assertion(session, promotion_policy_decision="AUTO_ELIGIBLE")
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_confirm_distinct_signal_rejected_for_do_not_promote_attack_K():
    engine, session = make_session()
    assertion = _governed_assertion(session, promotion_policy_decision="DO_NOT_PROMOTE")
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_confirm_distinct_signal_rejected_for_null_promotion_policy():
    engine, session = make_session()
    assertion = _governed_assertion(session, promotion_policy_decision=None)
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        record_reviewer_action(
            session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
            reconciliation_fingerprint=VALID_FINGERPRINT,
        )
    assert session.query(ReviewerAction).count() == 0
    session.close(); engine.dispose()


def test_existing_approve_signal_gate_still_enforced_unweakened():
    """Regression guard: introducing _ACTIONS_REQUIRING_APPROVAL_GATE must
    not change APPROVE_SIGNAL's own gate behavior at all."""
    engine, session = make_session()
    assertion = _governed_assertion(session, promotion_policy_decision="AUTO_ELIGIBLE")
    with pytest.raises(ValueError, match="promotion_policy_decision"):
        record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:reviewer",
        )
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# NO MUTATION (attack L)
# ---------------------------------------------------------------------------


def test_confirm_distinct_signal_never_constructs_a_signal_ast():
    import app.services.reviewer_action_persistence as module

    tree = ast.parse(inspect_module.getsource(module))
    signal_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Signal"
    ]
    assert signal_calls == []


def test_confirm_distinct_signal_does_not_change_signal_count():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    before = session.query(Signal).count()
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT, supersedes_action_id=approve.id,
    )
    session.commit()
    assert session.query(Signal).count() == before == 0
    session.close(); engine.dispose()


def test_confirm_distinct_signal_does_not_mutate_source_assertion():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    snapshot_before = (
        assertion.identity_guard_decision, assertion.intelligence_review_decision,
        assertion.promotion_policy_decision, assertion.signal_id, assertion.raw_relevant_text,
    )
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT, supersedes_action_id=approve.id,
    )
    session.commit()
    snapshot_after = (
        assertion.identity_guard_decision, assertion.intelligence_review_decision,
        assertion.promotion_policy_decision, assertion.signal_id, assertion.raw_relevant_text,
    )
    assert snapshot_after == snapshot_before
    session.close(); engine.dispose()


def test_confirm_distinct_signal_never_commits():
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)  # committed - a durable baseline row
    record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=VALID_FINGERPRINT, supersedes_action_id=approve.id,
    )
    session.rollback()
    # Only the committed APPROVE_SIGNAL survives - the pending
    # CONFIRM_DISTINCT_SIGNAL was never committed by the service itself.
    remaining = session.query(ReviewerAction).all()
    assert [a.action for a in remaining] == ["APPROVE_SIGNAL"]
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# MODEL CONTRACT (mission section 9): exactly one new field, nothing more.
# ---------------------------------------------------------------------------


def test_reviewer_action_gains_exactly_one_new_persisted_field():
    existing_columns_before_r4b = {
        "id", "source_assertion_id", "action", "reason", "reviewer", "created_at",
        "supersedes_action_id", "duplicate_of_signal_id",
    }
    current_columns = set(ReviewerAction.__table__.columns.keys())
    assert current_columns - existing_columns_before_r4b == {"reconciliation_fingerprint"}


def test_no_extraneous_reconciliation_state_fields_added():
    columns = set(ReviewerAction.__table__.columns.keys())
    forbidden_substrings = (
        "candidate", "score", "title_snapshot", "financial", "published",
        "override", "state",
    )
    for column in columns:
        for forbidden in forbidden_substrings:
            assert forbidden not in column, f"unexpected column {column!r} suggests scope creep ({forbidden!r})"


# ---------------------------------------------------------------------------
# Attack N: R4B cannot establish semantic fingerprint ownership - it accepts
# any syntactically valid fingerprint regardless of which SourceAssertion's
# reconciliation state it actually describes. This is documented, intended
# behavior, not a defect: recomputing/matching against a live reconciliation
# plan is R4C's responsibility (see the module docstring's "R4B/R4C
# boundary" and docs/architecture/existing-signal-reconciliation-r4b-
# reviewer-action-report.md).
# ---------------------------------------------------------------------------


def test_structurally_valid_fingerprint_from_a_different_context_is_accepted_by_r4b():
    """R4B performs no semantic validation of a fingerprint's provenance -
    it cannot, without recomputing the R4A plan, which is out of this
    slice's scope by design. A syntactically valid 64-char hex string is
    accepted no matter what it was "supposed" to describe; R4C's fresh
    recomputation-and-comparison at creation time is the actual safety
    boundary, not this persistence layer."""
    engine, session = make_session()
    assertion = _governed_assertion(session)
    approve = _approved(session, assertion)
    action = record_reviewer_action(
        session, assertion, action="CONFIRM_DISTINCT_SIGNAL", reason="x", reviewer="human:reviewer",
        reconciliation_fingerprint=OTHER_VALID_FINGERPRINT,  # not derived from this assertion at all
        supersedes_action_id=approve.id,
    )
    session.commit()
    assert action.reconciliation_fingerprint == OTHER_VALID_FINGERPRINT
    session.close(); engine.dispose()


# ---------------------------------------------------------------------------
# R3 non-interference: R4B must not have touched governed_signal_creation.py.
# ---------------------------------------------------------------------------


def test_governed_signal_creation_module_does_not_reference_confirm_distinct_signal():
    """This is R4C's job, not R4B's - governed_signal_creation.py must not
    yet know this action value exists."""
    import app.services.governed_signal_creation as module

    source = inspect_module.getsource(module)
    assert "CONFIRM_DISTINCT_SIGNAL" not in source
