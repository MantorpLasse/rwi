"""Tests for app/services/source_assertion_legacy_identity_attestation.py
(docs/architecture/rwi-legacy-attached-sourceassertion-identity-governance-
design.md).

Isolated, in-memory SQLite databases only - never the real one.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.source_assertion_legacy_identity_attestation import SourceAssertionLegacyIdentityAttestation
from app.services.source_assertion_legacy_identity_attestation import (
    ConflictingAttestationRequiresSupersessionError,
    MissingReviewableEvidenceError,
    ModernEvidenceBagExistsError,
    ModernIdentityGuardAlreadyRanError,
    NotLegacyAttachedError,
    SignalAlreadyLinkedError,
    SourceAssertionNotFoundError,
    TargetAirportMismatchError,
    TargetAirportNotFoundError,
    build_review_snapshot_payload,
    check_legacy_attestation_eligibility,
    get_latest_legacy_identity_attestation,
    hash_review_snapshot,
    is_attestation_current,
    record_legacy_identity_attestation,
    serialize_review_snapshot,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session, **overrides) -> SourceAssertion:
    airport = overrides.pop("airport", None) or Airport(
        name="Test Airport", iata_code="TST", icao_code="KTST", faa_code="TST", country="USA",
    )
    if airport.id is None:
        session.add(airport)
        session.flush()
    source = Source(title="Test grant", source_type="usaspending_grant")
    session.add(source)
    session.flush()
    kwargs = dict(
        source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text="TEST AIRPORT EMAS project", raw_product_type="EMAS",
        source_record_identifier="rec-1", evidence_quality="direct_strong",
        parser_identifier="legacy-source-backfill-v1",
    )
    kwargs.update(overrides)
    assertion = SourceAssertion(**kwargs)
    session.add(assertion)
    session.commit()
    return assertion


# --- Eligibility ---

class TestEligibility:
    def test_happy_legacy_attached_case_is_eligible(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            check_legacy_attestation_eligibility(session, assertion)  # must not raise

    def test_airport_id_null_is_ineligible(self):
        with Session(_engine()) as session:
            source = Source(title="x", source_type="usaspending_grant")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=None, assertion_type="project_construction",
                source_locator="loc-1", artifact_identity="art-1", raw_fragment_hash="hash-1",
                raw_relevant_text="text",
            )
            session.add(assertion)
            session.commit()
            with pytest.raises(NotLegacyAttachedError):
                check_legacy_attestation_eligibility(session, assertion)

    def test_candidate_linked_is_ineligible(self):
        """airport_id IS NULL is the same structural state as
        candidate-linked (mutual exclusivity), so this is covered by the
        same NotLegacyAttachedError - proven directly against the DB's own
        constraint shape rather than assumed."""
        with Session(_engine()) as session:
            from app.models.unknown_airport_candidate import UnknownAirportCandidate
            source = Source(title="x", source_type="web_discovery")
            session.add(source)
            session.flush()
            candidate = UnknownAirportCandidate(raw_name="Some Airport", candidate_fingerprint="fp-1")
            session.add(candidate)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, unknown_airport_candidate_id=candidate.id,
                assertion_type="project_construction", source_locator="loc-1",
                artifact_identity="art-1", raw_fragment_hash="hash-1", raw_relevant_text="text",
            )
            session.add(assertion)
            session.commit()
            with pytest.raises(NotLegacyAttachedError):
                check_legacy_attestation_eligibility(session, assertion)

    def test_identity_guard_decision_already_set_is_ineligible(self):
        with Session(_engine()) as session:
            assertion = _seed(session, identity_guard_decision="ATTACH_CONFIRMED")
            with pytest.raises(ModernIdentityGuardAlreadyRanError):
                check_legacy_attestation_eligibility(session, assertion)

    def test_evidence_bag_exists_is_ineligible(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json="{}", evidence_bag_hash="h", schema_version=1,
            ))
            session.commit()
            with pytest.raises(ModernEvidenceBagExistsError):
                check_legacy_attestation_eligibility(session, assertion)

    def test_signal_id_exists_is_ineligible(self):
        with Session(_engine()) as session:
            airport = Airport(name="Test Airport", country="USA")
            session.add(airport)
            session.flush()
            signal = Signal(airport=airport, title="x", category="new_installation", confidence="high")
            session.add(signal)
            session.flush()
            assertion = _seed(session, airport=airport, signal_id=signal.id)
            with pytest.raises(SignalAlreadyLinkedError):
                check_legacy_attestation_eligibility(session, assertion)

    def test_missing_raw_evidence_is_ineligible(self):
        with Session(_engine()) as session:
            assertion = _seed(session, raw_relevant_text=None, raw_product_type=None)
            with pytest.raises(MissingReviewableEvidenceError):
                check_legacy_attestation_eligibility(session, assertion)

    def test_missing_airport_is_ineligible(self):
        """Only reachable via a malformed/FK-disabled database - proven
        directly by detaching the row from a real session after seeding."""
        engine = _engine()
        with Session(engine) as session:
            assertion = _seed(session)
            assertion_id = assertion.id
            airport_id = assertion.airport_id
        # Delete the Airport out from under the assertion via a raw
        # connection with FKs off, simulating corruption - never reachable
        # through any real governed write path in this codebase.
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.exec_driver_sql(f"DELETE FROM airports WHERE id={airport_id}")
            conn.commit()
        with Session(engine) as session:
            assertion = session.get(SourceAssertion, assertion_id)
            with pytest.raises(TargetAirportNotFoundError):
                check_legacy_attestation_eligibility(session, assertion)


# --- Service: CONFIRM/REJECT/DEFER, append-only, firewall ---

class TestRecordAttestation:
    def test_confirm_appends_one_row(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="text matches airport", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            assert result.action == "CONFIRM_EXISTING_ATTACHMENT"
            rows = session.query(SourceAssertionLegacyIdentityAttestation).all()
            assert len(rows) == 1
            assert rows[0].matched_airport_id == assertion.airport_id

    def test_reject_appends_one_row_and_never_clears_airport_id(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            original_airport_id = assertion.airport_id
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="REJECT_EXISTING_ATTACHMENT",
                reason="text does not actually support this airport", reviewer="human:tester",
            )
            session.commit()
            refreshed = session.get(SourceAssertion, assertion.id)
            assert refreshed.airport_id == original_airport_id  # never cleared

    def test_defer_appends_one_row(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="need more research", reviewer="human:tester",
            )
            session.commit()
            assert result.action == "DEFER_IDENTITY_REVIEW"

    def test_multiple_defers_are_all_preserved(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            for i in range(3):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason=f"defer {i}", reviewer="human:tester",
                )
            session.commit()
            rows = session.query(SourceAssertionLegacyIdentityAttestation).all()
            assert len(rows) == 3

    def test_confirm_requires_matched_airport_id(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            with pytest.raises(ValueError, match="matched_airport_id is required"):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                    reason="x", reviewer="human:tester",
                )

    def test_confirm_matched_airport_id_must_equal_current_airport_id(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            other_airport = Airport(name="Other Airport", country="USA")
            session.add(other_airport)
            session.commit()
            with pytest.raises(TargetAirportMismatchError):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                    reason="x", reviewer="human:tester", matched_airport_id=other_airport.id,
                )

    def test_defer_rejects_matched_airport_id(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            with pytest.raises(ValueError, match="must be omitted"):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
                )

    def test_empty_reason_rejected(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            with pytest.raises(ValueError, match="reason is required"):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="   ", reviewer="human:tester",
                )

    def test_empty_reviewer_rejected(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            with pytest.raises(ValueError, match="reviewer is required"):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="  ",
                )

    def test_invalid_action_rejected(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            with pytest.raises(ValueError, match="action must be one of"):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="human:tester",
                )

    def test_nonexistent_source_assertion_raises(self):
        with Session(_engine()) as session:
            with pytest.raises(SourceAssertionNotFoundError):
                record_legacy_identity_attestation(
                    session, source_assertion_id=99999, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="human:tester",
                )


# --- Firewall: original fields, EvidenceBag, identity_guard_decision never mutated ---

class TestFirewall:
    def test_source_assertion_fields_never_mutated(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            before = (
                assertion.airport_id, assertion.identity_guard_decision, assertion.identity_guard_reason,
                assertion.raw_relevant_text, assertion.signal_id,
            )
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            refreshed = session.get(SourceAssertion, assertion.id)
            after = (
                refreshed.airport_id, refreshed.identity_guard_decision, refreshed.identity_guard_reason,
                refreshed.raw_relevant_text, refreshed.signal_id,
            )
            assert before == after

    def test_no_evidence_bag_ever_created(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            assert session.query(SourceAssertionEvidenceBag).count() == 0

    def test_attestation_rows_are_immutable(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="x", reviewer="human:tester",
            )
            session.commit()
            row = session.query(SourceAssertionLegacyIdentityAttestation).one()
            row.reason = "edited"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()
            session.rollback()

    def test_attestation_rows_cannot_be_deleted(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="x", reviewer="human:tester",
            )
            session.commit()
            row = session.query(SourceAssertionLegacyIdentityAttestation).one()
            session.delete(row)
            with pytest.raises(ValueError, match="cannot be deleted"):
                session.commit()
            session.rollback()


# --- Snapshot / staleness ---

class TestSnapshotStaleness:
    def test_recomputed_snapshot_matches_when_nothing_drifted(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            attestation = session.get(SourceAssertionLegacyIdentityAttestation, result.attestation_id)
            assert is_attestation_current(session, attestation) is True

    def test_airport_id_drift_makes_it_stale(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            attestation = session.get(SourceAssertionLegacyIdentityAttestation, result.attestation_id)

            other_airport = Airport(name="Different Airport", country="USA")
            session.add(other_airport)
            session.flush()
            # Simulate airport_id drift directly (out-of-band correction,
            # never something this module itself performs).
            assertion.airport_id = other_airport.id
            session.commit()

            assert is_attestation_current(session, attestation) is False

    def test_airport_canonical_identity_drift_makes_it_stale(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            attestation = session.get(SourceAssertionLegacyIdentityAttestation, result.attestation_id)

            airport = session.get(Airport, assertion.airport_id)
            airport.iata_code = "NEW"
            session.commit()

            assert is_attestation_current(session, attestation) is False

    def test_assertion_evidence_drift_makes_it_stale(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            attestation = session.get(SourceAssertionLegacyIdentityAttestation, result.attestation_id)

            assertion.raw_relevant_text = "COMPLETELY DIFFERENT TEXT"
            session.commit()

            assert is_attestation_current(session, attestation) is False

    def test_serialize_and_hash_are_deterministic(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            airport = session.get(Airport, assertion.airport_id)
            payload1 = build_review_snapshot_payload(assertion, airport)
            payload2 = build_review_snapshot_payload(assertion, airport)
            assert serialize_review_snapshot(payload1) == serialize_review_snapshot(payload2)
            assert hash_review_snapshot(serialize_review_snapshot(payload1)) == hash_review_snapshot(serialize_review_snapshot(payload2))


# --- Reversal / conflict safety ---

class TestReversalSafety:
    def test_confirm_then_reject_without_supersession_is_refused(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            confirm = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            with pytest.raises(ConflictingAttestationRequiresSupersessionError) as exc_info:
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="REJECT_EXISTING_ATTACHMENT",
                    reason="actually wrong", reviewer="human:tester",
                )
            assert exc_info.value.latest_attestation_id == confirm.attestation_id

    def test_confirm_then_reject_with_explicit_supersession_succeeds(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            confirm = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            reject = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="REJECT_EXISTING_ATTACHMENT",
                reason="actually wrong", reviewer="human:tester", supersedes_attestation_id=confirm.attestation_id,
            )
            session.commit()
            assert reject.is_reversal is True
            # Both rows remain, fully visible - nothing hidden or erased.
            rows = session.query(SourceAssertionLegacyIdentityAttestation).order_by(
                SourceAssertionLegacyIdentityAttestation.id
            ).all()
            assert [r.action for r in rows] == ["CONFIRM_EXISTING_ATTACHMENT", "REJECT_EXISTING_ATTACHMENT"]
            assert rows[1].supersedes_attestation_id == confirm.attestation_id

    def test_reject_then_confirm_without_supersession_is_refused(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            reject = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="REJECT_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester",
            )
            session.commit()
            with pytest.raises(ConflictingAttestationRequiresSupersessionError) as exc_info:
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                    reason="actually correct after all", reviewer="human:tester",
                    matched_airport_id=assertion.airport_id,
                )
            assert exc_info.value.latest_attestation_id == reject.attestation_id

    def test_defer_never_triggers_reversal_check(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="x", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()
            # DEFER after CONFIRM is never a "conflict" - no supersession needed.
            result = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="want a second look anyway", reviewer="human:tester",
            )
            session.commit()
            assert result.is_reversal is False

    def test_stale_supersedes_attestation_id_rejected(self):
        with Session(_engine()) as session:
            assertion = _seed(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="first", reviewer="human:tester",
            )
            session.commit()
            second = record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="second", reviewer="human:tester",
            )
            session.commit()
            # Citing an id that is no longer the latest is refused.
            with pytest.raises(ValueError, match="does not match the current latest"):
                record_legacy_identity_attestation(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="third", reviewer="human:tester", supersedes_attestation_id=1,
                )


def test_get_latest_returns_none_when_no_history():
    with Session(_engine()) as session:
        assertion = _seed(session)
        assert get_latest_legacy_identity_attestation(session, assertion.id) is None


def test_get_latest_returns_most_recent_by_recency():
    with Session(_engine()) as session:
        assertion = _seed(session)
        record_legacy_identity_attestation(
            session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
            reason="first", reviewer="human:tester",
        )
        session.commit()
        second = record_legacy_identity_attestation(
            session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
            reason="second", reviewer="human:tester",
        )
        session.commit()
        latest = get_latest_legacy_identity_attestation(session, assertion.id)
        assert latest.id == second.attestation_id
