"""RWI HQ "Public UX Simplification - Slice B: Global Footprint Incident
Mode" mission: focused tests for the third Global Footprint mode
("Incidenter"/"Incidents") - built entirely from governed Incident domain
rows, never from Signal/category == "replacement_after_incident", never
from Signal.lifecycle_state.

Every test uses a synthetic, isolated in-memory SQLite database - never
the real data/runway_safe.db. Fixtures (`_seed_bos_shaped`, `_engine`) are
imported from tests/test_static_export_design_v2.py where useful, matching
this repository's own established convention.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import date

from sqlalchemy.orm import Session

from app.models import Airport, Incident, Signal, Source
from app.static_export import build
from app.static_export import build_site
from app.static_export.build import _incident_map_view
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped

TODAY = date(2026, 9, 14)


def _seed_incident_airport(session, *, name, country, iata=None, icao=None, incidents: "list[dict]", latitude=None, longitude=None) -> Airport:
    airport = Airport(name=name, country=country, iata_code=iata, icao_code=icao, latitude=latitude, longitude=longitude)
    session.add(airport)
    session.commit()
    for kwargs in incidents:
        session.add(Incident(airport_id=airport.id, **kwargs))
    session.commit()
    return airport


def _incidents_panel(html: str) -> str:
    m = re.search(
        r'<div class="footprint-panel footprint-panel-incidents">(.*?)(?=</div>\s*</div>\s*</div>\s*<div class="card panel-strong">)',
        html, re.S,
    )
    assert m is not None, "expected the Incidents panel to be present in the generated HTML"
    return m.group(0)


# --- 1/2: third mode exists in both locales --------------------------------


class TestThirdModeExists:
    def test_third_mode_exists_in_swedish(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Test Incident Airport", country="USA", iata="TIA",
                latitude=42.0, longitude=-71.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert 'id="footprint-mode-incidents"' in html
        assert 'class="footprint-panel footprint-panel-incidents"' in html
        assert "Incidenter" in html

    def test_third_mode_exists_in_english(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Test Incident Airport", country="USA", iata="TIA",
                latitude=42.0, longitude=-71.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "en" / "index.html").read_text(encoding="utf-8")
        assert 'id="footprint-mode-incidents"' in html
        assert 'class="footprint-panel footprint-panel-incidents"' in html
        assert "Incidents" in html


# --- 3/4: existing two modes unchanged --------------------------------------


class TestExistingModesUnchanged:
    def test_installed_base_mode_still_present_and_functional(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_bos_shaped(session)
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert 'class="footprint-panel footprint-panel-installed"' in html
        assert 'id="footprint-mode-installed"' in html and "checked" in html

    def test_current_intelligence_mode_still_present_and_functional(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_bos_shaped(session)
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert 'class="footprint-panel footprint-panel-current"' in html
        assert 'class="map-legend"' in html


# --- 5/6/7: aggregation by Airport ------------------------------------------


class TestAggregationByAirport:
    def test_single_incident_airport_produces_exactly_one_marker(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Single Incident Field", country="USA", iata="SIF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert len(re.findall(r'class="map-node map-node-incident"', panel)) == 1

    def test_multi_incident_airport_still_produces_exactly_one_marker(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Multi Incident Field", country="USA", iata="MIF",
                latitude=41.0, longitude=-80.0,
                incidents=[
                    {"incident_date": date(1999, 1, 1), "incident_type": "Overrun", "emas_engaged": True},
                    {"incident_date": date(2005, 5, 5), "incident_type": "Excursion", "emas_engaged": False},
                    {"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True},
                ],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert len(re.findall(r'class="map-node map-node-incident"', panel)) == 1

    def test_incident_count_correct_for_aggregated_airport(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_incident_airport(
                session, name="Multi Incident Field", country="USA", iata="MIF",
                latitude=41.0, longitude=-80.0,
                incidents=[
                    {"incident_date": date(1999, 1, 1), "incident_type": "Overrun", "emas_engaged": True},
                    {"incident_date": date(2005, 5, 5), "incident_type": "Excursion", "emas_engaged": False},
                    {"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True},
                ],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert "3 incidenter" in html
        m = re.search(r'MIF[^<]*</span>[^(]*\([^)]*\) — (\d+) incident', html)
        assert m is not None
        assert m.group(1) == "3"


# --- 8: individual incidents represented in drilldown -----------------------


class TestIndividualIncidentsInDrilldown:
    def test_each_incident_listed_in_textual_drilldown(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_incident_airport(
                session, name="Multi Incident Field", country="USA", iata="MIF",
                latitude=41.0, longitude=-80.0,
                incidents=[
                    {"incident_date": date(1999, 1, 1), "incident_type": "Overrun", "emas_engaged": True},
                    {"incident_date": date(2005, 5, 5), "incident_type": "Excursion", "emas_engaged": False},
                ],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert "1999-01-01" in panel
        assert "Overrun" in panel
        assert "2005-05-05" in panel
        assert "Excursion" in panel


# --- 9: marker count == distinct airports, not Incident rows ---------------


class TestMarkerCountIsPerAirportNotPerIncident:
    def test_marker_count_equals_distinct_incident_bearing_airports(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Airport One", country="USA", iata="AO1", latitude=40.0, longitude=-90.0,
                incidents=[
                    {"incident_date": date(1999, 1, 1), "incident_type": "Overrun", "emas_engaged": True},
                    {"incident_date": date(2005, 5, 5), "incident_type": "Excursion", "emas_engaged": False},
                ],
            )
            _seed_incident_airport(
                session, name="Airport Two", country="USA", iata="AO2", latitude=41.0, longitude=-80.0,
                incidents=[{"incident_date": date(2010, 1, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        # 2 distinct incident-bearing airports, 3 total Incident rows.
        assert len(re.findall(r'class="map-node map-node-incident"', panel)) == 2


# --- 10/11: no-coordinate Airport --------------------------------------------


class TestNoCoordinateFallback:
    def test_no_coordinate_airport_produces_no_fabricated_marker(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="No Coordinate Field", country="Brazil", iata="NCF",
                latitude=None, longitude=None,
                incidents=[{"incident_date": date(2010, 1, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert len(re.findall(r'class="map-node map-node-incident"', panel)) == 0

    def test_no_coordinate_airport_incident_info_still_accessible_in_textual_fallback(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="No Coordinate Field", country="Brazil", iata="NCF",
                latitude=None, longitude=None,
                incidents=[{"incident_date": date(2010, 1, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert "No Coordinate Field" in panel
        assert "2010-01-01" in panel
        assert "ingen kartposition ännu" in panel  # existing footprint_no_marker fallback text, reused verbatim


# --- 12: no dependency on Signal.category or Signal lifecycle --------------


class TestNoSignalDependency:
    def test_incident_map_view_signature_takes_only_airport_views(self):
        sig = inspect.signature(_incident_map_view)
        params = list(sig.parameters)
        assert params == ["airport_views"]

    def test_incident_map_view_source_never_references_signal(self):
        source = inspect.getsource(_incident_map_view)
        assert "Signal" not in source
        assert "lifecycle" not in source
        assert "category" not in source

    def test_incident_appears_regardless_of_signal_publication_state(self, tmp_path):
        """The Incident insert's own event listener auto-creates an
        unpublished companion Signal (app/models/incident.py) - the
        Incident must still appear on the map/drilldown regardless of
        that Signal ever being published."""
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Unpublished Signal Field", country="USA", iata="USF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            # Confirm the auto-created Signal really is unpublished, so this
            # test actually exercises the claim above.
            auto_signal = session.query(Signal).filter(Signal.category == "replacement_after_incident").one()
            assert auto_signal.published is False
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert "Unpublished Signal Field" in panel
        assert len(re.findall(r'class="map-node map-node-incident"', panel)) == 1


# --- 13: no duplicate markers from incident-derived Signals -----------------


class TestNoDuplicateMarkersFromIncidentDerivedSignals:
    def test_published_incident_derived_signal_does_not_duplicate_marker(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_incident_airport(
                session, name="Dup Check Field", country="USA", iata="DCF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            # Publish the auto-created companion Signal - it must not add a
            # second marker for the same Airport.
            auto_signal = session.query(Signal).filter(Signal.category == "replacement_after_incident").one()
            auto_signal.published = True
            session.commit()
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert len(re.findall(r'class="map-node map-node-incident"', panel)) == 1


# --- 14: Airport detail Incident section still builds/reachable -----------


class TestAirportDetailIncidentSectionUnaffected:
    def test_airport_detail_incidents_section_still_builds(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_incident_airport(
                session, name="Detail Check Field", country="USA", iata="DTF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
        assert 'id="incident-1"' in html
        assert "2021-06-01" in html
        assert "Overrun" in html

    def test_incident_map_links_to_real_airport_detail_anchor(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_incident_airport(
                session, name="Anchor Check Field", country="USA", iata="ACF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        assert f'href="./airports/{airport.id}.html#incident-1"' in panel


# --- 15: bilingual structure/data parity ------------------------------------


class TestBilingualParity:
    def test_incident_marker_and_drilldown_data_identical_across_locales(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_incident_airport(
                session, name="Parity Field", country="USA", iata="PFD",
                latitude=40.0, longitude=-90.0,
                incidents=[
                    {"incident_date": date(1999, 1, 1), "incident_type": "Overrun", "emas_engaged": True},
                    {"incident_date": date(2021, 6, 1), "incident_type": "Excursion", "emas_engaged": False},
                ],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        sv_html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        en_html = (tmp_path / "site" / "en" / "index.html").read_text(encoding="utf-8")
        sv_panel, en_panel = _incidents_panel(sv_html), _incidents_panel(en_html)
        assert len(re.findall(r'class="map-node map-node-incident"', sv_panel)) == 1
        assert len(re.findall(r'class="map-node map-node-incident"', en_panel)) == 1
        for html in (sv_panel, en_panel):
            assert "1999-01-01" in html
            assert "2021-06-01" in html
            assert "Overrun" in html
            assert "Excursion" in html
        # Labels differ, data does not.
        assert "Incidenter" in sv_html and "Incidents" in en_html
        assert "Historiska incidenter" in sv_html
        assert "Historical incidents" in en_html


# --- 16: data.json schema unchanged -----------------------------------------


class TestDataJsonSchemaUnchanged:
    def test_data_json_schema_unchanged_by_incident_map(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Schema Check Field", country="USA", iata="SCF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
        assert set(data.keys()) == {"generated_at", "airports", "signals"}


# --- No current-opportunity implication in copy -----------------------------


class TestCopyDoesNotImplyCurrentOpportunity:
    def test_incident_mode_copy_avoids_opportunity_language(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_incident_airport(
                session, name="Copy Check Field", country="USA", iata="CCF",
                latitude=40.0, longitude=-90.0,
                incidents=[{"incident_date": date(2021, 6, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        panel = _incidents_panel(html)
        for forbidden in ("möjlighet", "projekt", "opportunity", "project", "vinst", "win"):
            assert forbidden.lower() not in panel.lower()


# --- Existing two-mode installed-base test parity, three-mode aware --------


class TestExistingInstalledBaseAndCurrentIntelligenceCoexistWithIncidents:
    def test_all_three_modes_present_simultaneously_in_generated_html(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_bos_shaped(session)
            _seed_incident_airport(
                session, name="Coexist Field", country="Brazil", iata="COF",
                latitude=None, longitude=None,
                incidents=[{"incident_date": date(2010, 1, 1), "incident_type": "Overrun", "emas_engaged": True}],
            )
            build_site(tmp_path / "site", session=session, today=TODAY)
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert 'class="footprint-panel footprint-panel-installed"' in html
        assert 'class="footprint-panel footprint-panel-current"' in html
        assert 'class="footprint-panel footprint-panel-incidents"' in html
