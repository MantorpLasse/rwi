"""Tests for "RWI - Mission #7J" (Explainable Attention Triggers): the
deterministic, presentation-only "Varför nu?" attention-reason line added to
the existing Marknadsläge page (Mission #7C), approved by Mission #7I's own
explainability contract.

Every reason traces to exactly one existing field - Signal.status,
Signal.confirmed_vendor, Source.source_type + Signal.planning_year, or
Signal.category - never probability_score, confidence, likely_supplier,
category alone, a bare year, stale_unresolved alone, an airport-level
count, or FH-D4 governance text (Mission #7I's own "explicitly excluded"
list, reproduced as this mission's own invariants). No LLM-generated
runtime prose; every string below is a fixed constant from
app.static_export.build.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_engine`, `_seed_bos_shaped`) are
imported from tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import Airport, Signal, Source
from app.services.signal_disposition_persistence import record_signal_group_disposition
from app.static_export import build_site
from app.static_export.build import _attention_reason_view
from app.static_export.signal_lifecycle import derive_signal_lifecycle
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped

_TODAY = date(2026, 8, 31)


def _airport(session: Session, name: str = "Test Airport", iata: str = "TST") -> Airport:
    airport = Airport(name=name, iata_code=iata, country="USA")
    session.add(airport)
    session.commit()
    return airport


def _signal(session: Session, airport: Airport, **kwargs) -> Signal:
    defaults = dict(
        title="Test signal", category="replacement", confidence="high",
        status="identified", published=True,
    )
    defaults.update(kwargs)
    signal = Signal(airport_id=airport.id, **defaults)
    session.add(signal)
    session.commit()
    return signal


def _reason(signal: Signal, *, today: date = _TODAY) -> str | None:
    """Exercises the real production call path: derive lifecycle first
    (same as _signal_view() does), then feed its state into the reason
    helper - never calls the helper with a hand-picked lifecycle state."""
    lifecycle = derive_signal_lifecycle(signal, today=today)
    return _attention_reason_view(signal, today=today, lifecycle_state=lifecycle.state)


# ---------------------------------------------------------------------
# One test per approved trigger (Mission #7I's own contract, Part H)
# ---------------------------------------------------------------------

def test_procurement_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="procurement", confidence="confirmed")
        assert _reason(signal) == "Upphandling pågår"


def test_construction_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)  # real BOS #3 shape: status="under construction"
        signal = session.query(Signal).filter(Signal.airport_id == airport.id).one()
        assert _reason(signal) == "Byggnation pågår"


def test_design_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="design")
        assert _reason(signal) == "Projektering pågår"


def test_master_plan_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="master_plan")
        assert _reason(signal) == "Master Plan-fas"


def test_environmental_review_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="environmental_review")
        assert _reason(signal) == "Miljöprövning pågår"


def test_cip_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="cip")
        assert _reason(signal) == "CIP-planering pågår"


def test_alp_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="alp")
        assert _reason(signal) == "ALP-planering pågår"


def test_funded_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="funded")
        assert _reason(signal) == "Finansiering beviljad"


def test_federal_grant_current_fiscal_year_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        source = Source(title="Test grant record", source_type="usaspending_grant")
        session.add(source)
        session.commit()
        signal = _signal(
            session, airport, source_id=source.id, planning_year=2026,  # today.year, delta=0
            category="new_installation",
        )
        assert _reason(signal) == "Aktuellt federalt finansieringsunderlag finns"


def test_federal_grant_past_fiscal_year_does_not_trigger():
    """Mission #7I §9: only current/future fiscal year counts - the exact
    same test derive_signal_lifecycle's own grant branch already applies,
    never a second definition of "current"."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        source = Source(title="Test grant record", source_type="usaspending_grant")
        session.add(source)
        session.commit()
        signal = _signal(session, airport, source_id=source.id, planning_year=2023)  # 3y ago
        assert _reason(signal) is None


def test_confirmed_vendor_trigger():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, confirmed_vendor="Runway Safe", status=None)
        assert _reason(signal) == "Runway Safe bekräftad som leverantör"


def test_incident_trigger_within_research_window():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(
            session, airport, category="replacement_after_incident", confidence="high",
            title="Test Airport — EMAS-ersättning väntas efter incident (2024-07-01)",
        )
        lifecycle = derive_signal_lifecycle(signal, today=_TODAY)
        assert lifecycle.state.value == "developing_watch"  # ~2.2y old, still within window
        assert _reason(signal) == "En incident har registrerats, ersättning inte bekräftad"


# ---------------------------------------------------------------------
# None / no-trigger behavior
# ---------------------------------------------------------------------

def test_no_trigger_returns_none():
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(session, airport, status="identified", category="replacement")
        assert _reason(signal) is None


def test_likely_supplier_alone_never_triggers_a_reason():
    """Mission #7I's own exclusion: likely_supplier is analyst judgment, not
    a confirmed fact - must never be read by the trigger function at all."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(
            session, airport, status="identified", category="replacement",
            likely_supplier="Runway Safe",
        )
        assert _reason(signal) is None


def test_stale_unresolved_incident_signal_does_not_fabricate_a_reason():
    """Mission #7I/#7J invariant: stale_unresolved != research priority. An
    18-year-old incident-derived Signal still has
    category=="replacement_after_incident" - the same field the active
    incident trigger reads - but must not surface a reason once it has
    aged past the research window."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(
            session, airport, category="replacement_after_incident", confidence="high",
            title="Test Airport — EMAS-ersättning väntas efter incident (2008-07-01)",
        )
        lifecycle = derive_signal_lifecycle(signal, today=_TODAY)
        assert lifecycle.state.value == "stale_unresolved"
        assert _reason(signal) is None


