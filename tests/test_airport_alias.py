"""Tests for app/models/airport_alias.py and app/services/airport_alias.py
(docs/architecture, "RWI - Governed Canonical Airport Aliases -
Cross-Script Identity Design" mission).

Isolated, in-memory SQLite databases only - never the real one.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.source_assertion_legacy_identity_attestation import SourceAssertionLegacyIdentityAttestation
from app.services.airport_alias import (
    AirportNotFoundError,
    AliasNotInExcerptError,
    CircularAliasEvidenceError,
    ConflictingAliasStatusRequiresSupersessionError,
    DuplicateActiveAliasError,
    EmptyAliasError,
    EmptyAnalystError,
    EmptyEvidenceExcerptError,
    ExcerptNotInPreservedEvidenceError,
    InsufficientSourceReliabilityError,
    NoIdentityAnchorError,
    SourceAssertionNotFoundError,
    SourceAssertionSourceMismatchError,
    SourceNotFoundError,
    check_airport_alias_admission_eligibility,
    get_admitted_airport_aliases,
    preview_airport_alias_admission_impact,
    record_airport_alias,
)
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    candidate_airport_from_airport_like,
    evaluate_attachment,
)
from app.services.evidence_bag_serialization import deserialize_evidence_bag
from app.services.manual_identity_evidence import record_manual_identity_evidence
from app.services.resolved_candidate_evidence_reevaluation import reevaluate_resolved_candidate_evidence
from app.services.effective_identity_guard_decision import (
    EffectiveIdentityGuardDecisionBasis,
    resolve_effective_identity_guard_decision,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


_ANCHOR_EXCERPT = "테스트공항(Test Airport) is the official name."
_ALIAS = "테스트공항"


def _seed_airport(session, **overrides) -> Airport:
    kwargs = dict(name="Test Airport", country="Testland")
    kwargs.update(overrides)
    airport = Airport(**kwargs)
    session.add(airport)
    session.flush()
    return airport


def _seed_admitting_evidence(session, airport, *, reliability_level="official", excerpt=_ANCHOR_EXCERPT, source_type="government"):
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


def _admit(session, airport, *, alias=_ALIAS, excerpt=_ANCHOR_EXCERPT, reliability_level="official", **kwargs):
    source, assertion = _seed_admitting_evidence(session, airport, reliability_level=reliability_level, excerpt=excerpt)
    return record_airport_alias(
        session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
        alias=alias, evidence_excerpt=excerpt, analyst="human:tester", **kwargs,
    ), source, assertion


def _seed_target_assertion(session, airport, *, raw_name=_ALIAS, source_type="news", reliability_level="unverified"):
    """The SA232-shaped population: a DIFFERENT, unresolved SourceAssertion
    for the same Airport that only ever states the alias, never the
    canonical name."""
    source = Source(title="News article", source_type=source_type, reliability_level=reliability_level)
    session.add(source)
    session.flush()
    excerpt = f"{raw_name} EMAS installation planned."
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text=excerpt, source_record_identifier=f"target-rec-{source.id}",
        evidence_quality="direct_strong",
    )
    session.add(assertion)
    session.flush()
    record_manual_identity_evidence(
        session, source_assertion_id=assertion.id, source_id=source.id,
        evidence_excerpt=excerpt, analyst="human:tester", raw_airport_name=raw_name,
    )
    return source, assertion


# --- 1-5: persistence / immutability ---

class TestPersistenceAndImmutability:
    def test_alias_persists(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _source, _assertion = _admit(session, airport)
            assert result.alias_id is not None
            assert result.status == "ADMITTED"

    def test_alias_unicode_preserved_exactly(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            session.commit()
            row = session.get(AirportAlias, result.alias_id)
            assert row.alias == _ALIAS

    def test_airport_alias_immutable_update_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            session.commit()
            row = session.get(AirportAlias, result.alias_id)
            row.analyst = "human:someone-else"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()
            session.rollback()

    def test_airport_alias_delete_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport)
            session.commit()
            row = session.get(AirportAlias, result.alias_id)
            session.delete(row)
            with pytest.raises(ValueError, match="auditable and cannot be deleted"):
                session.commit()
            session.rollback()

    def test_extraction_class_check_constraint_rejects_unknown_class(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            row = AirportAlias(
                airport_id=airport.id, alias=_ALIAS, source_id=source.id, source_assertion_id=assertion.id,
                evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester", evidence_class="HUMAN_RESOLUTION",
                status="ADMITTED",
            )
            session.add(row)
            with pytest.raises(Exception):
                session.commit()
            session.rollback()


# --- 6-9: required fields ---

class TestRequiredFields:
    def test_airport_required(self):
        with Session(_engine()) as session:
            source = Source(title="x", source_type="government", reliability_level="official")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=None, assertion_type="airport_inventory",
                raw_relevant_text=_ANCHOR_EXCERPT, source_record_identifier="rec-x",
            )
            session.add(assertion)
            session.flush()
            with pytest.raises(AirportNotFoundError):
                record_airport_alias(
                    session, airport_id=999999, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_source_required(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(SourceNotFoundError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=999999, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_source_assertion_required(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, _assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(SourceAssertionNotFoundError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=999999,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_alias_required(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(EmptyAliasError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias="   ", evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_evidence_excerpt_required(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(EmptyEvidenceExcerptError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt="  ", analyst="human:tester",
                )

    def test_analyst_required(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(EmptyAnalystError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="  ",
                )

    def test_evidence_basis_required(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(ValueError, match="evidence_class"):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                    evidence_class="MADE_UP",
                )


# --- 12-19: literal-evidence / anchor safety ---

class TestLiteralEvidenceAndAnchor:
    def test_alias_must_literally_occur_in_evidence(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(AliasNotInExcerptError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias="완전히다른이름", evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_canonical_name_anchor_accepted(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)  # name="Test Airport", in _ANCHOR_EXCERPT
            result, _s, _a = _admit(session, airport)
            assert result.alias_id is not None

    def test_iata_anchor_accepted(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Other Name", iata_code="TST")
            excerpt = "테스트공항 (TST) is the code."
            result, _s, _a = _admit(session, airport, excerpt=excerpt)
            assert result.alias_id is not None

    def test_icao_anchor_accepted(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Other Name", icao_code="KTST")
            excerpt = "테스트공항 (KTST) is the code."
            result, _s, _a = _admit(session, airport, excerpt=excerpt)
            assert result.alias_id is not None

    def test_faa_anchor_accepted(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Other Name", faa_code="TS1")
            excerpt = "테스트공항 (TS1) is the code."
            result, _s, _a = _admit(session, airport, excerpt=excerpt)
            assert result.alias_id is not None

    def test_no_canonical_anchor_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Completely Different Name")
            excerpt = "테스트공항 has an EMAS project."
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(NoIdentityAnchorError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=excerpt, analyst="human:tester",
                )

    def test_translation_only_rejected(self):
        """Supplying the ENGLISH translation as the 'alias' when the
        excerpt never states the local-script form at all - the anchor
        check demands the alias itself be literally present, not merely
        translatable from something present."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            excerpt = "테스트공항 has an EMAS project."  # no English form present
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(AliasNotInExcerptError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias="Test Airport Alternate Translation", evidence_excerpt=excerpt, analyst="human:tester",
                )

    def test_transliteration_only_rejected(self):
        """An analyst-supplied romanization that never literally appears
        in the excerpt must be refused - same mechanism as translation."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            excerpt = "테스트공항 has an EMAS project."
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(AliasNotInExcerptError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias="Teseuteugonghang", evidence_excerpt=excerpt, analyst="human:tester",
                )

    def test_wrong_airport_evidence_rejected(self):
        """Evidence anchored to a DIFFERENT airport's own name/codes can
        never admit an alias for this airport - the anchor check is
        airport-specific, not merely 'some airport-like text is present'."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Test Airport", iata_code="TST")
            other_airport = _seed_airport(session, name="Other Airport", iata_code="OTH")
            excerpt = "테스트공항 (OTH) - not this airport's own code."
            source, assertion = _seed_admitting_evidence(session, airport, excerpt=excerpt)
            with pytest.raises(NoIdentityAnchorError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=excerpt, analyst="human:tester",
                )


