"""Tests for scripts/review_reconciliation_item.py
(docs/architecture/existing-signal-reconciliation-r4e-reviewer-action-cli-report.md,
R4E of docs/architecture/existing-signal-reconciliation-r4-human-resolution-
design.md's own S20 roadmap).

Every test uses an isolated, disposable SQLite database - the real
data/runway_safe.db is never written to in this suite.
"""
from __future__ import annotations

import ast
import hashlib
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
from app.services.reviewer_action_persistence import get_latest_reviewer_action, record_reviewer_action
from scripts import review_reconciliation_item as cli
from scripts.migrate_reconciliation_confirmation_slice_r4b import downgrade as downgrade_r4b_migration


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _blocking_fixture(tmp_path, name="blocking.db"):
    """A single, self-contained, disposable database with one governed,
    APPROVE_SIGNAL-approved SourceAssertion runway-anchored to one existing
    Signal - the canonical positive case most tests below build on. Returns
    (database_path, source_assertion_id, existing_signal_id)."""
    db = _full_schema_database(tmp_path, name)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        airport = _airport(s)
        runway = _runway(s, airport)
        existing = _signal(s, airport, runway_id=runway.id)
        source = _source(s)
        assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
        assertion_id, signal_id = assertion.id, existing.id
    engine.dispose()
    return db, assertion_id, signal_id


# ---------------------------------------------------------------------------
# 1-2. Missing --database / missing write authorization.
# ---------------------------------------------------------------------------


class TestArgumentValidation:
    def test_missing_database_argument_rejected_by_argparse(self):
        with pytest.raises(SystemExit):
            cli.main(["--source-assertion-id", "1"])

    def test_write_without_action_rejected(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="requires --action"):
            cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, allow_database_write=True))

    def test_write_without_reviewer_rejected(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="--reviewer is required"):
            cli.run_review(cli.ReviewConfig(
                database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
                confirm_current_plan="a" * 64, reason="x", allow_database_write=True,
            ))

    def test_write_without_reason_rejected(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="--reason is required"):
            cli.run_review(cli.ReviewConfig(
                database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
                confirm_current_plan="a" * 64, reviewer="human:t", allow_database_write=True,
            ))

    def test_invalid_action_rejected(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="action must be one of"):
            cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="APPROVE_EVERYTHING"))

    def test_mark_duplicate_without_target_rejected_even_as_dry_run(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="--duplicate-of-signal-id is required"):
            cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="MARK_DUPLICATE"))

    def test_duplicate_target_only_valid_with_mark_duplicate(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="only valid with --action MARK_DUPLICATE"):
            cli.run_review(cli.ReviewConfig(
                database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL", duplicate_of_signal_id=sid,
            ))

    def test_confirm_current_plan_only_valid_with_confirm_distinct_signal(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        with pytest.raises(ValueError, match="only valid with --action CONFIRM_DISTINCT_SIGNAL"):
            cli.run_review(cli.ReviewConfig(
                database=db, source_assertion_id=aid, action="MARK_DUPLICATE",
                duplicate_of_signal_id=sid, confirm_current_plan="a" * 64,
            ))


# ---------------------------------------------------------------------------
# 3-4. Schema gate, and that it runs before any ORM access.
# ---------------------------------------------------------------------------


class TestSchemaGate:
    def test_missing_r4b_column_blocks_gracefully(self, tmp_path):
        db = _full_schema_database(tmp_path, "pre_r4b.db")
        downgrade_r4b_migration(db)
        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=1))
        assert cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER in result.blockers
        assert result.schema_readiness["reconciliation_fingerprint_column_exists"] is False

    def test_schema_gate_runs_before_any_orm_query(self, tmp_path):
        """A database missing R4B's column but with NO source_assertions
        table populated at all (id=1 does not exist) must still be blocked
        by the schema gate, not fail later with an id-not-found result or,
        worse, an uncaught OperationalError from the ORM itself."""
        db = _full_schema_database(tmp_path, "pre_r4b_empty.db")
        downgrade_r4b_migration(db)
        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=999999))
        assert result.blockers == (cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER,)

    def test_fully_migrated_schema_passes(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid))
        assert result.blockers == ()
        assert result.schema_readiness["reconciliation_fingerprint_column_exists"] is True

    def test_source_assertion_not_found(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=999999))
        assert result.blockers == (cli.SOURCE_ASSERTION_NOT_FOUND_BLOCKER,)

    def test_schema_degraded_between_dry_run_and_write_is_rechecked_not_trusted(self, tmp_path):
        """Review-checkpoint addition (mission Section 23): the write
        invocation must rerun schema readiness fresh, never trust an
        earlier (already-passed) dry-run's own schema check. Simulated by
        downgrading R4B's migration on the very database a successful
        dry-run just ran against, immediately before attempting the write."""
        db, aid, sid = _blocking_fixture(tmp_path, "degrade.db")
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert dry.blockers == ()
        assert dry.action_eligible is True

        downgrade_r4b_migration(db)

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.blockers == (cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert result.written is False


# ---------------------------------------------------------------------------
# 5-6. Dry-run blocking-item output; current fingerprint exposed, from R4A.
# ---------------------------------------------------------------------------


class TestDryRunInspection:
    def test_inspect_only_no_action_shows_blocking_state(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid))
        assert result.blockers == ()
        assert result.latest_action == "APPROVE_SIGNAL"
        assert result.linked_signal_id is None
        assert result.reconciliation_outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH"
        assert result.candidate_signal_ids == (sid,)
        assert any("identity_anchor:runway_id" in r for r in result.anchor_reasons)
        assert result.current_fingerprint is not None
        assert len(result.current_fingerprint) == 64
        assert result.proposed_action is None
        assert result.written is False

    def test_fingerprint_comes_from_r4a_not_local_logic(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            from app.services.existing_signal_reconciliation import evaluate_existing_signal_reconciliation
            from app.services.existing_signal_reconciliation_candidates import (
                build_reconciliation_subject, find_reconciliation_candidates,
            )
            from app.services.existing_signal_reconciliation_review import (
                build_reconciliation_review_plan, compute_reconciliation_fingerprint,
            )
            assertion = s.get(SourceAssertion, aid)
            subject = build_reconciliation_subject(assertion, (), category=None, reference_year=None)
            candidates = find_reconciliation_candidates(s, assertion)
            decision = evaluate_existing_signal_reconciliation(subject, candidates)
            plan = build_reconciliation_review_plan(source_assertion_id=aid, subject=subject, decision=decision)
            expected = compute_reconciliation_fingerprint(plan)
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid))
        assert result.current_fingerprint == expected

    def test_no_local_fingerprint_or_hashing_code(self):
        tree = ast.parse(inspect.getsource(cli))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("hashlib", "json")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("sha256", "dumps", "loads")


