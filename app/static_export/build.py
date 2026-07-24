from __future__ import annotations

import json
import shutil
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, UTC
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.models import Airport, Signal

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Confidence, as recorded in the database, has finer shades ("confirmed" vs
# "high", "programmed" vs "medium") than the 3-bar gauge component can show -
# see DESIGN_BRIEF.md's "Confidence som gauge, inte badge-text". Every value
# buckets into exactly one of the gauge's three visual levels.
_CONFIDENCE_LEVEL = {
    "high": "high",
    "confirmed": "high",
    "medium": "med",
    "programmed": "med",
    "low": "low",
    "planned": "low",
    "speculative": "low",
    "unknown": "low",
}

_CONFIDENCE_LABEL = {"high": "Hög", "med": "Medel", "low": "Låg"}

# Category text mapping — the single place the rest of the site imports from,
# per DESIGN_BRIEF.md's "Bygg denna mappning på ett ställe". Never show a raw
# database category value in a template. new_installation/replacement/
# replacement_after_incident/study/potential_new_construction are the values
# the brief lists explicitly; maintenance/replacement_watch/unknown are real
# values already present in production data that also need a label.
_CATEGORY = {
    "new_installation": ("Ny installation", "new"),
    "replacement": ("Ersättning", "replace"),
    "replacement_after_incident": ("Efter incident", "incident"),
    "study": ("Studie", "study"),
    "potential_new_construction": ("Möjlig ny installation", "new"),
    "maintenance": ("Underhåll", "study"),
    "replacement_watch": ("Ersättning – bevakas", "replace"),
    "unknown": ("Ej klassificerad", "study"),
}


def _confidence_level(value: str | None) -> str:
    return _CONFIDENCE_LEVEL.get((value or "").lower(), "low")


def _category_view(value: str | None) -> tuple[str, str]:
    return _CATEGORY.get(value or "", (value or "Okänd", "study"))


def _signal_view(signal: Signal) -> SimpleNamespace:
    source = signal.source
    category_label, category_class = _category_view(signal.category)
    confidence_level = _confidence_level(signal.confidence)
    return SimpleNamespace(
        id=signal.id,
        title=signal.title,
        category=signal.category,
        category_label=category_label,
        category_class=category_class,
        confidence=signal.confidence,
        confidence_level=confidence_level,
        confidence_label=_CONFIDENCE_LABEL[confidence_level],
        status=signal.status,
        planning_year=signal.planning_year,
        procurement_year=signal.procurement_year,
        probability_score=signal.probability_score,
        notes=signal.notes,
        manual_year_estimate=signal.manual_year_estimate,
        confirmed_vendor=signal.confirmed_vendor,
        likely_supplier=signal.likely_supplier,
        supplier_reason=signal.supplier_reason,
        estimated_total_value_usd=signal.estimated_total_value_usd,
        estimated_emas_value_usd=signal.estimated_emas_value_usd,
        airport_id=signal.airport_id,
        airport_name=signal.airport.name,
        airport_code=signal.airport.iata_code or signal.airport.icao_code or "–",
        country=signal.airport.country,
        runway_designation=signal.runway.designation if signal.runway else None,
        source_title=source.title if source else None,
        source_publisher=source.publisher if source else None,
        source_type=source.source_type if source else None,
        source_url=source.url if source else None,
    )


def _airport_view(airport: Airport) -> SimpleNamespace:
    return SimpleNamespace(
        id=airport.id,
        name=airport.name,
        iata_code=airport.iata_code,
        icao_code=airport.icao_code,
        city=airport.city,
        state_region=airport.state_region,
        country=airport.country,
        latitude=airport.latitude,
        longitude=airport.longitude,
        website_url=airport.website_url,
        signal_count=len(airport.signals),
        runways=[
            SimpleNamespace(designation=r.designation, length_m=r.length_m, width_m=r.width_m)
            for r in airport.runways
        ],
        signals=[
            _signal_view(s)
            for s in sorted(
                airport.signals,
                key=lambda s: (s.probability_score is None, -(s.probability_score or 0)),
            )
        ],
        installations=[
            SimpleNamespace(
                type=i.type,
                runway_end=i.runway_end,
                install_year=i.install_year,
                status=i.status,
                confirmed_vendor=i.confirmed_vendor,
            )
            for i in airport.installations
        ],
        incidents=[
            SimpleNamespace(
                incident_date=i.incident_date,
                incident_type=i.incident_type,
                emas_engaged=i.emas_engaged,
            )
            for i in airport.incidents
        ],
    )


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, SimpleNamespace):
        return vars(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_site(output_dir: Path, *, session: Session | None = None) -> None:
    owns_session = session is None
    session = session or SessionLocal()
    try:
        _build(output_dir, session)
    finally:
        if owns_session:
            session.close()


def _build(output_dir: Path, session: Session) -> None:
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "airports").mkdir()
    (output_dir / "signals").mkdir()
    shutil.copy2(STATIC_DIR / "style.css", output_dir / "style.css")

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    airports = session.scalars(
        select(Airport).options(
            selectinload(Airport.runways),
            selectinload(Airport.signals).selectinload(Signal.airport),
            selectinload(Airport.signals).selectinload(Signal.runway),
            selectinload(Airport.signals).selectinload(Signal.source),
            selectinload(Airport.installations),
            selectinload(Airport.incidents),
        ).order_by(Airport.name)
    ).all()
    airport_views = [_airport_view(a) for a in airports]

    all_signals = session.scalars(
        select(Signal).options(
            selectinload(Signal.airport),
            selectinload(Signal.runway),
            selectinload(Signal.source),
        )
    ).all()
    signal_views = [_signal_view(s) for s in all_signals]
    signal_views.sort(key=lambda s: (s.probability_score is None, -(s.probability_score or 0)))

    def render(name: str, path: Path, **context) -> None:
        template = env.get_template(name)
        path.write_text(template.render(generated_at=generated_at, **context), encoding="utf-8")

    render(
        "index.html",
        output_dir / "index.html",
        root=".",
        airport_count=len(airport_views),
        installation_count=sum(len(a.installations) for a in airport_views),
        signal_count=len(signal_views),
        high_confidence_count=sum(1 for s in signal_views if s.confidence_level == "high"),
        top_signals=signal_views[:8],
    )

    render(
        "airports_list.html",
        output_dir / "airports" / "index.html",
        root="..",
        airports=airport_views,
    )
    for airport in airport_views:
        render(
            "airport_detail.html",
            output_dir / "airports" / f"{airport.id}.html",
            root="..",
            airport=airport,
        )

    render(
        "signals_list.html",
        output_dir / "signals" / "index.html",
        root="..",
        signals=signal_views,
        statuses=sorted({s.status for s in signal_views if s.status}),
        countries=sorted({s.country for s in signal_views if s.country}),
    )
    for signal in signal_views:
        render(
            "signal_detail.html",
            output_dir / "signals" / f"{signal.id}.html",
            root="..",
            signal=signal,
        )

    data = {
        "generated_at": generated_at,
        "airports": airport_views,
        "signals": signal_views,
    }
    (output_dir / "data.json").write_text(
        json.dumps(data, default=_json_default, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
