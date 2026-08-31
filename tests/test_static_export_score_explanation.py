"""Tests for "RWI - Mission #6" (Public Score / Evidence Strength
Explanation): the canonical Score glossary entry and the small,
accessible links from every place a raw numeric Score is shown, pointing
to it.

No scoring logic, confidence calculation, or lifecycle derivation is
touched by this mission - these tests only prove the new presentation-layer
explanation exists, is linked, is honest, and does not expand the public
data.json contract.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_seed_bos_shaped`, `_engine`) are
imported from tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy.orm import Session

from app.static_export import build_site
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped


def test_score_glossary_entry_exists_and_is_anchored(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "ordlista.html").read_text(encoding="utf-8")
    assert 'id="score"' in html
    assert "<h1>Ordlista</h1>" in html


def test_score_glossary_distinguishes_evidence_strength_from_win_probability(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "ordlista.html").read_text(encoding="utf-8")
    entry = html.split('id="score"')[1][:2000]
    assert "underlag" in entry.lower()  # evidence-strength framing
    assert "vinner" in entry.lower() and "kontrakt" in entry.lower()  # explicit non-win-probability statement
    assert "Runway Safe" in entry


def test_score_glossary_distinguishes_from_project_phase_and_lifecycle(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "ordlista.html").read_text(encoding="utf-8")
    entry = html.split('id="score"')[1][:2000]
    assert "projektfas" in entry.lower() or "status" in entry.lower()
    assert "läge" in entry.lower()
    assert "historisk" in entry.lower()  # a historical fact can carry a high Score


def test_score_glossary_never_uses_forbidden_percentage_phrasing(tmp_path):
    """No wording anywhere on the site may present Score as a probability
    percentage (e.g. "72 % sannolikhet/chans/säker")."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    forbidden = re.compile(r"\d+\s?%\s?(säker|sannolikhet|chans)", re.IGNORECASE)
    for path in (tmp_path / "site").rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        assert not forbidden.search(html), f"forbidden win-probability percentage phrasing found in {path}"


def test_signals_list_score_cell_links_to_glossary(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#score"' in html
    assert 'class="score gloss-link"' in html


def test_signal_detail_score_links_to_glossary(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    signal_link = re.search(r'href="\.\./signals/(\d+)\.html"', html)
    assert signal_link
    signal_html = (tmp_path / "site" / "signals" / f"{signal_link.group(1)}.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#score"' in signal_html
    assert 'class="score score-secondary gloss-link"' in signal_html


def test_score_link_is_a_real_keyboard_focusable_anchor_not_hover_only_span(tmp_path):
    """Section 8's own accessibility requirement: no hover-only essential
    information. A <span title="…"> is not keyboard-reachable; a real <a
    href> is."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<a class="score gloss-link" href="[^"]*#score"', html)
    assert '<span class="score">' not in html


def test_score_absent_renders_dash_never_a_fabricated_value(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        from app.models import Airport, Signal
        airport = Airport(name="No Score Field", iata_code="NSF", country="USA")
        session.add(airport); session.commit()
        signal = Signal(
            airport_id=airport.id, title="No score signal", category="study",
            confidence="low", status="identified", published=True, probability_score=None,
        )
        session.add(signal); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    row = html.split(f'signals/{signal.id}.html')[1][:400]
    assert '>–<' in row or 'data-label="Score">–' in html


def test_data_json_score_contract_not_expanded(tmp_path):
    """No new score-related field was added to the public data.json
    contract - the explanation is presentation-layer only."""
    engine = _engine()
    with Session(engine) as session:
        _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    signal_keys = set(data["signals"][0].keys())
    assert "score_glossary_body" not in signal_keys
    assert "score_glossary_title" not in signal_keys
    # probability_score itself was already public before this mission - unchanged.
    assert "probability_score" in signal_keys


def test_bos_frozen_upper_airport_detail_unaffected(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert html.count('class="hero-status') == 1
    assert 'class="hero-status hero-status-lg construction"' in html
    # ("RWI - Mission #7G" mission) Score did not appear anywhere on Airport
    # Detail before that mission; it now appears, deliberately, in the
    # (non-frozen) "Projekt och bevakning" table - but the truly frozen
    # upper section (everything above that section) still never shows it.
    upper_section = html.split("Projekt och bevakning")[0]
    assert "gloss-link\" href=\"../ordlista.html#score\"" not in upper_section
    assert "gloss-link\" href=\"../ordlista.html#score\"" in html