# ---------------------------------------------------------------------------
# 7-9. CONFIRM_DISTINCT_SIGNAL: valid current plan, matching supplied
# confirmation, stale supplied confirmation before write.
# ---------------------------------------------------------------------------


class TestConfirmDistinctSignal:
    def test_valid_current_plan_write_succeeds(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert dry.action_eligible is True
        assert dry.written is False

        written = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="genuinely distinct",
            allow_database_write=True,
        ))
        assert written.written is True
        assert written.written_reviewer_action_id is not None

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            latest = get_latest_reviewer_action(s, aid)
            assert latest.action == "CONFIRM_DISTINCT_SIGNAL"
            assert latest.reconciliation_fingerprint == dry.current_fingerprint
        engine.dispose()

    def test_supplied_confirmation_fingerprint_must_match_before_write(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan="b" * 64, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason
        assert result.written is False

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 1  # only the original APPROVE_SIGNAL
        engine.dispose()

    def test_missing_confirmation_fingerprint_refused_before_write(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_REQUIRED" in result.action_refusal_reason
        assert result.written is False

    def test_stale_supplied_fingerprint_because_candidate_added_between_dry_run_and_write(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        stale_fingerprint = dry.current_fingerprint

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = s.get(SourceAssertion, aid).airport
            identity = _installation_identity(s, airport)
            second_signal = _signal(s, airport, title="second")
            supporting_source = _source(s)
            supporting_assertion = _governed_assertion(s, supporting_source, airport, approved=False, signal_id=second_signal.id)
            _link(s, supporting_assertion, identity)
            assertion = s.get(SourceAssertion, aid)
            _link(s, assertion, identity)
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=stale_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason
        assert result.written is False

    def test_candidate_removed_between_dry_run_and_write_also_detected(self, tmp_path):
        db = _full_schema_database(tmp_path, "removed.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            identity = _installation_identity(s, airport)
            signal_a = _signal(s, airport)
            signal_a_id = signal_a.id
            supporting_source_a = _source(s)
            supporting_assertion_a = _governed_assertion(s, supporting_source_a, airport, approved=False, signal_id=signal_a.id)
            supporting_assertion_a_id = supporting_assertion_a.id
            link_a = _link(s, supporting_assertion_a, identity)
            link_a_id = link_a.id
            signal_b = _signal(s, airport, title="second")
            signal_b_id = signal_b.id
            supporting_source_b = _source(s)
            supporting_assertion_b = _governed_assertion(s, supporting_source_b, airport, approved=False, signal_id=signal_b.id)
            _link(s, supporting_assertion_b, identity)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport)
            _link(s, assertion, identity)
            aid = assertion.id
        engine.dispose()

        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert set(dry.candidate_signal_ids) == {signal_a_id, signal_b_id}

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            supporting_assertion_a_fresh = s.get(SourceAssertion, supporting_assertion_a_id)
            _link(s, supporting_assertion_a_fresh, identity=None, outcome="UNRESOLVED", supersedes_link_id=link_a_id)
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason

    def test_anchor_changed_between_dry_run_and_write_also_detected(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, aid)
            supporting_assertion = SourceAssertion(
                source_id=assertion.source_id, airport_id=assertion.airport_id, assertion_type="project_construction",
                source_record_identifier="supporting-doc", signal_id=sid,
                identity_guard_decision="ATTACH_CONFIRMED", intelligence_review_decision="REVIEW_REQUIRED",
                promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
            )
            s.add(supporting_assertion)
            assertion.runway_id = None
            s.commit()
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason


# ---------------------------------------------------------------------------
# 13-14/32. Current identical confirmation is idempotent; stale old
# confirmation allows a new append-only one; fingerprint cannot be overridden.
# ---------------------------------------------------------------------------


class TestCurrentAndStaleConfirmation:
    def test_current_identical_confirmation_refused_no_duplicate_row(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        first = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert first.written is True

        second = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x again", allow_database_write=True,
        ))
        assert second.action_eligible is False
        assert "ALREADY_CONFIRMED_CURRENT_PLAN" in second.action_refusal_reason
        assert second.written is False

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 2  # APPROVE_SIGNAL + the one CONFIRM_DISTINCT_SIGNAL
        engine.dispose()

    def test_stale_old_confirmation_allows_new_append_only_confirmation(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        dry1 = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        first = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry1.current_fingerprint, reviewer="human:t", reason="first", allow_database_write=True,
        ))
        assert first.written is True
        old_fingerprint = dry1.current_fingerprint

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = s.get(SourceAssertion, aid).airport
            identity = _installation_identity(s, airport)
            second_signal = _signal(s, airport, title="second")
            supporting_source = _source(s)
            supporting_assertion = _governed_assertion(s, supporting_source, airport, approved=False, signal_id=second_signal.id)
            _link(s, supporting_assertion, identity)
            assertion = s.get(SourceAssertion, aid)
            _link(s, assertion, identity)
        engine.dispose()

        dry2 = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert dry2.action_eligible is True
        assert dry2.current_fingerprint != old_fingerprint

        second = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry2.current_fingerprint, reviewer="human:t", reason="second, updated", allow_database_write=True,
        ))
        assert second.written is True

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            actions = s.query(ReviewerAction).order_by(ReviewerAction.id).all()
            assert [a.action for a in actions] == ["APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL", "CONFIRM_DISTINCT_SIGNAL"]
            assert actions[1].reconciliation_fingerprint == old_fingerprint  # F1 never mutated
            assert actions[2].reconciliation_fingerprint == dry2.current_fingerprint  # F2, new row
            assert actions[2].supersedes_action_id == actions[1].id  # 15. correct supersession
        engine.dispose()

    def test_fingerprint_cannot_be_overridden_by_caller(self, tmp_path):
        """Even a syntactically valid-looking, attacker-chosen fingerprint
        supplied via --confirm-current-plan is never itself persisted - only
        ever compared. What actually gets written is always this script's
        own fresh recomputation."""
        db, aid, sid = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        written = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert written.written_reviewer_action_id is not None
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            latest = get_latest_reviewer_action(s, aid)
            assert latest.reconciliation_fingerprint == dry.current_fingerprint  # the FRESH value, not attacker input
        engine.dispose()

    def test_malformed_confirmation_fingerprint_input_rejected(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan="not-a-real-fingerprint", reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason
        assert result.written is False

    def test_uppercase_fingerprint_rejected_not_normalized(self, tmp_path):
        """Review-checkpoint addition (mission Section 6): an uppercase copy
        of the otherwise-correct current fingerprint must never be silently
        case-folded and accepted."""
        db, aid, _ = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint.upper(), reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason
        assert result.written is False

    def test_whitespace_padded_fingerprint_rejected_not_stripped(self, tmp_path):
        """Review-checkpoint addition (mission Section 6): leading/trailing
        whitespace around an otherwise-correct fingerprint must never be
        silently stripped and accepted."""
        db, aid, _ = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=f" {dry.current_fingerprint} ", reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason
        assert result.written is False

    def test_truncated_fingerprint_rejected(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint[:-1], reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason

    def test_genuine_fingerprint_from_a_different_source_assertion_rejected(self, tmp_path):
        """Review-checkpoint addition (mission Section 6): a REAL,
        genuinely-computed R4A fingerprint - not a hand-typed garbage string
        - taken from a different SourceAssertion's own current blocking
        state must still be rejected here, because source_assertion_id is
        baked into R4A's own hashed payload (R4A's own established
        guarantee, exercised through R4E rather than reimplemented)."""
        db = _full_schema_database(tmp_path, "cross_assertion.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            source_a = _source(s)
            assertion_a = _governed_assertion(s, source_a, airport, runway_id=runway.id)
            aid_a = assertion_a.id
            source_b = _source(s)
            assertion_b = _governed_assertion(s, source_b, airport, runway_id=runway.id)
            aid_b = assertion_b.id
        engine.dispose()

        dry_b = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid_b, action="CONFIRM_DISTINCT_SIGNAL"))
        assert dry_b.action_eligible is True

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid_a, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry_b.current_fingerprint, reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "CONFIRMATION_FINGERPRINT_MISMATCH" in result.action_refusal_reason
        assert result.written is False


# ---------------------------------------------------------------------------
# 16-22. MARK_DUPLICATE: target required, must be a current candidate,
# arbitrary target rejected, +1 ReviewerAction, sets signal_id, target
# Signal unchanged, no new Signal.
# ---------------------------------------------------------------------------


class TestMarkDuplicate:
    def test_target_must_be_a_current_blocking_candidate(self, tmp_path):
        db = _full_schema_database(tmp_path, "unrelated.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)  # the real blocking candidate
            unrelated_signal = _signal(s, airport, title="unrelated, never blocking")
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            aid, unrelated_id = assertion.id, unrelated_signal.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=unrelated_id,
        ))
        assert result.action_eligible is False
        assert "DUPLICATE_TARGET_NOT_A_CURRENT_CANDIDATE" in result.action_refusal_reason

    def test_valid_target_writes_one_reviewer_action_and_links_signal(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            target_before = s.get(Signal, sid)
            snapshot_before = (
                target_before.title, target_before.category, target_before.confidence, target_before.status,
                target_before.published, target_before.confirmed_vendor, target_before.likely_supplier,
                target_before.estimated_total_value_usd, target_before.estimated_emas_value_usd,
                target_before.updated_at,
            )
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:t", reason="same project", allow_database_write=True,
        ))
        assert result.written is True
        assert result.written_linked_signal_id == sid

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 2  # +1 only, APPROVE_SIGNAL + MARK_DUPLICATE
            assert s.query(Signal).count() == 1  # no new Signal
            assertion = s.get(SourceAssertion, aid)
            assert assertion.signal_id == sid
            target_after = s.get(Signal, sid)
            # 12. Target Signal immutability: full field-snapshot equality,
            # not just title - published/updated_at/vendor/financial/status
            # all byte-identical, only +ReviewerAction and +signal_id link.
            snapshot_after = (
                target_after.title, target_after.category, target_after.confidence, target_after.status,
                target_after.published, target_after.confirmed_vendor, target_after.likely_supplier,
                target_after.estimated_total_value_usd, target_after.estimated_emas_value_usd,
                target_after.updated_at,
            )
            assert snapshot_after == snapshot_before
        engine.dispose()

    def test_candidate_removed_between_dry_run_and_write_refuses_mark_duplicate(self, tmp_path):
        """Review-checkpoint addition (mission Section 10): MARK_DUPLICATE's
        own target validation must re-run fresh at write time too - a
        target that was blocking during a dry-run but stopped blocking
        before the write attempt must be refused, exactly like
        CONFIRM_DISTINCT_SIGNAL's own fingerprint-mismatch protection, via
        the identical mechanism (every call recomputes reconciliation from
        scratch, never trusting an earlier read)."""
        db = _full_schema_database(tmp_path, "md_removed.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            # A second, independently (runway-)anchored candidate is kept
            # constant throughout so the outcome stays
            # POSSIBLE_EXISTING_SIGNAL_MATCH both before and after signal_a's
            # own link is retracted below - isolating "signal_a stopped
            # being a valid target" as the only variable, rather than
            # collapsing the whole decision to CLEAR_TO_CREATE.
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id, title="stable anchor")

            identity = _installation_identity(s, airport)
            signal_a = _signal(s, airport)
            signal_a_id = signal_a.id
            supporting_source_a = _source(s)
            supporting_assertion_a = _governed_assertion(s, supporting_source_a, airport, approved=False, signal_id=signal_a.id)
            supporting_assertion_a_id = supporting_assertion_a.id
            link_a = _link(s, supporting_assertion_a, identity)
            link_a_id = link_a.id
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            _link(s, assertion, identity)
            aid = assertion.id
        engine.dispose()

        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=signal_a_id))
        assert dry.action_eligible is True

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            supporting_assertion_a_fresh = s.get(SourceAssertion, supporting_assertion_a_id)
            _link(s, supporting_assertion_a_fresh, identity=None, outcome="UNRESOLVED", supersedes_link_id=link_a_id)
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=signal_a_id,
            reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "DUPLICATE_TARGET_NOT_A_CURRENT_CANDIDATE" in result.action_refusal_reason
        assert result.written is False

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 1  # only APPROVE_SIGNAL
            assert s.get(SourceAssertion, aid).signal_id is None
        engine.dispose()

    def test_link_phase_failure_rolls_back_the_already_inserted_reviewer_action(self, tmp_path, monkeypatch):
        """Mission Section 11: attack failure AFTER ReviewerAction insertion
        but BEFORE link success specifically - a stronger, more targeted
        proof than a governance failure that never reaches the insert at
        all (already covered by test_failed_action_leaves_zero_partial_write
        for CONFIRM_DISTINCT_SIGNAL). The MARK_DUPLICATE row is genuinely
        flushed (visible within the transaction) before the simulated link
        failure - the assertion below proves the whole write is one atomic
        unit, not two independently-committed steps."""
        db, aid, sid = _blocking_fixture(tmp_path)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated link-phase failure")

        monkeypatch.setattr(cli, "link_source_assertion_to_duplicate_signal", _boom)

        with pytest.raises(RuntimeError, match="simulated link-phase failure"):
            cli.run_review(cli.ReviewConfig(
                database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
                reviewer="human:t", reason="x", allow_database_write=True,
            ))

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 1  # only APPROVE_SIGNAL - the MARK_DUPLICATE insert rolled back too
            assert s.get(SourceAssertion, aid).signal_id is None
        engine.dispose()


