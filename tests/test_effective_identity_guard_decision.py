"""Tests for app/services/effective_identity_guard_decision.py (EB5,
docs/architecture/rwi-eb5-downstream-identity-consumption-report.md, Slice
5 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

Isolated, in-memory (or tmp_path, for the real-migration-chain tests)
SQLite databases only - never the real one.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.evidence_attachment_guard import AttachmentOutcome
import app.services.effective_identity_guard_decision as eb5_module
from app.services.effective_identity_guard_decision import (
    EffectiveIdentityGuardDecisionBasis,
    resolve_effective_identity_guard_decision,
)
from app.services.resolved_candidate_evidence_reevaluation import (
    SourceAssertionNotFoundError,
    reevaluate_resolved_candidate_evidence,
)
from app.services.unknown_airport_candidate_persistence import record_unknown_airport_candidate_review
from app.services.unknown_airport_candidate_relevance_persistence import (
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    record_unknown_airport_candidate_relevance_review,
)
from app.services.unknown_airport_candidate_resolution import (
    create_airport_from_approved_candidate,
    resolve_candidate_to_existing_airport,
)
from app.services.unknown_airport_discovery_integration import (
    DiscoveryIdentityOutcome,
    resolve_or_persist_discovery_identity,
)
import scripts.migrate_evidence_bag_persistence_eb2 as eb2_migration
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, *, name="Foo Regional Airport", country="USA", **kwargs) -> Airport:
    airport = Airport(name=name, country=country, **kwargs)
    session.add(airport)
    session.flush()
    return airport


def _artifact(name: str) -> str:
    return f"eb5-test-artifact:{name}"


def _meta(document_identity: str) -> DiscoverySourceMetadata:
    return DiscoverySourceMetadata(document_identity=document_identity, title="Test document")


def _resolve_to_existing(session, *, candidate_id: int, matched_airport_id: int, reviewer: str = "tester"):
    candidate = session.get(UnknownAirportCandidate, candidate_id)
    review = record_unknown_airport_candidate_review(
        session, candidate, action="MATCH_EXISTING_AIRPORT", reason="test match",
        reviewer=reviewer, matched_airport_id=matched_airport_id,
    )
    session.flush()
    resolved = resolve_candidate_to_existing_airport(session, candidate_id=candidate_id, review_id=review.id)
    return review, resolved


def _full_match_lifecycle(session, *, key: str, raw_text: str, matched_airport_id: int, **fragment_kwargs):
    fragment = CandidateFragment(artifact_identity=_artifact(key), source_locator="p1", raw_text=raw_text, **fragment_kwargs)
    discovered = resolve_or_persist_discovery_identity(session, _meta(f"{key}-doc"), fragment, [])
    assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
    review, resolved = _resolve_to_existing(session, candidate_id=discovered.unknown_airport_candidate_id, matched_airport_id=matched_airport_id)
    return discovered, review, resolved


def _add_evaluation(session, *, source_assertion_id, snapshot_id, airport_id, outcome, created_at=None):
    evaluation = IdentityGuardEvaluation(
        source_assertion_id=source_assertion_id, evidence_bag_snapshot_id=snapshot_id,
        evaluated_against_airport_id=airport_id, outcome=outcome.value if hasattr(outcome, "value") else outcome,
        reason="synthetic test evaluation",
    )
    if created_at is not None:
        evaluation.created_at = created_at
    session.add(evaluation)
    session.flush()
    return evaluation


# ---------------------------------------------------------------------------
# The exact blocker, reproduced fresh
# ---------------------------------------------------------------------------


class TestBlockerReproduction:
    def test_historical_insufficient_latest_confirmed_now_eligible(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="blocker", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            assertion = session.get(SourceAssertion, discovered.source_assertion_id)
            assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"

            before = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert before.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert not before.is_identity_confirmed

            _add_evaluation(
                session, source_assertion_id=assertion.id, snapshot_id=discovered.evidence_bag_snapshot_id,
                airport_id=airport.id, outcome=AttachmentOutcome.ATTACH_CONFIRMED,
            )
            session.flush()

            after = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert after.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert after.is_identity_confirmed
            assert after.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION
            # historical field untouched
            assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"


# ---------------------------------------------------------------------------
# Precedence table - cases A-F
# ---------------------------------------------------------------------------


class TestPrecedenceTable:
    def test_case_a_original_confirmed_no_evaluation(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            fragment = CandidateFragment(artifact_identity=_artifact("case-a"), source_locator="p1", raw_text="FOO memo.", airport_identifiers=frozenset({"FOO"}))
            from app.services.discovery_evidence_persistence import persist_discovery_fragment
            from app.services.evidence_attachment_guard import CandidateAirport

            result = persist_discovery_fragment(session, _meta("case-a-doc"), fragment, [CandidateAirport(id=airport.id, name=airport.name, identifiers=frozenset({"FOO"}))])
            assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=result.source_assertion_id)
            assert state.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert state.is_identity_confirmed

    def test_case_b_original_not_confirmed_no_evaluation(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="case-b", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert not state.is_identity_confirmed

    @pytest.mark.parametrize(
        "outcome,expect_confirmed",
        [
            (AttachmentOutcome.ATTACH_CONFIRMED, True),
            (AttachmentOutcome.ATTACH_PROVISIONAL, False),
            (AttachmentOutcome.INSUFFICIENT_IDENTITY, False),
            (AttachmentOutcome.REJECT_CROSS_AIRPORT, False),
            (AttachmentOutcome.REVIEW_REQUIRED, False),
        ],
    )
    def test_cases_c_through_f_plus_review_required(self, outcome, expect_confirmed):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key=f"case-{outcome.value}", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            _add_evaluation(
                session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id,
                airport_id=airport.id, outcome=outcome,
            )
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.effective_decision == outcome
            assert state.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION
            assert state.is_identity_confirmed is expect_confirmed


# ---------------------------------------------------------------------------
# No "confirmed once = confirmed forever" / original confirmed + later negative
# ---------------------------------------------------------------------------


class TestNeverConfirmedForever:
    def test_confirmed_then_reject_is_not_confirmed(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="flip", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            _add_evaluation(session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.ATTACH_CONFIRMED, created_at=datetime.now(UTC) - timedelta(seconds=10))
            _add_evaluation(session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.REJECT_CROSS_AIRPORT)
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.effective_decision == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert not state.is_identity_confirmed

    def test_original_confirmed_later_reject_not_eligible(self):
        """§9/§31: original historical decision is ATTACH_CONFIRMED, a
        later governed evaluation is REJECT_CROSS_AIRPORT - the latest
        evaluation is authoritative for CURRENT eligibility, the stale
        original positive result is never silently reused. The historical
        field itself remains untouched."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            fragment = CandidateFragment(artifact_identity=_artifact("confirmed-then-reject"), source_locator="p1", raw_text="FOO memo.", airport_identifiers=frozenset({"FOO"}))
            from app.services.discovery_evidence_persistence import persist_discovery_fragment
            from app.services.evidence_attachment_guard import CandidateAirport

            result = persist_discovery_fragment(session, _meta("confirmed-then-reject-doc"), fragment, [CandidateAirport(id=airport.id, name=airport.name, identifiers=frozenset({"FOO"}))])
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"

            _add_evaluation(session, source_assertion_id=result.source_assertion_id, snapshot_id=result.attached_evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.REJECT_CROSS_AIRPORT)
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=result.source_assertion_id)
            assert state.effective_decision == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert not state.is_identity_confirmed
            assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"


