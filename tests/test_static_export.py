import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, Signal, Source
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