# ---------------------------------------------------------------------------
# 23-26. Transaction safety: rollback on action failure, rollback on link
# failure, one commit on success, no SessionLocal.
# ---------------------------------------------------------------------------


class TestTransactionSafety:
    def test_no_session_local_import(self):
        tree = ast.parse(inspect.getsource(cli))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "SessionLocal"

    def test_dry_run_never_calls_commit(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 1  # only the original APPROVE_SIGNAL
        engine.dispose()

    def test_no_backup_file_created_by_dry_run_or_write(self, tmp_path):
        """Review-checkpoint addition (mission Section 21): unlike every
        migration script in this project, this CLI deliberately never backs
        up the database (documented in its own module docstring) - proven
        directly here rather than only by the absence of a backup import."""
        db, aid, sid = _blocking_fixture(tmp_path, "nobackup.db")
        before = set(tmp_path.iterdir())

        cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        after_dry_run = set(tmp_path.iterdir())
        assert before == after_dry_run

        cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:t", reason="x", allow_database_write=True,
        ))
        after_write = set(tmp_path.iterdir())
        assert before == after_write  # no new file (e.g. a backup) appeared even after a real write

    def test_no_backup_function_imported(self):
        tree = ast.parse(inspect.getsource(cli))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert "backup" not in (alias.asname or alias.name).lower()

    def test_readonly_engine_used_without_write_authorization(self, tmp_path):
        db, aid, _ = _blocking_fixture(tmp_path)
        engine = cli.build_engine(db, writable=False)
        with Session(engine) as s:
            with pytest.raises(Exception):
                s.add(Airport(name="Should fail", country="USA"))
                s.commit()
        engine.dispose()

    def test_one_commit_on_success_produces_exactly_the_expected_rows(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:t", reason="x", allow_database_write=True,
        ))
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 2
            assert s.get(SourceAssertion, aid).signal_id == sid
        engine.dispose()

    def test_failed_action_leaves_zero_partial_write(self, tmp_path):
        """The write path (record_reviewer_action() itself) is the one that
        can genuinely fail closed - e.g. a malformed governance state. This
        proves the CLI's own try/except/rollback discipline actually works,
        not just that validation refuses upfront."""
        db = _full_schema_database(tmp_path, "bad_governance.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            _signal(s, airport, runway_id=runway.id)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            aid = assertion.id
            # Degrade governance AFTER approval - record_reviewer_action()'s
            # own gate will now refuse the write.
            assertion.identity_guard_decision = "ATTACH_PROVISIONAL"
            s.commit()
        engine.dispose()

        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        with pytest.raises(ValueError, match="identity_guard_decision"):
            cli.run_review(cli.ReviewConfig(
                database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
                confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
            ))

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ReviewerAction).count() == 1  # only the original APPROVE_SIGNAL - no partial write
        engine.dispose()


