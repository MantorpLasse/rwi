"""RWI HQ "Trust Preconditions for Update & Report V1" mission, Part 2 -
tests for app/services/signal_provenance_classification.py.

Every test uses an isolated in-memory SQLite database. Fixture pattern
for a real governed Signal (`_msp_222_shape`/`_approve`/`_MSP_FIELDS`)
mirrors tests/test_governed_signal_creation.py's own established
convention exactly, rather than inventing a second one.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Incident, Signal, Source, SourceAssertion
from app.services.governed_signal_creation import create_signal_from_approved_review
from app.services.reviewer_action_persistence import record_reviewer_action
from app.services.signal_provenance_classification import (
    SignalProvenanceGeneration,
    classify_signal_provenance,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _msp_222_shape(session) -> SourceAssertion:
    airport = Airport(name="Minneapolis St. Paul International", iata_code="MSP", country="USA")
    source = Source(title="EMAS Procurement Advance Deposit memo", source_type="web_discovery")
    assertion = SourceAssertion(
        source=source, airport=airport, assertion_type="project_construction",
        source_record_identifier="mac.granicus.document.4.2349.105406",
        identity_guard_decision="ATTACH_CONFIRMED",
        intelligence_review_decision="REVIEW_REQUIRED",
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )
    session.add_all([airport, source, assertion])
    session.commit()
    return assertion


def _approve(session, assertion):
    action = record_reviewer_action(
        session, assertion, action="APPROVE_SIGNAL",
        reason="Deposit PO + CIP ceiling both concern the same 30L EMAS replacement.",
        reviewer="reviewer@example.test",
    )
    session.commit()
    return action


_MSP_FIELDS = dict(
    title="MSP Runway 30L EMAS replacement - advance deposit requested",
    category="replacement", confidence="medium", status="identified", likely_supplier="Runway Safe",
)


# 1. Signal with a governed SourceAssertion path classifies MODERN_GOVERNED.
def test_governed_signal_classifies_modern_governed():
    session = make_session()
    assertion = _msp_222_shape(session)
    _approve(session, assertion)
    result = create_signal_from_approved_review(session, assertion, **_MSP_FIELDS)
    session.commit()

    classification = classify_signal_provenance(session, result.signal)
    assert classification.generation == SignalProvenanceGeneration.MODERN_GOVERNED
    assert classification.description == "modern governed promotion path"
    assert classification.signal_id == result.signal.id
    assert any("linked SourceAssertion" in e for e in classification.evidence)
    assert any("APPROVE_SIGNAL" in e for e in classification.evidence)


# 2. Incident-created Signal classifies INCIDENT_RULE.
def test_incident_created_signal_classifies_incident_rule():
    session = make_session()
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    session.add(Incident(airport=airport, incident_date=date(2026, 3, 1), incident_type="overrun"))
    session.commit()

    signal = session.query(Signal).filter(Signal.airport_id == airport.id).one()
    classification = classify_signal_provenance(session, signal)
    assert classification.generation == SignalProvenanceGeneration.INCIDENT_RULE
    assert classification.description == "incident rule-created"
    assert any("replacement_after_incident" in e for e in classification.evidence)


# 3. Legacy/manual shape (real Source citation, no SourceAssertion, not the
# incident category) classifies LEGACY_SCRIPT_OR_MANUAL.
def test_legacy_manual_signal_classifies_appropriately():
    session = make_session()
    airport = Airport(name="Greater Binghamton Airport", iata_code="BGM", country="USA")
    source = Source(title="USAspending award", source_type="usaspending_grant", reliability_level="official")
    session.add_all([airport, source])
    session.flush()
    signal = Signal(
        airport=airport, source=source, title="USAspending grant - $5.4M, FY2023",
        category="replacement", confidence="high", estimated_total_value_usd=5402598.30,
    )
    session.add(signal)
    session.commit()

    classification = classify_signal_provenance(session, signal)
    assert classification.generation == SignalProvenanceGeneration.LEGACY_SCRIPT_OR_MANUAL
    assert classification.description == "legacy/manual path"
    assert f"Source #{source.id}" in classification.evidence[0]


# 4. The classifier never calls a legacy result "untrusted" (or any other
# evaluative/quality verdict) anywhere in its output text.
def test_classifier_never_calls_legacy_untrusted():
    session = make_session()
    airport = Airport(name="Greater Binghamton Airport", iata_code="BGM", country="USA")
    source = Source(title="USAspending award", source_type="usaspending_grant", reliability_level="official")
    session.add_all([airport, source])
    session.flush()
    signal = Signal(airport=airport, source=source, title="Legacy signal", category="replacement", confidence="high")
    session.add(signal)
    session.commit()

    classification = classify_signal_provenance(session, signal)
    forbidden = ("untrusted", "unreliable", "bad", "invalid", "wrong")
    haystack = " ".join([classification.description, *classification.evidence]).lower()
    for word in forbidden:
        assert word not in haystack


# 5. Ambiguous case (no linked SourceAssertion, not the incident category,
# no Source citation at all) fails safe to UNKNOWN rather than guessing.
def test_ambiguous_signal_returns_unknown():
    session = make_session()
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(airport=airport, title="Manually added signal, no source", category="unknown", confidence="low")
    session.add(signal)
    session.commit()

    classification = classify_signal_provenance(session, signal)
    assert classification.generation == SignalProvenanceGeneration.UNKNOWN
    assert "fails safe" in classification.evidence[0]


# 6. Zero persistence: calling the classifier makes no DB write at all.
def test_classifier_makes_no_database_write():
    session = make_session()
    airport = Airport(name="Test Airport", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(airport=airport, title="Some signal", category="unknown", confidence="low")
    session.add(signal)
    session.commit()

    assert not session.new and not session.dirty and not session.deleted
    classify_signal_provenance(session, signal)
    classify_signal_provenance(session, signal)  # repeatable, still no writes
    assert not session.new
    assert not session.dirty
    assert not session.deleted
