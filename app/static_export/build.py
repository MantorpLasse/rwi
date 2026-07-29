from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, UTC
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.models import Airport, Installation, Signal

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

# scripts/graduate_signal_to_installation.py sets this - a distinct label so
# a graduated signal reads as "done", not as a broken/unrecognized status.
_STATUS_LABEL = {"completed": "Färdigställd"}

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

# Source.source_type -> (display label, /ordlista.html anchor, one-sentence
# tooltip) - the single place this mapping lives, per DESIGN_BRIEF.md's
# "Bygg denna mappning på ett ställe" (already applied to _CATEGORY above).
# Anchor/tooltip text is verbatim from the glossary content the source-type
# badges link to - never paraphrased here.
#
# "Master Plan" (capitalized, space) is an older, pre-snake_case alias for
# the same concept as "master_plan" - both map to the same anchor. A few
# source_type values already in the database (Airport/Authority/
# Environmental/FAA/Procurement/Watchlist) are leftover free-text fragments
# from early, pre-"forenkling" data, not a real taxonomy - deliberately not
# mapped here (fixing that is a separate, unrelated data-quality task); they
# fall through to the "no entry" branch below and render as a plain,
# unlinked badge showing the raw value, exactly as before this change.
_SOURCE_TYPE = {
    "master_plan": (
        "Master Plan", "master-plan",
        "En flygplats långsiktiga utvecklingsplan, ofta 10-20 år framåt.",
    ),
    "Master Plan": (
        "Master Plan", "master-plan",
        "En flygplats långsiktiga utvecklingsplan, ofta 10-20 år framåt.",
    ),
    "aip_grant": (
        "AIP-bidrag", "aip",
        "Ett amerikanskt statligt bidragsprogram. Ett beviljat AIP-bidrag betyder att "
        "pengarna finns, men inte alltid att bygget redan startat.",
    ),
    "iija_grant": (
        "IIJA-bidrag", "iija-bidrag",
        "En separat, större statlig bidragspott, fungerar ungefär som AIP men är en "
        "egen pengapåse.",
    ),
    "usaspending_grant": (
        "USAspending-bidrag", "usaspending-bidrag",
        "Ett verkligt, redan beviljat federalt bidrag, hämtat direkt från den "
        "amerikanska statens egna offentliga utbetalningsregister.",
    ),
    "faa_tableau": (
        "FAA:s kartdata", "faa-kartdata",
        "Officiell information direkt från den amerikanska luftfartsmyndigheten "
        "(FAA) om vad som redan är byggt.",
    ),
    "faa_fact_sheet": (
        "FAA:s faktablad", "faa-kartdata",
        "Officiell information direkt från den amerikanska luftfartsmyndigheten "
        "(FAA) om vad som redan är byggt.",
    ),
    "CIP": (
        "CIP", "cip",
        "En flygplats egen, mer kortsiktiga investeringslista (vanligtvis 3-5 år).",
    ),
    "ALP": (
        "ALP", "alp",
        "En teknisk ritning över hur flygplatsen ser ut och ska se ut.",
    ),
    "news": ("Nyhetskälla", None, None),
    "shareholder_newsletter": ("Aktieägarbrev", None, None),
    "faa_construction_report": ("FAA byggrapport", None, None),
    "environmental_assessment": ("Miljökonsekvensbeskrivning (EA)", None, None),
    "state_aviation_system_plan": ("Delstatlig flygplatsplan", None, None),
}


def _confidence_level(value: str | None) -> str:
    return _CONFIDENCE_LEVEL.get((value or "").lower(), "low")


def _category_view(value: str | None) -> tuple[str, str]:
    return _CATEGORY.get(value or "", (value or "Okänd", "study"))


def _source_type_view(value: str | None) -> tuple[str | None, str | None, str | None]:
    """Returns (label, anchor, tooltip) - anchor/tooltip are None when this
    source_type has no glossary entry, so source_badge() falls back to a
    plain, unlinked badge instead of a dead link."""
    if not value:
        return None, None, None
    return _SOURCE_TYPE.get(value, (value, None, None))


