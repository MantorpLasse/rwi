"""Tests for "RWI - Juicy Design Mission #4" (Evidence + Timeline
Intelligence): the "Intelligenshistorik" chronological Signal/Incident
event view, the honest dated/undated split, the "EMAS-installation"
presentation (Installation rows, no longer merged into the timeline; renamed
from "Historisk EMAS-kontext" and promoted to a prominent, un-collapsed
position by "RWI - Mission #8H" - the underlying data/fields these tests
exercise are unchanged), and the new "Bevis" (evidence) section built from
build.py's own governed read service.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db and never the real Signal69/BOS Signal #3.
Fixtures (`_seed_bos_shaped`, `_seed_sacheon_shaped`, `_engine`,
`_SOURCE_URL`) are imported from tests/test_static_export_design_v2.py
rather than duplicated, matching tests/test_static_export_airport_intelligence.py's
own established convention.
"""
from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Installation, Signal, Source
from app.static_export import build
from app.static_export import build_site
from tests.test_static_export_design_v2 import _SOURCE_URL, _engine, _seed_bos_shaped, _seed_sacheon_shaped


def _seed_bos_rich(session):
    """BOS-shaped airport with a richer real-world-mirrored shape: the
    dominant "under construction" project Signal (`_seed_bos_shaped`), a
    dated Installation (a real install_year, a dated Source), an undated
    Installation (no install_year, no Source date - a raw current-presence
    snapshot, mirrors real BOS installation #33), a funding Signal (a
    USAspending-shaped grant, current fiscal year), and an old (>5y),
    incident-derived Signal (mirrors the real product concern: a ~2016
    post-incident replacement must never present as a current opportunity).
    Never asserts anything about the real BOS/Signal-3 row itself."""
    airport = _seed_bos_shaped(session)

    dated_source = Source(
        title="Test Fact Sheet", source_type="faa_fact_sheet", reliability_level="official",
        url="https://faa.example.test/fact-sheet", published_date=date(2016, 2, 4),
    )
    undated_source = Source(
        title="Test FAA current-presence map", source_type="faa_tableau", reliability_level="official",
        url="https://faa.example.test/map",
    )
    session.add_all([dated_source, undated_source]); session.commit()

    dated_installation = Installation(
        airport_id=airport.id, source_id=dated_source.id, type="EMASMAX",
        status="active", install_year=2005,
    )
    undated_installation = Installation(
        airport_id=airport.id, source_id=undated_source.id, type="EMASMAX", status="active",
    )
    session.add_all([dated_installation, undated_installation]); session.commit()

    grant_source = Source(
        title="Test USAspending grant record", source_type="usaspending_grant", reliability_level="official",
        url="https://usaspending.example.test/award/TEST",
    )
    session.add(grant_source); session.commit()
    funding_signal = Signal(
        airport_id=airport.id, source_id=grant_source.id, title="USAspending grant — test, FY2026",
        category="new_installation", confidence="high", status="identified",
        planning_year=2026, estimated_total_value_usd=Decimal("9000000.00"), published=True,
    )
    session.add(funding_signal); session.commit()

    old_incident_signal = Signal(
        airport_id=airport.id, source_id=dated_source.id,
        title="Test replacement after incident (2016-01-01)",
        category="replacement_after_incident", confidence="high", status="identified",
        planning_year=2016, published=True,
    )
    session.add(old_incident_signal); session.commit()

    return airport, dated_installation, undated_installation, funding_signal, old_incident_signal


