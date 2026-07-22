from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import settings
from app.database import get_db
from app.models import Airport, Signal


app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _format_datetime(value: object) -> str:
    if value is None:
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


templates.env.filters["datetime"] = _format_datetime


def _airport_code(airport: Airport) -> str:
    """The best identifying code we have for an airport.

    Airports imported from the FAA EMAS CSVs only ever get faa_code filled in
    (see scripts/import_faa_csv.py) - falling back to it here avoids literally
    printing "None" for any airport without an IATA/ICAO code.
    """
    return airport.iata_code or airport.icao_code or airport.faa_code or "–"


templates.env.filters["airport_code"] = _airport_code


def _parse_optional_int(value: Optional[str], *, minimum: int, maximum: int) -> Optional[int]:
    """Parse a query-string int, treating blank/invalid/out-of-range input as "no filter"."""
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if not (minimum <= parsed <= maximum):
        return None
    return parsed


def _parse_optional_float(value: Optional[str], *, minimum: float, maximum: float) -> Optional[float]:
    """Parse a query-string float, treating blank/invalid/out-of-range input as "no filter"."""
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not (minimum <= parsed <= maximum):
        return None
    return parsed


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    airport_count = db.scalar(select(func.count(Airport.id))) or 0
    signal_count = db.scalar(select(func.count(Signal.id))) or 0
    confirmed_count = db.scalar(
        select(func.count(Signal.id)).where(Signal.confidence == "confirmed")
    ) or 0
    high_score_count = db.scalar(
        select(func.count(Signal.id)).where(Signal.probability_score >= 8)
    ) or 0

    top_signals = db.scalars(
        select(Signal)
        .options(joinedload(Signal.airport), joinedload(Signal.runway))
        .order_by(Signal.probability_score.desc().nullslast(), Signal.planning_year.asc().nullslast())
        .limit(8)
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "airport_count": airport_count,
            "signal_count": signal_count,
            "confirmed_count": confirmed_count,
            "high_score_count": high_score_count,
            "top_signals": top_signals,
        },
    )


@app.get("/airports", response_class=HTMLResponse)
def list_airports(
    request: Request,
    q: Optional[str] = None,
    country: Optional[str] = None,
    db: Session = Depends(get_db),
):
    stmt = select(Airport).options(selectinload(Airport.signals)).order_by(Airport.name)

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Airport.name.ilike(pattern),
                Airport.iata_code.ilike(pattern),
                Airport.icao_code.ilike(pattern),
                Airport.city.ilike(pattern),
                Airport.state_region.ilike(pattern),
            )
        )

    if country:
        stmt = stmt.where(Airport.country == country)

    airports = db.scalars(stmt).unique().all()
    countries = db.scalars(select(Airport.country).distinct().order_by(Airport.country)).all()

    return templates.TemplateResponse(
        request=request,
        name="airports/list.html",
        context={"airports": airports, "q": q or "", "country": country or "", "countries": countries},
    )


@app.get("/airports/{airport_id}", response_class=HTMLResponse)
def airport_detail(request: Request, airport_id: int, db: Session = Depends(get_db)):
    airport = db.scalar(
        select(Airport)
        .where(Airport.id == airport_id)
        .options(
            selectinload(Airport.runways),
            selectinload(Airport.signals),
            selectinload(Airport.installations),
            selectinload(Airport.incidents),
        )
    )
    if airport is None:
        raise HTTPException(status_code=404, detail="Airport not found")

    return templates.TemplateResponse(
        request=request,
        name="airports/detail.html",
        context={"airport": airport},
    )


@app.get("/signals", response_class=HTMLResponse)
def list_signals(
    request: Request,
    q: Optional[str] = None,
    status: Optional[str] = None,
    country: Optional[str] = None,
    year: Optional[str] = None,
    min_score: Optional[str] = None,
    db: Session = Depends(get_db),
):
    year = _parse_optional_int(year, minimum=2000, maximum=2100)
    min_score = _parse_optional_float(min_score, minimum=0, maximum=10)

    stmt = (
        select(Signal)
        .join(Signal.airport)
        .options(joinedload(Signal.airport), joinedload(Signal.runway))
        .order_by(Signal.probability_score.desc().nullslast(), Signal.planning_year.asc().nullslast())
    )

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Signal.title.ilike(pattern),
                Signal.notes.ilike(pattern),
                Airport.name.ilike(pattern),
                Airport.iata_code.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(Signal.status == status)
    if country:
        stmt = stmt.where(Airport.country == country)
    if year:
        stmt = stmt.where(Signal.planning_year == year)
    if min_score is not None:
        stmt = stmt.where(Signal.probability_score >= min_score)

    signals = db.scalars(stmt).unique().all()
    statuses = db.scalars(
        select(Signal.status).where(Signal.status.is_not(None)).distinct().order_by(Signal.status)
    ).all()
    countries = db.scalars(select(Airport.country).distinct().order_by(Airport.country)).all()

    return templates.TemplateResponse(
        request=request,
        name="signals/list.html",
        context={
            "signals": signals,
            "q": q or "",
            "status": status or "",
            "country": country or "",
            "year": year or "",
            "min_score": min_score if min_score is not None else "",
            "statuses": statuses,
            "countries": countries,
        },
    )


@app.get("/signals/{signal_id}", response_class=HTMLResponse)
def signal_detail(request: Request, signal_id: int, db: Session = Depends(get_db)):
    signal = db.scalar(
        select(Signal)
        .where(Signal.id == signal_id)
        .options(
            joinedload(Signal.airport),
            joinedload(Signal.runway),
            joinedload(Signal.source),
        )
    )
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")

    return templates.TemplateResponse(
        request=request,
        name="signals/detail.html",
        context={"signal": signal},
    )


@app.get("/api/signals")
def api_signals(
    q: Optional[str] = None,
    status: Optional[str] = None,
    min_score: Optional[str] = None,
    db: Session = Depends(get_db),
):
    min_score = _parse_optional_float(min_score, minimum=0, maximum=10)

    stmt = select(Signal).join(Signal.airport).options(joinedload(Signal.airport))

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Signal.title.ilike(pattern),
                Airport.name.ilike(pattern),
                Airport.iata_code.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(Signal.status == status)
    if min_score is not None:
        stmt = stmt.where(Signal.probability_score >= min_score)

    signals = db.scalars(stmt.order_by(Signal.probability_score.desc().nullslast())).unique().all()

    return [
        {
            "id": s.id,
            "airport": s.airport.name,
            "iata": s.airport.iata_code,
            "title": s.title,
            "status": s.status,
            "confidence": s.confidence,
            "planning_year": s.planning_year,
            "score": s.probability_score,
            "likely_supplier": s.likely_supplier,
        }
        for s in signals
    ]


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
