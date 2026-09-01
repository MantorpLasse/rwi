"""Tests for "RWI - Mission #8H" (Airport Installed Base & Verification
Presentation): the Airport Detail page now leads with a prominent, honest
"EMAS-installation" (documented installed base) presentation - the same
rich installation cards Mission #4 already built, moved and un-collapsed,
never duplicated - followed by a correctly-scoped "Verifierad förekomst"
(renamed from "EMAS idag") that never implies a documented installation is
missing, doubtful, or inactive.

Preserves exactly (Mission #8G's own product model):
  Documented Installed Base != Current Verification != Signal.
  Installation.status is never freshness evidence.

Every test uses a synthetic, isolated in-memory SQLite database - never the
real data/runway_safe.db. Fixtures (`_seed_airport_with_runway_pair`,
`_seed_reviewed_identity`, `_seed_nasr_presence`) are imported from
tests/test_static_export.py rather than duplicated.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Runway, Signal, Source
from app.static_export import build_site
from tests.test_static_export import (
    _seed_airport_with_runway_pair,
    _seed_nasr_presence,
    _seed_reviewed_identity,
)
from tests.test_static_export_design_v2 import _engine, _seed_bos_shaped


def _airport_html(tmp_path, airport_id):
    return (tmp_path / "site" / "airports" / f"{airport_id}.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Part I - representative-airport regression
# ---------------------------------------------------------------------------

def test_nasr_backed_airport_shows_both_documented_base_and_verification(tmp_path):
    """A NASR-cycle-backed airport: documented installed base (Installation
    row) AND current verification (the nasr pathway) both present - neither
    suppresses the other."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, ra, rb = _seed_airport_with_runway_pair(session, name="Test NASR Airport", code="TNA", pair="9/27")
        _seed_nasr_presence(session, airport=airport, physical_designation="9")
        installation_source = Source(title="Test FAA fact sheet", source_type="faa_fact_sheet")
        session.add(installation_source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=installation_source.id, runway_id=runway.id,
            runway_end="9", type="EMASMAX", install_year=2010, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = _airport_html(tmp_path, airport.id)
    assert ">EMAS-installation<" in html or "EMAS-installation (1)" in html
    assert "Installerad 2010" in html
    # ("RWI - Mission #8I.1") The top card now leads with the positive
    # documented-base summary whenever installations exist; the NASR
    # verification detail is preserved, demoted to secondary within the
    # same card - see tests/test_static_export_installed_base_presentation.py's
    # own #8I.1 section for the dedicated positive-summary assertions.
    assert ">Dokumenterad EMAS<" in html
    assert "FAA NASR aktuell förekomst" in html  # the existing, unchanged nasr badge label


def test_reviewed_identity_backed_airport_shows_both(tmp_path):
    """A PhysicalInstallationIdentity + InstallationAssertionLink-backed
    airport: same invariant - documented base and reviewed verification
    both present, independently."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, ra, rb = _seed_airport_with_runway_pair(session, name="Test Reviewed Airport", code="TRA", pair="9/27")
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=ra, physical_designation="9")
        installation_source = Source(title="Test reviewed source", source_type="faa_fact_sheet")
        session.add(installation_source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=installation_source.id, runway_id=runway.id,
            runway_end="9", type="EMASMAX", install_year=2012, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = _airport_html(tmp_path, airport.id)
    assert "Installerad 2012" in html
    assert ">Dokumenterad EMAS<" in html  # renamed by "RWI - Mission #8I.1" when installations exist
    assert "Granskad identitet" in html  # the existing, unchanged reviewed badge label


def test_historical_installation_only_airport_still_shows_documented_base(tmp_path):
    """An airport with a real Installation but no reviewed/NASR verification
    at all (LCY's own real shape): documented base must NOT disappear
    merely because current verification is absent."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Historical-Only Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(title="Test historical source", source_type="news")
        session.add(source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=source.id, type="EMASMAX", install_year=2019, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = _airport_html(tmp_path, airport.id)
    assert "Installerad 2019" in html
    assert "EMAS-installation (1)" in html
    assert ">Dokumenterad EMAS<" in html  # renamed by "RWI - Mission #8I.1" when installations exist
    assert "Ingen separat senare verifiering registrerad." in html  # restrained secondary note, not the old primary-negative one


def test_airport_with_signal_unaffected(tmp_path):
    """An airport with a real published Signal: "Vad händer just nu" and
    the rest of the Signal presentation remain exactly as before this
    mission - the documented-base/verification changes are additive, not a
    redesign of Signal presentation."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_bos_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = _airport_html(tmp_path, airport.id)
    assert "Vad händer just nu" in html
    assert html.count('class="hero-status') == 1


# ---------------------------------------------------------------------------
# Part J - Installation.status is never freshness/current-verification/
# Signal/Attention-Reason/opportunity evidence
# ---------------------------------------------------------------------------

def test_installation_status_active_does_not_feed_current_verification(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Status Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(title="Test status source", source_type="news")
        session.add(source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=source.id, type="EMASMAX", install_year=2020, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = _airport_html(tmp_path, airport.id)
    # No reviewed/nasr pathway was seeded - the secondary verification note
    # must still show the honest, restrained empty-state, never because of
    # status="active" ("RWI - Mission #8I.1": renders under "Dokumenterad
    # EMAS" now, since a documented installation exists).
    assert "Ingen separat senare verifiering registrerad." in html


def test_installation_status_active_does_not_create_signal(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test No-Signal Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(title="Test source", source_type="news")
        session.add(source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=source.id, type="EMASMAX", install_year=2020, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    with Session(engine) as session:
        assert session.scalars(select(Signal).where(Signal.airport_id == airport.id)).all() == []
    html = _airport_html(tmp_path, airport.id)
    assert "Inga offentliga projekt- eller bevakningsuppgifter registrerade." in html


def test_active_status_badge_removed_from_public_installed_base_card(tmp_path):
    """Mission #8G's own finding: all real Installation rows carry
    status="active" uniformly - it is never freshness-verified state. The
    public card must not render it as a badge; the persisted field itself
    is untouched (verified directly on the ORM row after build)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Badge Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(title="Test badge source", source_type="news")
        session.add(source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=source.id, type="EMASMAX", install_year=2021, status="active",
        )
        session.add(installation); session.commit()
        installation_id = installation.id
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = _airport_html(tmp_path, airport.id)
    card = html[html.index(f'id="installation-{installation_id}"'):]
    card = card[:card.index("</div>", card.index("Installerad"))]
    assert "active" not in card.lower()
    # Persisted data unchanged by this presentation-only mission.
    with Session(engine) as session:
        reloaded = session.get(Installation, installation_id)
        assert reloaded.status == "active"


def test_no_attention_reason_or_opportunity_semantics_from_installation(tmp_path):
    """A documented, "active" Installation with no Signal must never
    surface anywhere Attention Reasons/Marknadsläge render."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test No-Opportunity Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(title="Test source", source_type="news")
        session.add(source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=source.id, type="EMASMAX", install_year=2020, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    market_html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "Test No-Opportunity Airport" not in market_html
    signals_html = (tmp_path / "site" / "signals" / "index.html").read_text(encoding="utf-8")
    assert "Test No-Opportunity Airport" not in signals_html


# ---------------------------------------------------------------------------
# "RWI - Mission #8I.1" (LCY Positive Installed-Base Summary): the top
# small card must lead with what RWI KNOWS (a positive documented-base
# summary), not with the negative "no separate verification" message -
# verification information is demoted to secondary, never erased.
# ---------------------------------------------------------------------------

def _seed_lcy_shaped_summary_fixture(session):
    airport = Airport(name="Test Summary Airport", iata_code="TSU", icao_code="EGTS", country="United Kingdom", city="London")
    session.add(airport); session.commit()
    runway = Runway(airport_id=airport.id, designation="09/27")
    session.add(runway); session.commit()
    source = Source(
        title="Test summary source", source_type="regulatory_document", publisher="Test Authority",
        url="https://example.test/summary-source",
    )
    session.add(source); session.commit()
    for end in ("09", "27"):
        session.add(Installation(
            airport_id=airport.id, runway_id=runway.id, source_id=source.id, runway_end=end,
            type="EMASMAX", install_year=2023, status="active", confirmed_vendor="Test Runway Safe",
        ))
    session.commit()
    return airport


def test_lcy_shaped_top_summary_is_positive_and_documented(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = _seed_lcy_shaped_summary_fixture(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    i = html.index(">Dokumenterad EMAS<")
    card = html[i:i + 700]
    assert "2</strong> installationer" in card
    assert "EMASMAX" in card
    assert "Bana 09" in card and "27" in card
    assert "Installerat 2023" in card
    assert "Test Runway Safe" in card


def test_lcy_shaped_top_summary_does_not_claim_fresh_verification(tmp_path):
    """The positive summary must still honestly demote/omit current
    verification when none is recorded - never fabricate it, never revert
    to the old alarming "no verification" framing as the PRIMARY message."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = _seed_lcy_shaped_summary_fixture(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    i = html.index(">Dokumenterad EMAS<")
    card = html[i:i + 700]
    assert "Ingen separat senare verifiering registrerad." in card
    assert "RWI har ingen separat verifiering" not in card  # the old, primary-negative framing is gone
    assert "FAA-cykelbaserad evidens" not in card


def test_nasr_backed_airport_summary_preserves_full_verification_detail(tmp_path):
    """LIT/BOS-shaped case: the positive summary leads, but the existing
    NASR provenance text (badge + full sentence) must still render,
    unabridged, in the secondary position - never erased."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, ra, rb = _seed_airport_with_runway_pair(session, name="Test NASR Summary Airport", code="TNS", pair="9/27")
        _seed_nasr_presence(session, airport=airport, physical_designation="9")
        installation_source = Source(title="Test FAA fact sheet", source_type="faa_fact_sheet")
        session.add(installation_source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=installation_source.id, runway_id=runway.id,
            runway_end="9", type="EMASMAX", install_year=2010, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    i = html.index(">Dokumenterad EMAS<")
    card = html[i:i + 900]
    assert "1</strong> installation" in card
    assert "FAA NASR aktuell förekomst" in card  # unchanged badge label
    assert "Fysisk placering enligt FAA NASR" in card  # unabridged provenance sentence preserved
    assert "Ingen separat senare verifiering registrerad." not in card  # real verification exists - no fake absence note


def test_reviewed_identity_backed_airport_summary_preserves_full_verification_detail(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, ra, rb = _seed_airport_with_runway_pair(session, name="Test Reviewed Summary Airport", code="TRS", pair="9/27")
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=ra, physical_designation="9")
        installation_source = Source(title="Test reviewed source", source_type="faa_fact_sheet")
        session.add(installation_source); session.commit()
        installation = Installation(
            airport_id=airport.id, source_id=installation_source.id, runway_id=runway.id,
            runway_end="9", type="EMASMAX", install_year=2012, status="active",
        )
        session.add(installation); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    i = html.index(">Dokumenterad EMAS<")
    card = html[i:i + 900]
    assert "Granskad identitet" in card  # unchanged badge label
    assert "Fysisk placering enligt granskad identitet" in card  # unabridged provenance preserved


def test_installation_status_does_not_drive_the_summary(tmp_path):
    """Two Installations differing only in status - the summary content
    (count/type/ends/year/vendor) must be identical regardless, proving
    status is never read by _installed_base_summary_view()."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Status-Agnostic Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(title="Test source", source_type="news")
        session.add(source); session.commit()
        session.add(Installation(
            airport_id=airport.id, source_id=source.id, type="EMASMAX",
            install_year=2019, status="active", confirmed_vendor="Test Vendor",
        ))
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html_active = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")

    engine2 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine2)
    with Session(engine2) as session:
        airport2 = Airport(name="Test Status-Agnostic Airport", country="Testland")
        session.add(airport2); session.commit()
        source2 = Source(title="Test source", source_type="news")
        session.add(source2); session.commit()
        session.add(Installation(
            airport_id=airport2.id, source_id=source2.id, type="EMASMAX",
            install_year=2019, status="removed_or_unknown_status_value", confirmed_vendor="Test Vendor",
        ))
        build_site(tmp_path / "site" / "b", session=session, today=date(2026, 8, 31))
    html_other_status = (tmp_path / "site" / "b" / "airports" / f"{airport2.id}.html").read_text(encoding="utf-8")

    def summary_card(html):
        i = html.index(">Dokumenterad EMAS<")
        return html[i:i + 700]

    assert summary_card(html_active) == summary_card(html_other_status)


def test_installation_status_active_summary_creates_no_signal_or_opportunity(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = _seed_lcy_shaped_summary_fixture(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    with Session(engine) as session:
        assert session.scalars(select(Signal).where(Signal.airport_id == airport.id)).all() == []
    market_html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "Test Summary Airport" not in market_html


def test_airport_id_6_special_case_still_takes_precedence_over_positive_summary(tmp_path):
    """The pre-existing, airport-specific current_status_unverified case
    (airport.id == 6) must still win even when that airport also has
    documented Installation rows - Mission #8I.1 must never silently
    override an already-reviewed, more cautious real-world caveat."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for n in range(1, 6):
            session.add(Airport(name=f"Filler Airport {n}", country="Testland"))
        session.commit()
        airport6 = Airport(name="Test Airport Six", country="Testland")
        session.add(airport6); session.commit()
        assert airport6.id == 6
        source = Source(title="Test source", source_type="news")
        session.add(source); session.commit()
        session.add(Installation(
            airport_id=airport6.id, source_id=source.id, type="EMASMAX", install_year=2019, status="active",
        ))
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / "6.html").read_text(encoding="utf-8")
    assert "Aktuell EMAS-status ej verifierad" in html
    assert ">Dokumenterad EMAS<" not in html
