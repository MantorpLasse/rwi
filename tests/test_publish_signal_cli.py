"""Tests for scripts/publish_signal.py ("RWI - Signal Publication Governance
- Design + Implementation" mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing in
this file ever opens data/runway_safe.db.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.models.signal_publication_action import SignalPublicationAction
from app.services.cross_source_alias_attestation import record_cross_source_alias_attestation
from app.services.governed_signal_creation import create_signal_from_approved_review
from app.services.reviewer_action_persistence import record_reviewer_action
from scripts.publish_signal import PublishSignalConfig, main, run_publish


def _make_signal69_shaped_db(path) -> int:
    """Builds a Signal69-shaped governed Signal (published=False) in a
    temp-file database; returns its id. Mirrors
    tests/test_signal_publication_action.py's own `_signal69_shaped()`
    in-memory fixture, adapted to a file-backed engine so the CLI (which
    opens its own connection to the same file path) can see it."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport); s.commit()

        alias_source = Source(title="Alias registry", source_type="Authority", reliability_level="official")
        s.add(alias_source); s.commit()
        alias_assertion = SourceAssertion(
            source_id=alias_source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text="테스트공항(Test Airport) official.", source_record_identifier="rec-alias",
            evidence_quality="direct_strong",
        )
        s.add(alias_assertion); s.commit()
        alias = AirportAlias(
            airport_id=airport.id, alias="테스트공항", source_id=alias_source.id,
            source_assertion_id=alias_assertion.id, evidence_excerpt="테스트공항(Test Airport) official.",
            analyst="human:tester", evidence_class="AUTHORITATIVE_DIRECT", status="ADMITTED",
        )
        s.add(alias); s.commit()

        council_source = Source(
            title="Independent council", source_type="Authority", reliability_level="official",
            url="https://example.gov/record",
        )
        s.add(council_source); s.commit()
        assertion = SourceAssertion(
            source_id=council_source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="테스트공항 EMAS project underway.", source_record_identifier="rec-council",
            evidence_quality="direct_strong", identity_guard_decision="ATTACH_PROVISIONAL",
            intelligence_review_decision="REVIEW_REQUIRED", promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
        )
        s.add(assertion); s.commit()

        record_cross_source_alias_attestation(
            s, source_assertion_id=assertion.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="test",
        )
        s.commit()
        record_reviewer_action(
            s, assertion, action="APPROVE_SIGNAL", reason="Effectively confirmed, human-approved.",
            reviewer="human:tester",
        )
        s.commit()
        result = create_signal_from_approved_review(
            s, assertion, title="Test Airport EMAS installation", category="new_installation",
            confidence="medium", status="identified",
        )
        s.commit()
        signal_id = result.signal.id
    engine.dispose()
    return signal_id


class TestDryRun:
    def test_preview_shown_no_write_by_default(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(database=db, signal_id=signal_id))
        assert result.preview is not None
        assert result.preview["publish_eligible"] is True
        assert result.preview["current_published"] is False
        assert result.written is False

    def test_preview_causes_zero_writes(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        before_bytes = db.read_bytes()
        run_publish(PublishSignalConfig(database=db, signal_id=signal_id))
        assert db.read_bytes() == before_bytes

    def test_dry_run_even_with_reviewer_and_reason_does_not_write(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer="human:tester", reason="test", apply=False,
        ))
        assert result.written is False
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(SignalPublicationAction).count() == 0
            assert s.get(Signal, signal_id).published is False


class TestWrite:
    def test_apply_without_allow_database_write_does_not_write(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer="human:tester", reason="test",
            apply=True, allow_database_write=False,
        ))
        assert result.written is False
        assert any("allow-database-write" in b for b in result.blockers)

    def test_allow_database_write_without_apply_does_not_write(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer="human:tester", reason="test",
            apply=False, allow_database_write=True,
        ))
        assert result.written is False

    def test_missing_reviewer_refused(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer=None, reason="test",
            apply=True, allow_database_write=True,
        ))
        assert result.written is False
        assert any("reviewer" in b for b in result.blockers)

    def test_missing_reason_refused(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer="human:tester", reason=None,
            apply=True, allow_database_write=True,
        ))
        assert result.written is False
        assert any("reason" in b for b in result.blockers)

    def test_full_apply_writes_and_publishes(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer="human:lkarlsson@gmail.com",
            reason="Governed review approved.", apply=True, allow_database_write=True,
        ))
        assert result.written is True
        assert result.changed is True
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            signal = s.get(Signal, signal_id)
            assert signal.published is True
            actions = s.query(SignalPublicationAction).all()
            assert len(actions) == 1
            assert actions[0].action == "PUBLISH"
            assert actions[0].reviewer == "human:lkarlsson@gmail.com"

    def test_unpublish_action(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, reviewer="human:a", reason="publish first",
            apply=True, allow_database_write=True,
        ))
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=signal_id, action="UNPUBLISH", reviewer="human:b", reason="retract",
            apply=True, allow_database_write=True,
        ))
        assert result.written is True
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.get(Signal, signal_id).published is False
            assert s.query(SignalPublicationAction).count() == 2

    def test_no_bulk_or_wildcard_signal_selector_exists(self):
        """Structural guarantee: the CLI's parser accepts exactly one
        required --signal-id (an int), no --all/--wildcard/--eligible-only
        flag exists at all."""
        import scripts.publish_signal as cli
        parser = cli._parser()
        dest_names = {action.dest for action in parser._actions}
        assert "signal_id" in dest_names
        assert not any(name in dest_names for name in ("all", "wildcard", "eligible_only", "bulk"))

    def test_unknown_signal_id_refused(self, tmp_path):
        db = tmp_path / "test.db"
        _make_signal69_shaped_db(db)
        result = run_publish(PublishSignalConfig(
            database=db, signal_id=999999, reviewer="human:tester", reason="test",
            apply=True, allow_database_write=True,
        ))
        assert result.written is False
        assert any("no Signal with id" in b for b in result.blockers)


class TestMain:
    def test_main_returns_zero_on_successful_dry_run(self, tmp_path):
        db = tmp_path / "test.db"
        signal_id = _make_signal69_shaped_db(db)
        code = main(["--database", str(db), "--signal-id", str(signal_id)])
        assert code == 0

    def test_main_returns_nonzero_on_unknown_signal(self, tmp_path):
        db = tmp_path / "test.db"
        _make_signal69_shaped_db(db)
        code = main(["--database", str(db), "--signal-id", "999999"])
        assert code == 1
