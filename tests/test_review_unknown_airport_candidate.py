"""Tests for scripts/review_unknown_airport_candidate.py (UAC5A) and its
interaction with the guard-replay-feasibility finding (UAC5B).

Every test uses an isolated tmp_path-scoped SQLite database file - nothing
here ever opens data/runway_safe.db (see TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_candidate_linked_source_assertion
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass, ObservationPolarity
from app.services.fleet_health_check import _build_source_assertion_review_states
from app.services.fleet_health_review_rules import evaluate_fh_f2, evaluate_fh_f3
from app.services.unknown_airport_candidate_persistence import (
    find_or_create_unknown_airport_candidate,
    record_unknown_airport_candidate_review,
)
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    record_unknown_airport_candidate_relevance_review,
)
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration
import scripts.review_unknown_airport_candidate as cli_module
from scripts.review_unknown_airport_candidate import (
    CANDIDATE_NOT_FOUND_BLOCKER,
    ERG_SCHEMA_MIGRATION_REQUIRED_BLOCKER,
    SCHEMA_MIGRATION_REQUIRED_BLOCKER,
    UnknownAirportCandidateReviewConfig,
    build_engine,
    check_erg_schema_readiness,
    main,
    render_result,
    run_review,
)
import scripts.migrate_unknown_airport_candidate_relevance_assessments_erg2 as erg2_migration
import scripts.migrate_unknown_airport_candidate_relevance_reviews_erg3 as erg3_migration

CONFIRM = "CONFIRM_EMAS_RELEVANT"
MARK_NOT = "MARK_NOT_EMAS_RELEVANT"
DEFER_RELEVANCE = "DEFER_RELEVANCE_REVIEW"


def _make_full_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return engine


def _make_pre_uac_db(path):
    """No unknown_airport_candidates/unknown_airport_candidate_reviews
    tables, no source_assertions.unknown_airport_candidate_id column -
    the genuine "neither UAC2A nor UAC2B applied" starting state. Builds
    the FULL current schema first (so source_assertions' own forward FK
    to unknown_airport_candidates resolves cleanly during create_all),
    then rebuilds source_assertions back to its pre-UAC2B shape via raw
    SQL and drops the two UAC1 tables - the same technique
    tests/test_unknown_airport_candidate_migration.py's own
    _pre_uac1_db() fixture uses, established during UAC2B's own review
    for the identical NoReferencedTableError this shortcut would
    otherwise hit."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    replacement = "source_assertions__pre"
    conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
    quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
    conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
    conn.execute("DROP TABLE source_assertions")
    conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
    conn.execute("DROP TABLE unknown_airport_candidate_reviews")
    conn.execute("DROP TABLE unknown_airport_candidates")
    conn.commit()
    conn.close()
    return create_engine(f"sqlite:///{path}")


def _seed_candidate_with_n_assertions(engine, *, n=1, raw_name="Foo Regional Airport"):
    with Session(engine) as session:
        candidate = find_or_create_unknown_airport_candidate(session, raw_name=raw_name).candidate
        session.commit()
        assertion_ids = []
        for i in range(n):
            fragment = CandidateFragment(
                artifact_identity=f"art-{raw_name}-{i}", source_locator="p1", raw_text=f"{raw_name} evidence {i}.",
            )
            linked = persist_candidate_linked_source_assertion(
                session, DiscoverySourceMetadata(document_identity=f"doc-{raw_name}-{i}", title="t"), fragment,
                unknown_airport_candidate_id=candidate.id,
            )
            assertion_ids.append(linked.source_assertion_id)
        session.commit()
        return candidate.id, tuple(assertion_ids)


def _seed_airport(engine, *, name="Real Airport", country="XX", **kwargs):
    with Session(engine) as session:
        airport = Airport(name=name, country=country, **kwargs)
        session.add(airport)
        session.commit()
        return airport.id


def _record_review(engine, candidate_id, **kwargs):
    with Session(engine) as session:
        candidate = session.get(UnknownAirportCandidate, candidate_id)
        review = record_unknown_airport_candidate_review(session, candidate, **kwargs)
        session.commit()
        return review.id


def _make_admission_eligible(engine, candidate_id, assertion_ids):
    """ERG4 fixture helper: persists an ERG2 A-class (admission-relevant)
    assessment linked to the given SourceAssertions, then records an ERG3
    CONFIRM_EMAS_RELEVANT review against it - the minimal state
    create_airport_from_approved_candidate()'s ERG4 gate requires before
    proceeding to its pre-existing UAC4 checks. `assertion_ids` must be
    non-empty."""
    with Session(engine) as session:
        candidate = session.get(UnknownAirportCandidate, candidate_id)
        assessment = persist_unknown_airport_candidate_relevance_assessment(
            session, candidate,
            observations=(EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="erg4 fixture"),),
            source_assertion_ids=tuple(assertion_ids),
        ).assessment
        session.commit()
        record_unknown_airport_candidate_relevance_review(
            session, candidate, basis_assessment_id=assessment.id,
            action="CONFIRM_EMAS_RELEVANT", reviewer="human:erg4-fixture", reason="erg4 fixture confirm",
        )
        session.commit()


def _persist_assessment(engine, candidate_id, observations, assertion_ids=(), context=None):
    with Session(engine) as session:
        candidate = session.get(UnknownAirportCandidate, candidate_id)
        kwargs = dict(session=session, candidate=candidate, observations=observations, source_assertion_ids=tuple(assertion_ids))
        result = persist_unknown_airport_candidate_relevance_assessment(**kwargs)
        session.commit()
        return result.assessment.id


def _record_relevance_review(engine, candidate_id, **kwargs):
    with Session(engine) as session:
        candidate = session.get(UnknownAirportCandidate, candidate_id)
        review = record_unknown_airport_candidate_relevance_review(session, candidate, **kwargs)
        session.commit()
        return review.id


