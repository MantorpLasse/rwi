import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    Installation,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    Runway,
    RunwayEnd,
    Signal,
    Source,
    SourceAssertion,
)
from app.static_export import build_site


def _seed(session):
    airport = Airport(name="Aspen/Pitkin County Airport", iata_code="ASE", country="USA")
    runway = Runway(airport=airport, designation="15/33", length_m=2440, width_m=30)
    source = Source(title="Master Plan", source_type="master_plan", url="https://example.test/plan.pdf")
    signal = Signal(
        airport=airport,
        runway=runway,
        source=source,
        title="Runway 15/33 future EMAS",
        category="new_installation",
        confidence="confirmed",
        planning_year=2027,
        probability_score=8.5,
        notes="Two EMAS beds planned.",
    )
    session.add(signal)
    session.commit()


def test_build_site_writes_expected_pages_and_data(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)

        output = tmp_path / "site"
        build_site(output, session=session)

    assert (output / "index.html").exists()
    assert (output / "style.css").exists()
    assert (output / "data.json").exists()
    assert (output / "airports" / "index.html").exists()
    assert (output / "airports" / "1.html").exists()
    assert (output / "signals" / "index.html").exists()
    assert (output / "signals" / "1.html").exists()

    index_html = (output / "index.html").read_text(encoding="utf-8")
    assert "Runway 15/33 future EMAS" in index_html
    # ("RWI - Juicy Design Mission #2" mission) The Overview KPI markup was
    # intentionally replaced (`.stat`/`.value` -> the new hero-integrated
    # `.hero-kpi`/`.hero-kpi-value`) as part of that mission's authorized
    # visual overhaul - this assertion follows that change; the underlying
    # value (1 seeded airport) is unchanged.
    assert '<div class="hero-kpi-value">1</div>' in index_html

    signal_html = (output / "signals" / "1.html").read_text(encoding="utf-8")
    assert "ASE" in signal_html
    assert "Master Plan" in signal_html
    assert "gauge high" in signal_html
    assert "Ny installation" in signal_html  # category label, not the raw "new_installation"

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert len(data["airports"]) == 1
    assert len(data["signals"]) == 1
    assert data["signals"][0]["title"] == "Runway 15/33 future EMAS"


