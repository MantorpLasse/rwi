"""Tests for scripts/resolve_source_assertion_identity.py (KAR3,
docs/architecture/rwi-known-airport-ambiguity-resolution-design.md).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db. Modeled directly on
tests/test_review_unknown_airport_candidate.py's own `run_review()`-direct-
call convention.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Source, SourceAssertion
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.services.evidence_attachment_guard import EvidenceBag
from app.services.evidence_bag_serialization import hash_serialized_evidence_bag, serialize_evidence_bag
from scripts.resolve_source_assertion_identity import ResolveSourceAssertionIdentityConfig, run_resolve


def _make_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="St. Paul Downtown Airport", country="USA", iata_code="STP", icao_code="KSTP", faa_code="STP")
        s.add(airport)
        s.commit()
        source = Source(title="Test Source", source_type="web_discovery")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            source_locator="item-1", artifact_identity="doc-1", raw_fragment_hash="hash-1",
            identity_guard_decision="REVIEW_REQUIRED", identity_guard_reason="ambiguous across candidates",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        s.add(assertion)
        s.commit()
        bag = EvidenceBag(
            names=frozenset({"St. Paul Downtown Airport"}), runway_ends=frozenset({"14", "32"}),
            runway_pairs=frozenset({"14/32"}), document_title="Test doc",
        )
        serialized = serialize_evidence_bag(bag)
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=serialized,
            evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
        )
        s.add(snapshot)
        s.commit()
        assertion_id, airport_id = assertion.id, airport.id
    engine.dispose()
    return assertion_id, airport_id


class TestInspect:
    def test_inspect_shows_original_decision_and_evidence(self, tmp_path):
        db = tmp_path / "inspect.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(database=db, source_assertion_id=assertion_id))
        assert result.assertion_found is True
        assert result.identity_guard_decision == "REVIEW_REQUIRED"
        assert "St. Paul Downtown Airport" in result.evidence_names
        assert "14/32" in result.evidence_runway_pairs
        assert result.airport_id is None
        assert result.resolution_history == []

    def test_inspect_nonexistent_assertion_blocks(self, tmp_path):
        db = tmp_path / "inspect_missing.db"
        _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(database=db, source_assertion_id=999999))
        assert result.assertion_found is False
        assert result.blockers


class TestDryRunAndWriteGate:
    def test_dry_run_creates_zero_rows(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="name+topology match", matched_airport_id=airport_id,
            allow_database_write=False,
        ))
        assert result.action_eligible is True
        assert result.written is False
        followup = run_resolve(ResolveSourceAssertionIdentityConfig(database=db, source_assertion_id=assertion_id))
        assert followup.airport_id is None
        assert followup.resolution_history == []

    def test_write_gate_required_to_actually_write(self, tmp_path):
        db = tmp_path / "gate.db"
        assertion_id, airport_id = _make_db(db)
        run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="x", matched_airport_id=airport_id, allow_database_write=False,
        ))
        followup = run_resolve(ResolveSourceAssertionIdentityConfig(database=db, source_assertion_id=assertion_id))
        assert followup.airport_id is None

    def test_execute_with_write_gate_persists(self, tmp_path):
        db = tmp_path / "execute.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="name+topology match", matched_airport_id=airport_id,
            allow_database_write=True,
        ))
        assert result.written is True
        assert result.airport_id == airport_id
        followup = run_resolve(ResolveSourceAssertionIdentityConfig(database=db, source_assertion_id=assertion_id))
        assert followup.airport_id == airport_id
        assert len(followup.resolution_history) == 1
        assert followup.resolution_history[0]["action"] == "ATTACH_TO_EXISTING_AIRPORT"


class TestCliSafety:
    def test_missing_target_airport_blocked(self, tmp_path):
        db = tmp_path / "notarget.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "requires matched_airport_id" in result.action_refusal_reason

    def test_wrong_target_airport_id_blocked(self, tmp_path):
        db = tmp_path / "wrongtarget.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="x", matched_airport_id=999999, allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "does not exist" in result.action_refusal_reason

    def test_nonexistent_assertion_blocked(self, tmp_path):
        db = tmp_path / "noassertion.db"
        _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=999999, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="x", matched_airport_id=1, allow_database_write=True,
        ))
        assert result.assertion_found is False
        assert result.blockers

    def test_already_resolved_assertion_blocked(self, tmp_path):
        db = tmp_path / "resolved.db"
        assertion_id, airport_id = _make_db(db)
        run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
            reviewer="tester", reason="x", matched_airport_id=airport_id, allow_database_write=True,
        ))
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="REJECT_ATTACHMENT",
            reviewer="tester", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "already resolved" in result.action_refusal_reason

    def test_candidate_linked_assertion_blocked(self, tmp_path):
        db = tmp_path / "candidatelinked.db"
        assertion_id, airport_id = _make_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = s.get(SourceAssertion, assertion_id)
            assertion.unknown_airport_candidate_id = 42
            s.commit()
        engine.dispose()
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
            reviewer="tester", reason="x", allow_database_write=True,
        ))
        assert result.action_eligible is False
        assert "governed exclusively by UAC4" in result.action_refusal_reason

    def test_reject_with_target_supplied_blocked(self, tmp_path):
        db = tmp_path / "rejectwithtarget.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="REJECT_ATTACHMENT",
            reviewer="tester", reason="x", matched_airport_id=airport_id, allow_database_write=True,
        ))
        assert result.action_eligible is False

    def test_defer_with_target_supplied_blocked(self, tmp_path):
        db = tmp_path / "deferwithtarget.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
            reviewer="tester", reason="x", matched_airport_id=airport_id, allow_database_write=True,
        ))
        assert result.action_eligible is False

    def test_missing_reviewer_blocked(self, tmp_path):
        db = tmp_path / "noreviewer.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
            reviewer=None, reason="x", allow_database_write=True,
        ))
        assert result.blockers

    def test_missing_reason_blocked(self, tmp_path):
        db = tmp_path / "noreason.db"
        assertion_id, airport_id = _make_db(db)
        result = run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, action="DEFER_IDENTITY_REVIEW",
            reviewer="tester", reason=None, allow_database_write=True,
        ))
        assert result.blockers

    def test_inspect_only_never_writes(self, tmp_path):
        """No --action at all: pure inspection, must never write regardless
        of --allow-database-write."""
        db = tmp_path / "inspectonly.db"
        assertion_id, airport_id = _make_db(db)
        run_resolve(ResolveSourceAssertionIdentityConfig(
            database=db, source_assertion_id=assertion_id, allow_database_write=True,
        ))
        followup = run_resolve(ResolveSourceAssertionIdentityConfig(database=db, source_assertion_id=assertion_id))
        assert followup.airport_id is None
        assert followup.resolution_history == []


class TestMainReturnCode:
    def test_main_returns_1_on_blockers(self, tmp_path, capsys):
        from scripts.resolve_source_assertion_identity import main
        db = tmp_path / "maingate.db"
        _make_db(db)
        code = main(["--database", str(db), "--source-assertion-id", "999999"])
        assert code == 1

    def test_main_returns_0_on_clean_inspect(self, tmp_path, capsys):
        from scripts.resolve_source_assertion_identity import main
        db = tmp_path / "mainclean.db"
        assertion_id, airport_id = _make_db(db)
        code = main(["--database", str(db), "--source-assertion-id", str(assertion_id)])
        assert code == 0

    def test_main_returns_1_when_action_ineligible(self, tmp_path, capsys):
        from scripts.resolve_source_assertion_identity import main
        db = tmp_path / "mainineligible.db"
        assertion_id, airport_id = _make_db(db)
        code = main([
            "--database", str(db), "--source-assertion-id", str(assertion_id),
            "--action", "ATTACH_TO_EXISTING_AIRPORT", "--reviewer", "tester", "--reason", "x",
            "--matched-airport-id", "999999",
        ])
        assert code == 1
