"""Tests for app/models/airport_identifier.py and
app/services/airport_identifier.py (docs/architecture, "RWI - Governed
Canonical Airport Identifiers - Architecture Design" mission).

Isolated, in-memory SQLite databases only - never the real one.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.airport_identifier import AirportIdentifier
from app.services.airport_alias import record_airport_alias
from app.services.airport_identifier import (
    AirportNotFoundError,
    CircularIdentifierEvidenceError,
    ConflictingIdentifierStatusRequiresSupersessionError,
    CrossAirportTypedCollisionError,
    CurrentColumnPopulatedError,
    EmptyAnalystError,
    EmptyEvidenceExcerptError,
    EmptyIdentifierValueError,
    ExcerptNotInPreservedEvidenceError,
    IdentifierNotInExcerptError,
    InsufficientSourceReliabilityError,
    InvalidIdentifierTypeError,
    NoIdentityAnchorError,
    SourceAssertionAirportMismatchError,
    SourceAssertionNotFoundError,
    SourceAssertionSourceMismatchError,
    SourceNotFoundError,
    TypeEvidenceIncompleteError,
    TypeEvidenceNotInExcerptError,
    check_airport_identifier_admission_eligibility,
    preview_airport_identifier_admission_impact,
    record_airport_identifier,
)
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    candidate_airport_from_airport_like,
    evaluate_attachment,
)
from app.services.evidence_bag_serialization import deserialize_evidence_bag
from app.services.manual_identity_evidence import record_manual_identity_evidence


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


_EXCERPT = "Test Airport (TST) is the code. TST(IATA) TSTI(ICAO)"


def _seed_airport(session, **overrides) -> Airport:
    kwargs = dict(name="Test Airport", country="Testland")
    kwargs.update(overrides)
    airport = Airport(**kwargs)
    session.add(airport)
    session.flush()
    return airport


def _seed_admitting_evidence(session, airport, *, reliability_level="official", excerpt=_EXCERPT, source_type="government"):
    source = Source(title="Official registry", source_type=source_type, reliability_level=reliability_level)
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
        raw_relevant_text=excerpt, source_record_identifier=f"admit-rec-{source.id}",
        evidence_quality="direct_strong",
    )
    session.add(assertion)
    session.flush()
    return source, assertion


def _admit(session, airport, *, identifier_type="IATA", identifier_value="TST", token="TST(IATA)", excerpt=_EXCERPT, reliability_level="official", **kwargs):
    source, assertion = _seed_admitting_evidence(session, airport, reliability_level=reliability_level, excerpt=excerpt)
    return record_airport_identifier(
        session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
        identifier_type=identifier_type, identifier_value=identifier_value, evidence_excerpt=excerpt,
        analyst="human:tester", type_evidence_token=token, **kwargs,
    ), source, assertion


# --- Model / immutability (Phase 18) ---

class TestModelImmutability:
    def test_persists_and_writes_column(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            assert result.identifier_id is not None
            assert result.column_written is True
            assert airport.iata_code == "TST"

    def test_update_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            session.commit()
            row = session.get(AirportIdentifier, result.identifier_id)
            row.analyst = "human:someone-else"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()
            session.rollback()

    def test_delete_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            session.commit()
            row = session.get(AirportIdentifier, result.identifier_id)
            session.delete(row)
            with pytest.raises(ValueError, match="auditable and cannot be deleted"):
                session.commit()
            session.rollback()

    def test_invalid_identifier_type_check_constraint(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            row = AirportIdentifier(
                airport_id=airport.id, identifier_type="XYZ", identifier_value="TST",
                source_id=source.id, source_assertion_id=assertion.id, evidence_excerpt=_EXCERPT,
                analyst="human:tester", evidence_class="AUTHORITATIVE_DIRECT", status="ADMITTED",
            )
            session.add(row)
            with pytest.raises(Exception):
                session.commit()
            session.rollback()

    def test_supersedes_fk(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            reject_result = record_airport_identifier(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                identifier_type="IATA", identifier_value="TST", evidence_excerpt="withdrawal note",
                analyst="human:tester", status="REJECTED", supersedes_identifier_id=result.identifier_id,
            )
            session.commit()
            row = session.get(AirportIdentifier, reject_result.identifier_id)
            assert row.supersedes_identifier_id == result.identifier_id


# --- Typing (Phase 19) ---

class TestTypedRouting:
    def test_iata_writes_only_iata_code(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport, identifier_type="IATA", identifier_value="TST", token="TST(IATA)")
            assert airport.iata_code == "TST"
            assert airport.icao_code is None
            assert airport.faa_code is None

    def test_icao_writes_only_icao_code(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport, identifier_type="ICAO", identifier_value="TSTI", token="TSTI(ICAO)")
            assert airport.icao_code == "TSTI"
            assert airport.iata_code is None
            assert airport.faa_code is None

    def test_faa_writes_only_faa_code(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport, identifier_type="FAA", identifier_value="TS1", token="TS1(FAA)", excerpt="Test Airport (TS1). TS1(FAA)")
            assert airport.faa_code == "TS1"
            assert airport.iata_code is None
            assert airport.icao_code is None

    def test_invalid_type_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(InvalidIdentifierTypeError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="XYZ", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_type_not_established_fails(self):
        """Excerpt contains the value but never states its type at all."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            excerpt = "Test Airport code is TST, unspecified."
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(TypeEvidenceNotInExcerptError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_type_token_incomplete_fails(self):
        """type_evidence_token supplied but doesn't itself contain both value and type."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            excerpt = "Test Airport (TST) code TST here."
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(TypeEvidenceIncompleteError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
                    analyst="human:tester", type_evidence_token="TST",
                )


# --- Evidence gates (Phase 20) ---

class TestEvidenceGates:
    def test_identifier_not_literal_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(IdentifierNotInExcerptError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="ZZZ", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="ZZZ(IATA)",
                )

    def test_excerpt_not_within_source_assertion_text_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(ExcerptNotInPreservedEvidenceError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt="Test Airport (TST). TST(IATA) fabricated",
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_non_official_source_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport, reliability_level="unverified")
            with pytest.raises(InsufficientSourceReliabilityError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_source_mismatch_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, assertion = _seed_admitting_evidence(session, airport)
            other = Source(title="Other", source_type="government", reliability_level="official")
            session.add(other)
            session.commit()
            with pytest.raises(SourceAssertionSourceMismatchError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=other.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_airport_mismatch_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            other_airport = _seed_airport(session, name="Other Airport")
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(SourceAssertionAirportMismatchError):
                record_airport_identifier(
                    session, airport_id=other_airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_canonical_name_anchor_passes(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            assert result.identifier_id is not None

    def test_admitted_alias_anchor_passes(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Codename Only")
            alias_excerpt = "Alt Name (Codename Only) is official."
            alias_source, alias_assertion = _seed_admitting_evidence(session, airport, excerpt=alias_excerpt)
            record_airport_alias(
                session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
                alias="Alt Name", evidence_excerpt=alias_excerpt, analyst="human:tester",
            )
            excerpt = "Alt Name (TST) code. TST(IATA)"
            source2, assertion2 = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            result = record_airport_identifier(
                session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
                analyst="human:tester", type_evidence_token="TST(IATA)",
            )
            assert result.identifier_id is not None

    def test_retired_alias_anchor_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Codename Only")
            alias_excerpt = "Alt Name (Codename Only) is official."
            alias_source, alias_assertion = _seed_admitting_evidence(session, airport, excerpt=alias_excerpt)
            alias_result = record_airport_alias(
                session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
                alias="Alt Name", evidence_excerpt=alias_excerpt, analyst="human:tester",
            )
            record_airport_alias(
                session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
                alias="Alt Name", evidence_excerpt="retracted", analyst="human:tester",
                status="RETIRED", supersedes_alias_id=alias_result.alias_id,
            )
            excerpt = "Alt Name (TST) code. TST(IATA)"
            source2, assertion2 = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(NoIdentityAnchorError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_proposed_identifier_cannot_anchor_itself(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Anonymous Airport")
            excerpt = "TST(IATA) is a code with no airport name at all."
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(NoIdentityAnchorError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )


# --- Conflicts (Phase 21) ---

class TestConflicts:
    def test_target_column_null_is_eligible(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            assert result.identifier_id is not None

    def test_target_column_already_same_value_refused(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            source2, assertion2 = _seed_admitting_evidence(session, airport)
            with pytest.raises(CurrentColumnPopulatedError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_target_column_different_value_refused(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            excerpt2 = "Test Airport (XYZ). XYZ(IATA)"
            source2, assertion2 = _seed_admitting_evidence(session, airport, excerpt=excerpt2)
            with pytest.raises(CurrentColumnPopulatedError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                    identifier_type="IATA", identifier_value="XYZ", evidence_excerpt=excerpt2,
                    analyst="human:tester", type_evidence_token="XYZ(IATA)",
                )

    def test_same_typed_identifier_on_another_airport_refused(self):
        with Session(_engine()) as session:
            airport1 = _seed_airport(session, name="Airport One")
            airport2 = _seed_airport(session, name="Airport Two")
            excerpt1 = "Airport One (TST). TST(IATA)"
            _admit(session, airport1, excerpt=excerpt1)
            excerpt2 = "Airport Two (TST). TST(IATA)"
            source2, assertion2 = _seed_admitting_evidence(session, airport2, excerpt=excerpt2)
            with pytest.raises(CrossAirportTypedCollisionError):
                record_airport_identifier(
                    session, airport_id=airport2.id, source_id=source2.id, source_assertion_id=assertion2.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt2,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_same_string_different_type_does_not_falsely_collide(self):
        with Session(_engine()) as session:
            airport1 = _seed_airport(session, name="Airport One")
            airport2 = _seed_airport(session, name="Airport Two")
            excerpt1 = "Airport One (TST). TST(IATA)"
            _admit(session, airport1, identifier_type="IATA", identifier_value="TST", token="TST(IATA)", excerpt=excerpt1)
            excerpt2 = "Airport Two (TST). TST(ICAO)"
            result2, _s, _a = _admit(
                session, airport2, identifier_type="ICAO", identifier_value="TST", token="TST(ICAO)", excerpt=excerpt2,
            )
            assert result2.identifier_id is not None
            assert airport2.icao_code == "TST"

    def test_duplicate_governed_admission_refused(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            source2, assertion2 = _seed_admitting_evidence(session, airport)
            with pytest.raises(CurrentColumnPopulatedError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )


# --- Circularity (Phase 22) ---

class TestCircularity:
    def test_independent_source_governs_code_allowed(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            target_source = Source(title="News", source_type="news", reliability_level="unverified")
            session.add(target_source)
            session.flush()
            target_excerpt = "Test Airport EMAS news."
            target_assertion = SourceAssertion(
                source_id=target_source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=target_excerpt, source_record_identifier="target-rec",
                evidence_quality="direct_strong",
            )
            session.add(target_assertion)
            session.flush()
            record_manual_identity_evidence(
                session, source_assertion_id=target_assertion.id, source_id=target_source.id,
                evidence_excerpt=target_excerpt, analyst="human:tester", raw_airport_name="Test Airport",
            )
            result, _s, _a = _admit(session, airport)
            assert result.identifier_id is not None

    def test_self_confirming_source_assertion_rejected(self):
        """The admitting SourceAssertion is itself the only evidence for
        an identifier that would flip its OWN outcome."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source = Source(title="Circular", source_type="news", reliability_level="official")
            session.add(source)
            session.flush()
            excerpt = "Test Airport (TST) EMAS news. TST(IATA)"
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=excerpt, source_record_identifier="circ-rec", evidence_quality="direct_strong",
            )
            session.add(assertion)
            session.flush()
            record_manual_identity_evidence(
                session, source_assertion_id=assertion.id, source_id=source.id,
                evidence_excerpt=excerpt, analyst="human:tester", raw_airport_name="Test Airport",
                raw_identifier_code="TST",
            )
            with pytest.raises(CircularIdentifierEvidenceError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=excerpt,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_same_source_different_assertion_whose_outcome_flips_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source = Source(title="Shared source", source_type="news", reliability_level="official")
            session.add(source)
            session.flush()

            target_excerpt = "Test Airport EMAS news mentions TST identifier."
            target = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=target_excerpt, source_record_identifier="rec-target",
                evidence_quality="direct_strong",
            )
            session.add(target)
            session.flush()
            record_manual_identity_evidence(
                session, source_assertion_id=target.id, source_id=source.id,
                evidence_excerpt=target_excerpt, analyst="human:tester", raw_airport_name="Test Airport",
                raw_identifier_code="TST",
            )

            admit_excerpt = _EXCERPT
            admit_assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
                raw_relevant_text=admit_excerpt, source_record_identifier="rec-admit",
                evidence_quality="direct_strong",
            )
            session.add(admit_assertion)
            session.flush()

            with pytest.raises(CircularIdentifierEvidenceError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=admit_assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=admit_excerpt,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )

    def test_no_snapshot_reported_safely_not_treated_as_flip(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)  # no EvidenceBag/MIE for this one
            preview = preview_airport_identifier_admission_impact(
                session, airport_id=airport.id, identifier_type="IATA", proposed_value="TST",
            )
            row = next(r for r in preview.rows if r.source_assertion_id == assertion.id)
            assert row.has_snapshot is False
            assert row.changed is False

    def test_future_independent_source_conceptually_benefits_from_admitted_code(self):
        """Proves the intended use case (design mission Phase 18/21): once
        a code is admitted (via an independent source), a real,
        unmodified guard call for a DIFFERENT, later fragment naturally
        reaches ATTACH_CONFIRMED from the identifier alone."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            candidate = candidate_airport_from_airport_like(airport)
            from app.services.evidence_attachment_guard import EvidenceBag
            decision = evaluate_attachment(candidate, EvidenceBag(identifiers=frozenset({"TST"})))
            assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


# --- Atomicity (Phase 23) ---

class TestAtomicity:
    def test_failed_eligibility_leaves_no_audit_row_and_no_column_write(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport, reliability_level="unverified")
            with pytest.raises(InsufficientSourceReliabilityError):
                record_airport_identifier(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    identifier_type="IATA", identifier_value="TST", evidence_excerpt=_EXCERPT,
                    analyst="human:tester", type_evidence_token="TST(IATA)",
                )
            assert session.scalars(select(AirportIdentifier)).all() == []
            assert airport.iata_code is None

    def test_committed_admission_has_both_audit_row_and_column_value(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            session.commit()
            row = session.get(AirportIdentifier, result.identifier_id)
            refreshed_airport = session.get(Airport, airport.id)
            assert row is not None
            assert refreshed_airport.iata_code == row.identifier_value

    def test_rollback_undoes_both_audit_row_and_column_write_together(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            session.rollback()
            assert session.scalars(select(AirportIdentifier)).all() == []
            fresh = session.get(Airport, airport.id)
            assert fresh is None or fresh.iata_code is None


# --- IdentityGuard composition (Phase 24) ---

class TestIdentityGuardComposition:
    def test_before_and_after_governed_code_natural_transition(self):
        from app.services.evidence_attachment_guard import EvidenceBag
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            candidate_before = candidate_airport_from_airport_like(airport)
            before = evaluate_attachment(
                candidate_before, EvidenceBag(names=frozenset({"Test Airport"}), identifiers=frozenset({"TST"})),
            )
            assert before.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

            _admit(session, airport)

            candidate_after = candidate_airport_from_airport_like(airport)
            after = evaluate_attachment(
                candidate_after, EvidenceBag(names=frozenset({"Test Airport"}), identifiers=frozenset({"TST"})),
            )
            assert after.outcome == AttachmentOutcome.ATTACH_CONFIRMED

    def test_wrong_code_still_rejects_after_governed_admission(self):
        from app.services.evidence_attachment_guard import EvidenceBag
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            candidate = candidate_airport_from_airport_like(airport)
            decision = evaluate_attachment(candidate, EvidenceBag(identifiers=frozenset({"WRONG"})))
            assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


# --- Phase 25: a "Source-84-shaped" synthetic international fixture -
# never the real Airport 88/Source 84/사천공항/HIN/RKPS data, and no
# Korea-specific code anywhere in production. Proves the mechanism
# generically for a document that literally, in one excerpt, co-presents
# a native-script alias, an English canonical name, and BOTH a typed IATA
# and a typed ICAO code - exactly the real document's own shape, with
# entirely fictional values. ---

class TestSourceShapedSyntheticFixture:
    def test_iata_and_icao_both_eligible_from_one_official_excerpt(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Example Airport", country="Exampleland")
            excerpt = (
                "예시공항\n"
                "(Example Airport)\n\n"
                "코드 : XMP(IATA) / XMPL(ICAO)"
            )
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)

            iata_preview = check_airport_identifier_admission_eligibility(
                session, airport=airport, source=source, source_assertion=assertion,
                identifier_type="IATA", identifier_value="XMP", evidence_excerpt=excerpt,
                type_evidence_token="XMP(IATA)", evidence_class="AUTHORITATIVE_DIRECT",
            )
            assert iata_preview.airport_id == airport.id

            iata_result = record_airport_identifier(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                identifier_type="IATA", identifier_value="XMP", evidence_excerpt=excerpt,
                analyst="human:tester", type_evidence_token="XMP(IATA)",
            )
            assert iata_result.column_written is True
            assert airport.iata_code == "XMP"

            # ICAO fact from the SAME excerpt, as its own separately-
            # governed admission (design mission's own recommendation:
            # two independent facts, not one atomic operation).
            icao_result = record_airport_identifier(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                identifier_type="ICAO", identifier_value="XMPL", evidence_excerpt=excerpt,
                analyst="human:tester", type_evidence_token="XMPL(ICAO)",
            )
            assert icao_result.column_written is True
            assert airport.icao_code == "XMPL"

    def test_no_korea_specific_tokens_in_production_module(self):
        import inspect
        import re
        import app.services.airport_identifier as service_module
        import app.models.airport_identifier as model_module
        # Word-boundary matching - a bare substring check would false-
        # positive on ordinary English words like "matching"/"Washington"
        # containing "HIN" as a substring.
        forbidden = ("Sacheon", "Korea", "HIN", "RKPS", "사천")
        for module in (service_module, model_module):
            source = inspect.getsource(module)
            for token in forbidden:
                pattern = r"\b" + re.escape(token) + r"\b"
                assert not re.search(pattern, source, re.IGNORECASE), (
                    f"unexpected Korea/Sacheon-specific token {token!r} in {module.__name__}"
                )
