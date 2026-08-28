"""Tests for the LEGACY_HUMAN_ATTESTATION fallback tier added to
app/services/effective_identity_guard_decision.py (EB5) by
docs/architecture/rwi-legacy-attached-sourceassertion-identity-governance-
design.md.

Isolated, in-memory SQLite databases only - never the real one. Deliberately
a separate file from tests/test_effective_identity_guard_decision.py (the
large, already-reviewed pre-existing EB5 suite) rather than an edit to it -
this mission's own scope is additive only.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.services.effective_identity_guard_decision import (
    EffectiveIdentityGuardDecisionBasis,
    resolve_effective_identity_guard_decision,
)
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.source_assertion_legacy_identity_attestation import record_legacy_identity_attestation


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_legacy_assertion(session, **overrides) -> SourceAssertion:
    airport = Airport(name="Test Airport", iata_code="TST", icao_code="KTST", country="USA")
    session.add(airport)
    session.flush()
    source = Source(title="Test grant", source_type="usaspending_grant")
    session.add(source)
    session.flush()
    kwargs = dict(
        source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text="TEST AIRPORT EMAS project", raw_product_type="EMAS",
        source_record_identifier="rec-1", evidence_quality="direct_strong",
    )
    kwargs.update(overrides)
    assertion = SourceAssertion(**kwargs)
    session.add(assertion)
    session.commit()
    return assertion


class TestLegacyAttestationBecomesEffective:
    def test_valid_confirm_yields_attach_confirmed(self):
        with Session(_engine()) as session:
            assertion = _seed_legacy_assertion(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="text matches airport", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert result.basis == EffectiveIdentityGuardDecisionBasis.LEGACY_HUMAN_ATTESTATION
            assert result.is_identity_confirmed is True
            # original_decision (the permanent historical fact) stays untouched.
            assert result.original_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY

    def test_reject_blocks_confirmation(self):
        with Session(_engine()) as session:
            assertion = _seed_legacy_assertion(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="REJECT_EXISTING_ATTACHMENT",
                reason="text does not support this airport", reviewer="human:tester",
            )
            session.commit()

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert result.is_identity_confirmed is False

    def test_defer_blocks_confirmation(self):
        with Session(_engine()) as session:
            assertion = _seed_legacy_assertion(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="needs more research", reviewer="human:tester",
            )
            session.commit()

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

    def test_stale_confirm_blocks_confirmation(self):
        with Session(_engine()) as session:
            assertion = _seed_legacy_assertion(session)
            record_legacy_identity_attestation(
                session, source_assertion_id=assertion.id, action="CONFIRM_EXISTING_ATTACHMENT",
                reason="text matches airport", reviewer="human:tester", matched_airport_id=assertion.airport_id,
            )
            session.commit()

            # Drift: the assertion's own reviewed evidence text changes
            # after the attestation was recorded.
            assertion.raw_relevant_text = "SOMETHING ELSE ENTIRELY"
            session.commit()

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

    def test_no_attestation_at_all_is_unaffected(self):
        with Session(_engine()) as session:
            assertion = _seed_legacy_assertion(session)
            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert result.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

    def test_legacy_attestations_table_not_migrated_falls_back_cleanly(self):
        """Mirrors the pre-existing EB2-not-migrated compatibility test for
        identity_guard_evaluations - a database that has not yet run this
        mission's own migration must never raise."""
        import sqlite3

        engine = _engine()
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.exec_driver_sql("DROP TABLE IF EXISTS source_assertion_legacy_identity_attestations")
            conn.commit()

        with Session(engine) as session:
            assertion = _seed_legacy_assertion(session)
            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert result.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION


class TestExistingPrecedencePreserved:
    def test_real_original_decision_is_never_overridden_by_a_legacy_attestation(self):
        """A row WITH a real historical identity_guard_decision (e.g.
        REJECT_CROSS_AIRPORT) must behave exactly as before this mission -
        check_legacy_attestation_eligibility() already refuses to let an
        attestation be recorded for such a row, and this proves the
        matching defensive read-side enforcement independently."""
        with Session(_engine()) as session:
            assertion = _seed_legacy_assertion(session, identity_guard_decision="REJECT_CROSS_AIRPORT")
            session.commit()

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

    def test_eb4_reevaluation_precedence_is_unaffected(self):
        """A row with a real, current IdentityGuardEvaluation (EB4) must
        still take precedence over everything else, exactly as before -
        proven directly, not merely assumed, by seeding one alongside a
        real EvidenceBag (the only way EB4 can genuinely apply)."""
        with Session(_engine()) as session:
            airport = Airport(name="Test Airport", country="USA")
            session.add(airport)
            session.flush()
            source = Source(title="MAC doc", source_type="web_discovery")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                source_locator="loc-1", artifact_identity="art-1", raw_fragment_hash="hash-1",
                raw_relevant_text="text", identity_guard_decision="ATTACH_CONFIRMED",
            )
            session.add(assertion)
            session.flush()
            bag = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json="{}", evidence_bag_hash="h", schema_version=1,
            )
            session.add(bag)
            session.flush()
            evaluation = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=bag.id,
                evaluated_against_airport_id=airport.id, outcome="ATTACH_PROVISIONAL", reason="topology only",
            )
            session.add(evaluation)
            session.commit()

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL
            assert result.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION

    def test_candidate_linked_row_is_unaffected(self):
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

            result = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)

            assert result.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY
            assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
