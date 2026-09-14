"""RWI HQ "Public UX Simplification - Slice A: Default Signals View
Relevance" mission: focused tests proving the public /signals/ page's
default (no-JS) rendered state shows only active_opportunity/
developing_watch rows, while stale_unresolved/realized_historical rows
remain fully present in the underlying page/export and reachable through
the existing lifecycle filter - never deleted, never lifecycle-reclassified.

Every test uses an isolated in-memory database, matching this repository's
own established convention.
"""
from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source
from app.static_export import build_site

TODAY = date(2026, 9, 14)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_all_four_lifecycle_states(session: Session):
    airport = Airport(name="Test Field", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    source = Source(
        title="Original Source Title", source_type="master_plan", reliability_level="official",
        url="https://example.test/source", publisher="Example Publisher",
    )
    session.add(source)
    session.flush()

    active = Signal(
        airport_id=airport.id, source_id=source.id, title="Active opportunity signal",
        category="replacement", confidence="high", status="procurement",
        planning_year=2027, probability_score=8.0, published=True,
    )
    watch = Signal(
        airport_id=airport.id, source_id=source.id,
        title="Watch incident signal (2024-01-01)",
        category="replacement_after_incident", confidence="high", status="identified",
        probability_score=6.0, published=True,
    )
    stale = Signal(
        airport_id=airport.id, source_id=source.id,
        title="Stale incident signal (2010-01-01)",
        category="replacement_after_incident", confidence="high", status="identified",
        probability_score=7.0, published=True,
        source_notes="Ursprunglig svensk källtext, ska aldrig översättas.",
    )
    historical = Signal(
        airport_id=airport.id, source_id=source.id, title="Realized historical signal",
        category="replacement", confidence="high", status="completed",
        probability_score=5.0, published=True,
    )
    session.add_all([active, watch, stale, historical])
    session.commit()
    return airport, active, watch, stale, historical


def _build(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, active, watch, stale, historical = _seed_all_four_lifecycle_states(session)
        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)
        return output, active.id, watch.id, stale.id, historical.id


def _row(html: str, signal_id: int) -> str:
    m = re.search(rf'<tr[^>]*data-signal-ids="{signal_id}"[^>]*>', html, re.S)
    assert m is not None, f"no row found for signal {signal_id}"
    return m.group(0)


# --- 1/2: active_opportunity and developing_watch visible by default -------


class TestCurrentRowsVisibleByDefault:
    def test_active_opportunity_row_has_no_hidden_attribute(self, tmp_path):
        output, active_id, _, _, _ = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        assert "hidden" not in _row(html, active_id)

    def test_developing_watch_row_has_no_hidden_attribute(self, tmp_path):
        output, _, watch_id, _, _ = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        assert "hidden" not in _row(html, watch_id)


# --- 3/4: stale_unresolved and realized_historical hidden by default -------


class TestHistoricalStaleRowsHiddenByDefault:
    def test_stale_unresolved_row_is_hidden_by_default(self, tmp_path):
        output, _, _, stale_id, _ = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        row = _row(html, stale_id)
        assert "hidden" in row
        assert 'data-lifecycle="stale_unresolved"' in row

    def test_realized_historical_row_is_hidden_by_default(self, tmp_path):
        output, _, _, _, historical_id = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        row = _row(html, historical_id)
        assert "hidden" in row
        assert 'data-lifecycle="realized_historical"' in row


# --- 5: hidden rows remain reachable via the existing filter UI ------------


class TestHiddenRowsStillReachableViaFilters:
    def test_hidden_rows_still_have_full_row_markup_and_lifecycle_data(self, tmp_path):
        """The rows are hidden (native `hidden` attribute), never removed -
        every cell/link/data attribute the client-side lifecycle filter
        needs to reveal them on demand is still present verbatim."""
        output, _, _, stale_id, historical_id = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        for signal_id, title in ((stale_id, "Stale incident signal"), (historical_id, "Realized historical signal")):
            row = _row(html, signal_id)
            assert title in html  # title text is rendered, not stripped
            assert f'href="../signals/{signal_id}.html"' in html
            assert "data-search=" in row
            assert "data-status=" in row

    def test_lifecycle_filter_select_and_options_unchanged(self, tmp_path):
        output, *_ = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        assert '<option value="current" selected>' in html
        assert '<option value="historical">' in html
        assert '<option value="stale">' in html
        assert '<option value="">' in html

    def test_lifecycle_counts_bar_still_reflects_all_four_states(self, tmp_path):
        """The real, computed per-lifecycle-state counts (the compact
        legend above the table) must still count every published Signal -
        this mission changes default row VISIBILITY only, never what is
        counted/reported. One extra zero-count row for the defensive
        "other" state (0/4 real signals here) is pre-existing,
        unrelated-to-this-mission behavior of _lifecycle_counts_view()."""
        output, *_ = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        stats = re.search(r'<div class="lifecycle-stats">.*?</div>\s*<div class="filters">', html, re.S).group(0)
        counts = [int(c) for c in re.findall(r'lifecycle-stat-count">(\d+)<', stats)]
        assert counts.count(1) == 4
        assert sum(counts) == 4


# --- 6: detail pages for hidden-by-default rows still build ----------------


class TestHiddenRowDetailPagesStillReachable:
    def test_stale_and_historical_signal_detail_pages_still_build(self, tmp_path):
        output, _, _, stale_id, historical_id = _build(tmp_path)
        assert (output / "signals" / f"{stale_id}.html").exists()
        assert (output / "signals" / f"{historical_id}.html").exists()
        stale_html = (output / "signals" / f"{stale_id}.html").read_text(encoding="utf-8")
        assert "Stale incident signal" in stale_html


# --- Market page unaffected (signal_row() opt-in only, not global) ---------


class TestMarketPageStaleSectionUnaffected:
    def test_market_page_stale_section_row_is_not_hidden(self, tmp_path):
        """signal_row()'s new `apply_default_lifecycle_filter` flag
        defaults to False and is only passed True from signals_list.html -
        Market page's own dedicated stale_unresolved ("Behöver research")
        section must keep showing its own rows, unaffected."""
        output, _, _, stale_id, _ = _build(tmp_path)
        html = (output / "marknadslage.html").read_text(encoding="utf-8")
        row = _row(html, stale_id)
        assert "hidden" not in row

    def test_market_page_active_and_watch_rows_still_render(self, tmp_path):
        output, active_id, watch_id, _, _ = _build(tmp_path)
        html = (output / "marknadslage.html").read_text(encoding="utf-8")
        assert "hidden" not in _row(html, active_id)
        assert "hidden" not in _row(html, watch_id)


# --- 7: Swedish/English parity ----------------------------------------------


class TestBilingualParity:
    def test_default_visibility_identical_in_both_locales(self, tmp_path):
        output, active_id, watch_id, stale_id, historical_id = _build(tmp_path)
        sv_html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "signals" / "index.html").read_text(encoding="utf-8")
        for html in (sv_html, en_html):
            assert "hidden" not in _row(html, active_id)
            assert "hidden" not in _row(html, watch_id)
            assert "hidden" in _row(html, stale_id)
            assert "hidden" in _row(html, historical_id)


