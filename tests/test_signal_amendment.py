"""RWI HQ "Trust Preconditions for Update & Report V1" mission, Part 3 -
tests for app/services/signal_amendment.py."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, ReviewerAction, Signal, Source, SourceAssertion
from app.models.signal_publication_action import SignalPublicationAction
from app.services.signal_amendment import (
    ALLOWED_AMENDMENT_FIELDS,
    SignalAmendmentError,
    amend_signal,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_signal(session, **overrides) -> Signal:
    airport = overrides.pop("airport", None)
    if airport is None:
        airport = Airport(name="Greater Binghamton Airport", iata_code="BGM", country="USA")
        session.add(airport)
        session.flush()
    defaults = dict(
        airport=airport, title="Runway 16 departure EMAS project", category="new_installation",
        confidence="programmed", status="funded",
    )
    defaults.update(overrides)
    signal = Signal(**defaults)
    session.add(signal)
    session.commit()
    return signal


# 1/2. An allowed field can be amended; old/new value is returned.
def test_allowed_field_can_be_amended_and_returns_old_new_value():
    session = make_session()
    signal = make_signal(session, target_year=2028)

    result = amend_signal(
        session, signal_id=signal.id, changes={"target_year": 2026},
        reason="AMPU Table 8-1 assigns no EMAS-specific year; 2028 is only a shared capital-program bucket boundary.",
        reviewer="reviewer@example.test",
    )
    session.commit()

    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.field == "target_year"
    assert change.old_value == 2028
    assert change.new_value == 2026
    assert signal.target_year == 2026
    assert result.signal_id == signal.id
    assert result.reviewer == "reviewer@example.test"


# 3. reason is required.
def test_reason_required():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    with pytest.raises(SignalAmendmentError, match="reason is required"):
        amend_signal(session, signal_id=signal.id, changes={"target_year": 2026}, reason="   ", reviewer="r@example.test")


def test_reviewer_required():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    with pytest.raises(SignalAmendmentError, match="reviewer is required"):
        amend_signal(session, signal_id=signal.id, changes={"target_year": 2026}, reason="a real reason", reviewer="  ")


# 4. No-op amendment rejected.
def test_no_op_amendment_rejected():
    session = make_session()
    signal = make_signal(session, target_year=2028)
    with pytest.raises(SignalAmendmentError, match="no-op amendment"):
        amend_signal(session, signal_id=signal.id, changes={"target_year": 2028}, reason="a real reason", reviewer="r@example.test")


def test_empty_changes_rejected():
    session = make_session()
    signal = make_signal(session)
    with pytest.raises(SignalAmendmentError, match="non-empty mapping"):
        amend_signal(session, signal_id=signal.id, changes={}, reason="a real reason", reviewer="r@example.test")


# 5. Disallowed (unknown) field rejected.
def test_disallowed_unknown_field_rejected():
    session = make_session()
    signal = make_signal(session)
    with pytest.raises(SignalAmendmentError, match="not in the allowed amendment field set"):
        amend_signal(session, signal_id=signal.id, changes={"notes": "sneaky"}, reason="a real reason", reviewer="r@example.test")


# 6. published cannot be changed.
def test_published_cannot_be_changed():
    session = make_session()
    signal = make_signal(session)
    with pytest.raises(SignalAmendmentError, match="signal_publication"):
        amend_signal(session, signal_id=signal.id, changes={"published": True}, reason="a real reason", reviewer="r@example.test")
    assert "published" not in ALLOWED_AMENDMENT_FIELDS


# 7. airport_id cannot be changed unless explicitly approved by current design.
def test_airport_id_cannot_be_changed():
    session = make_session()
    signal = make_signal(session)
    with pytest.raises(SignalAmendmentError, match="identity-changing"):
        amend_signal(session, signal_id=signal.id, changes={"airport_id": 999}, reason="a real reason", reviewer="r@example.test")
    assert "airport_id" not in ALLOWED_AMENDMENT_FIELDS


# 8. category cannot be changed unless explicitly approved by current design.
def test_category_cannot_be_changed():
    session = make_session()
    signal = make_signal(session)
    with pytest.raises(SignalAmendmentError, match="identity-changing"):
        amend_signal(session, signal_id=signal.id, changes={"category": "replacement"}, reason="a real reason", reviewer="r@example.test")
    assert "category" not in ALLOWED_AMENDMENT_FIELDS


def test_installation_id_confidence_probability_score_also_disallowed():
    session = make_session()
    signal = make_signal(session)
    for field, value in (("installation_id", 1), ("confidence", "high"), ("probability_score", 9.0)):
        with pytest.raises(SignalAmendmentError):
            amend_signal(session, signal_id=signal.id, changes={field: value}, reason="a real reason", reviewer="r@example.test")


# 9. Supporting SourceAssertion is validated (must exist).
def test_supporting_source_assertion_must_exist():
    session = make_session()
    signal = make_signal(session)
    with pytest.raises(SignalAmendmentError, match="does not exist"):
        amend_signal(
            session, signal_id=signal.id, changes={"target_year": 2027}, reason="a real reason",
            reviewer="r@example.test", source_assertion_id=999999,
        )


def test_supporting_source_assertion_when_valid_is_recorded():
    session = make_session()
    signal = make_signal(session)
    source = Source(title="AMPU", source_type="master_plan")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source=source, airport_id=signal.airport_id, assertion_type="project_construction",
        source_record_identifier="ampu:table-8-1",
    )
    session.add(assertion)
    session.commit()

    result = amend_signal(
        session, signal_id=signal.id, changes={"target_year": 2027}, reason="AMPU cross-reference",
        reviewer="r@example.test", source_assertion_id=assertion.id,
    )
    session.commit()
    assert result.source_assertion_id == assertion.id
    assert f"SourceAssertion #{assertion.id}" in signal.source_notes


# 10/11. Incompatible provenance (different airport) fails closed - no
# transitive identity inference (same-airport is checked structurally,
# never inferred from anything else).
def test_incompatible_source_assertion_airport_fails_closed():
    session = make_session()
    signal = make_signal(session)  # BGM
    other_airport = Airport(name="Other Airport", iata_code="OTH", country="USA")
    session.add(other_airport)
    session.flush()
    source = Source(title="Unrelated document", source_type="news")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source=source, airport_id=other_airport.id, assertion_type="project_construction",
        source_record_identifier="unrelated:doc:1",
    )
    session.add(assertion)
    session.commit()

    with pytest.raises(SignalAmendmentError, match="incompatible provenance"):
        amend_signal(
            session, signal_id=signal.id, changes={"target_year": 2027}, reason="a real reason",
            reviewer="r@example.test", source_assertion_id=assertion.id,
        )


def test_incompatible_runway_airport_fails_closed_no_transitive_inference():
    session = make_session()
    signal = make_signal(session)  # BGM
    other_airport = Airport(name="Other Airport", iata_code="OTH", country="USA")
    session.add(other_airport)
    session.flush()
    other_runway = Runway(airport=other_airport, designation="09/27")
    session.add(other_runway)
    session.commit()

    with pytest.raises(SignalAmendmentError, match="no transitive identity inference"):
        amend_signal(
            session, signal_id=signal.id, changes={"runway_id": other_runway.id}, reason="a real reason",
            reviewer="r@example.test",
        )


def test_reviewer_action_must_match_supplied_source_assertion():
    session = make_session()
    signal = make_signal(session)
    source = Source(title="AMPU", source_type="master_plan")
    session.add(source)
    session.flush()
    assertion_a = SourceAssertion(
        source=source, airport_id=signal.airport_id, assertion_type="project_construction",
        source_record_identifier="ampu:table-8-1:a",
    )
    assertion_b = SourceAssertion(
        source=source, airport_id=signal.airport_id, assertion_type="project_construction",
        source_record_identifier="ampu:table-8-1:b",
    )
    session.add_all([assertion_a, assertion_b])
    session.commit()
    action = ReviewerAction(source_assertion_id=assertion_b.id, action="NEEDS_MORE_EVIDENCE", reason="x", reviewer="r@example.test")
    session.add(action)
    session.commit()

    with pytest.raises(SignalAmendmentError, match="not the supplied source_assertion_id"):
        amend_signal(
            session, signal_id=signal.id, changes={"target_year": 2027}, reason="a real reason",
            reviewer="r@example.test", source_assertion_id=assertion_a.id, reviewer_action_id=action.id,
        )


# 12. Service preserves unrelated Signal fields.
def test_unrelated_fields_preserved():
    session = make_session()
    signal = make_signal(session, target_year=2028, supplier="Runway Safe", confidence="programmed")
    original_title = signal.title
    original_confidence = signal.confidence
    original_category = signal.category

    amend_signal(session, signal_id=signal.id, changes={"target_year": 2026}, reason="a real reason", reviewer="r@example.test")
    session.commit()

    assert signal.title == original_title
    assert signal.confidence == original_confidence
    assert signal.category == original_category
    assert signal.supplier == "Runway Safe"


# 13. Transaction rollback works on validation failure - nothing is
# flushed/changed before a SignalAmendmentError is raised.
def test_rollback_on_validation_failure_leaves_nothing_changed():
    session = make_session()
    signal = make_signal(session, target_year=2028)

    with pytest.raises(SignalAmendmentError):
        amend_signal(session, signal_id=signal.id, changes={"category": "replacement"}, reason="a real reason", reviewer="r@example.test")

    assert not session.dirty
    session.rollback()
    refreshed = session.get(Signal, signal.id)
    assert refreshed.target_year == 2028
    assert refreshed.category == "new_installation"


# 14. The existing publication service remains the only publication write
# path - amend_signal() never touches Signal.published, in either
# direction, even indirectly.
def test_publication_state_never_touched():
    session = make_session()
    signal = make_signal(session)
    assert signal.published is True  # global default, untouched by this mission

    amend_signal(session, signal_id=signal.id, changes={"target_year": 2027}, reason="a real reason", reviewer="r@example.test")
    session.commit()
    assert signal.published is True

    session.query(Signal).filter(Signal.id == signal.id).update({"published": False})
    session.commit()
    amend_signal(session, signal_id=signal.id, changes={"target_year": 2028}, reason="another real reason", reviewer="r@example.test")
    session.commit()
    refreshed = session.get(Signal, signal.id)
    assert refreshed.published is False  # untouched either way


# 15. No new audit semantics invented silently - no ReviewerAction, no
# SignalPublicationAction row is ever created by this service.
def test_no_new_audit_semantics_invented_silently():
    session = make_session()
    signal = make_signal(session, target_year=2028)

    amend_signal(session, signal_id=signal.id, changes={"target_year": 2026}, reason="a real reason", reviewer="r@example.test")
    session.commit()

    assert session.scalars(select(ReviewerAction)).all() == []
    assert session.scalars(select(SignalPublicationAction)).all() == []
    # The only trace is the returned result object plus the documented,
    # explicitly-non-audit source_notes line.
    assert signal.source_notes is not None and "Amended by" in signal.source_notes


def test_amend_nonexistent_signal_fails_closed():
    session = make_session()
    with pytest.raises(SignalAmendmentError, match="does not exist"):
        amend_signal(session, signal_id=999999, changes={"target_year": 2027}, reason="a real reason", reviewer="r@example.test")


def test_source_notes_appends_rather_than_overwrites():
    session = make_session()
    signal = make_signal(session, target_year=2028, source_notes="Original research finding from AMPU.")

    amend_signal(session, signal_id=signal.id, changes={"target_year": 2026}, reason="a real reason", reviewer="r@example.test")
    session.commit()

    assert signal.source_notes.startswith("Original research finding from AMPU.\n")
    assert "Amended by" in signal.source_notes


def test_construction_and_completion_date_fields_amendable():
    session = make_session()
    signal = make_signal(session)
    result = amend_signal(
        session, signal_id=signal.id,
        changes={"construction_start": date(2026, 6, 1), "completion_date": date(2027, 3, 1)},
        reason="Construction confirmed by AMPU update", reviewer="r@example.test",
    )
    session.commit()
    assert {c.field for c in result.changes} == {"construction_start", "completion_date"}
    assert signal.construction_start == date(2026, 6, 1)
    assert signal.completion_date == date(2027, 3, 1)
