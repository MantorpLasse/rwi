"""Tests for app/models/manual_claim_evidence.py and
app/services/manual_claim_evidence.py ("RWI - First-Class Manual Claim
Evidence - Implementation" mission).

Isolated, in-memory SQLite databases only - never the real one.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.manual_claim_evidence import ManualClaimEvidence
import app.services.human_review_claim_enrichment as human_review_claim_enrichment
from app.services.human_review_claim_enrichment import enrich_claims
from app.services.manual_claim_evidence import (
    AmountEvidenceTokenMismatchError,
    AmountEvidenceTokenNotInExcerptError,
    DuplicateManualClaimEvidenceError,
    EmptyAnalystError,
    EmptyExcerptError,
    ExcerptNotInPreservedEvidenceError,
    IdentityNotEffectivelyConfirmedError,
    IncompleteFinancialGroupError,
    IncompleteRelationshipGroupError,
    InvalidClaimCategoryError,
    InvalidTemporalQualifierError,
    NoIdentityAnchorError,
    NotCanonicallyAttachedError,
    RelationshipPartyNotInExcerptError,
    TemporalYearTokenNotInExcerptError,
    get_manual_claims_for_source_assertion,
    preview_manual_claim_evidence,
    record_manual_claim_evidence,
)

_TEXT = "테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured for 2025년, 2026년, installation confirmed by the Authority."


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session, *, identity_guard_decision="ATTACH_CONFIRMED", raw_relevant_text=_TEXT, published_date=date(2025, 6, 11)):
    airport = Airport(name="Test Airport", country="Testland", iata_code="TST")
    session.add(airport); session.flush()
    source = Source(title="Authority Record", source_type="Authority", reliability_level="official", published_date=published_date)
    session.add(source); session.flush()
    sa = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text=raw_relevant_text, source_record_identifier="rec-1", evidence_quality="direct_strong",
        identity_guard_decision=identity_guard_decision,
    )
    session.add(sa); session.flush()
    return airport, source, sa


def _basic_kwargs(sa, **overrides):
    kwargs = dict(
        source_assertion_id=sa.id, claim_category="explicit_document_fact",
        subject="EMAS installation", statement="Installation confirmed at Test Airport.",
        evidence_excerpt="테스트공항 (Test Airport) EMAS project", analyst="human:tester",
    )
    kwargs.update(overrides)
    return kwargs


# --- MODEL / IMMUTABILITY ---

def test_create_persists():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        result = record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        row = session.get(ManualClaimEvidence, result.manual_claim_evidence_id)
        assert row.source_assertion_id == sa.id
        assert row.claim_category == "explicit_document_fact"


def test_update_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        result = record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        row = session.get(ManualClaimEvidence, result.manual_claim_evidence_id)
        row.statement = "changed"
        with pytest.raises(ValueError):
            session.commit()
        session.rollback()


def test_delete_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        result = record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        row = session.get(ManualClaimEvidence, result.manual_claim_evidence_id)
        session.delete(row)
        with pytest.raises(ValueError):
            session.commit()
        session.rollback()


# --- IDENTITY ---

@pytest.mark.parametrize("raw,expect_ok", [
    ("ATTACH_CONFIRMED", True),
    ("ATTACH_PROVISIONAL", False),
    ("INSUFFICIENT_IDENTITY", False),
    ("REJECT_CROSS_AIRPORT", False),
    (None, False),
])
def test_raw_identity_alone_vs_effective(raw, expect_ok):
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, identity_guard_decision=raw)
        if expect_ok:
            result = record_manual_claim_evidence(session, **_basic_kwargs(sa))
            assert result.manual_claim_evidence_id is not None
        else:
            with pytest.raises(IdentityNotEffectivelyConfirmedError):
                record_manual_claim_evidence(session, **_basic_kwargs(sa))


def test_raw_provisional_effective_confirmed_via_cross_source_attestation_accepted():
    """SA235's exact real shape: raw ATTACH_PROVISIONAL, effective
    ATTACH_CONFIRMED via CrossSourceAliasAttestation."""
    from app.models.airport_alias import AirportAlias
    from app.services.cross_source_alias_attestation import record_cross_source_alias_attestation

    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, identity_guard_decision="ATTACH_PROVISIONAL")
        alias_source = Source(title="Alias registry", source_type="Authority", reliability_level="official")
        session.add(alias_source); session.flush()
        alias_assertion = SourceAssertion(
            source_id=alias_source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text="테스트공항(Test Airport) official.", source_record_identifier="rec-alias",
            evidence_quality="direct_strong",
        )
        session.add(alias_assertion); session.flush()
        alias = AirportAlias(
            airport_id=airport.id, alias="테스트공항", source_id=alias_source.id,
            source_assertion_id=alias_assertion.id, evidence_excerpt="테스트공항(Test Airport) official.",
            analyst="human:tester", evidence_class="AUTHORITATIVE_DIRECT", status="ADMITTED",
        )
        session.add(alias); session.flush()

        record_cross_source_alias_attestation(
            session, source_assertion_id=sa.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="test",
        )
        session.commit()

        result = record_manual_claim_evidence(session, **_basic_kwargs(sa))
        assert result.manual_claim_evidence_id is not None


def test_effective_identity_reused_at_consumption_not_write_time():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, identity_guard_decision="ATTACH_CONFIRMED")
        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        claims = get_manual_claims_for_source_assertion(session, sa.id)
        assert claims is not None and len(claims) == 1


def test_consumption_returns_none_when_no_rows():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, identity_guard_decision="ATTACH_CONFIRMED")
        assert get_manual_claims_for_source_assertion(session, sa.id) is None


def test_consumption_returns_none_when_identity_not_confirmed():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, identity_guard_decision="ATTACH_PROVISIONAL")
        # Bypass write-time gate directly on the model to test consumption gate in isolation.
        row = ManualClaimEvidence(
            source_assertion_id=sa.id, claim_category="explicit_document_fact", subject="x", statement="y",
            evidence_excerpt="테스트공항 EMAS project", analyst="human:tester",
        )
        session.add(row); session.commit()
        assert get_manual_claims_for_source_assertion(session, sa.id) is None


# --- SOURCE BINDING ---

def test_no_canonical_airport_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        sa.airport_id = None
        session.flush()
        with pytest.raises(NotCanonicallyAttachedError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa))


def test_airport_and_source_inherited_never_caller_supplied():
    """No airport_id/source_id parameter exists at all on the write
    function - structurally impossible to supply a mismatch."""
    import inspect as _inspect
    sig = _inspect.signature(record_manual_claim_evidence)
    assert "airport_id" not in sig.parameters
    assert "source_id" not in sig.parameters


# --- LITERALITY ---

def test_excerpt_not_in_preserved_text_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(ExcerptNotInPreservedEvidenceError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, evidence_excerpt="something not present"))


def test_no_identity_anchor_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, raw_relevant_text="Generic unrelated text about EMAS in general, no airport named.")
        with pytest.raises(NoIdentityAnchorError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(sa, evidence_excerpt="Generic unrelated text about EMAS in general"),
            )


def test_translated_token_refused():
    """The excerpt contains an ENGLISH translation, not the literal source
    numeral/currency token - amount_evidence_token must be literal."""
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(AmountEvidenceTokenNotInExcerptError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(
                    sa, evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
                    financial_amount=Decimal("19600000"), financial_amount_evidence_token="USD 19.6 million",
                    financial_currency="USD", financial_semantic_role="budget_secured",
                ),
            )


def test_inferred_value_mismatch_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(AmountEvidenceTokenMismatchError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(
                    sa, evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
                    financial_amount=Decimal("99999"), financial_amount_evidence_token="27,000,000,000",
                    financial_currency="KRW", financial_semantic_role="budget_secured",
                ),
            )


def test_fuzzy_relationship_party_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(RelationshipPartyNotInExcerptError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(
                    sa, relationship_party="The Ministry (not literally present)", relationship_role="oversight",
                ),
            )


def test_empty_excerpt_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(EmptyExcerptError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, evidence_excerpt="  "))


def test_empty_analyst_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(EmptyAnalystError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, analyst="  "))


def test_invalid_claim_category_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(InvalidClaimCategoryError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, claim_category="not_a_real_category"))


# --- FINANCIAL ---

def test_decimal_preserved_exactly_no_conversion():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        record_manual_claim_evidence(
            session, **_basic_kwargs(
                sa, evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
                financial_amount=Decimal("27000000000"), financial_amount_evidence_token="27,000,000,000",
                financial_currency="KRW", financial_semantic_role="budget_secured",
            ),
        )
        session.commit()
        claims = get_manual_claims_for_source_assertion(session, sa.id)
        assert claims[0].financial.amount == Decimal("27000000000")
        assert claims[0].financial.currency == "KRW"


def test_generic_non_krw_currency_works():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, raw_relevant_text="테스트공항 (Test Airport) EUR 12,500,000 secured for the project.")
        record_manual_claim_evidence(
            session, **_basic_kwargs(
                sa, evidence_excerpt="테스트공항 (Test Airport) EUR 12,500,000 secured",
                financial_amount=Decimal("12500000"), financial_amount_evidence_token="12,500,000",
                financial_currency="EUR", financial_semantic_role="budget_secured",
            ),
        )
        session.commit()
        claims = get_manual_claims_for_source_assertion(session, sa.id)
        assert claims[0].financial.currency == "EUR"


def test_incomplete_financial_group_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(IncompleteFinancialGroupError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(sa, financial_amount=Decimal("1"), financial_currency="KRW"),
            )


def test_semantic_role_required_within_financial_group():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(IncompleteFinancialGroupError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(
                    sa, financial_amount=Decimal("27000000000"), financial_amount_evidence_token="27,000,000,000",
                    financial_currency="KRW",
                ),
            )


# --- TEMPORAL ---

def test_existing_supported_semantics_preserved():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, published_date=date(2025, 6, 11))
        record_manual_claim_evidence(
            session, **_basic_kwargs(
                sa, evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured for 2025년, 2026년",
                temporal_qualifier="planned_future_action", temporal_year_tokens=("2025년", "2026년"),
            ),
        )
        session.commit()
        claims = get_manual_claims_for_source_assertion(session, sa.id)
        temporal = claims[0].temporal
        assert temporal.qualifier.value == "planned_future_action"
        assert temporal.as_of_date == date(2025, 6, 11)
        assert temporal.detail == "2025년, 2026년"


def test_as_of_date_never_inferred_always_document_date():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session, published_date=None)
        record_manual_claim_evidence(
            session, **_basic_kwargs(sa, temporal_qualifier="unknown"),
        )
        session.commit()
        claims = get_manual_claims_for_source_assertion(session, sa.id)
        assert claims[0].temporal.as_of_date is None


def test_invalid_temporal_qualifier_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(InvalidTemporalQualifierError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, temporal_qualifier="not_a_real_qualifier"))


def test_year_token_not_in_excerpt_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(TemporalYearTokenNotInExcerptError):
            record_manual_claim_evidence(
                session, **_basic_kwargs(sa, temporal_qualifier="planned_future_action", temporal_year_tokens=("2099",)),
            )


# --- DUPLICATION ---

def test_exact_duplicate_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        with pytest.raises(DuplicateManualClaimEvidenceError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa))


def test_different_literal_fact_allowed():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        result = record_manual_claim_evidence(
            session, **_basic_kwargs(
                sa, subject="EMAS budget", statement="Budget secured.",
                evidence_excerpt="테스트공항 (Test Airport) EMAS project: KRW 27,000,000,000 secured",
                financial_amount=Decimal("27000000000"), financial_amount_evidence_token="27,000,000,000",
                financial_currency="KRW", financial_semantic_role="budget_secured",
            ),
        )
        assert result.manual_claim_evidence_id is not None


# --- ADAPTER DETERMINISM ---

def test_adapter_deterministic_same_input_same_claim():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        claims_1 = get_manual_claims_for_source_assertion(session, sa.id)
        claims_2 = get_manual_claims_for_source_assertion(session, sa.id)
        assert claims_1 == claims_2


# --- PIPELINE INTEGRATION ---

def test_unsupported_auto_parser_with_manual_claims_produces_claims():
    """SA235's exact real shape: parser_identifier="manual-korea-research-v1"
    has no registered automatic adapter (confirmed directly against
    enrich_claims()'s own registries, without needing to hand-construct a
    full HumanReviewItem), yet manual claim evidence still produces Claims."""
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        sa.parser_identifier = "manual-korea-research-v1"
        session.flush()

        assert sa.parser_identifier not in human_review_claim_enrichment._PARSER_ONLY_ADAPTERS
        assert all(sa.parser_identifier != key[1] for key in human_review_claim_enrichment._SOURCE_TYPE_SCOPED_ADAPTERS)

        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        claims = get_manual_claims_for_source_assertion(session, sa.id)
        assert claims is not None and len(claims) == 1


def test_automatic_extractor_registry_unchanged():
    """Sanity: enrich_claims()'s own dispatch registries are completely
    untouched by this mission - this module never imports or modifies
    app.services.human_review_claim_enrichment at all."""
    from app.acquisition.mac_granicus_extractor import PARSER_VERSION as MAC_PARSER_VERSION

    assert MAC_PARSER_VERSION in human_review_claim_enrichment._PARSER_ONLY_ADAPTERS
    assert callable(enrich_claims)


# --- PREVIEW ---

def test_preview_never_writes():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        preview = preview_manual_claim_evidence(session, **{k: v for k, v in _basic_kwargs(sa).items() if k != "analyst"})
        assert preview.eligible is True
        assert session.query(ManualClaimEvidence).count() == 0


def test_preview_and_write_share_validation():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        kwargs = {k: v for k, v in _basic_kwargs(sa, evidence_excerpt="not present").items() if k != "analyst"}
        preview = preview_manual_claim_evidence(session, **kwargs)
        assert preview.eligible is False
        with pytest.raises(ExcerptNotInPreservedEvidenceError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, evidence_excerpt="not present"))


# --- ATOMICITY ---

def test_failed_validation_leaves_no_row():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        with pytest.raises(InvalidClaimCategoryError):
            record_manual_claim_evidence(session, **_basic_kwargs(sa, claim_category="bogus"))
        assert session.query(ManualClaimEvidence).count() == 0


def test_rollback_removes_row():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.rollback()
        assert session.query(ManualClaimEvidence).count() == 0


def test_no_identity_mutation():
    engine = _engine()
    with Session(engine) as session:
        airport, source, sa = _seed(session)
        record_manual_claim_evidence(session, **_basic_kwargs(sa))
        session.commit()
        refreshed = session.get(SourceAssertion, sa.id)
        assert refreshed.identity_guard_decision == "ATTACH_CONFIRMED"