# --- 8: no Signal/DB mutation, no lifecycle value changes ------------------


class TestNoMutation:
    def test_underlying_lifecycle_values_and_signal_rows_unchanged(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, active, watch, stale, historical = _seed_all_four_lifecycle_states(session)
            ids = (active.id, watch.id, stale.id, historical.id)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            reloaded = [session.get(Signal, i) for i in ids]
            assert [s.status for s in reloaded] == ["procurement", "identified", "identified", "completed"]
            assert [s.category for s in reloaded] == [
                "replacement", "replacement_after_incident", "replacement_after_incident", "replacement",
            ]
            assert all(s.published is True for s in reloaded)


# --- 9: no data.json schema change ------------------------------------------


class TestDataJsonUnchanged:
    def test_data_json_still_lists_all_four_signals_with_unchanged_schema(self, tmp_path):
        output, active_id, watch_id, stale_id, historical_id = _build(tmp_path)
        data = json.loads((output / "data.json").read_text(encoding="utf-8"))
        assert set(data.keys()) == {"generated_at", "airports", "signals"}
        ids_present = {s["id"] for s in data["signals"]}
        assert ids_present == {active_id, watch_id, stale_id, historical_id}
        lifecycle_by_id = {s["id"]: s["lifecycle_state"] for s in data["signals"]}
        assert lifecycle_by_id[active_id] == "active_opportunity"
        assert lifecycle_by_id[watch_id] == "developing_watch"
        assert lifecycle_by_id[stale_id] == "stale_unresolved"
        assert lifecycle_by_id[historical_id] == "realized_historical"


# --- 10: no regression to search/watch/filter behavior ----------------------


class TestExistingBehaviorUnregressed:
    def test_watch_star_toggle_and_search_scaffolding_still_present(self, tmp_path):
        output, *_ = _build(tmp_path)
        html = (output / "signals" / "index.html").read_text(encoding="utf-8")
        assert 'class="star-toggle"' in html
        assert 'id="q"' in html
        assert 'id="status-filter"' in html
        assert 'id="country-filter"' in html
        assert 'id="watched-only"' in html
        assert 'src="../watch.js"' in html

    def test_evidence_and_source_content_unaffected(self, tmp_path):
        output, _, _, stale_id, _ = _build(tmp_path)
        html = (output / "signals" / f"{stale_id}.html").read_text(encoding="utf-8")
        assert "Ursprunglig svensk källtext, ska aldrig översättas." in html
        assert "Original Source Title" in html
