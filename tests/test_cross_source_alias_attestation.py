"""Tests for app/models/source_assertion_cross_source_alias_attestation.py
and app/services/cross_source_alias_attestation.py (docs/architecture,
"RWI - Cross-Source Governed Airport Identity Binding - Architecture
Recon" mission's own Option C).

Isolated, in-memory SQLite databases only - never the real one.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.models.source_assertion_cross_source_alias_attestation import (
    SourceAssertionCrossSourceAliasAttestation,
)
from app.services.airport_alias import record_airport_alias
from app.services.cross_source_alias_attestation import (
    AliasAirportMismatchError,
    AliasNotAdmittedError,
    AliasNotFoundError,
    AliasNotInAssertionEvidenceError,
    AmbiguousAliasAcrossAirportsError,
    DuplicateAttestationError,
    EmptyAnalystError,
    EmptyReasonError,
    NotCanonicallyAttachedError,
    NotIndependentSourceError,
    RawDecisionNotEligibleError,
    SignalAlreadyLinkedError,
    SourceAssertionNotFoundError,
    check_cross_source_alias_attestation_eligibility,
    get_latest_cross_source_alias_attestation,
    is_cross_source_alias_attestation_current,
    preview_cross_source_alias_attestation,
    record_cross_source_alias_attestation,
)
from app.services.effective_identity_guard_decision import (
    EffectiveIdentityGuardDecisionBasis,
    resolve_effective_identity_guard_decision,
)
from app.services.evidence_attachment_guard import AttachmentOutcome

_ALIAS = "테스트공항"
_ANCHOR_EXCERPT = "테스트공항(Test Airport) official record."


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, **overrides) -> Airport:
    kwargs = dict(name="Test Airport", country="Testland")
    kwargs.update(overrides)
    airport = Airport(**kwargs)
    session.add(airport)
    session.flush()
    return airport


def _seed_source(session, *, reliability_level="official") -> Source:
    source = Source(title="A registry", source_type="government", reliability_level=reliability_level)
    session.add(source)
    session.flush()
    return source


def _seed_assertion(session, airport, source, *, raw_relevant_text, identity_guard_decision="ATTACH_PROVISIONAL", signal_id=None) -> SourceAssertion:
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="airport_inventory",
        raw_relevant_text=raw_relevant_text, source_record_identifier=f"rec-{source.id}-{airport.id}-{raw_relevant_text[:8]}",
        evidence_quality="direct_strong", identity_guard_decision=identity_guard_decision, signal_id=signal_id,
    )
    session.add(assertion)
    session.flush()
    return assertion


def _admit_alias(session, airport, source, admitting_assertion, *, alias=_ALIAS, excerpt=_ANCHOR_EXCERPT) -> AirportAlias:
    result = record_airport_alias(
        session, airport_id=airport.id, source_id=source.id, source_assertion_id=admitting_assertion.id,
        alias=alias, evidence_excerpt=excerpt, analyst="human:tester",
    )
    return session.get(AirportAlias, result.alias_id)


def _standard_setup(session, *, being_attested_text=None):
    """One Airport, one ADMITTED alias (source A / assertion A1), one
    SEPARATE, INDEPENDENT being-attested SourceAssertion (source B) whose
    raw_relevant_text literally contains the alias - the canonical
    "everything should work" fixture every eligibility test starts from."""
    airport = _seed_airport(session)
    source_a = _seed_source(session)
    admitting_assertion = _seed_assertion(session, airport, source_a, raw_relevant_text=_ANCHOR_EXCERPT, identity_guard_decision=None)
    alias = _admit_alias(session, airport, source_a, admitting_assertion)

    source_b = _seed_source(session)
    text = being_attested_text if being_attested_text is not None else f"{_ALIAS} EMAS project underway."
    being_attested = _seed_assertion(session, airport, source_b, raw_relevant_text=text)
    return airport, source_a, admitting_assertion, alias, source_b, being_attested


# ---------------------------------------------------------------------------
# MODEL / IMMUTABILITY
# ---------------------------------------------------------------------------

def test_create_attestation_persists():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        result = record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="Independent official source uses the governed alias.",
        )
        session.commit()
        assert result.attestation_id is not None
        row = session.get(SourceAssertionCrossSourceAliasAttestation, result.attestation_id)
        assert row.source_assertion_id == being_attested.id
        assert row.matched_airport_id == airport.id
        assert row.matched_alias_id == alias.id


def test_required_fks_enforced():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        with pytest.raises(SourceAssertionNotFoundError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=999999, matched_alias_id=alias.id,
                analyst="human:tester", reason="x",
            )
        with pytest.raises(AliasNotFoundError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=999999,
                analyst="human:tester", reason="x",
            )


def test_update_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        result = record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.commit()
        row = session.get(SourceAssertionCrossSourceAliasAttestation, result.attestation_id)
        row.reason = "changed"
        with pytest.raises(ValueError):
            session.commit()
        session.rollback()


def test_delete_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        result = record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.commit()
        row = session.get(SourceAssertionCrossSourceAliasAttestation, result.attestation_id)
        session.delete(row)
        with pytest.raises(ValueError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# ELIGIBILITY - raw decision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_decision", ["ATTACH_CONFIRMED", "INSUFFICIENT_IDENTITY", "REJECT_CROSS_AIRPORT", None])
def test_raw_decision_other_than_provisional_refused(raw_decision):
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.identity_guard_decision = raw_decision
        session.flush()
        with pytest.raises(RawDecisionNotEligibleError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_raw_provisional_accepted():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        assert being_attested.identity_guard_decision == "ATTACH_PROVISIONAL"
        result = record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        assert result.attestation_id is not None


def test_no_canonical_airport_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.airport_id = None
        session.flush()
        with pytest.raises(NotCanonicallyAttachedError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_signal_already_linked_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.signal_id = None  # no real Signal seeded; simulate the linked state directly
        # Use a fake existing id to exercise the check without needing a full Signal fixture.
        being_attested.signal_id = 1
        session.flush()
        with pytest.raises(SignalAlreadyLinkedError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


# ---------------------------------------------------------------------------
# ALIAS state / anchoring
# ---------------------------------------------------------------------------

def test_retired_alias_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_airport_alias(
            session, airport_id=airport.id, source_id=source_a.id, source_assertion_id=admitting_assertion.id,
            alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
            status="RETIRED", supersedes_alias_id=alias.id,
        )
        with pytest.raises(AliasNotAdmittedError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_rejected_alias_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_airport_alias(
            session, airport_id=airport.id, source_id=source_a.id, source_assertion_id=admitting_assertion.id,
            alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
            status="REJECTED", supersedes_alias_id=alias.id,
        )
        with pytest.raises(AliasNotAdmittedError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_wrong_airport_alias_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        other_airport = _seed_airport(session, name="Other Airport")
        other_source = _seed_source(session)
        other_admitting = _seed_assertion(session, other_airport, other_source, raw_relevant_text="다른공항(Other Airport) official.", identity_guard_decision=None)
        other_alias = _admit_alias(session, other_airport, other_source, other_admitting, alias="다른공항", excerpt="다른공항(Other Airport) official.")
        with pytest.raises(AliasAirportMismatchError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=other_alias.id,
                analyst="human:tester", reason="reason",
            )


def test_literal_alias_required_fuzzy_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(
            session, being_attested_text="A nearby airfield, name unspecified, has EMAS work underway.",
        )
        with pytest.raises(AliasNotInAssertionEvidenceError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_canonical_name_alone_without_alias_text_does_not_qualify():
    """The being-attested text names the Airport by its CANONICAL name only
    - never the governed alias - which must not satisfy the literal-match
    requirement (this mechanism binds on the specific governed alias
    string, never on canonical name generically)."""
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(
            session, being_attested_text="Test Airport EMAS project underway.",
        )
        with pytest.raises(AliasNotInAssertionEvidenceError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


# ---------------------------------------------------------------------------
# UNIQUENESS
# ---------------------------------------------------------------------------

def test_unique_alias_owner_accepted():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        result = record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        assert result.attestation_id is not None


def test_duplicate_alias_across_two_airports_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        other_airport = _seed_airport(session, name="Other Airport")
        other_source = _seed_source(session)
        other_admitting = _seed_assertion(
            session, other_airport, other_source,
            raw_relevant_text=f"{_ALIAS}(Other Airport) unrelated official record.", identity_guard_decision=None,
        )
        # NOTE: admitting an identical alias STRING for a DIFFERENT Airport
        # is itself legal under AirportAlias's own airport-scoped-only
        # uniqueness (a pre-existing, known posture) - this is exactly the
        # collision this new mechanism must fail closed against.
        record_airport_alias(
            session, airport_id=other_airport.id, source_id=other_source.id, source_assertion_id=other_admitting.id,
            alias=_ALIAS, evidence_excerpt=f"{_ALIAS}(Other Airport) unrelated official record.",
            analyst="human:tester",
        )
        with pytest.raises(AmbiguousAliasAcrossAirportsError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


# ---------------------------------------------------------------------------
# INDEPENDENCE / ANTI-CIRCULARITY
# ---------------------------------------------------------------------------

def test_same_source_refused():
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport(session)
        source = _seed_source(session)
        admitting_assertion = _seed_assertion(session, airport, source, raw_relevant_text=_ANCHOR_EXCERPT, identity_guard_decision=None)
        alias = _admit_alias(session, airport, source, admitting_assertion)
        # Being-attested assertion from the SAME source as the alias's own admission.
        being_attested = _seed_assertion(session, airport, source, raw_relevant_text=f"{_ALIAS} EMAS project.")
        with pytest.raises(NotIndependentSourceError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_same_source_assertion_refused():
    """The admitting evidence's own SourceAssertion used AS the
    being-attested assertion - the most direct form of self-confirmation."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport(session)
        source = _seed_source(session)
        admitting_assertion = _seed_assertion(session, airport, source, raw_relevant_text=_ANCHOR_EXCERPT, identity_guard_decision="ATTACH_PROVISIONAL")
        alias = _admit_alias(session, airport, source, admitting_assertion)
        with pytest.raises(NotIndependentSourceError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=admitting_assertion.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )


