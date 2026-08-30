"""Tests for "RWI - Juicy Design Mission #1" (Overview V2 + BOS Airport
Detail V2): the new Overview metrics/market-summary, the airport-level
"project summary" presentation hierarchy, and the required Sacheon/BOS
regression proofs.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db and never the real Signal69/BOS Signal #3 (which
must remain untouched by this mission). "BOS-shaped"/"Sacheon-shaped"
fixtures mirror the real rows' own shape without asserting anything about
the real business data itself, matching the established pattern in
tests/test_static_export_publication_regression.py and
tests/test_static_export_evidence.py.
"""
from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.services.airport_alias import record_airport_alias
from app.services.cross_source_alias_attestation import record_cross_source_alias_attestation
from app.services.governed_signal_creation import create_signal_from_approved_review
from app.services.manual_claim_evidence import record_manual_claim_evidence
from app.services.reviewer_action_persistence import record_reviewer_action
from app.static_export import build_site

_KOREAN_ALIAS_EXCERPT = "테스트공항(Test Airport) official."
_KOREAN_COUNCIL_EXCERPT = "테스트공항 EMAS project confirmed. Budget secured 2025년 2026년."
_SOURCE_URL = "https://council.example.test/record"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_bos_shaped(session, *, published=True) -> Airport:
    """A US airport with one dominant "under construction" project - the
    exact shape (status/category/confidence/year) real BOS Signal #3 has,
    never asserting anything about the real row itself."""
    airport = Airport(name="Test Logan Airport", iata_code="TLG", icao_code="KTLG", state_region="Massachusetts", country="USA")
    session.add(airport); session.commit()
    source = Source(title="Test Grant Record", source_type="faa_tableau", reliability_level="official", url=_SOURCE_URL)
    session.add(source); session.commit()
    signal = Signal(
        airport_id=airport.id, source_id=source.id, title="Runway 9/27 test EMAS phase 2",
        category="new_installation", confidence="high", status="under construction",
        planning_year=2026, probability_score=10.0, published=published,
    )
    session.add(signal); session.commit()
    return airport


def _seed_sacheon_shaped(session) -> tuple[Airport, Signal]:
    """Mirrors real Sacheon/Signal69's full governed shape: admitted Korean
    alias, cross-source-confirmed identity, 1 governed claim, zero runway
    rows, a Signal created through the real governed pipeline."""
    airport = Airport(name="Test Airport", iata_code=None, icao_code="RKPT", country="South Korea")
    session.add(airport); session.commit()

    alias_source = Source(title="Registry", source_type="Authority", reliability_level="official")
    session.add(alias_source); session.commit()
    alias_assertion = SourceAssertion(
        source_id=alias_source.id, airport_id=airport.id, assertion_type="airport_inventory",
        raw_relevant_text=_KOREAN_ALIAS_EXCERPT, source_record_identifier="rec-alias", evidence_quality="direct_strong",
    )
    session.add(alias_assertion); session.commit()
    record_airport_alias(
        session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
        alias="테스트공항", evidence_excerpt=_KOREAN_ALIAS_EXCERPT, analyst="human:tester",
    )
    session.commit()
    alias = session.query(AirportAlias).filter_by(airport_id=airport.id).one()

    council_source = Source(
        title="Test Council Record", source_type="Authority", reliability_level="official",
        publisher="Test Council Committee", url=_SOURCE_URL, published_date=date(2025, 6, 11),
    )
    session.add(council_source); session.commit()
    sa = SourceAssertion(
        source_id=council_source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text=_KOREAN_COUNCIL_EXCERPT, source_record_identifier="rec-council", evidence_quality="direct_strong",
        identity_guard_decision="ATTACH_PROVISIONAL", intelligence_review_decision="REVIEW_REQUIRED",
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )
    session.add(sa); session.commit()
    record_cross_source_alias_attestation(
        session, source_assertion_id=sa.id, matched_alias_id=alias.id, analyst="human:tester", reason="test",
    )
    session.commit()
    record_manual_claim_evidence(
        session, source_assertion_id=sa.id, claim_category="explicit_document_fact",
        subject="EMAS installation", statement="EMAS installation is confirmed.",
        evidence_excerpt="테스트공항 EMAS project confirmed", analyst="human:tester",
    )
    session.commit()
    record_reviewer_action(
        session, sa, action="APPROVE_SIGNAL", reason="Effectively confirmed identity, human-approved.",
        reviewer="human:tester",
    )
    session.commit()
    result = create_signal_from_approved_review(
        session, sa, title="Test Sacheon EMAS installation", category="new_installation",
        confidence="medium", status="identified",
    )
    session.commit()
    from app.services.signal_publication import publish_signal
    publish_signal(session, result.signal, reviewer="human:tester", reason="test publication")
    session.commit()
    return airport, result.signal


# ---------------------------------------------------------------------------
# 1-2. Overview renders + real deterministic metrics
# ---------------------------------------------------------------------------