def _airport_html(tmp_path, airport_id):
    return (tmp_path / "site" / "airports" / f"{airport_id}.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1/6. Installations no longer feed the chronological timeline at all - the
# exact resolution of BOS's apparent "EMASMAX in both dated and undated"
# duplication (Mission #4 Section 3).
# ---------------------------------------------------------------------------

def test_installations_do_not_appear_in_intelligence_history(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, dated_installation, undated_installation, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    assert f'id="installation-{dated_installation.id}"' not in history
    assert f'id="installation-{undated_installation.id}"' not in history
    # Both installations are still fully presented, just in their own
    # dedicated section, never dropped.
    assert f'id="installation-{dated_installation.id}"' in html
    assert f'id="installation-{undated_installation.id}"' in html


def test_undated_installation_labeled_current_physical_state_not_odaterat(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, dated_installation, undated_installation, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    assert "Aktuellt tillstånd" in html
    assert "Odaterat" not in html
    # The dated installation keeps its real install year, distinguishing it
    # from the undated one - never the same label for both.
    assert "Installerad 2005" in html


# ---------------------------------------------------------------------------
# 2. Current project intelligence remains visually distinct (reuses the
# already-real SLT1 lifecycle badge, never a new status).
# ---------------------------------------------------------------------------

def test_current_construction_signal_gets_active_opportunity_badge_in_history(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    assert 'class="lifecycle active"' in history
    assert "Aktuell möjlighet" in history


def test_old_incident_derived_signal_never_gets_active_opportunity_badge(tmp_path):
    """The mission's own explicit product principle: an old (~2016)
    post-incident replacement must not present as a current opportunity,
    even though it has a real, dated timeline entry."""
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    incident_block = history.split("Test replacement after incident")[1][:600]
    assert 'class="lifecycle active"' not in incident_block
    assert ("Behöver research" in incident_block) or ("Historik" in incident_block)


# ---------------------------------------------------------------------------
# 3. Deterministic timeline ordering.
# ---------------------------------------------------------------------------

def test_intelligence_history_ordering_is_deterministic_across_rebuilds(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site" / "a", session=session, today=date(2026, 8, 30))
        build_site(tmp_path / "site" / "b", session=session, today=date(2026, 8, 30))
    html_a = (tmp_path / "site" / "a" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    html_b = (tmp_path / "site" / "b" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    history_a = html_a.split("Intelligenshistorik")[1].split("Underlag")[0]
    history_b = html_b.split("Intelligenshistorik")[1].split("Underlag")[0]
    assert history_a == history_b


def test_intelligence_history_dated_events_sorted_chronologically(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    years_in_order = [int(y) for y in
                       __import__("re").findall(r'timeline-year mono">(\d{4})<', history)]
    assert years_in_order == sorted(years_in_order)


# ---------------------------------------------------------------------------
# 4/5. Source publication date never silently becomes the project event
# date; undated records carry an honest, distinguishing reason.
# ---------------------------------------------------------------------------

def test_signal_with_source_date_but_no_event_year_lands_undated_with_reason(tmp_path):
    """Sacheon-shaped: the real Source has a real published_date, but the
    Signal itself has no target_year/planning_year - it must land in the
    undated bucket, never silently dated by the source's own date."""
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    # The source's own real year (2025) must never appear as this event's
    # own timeline-year - it has none.
    assert '<div class="timeline-year mono">2025</div>' not in history
    assert "Källan är daterad, men själva händelsens tidpunkt är inte fastställd." in history


def test_undated_event_with_no_source_date_either_gets_no_date_reason(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        undated_source = Source(title="No-date source", source_type="news")
        session.add(undated_source); session.commit()
        undated_signal = Signal(
            airport_id=airport.id, source_id=undated_source.id, title="Test undated project signal",
            category="study", confidence="low", status="identified", published=True,
        )
        session.add(undated_signal); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    assert "Varken händelsens tidpunkt eller källans datum är kända." in history


# ---------------------------------------------------------------------------
# 7. Sacheon receives no fabricated chronology.
# ---------------------------------------------------------------------------

def test_sacheon_intelligence_history_has_exactly_one_undated_event_no_dated(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    assert history.count("timeline-rail") == 1  # exactly one event, dated or undated
    assert history.count(f'signals/{signal.id}.html') == 1
    assert '<div class="timeline">' not in history  # no dated spine at all, only the undated one


# ---------------------------------------------------------------------------
# 8/9/10. Evidence section: Signal69-shaped claims/original excerpt/source
# preserved; financial caveat present for funding events; no governance
# leakage anywhere on the page.
# ---------------------------------------------------------------------------

def test_sacheon_shaped_evidence_claim_and_original_excerpt_render_in_bevis_section(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    assert "Underlag" in html
    assert "EMAS installation is confirmed." in html  # the governed claim statement
    assert "테스트공항 EMAS project confirmed" in html  # original-language excerpt, verbatim
    assert _SOURCE_URL in html


def test_funding_event_shows_total_grant_value_with_non_emas_contract_caveat(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    history = html.split("Intelligenshistorik")[1].split("Underlag")[0]
    assert "$9,000,000" in history
    assert "anger inte automatiskt ett EMAS-kontraktsvärde" in history


# ---------------------------------------------------------------------------
# RWI HQ "Signal Detail Funding-Caveat Parity" mission: Signal Detail's own
# "Ekonomi" card gets the SAME funding-source classification and the SAME
# _FUNDING_CAVEAT text Airport Detail's timeline already uses above - never
# a second, independently-worded caveat, never a BGM/Signal-id special case.
# Reuses `_seed_bos_rich`'s existing funding_signal (usaspending_grant,
# $9,000,000.00) and old_incident_signal (non-funding) fixtures verbatim.
# ---------------------------------------------------------------------------


def _signal_html(tmp_path, signal_id):
    return (tmp_path / "site" / "signals" / f"{signal_id}.html").read_text(encoding="utf-8")


def test_signal_detail_funding_signal_shows_bidragsbelopp_label_and_caveat(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _airport, _di, _ui, funding_signal, _old = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
        funding_signal_id = funding_signal.id
    html = _signal_html(tmp_path, funding_signal_id)
    assert "Bidragsbelopp" in html
    assert "Total projektbudget" not in html
    assert "anger inte automatiskt ett EMAS-kontraktsvärde" in html
    # Amount formatting itself is untouched by the label/caveat change.
    assert "$9,000,000" in html


def test_signal_detail_non_funding_signal_keeps_total_projektbudget_unchanged(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _airport, _di, _ui, _funding, old_incident_signal = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
        old_incident_signal_id = old_incident_signal.id
    html = _signal_html(tmp_path, old_incident_signal_id)
    assert "Total projektbudget" in html
    assert "Bidragsbelopp" not in html
    assert "anger inte automatiskt ett EMAS-kontraktsvärde" not in html


def test_signal_detail_funding_signal_other_fields_unaffected(tmp_path):
    """Score/confidence/status/source metadata/source_notes are untouched
    by this presentation-only change - only the "Ekonomi" card's own label
    and caveat changed."""
    engine = _engine()
    with Session(engine) as session:
        _airport, _di, _ui, funding_signal, _old = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
        funding_signal_id = funding_signal.id
    html = _signal_html(tmp_path, funding_signal_id)
    assert "Hög" in html  # confidence_label for confidence="high" - unaffected
    assert "USAspending grant" in html  # source_type_label - unaffected
    # estimated_emas_value_usd was never set on this fixture - still renders
    # the existing empty-value dash, untouched by this mission.
    assert "–" in html


def test_signal_view_funding_caveat_reuses_the_same_predicate_and_text_as_timeline(tmp_path):
    """Code-level proof (not just rendered HTML) that _signal_view() never
    grew a second, independently-maintained funding classification or a
    second, independently-worded caveat - and applies to no BGM/Signal-id
    special case, only the shared source_type predicate."""
    from app.static_export import build as build_module

    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        grant_source = Source(
            title="Test grant", source_type="usaspending_grant", reliability_level="official",
            url="https://usaspending.example.test/award/TEST2",
        )
        non_grant_source = Source(
            title="Test non-grant", source_type="faa_fact_sheet", reliability_level="official",
            url="https://faa.example.test/other",
        )
        session.add_all([grant_source, non_grant_source]); session.commit()
        grant_signal = Signal(
            airport_id=airport.id, source_id=grant_source.id, title="Grant signal",
            category="new_installation", confidence="high", status="identified", published=True,
        )
        non_grant_signal = Signal(
            airport_id=airport.id, source_id=non_grant_source.id, title="Non-grant signal",
            category="new_installation", confidence="high", status="identified", published=True,
        )
        session.add_all([grant_signal, non_grant_signal]); session.commit()

        grant_view = build_module._signal_view(grant_signal, today=date(2026, 8, 30))
        non_grant_view = build_module._signal_view(non_grant_signal, today=date(2026, 8, 30))

        assert grant_signal.source.source_type in build_module._GRANT_SOURCE_TYPES_TIMELINE
        assert grant_view.funding_caveat == build_module._FUNDING_CAVEAT
        assert non_grant_signal.source.source_type not in build_module._GRANT_SOURCE_TYPES_TIMELINE
        assert non_grant_view.funding_caveat is None


def test_bos_rich_page_has_no_internal_governance_leakage(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    for forbidden in ("ATTACH_PROVISIONAL", "ATTACH_CONFIRMED", "HUMAN_REVIEW_REQUIRED", "ReviewerAction", "human:tester", "reviewer="):
        assert forbidden not in html


def test_sacheon_shaped_page_has_no_internal_governance_leakage(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    for forbidden in ("ATTACH_PROVISIONAL", "ATTACH_CONFIRMED", "HUMAN_REVIEW_REQUIRED", "ReviewerAction", "human:tester", "reviewer="):
        assert forbidden not in html


def test_evidence_section_shows_plain_citation_when_no_governed_claim_exists(tmp_path):
    """BOS has no governed ManualClaimEvidence - its sources must still
    render as honest, source-backed citations, never omitted."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    assert "Underlag" in html
    assert "Källbelagd, men ingen granskad sakuppgift har transkriberats ännu" in html
    assert _SOURCE_URL in html


# ---------------------------------------------------------------------------
# data.json boundary: evidence (claim statements/original excerpts) must
# never be duplicated into the global JSON payload - same discipline
# test_static_export_evidence.py's own signal-level test already enforces.
# ---------------------------------------------------------------------------

def test_data_json_excludes_airport_level_claim_evidence(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    raw = (tmp_path / "site" / "data.json").read_text(encoding="utf-8")
    assert "테스트공항 EMAS project confirmed" not in raw
    assert "EMAS installation is confirmed." not in raw


# ---------------------------------------------------------------------------
# Structural: this mission's own new build.py functions never write to the
# database (read-only view derivation only) - matches this mission's own
# hard "no database write" boundary.
# ---------------------------------------------------------------------------

def test_intelligence_history_and_evidence_view_functions_are_read_only():
    for fn in (build._intelligence_history_view, build._airport_evidence_view, build._timeline_event):
        source = inspect.getsource(fn)
        assert "session.add(" not in source
        assert "session.commit(" not in source
        assert "session.flush(" not in source
        assert "session.delete(" not in source


# ---------------------------------------------------------------------------
# Mission #2/#3 structural regression: the frozen upper Airport Detail
# composition (hero status, quick-facts, phase strip, "Vad händer just nu",
# Runways, Verifierad förekomst, Plats) is untouched by this mission.
# ---------------------------------------------------------------------------

def test_frozen_upper_airport_detail_composition_unchanged_for_bos(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    assert html.count('class="hero-status') == 1
    assert 'class="hero-status hero-status-lg construction"' in html
    assert "Vad händer just nu" in html
    assert ">Banor<" in html
    # ("RWI - Mission #8H" renamed "EMAS idag" to "Verifierad förekomst";
    # "RWI - Mission #8I.1" then makes the top card lead with a positive
    # "Dokumenterad EMAS" summary once real Installation rows exist, as
    # they do for this fixture - the underlying data/logic is unchanged.
    assert ">Dokumenterad EMAS<" in html
    assert "project_intelligence" not in html  # locale key never leaks raw
    assert "Projektintelligens" in html


# ---------------------------------------------------------------------------
# "Visual Polish Checkpoint" mission: BOS compact provenance mode / Sacheon
# claim-rich mode, driven by evidence SHAPE, never by airport identity.
# ---------------------------------------------------------------------------

def test_bos_all_six_sources_still_present_none_dropped_for_lacking_claims(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    underlag = html.split('id="underlag"')[1]
    # _seed_bos_rich's own 4 distinct real Source rows (Test Grant Record,
    # dated_source, undated_source, grant_source - old_incident_signal
    # deliberately reuses dated_source, matching real-world source reuse).
    assert underlag.count("evidence-provenance-row") == 4
    assert "evidence-source-group" not in underlag  # BOS has no governed claims


def test_bos_generic_provenance_note_appears_exactly_once_not_per_source(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, *_ = _seed_bos_rich(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    underlag = html.split('id="underlag"')[1]
    assert underlag.count("Källbelagd, men ingen granskad sakuppgift har transkriberats ännu") == 1


def test_section_level_counts_derived_from_evidence_shape(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        bos_airport, *_ = _seed_bos_rich(session)
        sacheon_airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    bos_html = _airport_html(tmp_path, bos_airport.id)
    sacheon_html = _airport_html(tmp_path, sacheon_airport.id)
    assert "4 källor" in bos_html
    assert "1 källa · 1 granskad sakuppgift" in sacheon_html


def test_mixed_evidence_airport_renders_both_claim_rich_and_provenance_only(tmp_path):
    """Not a real airport ID special-case - an airport whose evidence
    naturally contains one claim-rich source and one provenance-only
    source must render both modes side by side."""
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        plain_source = Source(title="Plain unclaimed source", source_type="news", url="https://news.example.test/x")
        session.add(plain_source); session.commit()
        plain_signal = Signal(
            airport_id=airport.id, source_id=plain_source.id, title="Test unclaimed signal",
            category="study", confidence="low", status="identified", published=True,
        )
        session.add(plain_signal); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    underlag = html.split('id="underlag"')[1]
    assert "evidence-source-group" in underlag  # claim-rich (Signal69-shaped)
    assert "evidence-provenance-row" in underlag  # provenance-only (plain source)
    assert "2 källor · 1 granskad sakuppgift" in html


def test_evidence_summary_derivation_has_no_airport_specific_hardcoding():
    source = inspect.getsource(build._evidence_summary_view)
    for forbidden in ("airport.id", "== 3", "== 88", "airport_id == 3", "airport_id == 88"):
        assert forbidden not in source
    source2 = inspect.getsource(build._airport_evidence_view)
    for forbidden in ("airport.id", "== 3)", "== 88)", "airport_id == 3", "airport_id == 88"):
        assert forbidden not in source2


def test_original_korean_excerpt_styled_distinctly_from_claim_statement(tmp_path):
    """The original-language excerpt must render inside its own
    evidence-excerpt disclosure, visually distinguishable (via CSS, not
    content) from the claim statement's own plain prose."""
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = _airport_html(tmp_path, airport.id)
    assert 'class="annotation source-detail evidence-excerpt"' in html
    assert "테스트공항 EMAS project confirmed" in html


def test_polish_pass_does_not_reintroduce_evidence_into_data_json(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    raw = (tmp_path / "site" / "data.json").read_text(encoding="utf-8")
    assert "테스트공항 EMAS project confirmed" not in raw
    assert "granskade sakuppgifter" not in raw
    assert "källor" not in raw