# ---------------------------------------------------------------------------
# 27-28. Advisory-only / CLEAR refuses; ALREADY_LINKED refuses.
# ---------------------------------------------------------------------------


class TestRefusalStates:
    def test_clear_to_create_refuses_confirm_distinct(self, tmp_path):
        db = _full_schema_database(tmp_path, "clear.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport)  # no anchor at all
            aid = assertion.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert result.action_eligible is False
        assert "NO_BLOCKING_PLAN" in result.action_refusal_reason

    def test_clear_to_create_refuses_mark_duplicate_even_with_advisory_candidate(self, tmp_path):
        db = _full_schema_database(tmp_path, "advisory.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            advisory_signal = _signal(s, airport, category="replacement", confirmed_vendor="Acme")
            source = _source(s)
            assertion = _governed_assertion(s, source, airport)
            aid, advisory_id = assertion.id, advisory_signal.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=advisory_id,
        ))
        assert result.action_eligible is False
        assert "NO_BLOCKING_PLAN" in result.action_refusal_reason

    def test_already_linked_refuses_any_action(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, aid)
            assertion.signal_id = sid
            s.commit()
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert result.action_eligible is False
        assert "ALREADY_LINKED" in result.action_refusal_reason
        assert result.reconciliation_outcome is None  # no fake blocking plan built

    def test_already_linked_pure_inspection_shows_link_no_crash(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, aid)
            assertion.signal_id = sid
            s.commit()
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid))
        assert result.linked_signal_id == sid
        assert result.action_eligible is None  # no action was requested


