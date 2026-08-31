"""Tests for "RWI - Mission #7E" (Retire Naive Signal Grouping): the public
Signals list must show one row per published Signal, always - never a
synthetic collapsed row inheriting fields from one arbitrary member.

Mission #7D's own recon proved the removed `_group_signal_views()`
(airport_id, category) heuristic had no governed real-world-effort
semantics and could visually collapse Signals a human reviewer had
explicitly confirmed `DISTINCT` (7 of 13 real production groups). This file
proves the corrected behavior with the mission's own concrete acceptance
cases (MHT) plus synthetic FH-D4 DISTINCT/SAME_REAL_WORLD_EFFORT fixtures.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_engine`) are imported from
tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import Airport, Signal
from app.services.signal_disposition_persistence import record_signal_group_disposition
from app.static_export import build_site
from tests.test_static_export_design_v2 import _engine


def _row_for_signal_id(html: str, signal_id: int) -> str:
    marker = f'data-signal-ids="{signal_id}"'
    start = html.rindex("<tr", 0, html.index(marker))
    end = html.index("</tr>", start)
    return html[start:end]


def _mht_shaped_signals(session: Session) -> tuple[Signal, Signal]:
    """Mirrors the mission's own real MHT example shape - same airport,
    same category, distinct titles/status/Scores - never asserting
    anything about the real MHT Signal rows themselves."""
    airport = Airport(name="Test Manchester-Boston Regional", iata_code="TMH", country="USA")
    session.add(airport); session.commit()
    headline = Signal(
        airport_id=airport.id, title="Runway 6 departure-end EMAS replacement",
        category="replacement", confidence="confirmed", status="procurement",
        probability_score=10.0, published=True,
    )
    grant = Signal(
        airport_id=airport.id, title="USAspending grant — $7.9M, FY2026",
        category="replacement", confidence="high", status="identified",
        probability_score=8.0, published=True,
    )
    session.add_all([headline, grant]); session.commit()
    return headline, grant


def test_mht_shaped_signals_render_as_two_independent_rows(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")

    assert f'href="../signals/{headline.id}.html"' in html
    assert f'href="../signals/{grant.id}.html"' in html
    # No synthetic grouped title/caret/+N affordance anywhere.
    assert 'class="grouprow"' not in html
    assert 'class="grouptitle"' not in html
    assert "till" not in html or "+1 till" not in html


def test_mht_shaped_scores_remain_attached_to_their_own_signal_only(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")

    headline_row = _row_for_signal_id(html, headline.id)
    grant_row = _row_for_signal_id(html, grant.id)
    assert ">10.0</a>" in headline_row
    assert ">8.0</a>" not in headline_row
    assert ">8.0</a>" in grant_row
    assert ">10.0</a>" not in grant_row


def test_mht_shaped_titles_are_each_their_own_real_link(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")

    assert f'<a href="../signals/{headline.id}.html">Runway 6 departure-end EMAS replacement</a>' in html
    assert f'<a href="../signals/{grant.id}.html">USAspending grant — $7.9M, FY2026</a>' in html


def test_fh_d4_distinct_reviewed_signals_are_not_collapsed(tmp_path):
    """A pair a human has explicitly reviewed and confirmed DISTINCT must
    never be visually presented as one collapsible row - the exact defect
    Mission #7D proved existed in production (7 of 13 real groups)."""
    engine = _engine()
    with Session(engine) as session:
        headline, grant = _mht_shaped_signals(session)
        record_signal_group_disposition(
            session, signal_ids=[headline.id, grant.id], decision="DISTINCT",
            reviewer="human:test", reason="test - confirmed two separate real-world efforts",
        )
        session.commit()
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")

    assert 'class="grouprow"' not in html
    assert f'href="../signals/{headline.id}.html"' in html
    assert f'href="../signals/{grant.id}.html"' in html
    # Internal governance fields never leak.
    assert "human:test" not in html
    assert "DISTINCT" not in html


def test_fh_d4_same_real_world_effort_signals_still_render_independently(tmp_path):
    """Mission #7E explicitly does NOT implement FH-D4 group presentation
    yet - even a confirmed SAME_REAL_WORLD_EFFORT pair must still render
    as two fully independent rows in this mission, never merged, never
    given a canonical title/lifecycle/status/Score."""
    engine = _engine()
    with Session(engine) as session:
        headline, grant = _mht_shaped_signals(session)
        record_signal_group_disposition(
            session, signal_ids=[headline.id, grant.id], decision="SAME_REAL_WORLD_EFFORT",
            reviewer="human:test", reason="test - confirmed same real-world effort",
        )
        session.commit()
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")

    assert 'class="grouprow"' not in html
    headline_row = _row_for_signal_id(html, headline.id)
    grant_row = _row_for_signal_id(html, grant.id)
    # Each row still carries its own real Score - no canonical/inherited value.
    assert ">10.0</a>" in headline_row
    assert ">8.0</a>" in grant_row
    assert "SAME_REAL_WORLD_EFFORT" not in html
    assert "human:test" not in html


def test_unpublished_signal_remains_absent_after_grouping_removal(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Unpublished Field", iata_code="TUF", country="USA")
        session.add(airport); session.commit()
        published = Signal(
            airport_id=airport.id, title="Published test signal", category="replacement",
            confidence="high", status="identified", probability_score=8.0, published=True,
        )
        unpublished = Signal(
            airport_id=airport.id, title="Unpublished test signal - must never leak", category="replacement",
            confidence="high", status="identified", probability_score=8.0, published=False,
        )
        session.add_all([published, unpublished]); session.commit()
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))

    assert "Published test signal" in html
    assert "Unpublished test signal" not in html
    assert unpublished.id not in {s["id"] for s in data["signals"]}
    assert published.id in {s["id"] for s in data["signals"]}


def test_data_json_still_one_entry_per_published_signal(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    ids = {s["id"] for s in data["signals"]}
    assert headline.id in ids
    assert grant.id in ids