def test_independent_source_accepted():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        assert alias.source_id != being_attested.source_id
        result = record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        assert result.attestation_id is not None


# ---------------------------------------------------------------------------
# DUPLICATE ATTESTATION
# ---------------------------------------------------------------------------

def test_duplicate_attestation_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="first",
        )
        with pytest.raises(DuplicateAttestationError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="second",
            )


# ---------------------------------------------------------------------------
# EMPTY FIELDS
# ---------------------------------------------------------------------------

def test_empty_analyst_and_reason_refused():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        with pytest.raises(EmptyAnalystError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="  ", reason="reason",
            )
        with pytest.raises(EmptyReasonError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="  ",
            )


# ---------------------------------------------------------------------------
# ATOMICITY
# ---------------------------------------------------------------------------

def test_failed_eligibility_leaves_no_row():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.identity_guard_decision = "INSUFFICIENT_IDENTITY"
        session.flush()
        with pytest.raises(RawDecisionNotEligibleError):
            record_cross_source_alias_attestation(
                session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
                analyst="human:tester", reason="reason",
            )
        assert get_latest_cross_source_alias_attestation(session, being_attested.id) is None


def test_rollback_removes_inserted_attestation():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.rollback()
        assert get_latest_cross_source_alias_attestation(session, being_attested.id) is None


