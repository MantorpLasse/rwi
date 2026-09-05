"""Tests for scripts/review_staged_funding_evidence.py (RWI HQ "Commander
Staged Funding Review CLI"). Every test uses an isolated file-based SQLite
database (never the real data/runway_safe.db) - a file, not :memory:,
because the CLI itself builds its own engine from a --database path
argument, matching this repository's own established CLI-testing
convention (e.g. tests/test_research_airport_clue.py)."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import scripts.review_staged_funding_evidence as cli
from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion

_APPROVE_FIELDS = ["--title", "Test Signal", "--category", "replacement", "--confidence", "medium"]


def _database(tmp_path, name="test.db"):
    db = tmp_path / name
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return db


def _seed_funding_assertion(
    db_path, *, external_id="faa_aip:https://faa.gov/x.pdf#ZZZ#deadbeef", airport_id=None,
    source_record_identifier="fixture-1", raw_relevant_text="Construct EMAS. Total AIP: 1000000",
) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        if airport_id is None:
            airport = Airport(name="Test Airport", iata_code="ZZZ", country="USA")
            session.add(airport)
            session.flush()
            airport_id = airport.id
        source = Source(title="AIP grant: test", source_type="aip_grant", external_id=external_id)
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport_id, assertion_type="project_construction",
            raw_relevant_text=raw_relevant_text, evidence_quality="unverified_candidate",
            review_state="unreviewed", identity_guard_decision=None, intelligence_review_decision=None,
            promotion_policy_decision=None, source_record_identifier=source_record_identifier,
        )
        session.add(assertion)
        session.commit()
        return assertion.id


def _seed_discovery_assertion(db_path, *, airport_id=None) -> int:
    """The real, confirmed SA258 shape - Selection/KEEP-derived, not funding."""
    return _seed_funding_assertion(
        db_path, external_id="discovery:generic_web:deadbeef:cafef00d", airport_id=airport_id,
        source_record_identifier="fixture-discovery-1", raw_relevant_text="Phase 1 EMAS East Runway",
    )


def _counts(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        return (
            session.scalar(select(Airport)) and len(session.scalars(select(Airport)).all()),
            len(session.scalars(select(Source)).all()),
            len(session.scalars(select(SourceAssertion)).all()),
            len(session.scalars(select(Signal)).all()),
            len(session.scalars(select(ReviewerAction)).all()),
        )


def _file_sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- Preview is read-only by default -----------------------------------------


def test_preview_default_is_read_only(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    before = _file_sha(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    after = _file_sha(db)
    assert exit_code == 0
    assert before == after
    assert _counts(db) == (1, 1, 1, 0, 0)


def test_apply_without_allow_database_write_is_refused_and_read_only(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    before = _file_sha(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--apply",
    ])
    assert exit_code == 2
    assert _file_sha(db) == before


def test_allow_database_write_without_apply_is_refused_and_read_only(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    before = _file_sha(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--allow-database-write",
    ])
    assert exit_code == 2
    assert _file_sha(db) == before


# --- Eligibility integration --------------------------------------------------


def test_faa_aip_fixture_eligible(tmp_path, capsys):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db, external_id="faa_aip:https://faa.gov/x.pdf#ZZZ#deadbeef")
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    assert exit_code == 0
    assert "Eligibility: PASS" in capsys.readouterr().out


def test_usaspending_fixture_eligible(tmp_path, capsys):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db, external_id="usaspending:CONT_AWD_TEST_1")
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    assert exit_code == 0
    assert "Eligibility: PASS" in capsys.readouterr().out


def test_sa258_shaped_discovery_fixture_rejected(tmp_path, capsys):
    """The mandatory regression test protecting the exact defect that
    triggered the eligibility-hardening slice - checked BEFORE any
    ReviewerAction or Signal operation, in both preview and apply."""
    db = _database(tmp_path)
    aid = _seed_discovery_assertion(db)
    before = _file_sha(db)

    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    assert exit_code == 2
    assert "Eligibility: FAIL" in capsys.readouterr().err
    assert _file_sha(db) == before
    assert _counts(db) == (1, 1, 1, 0, 0)

    # Also attempted with --apply --allow-database-write - must be refused
    # identically, before any write.
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--apply", "--allow-database-write",
    ])
    assert exit_code == 2
    assert _file_sha(db) == before
    assert _counts(db) == (1, 1, 1, 0, 0)


def test_sa258_shaped_discovery_fixture_rejected_for_approve_signal_too(tmp_path):
    db = _database(tmp_path)
    aid = _seed_discovery_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write",
    ])
    assert exit_code == 2
    assert _counts(db) == (1, 1, 1, 0, 0)


def test_unknown_namespace_rejected(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db, external_id="some_other_namespace:12345")
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    assert exit_code == 2


def test_missing_source_assertion_rejected(tmp_path):
    db = _database(tmp_path)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", "9999", "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    assert exit_code == 2


# --- Required inputs -----------------------------------------------------


def test_missing_reviewer_rejected_by_argparse(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    with pytest.raises(SystemExit):
        cli.main([
            "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
            "--reason", "x",
        ])


def test_missing_reason_rejected_by_argparse(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    with pytest.raises(SystemExit):
        cli.main([
            "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
            "--reviewer", "human:t",
        ])


def test_only_supported_action_subset_exposed():
    parser = cli._parser()
    action_action = next(a for a in parser._actions if a.dest == "action")
    assert set(action_action.choices) == {"APPROVE_SIGNAL", "MARK_DUPLICATE", "NEEDS_MORE_EVIDENCE", "REJECT_SIGNAL"}
    assert "DEFER" not in action_action.choices
    assert "CONFIRM_DISTINCT_SIGNAL" not in action_action.choices


def test_invalid_action_rejected_by_argparse(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    with pytest.raises(SystemExit):
        cli.main([
            "--database", str(db), "--source-assertion-id", str(aid), "--action", "DEFER",
            "--reviewer", "human:t", "--reason", "x",
        ])


def test_mark_duplicate_requires_duplicate_target(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "MARK_DUPLICATE",
        "--reviewer", "human:t", "--reason", "x",
    ])
    assert exit_code == 2


def test_duplicate_target_rejected_for_non_mark_duplicate_action(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--duplicate-of-signal-id", "1",
    ])
    assert exit_code == 2


def test_approve_signal_missing_title_rejected(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", "--category", "replacement", "--confidence", "medium",
    ])
    assert exit_code == 2


def test_approve_signal_missing_category_rejected(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", "--title", "T", "--confidence", "medium",
    ])
    assert exit_code == 2


def test_approve_signal_missing_confidence_rejected_by_argparse_or_cli(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", "--title", "T", "--category", "replacement",
    ])
    assert exit_code == 2


def test_approve_signal_disallowed_status_rejected(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS, "--status", "completed",
    ])
    assert exit_code == 2


# --- APPROVE_SIGNAL apply: atomic two-step, one commit -----------------------


def test_approve_signal_apply_records_reviewer_action_and_signal_atomically(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    assert exit_code == 0
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        actions = session.scalars(select(ReviewerAction)).all()
        signals = session.scalars(select(Signal)).all()
        assert len(actions) == 1
        assert actions[0].action == "APPROVE_SIGNAL"
        assert len(signals) == 1
        signal = signals[0]
        assert signal.title == "Test Signal"
        assert signal.published is False
        assert signal.runway_id is None
        assert signal.probability_score == 6.0  # DEFAULT_SCORE_BY_CONFIDENCE["medium"]
        assertion = session.get(SourceAssertion, aid)
        assert assertion.signal_id == signal.id


def test_signal_creation_failure_rolls_back_the_reviewer_action_too(tmp_path):
    """The core Part 9 requirement: a second, conflicting-signature
    APPROVE_SIGNAL apply must leave NEITHER a new ReviewerAction row NOR a
    second Signal - both rolled back together."""
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "first", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        before_action_count = len(session.scalars(select(ReviewerAction)).all())
        before_signal_count = len(session.scalars(select(Signal)).all())
    assert before_action_count == 1
    assert before_signal_count == 1

    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "conflicting",
        "--title", "DIFFERENT TITLE", "--category", "replacement", "--confidence", "medium",
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    assert exit_code == 1

    with Session(engine) as session:
        after_action_count = len(session.scalars(select(ReviewerAction)).all())
        after_signal_count = len(session.scalars(select(Signal)).all())
    # The ReviewerAction that WOULD have been appended is rolled back together
    # with the failed Signal creation - never a durable orphan.
    assert after_action_count == before_action_count
    assert after_signal_count == before_signal_count


def test_no_signal_without_prior_approve_signal_reviewer_action(tmp_path):
    """create_signal_from_lightweight_funding_review()'s own precondition -
    this CLI never bypasses it: NEEDS_MORE_EVIDENCE alone never creates a
    Signal, and a subsequent, separate APPROVE_SIGNAL apply is required."""
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert len(session.scalars(select(Signal)).all()) == 0
        assert session.get(SourceAssertion, aid).signal_id is None


# --- Other actions create only a ReviewerAction ------------------------------


def test_needs_more_evidence_creates_only_reviewer_action(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--apply", "--allow-database-write", "--skip-backup",
    ])
    assert exit_code == 0
    assert _counts(db) == (1, 1, 1, 0, 1)


def test_reject_signal_creates_only_reviewer_action(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "REJECT_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", "--apply", "--allow-database-write", "--skip-backup",
    ])
    assert exit_code == 0
    assert _counts(db) == (1, 1, 1, 0, 1)


def test_mark_duplicate_creates_only_reviewer_action_with_valid_target(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        signal = Signal(airport_id=1, title="Existing", category="replacement", confidence="medium", published=False)
        session.add(signal)
        session.commit()
        signal_id = signal.id

    exit_code = cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "MARK_DUPLICATE",
        "--reviewer", "human:t", "--reason", "x", "--duplicate-of-signal-id", str(signal_id),
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    assert exit_code == 0
    with Session(engine) as session:
        assert len(session.scalars(select(Signal)).all()) == 1  # the pre-existing one only
        action = session.scalars(select(ReviewerAction)).all()[0]
        assert action.action == "MARK_DUPLICATE"
        assert action.duplicate_of_signal_id == signal_id


# --- Idempotency / replay -----------------------------------------------------


def test_repeated_actions_preserve_append_only_audit_behavior(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    for _ in range(2):
        cli.main([
            "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
            "--reviewer", "human:t", "--reason", "x", "--apply", "--allow-database-write", "--skip-backup",
        ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert len(session.scalars(select(ReviewerAction)).all()) == 2


def test_same_signature_approve_signal_replay_reuses_existing_signal(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    for _ in range(2):
        exit_code = cli.main([
            "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
            "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
            "--apply", "--allow-database-write", "--skip-backup",
        ])
        assert exit_code == 0
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert len(session.scalars(select(Signal)).all()) == 1
        assert len(session.scalars(select(ReviewerAction)).all()) == 2  # append-only, two APPROVE_SIGNAL rows


def test_conflicting_signature_replay_preview_predicts_refusal_without_mutation(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    before = _file_sha(db)

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = cli.main([
            "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
            "--reviewer", "human:t", "--reason", "x",
            "--title", "DIFFERENT", "--category", "replacement", "--confidence", "medium",
        ])
    assert exit_code == 0  # preview itself succeeds - it is APPLY that would be refused
    assert "APPLY WOULD BE REFUSED" in buf.getvalue()
    assert _file_sha(db) == before


# --- Signal semantics ----------------------------------------------------


def test_published_always_false(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert session.scalars(select(Signal)).all()[0].published is False


def test_no_dollar_value_field_exposed_by_cli():
    parser = cli._parser()
    dest_names = {a.dest for a in parser._actions}
    assert "estimated_total_value_usd" not in dest_names
    assert "estimated_emas_value_usd" not in dest_names
    assert "supplier" not in dest_names
    assert "likely_supplier" not in dest_names
    assert "runway_id" not in dest_names


def test_probability_score_uses_evidence_strength_lookup(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", "--title", "T", "--category", "replacement",
        "--confidence", "high", "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert session.scalars(select(Signal)).all()[0].probability_score == 8.0


def test_runway_and_supplier_remain_null(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        signal = session.scalars(select(Signal)).all()[0]
        assert signal.runway_id is None
        assert signal.supplier is None
        assert signal.likely_supplier is None
        assert signal.estimated_total_value_usd is None
        assert signal.estimated_emas_value_usd is None


def test_grant_amount_in_evidence_text_not_copied_into_signal_value(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db, raw_relevant_text="Total AIP: 9999999.00")
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        signal = session.scalars(select(Signal)).all()[0]
        assert signal.estimated_total_value_usd is None
        assert signal.estimated_emas_value_usd is None


def test_no_heavy_governance_decisions_synthesized(tmp_path):
    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assertion = session.get(SourceAssertion, aid)
        assert assertion.identity_guard_decision is None
        assert assertion.intelligence_review_decision is None
        assert assertion.promotion_policy_decision is None


# --- Relation to staged queue --------------------------------------------


def test_promoted_assertion_naturally_leaves_staged_queue(tmp_path):
    from app.services.human_review_queue import list_staged_evidence_items

    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert [i.source_assertion_id for i in list_staged_evidence_items(session)] == [aid]

    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "APPROVE_SIGNAL",
        "--reviewer", "human:t", "--reason", "x", *_APPROVE_FIELDS,
        "--apply", "--allow-database-write", "--skip-backup",
    ])

    with Session(engine) as session:
        assert list_staged_evidence_items(session) == ()


def test_non_approve_action_does_not_remove_assertion_from_staged_queue(tmp_path):
    from app.services.human_review_queue import list_staged_evidence_items

    db = _database(tmp_path)
    aid = _seed_funding_assertion(db)
    cli.main([
        "--database", str(db), "--source-assertion-id", str(aid), "--action", "NEEDS_MORE_EVIDENCE",
        "--reviewer", "human:t", "--reason", "x", "--apply", "--allow-database-write", "--skip-backup",
    ])
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        assert [i.source_assertion_id for i in list_staged_evidence_items(session)] == [aid]
