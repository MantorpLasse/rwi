"""Tests for scripts/record_airport_identifier.py (docs/architecture,
"RWI - Governed Canonical Airport Identifiers - Architecture Design"
mission).

Every test uses an isolated temp-file SQLite database (tmp_path). Nothing
in this file ever opens data/runway_safe.db.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Source, SourceAssertion
from app.models.airport_identifier import AirportIdentifier
from scripts.record_airport_identifier import RecordAirportIdentifierConfig, run_record

_EXCERPT = "Test Airport (TST) is the code. TST(IATA)"


def _make_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        airport = Airport(name="Test Airport", country="Testland")
        s.add(airport)
        s.commit()
        source = Source(title="Official registry", source_type="government", reliability_level="official")
        s.add(source)
        s.commit()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text=_EXCERPT, source_record_identifier="rec-1", evidence_quality="direct_strong",
        )
        s.add(assertion)
        s.commit()
        airport_id, source_id, assertion_id = airport.id, source.id, assertion.id
    engine.dispose()
    return airport_id, source_id, assertion_id


class TestInspect:
    def test_inspect_shows_airport_identity_and_no_history(self, tmp_path):
        db = tmp_path / "inspect.db"
        airport_id, _source_id, _assertion_id = _make_db(db)

        result = run_record(RecordAirportIdentifierConfig(database=db, airport_id=airport_id))

        assert result.airport_found is True
        assert result.airport_name == "Test Airport"
        assert result.identifier_history == []

    def test_inspect_causes_zero_writes(self, tmp_path):
        db = tmp_path / "inspect.db"
        airport_id, _source_id, _assertion_id = _make_db(db)
        before_bytes = db.read_bytes()

        run_record(RecordAirportIdentifierConfig(database=db, airport_id=airport_id))

        assert db.read_bytes() == before_bytes

    def test_inspect_nonexistent_airport_is_blocked(self, tmp_path):
        db = tmp_path / "inspect.db"
        _make_db(db)

        result = run_record(RecordAirportIdentifierConfig(database=db, airport_id=999999))

        assert result.airport_found is False
        assert result.blockers

    def test_config_has_no_decision_field(self):
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RecordAirportIdentifierConfig)}
        forbidden = {"decision", "identity_guard_decision", "expected_decision", "force_attach", "override"}
        assert forbidden.isdisjoint(field_names)


class TestDryRun:
    def test_dry_run_shows_impact_preview_and_performs_zero_writes(self, tmp_path):
        db = tmp_path / "dryrun.db"
        airport_id, source_id, assertion_id = _make_db(db)
        before_bytes = db.read_bytes()

        result = run_record(RecordAirportIdentifierConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            identifier_type="IATA", identifier_value="TST", type_evidence_token="TST(IATA)",
            evidence_excerpt=_EXCERPT, analyst="human:tester",
        ))

        assert result.decision_eligible is True
        assert result.written is False
        assert isinstance(result.impact_preview, list)
        assert db.read_bytes() == before_bytes

    def test_dry_run_refuses_unreliable_source(self, tmp_path):
        db = tmp_path / "dryrun.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            airport = Airport(name="Test Airport", country="Testland")
            s.add(airport)
            s.commit()
            source = Source(title="News", source_type="news", reliability_level="unverified")
            s.add(source)
            s.commit()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
                raw_relevant_text=_EXCERPT, source_record_identifier="rec-1", evidence_quality="direct_strong",
            )
            s.add(assertion)
            s.commit()
            airport_id, source_id, assertion_id = airport.id, source.id, assertion.id
        engine.dispose()

        result = run_record(RecordAirportIdentifierConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            identifier_type="IATA", identifier_value="TST", type_evidence_token="TST(IATA)",
            evidence_excerpt=_EXCERPT, analyst="human:tester",
        ))

        assert result.decision_eligible is False
        assert "reliability" in (result.decision_refusal_reason or "").lower()


class TestWrite:
    def test_write_requires_explicit_authorization(self, tmp_path):
        db = tmp_path / "write.db"
        airport_id, source_id, assertion_id = _make_db(db)

        result = run_record(RecordAirportIdentifierConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            identifier_type="IATA", identifier_value="TST", type_evidence_token="TST(IATA)",
            evidence_excerpt=_EXCERPT, analyst="human:tester",
            allow_database_write=False,
        ))

        assert result.written is False
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(AirportIdentifier).count() == 0
            assert s.get(Airport, airport_id).iata_code is None
        engine.dispose()

    def test_write_with_authorization_persists_and_writes_column(self, tmp_path):
        db = tmp_path / "write.db"
        airport_id, source_id, assertion_id = _make_db(db)

        result = run_record(RecordAirportIdentifierConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            identifier_type="IATA", identifier_value="TST", type_evidence_token="TST(IATA)",
            evidence_excerpt=_EXCERPT, analyst="human:tester",
            allow_database_write=True,
        ))

        assert result.written is True
        assert result.written_identifier_id is not None
        assert result.column_written is True
        assert result.airport_iata == "TST"

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(AirportIdentifier).count() == 1
            assert s.get(Airport, airport_id).iata_code == "TST"
        engine.dispose()

    def test_operates_on_exactly_one_airport_one_identifier_per_invocation(self):
        import inspect
        sig = inspect.signature(RecordAirportIdentifierConfig)
        assert "airport_ids" not in sig.parameters
        assert "identifiers" not in sig.parameters
        assert "bulk" not in sig.parameters