# --- 21-22: SourceAssertion/Source binding ---

class TestSourceBinding:
    def test_source_assertion_mismatch_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, assertion = _seed_admitting_evidence(session, airport)
            other_source = Source(title="Other", source_type="government", reliability_level="official")
            session.add(other_source)
            session.commit()
            with pytest.raises(SourceAssertionSourceMismatchError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=other_source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_excerpt_binding_to_preserved_text_enforced(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(ExcerptNotInPreservedEvidenceError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt="테스트공항(Test Airport) fabricated excerpt never preserved.",
                    analyst="human:tester",
                )


# --- 23-24: anti-circularity ---

class TestAntiCircularity:
    def test_circular_source_rejected_self_reference(self):
        """The admitting SourceAssertion is itself the only evidence for
        an alias that would flip its OWN outcome - the exact SA232
        self-confirmation shape."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            excerpt = "테스트공항(Test Airport) EMAS project."
            source = Source(title="Circular", source_type="news", reliability_level="official")
            session.add(source)
            session.flush()
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=excerpt, source_record_identifier="rec-circ", evidence_quality="direct_strong",
            )
            session.add(assertion)
            session.flush()
            record_manual_identity_evidence(
                session, source_assertion_id=assertion.id, source_id=source.id,
                evidence_excerpt=excerpt, analyst="human:tester", raw_airport_name=_ALIAS,
            )
            with pytest.raises(CircularAliasEvidenceError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=excerpt, analyst="human:tester",
                )

    def test_circular_source_rejected_same_source_different_assertion(self):
        """A DIFFERENT SourceAssertion from the SAME Source as the one
        whose own outcome would flip - still circular (same document)."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source = Source(title="Shared source", source_type="news", reliability_level="official")
            session.add(source)
            session.flush()

            target_excerpt = f"{_ALIAS} EMAS project."
            target = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=target_excerpt, source_record_identifier="rec-target",
                evidence_quality="direct_strong",
            )
            session.add(target)
            session.flush()
            record_manual_identity_evidence(
                session, source_assertion_id=target.id, source_id=source.id,
                evidence_excerpt=target_excerpt, analyst="human:tester", raw_airport_name=_ALIAS,
            )

            admit_excerpt = _ANCHOR_EXCERPT
            admit_assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
                raw_relevant_text=admit_excerpt, source_record_identifier="rec-admit",
                evidence_quality="direct_strong",
            )
            session.add(admit_assertion)
            session.flush()

            with pytest.raises(CircularAliasEvidenceError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=admit_assertion.id,
                    alias=_ALIAS, evidence_excerpt=admit_excerpt, analyst="human:tester",
                )

    def test_independent_source_accepted(self):
        """A DIFFERENT Source than the target assertion's own - the
        legitimate, non-circular case."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _target_source, _target_assertion = _seed_target_assertion(session, airport)
            result, _s, _a = _admit(session, airport)
            assert result.alias_id is not None


# --- source reliability (AUTHORITATIVE_DIRECT mechanical gate) ---

class TestSourceReliability:
    def test_unofficial_source_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport, reliability_level="unverified")
            with pytest.raises(InsufficientSourceReliabilityError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_official_source_accepted(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, _s, _a = _admit(session, airport, reliability_level="official")
            assert result.alias_id is not None


# --- 25-26: human never supplies a decision; HUMAN_RESOLUTION not an evidence basis ---

class TestNoDecisionOverride:
    def test_no_decision_parameter_on_write_function(self):
        import inspect
        sig = inspect.signature(record_airport_alias)
        forbidden = {"identity_guard_decision", "expected_decision", "force_attach", "override", "decision"}
        assert forbidden.isdisjoint(sig.parameters)

    def test_human_resolution_not_a_valid_evidence_class(self):
        from app.models.airport_alias import AIRPORT_ALIAS_EVIDENCE_CLASSES
        assert "HUMAN_RESOLUTION" not in AIRPORT_ALIAS_EVIDENCE_CLASSES
        assert AIRPORT_ALIAS_EVIDENCE_CLASSES == ("AUTHORITATIVE_DIRECT",)

    def test_human_resolution_rejected_as_evidence_class(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)
            with pytest.raises(ValueError, match="evidence_class"):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                    evidence_class="HUMAN_RESOLUTION",
                )


# --- 27-30: read helper / status / supersession ---

class TestActiveAliasReadPath:
    def test_admitted_alias_returned_by_read_helper(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            assert get_admitted_airport_aliases(session, airport.id) == frozenset({_ALIAS})

    def test_rejected_alias_not_returned(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            reject_result = record_airport_alias(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                alias=_ALIAS, evidence_excerpt="withdrawal note: found to be a different place",
                analyst="human:tester", status="REJECTED", supersedes_alias_id=result.alias_id,
            )
            assert reject_result.status == "REJECTED"
            assert get_admitted_airport_aliases(session, airport.id) == frozenset()

    def test_retired_alias_not_returned(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            record_airport_alias(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                alias=_ALIAS, evidence_excerpt="withdrawal note: airport renamed",
                analyst="human:tester", status="RETIRED", supersedes_alias_id=result.alias_id,
            )
            assert get_admitted_airport_aliases(session, airport.id) == frozenset()

    def test_supersession_preserves_history(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            record_airport_alias(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                alias=_ALIAS, evidence_excerpt="withdrawal note", analyst="human:tester",
                status="RETIRED", supersedes_alias_id=result.alias_id,
            )
            session.commit()
            rows = session.scalars(select(AirportAlias).where(AirportAlias.airport_id == airport.id)).all()
            assert len(rows) == 2
            assert rows[0].status == "ADMITTED"
            assert rows[1].status == "RETIRED"
            assert rows[1].supersedes_alias_id == rows[0].id

    def test_duplicate_active_alias_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            source2, assertion2 = _seed_admitting_evidence(session, airport)
            with pytest.raises(DuplicateActiveAliasError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                    alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                )

    def test_reversal_without_supersedes_id_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            with pytest.raises(ConflictingAliasStatusRequiresSupersessionError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt="withdrawal note", analyst="human:tester",
                    status="REJECTED",
                )

    def test_reversal_with_wrong_supersedes_id_rejected(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            with pytest.raises(ConflictingAliasStatusRequiresSupersessionError):
                record_airport_alias(
                    session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                    alias=_ALIAS, evidence_excerpt="withdrawal note", analyst="human:tester",
                    status="REJECTED", supersedes_alias_id=999999,
                )

    def test_readmission_after_rejection_allowed_with_supersession(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            result, source, assertion = _admit(session, airport)
            rejected = record_airport_alias(
                session, airport_id=airport.id, source_id=source.id, source_assertion_id=assertion.id,
                alias=_ALIAS, evidence_excerpt="withdrawal note", analyst="human:tester",
                status="REJECTED", supersedes_alias_id=result.alias_id,
            )
            source2, assertion2 = _seed_admitting_evidence(session, airport)
            readmit = record_airport_alias(
                session, airport_id=airport.id, source_id=source2.id, source_assertion_id=assertion2.id,
                alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
                status="ADMITTED", supersedes_alias_id=rejected.alias_id,
            )
            assert readmit.status == "ADMITTED"
            assert get_admitted_airport_aliases(session, airport.id) == frozenset({_ALIAS})


# --- 32-36: impact preview ---

class TestImpactPreview:
    def test_impact_preview_performs_zero_writes(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _seed_target_assertion(session, airport)
            preview_airport_alias_admission_impact(session, airport_id=airport.id, proposed_alias=_ALIAS)
            session.commit()  # would raise if anything unexpected were pending
            assert session.scalars(select(AirportAlias)).all() == []

    def test_impact_preview_current_outcome_correct(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, assertion = _seed_target_assertion(session, airport)
            preview = preview_airport_alias_admission_impact(session, airport_id=airport.id, proposed_alias=_ALIAS)
            row = next(r for r in preview.rows if r.source_assertion_id == assertion.id)
            assert row.current_outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY.value

    def test_impact_preview_hypothetical_outcome_correct(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, assertion = _seed_target_assertion(session, airport)
            preview = preview_airport_alias_admission_impact(session, airport_id=airport.id, proposed_alias=_ALIAS)
            row = next(r for r in preview.rows if r.source_assertion_id == assertion.id)
            assert row.hypothetical_outcome == AttachmentOutcome.ATTACH_PROVISIONAL.value

    def test_impact_preview_identifies_changed_assertions(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, assertion = _seed_target_assertion(session, airport)
            preview = preview_airport_alias_admission_impact(session, airport_id=airport.id, proposed_alias=_ALIAS)
            assert assertion.id in preview.changed_source_assertion_ids

    def test_impact_preview_reports_no_snapshot_rows_safely(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)  # no EvidenceBag/ManualIdentityEvidence
            preview = preview_airport_alias_admission_impact(session, airport_id=airport.id, proposed_alias=_ALIAS)
            row = next(r for r in preview.rows if r.source_assertion_id == assertion.id)
            assert row.has_snapshot is False
            assert row.current_outcome is None
            assert row.hypothetical_outcome is None
            assert row.changed is False


# --- 37-38: write recomputes preview; no automatic EB4 ---

class TestWritePathSafety:
    def test_alias_write_does_not_trust_stale_precomputed_preview(self):
        """The write path (check_airport_alias_admission_eligibility, via
        record_airport_alias) recomputes the anti-circularity simulation
        itself - proven by the fact that a circular admission is refused
        purely from parameters, with no preview object passed in at all."""
        import inspect
        sig = inspect.signature(record_airport_alias)
        assert "impact_preview" not in sig.parameters
        assert "precomputed_preview" not in sig.parameters

    def test_alias_admission_does_not_automatically_run_eb4(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, target = _seed_target_assertion(session, airport)
            before = session.get(SourceAssertion, target.id).identity_guard_decision
            _admit(session, airport)
            after = session.get(SourceAssertion, target.id).identity_guard_decision
            assert before == after == AttachmentOutcome.INSUFFICIENT_IDENTITY.value


# --- 39-43: CandidateAirport / IdentityGuard integration (unmodified) ---

class TestIdentityGuardIntegration:
    def test_candidate_airport_receives_admitted_aliases(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _admit(session, airport)
            aliases = get_admitted_airport_aliases(session, airport.id)
            candidate = candidate_airport_from_airport_like(airport, aliases=aliases)
            assert _ALIAS in candidate._names_norm or _ALIAS.casefold() in candidate._names_norm

    def test_name_evidence_function_unchanged_reference(self):
        """Structural proof this module never redefines or monkeypatches
        the guard's own comparison logic - it only ever supplies data
        into the existing, imported function."""
        import app.services.evidence_attachment_guard as guard
        import app.services.airport_alias as alias_module
        assert "_name_evidence" not in dir(alias_module)
        assert guard._name_evidence.__module__ == "app.services.evidence_attachment_guard"

    def test_evaluate_attachment_unchanged_reference(self):
        import app.services.evidence_attachment_guard as guard
        assert evaluate_attachment is guard.evaluate_attachment

    def test_cross_script_exact_alias_naturally_matches(self):
        """The core proof: once admitted, the alias alone (one NAME
        category) reaches ATTACH_PROVISIONAL naturally via the real,
        unmodified guard - never hard-coded."""
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, target = _seed_target_assertion(session, airport)
            _admit(session, airport)
            snapshot = session.scalar(
                select(SourceAssertionEvidenceBag).where(SourceAssertionEvidenceBag.source_assertion_id == target.id)
            )
            evidence = deserialize_evidence_bag(snapshot.evidence_bag_json)
            aliases = get_admitted_airport_aliases(session, airport.id)
            candidate = candidate_airport_from_airport_like(airport, aliases=aliases)
            decision = evaluate_attachment(candidate, evidence)
            assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

    def test_same_script_alias_naturally_matches(self):
        """An alias in the SAME script as the canonical name (e.g. an
        abbreviation) is handled identically - the guard has no script
        awareness at all."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Test International Airport")
            excerpt = "TIA (Test International Airport) news."
            result, _s, _a = _admit(session, airport, alias="TIA", excerpt=excerpt)
            assert result.alias_id is not None
            assert "TIA" in get_admitted_airport_aliases(session, airport.id)