# ---------------------------------------------------------------------------
# 29-30. Generic actions: DEFER / NEEDS_MORE_EVIDENCE semantics.
# ---------------------------------------------------------------------------


class TestGenericActions:
    @pytest.mark.parametrize("action", ["DEFER", "NEEDS_MORE_EVIDENCE", "REJECT_SIGNAL"])
    def test_generic_action_recorded_without_fingerprint_or_duplicate_target(self, tmp_path, action):
        db, aid, sid = _blocking_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action=action, reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.written is True
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            latest = get_latest_reviewer_action(s, aid)
            assert latest.action == action
            assert latest.reconciliation_fingerprint is None
            assert latest.duplicate_of_signal_id is None
        engine.dispose()

    def test_generic_action_works_even_when_reconciliation_is_clear(self, tmp_path):
        """DEFER/NEEDS_MORE_EVIDENCE/REJECT_SIGNAL have no reconciliation
        precondition - they must remain usable regardless of the fresh
        reconciliation outcome, unlike the two reconciliation-specific
        actions."""
        db = _full_schema_database(tmp_path, "clear2.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport)
            aid = assertion.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="DEFER", reviewer="human:t", reason="x",
            allow_database_write=True,
        ))
        assert result.written is True


# ---------------------------------------------------------------------------
# 33. Wrong-DB isolation.
# ---------------------------------------------------------------------------