def _signal_view(signal: Signal) -> SimpleNamespace:
    source = signal.source
    category_label, category_class = _category_view(signal.category)
    confidence_level = _confidence_level(signal.confidence)
    source_type_label, source_type_anchor, source_type_tooltip = _source_type_view(
        source.source_type if source else None
    )
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
        status_label=_STATUS_LABEL.get(signal.status, signal.status),
        is_completed=signal.status == "completed",
        installation_id=signal.installation_id,
        updated_at=signal.updated_at,
        target_year=signal.target_year,
        planning_year=signal.planning_year,
        procurement_year=signal.procurement_year,
        probability_score=signal.probability_score,
        # signal.notes and signal.manual_year_estimate (the "Min bedömning"
        # card's personal, unverified annotation - see
        # scripts/annotate_signal.py) are deliberately NOT exposed here.
        # This view is what feeds signal_detail.html *and* data.json for the
        # public static export, so anything added to it is published -
        # unlike app/templates/signals/detail.html, the devserver template,
        # which reads the ORM Signal directly and still shows both notes and
        # source_notes. source_notes itself IS public - sourced research
        # with a citation, the Signal equivalent of Installation.notes
        # ("Detaljer från källan").
        source_notes=signal.source_notes,
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
        source_type_label=source_type_label,
        source_type_anchor=source_type_anchor,
        source_type_tooltip=source_type_tooltip,
        source_url=source.url if source else None,
    )


_RUNWAY_TRACKED_INSTALLATION_TYPES = ("EMASMAX", "greenEMAS")


def _installation_view(installation: Installation) -> SimpleNamespace:
    source = installation.source
    source_type_label, source_type_anchor, source_type_tooltip = _source_type_view(
        source.source_type if source else None
    )
    return SimpleNamespace(
        id=installation.id,
        type=installation.type,
        runway_end=installation.runway_end,
        # True only when no runway link exists at all (genuinely ambiguous
        # between real, different runways, or no FAA match yet) - not when a
        # runway is linked but the specific end isn't (see
        # scripts/import_faa_runway_ends.py's "resolved_same_runway_multiple_ends").
        # Lets the UI say so explicitly instead of silently omitting a pill,
        # so a reader never mistakes an unrelated runway in the "Banor" panel
        # for this installation's runway.
        no_confirmed_runway=(
            installation.type in _RUNWAY_TRACKED_INSTALLATION_TYPES and installation.runway_id is None
        ),
        install_year=installation.install_year,
        updated_at=installation.updated_at,
        status=installation.status,
        confirmed_vendor=installation.confirmed_vendor,
        notes=installation.notes,
        source_title=source.title if source else None,
        source_publisher=source.publisher if source else None,
        source_type=source.source_type if source else None,
        source_type_label=source_type_label,
        source_type_anchor=source_type_anchor,
        source_type_tooltip=source_type_tooltip,
        source_url=source.url if source else None,
        source_published_date=source.published_date if source else None,
    )


def _timeline_event(
    *, kind: str, id: int, year: int | None, day: date | None,
    category_class: str, category_label: str, title: str, subtitle: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        id=id,
        year=year,
        day=day,
        category_class=category_class,
        category_label=category_label,
        title=title,
        subtitle=subtitle,
    )


