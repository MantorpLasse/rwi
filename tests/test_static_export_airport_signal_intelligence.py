"""Tests for "RWI - Mission #7G" (Strengthen Airport Intelligence
Drill-down): the Airport Detail "Projekt och bevakning" table gains
Läge/Källa/Score alongside its existing Status/Projekttyp/Confidence/År -
reusing exactly the same public `_signal_view()` fields, the same
`lifecycle_badge()`/`source_badge()` macros, and the same Mission #6
Score→glossary link pattern the global Signals list already uses.

No new domain concept, no FH-D4 read, no aggregation, no canonical Signal.
Each row remains exactly one Signal, per Mission #7E's own established
one-Signal-per-row contract - unchanged here, only extended with more of
that same Signal's own already-public fields.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_engine`, `_seed_bos_shaped`) are
imported from tests/test_static_export_design_v2.py rather than duplicated.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Airport, Signal, Source
from app.services.signal_disposition_persistence import record_signal_group_disposition
from app.static_export import build_site
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped


def _mht_shaped_signals(session: Session) -> tuple[Airport, Signal, Signal]:
    """Same shape as Mission #7E/#7F's own MHT example: one primary Signal
    and one funding Signal at the same airport, distinct titles/Scores,
    never FH-D4-reviewed together."""
    airport = Airport(name="Test Manchester-Boston Regional", iata_code="TMH", country="USA")
    session.add(airport); session.commit()
    headline = Signal(
        airport_id=airport.id, title="Runway 6 departure-end EMAS replacement",
        category="replacement", confidence="confirmed", status="procurement",
        planning_year=2026, probability_score=10.0, published=True,
    )
    session.add(headline); session.commit()
    grant_source = Source(title="Test USAspending grant record", source_type="usaspending_grant")
    session.add(grant_source); session.commit()
    grant = Signal(
        airport_id=airport.id, source_id=grant_source.id, title="USAspending grant — $7.9M, FY2026",
        category="replacement", confidence="high", status="identified",
        planning_year=2026, probability_score=8.0, published=True,
    )
    session.add(grant); session.commit()
    return airport, headline, grant


def _minneapolis_shaped_signal_with_no_score(session: Session) -> tuple[Airport, Signal]:
    """Same shape as the real Signal #67 (Minneapolis St. Paul) - a
    confirmed-vendor order with no numeric Score at all."""
    airport = Airport(name="Test Minneapolis St. Paul", iata_code="TMS", country="USA")
    session.add(airport); session.commit()
    no_score = Signal(
        airport_id=airport.id, title="MSP EMAS-order (Runway Safe bekräftad leverantör)",
        category="replacement", confidence="high", status="identified",
        confirmed_vendor="Runway Safe", probability_score=None, published=True,
    )
    session.add(no_score); session.commit()
    return airport, no_score


def _table_row_for_signal(html: str, signal_id: int) -> str:
    """Scoped to the "Projekt och bevakning" table specifically - the same
    Signal id can legitimately also appear in the frozen "Vad händer just
    nu" headline (an <h2>, not a <tr>) and in Intelligenshistorik, so
    searching the whole page for the first occurrence of the link is not
    safe."""
    table = html.split("Projekt och bevakning")[1].split("</table>")[0]
    marker = f'href="../signals/{signal_id}.html"'
    start = table.rindex("<tr", 0, table.index(marker))
    end = table.index("</tr>", start)
    return table[start:end]


def test_airport_projekt_table_shows_lage_kalla_score_columns(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    table = html.split("Projekt och bevakning")[1].split("</table>")[0]
    assert "<th>Läge</th>" in table
    assert "<th>Källa</th>" in table
    assert "<th>Score</th>" in table


def test_two_signals_at_same_airport_keep_independent_scores(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")

    headline_row = _table_row_for_signal(html, headline.id)
    assert ">10.0</a>" in headline_row
    assert ">8.0</a>" not in headline_row

    # Signal #45-shaped one is a funding signal - shown in the collapsed
    # disclosure, not the main table - but must still carry its own Score.
    disclosure = html.split("Finansiering och bidrag")[1].split("</details>")[0]
    assert f'signals/{grant.id}.html' in disclosure
    assert ">8.0</a>" in disclosure
    assert ">10.0</a>" not in disclosure


def test_none_score_renders_dash_never_fabricated(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _minneapolis_shaped_signal_with_no_score(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    row = _table_row_for_signal(html, signal.id)
    assert '<td data-label="Score">–</td>' in row


def test_lage_badge_reuses_existing_lifecycle_presentation(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    table = html.split("Projekt och bevakning")[1].split("</table>")[0]
    assert 'class="lifecycle active"' in table
    assert "Aktuell möjlighet" in table


def test_score_links_to_mission_6_glossary(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    table = html.split("Projekt och bevakning")[1].split("</table>")[0]
    assert 'class="score gloss-link" href="../ordlista.html#score"' in table


def test_source_citation_stays_per_signal_not_merged(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    table = html.split("Projekt och bevakning")[1].split("</table>")[0]
    # Only the headline (primary) Signal's source badge is in the main
    # table - the grant's own source citation lives with it in the
    # funding disclosure, never merged into one "airport source".
    assert table.count('data-label="Källa"') == 1


def test_fh_d4_disposition_never_exposed_on_airport_page(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, headline, grant = _mht_shaped_signals(session)
        record_signal_group_disposition(
            session, signal_ids=[headline.id, grant.id], decision="DISTINCT",
            reviewer="human:test", reason="test - confirmed two separate real-world efforts",
        )
        session.commit()
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    for forbidden in ("SignalDisposition", "SAME_REAL_WORLD_EFFORT", "DISTINCT", "disposition", "human:test"):
        assert forbidden not in html
    # Both Signals still render independently, each with its own Score.
    headline_row = _table_row_for_signal(html, headline.id)
    assert ">10.0</a>" in headline_row
    disclosure = html.split("Finansiering och bidrag")[1].split("</details>")[0]
    assert ">8.0</a>" in disclosure


def test_signal_links_resolve_to_own_detail_page(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport, headline, grant = _mht_shaped_signals(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert f'href="../signals/{headline.id}.html"' in html
    assert f'href="../signals/{grant.id}.html"' in html
    assert (tmp_path / "site" / "signals" / f"{headline.id}.html").exists()
    assert (tmp_path / "site" / "signals" / f"{grant.id}.html").exists()


def test_unpublished_signal_excluded_from_projekt_table(tmp_path):
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
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Published test signal" in html
    assert "Unpublished test signal" not in html


def test_intelligenshistorik_and_upper_sections_unaffected(tmp_path):
    """Mission #7G touches only 'Projekt och bevakning' - Intelligenshistorik
    and the frozen upper Airport Detail sections must render exactly as
    Mission #3/#4 established."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session)
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert html.count('class="hero-status') == 1
    assert "Vad händer just nu" in html
    assert "Intelligenshistorik" in html
    assert ">Banor<" in html
    assert ">EMAS idag<" in html
