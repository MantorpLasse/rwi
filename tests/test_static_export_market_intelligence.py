"""Tests for "RWI - Mission #7C" (Market Intelligence "Marknadsläge" first
slice): the new marknadslage.html page, its top-level nav link, and the
read-only view models that feed it (`_market_intelligence_view`,
`_market_category_distribution_view` in app/static_export/build.py).

Deliberately reuses the existing SLT1 lifecycle derivation and the existing
`signal_row()` component verbatim - no new lifecycle logic, no new
Signal-row presentation system, no FH-D4 read, no value/supplier
aggregation. See Missions #7/#7A/#7B for the recon/design this
implementation follows.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_engine`) are imported from
tests/test_static_export_design_v2.py rather than duplicated. Deterministic
lifecycle-shaped fixtures are built locally in this file rather than
depending on the real database's current row counts (13/24/28/2), per this
mission's own explicit instruction not to couple tests to those numbers.
"""
from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Airport, Signal, Source
from app.static_export import build
from app.static_export import build_site
from tests.test_static_export_design_v2 import _engine

_TODAY = date(2026, 8, 30)


def _seed_market_shaped(session: Session) -> dict:
    """One Signal in each of the four real SLT1 states, plus one
    unpublished Signal - deterministic regardless of `_TODAY` drifting,
    matching the exact rule branches `signal_lifecycle.py` documents:

    - active: status in the committed-pipeline vocabulary with a
      current-year best_year (rule 5).
    - watch: category="replacement_watch" (rule 4 - explicit watch-track,
      regardless of any year).
    - stale: no recognized status/grant shape, only a stale bare year
      (rule 6 fallback).
    - historical: status="completed" (rule 1 - unambiguous).
    """
    airport = Airport(name="Market Test Field", iata_code="MTF", country="USA")
    session.add(airport); session.commit()
    source = Source(title="Market Test Source", source_type="news", url="https://example.test/market")
    session.add(source); session.commit()

    active = Signal(
        airport_id=airport.id, source_id=source.id, title="Active test signal",
        category="new_installation", confidence="high", status="funded",
        target_year=_TODAY.year, probability_score=8.0, published=True,
        estimated_total_value_usd=Decimal("12345678.00"), confirmed_vendor="Acme EMAS Co",
    )
    watch = Signal(
        airport_id=airport.id, source_id=source.id, title="Watch test signal",
        category="replacement_watch", confidence="medium", status="identified",
        probability_score=6.0, published=True,
    )
    stale = Signal(
        airport_id=airport.id, source_id=source.id, title="Stale test signal",
        category="study", confidence="low", status="identified",
        target_year=2015, probability_score=3.0, published=True,
    )
    historical = Signal(
        airport_id=airport.id, source_id=source.id, title="Historical test signal",
        category="new_installation", confidence="high", status="completed",
        probability_score=8.0, published=True,
    )
    unpublished = Signal(
        airport_id=airport.id, source_id=source.id, title="Unpublished test signal - must never leak",
        category="new_installation", confidence="high", status="funded",
        target_year=_TODAY.year, probability_score=8.0, published=False,
    )
    session.add_all([active, watch, stale, historical, unpublished])
    session.commit()
    return {
        "airport": airport, "active": active, "watch": watch,
        "stale": stale, "historical": historical, "unpublished": unpublished,
    }


