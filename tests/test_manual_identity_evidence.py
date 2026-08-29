"""Tests for app/models/manual_identity_evidence.py and
app/services/manual_identity_evidence.py (docs/architecture, "RWI - New
Source Family Manual Identity Evidence - Architecture Design" mission).

Isolated, in-memory SQLite databases only - never the real one.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.manual_identity_evidence import ManualIdentityEvidence
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.source_assertion_legacy_identity_attestation import SourceAssertionLegacyIdentityAttestation
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.effective_identity_guard_decision import (
    EffectiveIdentityGuardDecisionBasis,
    resolve_effective_identity_guard_decision,
)
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.manual_identity_evidence import (
    EmptyEvidenceExcerptError,
    ExistingEvidenceBagError,
    ExistingManualIdentityEvidenceError,
    MissingCanonicalAirportError,
    ModernIdentityGuardAlreadyGovernedError,
    RawFieldNotInExcerptError,
    SignalAlreadyLinkedError,
    SourceAssertionNotFoundError,
    SourceMismatchError,
    SourceNotFoundError,
    TargetAirportNotFoundError,
    UnresolvedUnknownAirportCandidateError,
    check_manual_identity_evidence_eligibility,
    excerpt_contains_value,
    manual_identity_evidence_to_candidate_fragment,
    normalize_for_containment_check,
    record_manual_identity_evidence,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session, **overrides) -> tuple[Source, Airport, SourceAssertion]:
    """A Sacheon-shaped fixture: no identifier code stated anywhere (matches
    the real SA232/233 rows this mechanism was designed for), name+city
    present in the excerpt."""
    airport = overrides.pop("airport", None) or Airport(name="Test Airport", city="Test City", country="Testland")
    if airport.id is None:
        session.add(airport)
        session.flush()
    source = overrides.pop("source", None) or Source(title="Test news article", source_type="news")
    if source.id is None:
        session.add(source)
        session.flush()
    kwargs = dict(
        source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text="Test Airport in Test City is getting a new EMAS installation this year.",
        raw_product_type="EMAS", source_record_identifier="rec-1", evidence_quality="direct_strong",
        parser_identifier="manual-research-v1",
    )
    kwargs.update(overrides)
    assertion = SourceAssertion(**kwargs)
    session.add(assertion)
    session.commit()
    return source, airport, assertion


_EXCERPT = "Test Airport in Test City is getting a new EMAS installation this year."


def _record(session, source, assertion, **overrides):
    kwargs = dict(
        source_assertion_id=assertion.id, source_id=source.id, evidence_excerpt=_EXCERPT,
        analyst="human:tester", raw_airport_name="Test Airport", raw_city="Test City",
    )
    kwargs.update(overrides)
    return record_manual_identity_evidence(session, **kwargs)


# --- Model: immutability ---

class TestModelImmutability:
    def test_update_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            evidence.analyst = "human:someone-else"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()
            session.rollback()

    def test_delete_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            session.delete(evidence)
            with pytest.raises(ValueError, match="auditable and cannot be deleted"):
                session.commit()
            session.rollback()

    def test_extraction_mode_check_constraint_rejects_unknown_mode(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            evidence = ManualIdentityEvidence(
                source_assertion_id=assertion.id, source_id=source.id, evidence_excerpt=_EXCERPT,
                analyst="human:tester", extraction_mode="MACHINE_INFERRED", normalization_version=1,
            )
            session.add(evidence)
            with pytest.raises(Exception):
                session.commit()
            session.rollback()


# --- Test 1-2: happy path / persistence fields ---

class TestHappyPathAndPersistedFields:
    def test_happy_path_returns_a_result(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            assert result.manual_identity_evidence_id is not None
            assert result.source_assertion_id == assertion.id
            assert result.evidence_bag_snapshot_id is not None

    def test_analyst_timestamp_extraction_mode_excerpt_preserved(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion, analyst="human:lkarlsson@gmail.com")
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            assert evidence.analyst == "human:lkarlsson@gmail.com"
            assert evidence.created_at is not None
            assert evidence.extraction_mode == "HUMAN_TRANSCRIPTION"
            assert evidence.evidence_excerpt == _EXCERPT
            assert evidence.normalization_version == 1

    def test_source_and_source_assertion_binding_preserved(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            assert evidence.source_id == source.id
            assert evidence.source_assertion_id == assertion.id

    def test_transcribed_raw_fields_preserved(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion, raw_country=None)
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            assert evidence.raw_airport_name == "Test Airport"
            assert evidence.raw_city == "Test City"
            assert evidence.raw_country is None
            assert evidence.raw_identifier_code is None


# --- Eligibility ---

class TestEligibility:
    def test_happy_case_is_eligible(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)  # must not raise

    def test_wrong_source_id_is_ineligible(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            other_source = Source(title="Other", source_type="news")
            session.add(other_source)
            session.commit()
            with pytest.raises(SourceMismatchError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=other_source.id)

    def test_nonexistent_source_id_is_ineligible(self):
        with Session(_engine()) as session:
            _source, _airport, assertion = _seed(session)
            with pytest.raises(SourceNotFoundError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=999999)

    def test_airport_id_null_is_ineligible(self):
        with Session(_engine()) as session:
            source = Source(title="x", source_type="news")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=None, assertion_type="project_construction",
                source_locator="loc-1", artifact_identity="art-1", raw_fragment_hash="hash-1",
                raw_relevant_text="text",
            )
            session.add(assertion)
            session.commit()
            with pytest.raises(MissingCanonicalAirportError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)

    def test_unresolved_unknown_airport_candidate_is_ineligible(self):
        with Session(_engine()) as session:
            source = Source(title="x", source_type="news")
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
            with pytest.raises(UnresolvedUnknownAirportCandidateError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)

    def test_already_identity_governed_is_ineligible(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session, identity_guard_decision="ATTACH_CONFIRMED")
            with pytest.raises(ModernIdentityGuardAlreadyGovernedError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)

    def test_existing_evidence_bag_is_ineligible(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json="{}", evidence_bag_hash="h", schema_version=1,
            ))
            session.commit()
            with pytest.raises(ExistingEvidenceBagError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)

    def test_existing_manual_identity_evidence_is_ineligible(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            _record(session, source, assertion)
            session.commit()
            with pytest.raises(ExistingManualIdentityEvidenceError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)

    def test_signal_already_linked_is_ineligible(self):
        with Session(_engine()) as session:
            airport = Airport(name="Test Airport", city="Test City", country="Testland")
            session.add(airport)
            session.flush()
            signal = Signal(airport=airport, title="x", category="new_installation", confidence="high")
            session.add(signal)
            session.flush()
            source, _airport2, assertion = _seed(session, airport=airport, signal_id=signal.id)
            with pytest.raises(SignalAlreadyLinkedError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)

    def test_target_airport_missing_is_ineligible(self):
        """Only reachable via a malformed/foreign-key-disabled database -
        proven directly rather than assumed."""
        with Session(_engine()) as session:
            session.execute(select(1))  # ensure engine bound
            source, airport, assertion = _seed(session)
            session.execute(SourceAssertion.__table__.update().where(SourceAssertion.id == assertion.id).values(
                airport_id=999999
            ))
            session.commit()
            session.expire_all()
            assertion = session.get(SourceAssertion, assertion.id)
            with pytest.raises(TargetAirportNotFoundError):
                check_manual_identity_evidence_eligibility(session, assertion, source_id=source.id)


# --- Literal-transcription safety ---

class TestLiteralTranscriptionSafety:
    def test_empty_excerpt_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            with pytest.raises(EmptyEvidenceExcerptError):
                _record(session, source, assertion, evidence_excerpt="   ")

    def test_identifier_absent_from_excerpt_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            with pytest.raises(RawFieldNotInExcerptError):
                _record(session, source, assertion, raw_identifier_code="ZZZ")

    def test_identifier_present_in_excerpt_is_accepted(self):
        with Session(_engine()) as session:
            source, airport, assertion = _seed(
                session, raw_relevant_text="Test Airport (TST) in Test City is getting a new EMAS installation.",
            )
            result = _record(
                session, source, assertion,
                evidence_excerpt="Test Airport (TST) in Test City is getting a new EMAS installation.",
                raw_identifier_code="TST",
            )
            assert result.manual_identity_evidence_id is not None

    def test_name_absent_from_excerpt_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            with pytest.raises(RawFieldNotInExcerptError):
                _record(session, source, assertion, raw_airport_name="Nowhere International")

    def test_city_absent_from_excerpt_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            with pytest.raises(RawFieldNotInExcerptError):
                _record(session, source, assertion, raw_city="Nowhere")

    def test_country_absent_from_excerpt_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            with pytest.raises(RawFieldNotInExcerptError):
                _record(session, source, assertion, raw_country="Nowhereland")

    def test_case_insensitive_containment_is_accepted(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion, raw_airport_name="TEST AIRPORT")
            assert result.manual_identity_evidence_id is not None

    def test_hidden_inference_is_impossible_even_when_analyst_knows_the_code(self):
        """Phase 3's own literal example: even if the analyst personally
        knows the real code, supplying it when the source text never
        states it must be refused."""
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)  # excerpt never mentions any code
            with pytest.raises(RawFieldNotInExcerptError):
                _record(session, source, assertion, raw_identifier_code="HIN")

    def test_normalize_for_containment_check_collapses_whitespace_and_case(self):
        assert normalize_for_containment_check("  Test   Airport ") == "test airport"

    def test_excerpt_contains_value_helper(self):
        assert excerpt_contains_value("Test Airport in Test City", "test airport") is True
        assert excerpt_contains_value("Test Airport in Test City", "Nowhere") is False


# --- Source binding / no caller-selected airport ---

class TestSourceBindingAndCandidateDerivation:
    def test_mismatched_source_is_rejected(self):
        with Session(_engine()) as session:
            _source, _airport, assertion = _seed(session)
            other_source = Source(title="Other", source_type="news")
            session.add(other_source)
            session.commit()
            with pytest.raises(SourceMismatchError):
                record_manual_identity_evidence(
                    session, source_assertion_id=assertion.id, source_id=other_source.id,
                    evidence_excerpt=_EXCERPT, analyst="human:tester",
                    raw_airport_name="Test Airport", raw_city="Test City",
                )

    def test_no_airport_id_parameter_exists_on_the_write_function(self):
        """Structural proof, not merely a docstring claim: the write
        function's own signature has no airport_id parameter at all."""
        import inspect
        sig = inspect.signature(record_manual_identity_evidence)
        assert "airport_id" not in sig.parameters

    def test_candidate_fragment_adapter_never_reads_a_caller_airport(self):
        """manual_identity_evidence_to_candidate_fragment() has no
        airport_id parameter either - the candidate airport is derived
        exclusively downstream, from source_assertion.airport_id, by
        record_manual_identity_evidence() itself."""
        import inspect
        sig = inspect.signature(manual_identity_evidence_to_candidate_fragment)
        assert list(sig.parameters) == ["evidence"]

    def test_no_decision_parameter_exists_on_the_write_function(self):
        import inspect
        sig = inspect.signature(record_manual_identity_evidence)
        forbidden = {"identity_guard_decision", "expected_decision", "force_attach", "override", "decision"}
        assert forbidden.isdisjoint(sig.parameters)