def test_build_site_excludes_unpublished_signal_from_public_output(tmp_path):
    """Slice 9A (docs/architecture/signal-publication-separation-slice9a-report.md):
    Signal.published, not a hardcoded id exclusion, now decides what the
    static export shows. A Signal created with published=False must not
    appear anywhere in the generated site, while an ordinary published
    Signal (the existing _seed() fixture) still does."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        airport = session.query(Airport).one()
        source = session.query(Source).one()
        unpublished = Signal(
            airport=airport, source=source, title="Internal governed signal - not yet published",
            category="replacement", confidence="medium", published=False,
        )
        session.add(unpublished)
        session.commit()
        unpublished_id = unpublished.id

        output = tmp_path / "site"
        build_site(output, session=session)

    assert (output / "signals" / f"{unpublished_id}.html").exists() is False

    index_html = (output / "index.html").read_text(encoding="utf-8")
    assert "Internal governed signal - not yet published" not in index_html
    # The existing published fixture signal is still there, unaffected.
    assert "Runway 15/33 future EMAS" in index_html

    signals_index_html = (output / "signals" / "index.html").read_text(encoding="utf-8")
    assert "Internal governed signal - not yet published" not in signals_index_html

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert len(data["signals"]) == 1
    assert data["signals"][0]["title"] == "Runway 15/33 future EMAS"


def test_build_site_shows_unconfirmed_runway_pill_instead_of_a_pill_with_no_end(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        airport = session.query(Airport).one()
        unrelated_runway = Runway(airport=airport, designation="13C/31C", length_m=1988)
        session.add(unrelated_runway)
        session.add(
            Installation(
                airport=airport,
                type="EMASMAX",
                status="active",
                notes="FAA arresting-system data lists multiple EMAS-equipped ends here: 04R/22L/04R, 04R/22L/22L.",
            )
        )
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    detail_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Ingen bekräftad bankoppling" in detail_html
    # Public runway inventory ("Banor") now publishes every governed Runway
    # row for this airport (docs/product/public-canonical-runway-inventory-
    # report.md) - unlike the Installation's ambiguous runway_end text
    # above, this section is driven entirely by the canonical Runway model,
    # so both seeded runways legitimately appear here.
    assert "Banor" in detail_html
    assert "13C/31C" in detail_html
    assert "15/33" in detail_html


def test_build_site_does_not_expose_runway_end_or_runway_end_id_anywhere(tmp_path):
    """Canonical-runway-runway-end-slice1 (docs/domain/canonical-runway-runway-end-slice1-report.md)
    adds RunwayEnd and PhysicalInstallationIdentity.runway_end_id. Static
    export behavior must remain byte-for-byte unaffected - these must not
    leak into any generated HTML page or data.json, even when present and
    linked in the database."""
    from app.models import PhysicalInstallationIdentity, RunwayEnd

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        airport = session.query(Airport).one()
        runway = session.query(Runway).one()
        runway_end = RunwayEnd(runway=runway, designation="15")
        session.add(runway_end)
        session.flush()
        session.add(
            PhysicalInstallationIdentity(
                airport_id=airport.id, runway_id=runway.id, runway_end="15", runway_end_id=runway_end.id
            )
        )
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    for html_path in output.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        assert "runway_end_id" not in text
        assert "RunwayEnd" not in text

    data_json = (output / "data.json").read_text(encoding="utf-8")
    assert "runway_end_id" not in data_json
    assert "RunwayEnd" not in data_json


def _seed_airport_with_runways(session, *, name, iata_code, designations):
    """Generic airport + N governed canonical Runway rows - no Signal
    required. Used to prove the public runway section is driven purely by
    the canonical Runway model and works for any airport, not a BOS/ORH
    special case (docs/product/public-canonical-runway-inventory-report.md)."""
    airport = Airport(name=name, iata_code=iata_code, country="USA")
    session.add(airport)
    session.flush()
    for designation in designations:
        session.add(Runway(airport_id=airport.id, designation=designation, length_m=1500, width_m=45, surface="ASPH"))
    session.commit()
    return airport


def test_build_site_publishes_full_canonical_runway_inventory_for_a_large_airport(tmp_path):
    """A 6-runway airport (the BOS shape) - every governed Runway row must
    appear, including reciprocal-end pairs, under "Banor"."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = _seed_airport_with_runways(
            session, name="Large Test Airport", iata_code="LTA",
            designations=["4L/22R", "4R/22L", "9/27", "14/32", "15L/33R", "15R/33L"],
        )
        output = tmp_path / "site"
        build_site(output, session=session)

    detail_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Banor" in detail_html
    for designation in ("4L/22R", "4R/22L", "9/27", "14/32", "15L/33R", "15R/33L"):
        assert designation in detail_html

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    published_runways = next(a["runways"] for a in data["airports"] if a["iata_code"] == "LTA")
    assert sorted(r["designation"] for r in published_runways) == sorted(
        ["4L/22R", "4R/22L", "9/27", "14/32", "15L/33R", "15R/33L"]
    )
    # Public projection is designation-only - no internal Runway.id, and no
    # length/surface (populated consistently but styled inconsistently
    # across the real database - see build.py's _runway_view()).
    assert all(set(r.keys()) == {"designation"} for r in published_runways)


def test_build_site_publishes_minimal_canonical_runway_inventory_for_a_small_airport(tmp_path):
    """A 2-runway airport (the ORH shape)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = _seed_airport_with_runways(
            session, name="Small Test Airport", iata_code="STA", designations=["11/29", "15/33"],
        )
        output = tmp_path / "site"
        build_site(output, session=session)

    detail_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "11/29" in detail_html
    assert "15/33" in detail_html


def test_build_site_shows_empty_runway_state_when_airport_has_no_canonical_runways(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="No Runway Data Airport", iata_code="NRD", country="USA")
        session.add(airport)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    detail_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Ingen banuppgift registrerad." in detail_html


def test_build_site_runway_publication_does_not_affect_emas_publication_rules(tmp_path):
    """Publishing "Banor" must not change EMAS idag in any way - an airport
    with governed runways but no reviewed identity / promoted NASR presence
    must still show the unresolved-EMAS empty state, exactly as before this
    change."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = _seed_airport_with_runways(
            session, name="Runways Only Airport", iata_code="RWO", designations=["9/27", "18/36"],
        )
        output = tmp_path / "site"
        build_site(output, session=session)

    detail_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Banor" in detail_html
    assert "9/27" in detail_html
    assert "Ingen aktuell EMAS-förekomst är publicerad från granskad eller FAA-cykelbaserad evidens." in detail_html