# ---------------------------------------------------------------------------
# Repeated evaluations / tie-break
# ---------------------------------------------------------------------------


class TestRepeatedEvaluationsAndTieBreak:
    def test_sequence_follows_only_latest(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="sequence", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            sequence = [
                AttachmentOutcome.INSUFFICIENT_IDENTITY, AttachmentOutcome.ATTACH_CONFIRMED,
                AttachmentOutcome.ATTACH_PROVISIONAL, AttachmentOutcome.ATTACH_CONFIRMED,
                AttachmentOutcome.REJECT_CROSS_AIRPORT, AttachmentOutcome.ATTACH_CONFIRMED,
            ]
            for i, outcome in enumerate(sequence):
                _add_evaluation(
                    session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id,
                    airport_id=airport.id, outcome=outcome, created_at=datetime.now(UTC) - timedelta(seconds=(len(sequence) - i)),
                )
                state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
                assert state.effective_decision == outcome
                assert state.is_identity_confirmed == (outcome == AttachmentOutcome.ATTACH_CONFIRMED)

    def test_identical_timestamps_break_tie_by_id(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="tie-break", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            same_time = datetime.now(UTC)
            first = _add_evaluation(session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.ATTACH_CONFIRMED, created_at=same_time)
            second = _add_evaluation(session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.REJECT_CROSS_AIRPORT, created_at=same_time)
            assert first.id < second.id
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            # higher id wins the tie -> the row added second (REJECT)
            assert state.latest_evaluation_id == second.id
            assert state.effective_decision == AttachmentOutcome.REJECT_CROSS_AIRPORT


# ---------------------------------------------------------------------------
# Candidate-linked firewall
# ---------------------------------------------------------------------------


class TestCandidateLinkedFirewall:
    def test_unresolved_candidate_linked_assertion_never_eligible_via_evaluation(self):
        with Session(_engine()) as session:
            discovered = resolve_or_persist_discovery_identity(
                session, _meta("still-linked-doc"),
                CandidateFragment(artifact_identity=_artifact("still-linked"), source_locator="p1", raw_text="Foo Regional Airport memo.", airport_names=frozenset({"Foo Regional Airport"})),
                [],
            )
            assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assertion = session.get(SourceAssertion, discovered.source_assertion_id)
            assert assertion.airport_id is None
            assert assertion.unknown_airport_candidate_id is not None

            # a synthetic evaluation exists for this assertion even though it
            # is not properly resolvable via EB4 itself (EB4 refuses to run
            # against an unresolved assertion) - only reachable via direct
            # ORM construction, simulating DB corruption/bypass.
            _add_evaluation(session, source_assertion_id=assertion.id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=999, outcome=AttachmentOutcome.ATTACH_CONFIRMED)

            state = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert state.latest_evaluation_id is None
            assert not state.is_identity_confirmed


# ---------------------------------------------------------------------------
# Evaluation-Airport consistency
# ---------------------------------------------------------------------------


class TestEvaluationAirportConsistency:
    def test_evaluation_for_different_airport_is_inconsistent_not_eligible(self):
        with Session(_engine()) as session:
            airport_a = _seed_airport(session, name="Airport A", iata_code="AAA")
            airport_b = _seed_airport(session, name="Airport B", iata_code="BBB")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="airport-mismatch", raw_text="Airport A memo.", matched_airport_id=airport_a.id,
                airport_names=frozenset({"Airport A"}),
            )
            # simulate corrupted/stale evaluation pointing at a DIFFERENT
            # airport than the assertion's own current airport_id
            _add_evaluation(session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport_b.id, outcome=AttachmentOutcome.ATTACH_CONFIRMED)

            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.basis == EffectiveIdentityGuardDecisionBasis.INCONSISTENT_REEVALUATION
            assert not state.is_identity_confirmed
            assert state.effective_decision == state.original_decision
            assert state.latest_evaluation_id is not None  # reported for audit, just not trusted


# ---------------------------------------------------------------------------
# Malformed / failure states
# ---------------------------------------------------------------------------


class TestMalformedState:
    def test_nonexistent_source_assertion_raises(self):
        with Session(_engine()) as session:
            with pytest.raises(SourceAssertionNotFoundError):
                resolve_effective_identity_guard_decision(session, source_assertion_id=999999)

    def test_zero_evaluations_falls_back_cleanly(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="zero-eval", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.latest_evaluation_id is None
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

    def test_pre_eb2_migration_database_falls_back_to_historical_decision(self):
        """Adversarial-review finding, HIGH PRIORITY: the current real
        production database has NEVER run the EB2 migration - no
        identity_guard_evaluations table exists at all. Before this fix,
        resolve_effective_identity_guard_decision() (and therefore
        persist_intelligence_review()/persist_promotion_policy(), its two
        real callers) raised a raw OperationalError for EVERY
        SourceAssertion, including rows with no relationship to EB1-EB5
        whatsoever - a severe backward-compatibility regression that
        would have broken existing intelligence-review/promotion-policy
        functionality merely by deploying this code before the real DB
        is ever migrated. A database missing this table entirely must
        fall back cleanly to the historical decision, exactly as if EB4
        had never been built."""
        engine = create_engine("sqlite:///:memory:")
        no_eb_tables = [t for t in Base.metadata.sorted_tables if t.name not in ("source_assertion_evidence_bags", "identity_guard_evaluations")]
        Base.metadata.create_all(engine, tables=no_eb_tables)
        with Session(engine) as session:
            airport = _seed_airport(session, iata_code="FOO")
            source = Source(title="t", source_type="test", reliability_level="official", external_id="discovery:pre-eb2")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                source_locator="loc-1", raw_fragment_hash="hash-1", artifact_identity="artifact-1",
                raw_relevant_text="x", identity_guard_decision="ATTACH_CONFIRMED", identity_guard_reason="x",
            )
            session.add(assertion)
            session.flush()

            state = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert state.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert state.latest_evaluation_id is None
            assert state.is_identity_confirmed

    def test_malformed_present_evaluations_table_fails_loud_not_silently_zero(self):
        """The flip side of the fix above: a table that EXISTS but is
        malformed (a broken/partial migration, not "never migrated") must
        never be silently reinterpreted as "zero evaluations" - that
        would hide real corruption. Only table ABSENCE gets the safe
        fallback; table PRESENCE always reaches the real query, and any
        genuine structural failure there propagates."""
        from sqlalchemy import text as sa_text
        from sqlalchemy.exc import OperationalError

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            conn.execute(sa_text("DROP TABLE identity_guard_evaluations"))
            conn.execute(sa_text("CREATE TABLE identity_guard_evaluations (id INTEGER PRIMARY KEY, source_assertion_id INTEGER)"))
            conn.commit()
        with Session(engine) as session:
            airport = _seed_airport(session, iata_code="FOO")
            source = Source(title="t", source_type="test", reliability_level="official", external_id="discovery:broken-eb2")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                source_locator="loc-1", raw_fragment_hash="hash-1", artifact_identity="artifact-1",
                raw_relevant_text="x", identity_guard_decision="ATTACH_CONFIRMED", identity_guard_reason="x",
            )
            session.add(assertion)
            session.flush()

            with pytest.raises(OperationalError):
                resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

    def test_schema_check_never_opens_a_second_connection(self):
        """Regression coverage for the exact defect class EB3's own
        adversarial review already found once: sqlalchemy.inspect(session.get_bind())/
        engine.connect() opens a second, independent Connection that can
        corrupt an in-memory SQLite Session's own open transaction.
        _identity_guard_evaluations_table_exists() must use the Session's
        own connection instead - proven directly by connection identity,
        not merely inferred from other tests passing."""
        with Session(_engine()) as session:
            dbapi_connection_before = session.connection().connection
            eb5_module._identity_guard_evaluations_table_exists(session)
            dbapi_connection_after = session.connection().connection
            assert dbapi_connection_before is dbapi_connection_after