def _timeline_view(
    installations: list[SimpleNamespace],
    incidents: list[SimpleNamespace],
    signals: list[SimpleNamespace],
) -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    """Merge Installations/Incidents/Signals into one chronological timeline
    for airport_detail.html, alongside (not instead of) the existing
    per-entity tables. Year source per entity: Installation.install_year,
    Incident.incident_date, and for Signal a fallback chain
    (target_year -> planning_year) since target_year is populated on only a
    handful of signals database-wide while planning_year is what the
    Signals table already displays - a strict target_year-only reading
    would dump almost every signal into "Odaterat" even when a year is in
    fact known. manual_year_estimate is deliberately excluded from this
    chain, even as a last resort: it's a personal, unverified guess (see
    scripts/annotate_signal.py) and this timeline is part of the public
    static export - falling back to it would put that guess on the page as
    if it were a resolved year.

    Events without a resolvable year go to a separate `undated` list instead
    of the chronological one, per the "Odaterat" section requirement -
    Incident.incident_date is a required (non-nullable) column, so incidents
    always land in the chronological list.
    """
    dated: list[SimpleNamespace] = []
    undated: list[SimpleNamespace] = []

    for installation in installations:
        subtitle = " · ".join(
            p for p in (
                installation.status,
                f"Bana {installation.runway_end}" if installation.runway_end else None,
            ) if p
        ) or None
        event = _timeline_event(
            kind="installation",
            id=installation.id,
            year=installation.install_year,
            day=None,
            category_class="new",
            category_label="Installation",
            title=installation.type or "Installation",
            subtitle=subtitle,
        )
        (dated if event.year else undated).append(event)

    for incident in incidents:
        event = _timeline_event(
            kind="incident",
            id=incident.id,
            year=incident.incident_date.year,
            day=incident.incident_date,
            category_class="incident",
            category_label="Incident",
            title=incident.incident_type,
            subtitle="EMAS aktiverat" if incident.emas_engaged else "EMAS inte aktiverat",
        )
        dated.append(event)

    for signal in signals:
        year = signal.target_year or signal.planning_year
        event = _timeline_event(
            kind="signal",
            id=signal.id,
            year=year,
            day=None,
            category_class=signal.category_class,
            category_label=signal.category_label,
            title=signal.title,
            subtitle=signal.status,
        )
        (dated if event.year else undated).append(event)

    dated.sort(key=lambda e: (e.year, e.day.month, e.day.day, 1) if e.day else (e.year, 1, 1, 0))
    return dated, undated


# Pure SVG (no chart library) for index.html's install-trend card. Geometry
# is computed here, once, in real user units - the template only loops over
# ready-made coordinates, same division of labour as _timeline_view above.
_TREND_WIDTH = 700
_TREND_BAR_HEIGHT = 180
_TREND_BAR_MARGIN = {"top": 8, "right": 8, "bottom": 6, "left": 8}
_TREND_LINE_HEIGHT = 120
_TREND_LINE_MARGIN = {"top": 10, "right": 8, "bottom": 22, "left": 8}
_TREND_MAX_SPAN_YEARS = 40  # only a backstop against a bad outlier row; real data (1996-2030ish) sits well under this


def _trend_view(
    installations: list[SimpleNamespace],
    signals: list[SimpleNamespace],
) -> SimpleNamespace | None:
    """Installations-per-year (bar) + cumulative installations (line), plus
    a second, visually-muted bar series for planned signals per year - using
    the same year fallback chain as the airport timeline (target_year ->
    planning_year; manual_year_estimate deliberately excluded, see
    _timeline_view). Returns None when there's no dateable data at all, so
    the template can show an empty-state instead.
    """
    install_years = [i.install_year for i in installations if i.install_year]
    signal_years = [
        y
        for y in (
            (s.target_year or s.planning_year) for s in signals
        )
        if y
    ]

    all_years = install_years + signal_years
    if not all_years:
        return None

    end_year = max(all_years)
    end_year += (5 - end_year % 5) % 5  # round up to the next 5-year mark, e.g. 2028 -> 2030
    start_year = min(all_years)
    if end_year - start_year > _TREND_MAX_SPAN_YEARS:
        start_year = end_year - _TREND_MAX_SPAN_YEARS

    years = list(range(start_year, end_year + 1))
    install_counts = Counter(install_years)
    signal_counts = Counter(signal_years)
    max_per_year = max([install_counts.get(y, 0) for y in years] + [signal_counts.get(y, 0) for y in years] + [1])

    slot_w = (_TREND_WIDTH - _TREND_BAR_MARGIN["left"] - _TREND_BAR_MARGIN["right"]) / len(years)
    bar_w = max(slot_w * 0.34, 1.5)
    gap = slot_w * 0.08
    plot_h_bar = _TREND_BAR_HEIGHT - _TREND_BAR_MARGIN["top"] - _TREND_BAR_MARGIN["bottom"]
    bar_baseline_y = _TREND_BAR_MARGIN["top"] + plot_h_bar

    bars = []
    for idx, year in enumerate(years):
        slot_x = _TREND_BAR_MARGIN["left"] + idx * slot_w
        n_install = install_counts.get(year, 0)
        n_signal = signal_counts.get(year, 0)
        install_h = (n_install / max_per_year) * plot_h_bar
        signal_h = (n_signal / max_per_year) * plot_h_bar
        bars.append(
            SimpleNamespace(
                year=year,
                install_count=n_install,
                signal_count=n_signal,
                install_x=round(slot_x + gap, 2),
                install_y=round(bar_baseline_y - install_h, 2),
                install_h=round(install_h, 2),
                signal_x=round(slot_x + gap + bar_w + gap, 2),
                signal_y=round(bar_baseline_y - signal_h, 2),
                signal_h=round(signal_h, 2),
                bar_w=round(bar_w, 2),
            )
        )

    plot_h_line = _TREND_LINE_HEIGHT - _TREND_LINE_MARGIN["top"] - _TREND_LINE_MARGIN["bottom"]
    line_baseline_y = _TREND_LINE_MARGIN["top"] + plot_h_line
    max_cumulative = sum(install_counts.get(y, 0) for y in years) or 1

    points = []
    running = 0
    for idx, year in enumerate(years):
        running += install_counts.get(year, 0)
        x = _TREND_LINE_MARGIN["left"] + idx * slot_w + slot_w / 2
        y = line_baseline_y - (running / max_cumulative) * plot_h_line
        points.append(SimpleNamespace(x=round(x, 2), y=round(y, 2), year=year, cumulative=running))

    line_path = "M " + " L ".join(f"{p.x},{p.y}" for p in points)
    area_path = (
        line_path
        + f" L {points[-1].x},{line_baseline_y} L {points[0].x},{line_baseline_y} Z"
    )

    ticks = [
        SimpleNamespace(year=year, x=round(_TREND_BAR_MARGIN["left"] + idx * slot_w + slot_w / 2, 2))
        for idx, year in enumerate(years)
        if year % 5 == 0
    ]

    return SimpleNamespace(
        width=_TREND_WIDTH,
        bar_height=_TREND_BAR_HEIGHT,
        line_height=_TREND_LINE_HEIGHT,
        bar_baseline_y=round(bar_baseline_y, 2),
        line_baseline_y=round(line_baseline_y, 2),
        bars=bars,
        points=points,
        ticks=ticks,
        line_path=line_path,
        area_path=area_path,
        max_cumulative=max_cumulative,
        total_installations=max_cumulative,
        total_signals=sum(signal_counts.get(y, 0) for y in years),
        start_year=start_year,
        end_year=end_year,
    )