def test_no_raw_decision_mutation():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.commit()
        refreshed = session.get(SourceAssertion, being_attested.id)
        assert refreshed.identity_guard_decision == "ATTACH_PROVISIONAL"


# ---------------------------------------------------------------------------
# PREVIEW
# ---------------------------------------------------------------------------

def test_preview_never_writes_and_matches_eligibility():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        preview = preview_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
        )
        assert preview.eligible is True
        assert preview.literal_match is True
        assert preview.alias_currently_unique is True
        assert preview.source_independent is True
        assert preview.predicted_effective_decision == "ATTACH_CONFIRMED"
        assert preview.predicted_effective_basis == "CROSS_SOURCE_ALIAS_ATTESTATION"
        assert get_latest_cross_source_alias_attestation(session, being_attested.id) is None


def test_preview_reports_refusal_reason_without_raising():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.identity_guard_decision = "REJECT_CROSS_AIRPORT"
        session.flush()
        preview = preview_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
        )
        assert preview.eligible is False
        assert preview.refusal_reason is not None
        assert preview.predicted_effective_decision == preview.current_effective_decision


# ---------------------------------------------------------------------------
# EB5 INTEGRATION
# ---------------------------------------------------------------------------

def test_eb5_effective_confirmed_raw_stays_provisional():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        before = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert before.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL
        assert before.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION

        record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.commit()

        after = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert after.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
        assert after.basis == EffectiveIdentityGuardDecisionBasis.CROSS_SOURCE_ALIAS_ATTESTATION
        assert after.original_decision == AttachmentOutcome.ATTACH_PROVISIONAL

        refreshed = session.get(SourceAssertion, being_attested.id)
        assert refreshed.identity_guard_decision == "ATTACH_PROVISIONAL"


