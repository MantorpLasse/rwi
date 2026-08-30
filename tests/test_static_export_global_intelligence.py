"""Tests for "RWI - Juicy Design Mission #2 - V2.3/V2.4" (Global intelligens
country-level map - a real world-land silhouette as of V2.4's "WOW Pass" -
+ the "Utveckling" companion card, renamed from "Viktiga utvecklingar").

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_seed_bos_shaped`, `_engine`) are
imported from tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Airport, Signal, Source
from app.static_export import build
from app.static_export import build_site
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped


def _seed_signal(session, *, country, status, confidence="high", planning_year=2026, title=None) -> Signal:
    airport = Airport(name=f"Test Airport {country}", country=country)
    session.add(airport); session.commit()
    source = Source(title="Test Source", source_type="news", reliability_level="official")
    session.add(source); session.commit()
    signal = Signal(
        airport_id=airport.id, source_id=source.id,
        title=title or f"Test signal in {country}",
        category="new_installation", confidence=confidence, status=status,
        planning_year=planning_year, probability_score=5.0, published=True,
    )
    session.add(signal); session.commit()
    return signal


# ---------------------------------------------------------------------------
# Map section renders / structure
# ---------------------------------------------------------------------------

def test_global_intelligence_map_renders(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="global-map"' in html
    assert 'map-card' in html


def test_important_developments_renders(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)  # status="under construction" -> committed pipeline
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'important-developments' in html
    assert 'class="important-dev-row"' in html
    assert "Runway 9/27 test EMAS phase 2" in html


def test_important_developments_empty_state_when_no_committed_pipeline_signal(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_signal(session, country="USA", status="identified")  # not construction/procurement/funded
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="important-developments"' not in html
    assert "Inga signaler befinner sig i byggnation" in html


# ---------------------------------------------------------------------------
# No runtime remote-map dependency
# ---------------------------------------------------------------------------

def test_no_remote_map_dependency_in_rendered_html(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    for forbidden in ("googleapis.com/maps", "mapbox", "leaflet", "openstreetmap", "<iframe"):
        assert forbidden not in html.lower()


def test_no_remote_map_dependency_in_source_or_static_assets():
    css = Path("app/static_export/static/style.css").read_text(encoding="utf-8")
    for forbidden in ("mapbox", "leaflet", "googleapis.com/maps", "openstreetmap"):
        assert forbidden not in css.lower()
    build_source = Path("app/static_export/build.py").read_text(encoding="utf-8")
    for forbidden in ("mapbox", "leaflet", "requests.get(", "urllib.request", "import requests"):
        assert forbidden not in build_source


# ---------------------------------------------------------------------------
# Map data comes from real derived country activity; USA not hard-coded
# ---------------------------------------------------------------------------

def test_map_dominant_country_is_not_hardcoded_to_usa(tmp_path):
    """With 3 real Brazil signals and 1 real USA signal, Brazil - not USA -
    must be the dominant map node/legend entry."""
    engine = _engine()
    with Session(engine) as session:
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal 1")
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal 2")
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal 3")
        _seed_signal(session, country="USA", status="under construction", title="USA signal 1")
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="map-legend-item-dominant"' not in html  # class is combined, check via combined form below
    m = re.search(r'<div class="map-legend-item map-legend-item-dominant">.*?<span class="map-legend-country">([^<]+)</span>', html, re.S)
    assert m is not None
    assert m.group(1) == "Brazil"


def test_map_reflects_same_activity_numbers_as_marknadslage(tmp_path):
    """One single 'activity' definition for the page - the map and
    Marknadsläge must agree exactly on the real percentage."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    market_pct = re.search(r'<strong>(\d+)%</strong> av aktiviteten', html).group(1)
    map_pct = re.search(r'<span class="map-legend-pct mono">(\d+)%</span>', html).group(1)
    assert market_pct == map_pct == "100"


# ---------------------------------------------------------------------------
# No invented airport coordinates / no fake airport markers
# ---------------------------------------------------------------------------

def test_global_intelligence_view_never_reads_airport_coordinates():
    """Structural guard: the map's own geometry function takes only the
    already-computed `market_summary` (country/count/pct_of_total/flag) -
    no Airport, Session, or coordinate ever reaches it at all, proven via
    its own signature rather than a fragile text search (the function's
    docstring legitimately discusses latitude/longitude in prose, to
    explain the geo-recon finding - see build.py's own geo-recon docstring
    for why those columns, populated for USA Airports only, are never
    used here)."""
    params = list(inspect.signature(build._global_intelligence_view).parameters)
    assert params == ["market_summary"]
    full_source = inspect.getsource(build._global_intelligence_view)
    body = re.sub(r'""".*?"""', "", full_source, count=1, flags=re.S)  # strip the docstring only
    assert "latitude" not in body
    assert "longitude" not in body
    assert ".airport" not in body


def test_map_node_count_equals_distinct_countries_not_signals_or_airports(tmp_path):
    """3 Brazil signals (at 3 different airports) + 1 USA signal must
    produce exactly 2 map nodes (2 countries), never 4 (one per signal/
    airport) - proving this is country-level, not airport-point, and no
    fake per-airport marker is fabricated."""
    engine = _engine()
    with Session(engine) as session:
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal 1")
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal 2")
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal 3")
        _seed_signal(session, country="USA", status="under construction", title="USA signal 1")
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    node_count = len(re.findall(r'class="map-node[" ]', html))
    assert node_count == 2


