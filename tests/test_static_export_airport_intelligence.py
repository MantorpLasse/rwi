"""Tests for "RWI - Juicy Design Mission #3" (Airport Intelligence Detail +
Status Simplification): the public information model (primary project
phase / project type / evidence strength), the phase-progression strip,
the real-coordinate-only location panel, and the required BOS/Sacheon
regression proofs.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db and never the real Signal69/BOS Signal #3.
Fixtures (`_seed_bos_shaped`, `_seed_sacheon_shaped`, `_engine`) are
imported from tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

import inspect
import re
from datetime import date

from sqlalchemy.orm import Session

from app.static_export import build
from app.static_export import build_site
from tests.test_static_export_design_v2 import _SOURCE_URL, _engine, _seed_bos_shaped, _seed_sacheon_shaped


def _seed_bos_shaped_with_coordinates(session, *, latitude=42.362944, longitude=-71.006389):
    """BOS-shaped fixture with real-looking coordinates set directly on the
    Airport row - mirrors the real BOS row's own governed lat/long shape,
    never asserting the exact real value."""
    airport = _seed_bos_shaped(session)
    airport.latitude = latitude
    airport.longitude = longitude
    session.commit()
    return airport


# ---------------------------------------------------------------------------
# One dominant project phase in the header; project type/evidence remain
# distinguishable; lifecycle wording no longer competes as a second status.
# ---------------------------------------------------------------------------

def test_exactly_one_dominant_phase_badge_in_header(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert html.count('class="hero-status') == 1
    assert 'class="hero-status hero-status-lg construction"' in html
    assert "Under byggnation" in html


def test_project_type_and_evidence_strength_remain_distinguishable(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    quick_facts = re.search(r'<div class="quick-facts">.*?(?=<section)', html, re.S).group(0)
    assert "Ny installation" in quick_facts  # project type
    assert "Hög" in quick_facts  # evidence strength (confidence gauge label)


def test_lage_pill_no_longer_present_in_quick_facts(tmp_path):
    """The former "Läge" (lifecycle) quick-fact pill must be gone - its
    content is preserved as prose in "Varför RWI bevakar detta" instead,
    never simply deleted."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    quick_facts = re.search(r'<div class="quick-facts">.*?(?=<section)', html, re.S).group(0)
    assert "Läge" not in quick_facts
    assert 'class="lifecycle' not in quick_facts
    # the same real content still appears, just as prose:
    assert "Varför RWI bevakar detta" in html
    assert "Källor pekar på pågående eller kommande ekonomisk aktivitet" in html


def test_no_second_hero_status_class_appears_for_lifecycle(tmp_path):
    """Structural guard: lifecycle presentation must never reuse the
    .hero-status class - that class is reserved for the one dominant
    phase badge."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert html.count('hero-status') <= 2  # the class name + its one usage combo ("hero-status hero-status-lg")


# ---------------------------------------------------------------------------
# BOS real construction state / Sacheon real early-stage state
# ---------------------------------------------------------------------------

def test_bos_shaped_phase_progression_highlights_construction(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'class="phase-step phase-step-current"' in html
    current = re.search(r'<div class="phase-step phase-step-current">.*?</div>', html, re.S).group(0)
    assert "Under byggnation" in current
    # Everything before construction must be "past", nothing after it "future"
    assert html.count("phase-step-past") == 4  # identified, planning, funded, procurement
    assert html.count("phase-step-future") == 1  # completed only


def test_sacheon_shaped_phase_progression_highlights_identified_only(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, _signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    current = re.search(r'<div class="phase-step phase-step-current">.*?</div>', html, re.S).group(0)
    assert "Identifierad" in current
    assert html.count("phase-step-past") == 0  # nothing precedes "identified"
    assert html.count("phase-step-future") == 5


def test_phase_progression_absent_for_airport_with_no_primary_signal(tmp_path):
    """No primary Signal -> no honest phase to show - the whole section
    must be absent, never a guessed/default stage."""
    from app.models import Airport
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Empty Test Airport", country="USA")
        session.add(airport); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Projektintelligens" not in html
    assert 'class="phase-strip"' not in html


# ---------------------------------------------------------------------------
# Location panel: real coordinates only, honest fallback otherwise
# ---------------------------------------------------------------------------

def test_location_marker_renders_only_from_real_airport_coordinates(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped_with_coordinates(session, latitude=42.362944, longitude=-71.006389)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "location-card" in html
    assert "42.3629" in html
    assert "-71.0064" in html
    assert re.search(r'<circle cx="[\d.]+" cy="[\d.]+" r="5" class="map-node map-node-dominant"', html)


def test_no_fake_coordinates_or_marker_when_airport_has_none(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)  # no lat/long set
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'class="location-card"' not in html
    assert "Inga verifierade koordinater registrerade" in html
    assert 'r="5" class="map-node' not in html


def test_sacheon_shaped_has_no_fake_location_marker(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, _signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'class="location-card"' not in html
    assert "Inga verifierade koordinater registrerade" in html


def test_airport_location_view_never_uses_country_centroid_lookup():
    """Structural guard: the airport-level location view must derive its
    marker exclusively from the Airport's OWN lat/long - never fall back
    to the Overview map's coarser, representative _COUNTRY_MAP_POSITION
    lookup (a categorically different, less precise concept)."""
    source = inspect.getsource(build._airport_location_view)
    assert "_COUNTRY_MAP_POSITION" not in source


# ---------------------------------------------------------------------------
# No governance internals leak; Signal69-shaped evidence/source intact
# ---------------------------------------------------------------------------

def test_no_governance_internals_leak_on_new_sections(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    airport_html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    signal_html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    for token in (
        "ATTACH_PROVISIONAL", "ATTACH_CONFIRMED", "CROSS_SOURCE_ALIAS_ATTESTATION",
        "HUMAN_REVIEW_REQUIRED", "ReviewerAction", "human:tester",
    ):
        assert token not in airport_html
        assert token not in signal_html


def test_signal69_shaped_evidence_and_source_remain_intact(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "EMAS installation is confirmed." in html
    assert "테스트공항 EMAS project confirmed" in html
    assert f'href="{_SOURCE_URL}"' in html


# ---------------------------------------------------------------------------
# Overview (Mission #2) not structurally regressed
# ---------------------------------------------------------------------------

def test_overview_structure_not_regressed(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    for marker in (
        'class="hero-v2"', 'class="hero-kpi-grid"', 'class="dashboard-grid"',
        'class="global-map"', 'class="bottom-grid"',
    ):
        assert marker in html


def test_generated_html_is_well_formed(tmp_path):
    from html.parser import HTMLParser

    class _Checker(HTMLParser):
        pass

    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped_with_coordinates(session)
        _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    for html_file in (tmp_path / "site").rglob("*.html"):
        _Checker().feed(html_file.read_text(encoding="utf-8"))
