"""SLT1 (docs/architecture/rwi-signal-temporal-relevance-opportunity-
lifecycle-design.md): integration-level tests for the static-export wiring -
sorting, grouping, filtering, publication firewall, and no-row-loss, all
against the real Jinja templates via app.static_export.build_site(). Every
test uses an isolated in-memory database (never the real
data/runway_safe.db) and a fixed `today`, matching this repository's own
existing test_static_export.py convention.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source
from app.static_export import build_site

TODAY = date(2026, 8, 28)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_signal_view_exposes_lifecycle_fields_in_data_json(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        signal = Signal(
            airport=airport, title="Future EMAS project", category="new_installation",
            confidence="planned", status="alp", planning_year=2027, probability_score=8.5,
        )
        session.add(signal)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    view = data["signals"][0]
    assert view["lifecycle_state"] == "active_opportunity"
    assert view["lifecycle_label"] == "Aktuell möjlighet"
    assert "lifecycle_reason" in view
    # probability_score/confidence/status/category are completely untouched.
    assert view["probability_score"] == 8.5
    assert view["confidence"] == "planned"
    assert view["status"] == "alp"
    assert view["category"] == "new_installation"


def test_stale_signal_never_outranks_active_signal_despite_higher_score(tmp_path):
    """The core dashboard defect this mission fixes: a same-or-higher-scored
    stale/historical signal must sort AFTER a lower-scored active one."""
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        stale = Signal(
            airport=airport,
            title="Test Airport — EMAS-ersättning väntas efter incident (2006-07-01)",
            category="replacement_after_incident", confidence="high", status="identified",
            probability_score=8.0,
        )
        active = Signal(
            airport=airport, title="Runway EMAS replacement", category="replacement",
            confidence="planned", status="procurement", planning_year=2026, probability_score=7.0,
        )
        session.add_all([stale, active])
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    ids_in_order = [s["title"] for s in data["signals"]]
    assert ids_in_order == ["Runway EMAS replacement", "Test Airport — EMAS-ersättning väntas efter incident (2006-07-01)"]
    assert data["signals"][0]["lifecycle_state"] == "active_opportunity"
    assert data["signals"][1]["lifecycle_state"] == "stale_unresolved"
    # Scores are preserved exactly as stored - never redefined by lifecycle.
    assert data["signals"][0]["probability_score"] == 7.0
    assert data["signals"][1]["probability_score"] == 8.0


def test_signal_sort_is_deterministic_for_tied_score_and_tier(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        a = Signal(airport=airport, title="A", category="unknown", confidence="high", probability_score=8.0)
        b = Signal(airport=airport, title="B", category="unknown", confidence="high", probability_score=8.0)
        session.add_all([a, b])
        session.commit()
        ids = (a.id, b.id)

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)
        output2 = tmp_path / "site2"
        build_site(output2, session=session, today=TODAY)

    data1 = json.loads((output / "data.json").read_text(encoding="utf-8"))
    data2 = json.loads((output2 / "data.json").read_text(encoding="utf-8"))
    order1 = [s["id"] for s in data1["signals"]]
    order2 = [s["id"] for s in data2["signals"]]
    assert order1 == order2 == sorted(ids)


def test_each_signal_at_the_same_airport_and_category_keeps_its_own_lifecycle_state(tmp_path):
    """("RWI - Mission #7E" mission) Multiple replacement_after_incident
    signals at the same airport (one old, one recent) no longer collapse
    into one row (the former airport+category presentation grouping was
    removed - see Mission #7D's own recon and Mission #7E's report). Each
    Signal must render on its own row, carrying its own real
    data-lifecycle value, so each remains independently reachable from its
    own filter view - the exact invariant the old grouprow's own
    multi-token data-lifecycle used to protect, now satisfied trivially by
    each Signal owning its own row."""
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="PWK Test", iata_code="PWK", country="USA")
        old = Signal(
            airport=airport, title="PWK Test — EMAS-ersättning väntas efter incident (2016-01-01)",
            category="replacement_after_incident", confidence="high", status="identified", probability_score=8.0,
        )
        recent = Signal(
            airport=airport, title="PWK Test — EMAS-ersättning väntas efter incident (2025-09-01)",
            category="replacement_after_incident", confidence="high", status="identified", probability_score=8.0,
        )
        session.add_all([old, recent])
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)

    html = (output / "signals" / "index.html").read_text(encoding="utf-8")
    assert 'class="grouprow"' not in html
    old_row_start = html.rindex("<tr", 0, html.index(f'data-signal-ids="{old.id}"'))
    old_row_end = html.index("</tr>", old_row_start)
    assert f'data-lifecycle="stale_unresolved"' in html[old_row_start:old_row_end]
    recent_row_start = html.rindex("<tr", 0, html.index(f'data-signal-ids="{recent.id}"'))
    recent_row_end = html.index("</tr>", recent_row_start)
    assert f'data-lifecycle="developing_watch"' in html[recent_row_start:recent_row_end]


def test_lifecycle_counts_sum_to_published_signal_count(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        published = [
            Signal(airport=airport, title=f"Signal {i}", category="unknown", confidence="high", probability_score=8.0)
            for i in range(3)
        ]
        unpublished = Signal(
            airport=airport, title="Hidden", category="unknown", confidence="high",
            probability_score=8.0, published=False,
        )
        session.add_all([*published, unpublished])
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)

    html = (output / "signals" / "index.html").read_text(encoding="utf-8")
    import re
    counts = [int(m) for m in re.findall(r'lifecycle-stat-count">(\d+)<', html)]
    assert sum(counts) == 3  # the unpublished 4th signal is correctly excluded
    assert "Hidden" not in html


def test_unpublished_signal_never_appears_even_in_data_json(tmp_path):
    """Publication (Signal.published) and lifecycle are independent
    dimensions (design doc S5) - lifecycle wiring must never accidentally
    bypass the existing publication firewall Slice 9A already established."""
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        hidden = Signal(
            airport=airport, title="Hidden signal", category="new_installation",
            confidence="high", planning_year=2027, published=False,
        )
        session.add(hidden)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert data["signals"] == []
    assert not (output / "signals" / f"{hidden.id}.html").exists()


def test_signal_detail_page_shows_lifecycle_badge_and_reason(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        signal = Signal(
            airport=airport,
            title="Test Airport — EMAS-ersättning väntas efter incident (2006-07-01)",
            category="replacement_after_incident", confidence="high", status="identified",
            probability_score=8.0,
        )
        session.add(signal)
        session.commit()
        signal_id = signal.id

        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)

    html = (output / "signals" / f"{signal_id}.html").read_text(encoding="utf-8")
    assert "Behöver research" in html
    # The investor-facing Swedish tooltip explains what "needs research"
    # does and does not mean - the raw, English derivation reason
    # (lifecycle_reason) stays available on the view/data.json for testing
    # and future operator tooling, deliberately not mixed into the public
    # Swedish copy (matches presentation.py's own language-neutral-domain-
    # values-vs-Swedish-locale separation).
    assert "obekräftad" in html
    assert "8.0" in html  # score untouched and still shown


def test_build_site_default_today_is_real_current_date(tmp_path):
    """Omitting `today` falls back to date.today(), matching generated_at's
    own real-clock default - proven by a Signal whose lifecycle depends on
    which fixed date is used."""
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Airport", iata_code="TST", country="USA")
        signal = Signal(airport=airport, title="x", category="unknown", confidence="high", target_year=date.today().year + 5)
        session.add(signal)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)  # no today= override

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert data["signals"][0]["lifecycle_state"] == "active_opportunity"