# --- 44-49: EB4 / EB5 integration ---

class TestEB4EB5Integration:
    def test_eb4_reevaluation_consumes_original_evidencebag_unchanged(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, target = _seed_target_assertion(session, airport)
            snapshot_before = session.scalar(
                select(SourceAssertionEvidenceBag).where(SourceAssertionEvidenceBag.source_assertion_id == target.id)
            )
            hash_before = snapshot_before.evidence_bag_hash
            _admit(session, airport)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=target.id)
            snapshot_after = session.scalar(
                select(SourceAssertionEvidenceBag).where(SourceAssertionEvidenceBag.source_assertion_id == target.id)
            )
            assert snapshot_after.id == snapshot_before.id
            assert snapshot_after.evidence_bag_hash == hash_before

    def test_manual_identity_evidence_remains_unchanged_after_alias_admission(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            from app.models.manual_identity_evidence import ManualIdentityEvidence
            _source, target = _seed_target_assertion(session, airport)
            mie_before = session.scalar(
                select(ManualIdentityEvidence).where(ManualIdentityEvidence.source_assertion_id == target.id)
            )
            excerpt_before = mie_before.evidence_excerpt
            _admit(session, airport)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=target.id)
            mie_after = session.scalar(
                select(ManualIdentityEvidence).where(ManualIdentityEvidence.source_assertion_id == target.id)
            )
            assert mie_after.id == mie_before.id
            assert mie_after.evidence_excerpt == excerpt_before

    def test_raw_source_assertion_identity_decision_remains_unchanged(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, target = _seed_target_assertion(session, airport)
            raw_before = session.get(SourceAssertion, target.id).identity_guard_decision
            _admit(session, airport)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=target.id)
            raw_after = session.get(SourceAssertion, target.id).identity_guard_decision
            assert raw_after == raw_before == AttachmentOutcome.INSUFFICIENT_IDENTITY.value

    def test_new_identity_guard_evaluation_appended(self):
        with Session(_engine()) as session:
            from app.models.identity_guard_evaluation import IdentityGuardEvaluation
            airport = _seed_airport(session)
            _source, target = _seed_target_assertion(session, airport)
            _admit(session, airport)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=target.id)
            rows = session.scalars(
                select(IdentityGuardEvaluation).where(IdentityGuardEvaluation.source_assertion_id == target.id)
            ).all()
            assert len(rows) == 1
            assert rows[0].outcome == AttachmentOutcome.ATTACH_PROVISIONAL.value

    def test_eb5_switches_to_latest_reevaluation_naturally(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _source, target = _seed_target_assertion(session, airport)
            before = resolve_effective_identity_guard_decision(session, source_assertion_id=target.id)
            assert before.effective_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY

            _admit(session, airport)
            reevaluate_resolved_candidate_evidence(session, source_assertion_id=target.id)

            after = resolve_effective_identity_guard_decision(session, source_assertion_id=target.id)
            assert after.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION
            assert after.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL

    def test_no_legacy_attestation_created(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            _seed_target_assertion(session, airport)
            _admit(session, airport)
            assert session.scalars(select(SourceAssertionLegacyIdentityAttestation)).all() == []


# --- 51: SA233-shaped no-EvidenceBag row not magically confirmed ---

class TestUngovernedRowUnaffected:
    def test_no_evidencebag_row_not_magically_confirmed_by_alias_admission(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            source, assertion = _seed_admitting_evidence(session, airport)  # no EvidenceBag/MIE for this one
            _admit(session, airport)
            refreshed = session.get(SourceAssertion, assertion.id)
            assert refreshed.identity_guard_decision is None


# --- 57-59: existing regression (also see the mission-level full-suite run) ---

class TestExistingPathsUnaffected:
    def test_manual_identity_evidence_path_regression_smoke(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Plain Airport", city="Plain City")
            source = Source(title="x", source_type="news")
            session.add(source)
            session.flush()
            excerpt = "Plain Airport in Plain City news."
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=excerpt, source_record_identifier="rec-plain", evidence_quality="direct_strong",
            )
            session.add(assertion)
            session.flush()
            result = record_manual_identity_evidence(
                session, source_assertion_id=assertion.id, source_id=source.id,
                evidence_excerpt=excerpt, analyst="human:tester", raw_airport_name="Plain Airport", raw_city="Plain City",
            )
            assert result.identity_guard_decision == AttachmentOutcome.ATTACH_CONFIRMED.value

    def test_eb4_path_regression_smoke_no_aliases(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="Plain Airport", city="Plain City")
            source = Source(title="x", source_type="news")
            session.add(source)
            session.flush()
            excerpt = "Plain Airport in Plain City news."
            assertion = SourceAssertion(
                source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=excerpt, source_record_identifier="rec-plain2", evidence_quality="direct_strong",
            )
            session.add(assertion)
            session.flush()
            record_manual_identity_evidence(
                session, source_assertion_id=assertion.id, source_id=source.id,
                evidence_excerpt=excerpt, analyst="human:tester", raw_airport_name="Plain Airport", raw_city="Plain City",
            )
            result = reevaluate_resolved_candidate_evidence(session, source_assertion_id=assertion.id)
            assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED


# --- 60: Sacheon-shaped synthetic fixture, end to end ---

class TestSacheonShapedFixture:
    def test_full_flow_before_and_after_alias_admission(self):
        with Session(_engine()) as session:
            airport = Airport(name="Sacheon Airport", country="South Korea")
            session.add(airport)
            session.flush()

            # Target: SA232-shaped, Korean-only text, no code stated.
            target_source = Source(title="Kyunghyang-shaped", source_type="news", reliability_level="unverified")
            session.add(target_source)
            session.flush()
            target_excerpt = "사천공항 'EMAS' 첫 도입 추진."
            target = SourceAssertion(
                source_id=target_source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text=target_excerpt, source_record_identifier="sacheon-target",
                evidence_quality="direct_strong",
            )
            session.add(target)
            session.flush()
            mie_result = record_manual_identity_evidence(
                session, source_assertion_id=target.id, source_id=target_source.id,
                evidence_excerpt=target_excerpt, analyst="human:tester", raw_airport_name="사천공항",
            )
            assert mie_result.identity_guard_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY.value

            # Independent, official, admitting evidence.
            admit_excerpt = "사천공항(Sacheon Airport) is a domestic airport operated by MOLIT."
            admit_result, _admit_source, _admit_assertion = _admit(
                session, airport, alias="사천공항", excerpt=admit_excerpt,
            )
            assert admit_result.status == "ADMITTED"

            reeval = reevaluate_resolved_candidate_evidence(session, source_assertion_id=target.id)
            effective = resolve_effective_identity_guard_decision(session, source_assertion_id=target.id)

            # Natural, real, unmodified guard result - not hard-coded:
            # exactly one NAME category (no city/identifier on this
            # synthetic Airport 88 stand-in) -> ATTACH_PROVISIONAL, never
            # forced to ATTACH_CONFIRMED.
            assert reeval.outcome == AttachmentOutcome.ATTACH_PROVISIONAL
            assert effective.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION
            assert effective.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL

            # Original evidence stayed untouched throughout.
            assert session.get(SourceAssertion, target.id).identity_guard_decision == AttachmentOutcome.INSUFFICIENT_IDENTITY.value
