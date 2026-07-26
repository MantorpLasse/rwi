import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Runway, Signal, Source
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
    assert '<div class="value">1</div>' in index_html

    signal_html = (output / "signals" / "1.html").read_text(encoding="utf-8")
    assert "ASE" in signal_html
    assert "Master Plan" in signal_html
    assert "gauge high" in signal_html
    assert "Ny installation" in signal_html  # category label, not the raw "new_installation"

    data = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert len(data["airports"]) == 1
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
    assert "13C/31C" in detail_html  # the airport's real, unrelated runway is still shown in "Banor"


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
    assert '<span class="pill status" title="Källtyp: Environmental">Environmental</span>' in signal_html
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