def _make_erg_pre_migration_db(path):
    """UAC2A/UAC2B ready, but ERG2/ERG3 tables absent - the exact real
    production database's own current state at this mission's starting
    checkpoint (independently confirmed via direct PRAGMA inspection).
    Builds the full current schema first, then drops only the two ERG
    tables, leaving everything else (including the ERG2 evidence-link
    table, which must also go since it FK-references the assessments
    table) intact."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE unknown_airport_candidate_relevance_assessment_evidence_links")
    conn.execute("DROP TABLE unknown_airport_candidate_relevance_reviews")
    conn.execute("DROP TABLE unknown_airport_candidate_relevance_assessments")
    conn.commit()
    conn.close()
    return create_engine(f"sqlite:///{path}")


def _canonical_counts(engine):
    with Session(engine) as session:
        return dict(
            airports=session.query(Airport).count(), runways=session.query(Runway).count(),
            runway_ends=session.query(RunwayEnd).count(), installations=session.query(Installation).count(),
            signals=session.query(Signal).count(),
            physical_installation_identities=session.query(PhysicalInstallationIdentity).count(),
        )


# ---------------------------------------------------------------------------
# A/J. Schema gate
# ---------------------------------------------------------------------------


class TestSchemaGate:
    def test_missing_schema_returns_structured_blocker_not_operational_error(self, tmp_path):
        db = tmp_path / "pre_uac.db"
        _make_pre_uac_db(db).dispose()
        config = UnknownAirportCandidateReviewConfig(database=db, candidate_id=1)
        result = run_review(config)
        assert result.blockers == (SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert result.candidate_found is None

    def test_full_schema_passes_gate(self, tmp_path):
        db = tmp_path / "full.db"
        _make_full_db(db).dispose()
        config = UnknownAirportCandidateReviewConfig(database=db, candidate_id=1)
        result = run_review(config)
        assert SCHEMA_MIGRATION_REQUIRED_BLOCKER not in result.blockers


# ---------------------------------------------------------------------------
# A. Inspect
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_never_mutates_and_shows_full_state(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=2)
        engine.dispose()

        config = UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id)
        result = run_review(config)
        assert result.mode == "inspect"
        assert result.candidate_found is True
        assert result.raw_name == "Foo Regional Airport"
        assert result.linked_assertion_count == 2
        assert result.resolved_airport_id is None
        assert result.review_history == ()
        assert result.latest_review is None

        # Read-only engine - even if something tried to write, it would be
        # refused at the driver level.
        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            assert session.query(SourceAssertion).count() == 2
        engine2.dispose()

    def test_read_candidate_state_does_not_leak_on_unrelated_invalid_pending_object(self, tmp_path):
        """UAC-H1: _read_candidate_state() (the CLI's own inspect/preview
        read path, which calls get_latest_unknown_airport_candidate_review())
        must not leak a raw IntegrityError when an unrelated invalid
        pending object exists in the same session - mirrors the ERG4-
        review-discovered UAC1 finding, re-verified at this CLI call site
        specifically. Called directly (bypassing run_review(), which owns
        its own session internally and cannot have foreign state injected
        into it from outside)."""
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        with Session(create_engine(f"sqlite:///{db}")) as session:
            candidate = session.get(UnknownAirportCandidate, candidate_id)
            bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
            session.add(bad)
            state = cli_module._read_candidate_state(session, candidate, erg_schema_ready=False)
            assert state["candidate_id"] == candidate_id
            assert state["latest_review"] is None
            assert bad in session.new

    def test_inspect_nonexistent_candidate(self, tmp_path):
        db = tmp_path / "db.sqlite"
        _make_full_db(db).dispose()
        config = UnknownAirportCandidateReviewConfig(database=db, candidate_id=999999)
        result = run_review(config)
        assert result.blockers == (CANDIDATE_NOT_FOUND_BLOCKER,)
        assert result.candidate_found is False

    def test_inspect_shows_deterministic_code_match_never_fuzzy(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        _seed_airport(engine, name="Existing", icao_code="KABC")
        with Session(engine) as session:
            candidate = find_or_create_unknown_airport_candidate(
                session, raw_name="Totally Different Name", raw_icao_code="kabc",
            ).candidate
            session.commit()
            candidate_id = candidate.id
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert len(result.deterministic_code_matches) == 1
        match = result.deterministic_code_matches[0]
        assert match.matched_field == "icao_code"
        assert match.airport_name == "Existing"


# ---------------------------------------------------------------------------
# B/C. Dry-run and write gate
# ---------------------------------------------------------------------------


class TestDryRunAndWriteGate:
    def test_dry_run_defer_never_writes(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        engine.dispose()

        config = UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="DEFER", reviewer="human:x", reason="need more",
        )
        result = run_review(config)
        assert result.mode == "dry_run"
        assert result.action_eligible is True
        assert result.written is False

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            assert session.query(UnknownAirportCandidateReview).count() == 0
        engine2.dispose()

    def test_write_requires_allow_database_write(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        engine.dispose()

        with pytest.raises(ValueError, match="requires --decision, --execute, or --relevance-decision"):
            run_review(UnknownAirportCandidateReviewConfig(
                database=db, candidate_id=candidate_id, allow_database_write=True,
            ))


# ---------------------------------------------------------------------------
# D/E/F/G. Review recording for each action
# ---------------------------------------------------------------------------


class TestReviewRecording:
    def test_match_review_recorded(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        real_id = _seed_airport(engine)
        engine.dispose()

        config = UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="MATCH_EXISTING_AIRPORT",
            matched_airport_id=real_id, reviewer="human:x", reason="same airport",
            allow_database_write=True,
        )
        result = run_review(config)
        assert result.mode == "write"
        assert result.written is True
        assert result.written_review_id is not None

        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.latest_review.action == "MATCH_EXISTING_AIRPORT"
        assert inspect_result.latest_review.matched_airport_id == real_id
        assert inspect_result.resolved_airport_id is None  # recording != executing

    def test_create_review_recorded(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="CREATE_NEW_AIRPORT",
            reviewer="human:x", reason="genuinely new", allow_database_write=True,
        ))
        assert result.written is True
        assert _canonical_counts(create_engine(f"sqlite:///{db}"))["airports"] == 0

    def test_defer_review_recorded_no_canonical_change(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="DEFER",
            reviewer="human:x", reason="need more", allow_database_write=True,
        ))
        assert result.written is True
        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.resolved_airport_id is None
        assert inspect_result.linked_assertion_count == 1

    def test_reject_review_recorded_no_canonical_change(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="REJECT_CANDIDATE",
            reviewer="human:x", reason="hallucinated", allow_database_write=True,
        ))
        assert result.written is True
        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.resolved_airport_id is None
        assert inspect_result.linked_assertion_count == 1

    def test_create_rejects_matched_airport_id(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        real_id = _seed_airport(engine)
        engine.dispose()

        with pytest.raises(ValueError, match="only valid when --decision MATCH_EXISTING_AIRPORT"):
            run_review(UnknownAirportCandidateReviewConfig(
                database=db, candidate_id=candidate_id, decision="CREATE_NEW_AIRPORT",
                matched_airport_id=real_id, reviewer="human:x", reason="x",
            ))

    def test_match_requires_matched_airport_id(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine)
        engine.dispose()

        with pytest.raises(ValueError, match="--matched-airport-id is required"):
            run_review(UnknownAirportCandidateReviewConfig(
                database=db, candidate_id=candidate_id, decision="MATCH_EXISTING_AIRPORT",
                reviewer="human:x", reason="x",
            ))


# ---------------------------------------------------------------------------
# O/P. Execute (MATCH / CREATE) via CLI
# ---------------------------------------------------------------------------


class TestExecuteMatch:
    def test_dry_run_then_execute_full_flow(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=2)
        real_id = _seed_airport(engine)
        review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        engine.dispose()

        dry = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
        ))
        assert dry.mode == "execute_dry_run"
        assert dry.execute_eligible is True
        assert dry.executed is False

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            assert session.get(UnknownAirportCandidate, candidate_id).resolved_airport_id is None
        engine2.dispose()

        written = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))
        assert written.executed is True
        assert written.execution_resolved_airport_id == real_id
        assert set(written.execution_moved_assertion_ids) == set(assertion_ids)

        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.resolved_airport_id == real_id
        assert inspect_result.linked_assertion_count == 0  # moved off candidate linkage


class TestExecuteCreate:
    def test_dry_run_then_execute_full_flow(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        review_id = _record_review(engine, candidate_id, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x")
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        engine.dispose()

        dry = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            new_airport_name="Foo Regional Airport", new_airport_country="Fictionland",
        ))
        assert dry.execute_eligible is True
        assert dry.executed is False
        assert _canonical_counts(create_engine(f"sqlite:///{db}"))["airports"] == 0

        written = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            new_airport_name="Foo Regional Airport", new_airport_country="Fictionland",
            allow_database_write=True,
        ))
        assert written.executed is True
        assert written.execution_created_airport_id is not None
        counts = _canonical_counts(create_engine(f"sqlite:///{db}"))
        assert counts["airports"] == 1
        assert counts["runways"] == 0
        assert counts["signals"] == 0

    def test_missing_new_airport_fields_refused(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        review_id = _record_review(engine, candidate_id, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x")
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))
        assert result.execute_eligible is False
        assert "new-airport-name" in result.execute_refusal_reason


# ---------------------------------------------------------------------------
# H. Stale review handshake
# ---------------------------------------------------------------------------


class TestStaleReviewHandshake:
    def test_execute_refuses_after_newer_review_recorded(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        real_id = _seed_airport(engine)
        first_review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        _record_review(
            engine, candidate_id, action="DEFER", reason="changed mind", reviewer="human:y",
            supersedes_review_id=first_review_id,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=first_review_id,
            allow_database_write=True,
        ))
        assert result.execute_eligible is False
        assert "stale" in result.execute_refusal_reason.lower()

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            assert session.get(UnknownAirportCandidate, candidate_id).resolved_airport_id is None
        engine2.dispose()

    def test_execute_refuses_reject_or_defer_review(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        review_id = _record_review(engine, candidate_id, action="DEFER", reason="x", reviewer="human:x")
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))
        assert result.execute_eligible is False
        assert "REJECT_CANDIDATE or DEFER" not in (result.execute_refusal_reason or "")  # message doesn't mislead
        assert result.execute_action == "DEFER"


# ---------------------------------------------------------------------------
# Q/R/S. Execution failure / repeat / contradictory later review
# ---------------------------------------------------------------------------


class TestExecutionFailureRepeatAndContradiction:
    def test_review_survives_execution_failure(self, tmp_path):
        """Review recorded, then the matched Airport is deleted before
        execute runs - execution must refuse, but the review row must
        remain fully committed authorization history (mission §22/§23)."""
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        real_id = _seed_airport(engine)
        review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        with Session(engine) as session:
            session.execute(Airport.__table__.delete().where(Airport.id == real_id))
            session.commit()
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))
        assert result.execute_eligible is False

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            assert session.query(UnknownAirportCandidateReview).count() == 1
            assert session.get(UnknownAirportCandidateReview, review_id) is not None
        engine2.dispose()

    def test_repeat_execute_refused(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        real_id = _seed_airport(engine)
        review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        engine.dispose()

        first = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))
        assert first.executed is True

        second = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))
        assert second.execute_eligible is False
        assert "already resolved" in second.execute_refusal_reason.lower()
        assert _canonical_counts(create_engine(f"sqlite:///{db}"))["airports"] == 1

    def test_contradictory_later_review_never_re_executes(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        real_id = _seed_airport(engine, name="Real")
        other_id = _seed_airport(engine, name="Other")
        review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        engine.dispose()

        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))

        later_review_id = _record_review(
            create_engine(f"sqlite:///{db}"), candidate_id, action="MATCH_EXISTING_AIRPORT", reason="actually this one",
            reviewer="human:y", matched_airport_id=other_id, supersedes_review_id=review_id,
        )

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=later_review_id,
            allow_database_write=True,
        ))
        assert result.execute_eligible is False
        assert "already resolved" in result.execute_refusal_reason.lower()

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            assert session.get(UnknownAirportCandidate, candidate_id).resolved_airport_id == real_id
        engine2.dispose()


# ---------------------------------------------------------------------------
# K. Canonical side-effect firewall
# ---------------------------------------------------------------------------


class TestCanonicalSideEffectFirewall:
    def test_create_execute_touches_only_airport_count(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        review_id = _record_review(engine, candidate_id, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x")
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        before = _canonical_counts(engine)
        engine.dispose()

        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            new_airport_name="Foo", new_airport_country="XX", allow_database_write=True,
        ))
        after = _canonical_counts(create_engine(f"sqlite:///{db}"))
        assert after["airports"] == before["airports"] + 1
        for key in ("runways", "runway_ends", "installations", "signals", "physical_installation_identities"):
            assert after[key] == before[key]


# ---------------------------------------------------------------------------
# L. Unicode
# ---------------------------------------------------------------------------


class TestUnicode:
    def test_unicode_candidate_round_trips_through_inspect(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, raw_name="羽田空港")
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.raw_name == "羽田空港"
        rendered = render_result(result)
        assert "羽田空港" in rendered


# ---------------------------------------------------------------------------
# M. Deterministic output
# ---------------------------------------------------------------------------


class TestDeterministicOutput:
    def test_render_result_is_deterministic(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=2)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert render_result(result) == render_result(result)


# ---------------------------------------------------------------------------
# N. No business logic in CLI
# ---------------------------------------------------------------------------


class TestNoBusinessLogicInCli:
    def test_no_canonical_construction_in_cli_source(self):
        tree = ast.parse(inspect_module.getsource(cli_module))
        forbidden = {"Airport", "Runway", "RunwayEnd", "Installation", "Signal", "PhysicalInstallationIdentity"}
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()

    def test_no_reimplemented_stale_review_or_fingerprint_logic(self):
        source = inspect_module.getsource(cli_module)
        assert "compute_candidate_fingerprint" not in source
        assert "casefold" in source  # only the deterministic-code-match display helper


# ---------------------------------------------------------------------------
# T/U. Downstream continuation note honesty
# ---------------------------------------------------------------------------


class TestDownstreamContinuationNote:
    def test_unresolved_candidate_has_no_continuation_note(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()
        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.downstream_continuation_note is None

    def test_resolved_candidate_shows_honest_continuation_note_and_insufficient_identity_persists(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        real_id = _seed_airport(engine)
        review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        engine.dispose()

        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id, allow_database_write=True,
        ))
        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.downstream_continuation_note is not None
        assert "INSUFFICIENT_IDENTITY" in result.downstream_continuation_note

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            reloaded = session.get(SourceAssertion, assertion_ids[0])
            assert reloaded.airport_id == real_id
            assert reloaded.identity_guard_decision == "INSUFFICIENT_IDENTITY"
        engine2.dispose()

    def test_reevaluation_service_now_exists_and_closes_the_uac5b_gap(self, tmp_path):
        """UAC5B originally found guard replay architecturally blocked (see
        the UAC5 report): a historical SourceAssertion's identity_guard_decision
        could never be safely re-run because the full EvidenceBag it was
        computed from was never persisted. EB1-EB4
        (docs/architecture/rwi-eb4-resolved-evidence-reevaluation-report.md)
        closed that prerequisite for modern-discovery assertions and built
        the re-evaluation service itself - this test replaces the old
        "no such service exists" regression marker with a positive proof
        that the gap is actually closed: the exact resolved,
        INSUFFICIENT_IDENTITY-decided scenario from
        test_resolved_candidate_shows_honest_continuation_note_and_insufficient_identity_persists
        above can now be genuinely re-evaluated, where UAC5B could only
        document it as blocked."""
        from app.services.resolved_candidate_evidence_reevaluation import reevaluate_resolved_candidate_evidence

        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        real_id = _seed_airport(engine)
        review_id = _record_review(
            engine, candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        engine.dispose()

        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id, allow_database_write=True,
        ))

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            reloaded = session.get(SourceAssertion, assertion_ids[0])
            assert reloaded.identity_guard_decision == "INSUFFICIENT_IDENTITY"
            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=assertion_ids[0])
            session.commit()
            # the ORIGINAL historical decision remains untouched even though
            # a real re-evaluation now genuinely ran against it
            assert reloaded.identity_guard_decision == "INSUFFICIENT_IDENTITY"
            assert result.evaluated_against_airport_id == real_id
        engine2.dispose()


# ---------------------------------------------------------------------------
# Z. Fleet Health FH-F2/FH-F3 candidate/unresolved/resolved states
# ---------------------------------------------------------------------------


class TestFleetHealthThreeStates:
    def test_candidate_linked_unresolved_resolved_and_unattributed_all_classify_correctly(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1, raw_name="Still Unresolved Airport")
        resolved_candidate_id, resolved_assertion_ids = _seed_candidate_with_n_assertions(
            engine, n=1, raw_name="Now Resolved Airport",
        )
        real_id = _seed_airport(engine)
        review_id = _record_review(
            engine, resolved_candidate_id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
            matched_airport_id=real_id,
        )
        engine.dispose()
        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=resolved_candidate_id, execute=True, review_id=review_id,
            allow_database_write=True,
        ))

        # A genuinely, truly unattributed row (no candidate link at all).
        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            from app.models import Source
            source = Source(title="t", source_type="web_discovery", external_id="discovery:unattributed")
            session.add(source)
            session.flush()
            unattributed = SourceAssertion(
                source_id=source.id, airport_id=None, unknown_airport_candidate_id=None,
                assertion_type="project_construction", raw_relevant_text="orphan evidence",
                artifact_identity="art-orphan", source_locator="p1", raw_fragment_hash="hash-orphan",
                review_state="reviewed",
            )
            session.add(unattributed)
            session.commit()

            facts = _build_source_assertion_review_states(session)
            f2 = evaluate_fh_f2(facts)
            f3 = evaluate_fh_f3(facts)

            fact_ids = {f.assertion_id: f for f in facts}
            # Candidate-linked unresolved: present in the input set, skipped by both rules.
            assert any(f.unknown_airport_candidate_id == candidate_id for f in facts)
            # Resolved: no longer even in the airport_id-IS-NULL input set at all.
            assert not any(f.assertion_id == resolved_assertion_ids[0] for f in facts)
            # Truly unattributed: present, and since review_state="reviewed", FH-F3 fires for it.
            assert unattributed.id in fact_ids
            f3_ids = {aid for finding in f3 for aid in finding.entity_ids}
            assert unattributed.id in f3_ids
        engine2.dispose()


# ---------------------------------------------------------------------------
# AA. Migration-chain parity - full CLI flow against a genuinely migrated DB
# ---------------------------------------------------------------------------


class TestMigrationChainParity:
    def test_full_cli_flow_against_genuinely_migrated_schema(self, tmp_path):
        db = tmp_path / "migrated.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_reviews")
        conn.execute("DROP TABLE unknown_airport_candidates")
        replacement = "source_assertions__pre"
        conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
        quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
        conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
        conn.execute("DROP TABLE source_assertions")
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
        conn.commit()
        conn.close()

        uac2a_migration.upgrade(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        review_id = _record_review(engine, candidate_id, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x")
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        engine.dispose()

        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.latest_review.action == "CREATE_NEW_AIRPORT"

        written = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=review_id,
            new_airport_name="Foo Regional Airport", new_airport_country="Fictionland",
            allow_database_write=True,
        ))
        assert written.executed is True
        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            reloaded = session.get(SourceAssertion, assertion_ids[0])
            assert reloaded.airport_id == written.execution_created_airport_id
        engine2.dispose()


# ---------------------------------------------------------------------------
# I. Wrong-DB isolation
# ---------------------------------------------------------------------------


class TestWrongDbIsolation:
    def test_writing_to_one_database_never_touches_another(self, tmp_path):
        db_a = tmp_path / "a.db"
        db_b = tmp_path / "b.db"
        engine_a = _make_full_db(db_a)
        engine_b = _make_full_db(db_b)
        candidate_id_a, _ = _seed_candidate_with_n_assertions(engine_a, n=1)
        candidate_id_b, _ = _seed_candidate_with_n_assertions(engine_b, n=1)
        engine_a.dispose()
        engine_b.dispose()

        run_review(UnknownAirportCandidateReviewConfig(
            database=db_a, candidate_id=candidate_id_a, decision="REJECT_CANDIDATE",
            reviewer="human:x", reason="x", allow_database_write=True,
        ))

        with Session(create_engine(f"sqlite:///{db_b}")) as session:
            assert session.query(UnknownAirportCandidateReview).count() == 0


# ---------------------------------------------------------------------------
# AB. Real DB no-access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_real_database_path_literal_outside_the_module_docstring(self):
        """The module docstring's own usage examples legitimately mention
        data/runway_safe.db as an illustration - this proves it never
        appears as an executable string literal (a default value, a
        hardcoded path) anywhere in the actual code."""
        tree = ast.parse(inspect_module.getsource(cli_module))
        docstring_node = tree.body[0].value if tree.body and isinstance(tree.body[0], ast.Expr) else None
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node is docstring_node:
                    continue
                assert "runway_safe.db" not in node.value

    def test_database_argument_has_no_default(self):
        parser = cli_module._parser()
        for action in parser._actions:
            if action.dest == "database":
                assert action.default is None
                assert action.required is True
                return
        pytest.fail("--database argument not found")

    def test_no_sessionlocal_or_process_global_engine(self):
        source = inspect_module.getsource(cli_module)
        assert "import SessionLocal" not in source
        assert "SessionLocal()" not in source

    def test_no_migration_execution_imported(self):
        source = inspect_module.getsource(cli_module)
        assert "uac2a_migration" not in source
        assert ".upgrade(" not in source
        assert ".downgrade(" not in source


# ---------------------------------------------------------------------------
# main() / argv-level smoke test
# ---------------------------------------------------------------------------


class TestMainEntrypoint:
    def test_main_inspect_exit_code_zero(self, tmp_path, capsys):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        exit_code = main(["--database", str(db), "--candidate-id", str(candidate_id)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Foo Regional Airport" in captured.out

    def test_main_schema_missing_exit_code_one(self, tmp_path, capsys):
        db = tmp_path / "pre_uac.db"
        _make_pre_uac_db(db).dispose()
        exit_code = main(["--database", str(db), "--candidate-id", "1"])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert SCHEMA_MIGRATION_REQUIRED_BLOCKER in captured.out


# ---------------------------------------------------------------------------
# Adversarial review addendum - regression tests for genuine findings
# ---------------------------------------------------------------------------


class TestSupersedesReviewIdWiring:
    """Completeness gap found during adversarial review: the CLI's first
    draft never exposed record_unknown_airport_candidate_review()'s own
    optional supersedes_review_id parameter, even though the underlying
    governed function already validates and accepts it - a real, if
    minor, audit-annotation gap for a "human review CLI," not a new
    capability."""

    def test_supersedes_review_id_recorded_and_visible_in_history(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        first = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="DEFER",
            reviewer="human:x", reason="need more", allow_database_write=True,
        ))
        first_id = first.written_review_id

        second = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="REJECT_CANDIDATE",
            reviewer="human:y", reason="now confident it's hallucinated",
            supersedes_review_id=first_id, allow_database_write=True,
        ))
        assert second.written is True

        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.latest_review.supersedes_review_id == first_id

    def test_supersedes_review_id_rejected_without_decision(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        with pytest.raises(ValueError, match="--supersedes-review-id is only valid when --decision"):
            run_review(UnknownAirportCandidateReviewConfig(
                database=db, candidate_id=candidate_id, supersedes_review_id=1,
            ))

    def test_supersedes_review_id_rejected_with_execute(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        with pytest.raises(ValueError, match="only valid when recording a review"):
            run_review(UnknownAirportCandidateReviewConfig(
                database=db, candidate_id=candidate_id, execute=True, review_id=1, supersedes_review_id=1,
            ))


class TestUac5bEvidenceBagReconstructionProof:
    """Adversarial review of the UAC5B STOP (mission Part B) - empirical,
    executable proof, not just re-trusted reasoning from the report."""

    def test_comma_joined_persistence_is_provably_lossy_not_merely_theoretically(self):
        """Two structurally DIFFERENT EvidenceBag.identifiers sets - one
        containing a single value that itself embeds the join delimiter,
        one containing two ordinary separate values - collapse to the
        IDENTICAL persisted string. This is empirical proof the encoding
        is non-reversible, not merely an assumption."""
        from app.services.discovery_evidence_persistence import _join_or_none

        set_a = frozenset({"KABC, KXYZ"})
        set_b = frozenset({"KABC", "KXYZ"})
        assert _join_or_none(set_a) == _join_or_none(set_b)

    def test_lost_contradicting_evidence_flips_reject_cross_airport_into_false_attach_confirmed(self):
        """The single most important empirical proof behind the UAC5B
        STOP: identical positive (identifier) evidence, with and without
        the SAME real contradicting_issuers fact that is NEVER persisted
        anywhere on SourceAssertion, produces two DIFFERENT, opposite-in-
        consequence guard outcomes. A partial replay fed only what
        SourceAssertion actually persists would silently reach
        ATTACH_CONFIRMED where the original, full-evidence evaluation
        correctly reached REJECT_CROSS_AIRPORT."""
        from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport, EvidenceBag, evaluate_attachment

        candidate = CandidateAirport(id=1, name="Foo Regional Airport", identifiers=frozenset({"KFOO"}))

        original_bag = EvidenceBag(
            identifiers=frozenset({"KFOO"}),
            contradicting_issuers=frozenset({"Bar County Airport Authority"}),
        )
        original_decision = evaluate_attachment(candidate, original_bag)
        assert original_decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT

        # Only `identifiers` survives persistence (as a lossy joined
        # string) - contradicting_issuers has no column anywhere on
        # SourceAssertion, so a replay's own reconstructed bag omits it.
        replay_bag = EvidenceBag(identifiers=frozenset({"KFOO"}))
        replay_decision = evaluate_attachment(candidate, replay_bag)
        assert replay_decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED

    def test_evidencebag_fields_never_persisted_have_no_sourceassertion_column(self):
        """Structural proof, via the ORM's own mapped columns, that
        locations/issuers/every contradicting_*/both alternate_airport_*
        fields have no column anywhere on SourceAssertion."""
        column_names = {c.name for c in SourceAssertion.__table__.columns}
        never_persisted_evidencebag_fields = {
            "locations", "issuers", "contradicting_names", "contradicting_issuers",
            "contradicting_locations", "alternate_airport_runway_ends", "alternate_airport_runway_pairs",
        }
        assert never_persisted_evidencebag_fields.isdisjoint(column_names)

    def test_no_structural_link_from_source_assertion_to_snapshot_payload(self):
        """Part B(E): Snapshot.payload (app.models.acquisition.Snapshot)
        does preserve the ORIGINAL raw document bytes immutably - but
        there is no database-enforced (or even code-level) mapping from
        SourceAssertion.artifact_identity back to a specific Snapshot row
        anywhere in this repository. Even if such a mapping existed,
        recovering an EvidenceBag from raw bytes would require RE-RUNNING
        EXTRACTION (a separately-scoped, not-necessarily-deterministic
        capability for any AI-assisted extractor - explicitly out of
        scope, "no recurring acquisition") rather than reading an already-
        persisted structured field. This test proves the negative half of
        that claim structurally: no FK, and no code anywhere joins
        artifact_identity to snapshots."""
        from app.models import source_assertion as source_assertion_module
        from app.models import acquisition as acquisition_module

        # No FK from SourceAssertion to Snapshot/AcquisitionSource/AcquisitionRun.
        fk_targets = {
            fk.target_fullname for column in SourceAssertion.__table__.columns for fk in column.foreign_keys
        }
        assert not any("snapshot" in t or "acquisition" in t for t in fk_targets)

        # acquisition.py (Snapshot/AcquisitionSource/AcquisitionRun) never
        # references artifact_identity at all - no join key exists on
        # either side.
        acquisition_source = inspect_module.getsource(acquisition_module)
        assert "artifact_identity" not in acquisition_source

        # source_assertion.py never references "snapshot" - confirms the
        # column's own docstring never even documents an intended
        # convention-based mapping, let alone a structural one.
        source_assertion_source = inspect_module.getsource(source_assertion_module)
        assert "snapshot" not in source_assertion_source.lower()


class TestNonexistentDatabaseFileBehavior:
    """Adversarial finding, NOT fixed in this mission: a nonexistent
    --database path crashes with a raw sqlite3.OperationalError rather
    than a clean, structured blocker. Confirmed to be a PRE-EXISTING
    pattern already present in the already-committed, already-reviewed
    scripts/review_signal_disposition.py precedent (same crash, same
    root cause: check_schema_readiness()'s own read-only sqlite3.connect()
    call is never wrapped) - not a UAC5-introduced regression. Documented
    here, not silently fixed, per this review's own "fix only defects
    that belong inside UAC5's established scope" instruction: patching
    this in exactly one of at least two sibling scripts sharing the
    identical pattern would be an inconsistent, scope-creeping partial
    fix; the correct fix is a shared, repo-wide follow-up."""

    def test_nonexistent_database_file_raises_rather_than_returning_a_blocker(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        with pytest.raises(Exception):
            run_review(UnknownAirportCandidateReviewConfig(database=missing, candidate_id=1))

    def test_identical_pattern_already_exists_in_the_committed_precedent_script(self, tmp_path):
        import scripts.review_signal_disposition as precedent_module

        missing = tmp_path / "does_not_exist.db"
        with pytest.raises(Exception):
            precedent_module.run_review(precedent_module.SignalDispositionReviewConfig(database=missing))


# ---------------------------------------------------------------------------
# ERG5 - operator/CLI governance flow (docs/architecture/rwi-erg5-operator-
# governance-flow-report.md). Test-matrix letters A-X match the mission's
# own §32 numbering.
# ---------------------------------------------------------------------------


class TestErg5GovernanceViewInspect:
    def test_a_no_assessment(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.automatic_relevance is None
        assert result.human_relevance_review.state == "NO_ASSESSMENT_YET"
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "NO_RELEVANCE_ASSESSMENT"
        rendered = render_result(result)
        assert "NO ASSESSMENT YET" in rendered

    def test_b_anoka_auto_negative_mark_not(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1, raw_name="Anoka County-Blaine Airport")
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="resurfacing"),),
            assertion_ids,
        )
        _record_relevance_review(
            engine, candidate_id, basis_assessment_id=assessment_id, action=MARK_NOT,
            reviewer="human:x", reason="not EMAS related",
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        ar = result.automatic_relevance
        assert ar.outcome == "RUNWAY_ONLY_NOT_EMAS_RELEVANT"
        assert ar.is_inventory_relevant is False
        assert ar.is_watch_worthy is False
        hr = result.human_relevance_review
        assert hr.state == "CURRENT"
        assert hr.action == MARK_NOT
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE"

    def test_c_anoka_auto_negative_confirm_remains_blocked(self, tmp_path):
        """The central product rule, visible at the CLI layer: human
        CONFIRM cannot manufacture EMAS relevance the evaluator did not
        find."""
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1, raw_name="Anoka County-Blaine Airport")
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="resurfacing"),),
            assertion_ids,
        )
        _record_relevance_review(
            engine, candidate_id, basis_assessment_id=assessment_id, action=CONFIRM,
            reviewer="human:x", reason="looks fine to me",
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.human_relevance_review.state == "CURRENT"
        assert result.human_relevance_review.action == CONFIRM
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE"

    def test_d_auto_positive_unreviewed(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.automatic_relevance.is_watch_worthy is True
        assert result.human_relevance_review.state == "UNREVIEWED"
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "NO_CURRENT_HUMAN_REVIEW"

    def test_e_auto_positive_defer(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        _record_relevance_review(
            engine, candidate_id, basis_assessment_id=assessment_id, action=DEFER_RELEVANCE,
            reviewer="human:x", reason="need more info",
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.human_relevance_review.state == "CURRENT"
        assert result.human_relevance_review.action == DEFER_RELEVANCE
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "HUMAN_REVIEW_DEFERRED"

    def test_f_auto_positive_mark_not(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        _record_relevance_review(
            engine, candidate_id, basis_assessment_id=assessment_id, action=MARK_NOT,
            reviewer="human:x", reason="out of band knowledge",
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "HUMAN_REVIEW_MARKED_NOT_RELEVANT"

    def test_g_auto_positive_current_confirm_eligible(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.canonical_admission.eligible is True
        assert result.canonical_admission.reason == "ELIGIBLE"
        rendered = render_result(result)
        assert "does NOT mean an Airport has already been created" in rendered

    def test_h_stale_confirm(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        a1 = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        _record_relevance_review(engine, candidate_id, basis_assessment_id=a1, action=CONFIRM, reviewer="human:x", reason="x")
        a2 = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="y"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.automatic_relevance.assessment_id == a2
        hr = result.human_relevance_review
        assert hr.state == "STALE"
        assert hr.is_current is False
        assert hr.action == CONFIRM  # the historical CONFIRM is still shown, just not current
        assert hr.basis_assessment_id == a1
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "HUMAN_REVIEW_STALE"
        rendered = render_result(result)
        assert "STALE, NOT current authority" in rendered

    def test_i_rediscovery_stale_then_new_confirm_eligible(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        a1 = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        _record_relevance_review(engine, candidate_id, basis_assessment_id=a1, action=CONFIRM, reviewer="human:x", reason="x")
        a2 = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="y"),), assertion_ids,
        )

        stale_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert stale_result.canonical_admission.eligible is False

        _record_relevance_review(engine, candidate_id, basis_assessment_id=a2, action=CONFIRM, reviewer="human:x", reason="reconfirmed")
        engine.dispose()

        eligible_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert eligible_result.human_relevance_review.state == "CURRENT"
        assert eligible_result.canonical_admission.eligible is True

    def test_j_dormant_inventory_only(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.E_EXISTING_INSTALLATION, basis="x"),), assertion_ids,
        )
        _record_relevance_review(engine, candidate_id, basis_assessment_id=assessment_id, action=CONFIRM, reviewer="human:x", reason="x")
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.automatic_relevance.is_inventory_relevant is True
        assert result.automatic_relevance.is_watch_worthy is False
        assert result.canonical_admission.eligible is True

    def test_k_watch_only(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        _record_relevance_review(engine, candidate_id, basis_assessment_id=assessment_id, action=CONFIRM, reviewer="human:x", reason="x")
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.automatic_relevance.is_inventory_relevant is False
        assert result.automatic_relevance.is_watch_worthy is True
        assert result.canonical_admission.eligible is True

    def test_l_contradictions_displayed_even_with_confirm(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id,
            (
                EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),
                EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="y", polarity=ObservationPolarity.CONTRADICTING),
            ),
            assertion_ids,
        )
        _record_relevance_review(engine, candidate_id, basis_assessment_id=assessment_id, action=CONFIRM, reviewer="human:x", reason="x")
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert "A_EXPLICIT_EMAS" in result.automatic_relevance.contradicting_evidence_classes
        rendered = render_result(result)
        assert "contradicting_evidence_classes: ['A_EXPLICIT_EMAS']" in rendered

    def test_m_evaluator_version_displayed(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.G_GENERIC_RUNWAY_WORK, basis="x"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.automatic_relevance.evaluator_version
        rendered = render_result(result)
        assert f"evaluator_version: {result.automatic_relevance.evaluator_version}" in rendered

    def test_n_exact_evidence_link_ids(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=2)
        _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert set(result.automatic_relevance.linked_source_assertion_ids) == set(assertion_ids)


class TestErg5RelevanceReviewRecording:
    def test_o_stale_basis_ux_honest_refusal_names_current_id(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        a1 = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        a2 = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="y"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=a1,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.relevance_action_eligible is False
        assert str(a2) in result.relevance_action_refusal_reason
        assert "stale" in result.relevance_action_refusal_reason.lower() or "current latest" in result.relevance_action_refusal_reason.lower()

    def test_p_cross_candidate_basis_review_attempt_refused(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_a_id, assertions_a = _seed_candidate_with_n_assertions(engine, n=1, raw_name="Candidate A")
        candidate_b_id, assertions_b = _seed_candidate_with_n_assertions(engine, n=1, raw_name="Candidate B")
        assessment_b = _persist_assessment(
            engine, candidate_b_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertions_b,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_a_id, relevance_decision=CONFIRM, basis_assessment_id=assessment_b,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.relevance_action_eligible is False
        assert "different candidate" in result.relevance_action_refusal_reason

    def test_q_dry_run_writes_zero_rows(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=assessment_id,
            reviewer="human:x", reason="x",
        ))
        assert result.relevance_action_eligible is True
        assert result.relevance_written is False

        with Session(create_engine(f"sqlite:///{db}")) as session:
            from app.models.unknown_airport_candidate_relevance_review import UnknownAirportCandidateRelevanceReview
            assert session.query(UnknownAirportCandidateRelevanceReview).count() == 0

    def test_r_authorized_write_appends_exactly_one_review(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=assessment_id,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.relevance_written is True
        assert result.relevance_written_review_id is not None

        with Session(create_engine(f"sqlite:///{db}")) as session:
            from app.models.unknown_airport_candidate_relevance_review import UnknownAirportCandidateRelevanceReview
            reviews = session.query(UnknownAirportCandidateRelevanceReview).all()
            assert len(reviews) == 1
            assert reviews[0].action == CONFIRM

    def test_action_vocabulary_rejects_identity_review_action(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        engine.dispose()

        with pytest.raises(ValueError, match="--relevance-decision must be one of"):
            run_review(UnknownAirportCandidateReviewConfig(
                database=db, candidate_id=candidate_id, relevance_decision="MATCH_EXISTING_AIRPORT",
                basis_assessment_id=assessment_id, reviewer="human:x", reason="x",
            ))


class TestErg5SameBasisMultiReview:
    def test_s_defer_then_confirm_same_basis_shows_current_confirm(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=DEFER_RELEVANCE, basis_assessment_id=assessment_id,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=assessment_id,
            reviewer="human:y", reason="reconsidered", allow_database_write=True,
        ))
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.human_relevance_review.state == "CURRENT"
        assert result.human_relevance_review.action == CONFIRM
        assert result.canonical_admission.eligible is True

    def test_confirm_then_mark_not_same_basis_shows_current_mark_not(self, tmp_path):
        """Mission's own §9 second half: the REVERSE direction (CONFIRM
        then MARK_NOT, same basis) - a human reversing an earlier CONFIRM
        must also correctly flip canonical_admission back to BLOCKED."""
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )
        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=assessment_id,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=MARK_NOT, basis_assessment_id=assessment_id,
            reviewer="human:y", reason="reconsidered, not relevant after all", allow_database_write=True,
        ))
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.human_relevance_review.state == "CURRENT"
        assert result.human_relevance_review.action == MARK_NOT
        assert result.canonical_admission.eligible is False
        assert result.canonical_admission.reason == "HUMAN_REVIEW_MARKED_NOT_RELEVANT"


class TestErg5IdentityVsRelevanceSeparation:
    def test_t_relevance_eligible_but_identity_defer_still_blocks_execution(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        identity_review_id = _record_review(engine, candidate_id, action="DEFER", reason="still checking", reviewer="human:x")
        engine.dispose()

        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert inspect_result.canonical_admission.eligible is True
        assert inspect_result.latest_review.action == "DEFER"

        execute_result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, execute=True, review_id=identity_review_id,
            new_airport_name="Foo", new_airport_country="XX", allow_database_write=True,
        ))
        assert execute_result.execute_eligible is False
        assert "REJECT_CANDIDATE or DEFER" not in (execute_result.execute_refusal_reason or "")
        assert execute_result.execute_action == "DEFER"

    def test_no_flattening_into_one_approved_flag(self, tmp_path):
        """The output contract itself must keep identity review state,
        relevance review state, and canonical admission as three
        independently-readable fields - never collapsed into a single
        boolean."""
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert result.latest_review is None  # no identity review recorded at all
        assert result.canonical_admission.eligible is True  # relevance gate alone
        assert result.resolved_airport_id is None  # not actually admitted


class TestErg5SchemaAbsent:
    def test_u_inspect_works_cleanly_with_erg_schema_absent(self, tmp_path):
        db = tmp_path / "erg_absent.db"
        engine = _make_erg_pre_migration_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert not result.blockers
        assert result.erg_schema_readiness["ready"] is False
        assert result.automatic_relevance is None
        assert result.human_relevance_review is None
        assert result.canonical_admission is None
        rendered = render_result(result)
        assert ERG_SCHEMA_MIGRATION_REQUIRED_BLOCKER in rendered

    def test_u_relevance_decision_hard_blocked_when_erg_schema_absent(self, tmp_path):
        db = tmp_path / "erg_absent2.db"
        engine = _make_erg_pre_migration_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=1,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.blockers == (ERG_SCHEMA_MIGRATION_REQUIRED_BLOCKER,)

    def test_u_identity_review_and_execute_still_work_when_erg_schema_absent(self, tmp_path):
        """Old identity-only CLI functionality must not catastrophically
        break just because ERG2/ERG3 are not yet migrated."""
        db = tmp_path / "erg_absent3.db"
        engine = _make_erg_pre_migration_db(db)
        candidate_id, _ = _seed_candidate_with_n_assertions(engine, n=1)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, decision="DEFER", reviewer="human:x", reason="x",
            allow_database_write=True,
        ))
        assert result.written is True
        assert result.automatic_relevance is None


class TestErg5PartialOrMalformedSchemaAttack:
    """Mission's own §18: partial/malformed ERG schema states must never
    be misreported as ready, must fail closed, and must never be
    silently repaired."""

    def test_a_erg2_present_erg3_absent(self, tmp_path):
        db = tmp_path / "partial_a.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_relevance_reviews")
        conn.commit()
        conn.close()

        readiness = check_erg_schema_readiness(db)
        assert readiness == {"erg2_ready": True, "erg3_ready": False, "ready": False}

    def test_b_erg3_present_erg2_absent(self, tmp_path):
        db = tmp_path / "partial_b.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_relevance_assessment_evidence_links")
        conn.execute("DROP TABLE unknown_airport_candidate_relevance_assessments")
        conn.commit()
        conn.close()

        readiness = check_erg_schema_readiness(db)
        assert readiness == {"erg2_ready": False, "erg3_ready": True, "ready": False}

    def test_c_erg2_malformed_missing_column(self, tmp_path):
        db = tmp_path / "malformed_c.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("ALTER TABLE unknown_airport_candidate_relevance_assessments DROP COLUMN evaluator_version")
        conn.commit()
        conn.close()

        readiness = check_erg_schema_readiness(db)
        assert readiness["erg2_ready"] is False
        assert readiness["ready"] is False

    def test_d_erg3_malformed_missing_column(self, tmp_path):
        db = tmp_path / "malformed_d.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("ALTER TABLE unknown_airport_candidate_relevance_reviews DROP COLUMN reason")
        conn.commit()
        conn.close()

        readiness = check_erg_schema_readiness(db)
        assert readiness["erg3_ready"] is False
        assert readiness["ready"] is False

    def test_partial_schema_relevance_decision_still_hard_blocked_no_crash(self, tmp_path):
        """End-to-end through run_review(): a partial (not fully absent)
        ERG schema must still hard-block --relevance-decision cleanly,
        never leak an OperationalError, never attempt a partial write."""
        db = tmp_path / "partial_e2e.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_relevance_reviews")
        conn.commit()
        conn.close()

        engine2 = create_engine(f"sqlite:///{db}")
        candidate_id, _ = _seed_candidate_with_n_assertions(engine2, n=1)
        engine2.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=1,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        assert result.blockers == (ERG_SCHEMA_MIGRATION_REQUIRED_BLOCKER,)

        # Plain inspect must also stay safe (governance fields left None,
        # not a partial/misleading ERG2-only view).
        inspect_result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert not inspect_result.blockers
        assert inspect_result.automatic_relevance is None
        assert inspect_result.human_relevance_review is None
        assert inspect_result.canonical_admission is None


class TestErg5NoAutoflush:
    def test_v_inspect_does_not_leak_on_unrelated_invalid_pending_object(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _make_admission_eligible(engine, candidate_id, assertion_ids)

        with Session(engine) as session:
            candidate = session.get(UnknownAirportCandidate, candidate_id)
            candidate_id_captured = candidate.id  # captured before unrelated pending state
            bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
            session.add(bad)
            from app.services.unknown_airport_candidate_governance_view import get_unknown_airport_candidate_governance_view
            view = get_unknown_airport_candidate_governance_view(session, candidate_id_captured)
            assert view.canonical_admission.eligible is True
            assert bad in session.new
        engine.dispose()

    def test_v_relevance_review_write_precondition_phase_does_not_leak(self, tmp_path):
        """Attacks the PRECONDITION-CHECK phase specifically (an invalid
        basis_assessment_id, so the function raises its own governed
        ValueError before ever reaching its own intentional write flush)
        - a genuinely successful write's own flush legitimately DOES
        flush bad's violation too (repository-standard "no swallowing of
        real write errors" behavior, already proven at the service layer
        by ERG3's own test suite), so that is not what this test attacks."""
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="x"),), assertion_ids,
        )

        with Session(engine) as session:
            candidate = session.get(UnknownAirportCandidate, candidate_id)
            bad = UnknownAirportCandidate(candidate_fingerprint="deadbeef")
            session.add(bad)
            with pytest.raises(ValueError, match="does not exist"):
                record_unknown_airport_candidate_relevance_review(
                    session, candidate, basis_assessment_id=999999, action=CONFIRM,
                    reviewer="human:x", reason="x",
                )
            assert bad in session.new
        engine.dispose()


