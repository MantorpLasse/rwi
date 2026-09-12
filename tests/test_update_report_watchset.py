"""RWI HQ "Update & Report V1" mission - tests for
app/services/update_report_watchset.py (Part 16, WATCH SET items 1-3)."""
from __future__ import annotations

import itertools

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.services.update_report_watchset import (
    WATCH_REASON_NEEDS_MORE_EVIDENCE,
    WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION,
    WATCH_REASON_UNPUBLISHED_SIGNAL,
    plan_watch_set,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_airport(session, **overrides) -> Airport:
    defaults = dict(name="Test Airport", iata_code="TST", country="USA")
    defaults.update(overrides)
    airport = Airport(**defaults)
    session.add(airport)
    session.flush()
    return airport


def make_source(session, **overrides) -> Source:
    defaults = dict(title="Test Source", source_type="news", reliability_level="official", url="https://example.test/a")
    defaults.update(overrides)
    source = Source(**defaults)
    session.add(source)
    session.flush()
    return source


def make_signal(session, airport, **overrides) -> Signal:
    defaults = dict(airport=airport, title="Test Signal", category="new_installation", confidence="medium", status="funded")
    defaults.update(overrides)
    signal = Signal(**defaults)
    session.add(signal)
    session.flush()
    return signal


_assertion_seq = itertools.count(1)


def make_assertion(session, source, **overrides) -> SourceAssertion:
    defaults = dict(
        source=source, assertion_type="project_construction",
        source_record_identifier=f"rec-{next(_assertion_seq)}",
        raw_relevant_text="Some evidence text.",
    )
    defaults.update(overrides)
    assertion = SourceAssertion(**defaults)
    session.add(assertion)
    session.flush()
    return assertion


def test_bounded_watch_set_not_all_airports():
    session = make_session()
    airport = make_airport(session)
    for other in range(5):
        make_airport(session, name=f"Other Airport {other}", iata_code=f"O{other}")
    make_signal(session, airport, published=False)

    watch_items = plan_watch_set(session)

    assert len(watch_items) == 1
    assert watch_items[0].airport_id == airport.id


def test_every_watch_item_has_an_explicit_reason_and_detail():
    session = make_session()
    airport = make_airport(session)
    make_signal(session, airport, published=False, target_year=2028)

    watch_items = plan_watch_set(session)

    assert len(watch_items) == 1
    item = watch_items[0]
    assert item.watch_reason == WATCH_REASON_UNPUBLISHED_SIGNAL
    assert item.detail
    assert "2028" in item.detail


def test_staged_evidence_needing_attention_is_watched():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)

    watch_items = plan_watch_set(session)

    assert len(watch_items) == 1
    item = watch_items[0]
    assert item.watch_reason == WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION
    assert item.source_assertion_id == assertion.id
    assert item.signal_id is None


def test_needs_more_evidence_state_is_labeled_distinctly():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    assertion = make_assertion(session, source, airport_id=airport.id)
    session.add(
        ReviewerAction(
            source_assertion=assertion, action="NEEDS_MORE_EVIDENCE", reason="need more", reviewer="tester",
        )
    )
    session.commit()

    watch_items = plan_watch_set(session)

    assert len(watch_items) == 1
    assert watch_items[0].watch_reason == WATCH_REASON_NEEDS_MORE_EVIDENCE


def test_structural_airport_inventory_evidence_is_not_watched():
    session = make_session()
    airport = make_airport(session)
    source = make_source(session)
    make_assertion(session, source, airport_id=airport.id, assertion_type="airport_inventory")

    watch_items = plan_watch_set(session)

    assert watch_items == ()


def test_staged_evidence_with_no_airport_is_not_watched():
    session = make_session()
    source = make_source(session)
    make_assertion(session, source, airport_id=None, source_locator="loc-1", raw_fragment_hash="hash-1", source_record_identifier=None)

    watch_items = plan_watch_set(session)

    assert watch_items == ()


def test_duplicate_watch_items_are_deduplicated():
    session = make_session()
    airport = make_airport(session)
    signal = make_signal(session, airport, published=False)

    watch_items = plan_watch_set(session)
    watch_items_again = plan_watch_set(session)

    assert len(watch_items) == 1
    assert watch_items == watch_items_again


def test_limit_bounds_the_watch_set():
    session = make_session()
    for i in range(5):
        airport = make_airport(session, name=f"Airport {i}", iata_code=f"A{i}")
        make_signal(session, airport, published=False)

    watch_items = plan_watch_set(session, limit=2)

    assert len(watch_items) == 2


def test_published_signal_is_not_watched():
    session = make_session()
    airport = make_airport(session)
    make_signal(session, airport, published=True)

    watch_items = plan_watch_set(session)

    assert watch_items == ()