# ---------------------------------------------------------------------------
# Legacy assertions
# ---------------------------------------------------------------------------


class TestLegacyAssertions:
    def test_legacy_no_snapshot_no_evaluation_unchanged(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            source = Source(title="t", source_type="web_discovery", external_id="discovery:legacy-eb5")
            session.add(source)
            session.flush()
            legacy = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text="legacy", source_locator="p1", raw_fragment_hash="a" * 64,
                artifact_identity="art:legacy-eb5", evidence_quality="unverified_candidate", review_state="unreviewed",
                identity_guard_decision="ATTACH_CONFIRMED", identity_guard_reason="legacy pre-EB3 decision",
            )
            session.add(legacy)
            session.flush()
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=legacy.id)
            assert state.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert state.latest_evaluation_id is None


# ---------------------------------------------------------------------------
# Migration-chain MATCH / CREATE lifecycles
# ---------------------------------------------------------------------------


class TestMigrationChainLifecycle:
    def _migrated_db(self, tmp_path, name: str):
        db = tmp_path / name
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_reviews")
        conn.execute("DROP TABLE unknown_airport_candidates")
        conn.execute("DROP TABLE source_assertion_evidence_bags")
        conn.execute("DROP TABLE identity_guard_evaluations")
        replacement = "source_assertions__presetup"
        conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
        quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
        conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
        conn.execute("DROP TABLE source_assertions")
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
        conn.commit()
        conn.close()
        uac2a_migration.upgrade(db)
        uac2b_migration.upgrade(db)
        eb2_migration.upgrade(db)
        return db

    def test_full_match_lifecycle_through_effective_state(self, tmp_path):
        db = self._migrated_db(tmp_path, "eb5_match.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="migration-match", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            session.commit()
            assertion = session.get(SourceAssertion, discovered.source_assertion_id)
            original = assertion.identity_guard_decision

            eb4_result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=review.id)
            session.commit()
            assert eb4_result.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.is_identity_confirmed
            assert state.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION
            assert assertion.identity_guard_decision == original
        engine.dispose()

    def test_full_create_lifecycle_no_topology_then_topology_added(self, tmp_path):
        db = self._migrated_db(tmp_path, "eb5_create.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            discovered = resolve_or_persist_discovery_identity(
                session, _meta("migration-create-doc"),
                CandidateFragment(artifact_identity=_artifact("migration-create"), source_locator="p1", raw_text="Brand New Airport runway 09/27 memo.", airport_names=frozenset({"Brand New Airport"}), runway_pairs=frozenset({"09/27"})),
                [],
            )
            session.commit()
            candidate = session.get(UnknownAirportCandidate, discovered.unknown_airport_candidate_id)
            review = record_unknown_airport_candidate_review(session, candidate, action="CREATE_NEW_AIRPORT", reason="new", reviewer="tester")
            session.flush()
            # ERG4 precondition: the candidate needs a current admission-
            # relevant automatic assessment plus a current human CONFIRM
            # before create_airport_from_approved_candidate() will proceed.
            erg2_result = persist_unknown_airport_candidate_relevance_assessment(
                session, candidate,
                observations=(EmasEvidenceObservation(EvidenceClass.A_EXPLICIT_EMAS, basis="erg4 fixture"),),
                source_assertion_ids=(discovered.source_assertion_id,),
            )
            session.commit()
            record_unknown_airport_candidate_relevance_review(
                session, candidate, basis_assessment_id=erg2_result.assessment.id,
                action="CONFIRM_EMAS_RELEVANT", reviewer="human:erg4-fixture", reason="erg4 fixture confirm",
            )
            session.commit()
            created = create_airport_from_approved_candidate(session, candidate_id=candidate.id, review_id=review.id, name="Brand New Airport", country="USA")
            session.commit()

            eb4_result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=review.id)
            session.commit()
            assert eb4_result.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert not state.is_identity_confirmed

            new_airport = session.get(Airport, created.created_airport_id)
            runway = Runway(airport_id=new_airport.id, designation="09/27")
            session.add(runway)
            session.flush()
            session.add(RunwayEnd(runway_id=runway.id, designation="09"))
            session.add(RunwayEnd(runway_id=runway.id, designation="27"))
            session.flush()

            eb4_result_2 = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            session.commit()
            assert eb4_result_2.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            state_2 = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state_2.is_identity_confirmed
            assert state_2.latest_evaluation_id == eb4_result_2.identity_guard_evaluation_id
        engine.dispose()