def test_eb5_stale_attestation_after_alias_retired_does_not_apply():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.commit()
        after = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert after.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED

        record_airport_alias(
            session, airport_id=airport.id, source_id=source_a.id, source_assertion_id=admitting_assertion.id,
            alias=_ALIAS, evidence_excerpt=_ANCHOR_EXCERPT, analyst="human:tester",
            status="RETIRED", supersedes_alias_id=alias.id,
        )
        session.commit()

        after_retirement = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert after_retirement.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL
        assert after_retirement.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION


def test_eb5_stale_attestation_after_ambiguity_introduced_does_not_apply():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        record_cross_source_alias_attestation(
            session, source_assertion_id=being_attested.id, matched_alias_id=alias.id,
            analyst="human:tester", reason="reason",
        )
        session.commit()
        assert is_cross_source_alias_attestation_current(
            session, get_latest_cross_source_alias_attestation(session, being_attested.id)
        )

        other_airport = _seed_airport(session, name="Other Airport")
        other_source = _seed_source(session)
        other_admitting = _seed_assertion(
            session, other_airport, other_source,
            raw_relevant_text=f"{_ALIAS}(Other Airport) unrelated.", identity_guard_decision=None,
        )
        record_airport_alias(
            session, airport_id=other_airport.id, source_id=other_source.id, source_assertion_id=other_admitting.id,
            alias=_ALIAS, evidence_excerpt=f"{_ALIAS}(Other Airport) unrelated.", analyst="human:tester",
        )
        session.commit()

        after = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert after.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL
        assert after.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION


def test_eb5_never_overrides_already_confirmed():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.identity_guard_decision = "ATTACH_CONFIRMED"
        session.flush()
        result = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert result.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION


def test_eb5_never_overrides_reject():
    engine = _engine()
    with Session(engine) as session:
        airport, source_a, admitting_assertion, alias, source_b, being_attested = _standard_setup(session)
        being_attested.identity_guard_decision = "REJECT_CROSS_AIRPORT"
        session.flush()
        result = resolve_effective_identity_guard_decision(session, source_assertion_id=being_attested.id)
        assert result.effective_decision == AttachmentOutcome.REJECT_CROSS_AIRPORT
        assert result.basis == EffectiveIdentityGuardDecisionBasis.ORIGINAL_DECISION