# The date created_at/updated_at were added to Signal/Installation/Source/
# Incident (see docs/utredning_senast_uppdaterat.md and
# scripts/add_created_updated_timestamps.py) - a fixed historical fact used
# only for the "Senast uppdaterat" empty-state copy below. Deliberately not
# datetime.now(): that would make the dashboard claim a fresh "log started
# today" on every rebuild, long after the real migration date.
_CHANGELOG_START_DATE = date(2026, 7, 27)


def _recent_changes_view(
    airport_views: list[SimpleNamespace],
    signal_views: list[SimpleNamespace],
    *,
    limit: int = 15,
) -> list[SimpleNamespace]:
    """index.html's "Senast uppdaterat" feed - Signal/Installation/Incident
    rows with a real updated_at (see docs/utredning_senast_uppdaterat.md).

    Source is deliberately excluded even though it also got the column: it
    has no detail page or anchor anywhere on the site, so there's nowhere
    honest to link a change to. A row with updated_at=None isn't an
    "unknown change" - it predates the column entirely - so it's left out
    rather than shown with a missing/fake date.
    """
    entries: list[SimpleNamespace] = []

    for airport in airport_views:
        for installation in airport.installations:
            if installation.updated_at is None:
                continue
            entries.append(
                SimpleNamespace(
                    kind="installation",
                    id=installation.id,
                    category_label="Installation",
                    category_class="new",
                    title=installation.type or "Installation",
                    airport_id=airport.id,
                    airport_code=airport.iata_code or airport.icao_code or "–",
                    airport_name=airport.name,
                    updated_at=installation.updated_at,
                )
            )
        for incident in airport.incidents:
            if incident.updated_at is None:
                continue
            entries.append(
                SimpleNamespace(
                    kind="incident",
                    id=incident.id,
                    category_label="Incident",
                    category_class="incident",
                    title=incident.incident_type,
                    airport_id=airport.id,
                    airport_code=airport.iata_code or airport.icao_code or "–",
                    airport_name=airport.name,
                    updated_at=incident.updated_at,
                )
            )

    for signal in signal_views:
        if signal.updated_at is None:
            continue
        entries.append(
            SimpleNamespace(
                kind="signal",
                id=signal.id,
                category_label=signal.category_label,
                category_class=signal.category_class,
                title=signal.title,
                airport_id=signal.airport_id,
                airport_code=signal.airport_code,
                airport_name=signal.airport_name,
                updated_at=signal.updated_at,
            )
        )

    entries.sort(key=lambda e: e.updated_at, reverse=True)
    return [
        SimpleNamespace(**vars(e), date_label=e.updated_at.strftime("%Y-%m-%d"))
        for e in entries[:limit]
    ]