class TestWrongDatabaseSafety:
    def test_action_against_target_never_touches_protected_database(self, tmp_path):
        target, aid, sid = _blocking_fixture(tmp_path, "target.db")
        protected, _, _ = _blocking_fixture(tmp_path, "protected.db")
        protected_before = _sha(protected)

        result = cli.run_review(cli.ReviewConfig(
            database=target, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:t", reason="x", allow_database_write=True,
        ))
        assert result.written is True
        assert _sha(protected) == protected_before

    def test_no_implicit_default_database_path(self):
        with pytest.raises(SystemExit):
            cli.main(["--source-assertion-id", "1", "--action", "CONFIRM_DISTINCT_SIGNAL"])


# ---------------------------------------------------------------------------
# 34. No publication change; 21. Target Signal unchanged.
# ---------------------------------------------------------------------------


class TestPublicationAndSignalSafety:
    def test_target_signal_published_flag_untouched(self, tmp_path):
        db = _full_schema_database(tmp_path, "pub.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            existing = _signal(s, airport, runway_id=runway.id, published=True)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            aid, sid = assertion.id, existing.id
        engine.dispose()

        cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:t", reason="x", allow_database_write=True,
        ))
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.get(Signal, sid).published is True
        engine.dispose()

    def test_confirm_distinct_creates_no_signal(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        dry = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL",
            confirm_current_plan=dry.current_fingerprint, reviewer="human:t", reason="x", allow_database_write=True,
        ))
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(Signal).count() == 1  # only the pre-existing candidate
            assert s.get(SourceAssertion, aid).signal_id is None  # CONFIRM_DISTINCT never links
        engine.dispose()

    def test_no_create_signal_from_approved_review_import(self):
        tree = ast.parse(inspect.getsource(cli))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert (alias.asname or alias.name) != "create_signal_from_approved_review"


# ---------------------------------------------------------------------------
# 35-37. Financial/title irrelevance; provider agnostic; international.
# ---------------------------------------------------------------------------


class TestFinancialTitleAndInternational:
    def test_money_and_title_never_affect_fingerprint_or_eligibility(self, tmp_path):
        from decimal import Decimal

        db = _full_schema_database(tmp_path, "money.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            existing = _signal(s, airport, runway_id=runway.id, estimated_total_value_usd=Decimal("1000.00"))
            existing_id = existing.id
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            aid = assertion.id
        engine.dispose()

        before = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            sig = s.get(Signal, existing_id)
            sig.estimated_total_value_usd = Decimal("99999999.99")
            sig.title = "A completely different title"
            s.commit()
        engine.dispose()

        after = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert after.current_fingerprint == before.current_fingerprint
        assert after.action_eligible == before.action_eligible

    def test_no_financial_or_title_argument_reaches_reconciliation_calls(self):
        source_text = inspect.getsource(cli)
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                "build_reconciliation_subject", "find_reconciliation_candidates",
                "build_reconciliation_review_plan", "compute_reconciliation_fingerprint",
            ):
                call_source = ast.get_source_segment(source_text, node) or ""
                for forbidden in ("estimated_total_value_usd", "estimated_emas_value_usd", "title", "notes"):
                    assert forbidden not in call_source

    def test_module_source_names_no_provider_or_source_family(self):
        source_text = inspect.getsource(cli)
        for token in ("MAC", "MSP", "FAA", "Runway Safe", "USAspending", "Granicus"):
            assert token not in source_text

    def test_non_us_international_case_identical_workflow(self, tmp_path):
        db = _full_schema_database(tmp_path, "intl.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s, name="Haneda Airport", code="HND")
            runway = _runway(s, airport, designation="05/23")
            existing = _signal(s, airport, runway_id=runway.id, confirmed_vendor="Taiyo Safety Materials KK")
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            aid, sid = assertion.id, existing.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:审查员", reason="同一个项目 - same project, unicode reason", allow_database_write=True,
        ))
        assert result.written is True
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            latest = get_latest_reviewer_action(s, aid)
            assert latest.reviewer == "human:审查员"
        engine.dispose()