# ---------------------------------------------------------------------------
# International
# ---------------------------------------------------------------------------


class TestInternational:
    @pytest.mark.parametrize("name", ["Arlanda Regional", "Aeroporto Regional", "成田リージョナル空港"])
    def test_effective_state_works_for_international_names(self, name):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name=name, country="XX")
            discovered, review, resolved = _full_match_lifecycle(
                session, key=f"intl-{abs(hash(name))}", raw_text=f"{name} memo.", matched_airport_id=airport.id,
                airport_names=frozenset({name}),
            )
            _add_evaluation(session, source_assertion_id=discovered.source_assertion_id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.ATTACH_CONFIRMED)
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert state.is_identity_confirmed


# ---------------------------------------------------------------------------
# Governed Signal creation firewall (§20) - proves EB5 eligibility for
# intelligence review does NOT bypass Signal creation's own, unmodified
# gate, which still checks the permanent HISTORICAL identity_guard_decision
# column directly (governed_signal_creation.py is not touched by EB5 at
# all - this is a firewall proof, not new EB5 behavior).
# ---------------------------------------------------------------------------


class TestGovernedSignalCreationFirewall:
    """At the time this test was first written, Signal creation's own gate
    independently re-checked the RAW identity_guard_decision column, so a
    row with historical INSUFFICIENT_IDENTITY but a later, genuinely
    governed LATEST_REEVALUATION confirmation could reach intelligence
    review but never actually have a Signal created for it - a real,
    disclosed inconsistency. A LATER, separate mission ("RWI - Raw-vs-
    Effective Signal Creation Gate - Narrow Fix") closed it by making
    create_signal_from_approved_review() also consume EB5's effective
    decision, mirroring the reviewer-action approval gate's own identical
    fix - so that same shape now correctly SUCCEEDS here, once the other,
    unrelated governance gates (intelligence review, promotion policy, a
    recorded ReviewerAction) are also satisfied, exactly as they would be
    for any other effectively-confirmed row."""

    def test_historical_insufficient_latest_confirmed_now_allows_signal_creation(self):
        from app.models import ReviewerAction
        from app.services.governed_signal_creation import create_signal_from_approved_review

        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="signal-firewall", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            assertion = session.get(SourceAssertion, discovered.source_assertion_id)
            assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"

            _add_evaluation(session, source_assertion_id=assertion.id, snapshot_id=discovered.evidence_bag_snapshot_id, airport_id=airport.id, outcome=AttachmentOutcome.ATTACH_CONFIRMED)
            state = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert state.is_identity_confirmed  # eligible for intelligence review...

            # ...and now, correctly, for Signal creation too, once the
            # other, unrelated governance gates are also satisfied.
            assertion.intelligence_review_decision = "REVIEW_REQUIRED"
            assertion.promotion_policy_decision = "HUMAN_REVIEW_REQUIRED"
            session.add(ReviewerAction(
                source_assertion_id=assertion.id, action="APPROVE_SIGNAL",
                reason="Effectively confirmed via LATEST_REEVALUATION.", reviewer="human:tester",
            ))
            session.commit()

            result = create_signal_from_approved_review(
                session, assertion, title="t", category="physical_installation", confidence="high",
            )
            assert result.created is True
            assert session.query(Signal).count() == 1
            assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"  # raw history untouched