def _group_signal_views(signal_views: list[SimpleNamespace]) -> list[SimpleNamespace]:
    """Group signal_views by (airport_id, category) into single rows or
    expandable groups for signals_list.html - purely a presentation grouping,
    the underlying Signal rows/data are untouched.

    A group of 1 renders exactly like a normal row always has (kind="single").
    A group of >1 (e.g. three replacement_after_incident signals at the same
    airport, one per Incident - see app/models/incident.py) becomes one
    headline row plus its members, so the list doesn't repeat the same
    airport+category story once per incident/grant/etc. Per
    grouping_mockup.html: the headline shows the top member's own title
    (styled like a normal signal link) plus a muted "+N till" count - not
    generic "N signaler" text.

    Groups appear at the position of their first member in signal_views'
    incoming order, which callers sort by probability_score descending - so
    a group surfaces at its best member's rank, and members[0] (used as the
    headline) is that best member, since a subsequence of a sorted list is
    itself sorted.
    """
    order: list[tuple[int, str]] = []
    members_by_key: dict[tuple[int, str], list[SimpleNamespace]] = {}
    for view in signal_views:
        key = (view.airport_id, view.category)
        if key not in members_by_key:
            order.append(key)
        members_by_key.setdefault(key, []).append(view)

    rows: list[SimpleNamespace] = []
    for key in order:
        members = members_by_key[key]
        if len(members) == 1:
            rows.append(SimpleNamespace(kind="single", signal=members[0]))
            continue

        top = members[0]
        rows.append(
            SimpleNamespace(
                kind="group",
                group_id=f"{key[0]}-{key[1]}",
                airport_id=top.airport_id,
                airport_name=top.airport_name,
                airport_code=top.airport_code,
                country=top.country,
                category_label=top.category_label,
                category_class=top.category_class,
                confidence_level=top.confidence_level,
                confidence_label=top.confidence_label,
                top_signal=top,
                more_count=len(members) - 1,
                members=members,
            )
        )
    return rows


def _airport_view(airport: Airport) -> SimpleNamespace:
    signal_views = [
        _signal_view(s)
        for s in sorted(
            airport.signals,
            key=lambda s: (s.probability_score is None, -(s.probability_score or 0)),
        )
    ]
    installation_views = [_installation_view(i) for i in airport.installations]
    incident_views = [
        SimpleNamespace(
            id=i.id,
            incident_date=i.incident_date,
            incident_type=i.incident_type,
            emas_engaged=i.emas_engaged,
            updated_at=i.updated_at,
        )
        for i in airport.incidents
    ]
    timeline_dated, timeline_undated = _timeline_view(installation_views, incident_views, signal_views)
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
        signals=signal_views,
        installations=installation_views,
        incidents=incident_views,
        timeline_dated=timeline_dated,
        timeline_undated=timeline_undated,
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
    shutil.copy2(STATIC_DIR / "watch.js", output_dir / "watch.js")

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    airports = session.scalars(
        select(Airport).options(
            selectinload(Airport.runways),
            selectinload(Airport.signals).selectinload(Signal.airport),
            selectinload(Airport.signals).selectinload(Signal.runway),
            selectinload(Airport.signals).selectinload(Signal.source),
            selectinload(Airport.installations).selectinload(Installation.source),
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
        trend=_trend_view([i for a in airport_views for i in a.installations], signal_views),
        recent_changes=_recent_changes_view(airport_views, signal_views),
        changelog_start_date=_CHANGELOG_START_DATE.isoformat(),
    )

    render(
        "ordlista.html",
        output_dir / "ordlista.html",
        root=".",
    )

    render(
        "om.html",
        output_dir / "om.html",
        root=".",
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
        signal_rows=_group_signal_views(signal_views),
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