# ---------------------------------------------------------------------------
# 38-40. Deterministic output; multiple-candidate display; no auto-selection.
# ---------------------------------------------------------------------------


class TestDeterminismAndNoAutoSelection:
    def test_repeated_dry_run_output_identical(self, tmp_path):
        db, aid, sid = _blocking_fixture(tmp_path)
        first = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        second = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert first == second
        assert cli.render_result(first) == cli.render_result(second)

    def test_multiple_candidates_all_displayed_no_truncation(self, tmp_path):
        db = _full_schema_database(tmp_path, "multi.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s)
            runway = _runway(s, airport)
            signal_via_runway = _signal(s, airport, runway_id=runway.id)
            signal_via_runway_id = signal_via_runway.id
            identity = _installation_identity(s, airport)
            signal_via_installation = _signal(s, airport, title="second")
            signal_via_installation_id = signal_via_installation.id
            supporting_source = _source(s)
            supporting_assertion = _governed_assertion(s, supporting_source, airport, approved=False, signal_id=signal_via_installation.id)
            _link(s, supporting_assertion, identity)
            source = _source(s)
            assertion = _governed_assertion(s, source, airport, runway_id=runway.id)
            _link(s, assertion, identity)
            aid = assertion.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid))
        expected = tuple(sorted([signal_via_runway_id, signal_via_installation_id]))
        assert result.candidate_signal_ids == expected
        assert any(f"signal {signal_via_runway_id}:" in r for r in result.anchor_reasons)
        assert any(f"signal {signal_via_installation_id}:" in r for r in result.anchor_reasons)

        # No auto-selection: neither candidate is chosen for MARK_DUPLICATE
        # without an explicit --duplicate-of-signal-id, even though there
        # are exactly two, unambiguous candidates.
        with pytest.raises(ValueError, match="--duplicate-of-signal-id is required"):
            cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="MARK_DUPLICATE"))


# ---------------------------------------------------------------------------
# 41. Real MSP #222 compatibility-safe, read-only inspection - conceptual,
# reproduced synthetically; the real database is never opened by this test
# suite (see the R4E report for the actual read-only investigation performed
# outside pytest).
# ---------------------------------------------------------------------------


class TestMSPSyntheticAlreadyResolved:
    def test_msp_shaped_resolved_duplicate_refuses_further_reconciliation_action(self, tmp_path):
        db = _full_schema_database(tmp_path, "msp.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s, name="Minneapolis St. Paul International", code="MSP")
            signal_67 = _signal(
                s, airport, title="EMAS order (vendor confirmed)", category="replacement",
                confidence="high", confirmed_vendor="Runway Safe",
            )
            signal_67_id = signal_67.id
            source = _source(s, title="EMAS Procurement Advance Deposit memo")
            assertion = _governed_assertion(s, source, airport, source_record_identifier="msp-222", approved=False)
            approve = record_reviewer_action(s, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:a")
            s.commit()
            record_reviewer_action(
                s, assertion, action="MARK_DUPLICATE", reason="Corroborates existing signal.",
                reviewer="human:b", supersedes_action_id=approve.id, duplicate_of_signal_id=signal_67.id,
            )
            s.commit()
            assertion.signal_id = signal_67.id
            s.commit()
            aid = assertion.id
        engine.dispose()

        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid, action="CONFIRM_DISTINCT_SIGNAL"))
        assert result.action_eligible is False
        assert "ALREADY_LINKED" in result.action_refusal_reason
        assert result.linked_signal_id == signal_67_id


# ---------------------------------------------------------------------------
# 42. Real SA81/Signal44 legacy shape - the new direct-unique-source anchor
# (docs/architecture: rwi-legacy-signal-reconciliation-gap-design and its own
# real-DB blast-radius review) makes MARK_DUPLICATE reachable through this
# same, unmodified CLI for a legacy Signal that has zero supporting
# SourceAssertions, without any change to this script itself.
# ---------------------------------------------------------------------------