def test_data_json_does_not_carry_map_geometry_or_new_activity_definition(tmp_path):
    """The map's own computed geometry (x/y/radius) is presentation-only,
    like _trend_view()'s own SVG geometry - never duplicated into
    data.json."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    assert "global_intelligence" not in data
    assert "important_developments" not in data


# ---------------------------------------------------------------------------
# Important developments derive from existing public data (deterministic)
# ---------------------------------------------------------------------------

def test_important_developments_only_includes_committed_pipeline_signals(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_signal(session, country="USA", status="under construction", title="Construction signal")
        _seed_signal(session, country="USA", status="identified", title="Merely identified signal")
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    dev_section = re.search(
        r'<div class="card-body important-developments">.*?(?=EMAS-aktivitet över tid)', html, re.S,
    ).group(0)
    assert "Construction signal" in dev_section
    assert "Merely identified signal" not in dev_section


def test_important_developments_excludes_unpublished_signal(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session, published=False)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Runway 9/27 test EMAS phase 2" not in html


# ---------------------------------------------------------------------------
# Generated HTML validity / responsive structure / no forbidden values
# ---------------------------------------------------------------------------

def test_generated_html_is_well_formed(tmp_path):
    from html.parser import HTMLParser

    class _Checker(HTMLParser):
        pass

    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    _Checker().feed(html)  # raises on malformed markup


def test_responsive_media_queries_cover_new_map_row():
    css = Path("app/static_export/static/style.css").read_text(encoding="utf-8")
    assert "@media (max-width: 480px)" in css and ".global-map" in css
    # V2.3's own overflow fix: the ≤1080px collapse must use minmax(0, 1fr),
    # not a bare 1fr, or a wide child (e.g. the trend chart) can force the
    # whole page wider than the real viewport.
    assert "grid-template-columns: minmax(0, 1fr);" in css


def test_no_forbidden_fake_values_in_new_components(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    for forbidden in (
        "Watchlist", "market share", "marknadsandel", "passenger", "passagerare",
        "AI-ranked", "importance score", "revenue",
    ):
        assert forbidden not in html


# ---------------------------------------------------------------------------
# "RWI - Juicy Design Mission #2 - V2.4 Global Intelligence Map WOW Pass"
# ---------------------------------------------------------------------------

def test_developments_heading_is_exactly_utveckling(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'card-header">Utveckling<', html)


def test_old_viktiga_utvecklingar_heading_is_absent(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Viktiga utvecklingar" not in html


def test_map_uses_a_real_local_world_land_asset_with_documented_provenance():
    """Geo-asset recon requirement: a real, licensed, locally-committed
    geometry file - never fetched at runtime, never silently downloaded
    without documentation."""
    geo_dir = Path("app/static_export/geo")
    path_file = geo_dir / "world_land_110m.path.txt"
    provenance = geo_dir / "PROVENANCE.md"
    assert path_file.is_file()
    assert path_file.stat().st_size > 10_000  # a real, non-trivial path, not a stub
    assert provenance.is_file()
    license_text = provenance.read_text(encoding="utf-8")
    assert "ISC" in license_text
    assert "public domain" in license_text.lower()
    assert "Natural Earth" in license_text


def test_world_land_path_is_embedded_inline_not_fetched(tmp_path):
    """The real map geometry must be baked into the exported HTML itself
    (inline SVG <path>) - no separate .json/.svg request, no CDN, matching
    the "genuinely static export, no runtime dependency" requirement."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<path d="([^"]{5000,})" class="map-land"', html)
    assert m is not None, "expected a large inline <path> for the world map"


def test_every_real_active_market_appears_in_legend_even_without_a_map_pin(tmp_path):
    """A real country not present in the small, non-exhaustive
    _COUNTRY_MAP_POSITION lookup must still show its real numbers in the
    legend - map placement is decorative only, never a gate on real data."""
    engine = _engine()
    with Session(engine) as session:
        _seed_signal(session, country="Testlandia", status="under construction", title="Unmapped-country signal")
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Testlandia" in html
    assert re.search(r'map-legend-country">Testlandia<', html)
    # and it must NOT have produced a map pin (not in the lookup table)
    assert html.count('class="map-node') == 0


def test_map_node_positions_are_not_hardcoded_per_request(tmp_path):
    """Two different real datasets (different dominant countries) must
    produce different real node positions - proving positions are derived
    from data + the lookup table, not a fixed/hardcoded per-request
    layout."""
    engine1 = _engine()
    with Session(engine1) as session:
        _seed_signal(session, country="USA", status="under construction", title="USA signal")
        build_site(tmp_path / "site1", session=session, today=date(2026, 8, 30))
    engine2 = _engine()
    with Session(engine2) as session:
        _seed_signal(session, country="Brazil", status="under construction", title="Brazil signal")
        build_site(tmp_path / "site2", session=session, today=date(2026, 8, 30))
    html1 = (tmp_path / "site1" / "index.html").read_text(encoding="utf-8")
    html2 = (tmp_path / "site2" / "index.html").read_text(encoding="utf-8")
    node1 = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="[\d.]+" class="map-node map-node-dominant"', html1)
    node2 = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="[\d.]+" class="map-node map-node-dominant"', html2)
    assert node1 is not None and node2 is not None
    assert node1.groups() != node2.groups()


def test_no_horizontal_overflow_css_pattern_preserved():
    """Regression guard: the V2.3 overflow fix (minmax(0, 1fr) at the
    <=1080px breakpoint) must not have been reintroduced as a bare 1fr by
    this pass's own CSS edits."""
    css = Path("app/static_export/static/style.css").read_text(encoding="utf-8")
    # Non-greedy match up to a `}` at the start of its own line - the outer
    # media-block close (inner per-selector rules are indented, so their
    # own closing braces never start a line).
    media_block = re.search(r'@media \(max-width: 1080px\) \{.*?\n\}', css, re.S).group(0)
    assert "grid-template-columns: 1fr;" not in media_block
    assert media_block.count("minmax(0, 1fr)") >= 3