def _market_html(tmp_path):
    return (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Page generation + navigation
# ---------------------------------------------------------------------------

def test_market_page_is_generated(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    assert (tmp_path / "site" / "marknadslage.html").exists()


def test_top_level_nav_links_to_market_page_on_every_page_type(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        seeded = _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    index_html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert '<a href="./marknadslage.html">Marknadsläge</a>' in index_html
    airport_html = (tmp_path / "site" / "airports" / f"{seeded['airport'].id}.html").read_text(encoding="utf-8")
    assert '<a href="../marknadslage.html">Marknadsläge</a>' in airport_html
    market_html = _market_html(tmp_path)
    assert '<a href="./marknadslage.html">Marknadsläge</a>' in market_html


# ---------------------------------------------------------------------------
# Lifecycle bucketing correctness
# ---------------------------------------------------------------------------

def test_active_opportunity_signal_appears_in_aktuella_mojligheter(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        seeded = _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    section = html.split("Aktuella möjligheter")[1].split("Under bevakning")[0]
    assert "Active test signal" in section
    assert "Watch test signal" not in section
    assert "Stale test signal" not in section
    assert "Historical test signal" not in section


def test_developing_watch_signal_appears_in_under_bevakning(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        seeded = _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    # "Under bevakning" is also the lifecycle chip label in the snapshot bar
    # above this section - split on the section header's own "(" count
    # suffix (unique to the actual <summary>/card-header, never the chip).
    section = html.split("Under bevakning (")[1].split("Behöver research (")[0]
    assert "Watch test signal" in section
    assert "Active test signal" not in section
    assert "Stale test signal" not in section


def test_stale_unresolved_signal_appears_in_behover_research(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        seeded = _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    # Same chip/header text collision as above - disambiguate the same way.
    section = html.split("Behöver research (")[1].split("Efter projekttyp")[0]
    assert "Stale test signal" in section
    assert "Active test signal" not in section
    assert "Watch test signal" not in section
    # honest research-backlog framing, never dead/inactive/rejected wording
    assert "död" not in section.lower()
    assert "inaktiv" not in section.lower()
    assert "avfärdad" not in section.lower()


def test_realized_historical_counted_in_snapshot_but_no_standalone_list(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        seeded = _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    # never appears anywhere on the page - only counted, never listed
    assert "Historical test signal" not in html
    # never appears in any of the three signal-row sections (redundant with
    # the whole-page check above, kept for an explicit per-section proof)
    for section_name, next_name in [
        ("Aktuella möjligheter", "Under bevakning"),
        ("Under bevakning", "Behöver research"),
        ("Behöver research", "Efter projekttyp"),
    ]:
        section = html.split(section_name)[1].split(next_name)[0]
        assert "Historical test signal" not in section
    # no fourth signal-row table exists for it
    assert html.count("<table>") == 3


# ---------------------------------------------------------------------------
# Efter projekttyp scoping
# ---------------------------------------------------------------------------

def test_project_type_distribution_uses_active_and_watch_only(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    section = html.split("Efter projekttyp")[1]
    # active=new_installation, watch=replacement_watch -> both categories present
    assert "Ny installation" in section
    assert "Ersättning – bevakas" in section
    # stale=study, historical=new_installation(completed) -> neither Signal counted here
    assert "Studie" not in section


def test_project_type_distribution_excludes_stale_and_historical(tmp_path):
    """category_distribution's own total must equal len(active)+len(watch),
    never len(active)+len(watch)+len(stale)+len(historical)."""
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    section = html.split("Efter projekttyp")[1]
    import re
    counts = [int(n) for n in re.findall(r'market-count">(\d+)', section)]
    assert sum(counts) == 2  # 1 active (new_installation) + 1 watch (replacement_watch)


# ---------------------------------------------------------------------------
# Reuse, not reimplementation
# ---------------------------------------------------------------------------

def test_lifecycle_derivation_is_reused_not_reimplemented():
    source = inspect.getsource(build._market_intelligence_view)
    assert "derive_signal_lifecycle" not in source  # consumes already-computed lifecycle_state, never re-derives
    assert "lifecycle_state ==" in source
    # Uses the existing snapshot view verbatim.
    assert "_lifecycle_counts_view(signal_views)" in source


def test_score_rendering_matches_mission_6_glossary_link(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    assert 'class="score gloss-link" href="./ordlista.html#score"' in html
    assert "Bedömning av underlagets styrka och tillförlitlighet" in html


# ---------------------------------------------------------------------------
# Publication boundary / forbidden exposure
# ---------------------------------------------------------------------------

def test_unpublished_signal_never_appears(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    assert "Unpublished test signal" not in html


def test_no_fh_d4_disposition_data_is_read_or_exposed(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    for forbidden in ("SignalDisposition", "SAME_REAL_WORLD_EFFORT", "DISTINCT", "disposition", "effort"):
        assert forbidden not in html
    # Structural: the two new Market Intelligence functions themselves never
    # import or reference the disposition mechanism (module-level comments
    # elsewhere in build.py legitimately *mention* it by name to document
    # its deliberate absence - checked here at the function-source level,
    # not the whole-module level, to avoid that false positive).
    for fn in (build._market_intelligence_view, build._market_category_distribution_view):
        source = inspect.getsource(fn)
        assert "SignalDisposition" not in source
        assert "disposition" not in source
    # No top-of-file import of the disposition module/model either.
    with open(build.__file__, encoding="utf-8") as f:
        head = f.read(4000)
    assert "signal_disposition" not in head


def test_no_value_or_supplier_data_exposed_even_when_present_on_signal(tmp_path):
    """The active fixture Signal deliberately carries a real
    estimated_total_value_usd and confirmed_vendor - proving the page
    doesn't leak the financial value fields or the analyst-judgment
    likely_supplier field (rather than merely proving an unpopulated field
    is absent).

    ("RWI - Mission #7J" mission) confirmed_vendor itself is no longer in
    that list: Mission #7I's own approved trigger #10 ("{confirmed_vendor}
    bekräftad som leverantör") deliberately, explicitly surfaces this exact
    sourced fact as a "Varför nu?" attention reason - see
    tests/test_static_export_attention_reasons.py for that mechanism's own
    dedicated coverage. This is a real, intentional supersession of this
    test's original invariant, not a weakening: the raw field name/estimate
    fields below remain correctly hidden."""
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    assert "12345678" not in html
    assert "12,345,678" not in html
    assert "Acme EMAS Co bekräftad som leverantör" in html  # Mission #7J's own approved trigger #10
    assert "confirmed_vendor" not in html  # raw field name never leaks, only the sourced fact
    assert "likely_supplier" not in html
    assert "estimated_total_value_usd" not in html
    assert "estimated_emas_value_usd" not in html


def test_no_governance_leakage(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    for forbidden in ("ATTACH_PROVISIONAL", "ATTACH_CONFIRMED", "HUMAN_REVIEW_REQUIRED", "ReviewerAction", "human:", "reviewer"):
        assert forbidden not in html


def test_data_json_not_expanded_by_market_view(tmp_path):
    import json
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    top_keys = set(data.keys())
    assert top_keys == {"generated_at", "airports", "signals"}  # no new "market" key added


# ---------------------------------------------------------------------------
# Empty-state behavior
# ---------------------------------------------------------------------------

def test_empty_state_when_no_published_signals_exist(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    assert "Inga aktuella möjligheter är publicerade just nu." in html
    assert "Inga signaler under bevakning just nu." in html
    assert "Inga signaler behöver förnyad research just nu." in html
    assert "Inga aktuella eller bevakade projekt att kategorisera just nu." in html
    assert html.count("<table>") == 0


# ---------------------------------------------------------------------------
# Structural markup / mobile safety (data-label attributes reused from the
# existing signal_row() macro, already covered by its own responsive CSS -
# no new structural pattern introduced).
# ---------------------------------------------------------------------------

def test_signal_rows_use_existing_data_label_responsive_pattern(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    assert 'data-label="Flygplats"' in html
    assert 'data-label="Signal"' in html
    assert 'data-label="Score"' in html  # signal_row's own cells already carry data-label for mobile stacking


def test_generated_signal_and_airport_links_resolve_within_export(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        seeded = _seed_market_shaped(session)
        build_site(tmp_path / "site", session=session, today=_TODAY)
    html = _market_html(tmp_path)
    import re
    for match in re.findall(r'href="(\./(?:signals|airports)/[^"]+\.html)"', html):
        target = tmp_path / "site" / match.lstrip("./")
        assert target.exists(), f"broken link: {match}"
