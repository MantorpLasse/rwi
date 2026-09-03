"""Tests for "RWI - Mission #23C" (Global Footprint V1): a second,
independent map mode on the Overview page - "Installerad bas" - built
entirely from governed Installation data, never from Signal/market_summary.
Preserves the Mission #22A/#23A/#23B invariant:

    DOCUMENTED INSTALLED BASE != CURRENT INTELLIGENCE != CURRENT OPPORTUNITY

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_engine`, `_seed_bos_shaped`) are
imported from tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import date

from sqlalchemy.orm import Session

from app.models import Airport, Installation
from app.static_export import build
from app.static_export import build_site
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped


def _seed_installation_airport(
    session, *, name, country, iata=None, icao=None, installations: "list[dict]",
    latitude=None, longitude=None,
) -> Airport:
    """Minimal Installation-bearing airport, no Signal, no Runway - the
    exact shape a documented-historical-only airport (like the real LCY)
    has today. Each dict in `installations` may set: runway_end, type,
    install_year, confirmed_vendor, notes. `latitude`/`longitude` default
    to None (the exact shape every non-US Airport had before Mission
    #26G-#26I's governed coordinate acceptance) - pass real values to
    exercise Mission #26J's airport-level marker rendering."""
    airport = Airport(name=name, country=country, iata_code=iata, icao_code=icao, latitude=latitude, longitude=longitude)
    session.add(airport)
    session.commit()
    for kwargs in installations:
        session.add(Installation(airport_id=airport.id, status="active", **kwargs))
    session.commit()
    return airport


def _footprint_installed_section(html: str) -> str:
    # Jinja comments never reach the rendered output, so the reliable anchor
    # for "end of the installed-base panel" is the start of the next panel.
    m = re.search(
        r'<div class="footprint-panel footprint-panel-installed">(.*?)'
        r'(?=<div class="footprint-panel footprint-panel-current">)',
        html, re.S,
    )
    assert m is not None, "expected the Installed Base panel to be present in the generated HTML"
    return m.group(0)


# ---------------------------------------------------------------------------
# Core separation: Installation-only vs Signal-only country
# ---------------------------------------------------------------------------


def test_installation_only_country_appears_in_installed_base(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Congonhas", country="Brazil",
            installations=[{"type": "greenEMAS", "install_year": 2022}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "Test Congonhas" in section
    assert "Brazil" in section


def test_installation_only_country_absent_from_current_intelligence(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Congonhas", country="Brazil",
            installations=[{"type": "greenEMAS", "install_year": 2022}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    # Current Intelligence is empty (no Signal at all) - its own empty state shows.
    assert "Ingen aktivitetsdata ännu." in html


def test_signal_only_country_remains_in_current_intelligence_but_not_installed_base(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)  # USA, one Signal, zero Installation
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'map-legend-country">USA<', html)  # Current Intelligence still shows it
    installed_section = _footprint_installed_section(html)
    assert "Inga dokumenterade installationer i denna vy." in installed_section


# ---------------------------------------------------------------------------
# Counting semantics - Mission #23B/#23C Part D
# ---------------------------------------------------------------------------


def test_two_installation_rows_one_airport_count_not_two(tmp_path):
    """LCY-shaped: two Installation rows (one per runway end), same
    airport - must contribute exactly 1 to the country's airport count,
    never 2, and the word "system"/"system" count must never appear."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test London City Airport", country="United Kingdom", iata="LCY", icao="EGLC",
            installations=[
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "09", "confirmed_vendor": "Runway Safe"},
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "27", "confirmed_vendor": "Runway Safe"},
            ],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "1 flygplats med dokumenterad EMAS" in section
    assert "2 flygplatser" not in section
    for forbidden in ("2 EMAS-system", "system", "System"):
        assert forbidden not in section


def test_no_physical_system_language_anywhere_in_installed_base(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Japan",
            installations=[{"type": "greenEMAS", "install_year": 2019, "confirmed_vendor": "Runway Safe"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    for forbidden in ("physical system", "fysiskt system", "EMAS-system"):
        assert forbidden not in section


# ---------------------------------------------------------------------------
# Vendor semantics - Mission #23B/#23C Part O
# ---------------------------------------------------------------------------


def test_confirmed_vendor_shown_when_governed(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Zurich", country="Switzerland",
            installations=[{"type": "greenEMAS", "install_year": 2016, "confirmed_vendor": "Runway Safe"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "Bekräftad leverantör: Runway Safe" in section


def test_unconfirmed_vendor_shown_explicitly_not_omitted(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Congonhas", country="Brazil",
            installations=[{"type": "greenEMAS", "install_year": 2022}],  # no confirmed_vendor
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "Leverantör ej bekräftad" in section


def test_mixed_vendor_state_not_collapsed_into_country_claim(tmp_path):
    """Two airports in one country, one confirmed-vendor, one not - each
    airport's own line must be independently correct; no single
    country-level vendor summary may exist to collapse them."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport A", country="France",
            installations=[{"type": "greenEMAS", "confirmed_vendor": "Runway Safe"}],
        )
        _seed_installation_airport(
            session, name="Test Airport B", country="France",
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    # Each airport's own tag is independently correct - one confirmed, one
    # not - with no separate country-level vendor rollup line to collapse
    # them into (only the per-airport <li> tags carry vendor text at all).
    assert section.count("Bekräftad leverantör: Runway Safe") == 1
    assert section.count("Leverantör ej bekräftad") == 1


# ---------------------------------------------------------------------------
# Product type / install year - not arbitrarily collapsed
# ---------------------------------------------------------------------------


def test_product_type_shown_per_airport(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Germany",
            installations=[{"type": "EMASMAX", "install_year": 2020}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "EMASMAX" in section


def test_install_year_range_when_airport_rows_disagree(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Germany",
            installations=[
                {"type": "EMASMAX", "install_year": 2018, "runway_end": "09"},
                {"type": "EMASMAX", "install_year": 2020, "runway_end": "27"},
            ],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "Installerad 2018–2020" in section


def test_unknown_install_year_shown_explicitly(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Germany",
            installations=[{"type": "EMASMAX"}],  # no install_year
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "Installationsår okänt" in section


# ---------------------------------------------------------------------------
# Hard publication boundary - Installation.notes
# ---------------------------------------------------------------------------


def test_installed_base_global_view_never_reads_notes():
    """Structural guard, mirroring the existing
    test_global_intelligence_view_never_reads_airport_coordinates pattern:
    the adapter's own source code never references `.notes` at all."""
    params = list(inspect.signature(build._installed_base_global_view).parameters)
    assert params == ["airport_views"]
    full_source = inspect.getsource(build._installed_base_global_view)
    body = re.sub(r'""".*?"""', "", full_source, count=1, flags=re.S)
    assert "notes" not in body
    assert ".installations" not in body  # only .installed_base_summary is read


def test_secret_note_text_never_appears_in_installed_base_html(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Brazil",
            installations=[{"type": "greenEMAS", "notes": "INTERNAL-ANALYST-SHORTHAND-9f3c"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "INTERNAL-ANALYST-SHORTHAND-9f3c" not in html


# ---------------------------------------------------------------------------
# No coordinates required; missing map position handled gracefully
# ---------------------------------------------------------------------------


def test_missing_airport_coordinates_do_not_block_installed_base(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_installation_airport(
            session, name="Test Airport", country="Brazil",
            installations=[{"type": "greenEMAS"}],
        )
        assert airport.latitude is None and airport.longitude is None
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Test Airport" in html


def test_missing_country_map_position_still_shows_textual_listing_no_marker(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Testlandia",
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "Testlandia" in section
    assert "ingen kartposition ännu" in section
    assert html.count('class="map-node map-node-installed"') == 0


def test_airport_with_coordinates_has_a_map_marker(tmp_path):
    """Mission #26J: a marker now appears because the AIRPORT itself has
    real coordinates - not merely because its country happens to be in
    the legacy _COUNTRY_MAP_POSITION lookup (retired for Installed Base;
    see test_no_country_centroid_fallback_for_installed_base below for
    the negative case proving that lookup is genuinely never consulted
    here)."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Zurich", country="Switzerland",
            latitude=47.4647, longitude=8.5492,
            installations=[{"type": "greenEMAS", "confirmed_vendor": "Runway Safe"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "ingen kartposition ännu" not in section
    assert 'class="map-node map-node-installed"' in html


def test_no_country_centroid_fallback_for_installed_base(tmp_path):
    """Mission #26J Part J: an Airport with NO coordinates of its own gets
    NO marker, even though "Switzerland" has a curated
    _COUNTRY_MAP_POSITION entry the pre-#26J code would have used. Proves
    the country-centroid fallback is genuinely retired for Installed
    Base, not merely untested."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Zurich No Coords", country="Switzerland",
            installations=[{"type": "greenEMAS", "confirmed_vendor": "Runway Safe"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert html.count('class="map-node map-node-installed"') == 0
    assert "ingen kartposition ännu" in section


def test_marker_xy_matches_project_lon_lat_directly(tmp_path):
    """Mission #26J Part I: marker x/y is byte-identical to calling
    _project_lon_lat() directly with the Airport's own coordinates - no
    second projection formula, no recalibration."""
    lat, lon = 47.4647, 8.5492
    expected_x, expected_y = build._project_lon_lat(lon, lat)
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Zurich", country="Switzerland",
            latitude=lat, longitude=lon,
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert f'cx="{expected_x}" cy="{expected_y}"' in section


def test_multi_installation_airport_with_coordinates_is_one_marker(tmp_path):
    """Mission #26J Part K: LCY-shaped (two Installation rows, one per
    runway end) but NOW seeded with real coordinates - must still produce
    exactly ONE map marker, never two."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test London City Airport", country="United Kingdom", iata="LCY", icao="EGLC",
            latitude=51.5053, longitude=0.0553,
            installations=[
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "09", "confirmed_vendor": "Runway Safe"},
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "27", "confirmed_vendor": "Runway Safe"},
            ],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert section.count('class="map-node map-node-installed"') == 1


def test_marker_count_equals_unique_coordinate_bearing_airports(tmp_path):
    """Mission #26J Part K: representative multi-airport fixture - marker
    count must equal the number of unique Airports with coordinates, not
    the number of Installation rows or countries."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport A", country="France",
            latitude=-20.8871, longitude=55.5103,
            installations=[{"type": "greenEMAS"}],
        )
        _seed_installation_airport(
            session, name="Test Airport B", country="France",
            latitude=-12.8047, longitude=45.2811,
            installations=[
                {"type": "greenEMAS", "runway_end": "09"},
                {"type": "greenEMAS", "runway_end": "27"},
            ],
        )
        _seed_installation_airport(
            session, name="Test Airport C No Coords", country="France",
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    # 2 airports have coordinates (A, B) -> 2 markers, regardless of B's 2
    # Installation rows or the fact all 3 share one country.
    assert section.count('class="map-node map-node-installed"') == 2


def test_marker_has_accessible_title(tmp_path):
    """Mission #26J Part N: each marker carries a real, non-empty <title>
    naming the Airport - readable by assistive technology without hover,
    matching the pre-existing per-country marker's own accessibility
    precedent."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Accessible Airport", country="Germany",
            latitude=49.2146, longitude=7.1095,
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    m = re.search(r'<circle[^>]*class="map-node map-node-installed">\s*<title>(.*?)</title>', section, re.S)
    assert m is not None
    assert "Test Accessible Airport" in m.group(1)


def test_no_hardcoded_run_dza_special_case_in_source():
    """Mission #26J Part Q/R: RUN and DZA must emerge from GENERIC
    airport-coordinate rendering, never a special-cased literal anywhere
    in the modified view function."""
    full_source = inspect.getsource(build._installed_base_global_view)
    body = re.sub(r'""".*?"""', "", full_source, count=1, flags=re.S)
    for forbidden in ("RUN", "DZA", "Roland Garros", "Dzaoudzi", "Réunion", "Mayotte"):
        assert forbidden not in body


# ---------------------------------------------------------------------------
# No-JS: both datasets exist in the generated static HTML
# ---------------------------------------------------------------------------


def test_both_modes_fully_present_in_static_html_no_js_required(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        _seed_installation_airport(
            session, name="Test Airport", country="Brazil",
            installations=[{"type": "greenEMAS", "confirmed_vendor": "Runway Safe"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="footprint-panel footprint-panel-installed"' in html
    assert 'class="footprint-panel footprint-panel-current"' in html
    assert "Test Airport" in html  # Installed Base content present
    assert re.search(r'map-legend-country">USA<', html)  # Current Intelligence content present
    assert 'id="footprint-mode-installed"' in html and "checked" in html
    assert 'type="radio"' in html


def test_installed_base_default_checked(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<input type="radio" name="footprint-mode" id="footprint-mode-installed" class="footprint-radio"( checked)?>', html)
    assert m is not None and m.group(1) == " checked"


# ---------------------------------------------------------------------------
# Current Intelligence semantics unchanged
# ---------------------------------------------------------------------------


def test_current_intelligence_map_still_renders_unchanged(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="global-map"' in html
    assert re.search(r'\d+ signaler', html)


def test_data_json_unchanged_in_scope(tmp_path):
    """V1 is template-rendered only - no new public JSON surface."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Brazil",
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    assert "installed_base_global" not in data
    assert "footprint" not in json.dumps(data).lower()


# ---------------------------------------------------------------------------
# LCY acceptance case (Mission #23C Part O)
# ---------------------------------------------------------------------------


def test_lcy_shaped_acceptance(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test London City Airport", country="United Kingdom", iata="LCY", icao="EGLC",
            installations=[
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "09", "confirmed_vendor": "Runway Safe"},
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "27", "confirmed_vendor": "Runway Safe"},
            ],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "United Kingdom" in section
    assert "1 flygplats med dokumenterad EMAS" in section
    assert "Bana 09 och 27" in section
    assert "EMASMAX" in section
    assert "Installerad 2023" in section
    assert "Bekräftad leverantör: Runway Safe" in section
    assert "Dokumenterad installation enligt tillgängliga källor" in section
    # zero Signal for this airport -> absent from Current Intelligence
    assert "Ingen aktivitetsdata ännu." in html


# ---------------------------------------------------------------------------
# Country drill-down (Mission #24B): native <details>/<summary>, collapsed
# by default, no JavaScript. All assertions below run against a build with
# TWO countries so the "collapsed by default" invariant is exercised on
# more than a single lucky element.
# ---------------------------------------------------------------------------


def _build_two_country_site(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)  # gives Current Intelligence real content too
        _seed_installation_airport(
            session, name="Test London City Airport", country="United Kingdom", iata="LCY", icao="EGLC",
            installations=[
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "09", "confirmed_vendor": "Runway Safe"},
                {"type": "EMASMAX", "install_year": 2023, "runway_end": "27", "confirmed_vendor": "Runway Safe"},
            ],
        )
        _seed_installation_airport(
            session, name="Test RAF Northolt", country="United Kingdom", iata=None, icao="EGWU",
            installations=[{"type": "greenEMAS", "install_year": 2019, "confirmed_vendor": "Runway Safe"}],
        )
        _seed_installation_airport(
            session, name="Test Congonhas", country="Brazil",
            installations=[{"type": "greenEMAS", "install_year": 2022}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 3))
    return (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def test_each_country_renders_as_details_element(tmp_path):
    html = _build_two_country_site(tmp_path)
    section = _footprint_installed_section(html)
    tags = re.findall(r'<details[^>]*class="footprint-country"[^>]*>', section)
    assert len(tags) == 2  # United Kingdom, Brazil


def test_countries_collapsed_by_default_no_open_attribute(tmp_path):
    html = _build_two_country_site(tmp_path)
    section = _footprint_installed_section(html)
    tags = re.findall(r'<details[^>]*class="footprint-country"[^>]*>', section)
    assert len(tags) == 2
    for tag in tags:
        assert "open" not in tag


def test_each_country_has_summary_header(tmp_path):
    html = _build_two_country_site(tmp_path)
    section = _footprint_installed_section(html)
    assert section.count('<summary class="footprint-country-header">') == 2


def test_summary_still_contains_country_name_and_airport_count(tmp_path):
    html = _build_two_country_site(tmp_path)
    section = _footprint_installed_section(html)
    m = re.search(
        r'<summary class="footprint-country-header">(.*?)</summary>',
        section, re.S,
    )
    assert m is not None
    # first summary in document order is the larger country (UK, 2 airports) -
    # same "-len(entries), country name" ordering _installed_base_global_view
    # already used pre-#24B; unchanged by this mission.
    first_summary = m.group(1)
    assert "United Kingdom" in first_summary
    assert "2 flygplatser med dokumenterad EMAS" in first_summary


def test_airport_row_shows_no_marker_note_when_coordinates_missing(tmp_path):
    """Mission #26J: the "no map position" note moved from the country
    <summary> header (retired - country headers no longer have their own
    map position at all) onto the individual airport <li> that actually
    lacks coordinates."""
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Testlandia",
            installations=[{"type": "greenEMAS"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 3))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    m = re.search(r'<summary class="footprint-country-header">(.*?)</summary>', section, re.S)
    assert "ingen kartposition ännu" not in m.group(1)  # country header no longer carries this note
    li_blocks = re.findall(r"<li>.*?</li>", section, re.S)
    matching = [li for li in li_blocks if "Test Airport" in li]
    assert len(matching) == 1
    assert "ingen kartposition ännu" in matching[0]


def test_airport_list_present_in_raw_html_even_though_collapsed(tmp_path):
    """No `open` attribute means the browser hides the <ul> visually, but
    the static HTML file itself must still contain the full airport list -
    the whole point of using <details> instead of e.g. a JS-populated
    fetch: nothing is missing from the page source."""
    html = _build_two_country_site(tmp_path)
    section = _footprint_installed_section(html)
    assert "Test RAF Northolt" in section
    assert "Test Congonhas" in section
    assert 'class="footprint-airport-list"' in section
    assert section.count("<li>") == 3  # LCY + RAF Northolt + Congonhas


def test_lcy_appears_exactly_once_as_airport_row_under_uk(tmp_path):
    """LCY's own name legitimately appears twice in its one <li> (once in
    the airport_label() badge's title attribute, once as the visible link
    text) - the real per-airport-row invariant is exactly one <li>, not a
    bare substring count."""
    html = _build_two_country_site(tmp_path)
    section = _footprint_installed_section(html)
    assert section.count("Test London City Airport") == 2  # title attr + link text, both inside ONE <li>
    li_blocks = re.findall(r"<li>.*?</li>", section, re.S)
    lcy_rows = [li for li in li_blocks if "Test London City Airport" in li]
    assert len(lcy_rows) == 1


def test_current_intelligence_unaffected_by_details_change(tmp_path):
    html = _build_two_country_site(tmp_path)
    # Mode 2 markup must remain the pre-#24B plain div/legend structure -
    # no <details>/<summary> introduced there. Anchored on the start of the
    # NEXT top-level card (Utveckling), the same "next sibling" anchoring
    # style _footprint_installed_section already uses.
    m = re.search(
        r'<div class="footprint-panel footprint-panel-current">(.*?)'
        r'(?=<div class="card panel-strong">)',
        html, re.S,
    )
    assert m is not None
    current_section = m.group(0)
    assert "<details" not in current_section
    assert "<summary" not in current_section
    assert 'class="map-legend"' in current_section


def test_no_javascript_dependency_introduced_by_drilldown(tmp_path):
    html = _build_two_country_site(tmp_path)
    assert "<script" not in html


def test_no_notes_leakage_with_drilldown_structure(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Airport", country="Brazil",
            installations=[{"type": "greenEMAS", "notes": "INTERNAL-ANALYST-SHORTHAND-24b"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 3))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "INTERNAL-ANALYST-SHORTHAND-24b" not in html
