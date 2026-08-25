"""Tests for app/services/resolved_candidate_evidence_reevaluation.py (EB4,
docs/architecture/rwi-eb4-resolved-evidence-reevaluation-report.md, Slice 4
of docs/architecture/rwi-full-evidencebag-persistence-design.md).

Isolated, in-memory (or tmp_path, for the real-migration-chain tests)
SQLite databases only - never the real one. Fixtures are entirely
fictional.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.evidence_attachment_guard import AttachmentOutcome
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
import app.services.resolved_candidate_evidence_reevaluation as eb4_module
from app.services.resolved_candidate_evidence_reevaluation import (
    IdentityGuardReevaluationResult,
    MissingEvidenceBagSnapshotError,
    SourceAssertionNotFoundError,
    TamperedEvidenceBagSnapshotError,
    UnresolvedSourceAssertionError,
    reevaluate_resolved_candidate_evidence,
)
import scripts.migrate_evidence_bag_persistence_eb2 as eb2_migration
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _engine_with_fk_enforced():
    """A plain sqlite:///:memory: engine does NOT enforce foreign keys by
    default. The listener must be registered BEFORE the first connection
    is ever opened (SingletonThreadPool caches and reuses one physical
    connection per engine) - matches tests/test_evidence_bag_persistence.py's
    own established precedent exactly."""
    from sqlalchemy import event

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, *, name="Foo Regional Airport", country="USA", **kwargs) -> Airport:
    airport = Airport(name=name, country=country, **kwargs)
    session.add(airport)
    session.flush()
    return airport


def _add_runway(session, airport: Airport, designation: str, ends: "tuple[str, ...]" = ()) -> Runway:
    runway = Runway(airport_id=airport.id, designation=designation)
    session.add(runway)
    session.flush()
    for end_designation in ends:
        session.add(RunwayEnd(runway_id=runway.id, designation=end_designation))
    session.flush()
    return runway


def _artifact(name: str) -> str:
    return f"eb4-test-artifact:{name}"


def _meta(document_identity: str) -> DiscoverySourceMetadata:
    return DiscoverySourceMetadata(document_identity=document_identity, title="Test document")


def _discover_unknown(session, *, key: str, raw_text: str, **fragment_kwargs):
    fragment = CandidateFragment(artifact_identity=_artifact(key), source_locator="p1", raw_text=raw_text, **fragment_kwargs)
    return resolve_or_persist_discovery_identity(session, _meta(f"{key}-doc"), fragment, [])


def _resolve_to_existing(session, *, candidate_id: int, matched_airport_id: int, reviewer: str = "tester"):
    candidate = session.get(UnknownAirportCandidate, candidate_id)
    review = record_unknown_airport_candidate_review(
        session, candidate, action="MATCH_EXISTING_AIRPORT", reason="test match",
        reviewer=reviewer, matched_airport_id=matched_airport_id,
    )
    session.flush()
    resolved = resolve_candidate_to_existing_airport(session, candidate_id=candidate_id, review_id=review.id)
    return review, resolved


def _resolve_to_new(session, *, candidate_id: int, name: str, country: str = "USA", reviewer: str = "tester"):
    candidate = session.get(UnknownAirportCandidate, candidate_id)
    review = record_unknown_airport_candidate_review(
        session, candidate, action="CREATE_NEW_AIRPORT", reason="test create", reviewer=reviewer,
    )
    session.flush()
    created = create_airport_from_approved_candidate(session, candidate_id=candidate_id, review_id=review.id, name=name, country=country)
    return review, created


def _full_match_lifecycle(session, *, key: str, raw_text: str, matched_airport_id: int, **fragment_kwargs):
    discovered = _discover_unknown(session, key=key, raw_text=raw_text, **fragment_kwargs)
    assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
    review, resolved = _resolve_to_existing(
        session, candidate_id=discovered.unknown_airport_candidate_id, matched_airport_id=matched_airport_id,
    )
    return discovered, review, resolved


# ---------------------------------------------------------------------------
# A/F/G. Missing assertion / missing Airport / still candidate-linked
# ---------------------------------------------------------------------------


class TestPreconditionFailures:
    def test_missing_assertion_raises(self):
        with Session(_engine()) as session:
            with pytest.raises(SourceAssertionNotFoundError):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=999)
            assert session.query(IdentityGuardEvaluation).count() == 0

    def test_still_candidate_linked_assertion_raises(self):
        with Session(_engine()) as session:
            discovered = _discover_unknown(
                session, key="still-linked", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            with pytest.raises(UnresolvedSourceAssertionError):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert session.query(IdentityGuardEvaluation).count() == 0

    def test_fully_unresolved_assertion_raises(self):
        with Session(_engine()) as session:
            discovered = _discover_unknown(session, key="fully-unresolved", raw_text="Vague EMAS mention only.")
            assert discovered.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert discovered.attached_airport_id is None
            with pytest.raises(UnresolvedSourceAssertionError):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)

    def test_dangling_airport_id_raises_value_error(self):
        """§24: only reachable via a malformed DB with FK enforcement
        disabled - a real SourceAssertion.airport_id FK makes this
        structurally impossible otherwise."""
        with Session(_engine()) as session:
            session.execute(text("PRAGMA foreign_keys=OFF"))
            source = Source(title="t", source_type="web_discovery", external_id="discovery:dangling-doc")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=999999, assertion_type="project_construction",
                raw_relevant_text="x", source_locator="p1", raw_fragment_hash="h" * 64,
                artifact_identity="art:dangling", evidence_quality="unverified_candidate", review_state="unreviewed",
            )
            session.add(assertion)
            session.flush()
            with pytest.raises(ValueError, match="does not reference an existing Airport"):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=assertion.id)


# ---------------------------------------------------------------------------
# B. Missing snapshot (legacy row)
# ---------------------------------------------------------------------------


class TestMissingSnapshot:
    def test_legacy_resolved_assertion_without_snapshot_fails_closed(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source = Source(title="t", source_type="web_discovery", external_id="discovery:legacy-doc")
            session.add(source)
            session.flush()
            legacy_assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text="legacy text", source_locator="p1", raw_fragment_hash="a" * 64,
                artifact_identity="art:legacy", evidence_quality="unverified_candidate", review_state="unreviewed",
                identity_guard_decision="ATTACH_CONFIRMED", identity_guard_reason="legacy pre-EB3 decision",
            )
            session.add(legacy_assertion)
            session.flush()
            with pytest.raises(MissingEvidenceBagSnapshotError):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=legacy_assertion.id)
            assert session.query(IdentityGuardEvaluation).count() == 0


# ---------------------------------------------------------------------------
# C/D/E. Tamper attacks - payload/hash/schema mismatch, malformed JSON,
# unsupported version. Guard call count must be zero.
# ---------------------------------------------------------------------------


class TestTamperAttacks:
    def _lifecycle_snapshot_id(self, session, key="tamper"):
        airport = _seed_airport(session, iata_code="FOO")
        discovered, review, resolved = _full_match_lifecycle(
            session, key=key, raw_text="Foo Regional Airport memo.", matched_airport_id=airport.id,
            airport_names=frozenset({"Foo Regional Airport"}),
        )
        session.flush()
        return discovered.source_assertion_id, discovered.evidence_bag_snapshot_id

    def _assert_guard_never_called(self, session, source_assertion_id, monkeypatch, expect):
        calls = {"n": 0}
        original = eb4_module.evaluate_attachment

        def _spy(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(eb4_module, "evaluate_attachment", _spy)
        with pytest.raises(expect):
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=source_assertion_id)
        assert calls["n"] == 0
        assert session.query(IdentityGuardEvaluation).count() == 0

    def test_payload_changed_hash_stale(self, monkeypatch):
        with Session(_engine()) as session:
            sa_id, snap_id = self._lifecycle_snapshot_id(session)
            snapshot = session.get(SourceAssertionEvidenceBag, snap_id)
            session.execute(
                text("UPDATE source_assertion_evidence_bags SET evidence_bag_json = :p WHERE id = :id"),
                {"p": snapshot.evidence_bag_json.replace("Foo Regional Airport", "Tampered Airport"), "id": snap_id},
            )
            session.expire_all()
            self._assert_guard_never_called(session, sa_id, monkeypatch, TamperedEvidenceBagSnapshotError)

    def test_hash_changed_alone(self, monkeypatch):
        with Session(_engine()) as session:
            sa_id, snap_id = self._lifecycle_snapshot_id(session)
            session.execute(
                text("UPDATE source_assertion_evidence_bags SET evidence_bag_hash = :h WHERE id = :id"),
                {"h": "0" * 64, "id": snap_id},
            )
            session.expire_all()
            self._assert_guard_never_called(session, sa_id, monkeypatch, TamperedEvidenceBagSnapshotError)

    def test_schema_version_column_changed(self, monkeypatch):
        with Session(_engine()) as session:
            sa_id, snap_id = self._lifecycle_snapshot_id(session)
            session.execute(
                text("UPDATE source_assertion_evidence_bags SET schema_version = 2 WHERE id = :id"), {"id": snap_id},
            )
            session.expire_all()
            self._assert_guard_never_called(session, sa_id, monkeypatch, TamperedEvidenceBagSnapshotError)

    def test_malformed_json_payload(self, monkeypatch):
        with Session(_engine()) as session:
            sa_id, snap_id = self._lifecycle_snapshot_id(session)
            from app.services.evidence_bag_serialization import hash_serialized_evidence_bag

            broken_payload = "{not valid json"
            session.execute(
                text("UPDATE source_assertion_evidence_bags SET evidence_bag_json = :p, evidence_bag_hash = :h WHERE id = :id"),
                {"p": broken_payload, "h": hash_serialized_evidence_bag(broken_payload), "id": snap_id},
            )
            session.expire_all()
            self._assert_guard_never_called(session, sa_id, monkeypatch, TamperedEvidenceBagSnapshotError)

    def test_unsupported_embedded_schema_version(self, monkeypatch):
        with Session(_engine()) as session:
            sa_id, snap_id = self._lifecycle_snapshot_id(session)
            from app.services.evidence_bag_serialization import hash_serialized_evidence_bag
            import json

            snapshot = session.get(SourceAssertionEvidenceBag, snap_id)
            payload = json.loads(snapshot.evidence_bag_json)
            payload["schema_version"] = 99
            tampered = json.dumps(payload, sort_keys=True)
            session.execute(
                text("UPDATE source_assertion_evidence_bags SET evidence_bag_json = :p, evidence_bag_hash = :h, schema_version = 99 WHERE id = :id"),
                {"p": tampered, "h": hash_serialized_evidence_bag(tampered), "id": snap_id},
            )
            session.expire_all()
            self._assert_guard_never_called(session, sa_id, monkeypatch, TamperedEvidenceBagSnapshotError)


# ---------------------------------------------------------------------------
# H/I/J/K. Outcome matrix
# ---------------------------------------------------------------------------


class TestOutcomeMatrix:
    def test_attach_confirmed(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="confirmed", raw_text="FOO airport identifier memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
            assert result.evaluated_against_airport_id == airport.id

    def test_reject_cross_airport_proves_uac5_gap_closed(self):
        """The exact UAC5 failure mode: a contradicting_issuers fact that
        would have been LOST by the old lossy comma-joined raw_* columns
        must NOT be lost here - EB3's lossless snapshot preserves it, so
        re-evaluation correctly rejects rather than falsely confirming."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            fragment = CandidateFragment(
                artifact_identity=_artifact("contradict"), source_locator="p1",
                raw_text="Foo Regional Airport memo, actually issued by a different authority.",
                airport_names=frozenset({"Foo Regional Airport"}),
                contradicting_issuers=frozenset({"Definitely Not FOO Authority"}),
            )
            discovered = resolve_or_persist_discovery_identity(session, _meta("contradict-doc"), fragment, [])
            assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            review, resolved = _resolve_to_existing(
                session, candidate_id=discovered.unknown_airport_candidate_id, matched_airport_id=airport.id,
            )

            # sanity: the snapshot really does carry the contradiction fact
            snapshot = session.get(SourceAssertionEvidenceBag, discovered.evidence_bag_snapshot_id)
            assert "Definitely Not FOO Authority" in snapshot.evidence_bag_json

            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert result.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert result.outcome != AttachmentOutcome.ATTACH_CONFIRMED

    def test_lossy_bag_would_have_falsely_confirmed_full_bag_correctly_rejects(self):
        """§17's own specific demand: not merely proving the full bag
        rejects, but explicitly constructing the counterfactual UAC5
        worried about - a bag with the SAME definitive positive evidence
        MINUS the contradiction fact (exactly what the old lossy,
        comma-joined raw_* SourceAssertion columns could carry, since they
        have no field for contradicting_issuers at all) would flip the
        outcome to a FALSE ATTACH_CONFIRMED. An identifier match is
        definitive alone (evaluate_attachment()'s own rule 3) - strong
        enough that only an unconditional, first-checked contradiction can
        override it."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            fragment = CandidateFragment(
                artifact_identity=_artifact("counterfactual"), source_locator="p1",
                raw_text="FOO memo, actually issued by a different authority.",
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
                contradicting_issuers=frozenset({"Definitely Not FOO Authority"}),
            )
            discovered = resolve_or_persist_discovery_identity(session, _meta("counterfactual-doc"), fragment, [])
            _resolve_to_existing(session, candidate_id=discovered.unknown_airport_candidate_id, matched_airport_id=airport.id)

            full_result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert full_result.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT

            # The counterfactual: the SAME identifier evidence, but with
            # the contradiction dropped - exactly what a lossy
            # reconstruction (no persisted contradicting_issuers field)
            # would have produced. Built directly, not through the
            # persistence layer (there is no way to persist a "lossy" bag
            # through the modern, lossless pipeline - that is the whole
            # point EB1-EB4 fixed).
            from app.services.evidence_attachment_guard import EvidenceBag, evaluate_attachment

            lossy_bag = EvidenceBag(identifiers=frozenset({"FOO"}))  # contradiction silently dropped
            candidate = eb4_module.candidate_airport_from_airport_like(session.get(Airport, airport.id))
            lossy_decision = evaluate_attachment(candidate, lossy_bag)
            assert lossy_decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            # EB4's own real re-evaluation, using the actual preserved
            # snapshot, never exhibits this false positive.
            assert full_result.outcome != lossy_decision.outcome

    def test_insufficient_identity_remains_insufficient(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Totally Unrelated Airport", iata_code="XYZ")
            fragment = CandidateFragment(
                artifact_identity=_artifact("insufficient"), source_locator="p1",
                raw_text="Some airport somewhere plans EMAS work.",
                airport_names=frozenset({"Some Placeholder Name"}),
            )
            discovered = resolve_or_persist_discovery_identity(session, _meta("insufficient-doc"), fragment, [])
            assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            _resolve_to_existing(session, candidate_id=discovered.unknown_airport_candidate_id, matched_airport_id=airport.id)

            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert result.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY

    def test_review_required_is_structurally_unreachable(self):
        """§11/§21: EB4 deliberately calls the single-candidate
        evaluate_attachment() primitive, never
        evaluate_attachment_for_candidates() - REVIEW_REQUIRED can only
        ever be produced by comparing MULTIPLE candidates (that function's
        own docstring), so a single-candidate re-evaluation can never
        produce it. Proven structurally here, not merely by absence of a
        counterexample."""
        # eb4_module never imports evaluate_attachment_for_candidates at
        # all - a structural guarantee, not merely a source-text search
        # that could be fooled by (or false-positive against) prose in a
        # docstring explaining the design choice.
        assert not hasattr(eb4_module, "evaluate_attachment_for_candidates")
        assert eb4_module.evaluate_attachment is not None


# ---------------------------------------------------------------------------
# 17/L. Evaluation persistence + append-only repeated re-evaluation
# ---------------------------------------------------------------------------


class TestEvaluationPersistenceAndRepeat:
    def test_evaluation_fields_correct(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="fields", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            result = reevaluate_resolved_candidate_evidence(
                session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=review.id,
            )
            evaluation = session.get(IdentityGuardEvaluation, result.identity_guard_evaluation_id)
            assert evaluation.source_assertion_id == discovered.source_assertion_id
            assert evaluation.evidence_bag_snapshot_id == discovered.evidence_bag_snapshot_id
            assert evaluation.evaluated_against_airport_id == airport.id
            assert evaluation.outcome == AttachmentOutcome.ATTACH_CONFIRMED.value
            assert evaluation.triggering_review_id == review.id
            assert evaluation.created_at is not None

    def test_triggering_review_id_optional_and_none_by_default(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-review-id", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert result.triggering_review_id is None
            evaluation = session.get(IdentityGuardEvaluation, result.identity_guard_evaluation_id)
            assert evaluation.triggering_review_id is None

    def test_invalid_triggering_review_id_raises(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="bad-review-id", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            with pytest.raises(ValueError, match="does not reference an existing"):
                reevaluate_resolved_candidate_evidence(
                    session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=999999,
                )
            assert session.query(IdentityGuardEvaluation).count() == 0

    def test_cross_candidate_triggering_review_id_rejected(self):
        """Adversarial-review finding: a triggering_review_id must not be
        accepted merely because it references SOME real review row - a
        review belonging to a completely unrelated UnknownAirportCandidate
        (resolved to a DIFFERENT Airport) must never be recordable as if
        it caused THIS assertion's own resolution. Proven reachable
        through the public API before this fix; must now fail loud."""
        with Session(_engine()) as session:
            airport_a = _seed_airport(session, name="Airport A", iata_code="AAA")
            airport_b = _seed_airport(session, name="Airport B", iata_code="BBB")
            d_a, review_a, _ = _full_match_lifecycle(
                session, key="prov-a", raw_text="Airport A memo.", matched_airport_id=airport_a.id,
                airport_names=frozenset({"Airport A"}), airport_identifiers=frozenset({"AAA"}),
            )
            d_b, review_b, _ = _full_match_lifecycle(
                session, key="prov-b", raw_text="Airport B memo.", matched_airport_id=airport_b.id,
                airport_names=frozenset({"Airport B"}), airport_identifiers=frozenset({"BBB"}),
            )
            with pytest.raises(ValueError, match="refusing to record a false causal link"):
                reevaluate_resolved_candidate_evidence(
                    session, source_assertion_id=d_a.source_assertion_id, triggering_review_id=review_b.id,
                )
            assert session.query(IdentityGuardEvaluation).count() == 0

    def test_reject_candidate_review_never_a_valid_triggering_review(self):
        """A REJECT_CANDIDATE/DEFER review's own candidate never has
        resolved_airport_id set - such a review could never have caused
        any canonical attribution, so it must be rejected exactly like a
        cross-candidate review, with no special-casing needed."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, match_review, _ = _full_match_lifecycle(
                session, key="reject-review", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            other_discovered = _discover_unknown(
                session, key="never-resolved", raw_text="Bar Municipal Airport memo.",
                airport_names=frozenset({"Bar Municipal Airport"}),
            )
            other_candidate = session.get(UnknownAirportCandidate, other_discovered.unknown_airport_candidate_id)
            reject_review = record_unknown_airport_candidate_review(
                session, other_candidate, action="REJECT_CANDIDATE", reason="not real", reviewer="tester",
            )
            session.flush()
            with pytest.raises(ValueError, match="refusing to record a false causal link"):
                reevaluate_resolved_candidate_evidence(
                    session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=reject_review.id,
                )

    def test_repeated_reevaluation_is_never_deduplicated(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="repeat", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            r1 = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            r2 = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert r1.identity_guard_evaluation_id != r2.identity_guard_evaluation_id
            assert session.query(IdentityGuardEvaluation).filter_by(source_assertion_id=discovered.source_assertion_id).count() == 2


# ---------------------------------------------------------------------------
# M. Changed topology between two evaluations
# ---------------------------------------------------------------------------


class TestChangedTopology:
    def test_second_evaluation_may_differ_first_unchanged(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Foo Regional Airport")  # no identifier code yet
            discovered, review, resolved = _full_match_lifecycle(
                session, key="topology", raw_text="Foo Regional Airport runway 09/27 memo.",
                matched_airport_id=airport.id, airport_names=frozenset({"Foo Regional Airport"}),
                runway_pairs=frozenset({"09/27"}),
            )
            first = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            # only ONE positive category (name) matched -> ATTACH_PROVISIONAL
            assert first.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

            # canonical topology changes: runway 09/27 is now on record.
            # expire_all() forces a fresh read of airport.runways - a
            # long-lived ORM session's already-loaded lazy relationship
            # collection is otherwise cached and would not reflect the
            # newly added row without an explicit refresh (ordinary
            # SQLAlchemy behavior, not something reevaluate_resolved_candidate_evidence()
            # itself needs to manage - a fresh session per call, the
            # normal calling pattern, would see current state naturally).
            _add_runway(session, airport, "09/27", ends=("09", "27"))
            session.flush()
            session.expire_all()

            second = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            # now TWO independent categories (name + runway_topology) -> ATTACH_CONFIRMED
            assert second.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            first_row = session.get(IdentityGuardEvaluation, first.identity_guard_evaluation_id)
            assert first_row.outcome == AttachmentOutcome.ATTACH_PROVISIONAL.value
            second_row = session.get(IdentityGuardEvaluation, second.identity_guard_evaluation_id)
            assert second_row.outcome == AttachmentOutcome.ATTACH_CONFIRMED.value
            assert first_row.id != second_row.id


# ---------------------------------------------------------------------------
# N/O. Historical firewall - original SourceAssertion fields and snapshot
# both unchanged
# ---------------------------------------------------------------------------


class TestFirewalls:
    def test_original_guard_fields_never_mutated(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="firewall-guard", raw_text="Some vague memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Some Vague Name"}),
            )
            assertion = session.get(SourceAssertion, discovered.source_assertion_id)
            before_decision = assertion.identity_guard_decision
            before_reason = assertion.identity_guard_reason

            reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            session.expire_all()

            assertion_after = session.get(SourceAssertion, discovered.source_assertion_id)
            assert assertion_after.identity_guard_decision == before_decision
            assert assertion_after.identity_guard_reason == before_reason

    def test_snapshot_never_mutated(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="firewall-snapshot", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            snapshot_before = session.get(SourceAssertionEvidenceBag, discovered.evidence_bag_snapshot_id)
            payload_before = snapshot_before.evidence_bag_json
            hash_before = snapshot_before.evidence_bag_hash
            schema_before = snapshot_before.schema_version
            created_before = snapshot_before.created_at

            reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            session.expire_all()

            snapshot_after = session.get(SourceAssertionEvidenceBag, discovered.evidence_bag_snapshot_id)
            assert snapshot_after.evidence_bag_json == payload_before
            assert snapshot_after.evidence_bag_hash == hash_before
            assert snapshot_after.schema_version == schema_before
            assert snapshot_after.created_at == created_before


# ---------------------------------------------------------------------------
# P/Q. No Signal creation, no Airport mutation
# ---------------------------------------------------------------------------


class TestNoSideEffects:
    def test_no_signal_created(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-signal", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert session.query(Signal).count() == 0

    def test_airport_row_never_mutated(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-airport-mutation", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            before = (airport.name, airport.iata_code, airport.icao_code, airport.faa_code, airport.country)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            session.expire_all()
            reloaded = session.get(Airport, airport.id)
            after = (reloaded.name, reloaded.iata_code, reloaded.icao_code, reloaded.faa_code, reloaded.country)
            assert before == after
            assert session.query(Airport).count() == 1


# ---------------------------------------------------------------------------
# R. Rollback / transaction atomicity
# ---------------------------------------------------------------------------


class TestTransactionAtomicity:
    def test_flush_failure_leaves_no_evaluation_row(self, monkeypatch):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="rollback", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            session.commit()

            original_flush = session.flush
            call_count = {"n": 0}

            def _boom(*args, **kwargs):
                call_count["n"] += 1
                raise RuntimeError("simulated evaluation flush failure")

            monkeypatch.setattr(session, "flush", _boom)
            with pytest.raises(RuntimeError, match="simulated evaluation flush failure"):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            monkeypatch.setattr(session, "flush", original_flush)
            session.rollback()
            assert session.query(IdentityGuardEvaluation).count() == 0
            assert session.query(SourceAssertionEvidenceBag).count() == 1

    def test_no_hidden_commit(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-commit", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            session.commit()
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            session.rollback()
            assert session.query(IdentityGuardEvaluation).count() == 0


# ---------------------------------------------------------------------------
# Query shape
# ---------------------------------------------------------------------------


class TestQueryShape:
    def test_query_count_bounded_regardless_of_runway_count(self):
        """§26 adversarial-review finding: the first version of this
        service loaded Airport via a plain session.get() with no eager
        loading, so candidate_airport_from_airport_like()'s own iteration
        over airport.runways[*].runway_ends triggered one extra SELECT
        PER RUNWAY (its own docstring explicitly documents this as the
        caller's responsibility to avoid via selectinload - the same
        convention app/static_export/build.py already establishes for
        this identical Airport -> Runway -> RunwayEnd shape). Fixed by
        eager-loading both levels up front. Proven here with a real SQL
        listener, not just by re-reading the source: an airport with 5
        runways must not cost more statements than one with 1."""
        from sqlalchemy import event

        def _count_queries(n_runways: int) -> int:
            engine = _engine()
            with Session(engine) as session:
                airport = _seed_airport(session, iata_code=f"Q{n_runways}")
                for i in range(n_runways):
                    _add_runway(session, airport, f"0{i}/1{i}", ends=(f"0{i}", f"1{i}"))
                discovered, review, resolved = _full_match_lifecycle(
                    session, key=f"query-shape-{n_runways}", raw_text=f"Q{n_runways} memo.",
                    matched_airport_id=airport.id,
                    airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({f"Q{n_runways}"}),
                )
                session.commit()
                source_assertion_id = discovered.source_assertion_id

            with Session(engine) as session2:
                queries = []

                def _log(conn, cursor, statement, parameters, context, executemany):
                    queries.append(statement)

                event.listen(engine, "before_cursor_execute", _log)
                try:
                    reevaluate_resolved_candidate_evidence(session2, source_assertion_id=source_assertion_id)
                finally:
                    event.remove(engine, "before_cursor_execute", _log)
                return len(queries)

        count_one_runway = _count_queries(1)
        count_five_runways = _count_queries(5)
        assert count_one_runway == count_five_runways


# ---------------------------------------------------------------------------
# S. Read-only guard phase / no hidden autoflush of unrelated pending state
# ---------------------------------------------------------------------------


class TestNoAutoflushOfUnrelatedState:
    def test_pending_unrelated_invalid_object_not_flushed_during_guard_phase(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="no-autoflush", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            session.commit()

            # Add a pending, INVALID (NOT NULL violation) unrelated object -
            # if the guard-evaluation phase triggered a hidden autoflush,
            # this would raise here instead of at the caller's own,
            # later, explicit flush/commit.
            invalid_source = Source(title=None, source_type="web_discovery", external_id="discovery:invalid-pending")
            session.add(invalid_source)

            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            session.expunge(invalid_source)


# ---------------------------------------------------------------------------
# T/U. Migration-chain full lifecycle - MATCH and CREATE
# ---------------------------------------------------------------------------


class TestMigrationChainFullLifecycle:
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

    def test_full_lifecycle_match_existing(self, tmp_path):
        db = self._migrated_db(tmp_path, "eb4_lifecycle_match.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            airport = _seed_airport(session, iata_code="FOO")
            session.commit()

            discovered = _discover_unknown(
                session, key="lifecycle-match", raw_text="FOO airport memo.", airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            session.commit()
            assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE

            candidate = session.get(UnknownAirportCandidate, discovered.unknown_airport_candidate_id)
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="matches",
                reviewer="tester", matched_airport_id=airport.id,
            )
            session.flush()
            resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            session.commit()

            assertion = session.get(SourceAssertion, discovered.source_assertion_id)
            assert assertion.airport_id == airport.id
            assert assertion.unknown_airport_candidate_id is None

            result = reevaluate_resolved_candidate_evidence(
                session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=review.id,
            )
            session.commit()
            assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            # every historical layer remains queryable
            assert session.get(SourceAssertionEvidenceBag, discovered.evidence_bag_snapshot_id) is not None
            assert session.get(UnknownAirportCandidateReview, review.id) is not None
            assert session.get(IdentityGuardEvaluation, result.identity_guard_evaluation_id) is not None
            assert session.query(Signal).count() == 0
        engine.dispose()

    def test_full_lifecycle_create_new_airport_no_topology_yet(self, tmp_path):
        """§30: a brand-new Airport created by CREATE_NEW_AIRPORT has no
        Runway topology yet. Runway claims in the original evidence
        cannot be confirmed against nothing - honest outcome is
        NOT forced to ATTACH_CONFIRMED merely because a human created the
        Airport."""
        db = self._migrated_db(tmp_path, "eb4_lifecycle_create.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            discovered = _discover_unknown(
                session, key="lifecycle-create", raw_text="Brand New Airport runway 09/27 memo.",
                airport_names=frozenset({"Brand New Airport"}), runway_pairs=frozenset({"09/27"}),
            )
            session.commit()
            assert discovered.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE

            candidate = session.get(UnknownAirportCandidate, discovered.unknown_airport_candidate_id)
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="new airport", reviewer="tester",
            )
            session.flush()
            # ERG4 precondition: current admission-relevant automatic
            # assessment + current human CONFIRM, before
            # create_airport_from_approved_candidate() will proceed.
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
            created = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name="Brand New Airport", country="USA",
            )
            session.commit()

            new_airport = session.get(Airport, created.created_airport_id)
            assert new_airport.runways == []

            result = reevaluate_resolved_candidate_evidence(
                session, source_assertion_id=discovered.source_assertion_id, triggering_review_id=review.id,
            )
            session.commit()
            # name matches (one positive category), runway claim cannot be
            # confirmed against an empty topology (absence, not
            # contradiction) -> exactly one category -> ATTACH_PROVISIONAL,
            # never a forced ATTACH_CONFIRMED.
            assert result.outcome == AttachmentOutcome.ATTACH_PROVISIONAL
            assert session.query(Signal).count() == 0
        engine.dispose()


# ---------------------------------------------------------------------------
# V. Unicode / international
# ---------------------------------------------------------------------------


class TestInternational:
    @pytest.mark.parametrize(
        "name,raw_text",
        [
            ("Arlanda Regional", "Arlanda Regional flygplats EMAS-arbete, Sverige."),
            ("Aeroporto Regional", "Aeroporto Regional, obras de EMAS, Brasil."),
            ("成田リージョナル空港", "成田リージョナル空港のEMAS工事に関するメモ。"),
        ],
    )
    def test_international_names_survive_full_lifecycle(self, name, raw_text):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name=name, country="XX")
            discovered, review, resolved = _full_match_lifecycle(
                session, key=f"intl-{abs(hash(name))}", raw_text=raw_text, matched_airport_id=airport.id,
                airport_names=frozenset({name}),
            )
            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=discovered.source_assertion_id)
            assert result.outcome == AttachmentOutcome.ATTACH_PROVISIONAL
            snapshot = session.get(SourceAssertionEvidenceBag, discovered.evidence_bag_snapshot_id)
            assert name in snapshot.evidence_bag_json


# ---------------------------------------------------------------------------
# W. Cross-assertion snapshot safety
# ---------------------------------------------------------------------------


class TestCrossAssertionSafety:
    def test_evaluation_always_references_its_own_assertions_own_snapshot(self):
        with Session(_engine()) as session:
            airport_a = _seed_airport(session, name="Airport A", iata_code="AAA")
            airport_b = _seed_airport(session, name="Airport B", iata_code="BBB")
            d_a, _, _ = _full_match_lifecycle(
                session, key="cross-a", raw_text="Airport A memo.", matched_airport_id=airport_a.id,
                airport_names=frozenset({"Airport A"}), airport_identifiers=frozenset({"AAA"}),
            )
            d_b, _, _ = _full_match_lifecycle(
                session, key="cross-b", raw_text="Airport B memo.", matched_airport_id=airport_b.id,
                airport_names=frozenset({"Airport B"}), airport_identifiers=frozenset({"BBB"}),
            )
            r_a = reevaluate_resolved_candidate_evidence(session, source_assertion_id=d_a.source_assertion_id)
            r_b = reevaluate_resolved_candidate_evidence(session, source_assertion_id=d_b.source_assertion_id)
            assert r_a.evidence_bag_snapshot_id == d_a.evidence_bag_snapshot_id
            assert r_b.evidence_bag_snapshot_id == d_b.evidence_bag_snapshot_id
            assert r_a.evidence_bag_snapshot_id != r_b.evidence_bag_snapshot_id

            eval_a = session.get(IdentityGuardEvaluation, r_a.identity_guard_evaluation_id)
            eval_b = session.get(IdentityGuardEvaluation, r_b.identity_guard_evaluation_id)
            assert eval_a.source_assertion_id == d_a.source_assertion_id
            assert eval_b.source_assertion_id == d_b.source_assertion_id

    def test_composite_fk_rejects_cross_assertion_evaluation_at_db_layer(self):
        with Session(_engine_with_fk_enforced()) as session:
            airport_a = _seed_airport(session, name="Airport A", iata_code="AAA")
            airport_b = _seed_airport(session, name="Airport B", iata_code="BBB")
            d_a, _, _ = _full_match_lifecycle(
                session, key="fk-a", raw_text="Airport A memo.", matched_airport_id=airport_a.id,
                airport_names=frozenset({"Airport A"}), airport_identifiers=frozenset({"AAA"}),
            )
            d_b, _, _ = _full_match_lifecycle(
                session, key="fk-b", raw_text="Airport B memo.", matched_airport_id=airport_b.id,
                airport_names=frozenset({"Airport B"}), airport_identifiers=frozenset({"BBB"}),
            )
            from sqlalchemy.exc import IntegrityError

            malformed = IdentityGuardEvaluation(
                source_assertion_id=d_a.source_assertion_id,
                evidence_bag_snapshot_id=d_b.evidence_bag_snapshot_id,  # B's snapshot, claiming A
                evaluated_against_airport_id=airport_a.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="malicious cross-assertion claim",
            )
            session.add(malformed)
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()

    def test_multiple_snapshot_rows_for_same_assertion_fails_loud_not_silently_picks_one(self, tmp_path):
        """§4: EB1's own unique=True FK makes this structurally impossible
        in any well-formed database - only reachable here by rebuilding
        source_assertion_evidence_bags WITHOUT its own UNIQUE constraint
        via raw DDL (SQLite enforces UNIQUE indexes regardless of
        PRAGMA foreign_keys, so that pragma alone cannot bypass it; the
        table itself must be rebuilt). The service must never silently
        take "the first snapshot it finds" if this constraint were ever
        bypassed; it must fail loud instead (MultipleResultsFound), never
        producing an evaluation against an arbitrarily-chosen snapshot."""
        db = tmp_path / "multi_snapshot.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            airport = _seed_airport(session, iata_code="FOO")
            discovered, review, resolved = _full_match_lifecycle(
                session, key="multi-snapshot", raw_text="FOO memo.", matched_airport_id=airport.id,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"FOO"}),
            )
            session.commit()
            source_assertion_id = discovered.source_assertion_id
            original_snapshot = session.get(SourceAssertionEvidenceBag, discovered.evidence_bag_snapshot_id)
            payload = original_snapshot.evidence_bag_json
            digest = original_snapshot.evidence_bag_hash
        engine.dispose()

        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE source_assertion_evidence_bags")
        conn.execute(
            "CREATE TABLE source_assertion_evidence_bags (id INTEGER PRIMARY KEY, "
            "source_assertion_id INTEGER, evidence_bag_json TEXT, evidence_bag_hash TEXT, "
            "schema_version INTEGER, created_at DATETIME)"
        )
        conn.execute(
            "INSERT INTO source_assertion_evidence_bags "
            "(source_assertion_id, evidence_bag_json, evidence_bag_hash, schema_version, created_at) "
            "VALUES (?, ?, ?, 1, datetime('now')), (?, ?, ?, 1, datetime('now'))",
            (source_assertion_id, payload, digest, source_assertion_id, payload, digest),
        )
        conn.commit()
        conn.close()

        from sqlalchemy.orm.exc import MultipleResultsFound

        engine2 = create_engine(f"sqlite:///{db}")
        with Session(engine2) as session:
            with pytest.raises(MultipleResultsFound):
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=source_assertion_id)
            assert session.query(IdentityGuardEvaluation).count() == 0
        engine2.dispose()


# ---------------------------------------------------------------------------
# 29/33. Information firewall - no Signal/promotion/candidate-resolution/
# network imports
# ---------------------------------------------------------------------------


class TestInformationFirewall:
    def test_no_forbidden_imports(self):
        source = inspect_module.getsource(eb4_module)
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        forbidden = (
            "governed_signal_creation", "promotion_policy", "unknown_airport_candidate_resolution",
            "unknown_airport_candidate_persistence", "requests", "urllib", "httpx",
        )
        for module_name in imported_modules:
            assert not any(term in module_name for term in forbidden), module_name

    def test_no_network_or_session_local(self):
        source = inspect_module.getsource(eb4_module)
        for term in ("import requests", "import urllib", "httpx", "socket.", "session.commit("):
            assert term not in source
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported_names.update(alias.name for alias in node.names)
        assert "SessionLocal" not in imported_names


# ---------------------------------------------------------------------------
# X. Real database never touched
# ---------------------------------------------------------------------------


class TestRealDatabaseNeverTouched:
    def test_module_never_references_real_db_path(self):
        source = inspect_module.getsource(eb4_module)
        assert "runway_safe.db" not in source