class TestDirectUniqueSourceAnchorReachability:
    def _legacy_fixture(self, tmp_path, name="legacy.db"):
        db = _full_schema_database(tmp_path, name)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s, name="Greenville Downtown", code="GMU")
            source = _source(s, title="USAspending grant: Greenville Airport Commission")
            legacy_signal = _signal(
                s, airport, source_id=source.id, title="USAspending grant - $8.3M, FY2022",
                category="new_installation",
            )
            legacy_signal_id = legacy_signal.id
            assertion = _governed_assertion(s, source, airport, approved=False)
            assertion_id = assertion.id
        engine.dispose()
        return db, assertion_id, legacy_signal_id

    def test_previously_clear_to_create_becomes_possible_match(self, tmp_path):
        db, aid, sid = self._legacy_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(database=db, source_assertion_id=aid))
        assert result.reconciliation_outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH"
        assert result.candidate_signal_ids == (sid,)
        assert any("identity_anchor:direct_unique_source" in r for r in result.anchor_reasons)
        assert result.current_fingerprint is not None

    def test_mark_duplicate_is_now_eligible_in_dry_run(self, tmp_path):
        db, aid, sid = self._legacy_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
        ))
        assert result.action_eligible is True
        assert result.written is False  # dry run - no --allow-database-write

    def test_no_write_occurs_without_allow_database_write(self, tmp_path):
        db, aid, sid = self._legacy_fixture(tmp_path)
        before = _sha(db)
        cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
        ))
        assert _sha(db) == before

    def test_real_write_links_signal_id_and_records_exactly_one_reviewer_action(self, tmp_path):
        db, aid, sid = self._legacy_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:tester", reason="Same grant/source as the existing legacy Signal.",
            allow_database_write=True,
        ))
        assert result.written is True
        assert result.written_linked_signal_id == sid

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, aid)
            assert assertion.signal_id == sid
            actions = s.query(ReviewerAction).filter_by(source_assertion_id=aid).all()
            assert len(actions) == 1
            assert actions[0].action == "MARK_DUPLICATE"
            assert actions[0].duplicate_of_signal_id == sid
            # No auto-mutation, no auto-retirement, no second Signal.
            signal = s.get(Signal, sid)
            assert signal.title == "USAspending grant - $8.3M, FY2022"
            assert signal.category == "new_installation"
            assert s.query(Signal).count() == 1
        engine.dispose()


# ---------------------------------------------------------------------------
# 43. MARK_DUPLICATE effective-identity gate (rwi-mark-duplicate-upstream-
# governance-gate design/review, Option B): a SourceAssertion with a genuine
# structural anchor but ungoverned identity (EB5 INSUFFICIENT_IDENTITY) must
# be refused, surfaced by this same, otherwise-unmodified CLI.
# ---------------------------------------------------------------------------


class TestMarkDuplicateEffectiveIdentityGate:
    def _ungoverned_legacy_fixture(self, tmp_path, name="ungoverned.db"):
        db = _full_schema_database(tmp_path, name)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = _airport(s, name="Ungoverned Airport", code="UNG")
            source = _source(s, title="USAspending grant: ungoverned")
            legacy_signal = _signal(s, airport, source_id=source.id, title="Legacy signal")
            legacy_signal_id = legacy_signal.id
            # Deliberately NOT _governed_assertion(): no identity_guard_decision,
            # no legacy identity attestation - EB5 falls back to
            # INSUFFICIENT_IDENTITY, the SA76-shaped real control case.
            assertion = SourceAssertion(
                source=source, airport=airport, assertion_type="project_construction",
                raw_relevant_text="ENGINEERED MATERIAL ARRESTING SYSTEM", source_record_identifier="rec-ungoverned",
            )
            s.add(assertion)
            s.commit()
            assertion_id = assertion.id
        engine.dispose()
        return db, assertion_id, legacy_signal_id

    def test_dry_run_shows_effective_identity_refusal(self, tmp_path):
        db, aid, sid = self._ungoverned_legacy_fixture(tmp_path)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
        ))
        assert result.reconciliation_outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH"
        assert result.action_eligible is False
        assert "EFFECTIVE_IDENTITY_NOT_CONFIRMED" in result.action_refusal_reason
        assert "INSUFFICIENT_IDENTITY" in result.action_refusal_reason

    def test_real_write_attempt_is_also_refused_and_writes_nothing(self, tmp_path):
        db, aid, sid = self._ungoverned_legacy_fixture(tmp_path)
        before = _sha(db)
        result = cli.run_review(cli.ReviewConfig(
            database=db, source_assertion_id=aid, action="MARK_DUPLICATE", duplicate_of_signal_id=sid,
            reviewer="human:tester", reason="Attempted duplicate link.", allow_database_write=True,
        ))
        assert result.written is False
        assert result.action_eligible is False
        assert _sha(db) == before

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, aid)
            assert assertion.signal_id is None
            assert s.query(ReviewerAction).filter_by(source_assertion_id=aid).count() == 0
        engine.dispose()