# ---------------------------------------------------------------------------
# Information firewall
# ---------------------------------------------------------------------------


class TestNoAutoflushOfUnrelatedState:
    def test_pending_unrelated_invalid_object_not_flushed_by_resolver(self):
        """Adversarial-review finding: this entire resolver is read-only,
        but session.get()/session.execute()/session.scalars() all
        autoflush pending state by default just like any other query - a
        caller with OTHER unrelated pending work in the same Session
        could have that state silently and prematurely flushed merely by
        asking for an effective identity decision. The exact class of bug
        EB4's own adversarial review already found and fixed once, now
        found again and fixed here. source_assertion_id is captured as a
        plain int BEFORE the unrelated pending object is added - matching
        the resolver's own actual public API and this project's
        established test convention for this exact attack (see
        tests/test_resolved_candidate_evidence_reevaluation.py's own
        identically-named test)."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-autoflush", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            source_assertion_id = discovered.source_assertion_id
            session.commit()

            invalid_source = Source(title=None, source_type="web_discovery", external_id="discovery:invalid-pending-eb5")
            session.add(invalid_source)

            state = resolve_effective_identity_guard_decision(session, source_assertion_id=source_assertion_id)
            assert state.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

            session.expunge(invalid_source)


class TestInformationFirewall:
    def test_no_write_side_effects_no_forbidden_imports(self):
        source = inspect_module.getsource(eb5_module)
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        forbidden = ("governed_signal_creation", "promotion_policy", "unknown_airport_candidate_resolution", "unknown_airport_candidate_persistence", "requests", "urllib", "httpx")
        for m in imported:
            assert not any(term in m for term in forbidden), m
        for term in ("session.add(", "session.commit(", "session.flush(", "import requests"):
            assert term not in source

    def test_no_evaluation_or_signal_created_by_resolver(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-side-effects", raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            before_eval_count = session.query(IdentityGuardEvaluation).count()
            resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            resolve_effective_identity_guard_decision(session, source_assertion_id=discovered.source_assertion_id)
            assert session.query(IdentityGuardEvaluation).count() == before_eval_count
            assert session.query(Signal).count() == 0