def test_build_site_groups_multiple_signals_at_the_same_airport_and_category(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Chicago Executive", iata_code="PWK", country="USA")
        session.add(airport)
        session.flush()
        for year, score in [(2016, 6.0), (2021, 7.0), (2025, 9.0)]:
            session.add(
                Signal(
                    airport=airport,
                    title=f"Chicago Executive replacement after incident ({year})",
                    category="replacement_after_incident",
                    confidence="high",
                    probability_score=score,
                )
            )
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    list_html = (output / "signals" / "index.html").read_text(encoding="utf-8")
    # Headline shows the top (highest-score) member's real title, styled
    # like a normal signal link, plus a muted count tag - not generic text.
    assert '<span class="grouptitle">Chicago Executive replacement after incident (2025)</span>' in list_html
    assert "+2 till" in list_html
    assert "Efter incident" in list_html
    # All three underlying signals are still present (grouping is presentation-only) -
    # as strips inside one inset detail panel, not more table rows.
    assert list_html.count('class="strip"') == 3
    assert '(2016)' in list_html
    assert '(2021)' in list_html
    assert 'class="detail-panel"' in list_html
    assert list_html.count('class="grouprow"') == 1

    # data.json is untouched by the grouping - still one entry per Signal row.
    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert len(data["signals"]) == 3


def test_build_site_does_not_group_a_lone_signal(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        output = tmp_path / "site"
        build_site(output, session=session)

    list_html = (output / "signals" / "index.html").read_text(encoding="utf-8")
    assert 'class="grouprow"' not in list_html
    assert 'class="detail-panel"' not in list_html
    assert "Runway 15/33 future EMAS" in list_html


def test_build_site_shows_completed_pill_and_installation_link_for_graduated_signal(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Wellington International Airport", iata_code="WLG", country="New Zealand")
        session.add(airport)
        session.flush()
        installation = Installation(airport=airport, type="EMAS", install_year=2026, status="active")
        session.add(installation)
        session.flush()
        signal = Signal(
            airport=airport,
            title="Wellington EMAS-order",
            category="new_installation",
            confidence="high",
            status="completed",
            installation_id=installation.id,
        )
        session.add(signal)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Färdigställd" in signal_html
    assert "pill done" in signal_html
    assert f"airports/{airport.id}.html#installation-{installation.id}" in signal_html

    airport_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert f'id="installation-{installation.id}"' in airport_html


def test_build_site_is_idempotent_and_replaces_stale_output(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    output = tmp_path / "site"
    output.mkdir()
    stale = output / "stale.html"
    stale.write_text("old", encoding="utf-8")

    with Session(engine) as session:
        build_site(output, session=session)

    assert not stale.exists()
    assert (output / "index.html").exists()


def test_build_site_writes_ordlista_page_linked_from_nav(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        build_site(tmp_path / "site", session=session)

    output = tmp_path / "site"
    assert (output / "ordlista.html").exists()

    ordlista_html = (output / "ordlista.html").read_text(encoding="utf-8")
    for anchor, heading in [
        ("emas", "EMAS (Engineered Material Arresting System)"),
        ("emasmax", "EMASMAX"),
        ("greenemas", "greenEMAS"),
        ("rsa", "RSA (Runway Safety Area)"),
        ("resa", "RESA (Runway End Safety Area)"),
        ("part-139", "Part 139"),
        ("notam", "NOTAM (Notice to Airmen)"),
        ("master-plan", "Master Plan"),
        ("aip", "AIP (Airport Improvement Program)"),
        ("iija-bidrag", "IIJA-bidrag"),
        ("cip", "CIP (Capital Improvement Plan)"),
        ("alp", "ALP (Airport Layout Plan)"),
        ("usaspending-bidrag", "USAspending-bidrag"),
        ("faa-kartdata", "FAA:s kartdata / FAA:s faktablad"),
        ("bekraftad-leverantor", "Bekräftad leverantör"),
        ("confidence", "Confidence (Hög/Medel/Låg)"),
    ]:
        assert f'id="{anchor}"' in ordlista_html
        assert heading in ordlista_html

    index_html = (output / "index.html").read_text(encoding="utf-8")
    assert '<a href="./ordlista.html">Ordlista</a>' in index_html


def test_build_site_links_recognized_source_types_to_glossary_anchors(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Field", iata_code="TST", country="USA")
        session.add(airport)
        session.flush()

        master_plan_source = Source(title="Some Master Plan", source_type="master_plan", url="https://example.test/mp")
        faa_source = Source(title="FAA map", source_type="faa_tableau")
        signal = Signal(
            airport=airport, source=master_plan_source, title="A signal",
            category="new_installation", confidence="high",
        )
        installation = Installation(airport=airport, source=faa_source, type="EMASMAX", status="active")
        session.add_all([signal, installation])
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#master-plan"' in signal_html
    assert 'href="../ordlista.html#confidence"' in signal_html

    airport_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#faa-kartdata"' in airport_html

    list_html = (output / "signals" / "index.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#master-plan"' in list_html


def test_build_site_falls_back_to_unlinked_badge_for_unmapped_source_type(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Field", iata_code="TST", country="USA")
        session.add(airport)
        session.flush()
        source = Source(title="Old free-text source", source_type="Environmental")
        signal = Signal(
            airport=airport, source=source, title="A signal",
            category="new_installation", confidence="high",
        )
        session.add(signal)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert '<span class="pill status" title="Källtyp: Övrig källa">Övrig källa</span>' in signal_html
    assert "ordlista.html#environmental" not in signal_html.lower()


def test_build_site_links_confirmed_vendor_pill_to_glossary(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Test Field", iata_code="TST", country="USA")
        session.add(airport)
        session.flush()
        installation = Installation(
            airport=airport, type="EMASMAX", status="active", confirmed_vendor="Runway Safe",
        )
        signal = Signal(
            airport=airport, title="A signal", category="new_installation", confidence="high",
            confirmed_vendor="Runway Safe",
        )
        session.add_all([installation, signal])
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    airport_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#bekraftad-leverantor"' in airport_html
    assert "Bekräftad leverantör</a>: Runway Safe" in airport_html

    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert 'href="../ordlista.html#bekraftad-leverantor"' in signal_html


def test_build_site_writes_om_page_linked_from_every_page_footer(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        output = tmp_path / "site"
        build_site(output, session=session)

    assert (output / "om.html").exists()
    om_html = (output / "om.html").read_text(encoding="utf-8")
    for heading in [
        "OM DEN HÄR SIDAN",
        "Varifrån datan kommer",
        "Ingen garanti för korrekthet",
        "Inte investeringsrådgivning",
        "Varumärken",
    ]:
        assert heading in om_html
    assert "Runway Safe Group" in om_html

    # Footer link present on a root-level page, an airports/ page and a signals/ page.
    index_html = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="./om.html">Om sidan &amp; ansvarsfriskrivning →</a>' in index_html

    airport = session.query(Airport).one()
    airport_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert 'href="../om.html">Om sidan &amp; ansvarsfriskrivning →</a>' in airport_html

    signal = session.query(Signal).one()
    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert 'href="../om.html">Om sidan &amp; ansvarsfriskrivning →</a>' in signal_html


def test_build_site_never_exposes_signal_notes_or_manual_year_estimate(tmp_path):
    """The "Min bedömning" card (Signal.notes / Signal.manual_year_estimate,
    set via scripts/annotate_signal.py) is a personal, unverified annotation
    - it must never reach the public static export, only the devserver
    (app/templates/signals/detail.html, which reads the ORM Signal directly
    and is unaffected by this test)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Manchester-Boston Regional Airport", iata_code="MHT", country="USA")
        session.add(airport)
        session.flush()
        signal = Signal(
            airport=airport,
            title="Runway 6 departure-end EMAS replacement",
            category="replacement",
            confidence="confirmed",
            planning_year=2027,
            notes="[2026-07-24] Bekräftat via bidding addendum (flymanchester.com, 2026-06-18).",
            manual_year_estimate=2027,
        )
        session.add(signal)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Min bedömning" not in signal_html
    assert "flymanchester.com" not in signal_html
    assert "bidding addendum" not in signal_html

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    signal_data = next(s for s in data["signals"] if s["id"] == signal.id)
    assert "notes" not in signal_data
    assert "manual_year_estimate" not in signal_data
    assert "flymanchester.com" not in json.dumps(data)

    # Installation.notes ("Detaljer från källan" - source-derived, not a
    # personal annotation) is a different field and must still be public.
    with Session(engine) as session:
        airport = session.query(Airport).one()
        session.add(
            Installation(
                airport=airport,
                type="EMAS",
                status="active",
                notes="Källan anger kostnad och datum i fritext.",
            )
        )
        session.commit()

        output2 = tmp_path / "site2"
        build_site(output2, session=session)

    airport_html = (output2 / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Detaljer från källan" in airport_html
    assert "Källan anger kostnad och datum i fritext." in airport_html


def test_build_site_omits_signal_source_notes_and_private_notes(tmp_path):
    """Raw signal research notes stay out of public HTML and data.json."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Fulton County Executive Airport", iata_code="FTY", country="USA")
        session.add(airport)
        session.flush()
        signal = Signal(
            airport=airport,
            title="Runway 8/26 EMAS safety improvements",
            category="new_installation",
            confidence="programmed",
            source_notes="Bekräftat via Draft Environmental Assessment (fultoncountyga.gov).",
            notes="[2026-07-24] Min privata anteckning om detta ärendet.",
        )
        session.add(signal)
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    signal_html = (output / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Bekräftat via Draft Environmental Assessment" not in signal_html
    assert "Min privata anteckning" not in signal_html
    assert "Min bedömning" not in signal_html

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    signal_data = next(s for s in data["signals"] if s["id"] == signal.id)
    assert "source_notes" not in signal_data
    assert "notes" not in signal_data


# ---------------------------------------------------------------------------
# Public EMAS protected-direction presentation
# (docs/product/public-emas-protected-direction-presentation.md).
# ---------------------------------------------------------------------------


def _seed_airport_with_runway_pair(session, *, name, code, pair):
    airport = Airport(name=name, faa_code=code, country="USA")
    session.add(airport)
    session.flush()
    runway = Runway(airport_id=airport.id, designation=pair)
    session.add(runway)
    session.flush()
    end_a, end_b = pair.split("/")
    ra = RunwayEnd(runway_id=runway.id, designation=end_a)
    rb = RunwayEnd(runway_id=runway.id, designation=end_b)
    session.add_all([ra, rb])
    session.flush()
    return airport, runway, ra, rb


def _seed_reviewed_identity(session, *, airport, runway, runway_end, physical_designation):
    identity = PhysicalInstallationIdentity(
        airport_id=airport.id, runway_id=runway.id, runway_end=physical_designation, runway_end_id=runway_end.id
    )
    session.add(identity)
    session.flush()
    source = Source(title="FAA NASR", source_type="faa_nasr_apt_ars", url="https://example.test/nasr")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="runway_end",
        raw_runway_end_value=physical_designation, source_record_identifier=f"reviewed-{identity.id}",
        evidence_quality="direct_strong", review_state="reviewed",
    )
    session.add(assertion)
    session.flush()
    session.add(InstallationAssertionLink(
        assertion_id=assertion.id, physical_installation_id=identity.id,
        outcome="SAME_PHYSICAL_INSTALLATION", reason="test", actor="human:test",
    ))
    session.commit()
    return identity


def _seed_nasr_presence(session, *, airport, physical_designation, cycle="2026-08-06", title="NASR test cycle"):
    source = Source(
        title="FAA NASR", source_type="faa_nasr_apt_ars", url="https://example.test/nasr",
        external_id=f"faa_nasr:airport_csv:{cycle}:{title}",
    )
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport.id, assertion_type="runway_end",
        raw_runway_end_value=physical_designation, runway_end=physical_designation,
        source_record_identifier=f"nasr-{source.id}", evidence_quality="direct_strong", review_state="unreviewed",
    )
    session.add(assertion)
    session.commit()
    return assertion


def _current_emas_for(output, airport_id):
    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    return next(a for a in data["airports"] if a["id"] == airport_id)["current_emas"]


def test_physical_04l_on_canonical_04l_22r_derives_protected_22r(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, end_04l, end_22r = _seed_airport_with_runway_pair(
            session, name="Test Field", code="TST", pair="4L/22R"
        )
        _seed_nasr_presence(session, airport=airport, physical_designation="04L")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert len(items) == 1
    assert items[0]["physical_runway_end"] == "04L"
    assert items[0]["protected_runway_direction"] == "22R"
    assert items[0]["primary_label"] == "Bana 22R"


def test_derivation_uses_topology_not_designation_arithmetic(tmp_path):
    """A non-numeric-heading-arithmetic-friendly pair (L/R suffixes) still
    resolves correctly, because the lookup is a relationship traversal
    (runway_end.runway.runway_ends), never string/heading math."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="15L/33R")
        _seed_nasr_presence(session, airport=airport, physical_designation="15L")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["protected_runway_direction"] == "33R"


def test_asymmetric_suffix_case_works_through_topology(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="6L/24")
        _seed_nasr_presence(session, airport=airport, physical_designation="6L")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["protected_runway_direction"] == "24"


def test_zero_matching_physical_end_fails_closed(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="9/27")
        # "13" does not exist on this airport at all.
        _seed_nasr_presence(session, airport=airport, physical_designation="13")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert len(items) == 1
    assert items[0]["protected_runway_direction"] is None
    assert items[0]["primary_label"] == "Bana 13"  # physical value shown, never a guessed reciprocal
    assert items[0]["physical_runway_end"] == "13"


def test_multiple_matches_fails_closed(tmp_path):
    """Two different canonical Runways at the same airport whose ends both
    normalize to the same token - a deliberately malformed fixture proving
    the derivation refuses to pick one arbitrarily."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Odd Field", faa_code="ODD", country="USA")
        session.add(airport)
        session.flush()
        runway_a = Runway(airport_id=airport.id, designation="9/27")
        runway_b = Runway(airport_id=airport.id, designation="9L/27R")
        session.add_all([runway_a, runway_b])
        session.flush()
        session.add_all([
            RunwayEnd(runway_id=runway_a.id, designation="9"),
            RunwayEnd(runway_id=runway_a.id, designation="27"),
            RunwayEnd(runway_id=runway_b.id, designation="9"),  # deliberate duplicate designation
            RunwayEnd(runway_id=runway_b.id, designation="27R"),
        ])
        session.commit()
        _seed_nasr_presence(session, airport=airport, physical_designation="9")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["protected_runway_direction"] is None


def test_malformed_parent_topology_fails_closed(tmp_path):
    """A canonical Runway with only one governed RunwayEnd (malformed -
    every real governed Runway always has exactly two) must not derive a
    guessed reciprocal."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Broken Field", faa_code="BRK", country="USA")
        session.add(airport)
        session.flush()
        runway = Runway(airport_id=airport.id, designation="9/27")
        session.add(runway)
        session.flush()
        session.add(RunwayEnd(runway_id=runway.id, designation="9"))  # only one end - malformed
        session.commit()
        _seed_nasr_presence(session, airport=airport, physical_designation="9")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["protected_runway_direction"] is None
    assert items[0]["physical_runway_end"] == "9"


def test_physical_value_preserved_separately_from_derived_label(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="4L/22R")
        _seed_nasr_presence(session, airport=airport, physical_designation="04L")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["physical_runway_end"] == "04L"  # raw value, never silently normalized away
    assert items[0]["protected_runway_direction"] == "22R"
    assert items[0]["primary_label"] != items[0]["physical_runway_end"]


def test_primary_label_uses_protected_direction_not_physical_value(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="4L/22R")
        _seed_nasr_presence(session, airport=airport, physical_designation="04L")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert "22R" in items[0]["primary_label"]
    assert "04L" not in items[0]["primary_label"]


def test_nasr_only_public_item_renders_with_cycle_and_caveat(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_presence(session, airport=airport, physical_designation="09", cycle="2026-08-06")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["evidence_basis"] == "nasr"
    assert "2026-08-06" in items[0]["provenance_text"]
    assert "projektstatus" in items[0]["provenance_text"]  # the "does not mean install year etc." caveat


def test_reviewed_identity_renders_correctly(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway_pair(
            session, name="Test Field", code="TST", pair="9/27"
        )
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=end_9, physical_designation="9")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert len(items) == 1
    assert items[0]["evidence_basis"] == "reviewed"
    assert items[0]["protected_runway_direction"] == "27"


def test_reviewed_and_nasr_same_bed_deduplicates_to_one_item(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway_pair(
            session, name="Test Field", code="TST", pair="9/27"
        )
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=end_9, physical_designation="9")
        _seed_nasr_presence(session, airport=airport, physical_designation="09")  # same physical bed, different pathway

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert len(items) == 1  # deduplicated, not two items for the same physical bed


def test_reviewed_identity_wins_presentation_precedence_over_nasr(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway_pair(
            session, name="Test Field", code="TST", pair="9/27"
        )
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=end_9, physical_designation="9")
        _seed_nasr_presence(session, airport=airport, physical_designation="09")

        output = tmp_path / "site"
        build_site(output, session=session)

    items = _current_emas_for(output, airport.id)
    assert items[0]["evidence_basis"] == "reviewed"  # not overwritten by the (unreviewed) NASR duplicate


def test_review_required_style_airport_remains_excluded_from_publication(tmp_path):
    """BOS/ORH-shaped case: an airport with governed runways but no
    reviewed identity and no promoted (runway_end IS NULL) NASR assertion
    must show zero current-EMAS items - simulating a REVIEW_REQUIRED
    assertion that a future promotion writer deliberately left unpromoted."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Boston-Shaped Field", code="BST", pair="4L/22R")
        source = Source(
            title="FAA NASR", source_type="faa_nasr_apt_ars", url="https://example.test/nasr",
            external_id="faa_nasr:airport_csv:2026-08-06:unpromoted",
        )
        session.add(source)
        session.flush()
        session.add(SourceAssertion(  # runway_end deliberately left NULL - the REVIEW_REQUIRED shape
            source_id=source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_end_value="04L", runway_end=None,
            source_record_identifier="unpromoted-1", evidence_quality="direct_strong", review_state="unreviewed",
        ))
        session.commit()

        output = tmp_path / "site"
        build_site(output, session=session)

    assert _current_emas_for(output, airport.id) == []


def test_worcester_shaped_airport_also_remains_excluded(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Worcester-Shaped Field", code="WST", pair="11/29")
        output = tmp_path / "site"
        build_site(output, session=session)

    assert _current_emas_for(output, airport.id) == []


def test_current_emas_never_leaks_internal_ids(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway_pair(
            session, name="Test Field", code="TST", pair="9/27"
        )
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=end_9, physical_designation="9")
        _seed_nasr_presence(session, airport=airport, physical_designation="27")

        output = tmp_path / "site"
        build_site(output, session=session)

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    airport_data = next(a for a in data["airports"] if a["id"] == airport.id)
    for item in airport_data["current_emas"]:
        assert set(item.keys()) == {
            "primary_label", "physical_runway_end", "protected_runway_direction",
            "evidence_basis", "evidence_basis_label", "provenance_text",
        }
    detail_html = (output / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "runway_end_id" not in detail_html
    assert "assertion" not in detail_html.lower() or "assertion" not in " ".join(
        line for line in detail_html.splitlines() if "EMAS" in line
    )


def test_data_json_current_emas_semantics_are_explicit(tmp_path):
    """The old ambiguous `runway_end`-named fields (reviewed_identities/
    nasr_presence) are gone; the new field names are self-describing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_presence(session, airport=airport, physical_designation="09")
        output = tmp_path / "site"
        build_site(output, session=session)

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    airport_data = next(a for a in data["airports"] if a["id"] == airport.id)
    assert "reviewed_identities" not in airport_data
    assert "nasr_presence" not in airport_data
    assert "current_emas" in airport_data
    assert "physical_runway_end" in airport_data["current_emas"][0]
    assert "protected_runway_direction" in airport_data["current_emas"][0]


def test_canonical_runway_inventory_unaffected_by_current_emas_changes(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway_pair(session, name="Test Field", code="TST", pair="9/27")
        Runway_row = Runway(airport_id=airport.id, designation="18/36")
        session.add(Runway_row)
        session.commit()
        _seed_nasr_presence(session, airport=airport, physical_designation="09")

        output = tmp_path / "site"
        build_site(output, session=session)

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    airport_data = next(a for a in data["airports"] if a["id"] == airport.id)
    assert sorted(r["designation"] for r in airport_data["runways"]) == ["18/36", "9/27"]


def test_no_database_mutation_occurs_during_build(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway_pair(
            session, name="Test Field", code="TST", pair="9/27"
        )
        _seed_reviewed_identity(session, airport=airport, runway=runway, runway_end=end_9, physical_designation="9")
        _seed_nasr_presence(session, airport=airport, physical_designation="27")

        output = tmp_path / "site"
        build_site(output, session=session)

        assert len(session.new) == 0 and len(session.dirty) == 0 and len(session.deleted) == 0
