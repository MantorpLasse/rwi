"""Tests for "RWI - Juicy Design Mission #2" (Overview visual overhaul: hero
image, KPI strip, market pulse, stage-distribution donut; BOS Airport
Detail header strengthening).

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db and never the real Signal69/BOS Signal #3.
Fixtures (`_seed_bos_shaped`, `_seed_sacheon_shaped`) are imported from
tests/test_static_export_design_v2.py (Mission #1) rather than duplicated.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.static_export import build_site
from tests.test_static_export_design_v2 import (
    _SOURCE_URL,
    _engine,
    _seed_bos_shaped,
    _seed_sacheon_shaped,
)

HERO_IMAGE_SOURCE = Path("app/static_export/static/images/rwi-hero-emas.jpg")

# Fabricated values lifted verbatim from docs/design/rwi-visual-target.png -
# this mission's own explicit list of things that must NEVER appear in real
# rendered output (they are composition/hierarchy reference only, never a
# data specification).
_FORBIDDEN_REFERENCE_VALUES = (
    "1,284", "42.7M", "Passengers", "+24 this month", "+7 this month",
    "+5 this month", "+2 this month", "Watchlist", "Early Movers",
    "On Watchlist", "Massport Board approves $167M", "149",
)


def test_hero_image_asset_exists_in_source_tree():
    """The real, authorized asset must be committed at the source path the
    exporter's own static-asset copytree reads from - not left in a
    generated (gitignored) directory."""
    assert HERO_IMAGE_SOURCE.is_file()
    assert HERO_IMAGE_SOURCE.stat().st_size > 10_000  # a real photo, not a stub/placeholder


def test_overview_export_succeeds_and_hero_image_resolves_locally(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "site" / "images" / "rwi-hero-emas.jpg").is_file()
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'src="./images/rwi-hero-emas.jpg"' in html


def test_overview_hero_and_dashboard_structure_present(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="hero-v2"' in html
    assert 'class="hero-kpi-grid"' in html
    assert 'class="dashboard-grid"' in html
    assert 'class="stage-donut"' in html


def test_overview_kpi_values_are_real_no_invented_deltas(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    kpi_section = re.search(r'<div class="hero-kpi-grid">.*?</div>\s*</div>\s*</div>', html, re.S).group(0)
    assert "this month" not in kpi_section  # no fabricated month-over-month delta
    assert re.search(r'hero-kpi-value[^>]*>1<', kpi_section)  # 1 airport seeded
    assert re.search(r'hero-kpi-value[^>]*>1<', kpi_section)  # 1 active opportunity (construction+active lifecycle)


def test_no_reference_image_fabricated_values_appear_anywhere(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    for path in (tmp_path / "site").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        # ("RWI - Juicy Design Mission #2 - V2.4" mission) The real world
        # map's own inline SVG path (app/static_export/geo/) is a blob of
        # numeric coordinates - "149" can trivially occur as a substring of
        # an unrelated decimal there (e.g. "149.4"). Strip that one
        # attribute's value before this human-readable-content scan; it is
        # covered on its own merits by
        # tests/test_static_export_global_intelligence.py instead.
        text = re.sub(r'<path d="[^"]*" class="map-land"', "", text)
        for forbidden in _FORBIDDEN_REFERENCE_VALUES:
            assert forbidden not in text, f"fabricated reference-image value {forbidden!r} leaked into {path}"
    data_json = (tmp_path / "site" / "data.json").read_text(encoding="utf-8")
    for forbidden in _FORBIDDEN_REFERENCE_VALUES:
        assert forbidden not in data_json


def test_no_world_map_or_fake_watchlist_nav_item(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert ">Watchlist<" not in html
    assert "leaflet" not in html.lower()
    assert "market-map" not in html
    assert "world-map" not in html


def test_published_filtering_remains_intact(tmp_path):
    """An unpublished Signal must not appear anywhere in the redesigned
    Overview (Signals Snapshot / Market Pulse / Stage donut all derive from
    the same already-filtered signal_views)."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session, published=False)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Runway 9/27 test EMAS phase 2" not in html
    assert not (tmp_path / "site" / "signals" / "1.html").exists()


def test_bos_shaped_airport_detail_renders_with_new_header(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'class="airport-header"' in html
    assert 'class="hero-status hero-status-lg construction"' in html
    assert "🇺🇸" in html
    assert 'class="quick-facts"' in html


def test_sacheon_shaped_airport_detail_renders_with_new_header(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, _signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'class="airport-header"' in html
    assert "테스트공항" in html
    assert "Ingen banuppgift registrerad" in html
    assert "🇰🇷" in html


def test_signal69_shaped_evidence_and_source_remain_intact(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "EMAS installation is confirmed." in html
    assert f'href="{_SOURCE_URL}"' in html


def test_governance_internals_remain_hidden(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed_sacheon_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    signal_html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    airport_html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    index_html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    for token in ("ATTACH_PROVISIONAL", "ATTACH_CONFIRMED", "CROSS_SOURCE_ALIAS_ATTESTATION", "HUMAN_REVIEW_REQUIRED", "human:tester"):
        assert token not in signal_html
        assert token not in airport_html
        assert token not in index_html


def test_stage_distribution_reflects_real_status_counts(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)  # 1 signal, status="under construction" -> role "construction"
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Under byggnation: 1 (100%)" in html


def test_market_pulse_dominant_market_matches_real_leading_country(tmp_path):
    """With unequal real counts (2 USA signals vs 1 South Korea signal),
    the dominant card must be the country with more real Signals - never an
    arbitrary pick, and never the smaller market inflated to look leading."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        _seed_bos_shaped(session, published=True)  # a second USA signal -> USA=2
        _seed_sacheon_shaped(session)  # South Korea=1
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'market-dominant-country">([^<]+)</div>', html)
    assert m is not None
    assert m.group(1) == "USA"
    # ("RWI - Juicy Design Mission #2 - Visual Correction Pass") The
    # dominant card now shows a real percentage-of-total (pct_of_total),
    # not a raw count, per that pass's own Marknadsläge redesign.
    m_pct = re.search(r'<strong>(\d+)%</strong> av aktiviteten · (\d+) signaler', html)
    assert m_pct is not None
    assert int(m_pct.group(2)) == 2
    assert int(m_pct.group(1)) == round(100 * 2 / 3)  # 2 of 3 total real signals


def test_no_horizontal_overflow_markers_in_new_css():
    """Structural guard: every new fixed-pixel grid-template-columns value
    introduced by this mission has a corresponding narrower-viewport
    override, so a fixed width can never force page-level horizontal
    scroll on a ~390px viewport."""
    css = Path("app/static_export/static/style.css").read_text(encoding="utf-8")
    # ("RWI - Juicy Design Mission #2 - Visual Correction Pass") Breakpoint
    # moved from 900px to 1080px when .dashboard-grid/.bottom-grid were
    # widened alongside the new 1440px .wrap.
    assert "@media (max-width: 1080px)" in css and ".dashboard-grid" in css and ".bottom-grid" in css
    assert "@media (max-width: 980px)" in css and ".airport-body-grid" in css
    assert "@media (max-width: 760px)" in css and ".runway-emas-grid" in css
    assert "overflow-x: hidden" in css  # full-bleed hero safety net