def test_score_and_confidence_never_read_by_trigger_function():
    """Mission #7I's core invariant: Score != Attention. A Signal with a
    very high Score/confidence but no approved trigger still gets None."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(
            session, airport, status="identified", category="replacement",
            confidence="confirmed", probability_score=10.0,
        )
        assert _reason(signal) is None


def test_confirmed_vendor_reason_fires_with_score_none():
    """The mission's own central proof case (real Signal #67 shape):
    confirmed_vendor fires its reason entirely independent of Score, which
    is None here."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        signal = _signal(
            session, airport, confirmed_vendor="Runway Safe", status=None,
            probability_score=None, confidence="high",
        )
        assert signal.probability_score is None
        assert _reason(signal) == "Runway Safe bekräftad som leverantör"


# ---------------------------------------------------------------------
# Marknadsläge integration / end-to-end
# ---------------------------------------------------------------------

def test_marknadslage_shows_varfor_nu_text_for_a_firing_reason(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(session, airport, status="procurement", confidence="confirmed", probability_score=10.0)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "Varför nu? Upphandling pågår" in html
    assert 'class="attention-reason"' in html


def test_marknadslage_renders_cleanly_with_no_reason_no_placeholder(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(session, airport, status="identified", category="replacement")
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "Varför nu?" not in html
    assert 'class="attention-reason"' not in html


def test_marknadslage_has_no_new_column_header(tmp_path):
    """Design decision: the reason is a full-width subtitle row (colspan),
    not an 11th <th> column - locks that decision in against an accidental
    future regression toward a new column."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(session, airport, status="procurement")
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "<th>Varför nu?</th>" not in html
    assert 'colspan="10"' in html


def test_mht_shaped_signals_never_imply_grant_funds_procurement(tmp_path):
    """MHT deep case (Mission #7I/#7J): #2-shaped (procurement) and
    #45-shaped (current-FY grant) at the same airport, no FH-D4 relationship
    between them - each must carry only its own reason, never a claim that
    one funds the other."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session, name="Test MHT", iata="TMH")
        procurement_signal = _signal(
            session, airport, title="Runway 6 test EMAS replacement", status="procurement",
            confidence="confirmed", probability_score=10.0, category="replacement",
        )
        grant_source = Source(title="Test USAspending grant record", source_type="usaspending_grant")
        session.add(grant_source)
        session.commit()
        grant_signal = _signal(
            session, airport, title="Test USAspending grant — FY2026", source_id=grant_source.id,
            planning_year=2026, probability_score=8.0, category="replacement",
        )
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "Varför nu? Upphandling pågår" in html
    assert "Varför nu? Aktuellt federalt finansieringsunderlag finns" in html
    # No wording anywhere claims a funding/support relationship between the
    # two Signals.
    for forbidden in ("finansierar", "stödjer", "funds", "supports"):
        assert forbidden not in html.lower()


def test_no_fh_d4_governance_leakage_in_attention_reasons(tmp_path):
    """BOS deep case: a real SAME_REAL_WORLD_EFFORT disposition must never
    surface its own internal governance terminology through the new
    attention-reason text."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session, name="Test BOS", iata="TBS")
        s1 = _signal(session, airport, title="Grant A", status="identified", category="new_installation")
        s2 = _signal(session, airport, title="Grant B", status="identified", category="new_installation")
        record_signal_group_disposition(
            session, signal_ids=[s1.id, s2.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:test", reason="test - same real-world project, sequential phases",
        )
        session.commit()
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    for forbidden in ("SignalDisposition", "SAME_REAL_WORLD_EFFORT", "DISTINCT", "disposition", "human:test"):
        assert forbidden not in html


def test_no_change_to_one_signal_per_row_grouping_semantics(tmp_path):
    """Mission #7E's own established contract must remain untouched: no
    grouprow/grouptitle/detail-panel markup reappears because of this
    mission's own new markup."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(session, airport, status="procurement")
        _signal(session, airport, status="design")
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert 'class="grouprow"' not in html
    assert 'class="grouptitle"' not in html
    assert "detail-panel" not in html


def test_active_opportunities_sort_order_unaffected_by_attention_reason(tmp_path):
    """No change to sorting/ranking: order must still follow
    _signal_sort_key (score descending within the active tier), regardless
    of which rows have a reason and which don't."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(
            session, airport, title="Low score, has a reason", status="procurement",
            confidence="confirmed", probability_score=6.0,
        )
        _signal(
            session, airport, title="High score, no reason", status="identified",
            category="replacement", confidence="confirmed", probability_score=10.0,
            planning_year=2026,  # explicit future/current year alone -> active_opportunity too
        )
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert html.index("High score, no reason") < html.index("Low score, has a reason")


def test_attention_reason_does_not_leak_into_signals_list_page(tmp_path):
    """Scope discipline: this mission integrates Marknadsläge only - the
    shared signal_row() macro used by /signals/index.html is untouched."""
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(session, airport, status="procurement")
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    assert "Varför nu?" not in html
    assert "attention-row" not in html


def test_attention_reason_does_not_leak_into_airport_detail_page(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _airport(session)
        _signal(session, airport, status="procurement")
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Varför nu?" not in html
    assert "attention-row" not in html