class TestErg5SignalInstallationFirewall:
    def test_x_no_signal_or_installation_created_across_full_flow(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        before = _canonical_counts(engine)

        assessment_id = _persist_assessment(
            engine, candidate_id, (EmasEvidenceObservation(EvidenceClass.E_EXISTING_INSTALLATION, basis="x"),), assertion_ids,
        )
        run_review(UnknownAirportCandidateReviewConfig(
            database=db, candidate_id=candidate_id, relevance_decision=CONFIRM, basis_assessment_id=assessment_id,
            reviewer="human:x", reason="x", allow_database_write=True,
        ))
        run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        engine.dispose()

        after = _canonical_counts(create_engine(f"sqlite:///{db}"))
        assert after["installations"] == before["installations"]
        assert after["signals"] == before["signals"]
        assert after["runways"] == before["runways"]

    def test_no_signal_eligible_field_invented(self, tmp_path):
        db = tmp_path / "db.sqlite"
        engine = _make_full_db(db)
        candidate_id, assertion_ids = _seed_candidate_with_n_assertions(engine, n=1)
        _make_admission_eligible(engine, candidate_id, assertion_ids)
        engine.dispose()

        result = run_review(UnknownAirportCandidateReviewConfig(database=db, candidate_id=candidate_id))
        assert not hasattr(result, "signal_eligible")
        assert not hasattr(result.canonical_admission, "signal_eligible")


class TestErg5DirectGovernanceReuse:
    def test_no_duplicated_latest_ordering_or_admission_rule_in_cli_source(self):
        import scripts.review_unknown_airport_candidate as cli_mod

        source = inspect_module.getsource(cli_mod)
        # The CLI module itself must never construct an ORDER BY over
        # relevance assessments/reviews, nor re-derive the inventory-OR-
        # watch admission rule - both must come only from the imported
        # governance view / ERG2 / ERG3 / ERG4 helpers.
        assert "is_inventory_relevant or" not in source
        assert "is_watch_worthy or" not in source
        assert "UnknownAirportCandidateRelevanceAssessment)" not in source
        assert "UnknownAirportCandidateRelevanceReview)" not in source

    def test_governance_view_module_never_imports_signal_or_installation(self):
        import app.services.unknown_airport_candidate_governance_view as view_mod

        tree = ast.parse(inspect_module.getsource(view_mod))
        imported_names = set()
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_modules.add(node.module or "")
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)

        assert "Signal" not in imported_names
        assert "Installation" not in imported_names
        assert not any("unknown_airport_discovery_integration" in m for m in imported_modules)
        assert not any("identity_guard" in m for m in imported_modules)
