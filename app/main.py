from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import settings
from app.database import get_db
from app.models import Airport, Document, Incident, Project, PublishingSource
from app.models.document import DOCUMENT_STATUSES


app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    airport_count = db.scalar(select(func.count(Airport.id))) or 0
    project_count = db.scalar(select(func.count(Project.id))) or 0
    confirmed_count = db.scalar(
        select(func.count(Project.id)).where(Project.confidence_level == "confirmed")
    ) or 0
    high_score_count = db.scalar(
        select(func.count(Project.id)).where(Project.probability_score >= 8)
    ) or 0

    top_projects = db.scalars(
        select(Project)
        .options(joinedload(Project.airport), joinedload(Project.runway))
        .order_by(Project.probability_score.desc(), Project.planning_year.asc().nullslast())
        .limit(8)
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "airport_count": airport_count,
            "project_count": project_count,
            "confirmed_count": confirmed_count,
            "high_score_count": high_score_count,
            "top_projects": top_projects,
        },
    )


@app.get("/airports", response_class=HTMLResponse)
def list_airports(
    request: Request,
    q: Optional[str] = None,
    country: Optional[str] = None,
    db: Session = Depends(get_db),
):
    stmt = select(Airport).options(selectinload(Airport.projects)).order_by(Airport.name)

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
            selectinload(Airport.projects),
            selectinload(Airport.installations),
            selectinload(Airport.incidents),
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="airports/detail.html",
        context={"airport": airport},
    )


@app.get("/projects", response_class=HTMLResponse)
def list_projects(
    request: Request,
    q: Optional[str] = None,
    status: Optional[str] = None,
    country: Optional[str] = None,
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
    min_score: Optional[float] = Query(default=None, ge=0, le=10),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Project)
        .join(Project.airport)
        .options(joinedload(Project.airport), joinedload(Project.runway))
        .order_by(Project.probability_score.desc(), Project.planning_year.asc().nullslast())
    )

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Project.title.ilike(pattern),
                Project.description.ilike(pattern),
                Airport.name.ilike(pattern),
                Airport.iata_code.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(Project.status == status)
    if country:
        stmt = stmt.where(Airport.country == country)
    if year:
        stmt = stmt.where(Project.planning_year == year)
    if min_score is not None:
        stmt = stmt.where(Project.probability_score >= min_score)

    projects = db.scalars(stmt).unique().all()
    statuses = db.scalars(select(Project.status).distinct().order_by(Project.status)).all()
    countries = db.scalars(select(Airport.country).distinct().order_by(Airport.country)).all()

    return templates.TemplateResponse(
        request=request,
        name="projects/list.html",
        context={
            "projects": projects,
            "q": q or "",
            "status": status or "",
            "country": country or "",
            "year": year or "",
            "min_score": min_score if min_score is not None else "",
            "statuses": statuses,
            "countries": countries,
        },
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int, db: Session = Depends(get_db)):
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            joinedload(Project.airport),
            joinedload(Project.runway),
            selectinload(Project.documents).selectinload(Document.source),
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request=request,
        name="projects/detail.html",
        context={"project": project},
    )


@app.get("/documents", response_class=HTMLResponse)
def list_documents(
    request: Request,
    q: Optional[str] = None,
    status: Optional[str] = None,
    document_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = (q or "").strip()
    status = (status or "").strip()
    document_type = (document_type or "").strip()

    stmt = select(Document).join(Document.source).options(
        joinedload(Document.source),
        selectinload(Document.projects),
    )

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Document.title.ilike(pattern),
                Document.document_reference.ilike(pattern),
                PublishingSource.name.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(Document.status == status)
    if document_type:
        stmt = stmt.where(Document.document_type == document_type)

    stmt = stmt.order_by(
        case((Document.status == "incomplete", 0), else_=1),
        case((Document.published_date.is_(None), 1), else_=0),
        Document.published_date.desc(),
        case((Document.accessed_date.is_(None), 1), else_=0),
        Document.accessed_date.desc(),
        Document.title.asc(),
        Document.id.asc(),
    )

    documents = db.scalars(stmt).unique().all()
    document_types = db.scalars(
        select(Document.document_type)
        .where(Document.document_type.is_not(None), Document.document_type != "")
        .distinct()
        .order_by(Document.document_type)
    ).all()
    total_document_count = db.scalar(select(func.count(Document.id))) or 0

    return templates.TemplateResponse(
        request=request,
        name="documents/list.html",
        context={
            "documents": documents,
            "document_types": document_types,
            "known_statuses": sorted(DOCUMENT_STATUSES),
            "total_document_count": total_document_count,
            "q": q,
            "status": status,
            "document_type": document_type,
            "filters_active": bool(q or status or document_type),
        },
    )


@app.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(request: Request, document_id: int, db: Session = Depends(get_db)):
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(
            joinedload(Document.source),
            selectinload(Document.projects).joinedload(Project.airport),
            selectinload(Document.projects).joinedload(Project.runway),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return templates.TemplateResponse(
        request=request,
        name="documents/detail.html",
        context={"document": document},
    )


@app.get("/api/projects")
def api_projects(
    q: Optional[str] = None,
    status: Optional[str] = None,
    min_score: Optional[float] = Query(default=None, ge=0, le=10),
    db: Session = Depends(get_db),
):
    stmt = select(Project).join(Project.airport).options(joinedload(Project.airport))

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Project.title.ilike(pattern),
                Airport.name.ilike(pattern),
                Airport.iata_code.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(Project.status == status)
    if min_score is not None:
        stmt = stmt.where(Project.probability_score >= min_score)

    projects = db.scalars(stmt.order_by(Project.probability_score.desc())).unique().all()

    return [
        {
            "id": p.id,
            "airport": p.airport.name,
            "iata": p.airport.iata_code,
            "title": p.title,
            "status": p.status,
            "confidence": p.confidence_level,
            "planning_year": p.planning_year,
            "score": p.probability_score,
            "likely_supplier": p.likely_supplier,
        }
        for p in projects
    ]


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
