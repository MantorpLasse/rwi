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
    session, *, name, country, iata=None, icao=None, installations: "list[dict]"
) -> Airport:
    """Minimal Installation-bearing airport, no Signal, no Runway - the
    exact shape a documented-historical-only airport (like the real LCY)
    has today. Each dict in `installations` may set: runway_end, type,
    install_year, confirmed_vendor, notes."""
    airport = Airport(name=name, country=country, iata_code=iata, icao_code=icao)
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


def test_switzerland_now_has_a_map_position(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_installation_airport(
            session, name="Test Zurich", country="Switzerland",
            installations=[{"type": "greenEMAS", "confirmed_vendor": "Runway Safe"}],
        )
        build_site(tmp_path / "site", session=session, today=date(2026, 9, 2))
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = _footprint_installed_section(html)
    assert "ingen kartposition ännu" not in section
    assert 'class="map-node map-node-installed"' in html


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