# --- CandidateFragment adapter ---

class TestCandidateFragmentAdapter:
    def test_builds_from_persisted_record(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            fragment = manual_identity_evidence_to_candidate_fragment(evidence)
            assert isinstance(fragment, CandidateFragment)
            assert fragment.raw_text == _EXCERPT
            assert "Test Airport" in fragment.airport_names
            assert "Test City" in fragment.locations
            assert fragment.parser_identifier == "manual_identity_evidence_v1"

    def test_parser_identifier_is_generic_not_korea_specific(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            fragment = manual_identity_evidence_to_candidate_fragment(evidence)
            assert fragment.parser_identifier == "manual_identity_evidence_v1"
            assert "korea" not in fragment.parser_identifier.lower()
            assert "sacheon" not in fragment.parser_identifier.lower()

    def test_identifier_code_maps_to_airport_identifiers(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(
                session, raw_relevant_text="Test Airport (TST) in Test City.",
            )
            result = _record(
                session, source, assertion, evidence_excerpt="Test Airport (TST) in Test City.",
                raw_identifier_code="TST",
            )
            session.commit()
            evidence = session.get(ManualIdentityEvidence, result.manual_identity_evidence_id)
            fragment = manual_identity_evidence_to_candidate_fragment(evidence)
            assert fragment.airport_identifiers == frozenset({"TST"})


# --- EvidenceBag / SourceAssertionEvidenceBag reuse ---

class TestEvidenceBagPersistence:
    def test_evidence_bag_snapshot_created_exactly_once(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            _record(session, source, assertion)
            session.commit()
            snapshots = session.scalars(
                select(SourceAssertionEvidenceBag).where(
                    SourceAssertionEvidenceBag.source_assertion_id == assertion.id
                )
            ).all()
            assert len(snapshots) == 1

    def test_evidence_bag_snapshot_is_immutable(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion)
            session.commit()
            snapshot = session.get(SourceAssertionEvidenceBag, result.evidence_bag_snapshot_id)
            snapshot.evidence_bag_hash = "tampered"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()
            session.rollback()

    def test_duplicate_rerun_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            _record(session, source, assertion)
            session.commit()
            with pytest.raises(ExistingManualIdentityEvidenceError):
                _record(session, source, assertion)

    def test_assertion_already_has_evidence_bag_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json="{}", evidence_bag_hash="h", schema_version=1,
            ))
            session.commit()
            with pytest.raises(ExistingEvidenceBagError):
                _record(session, source, assertion)

    def test_assertion_already_identity_governed_is_rejected(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session, identity_guard_decision="REJECT_CROSS_AIRPORT")
            with pytest.raises(ModernIdentityGuardAlreadyGovernedError):
                _record(session, source, assertion)

    def test_no_legacy_attestation_created(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            _record(session, source, assertion)
            session.commit()
            rows = session.scalars(select(SourceAssertionLegacyIdentityAttestation)).all()
            assert rows == []


# --- IdentityGuard reuse (real, not hard-coded) ---

class TestIdentityGuardReuse:
    def test_strong_matching_fixture_naturally_reaches_attach_confirmed(self):
        """Name + city both independently match -> 2 positive categories
        -> ATTACH_CONFIRMED, computed by the real, unmodified guard - never
        hard-coded or bypassed here."""
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion, raw_airport_name="Test Airport", raw_city="Test City")
            assert result.identity_guard_decision == AttachmentOutcome.ATTACH_CONFIRMED.value

    def test_nonmatching_fixture_does_not_get_forced_attach_confirmed(self):
        """A different Airport entirely (name/city that don't match the
        assertion's own canonical Airport) yields no positive evidence -
        INSUFFICIENT_IDENTITY, never forced to ATTACH_CONFIRMED."""
        with Session(_engine()) as session:
            airport = Airport(name="Unrelated Airport", city="Elsewhere", country="Otherland")
            session.add(airport)
            session.flush()
            source = Source(title="Test", source_type="news")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=_EXCERPT, source_record_identifier="rec-2", evidence_quality="direct_strong",
            )
            session.add(assertion)
            session.commit()
            result = _record(
                session, source, assertion, raw_airport_name="Test Airport", raw_city="Test City",
            )
            assert result.identity_guard_decision != AttachmentOutcome.ATTACH_CONFIRMED.value
            assert result.identity_guard_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY.value

    def test_single_positive_category_reaches_attach_provisional(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            result = _record(session, source, assertion, raw_airport_name="Test Airport", raw_city=None)
            assert result.identity_guard_decision == AttachmentOutcome.ATTACH_PROVISIONAL.value

    def test_identifier_alone_reaches_attach_confirmed(self):
        with Session(_engine()) as session:
            source, airport, assertion = _seed(
                session, airport=Airport(name="Test Airport", city="Test City", country="Testland", iata_code="TST"),
                raw_relevant_text="Test Airport (TST) news.",
            )
            result = _record(
                session, source, assertion, evidence_excerpt="Test Airport (TST) news.",
                raw_airport_name=None, raw_city=None, raw_identifier_code="TST",
            )
            assert result.identity_guard_decision == AttachmentOutcome.ATTACH_CONFIRMED.value

    def test_identity_guard_decision_and_reason_persisted_on_assertion(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            _record(session, source, assertion)
            session.commit()
            refreshed = session.get(SourceAssertion, assertion.id)
            assert refreshed.identity_guard_decision == AttachmentOutcome.ATTACH_CONFIRMED.value
            assert refreshed.identity_guard_reason


# --- EB5 read-boundary indistinguishability ---

class TestEB5ReadBoundary:
    def test_eb5_resolves_via_existing_original_decision_basis(self):
        with Session(_engine()) as session:
            source, _airport, assertion = _seed(session)
            _record(session, source, assertion)
            session.commit()
            effective = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert effective.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert effective.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert effective.is_identity_confirmed is True

    def test_manual_path_indistinguishable_from_automated_discovery_at_eb5(self):
        """Build one assertion via this mechanism and one shaped exactly
        like a discovery-time-governed row (identity_guard_decision set
        directly, no legacy attestation, no IdentityGuardEvaluation) - EB5
        must treat both identically (same basis, same is_identity_confirmed
        semantics)."""
        with Session(_engine()) as session:
            source, _airport, manual_assertion = _seed(session)
            _record(session, source, manual_assertion)
            session.commit()

            automated_airport = Airport(name="Auto Airport", country="USA")
            session.add(automated_airport)
            session.flush()
            automated_assertion = SourceAssertion(
                source_id=source.id, airport_id=automated_airport.id, assertion_type="airport_inventory",
                raw_relevant_text="x", source_record_identifier="rec-auto",
                identity_guard_decision="ATTACH_CONFIRMED", identity_guard_reason="matched by discovery",
            )
            session.add(automated_assertion)
            session.commit()

            manual_effective = resolve_effective_identity_guard_decision(session, source_assertion_id=manual_assertion.id)
            automated_effective = resolve_effective_identity_guard_decision(session, source_assertion_id=automated_assertion.id)
            assert manual_effective.basis == automated_effective.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
            assert manual_effective.is_identity_confirmed == automated_effective.is_identity_confirmed is True


# --- Regression: existing extractors/mechanisms unaffected ---

class TestNoRegressionOnExistingMechanisms:
    def test_usaspending_grant_claims_extractor_unaffected(self):
        from app.acquisition.usaspending_grant_claims import extract_usaspending_grant_claims  # noqa: F401

    def test_mac_claims_enrichment_unaffected(self):
        from app.services.human_review_claim_enrichment import _PARSER_ONLY_ADAPTERS
        assert _PARSER_ONLY_ADAPTERS  # still populated, untouched

    def test_faa_backfill_extractor_import_unaffected(self):
        import app.acquisition.faa_emas_parser  # noqa: F401


def _sacheon_shaped_excerpt() -> str:
    """A representative excerpt in the same shape as the real SA232/233
    rows (Korean text, no IATA/ICAO code stated) - NOT the real rows
    themselves, and NEVER applied to the real database (see
    tests/test_manual_identity_evidence_cli.py and this mission's own
    Phase 19 real-DB verification, done outside pytest)."""
    return "사천공항 활주로 안전구역에 EMAS 설치 공사가 진행 중이다."


class TestSacheonShapedFixture:
    def test_sacheon_shaped_fixture_reaches_a_real_governed_decision(self):
        with Session(_engine()) as session:
            airport = Airport(name="Sacheon Airport", country="South Korea")
            session.add(airport)
            session.flush()
            source = Source(title="Kyunghyang Shinmun test fixture", source_type="news")
            session.add(source)
            session.flush()
            excerpt = _sacheon_shaped_excerpt()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=excerpt, source_record_identifier="fixture:sacheon-shaped",
                evidence_quality="direct_strong",
            )
            session.add(assertion)
            session.commit()

            # Literal transcription: the excerpt names "사천공항" (Sacheon
            # Airport) - no IATA/ICAO code is stated, exactly like the real
            # rows, so none is supplied here either (Phase 3's own
            # "hidden inference forbidden" rule).
            result = record_manual_identity_evidence(
                session, source_assertion_id=assertion.id, source_id=source.id,
                evidence_excerpt=excerpt, analyst="human:tester", raw_airport_name="사천공항",
            )
            # Real Airport 88 in production also has no alias table entry
            # for "사천공항" against name="Sacheon Airport" - single-category
            # name evidence alone against a non-matching name is expected
            # to be INSUFFICIENT_IDENTITY, computed by the real guard, never
            # forced either way.
            assert result.identity_guard_decision in {
                AttachmentOutcome.INSUFFICIENT_IDENTITY.value, AttachmentOutcome.ATTACH_PROVISIONAL.value,
            }


# --- CLI single-invocation, no bulk mode contract check (also see the
# dedicated CLI test file) ---

class TestNoBulkInterface:
    def test_write_function_operates_on_exactly_one_assertion(self):
        import inspect
        sig = inspect.signature(record_manual_identity_evidence)
        assert "source_assertion_ids" not in sig.parameters
        assert "source_assertion_id" in sig.parameters
