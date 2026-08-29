"""Tests for scripts/record_manual_identity_evidence.py (docs/architecture,
"RWI - New Source Family Manual Identity Evidence - Architecture Design"
mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db. Modeled directly on
tests/test_resolve_source_assertion_legacy_identity.py's own
`run_resolve()`-direct-call convention.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Source, SourceAssertion
from app.models.manual_identity_evidence import ManualIdentityEvidence
from scripts.record_manual_identity_evidence import (
    RecordManualIdentityEvidenceConfig,
    run_record,
)

_EXCERPT = "Test Airport in Test City is getting a new EMAS installation this year."


def _make_db(path, *, extra_assertion_kwargs=None):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Test Airport", city="Test City", country="Testland")
        s.add(airport)
        s.commit()
        source = Source(title="Test news article", source_type="news")
        s.add(source)
        s.commit()
        kwargs = dict(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text=_EXCERPT, raw_product_type="EMAS",
            source_record_identifier="rec-1", evidence_quality="direct_strong",
        )
        if extra_assertion_kwargs:
            kwargs.update(extra_assertion_kwargs)
        assertion = SourceAssertion(**kwargs)
        s.add(assertion)
        s.commit()
        assertion_id, airport_id, source_id = assertion.id, airport.id, source.id
    engine.dispose()
    return assertion_id, airport_id, source_id


class TestInspect:
    def test_inspect_shows_baseline_eligibility_and_current_eb5_state(self, tmp_path):
        db = tmp_path / "inspect.db"
        assertion_id, airport_id, _source_id = _make_db(db)

        result = run_record(RecordManualIdentityEvidenceConfig(database=db, source_assertion_id=assertion_id))

        assert result.assertion_found is True
        assert result.airport_id == airport_id
        assert result.baseline_eligible is True
        assert result.baseline_eligibility_blocker is None
        assert result.current_effective_decision == "INSUFFICIENT_IDENTITY"

    def test_inspect_causes_zero_writes(self, tmp_path):
        db = tmp_path / "inspect.db"
        assertion_id, _airport_id, _source_id = _make_db(db)
        before_bytes = db.read_bytes()

        run_record(RecordManualIdentityEvidenceConfig(database=db, source_assertion_id=assertion_id))

        assert db.read_bytes() == before_bytes

    def test_inspect_nonexistent_assertion_is_blocked(self, tmp_path):
        db = tmp_path / "inspect.db"
        _make_db(db)

        result = run_record(RecordManualIdentityEvidenceConfig(database=db, source_assertion_id=999999))

        assert result.assertion_found is False
        assert result.blockers

    def test_inspect_never_shows_a_decision_choice(self, tmp_path):
        """Structural proof: the config dataclass has no field for an
        identity decision/outcome/override of any kind."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RecordManualIdentityEvidenceConfig)}
        forbidden = {"decision", "identity_guard_decision", "expected_decision", "force_attach", "override"}
        assert forbidden.isdisjoint(field_names)


class TestDryRun:
    def test_dry_run_shows_eligible_and_performs_zero_writes(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, _airport_id, source_id = _make_db(db)
        before_bytes = db.read_bytes()

        result = run_record(RecordManualIdentityEvidenceConfig(
            database=db, source_assertion_id=assertion_id, source_id=source_id,
            analyst="human:tester", evidence_excerpt=_EXCERPT,
            raw_airport_name="Test Airport", raw_city="Test City",
        ))

        assert result.decision_eligible is True
        assert result.written is False
        assert db.read_bytes() == before_bytes

    def test_dry_run_requires_excerpt_contained_in_raw_relevant_text(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, _airport_id, source_id = _make_db(db)

        result = run_record(RecordManualIdentityEvidenceConfig(
            database=db, source_assertion_id=assertion_id, source_id=source_id,
            analyst="human:tester", evidence_excerpt="This text never appears in raw_relevant_text.",
        ))

        assert result.blockers
        assert any("does not occur" in b for b in result.blockers)

    def test_dry_run_fails_safely_when_no_raw_relevant_text_stored(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, _airport_id, source_id = _make_db(db, extra_assertion_kwargs={
            "raw_relevant_text": None, "source_locator": "loc-1", "artifact_identity": "art-1",
            "raw_fragment_hash": "hash-1", "source_record_identifier": None,
        })

        result = run_record(RecordManualIdentityEvidenceConfig(
            database=db, source_assertion_id=assertion_id, source_id=source_id,
            analyst="human:tester", evidence_excerpt=_EXCERPT,
        ))

        assert result.blockers
        assert any("no preserved raw_relevant_text" in b for b in result.blockers)

    def test_dry_run_refuses_mismatched_source(self, tmp_path):
        db = tmp_path / "dryrun.db"
        assertion_id, _airport_id, _source_id = _make_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            other = Source(title="Other", source_type="news")
            s.add(other)
            s.commit()
            other_id = other.id
        engine.dispose()

        result = run_record(RecordManualIdentityEvidenceConfig(
            database=db, source_assertion_id=assertion_id, source_id=other_id,
            analyst="human:tester", evidence_excerpt=_EXCERPT, raw_airport_name="Test Airport",
        ))

        assert result.decision_eligible is False
        assert "source_id" in (result.decision_refusal_reason or "")


class TestWrite:
    def test_write_requires_explicit_authorization(self, tmp_path):
        db = tmp_path / "write.db"
        assertion_id, _airport_id, source_id = _make_db(db)

        result = run_record(RecordManualIdentityEvidenceConfig(
            database=db, source_assertion_id=assertion_id, source_id=source_id,
            analyst="human:tester", evidence_excerpt=_EXCERPT,
            raw_airport_name="Test Airport", raw_city="Test City",
            allow_database_write=False,
        ))

        assert result.written is False
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ManualIdentityEvidence).count() == 0
        engine.dispose()

    def test_write_with_authorization_persists_and_governs(self, tmp_path):
        db = tmp_path / "write.db"
        assertion_id, _airport_id, source_id = _make_db(db)

        result = run_record(RecordManualIdentityEvidenceConfig(
            database=db, source_assertion_id=assertion_id, source_id=source_id,
            analyst="human:tester", evidence_excerpt=_EXCERPT,
            raw_airport_name="Test Airport", raw_city="Test City",
            allow_database_write=True,
        ))

        assert result.written is True
        assert result.written_manual_identity_evidence_id is not None
        assert result.identity_guard_decision_after == "ATTACH_CONFIRMED"
        assert result.effective_decision_after == "ATTACH_CONFIRMED"

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(ManualIdentityEvidence).count() == 1
            assertion = s.get(SourceAssertion, assertion_id)
            assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"
        engine.dispose()

    def test_operates_on_exactly_one_source_assertion_per_invocation(self):
        import inspect
        sig = inspect.signature(RecordManualIdentityEvidenceConfig)
        assert "source_assertion_ids" not in sig.parameters
        assert "bulk" not in sig.parameters
