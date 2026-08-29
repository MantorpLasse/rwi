"""Tests for scripts/record_airport_alias.py (docs/architecture, "RWI -
Governed Canonical Airport Aliases - Cross-Script Identity Design"
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
from app.models.airport_alias import AirportAlias
from scripts.record_airport_alias import RecordAirportAliasConfig, run_record

_EXCERPT = "테스트공항(Test Airport) is the official name."
_ALIAS = "테스트공항"


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

        result = run_record(RecordAirportAliasConfig(database=db, airport_id=airport_id))

        assert result.airport_found is True
        assert result.airport_name == "Test Airport"
        assert result.currently_admitted_aliases == ()
        assert result.alias_history == []

    def test_inspect_causes_zero_writes(self, tmp_path):
        db = tmp_path / "inspect.db"
        airport_id, _source_id, _assertion_id = _make_db(db)
        before_bytes = db.read_bytes()

        run_record(RecordAirportAliasConfig(database=db, airport_id=airport_id))

        assert db.read_bytes() == before_bytes

    def test_inspect_nonexistent_airport_is_blocked(self, tmp_path):
        db = tmp_path / "inspect.db"
        _make_db(db)

        result = run_record(RecordAirportAliasConfig(database=db, airport_id=999999))

        assert result.airport_found is False
        assert result.blockers

    def test_config_has_no_decision_field(self):
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RecordAirportAliasConfig)}
        forbidden = {"decision", "identity_guard_decision", "expected_decision", "force_attach", "override"}
        assert forbidden.isdisjoint(field_names)


class TestDryRun:
    def test_dry_run_shows_impact_preview_and_performs_zero_writes(self, tmp_path):
        db = tmp_path / "dryrun.db"
        airport_id, source_id, assertion_id = _make_db(db)
        before_bytes = db.read_bytes()

        result = run_record(RecordAirportAliasConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            alias=_ALIAS, evidence_excerpt=_EXCERPT, analyst="human:tester",
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

        result = run_record(RecordAirportAliasConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            alias=_ALIAS, evidence_excerpt=_EXCERPT, analyst="human:tester",
        ))

        assert result.decision_eligible is False
        assert "reliability" in (result.decision_refusal_reason or "").lower()


class TestWrite:
    def test_write_requires_explicit_authorization(self, tmp_path):
        db = tmp_path / "write.db"
        airport_id, source_id, assertion_id = _make_db(db)

        result = run_record(RecordAirportAliasConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            alias=_ALIAS, evidence_excerpt=_EXCERPT, analyst="human:tester",
            allow_database_write=False,
        ))

        assert result.written is False
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(AirportAlias).count() == 0
        engine.dispose()

    def test_write_with_authorization_persists(self, tmp_path):
        db = tmp_path / "write.db"
        airport_id, source_id, assertion_id = _make_db(db)

        result = run_record(RecordAirportAliasConfig(
            database=db, airport_id=airport_id, source_id=source_id, source_assertion_id=assertion_id,
            alias=_ALIAS, evidence_excerpt=_EXCERPT, analyst="human:tester",
            allow_database_write=True,
        ))

        assert result.written is True
        assert result.written_alias_id is not None
        assert _ALIAS in result.currently_admitted_aliases

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assert s.query(AirportAlias).count() == 1
        engine.dispose()

    def test_operates_on_exactly_one_airport_one_alias_per_invocation(self):
        import inspect
        sig = inspect.signature(RecordAirportAliasConfig)
        assert "airport_ids" not in sig.parameters
        assert "aliases" not in sig.parameters
        assert "bulk" not in sig.parameters