def test_overview_renders_successfully(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert (tmp_path / "site" / "index.html").exists()


def test_overview_active_opportunity_count_is_real_and_deterministic(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)  # under construction -> active_opportunity
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Aktiva möjligheter" in html
    # ("RWI - Juicy Design Mission #2" mission) The Overview KPI markup was
    # intentionally replaced (`.stat`/`.value`, label-then-value -> the new
    # hero-integrated `.hero-kpi`/`.hero-kpi-value`, value-then-label) by
    # that mission's authorized visual overhaul - this assertion follows
    # that change; the underlying metric/value is unchanged.
    m = re.search(r'<div class="hero-kpi-value"[^>]*>(\d+)</div>\s*<div class="hero-kpi-label">Aktiva möjligheter</div>', html)
    assert m is not None
    assert int(m.group(1)) == 1  # exactly the one under-construction signal seeded


def test_overview_market_summary_reflects_real_country_counts(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    # ("RWI - Juicy Design Mission #2" mission) The market-summary card was
    # renamed "Marknader" -> "Marknadsläge" (t("market_pulse")) as part of
    # that mission's own "Market Pulse" redesign.
    assert "Marknadsläge" in html
    assert "USA" in html
    assert "South Korea" in html
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    countries = {s["country"] for s in data["signals"]}
    assert countries == {"USA", "South Korea"}  # no fabricated country appears


def test_overview_omits_market_summary_card_when_no_public_signals(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        Airport(name="Empty Airport", country="Nowhere")
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "market-summary" not in html


# ---------------------------------------------------------------------------
# 3-4. BOS-shaped hierarchy: one dominant state, not contradicted
# ---------------------------------------------------------------------------

def test_bos_shaped_airport_detail_renders_project_summary(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    # ("RWI - Juicy Design Mission #2 - Visual Correction Pass") The
    # project-summary section is now also `.card.panel-strong` (moved into
    # the new 3-column .airport-body-grid) - `project-summary` is still a
    # class token on the element, just not the sole class attribute value.
    assert 'project-summary' in html
    # The dominant status badge moved from inside `.project-summary` into
    # the new `.airport-header` (Mission #2's own header-strengthening
    # change) - still present on the page, still exactly the same real
    # status_role/status_label.
    assert 'class="hero-status hero-status-lg construction"' in html
    assert "Under byggnation" in html


def test_bos_shaped_primary_state_not_contradicted_by_another_equally_prominent_badge(tmp_path):
    """Exactly one `.hero-status` (the dominant descriptor) appears
    anywhere on the page (in the header, since "RWI - Juicy Design Mission
    #2")."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert html.count('class="hero-status') == 1
    # ("RWI - Juicy Design Mission #2 - Visual Correction Pass") The
    # quick-facts strip sits directly under the header, outside the
    # project-summary <section> - it must contain no second .hero-status,
    # the header remaining the sole owner of that class.
    quick_facts = re.search(r'<div class="quick-facts">.*?(?=<div class="airport-body-grid">)', html, re.S).group(0)
    assert 'class="hero-status' not in quick_facts
    # ("RWI - Juicy Design Mission #3" mission) The lifecycle ("Läge")
    # pill that used to sit in .quick-facts was intentionally removed
    # (Mission #3's own semantic recon: it was a second, frequently
    # near-tautological status-shaped badge competing with the real
    # phase/type/evidence facts) - its real content is preserved as prose
    # in "Varför RWI bevakar detta" instead (tests/
    # test_static_export_airport_intelligence.py's own
    # test_lage_pill_no_longer_present_in_quick_facts covers this
    # directly). This assertion is updated accordingly, not merely
    # relaxed: a `.lifecycle` class must not appear in .quick-facts.
    assert 'class="lifecycle' not in quick_facts


# ---------------------------------------------------------------------------
# 5-10. Sacheon regression
# ---------------------------------------------------------------------------

def test_sacheon_shaped_airport_detail_renders_correctly(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, _signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert (tmp_path / "site" / "airports" / f"{airport.id}.html").exists()


def test_sacheon_shaped_korean_alias_present(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, _signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "테스트공항" in html


def test_sacheon_shaped_signal_remains_linked_from_airport(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert f'signals/{signal.id}.html' in html
    assert (tmp_path / "site" / "signals" / f"{signal.id}.html").exists()


def test_sacheon_shaped_zero_runway_state_does_not_fabricate_runway_content(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, _signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Ingen banuppgift registrerad" in html
    assert '<span class="pill status">' not in re.search(
        r'<div class="card-header">Banor</div>.*?</div>\s*</div>', html, re.S,
    ).group(0)


def test_sacheon_shaped_evidence_remains_intact(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "EMAS installation is confirmed." in html
    assert "테스트공항 EMAS project confirmed" in html


def test_sacheon_shaped_source_url_remains_exact(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert f'href="{_SOURCE_URL}"' in html


# ---------------------------------------------------------------------------
# 11. Internal governance fields absent
# ---------------------------------------------------------------------------

def test_internal_governance_fields_absent_from_public_html_and_data_json(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    signal_html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    airport_html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    data_json = (tmp_path / "site" / "data.json").read_text(encoding="utf-8")
    forbidden = (
        "ATTACH_PROVISIONAL", "ATTACH_CONFIRMED", "CROSS_SOURCE_ALIAS_ATTESTATION",
        "HUMAN_REVIEW_REQUIRED", "REVIEW_REQUIRED", "APPROVE_SIGNAL",
        "SignalPublicationAction", "human:tester",
    )
    for token in forbidden:
        assert token not in signal_html
        assert token not in airport_html
        assert token not in data_json


# ---------------------------------------------------------------------------
# 12. Static export succeeds
# ---------------------------------------------------------------------------

def test_static_export_succeeds_with_both_fixtures_together(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "site" / "airports" / "index.html").exists()
    assert (tmp_path / "site" / "signals" / "index.html").exists()


# ---------------------------------------------------------------------------
# 13. Existing public filtering still works (signals_list.html data-search)
# ---------------------------------------------------------------------------

def test_signals_list_filtering_data_attributes_still_present(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    assert 'data-search=' in html
    assert 'data-country=' in html
    assert 'data-lifecycle=' in html


# ---------------------------------------------------------------------------
# 14. No unpublished Signal becomes public (incl. the new primary_signal path)
# ---------------------------------------------------------------------------

def test_unpublished_signal_does_not_become_primary_signal_or_public(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session, published=False)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'class="project-summary"' not in html
    assert not (tmp_path / "site" / "signals" / "1.html").exists()
