from __future__ import annotations

import json
import math
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
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, SourceAssertion
from app.services.airport_alias import get_admitted_airport_aliases
from app.services.evidence_claim_semantics import Claim
from app.services.manual_claim_evidence import get_manual_claims_for_source_assertion
from app.services.manual_identity_evidence import normalize_for_containment_check
from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end
from app.static_export.presentation import lifecycle_view, public_signal_state, status_view, text
from app.static_export.signal_lifecycle import SignalLifecycleState, derive_signal_lifecycle

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

# app.services.evidence_claim_semantics.ClaimCategory.value -> public-facing
# label ("RWI - Sacheon Evidence Surfacing - View-Model Slice" mission).
# The single place this mapping lives, per DESIGN_BRIEF.md's "Bygg denna
# mappning på ett ställe" - never a raw ClaimCategory value in a template.
# Generic over every existing member, never keyed on a specific claim's own
# content.
_CLAIM_CATEGORY_LABEL = {
    "explicit_document_fact": "Bekräftat sakförhållande",
    "procedural_request": "Begäran/förfarande",
    "temporal_statement": "Tidsuppgift",
    "relationship": "Ansvarig part",
}

# app.services.evidence_claim_semantics.TemporalQualifier.value -> public-
# facing label - same single-mapping discipline as _CLAIM_CATEGORY_LABEL.
_TEMPORAL_QUALIFIER_LABEL = {
    "historical_fact": "Historiskt förhållande",
    "current_state_as_of_document_date": "Aktuellt läge vid källans datum",
    "planned_future_action": "Planerad/kommande åtgärd",
    "requested_pending_approval": "Begärd, väntar godkännande",
    "completed": "Genomförd",
    "unknown": "Okänt tidsläge",
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


# SLT1: default-view relevance ordering for SignalLifecycleState - current/
# future opportunities first, then developing watch, then stale/unresolved
# (still worth surfacing for research priority), then realized/historical
# last (settled, own dedicated view - design doc S11). Never used to
# recompute or override probability_score itself, only as the primary sort
# key ahead of it (see _signal_sort_key below).
_LIFECYCLE_SORT_TIER = {
    SignalLifecycleState.ACTIVE_OPPORTUNITY: 0,
    SignalLifecycleState.DEVELOPING_WATCH: 1,
    SignalLifecycleState.STALE_UNRESOLVED: 2,
    SignalLifecycleState.REALIZED_HISTORICAL: 3,
    SignalLifecycleState.OTHER: 4,
}


def _signal_sort_key(view: SimpleNamespace) -> tuple:
    """Lifecycle tier first, existing score-descending meaning preserved as
    the tie-break within a tier, Signal id as a final deterministic
    tie-break (mission-required: "sorting inside a lifecycle tier remains
    deterministic" - the pre-SLT1 sort had no such guarantee for same-score
    rows). probability_score's own meaning is untouched; only sort order
    changes."""
    return (
        view.lifecycle_tier,
        view.probability_score is None,
        -(view.probability_score or 0),
        view.id,
    )


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
    return _SOURCE_TYPE.get(value, ("Övrig källa", None, None))


def _signal_view(signal: Signal, *, today: date) -> SimpleNamespace:
    source = signal.source
    category_label, category_class = _category_view(signal.category)
    confidence_level = _confidence_level(signal.confidence)
    source_type_label, source_type_anchor, source_type_tooltip = _source_type_view(
        source.source_type if source else None
    )
    public_status_label, public_qualification = public_signal_state(signal.id, signal.status)
    # SLT1 (docs/architecture/rwi-signal-temporal-relevance-opportunity-
    # lifecycle-design.md): a presentation-only, non-persisted read, never a
    # replacement for status/confidence/probability_score - see
    # app.static_export.signal_lifecycle's own module docstring.
    lifecycle = derive_signal_lifecycle(signal, today=today)
    lifecycle_label, lifecycle_class, lifecycle_tooltip = lifecycle_view(lifecycle.state.value)
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
        status_label=public_status_label,
        status_role=status_view(signal.status)[1],
        public_qualification=public_qualification,
        is_completed=signal.status == "completed",
        installation_id=signal.installation_id,
        lifecycle_state=lifecycle.state.value,
        lifecycle_tier=_LIFECYCLE_SORT_TIER[lifecycle.state],
        lifecycle_label=lifecycle_label,
        lifecycle_class=lifecycle_class,
        lifecycle_tooltip=lifecycle_tooltip,
        lifecycle_reason=lifecycle.reason,
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
        source_published_date=source.published_date if source else None,
    )


# ("RWI - Sacheon Evidence Surfacing - View-Model Slice" mission) Public-safe
# projection of one governed Claim (app.services.evidence_claim_semantics,
# produced generically by any extractor - MAC Granicus, USAspending, or
# ManualClaimEvidence - never a Sacheon/Korean-specific shape). Exposes only
# what the mission's own public/internal boundary allows: category label,
# the claim's own already-public-safe `statement` (a normalized restatement,
# never raw governance text), the literal preserved `raw_text_excerpt` in
# its ORIGINAL language/script exactly as governed (never translated,
# summarized, or altered - see Claim.provenance's own docstring), and
# optional temporal/relationship context. Deliberately excludes:
# provenance.artifact_identity/source_locator/fragment_hash (internal
# identity plumbing, not meaningful to a visitor - the Signal's own
# source_url/source_title already carry the public-facing provenance),
# `financial` (Signal already has its own dedicated, USD-only financial
# fields/card - this slice does not duplicate or extend financial
# semantics), and `relationship.role`/`.scope` (free-text governance
# classification vocabulary, not intended for direct public display -
# `claim.statement` already restates the relationship in plain language).
def _claim_view(claim: Claim) -> SimpleNamespace:
    temporal_label = None
    temporal_detail = None
    if claim.temporal is not None:
        temporal_label = _TEMPORAL_QUALIFIER_LABEL.get(claim.temporal.qualifier.value, claim.temporal.qualifier.value)
        temporal_detail = claim.temporal.detail
    return SimpleNamespace(
        category_label=_CLAIM_CATEGORY_LABEL.get(claim.category.value, claim.category.value),
        statement=claim.statement,
        excerpt=claim.provenance.raw_text_excerpt,
        temporal_label=temporal_label,
        temporal_detail=temporal_detail,
        relationship_party=claim.relationship.party if claim.relationship is not None else None,
    )


# Read-only, per-Signal evidence projection - reuses the existing governed
# read service (app.services.manual_claim_evidence.get_manual_claims_for_source_assertion)
# verbatim, never a second, parallel claims-reading implementation. Walks
# Signal.supporting_source_assertions (the existing governed Signal<-
# SourceAssertion link, Slice 9C - never a new relationship). Fails safely
# by construction: a Signal with no supporting SourceAssertion iterates zero
# times and returns an empty tuple; a SourceAssertion with no
# ManualClaimEvidence, or whose effective identity is not currently
# ATTACH_CONFIRMED, is skipped because the read service itself returns None
# for both cases (see that function's own docstring) - this projection adds
# no eligibility logic of its own. Deliberately NOT attached to
# _signal_view()'s own returned object - see _build()'s own "DATA.JSON"
# note for why evidence is threaded as a separate, signal_detail.html-only
# template context variable instead.
def _evidence_view(signal: Signal, *, session: Session) -> "tuple[SimpleNamespace, ...]":
    claims: "list[Claim]" = []
    for source_assertion in signal.supporting_source_assertions:
        found = get_manual_claims_for_source_assertion(session, source_assertion.id)
        if found:
            claims.extend(found)
    return tuple(_claim_view(c) for c in claims)


# Read-only public projection of an Airport's currently-ADMITTED aliases
# (app.services.airport_alias.get_admitted_airport_aliases(), reused
# verbatim - never a second alias-reading implementation, never a raw query
# against airport_aliases). REJECTED/RETIRED/superseded rows are already
# excluded by that function's own "current by recency" derivation - this
# projection adds no filtering of its own beyond suppressing a duplicate of
# the canonical name (mission's own explicit instruction), using the exact
# same normalize_for_containment_check() the alias-admission service itself
# uses for alias-key comparison (no second normalization implementation).
# The AirportAlias model carries no local-language/alternate-name TYPE
# distinction (language/script are audit-only, never read by any matching
# or presentation code) - so this renders as one plain, undifferentiated
# alternate/local-name list, exactly as the mission's own fallback
# instruction requires; no distinction is invented here.
def _alias_view(airport: Airport, *, session: Session) -> "tuple[str, ...]":
    aliases = get_admitted_airport_aliases(session, airport.id)
    canonical_key = normalize_for_containment_check(airport.name or "")
    return tuple(sorted(a for a in aliases if normalize_for_containment_check(a) != canonical_key))


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


def _runway_view(runway: Runway) -> SimpleNamespace:
    """Minimal public projection of one governed canonical Runway - the
    physical runway pair's designation only (docs/product/public-canonical-
    runway-inventory-report.md). No id, no length/width/surface: `surface`
    is a mix of human-readable text and raw NASR codes (ASPH/CONC/GRVL/...)
    across the 180 governed rows - consistent in population, not in style -
    so it is left unpublished for now rather than shown inconsistently.
    Deliberately says nothing about EMAS; that remains driven entirely by
    reviewed_identities/nasr_presence below, unchanged by this."""
    return SimpleNamespace(designation=runway.designation)


# --- Current-EMAS public presentation ---------------------------------
# docs/product/public-emas-protected-direction-presentation.md.
#
# Every current-EMAS field this project has ever governed
# (SourceAssertion.runway_end, PhysicalInstallationIdentity.runway_end_id)
# means the canonical PHYSICAL RunwayEnd an authoritative source reports -
# never the reciprocal/protected-direction end (docs/domain/emas-runway-
# end-semantics-and-nasr-promotion-analysis.md S9, confirmed by every
# real MDW/CGF InstallationAssertionLink.reason, which quotes the NASR
# value unchanged). BOS's own evidence proved the public/operator-facing
# name for a bed is usually the *reciprocal* end instead (Massport's own
# language: a bed NASR reports at 04L is "Runway 22R's EMAS"). The public
# page should lead with that protected direction while keeping the
# physical value as visible provenance - never inventing the reciprocal,
# only deriving it via canonical Runway<->RunwayEnd topology, which is
# 100% deterministic (every governed Runway has exactly two RunwayEnd
# rows, verified nationwide with zero exceptions).

_CURRENT_EMAS_BASIS_LABEL = {
    "reviewed": "Granskad identitet",
    "nasr": "FAA NASR aktuell förekomst",
}
# Separate from _CURRENT_EMAS_BASIS_LABEL (the badge text) because "FAA
# NASR" reads correctly capitalized in a badge but awkward mid-sentence if
# naively lowercased for prose ("enligt faa nasr...") - own phrasing per
# basis keeps both correct instead of deriving one from the other.
_CURRENT_EMAS_PROVENANCE_PHRASE = {
    "reviewed": "granskad identitet",
    "nasr": "FAA NASR",
}


def _find_canonical_runway_end(airport: Airport, physical_designation: str | None) -> RunwayEnd | None:
    """Topology lookup only - the single canonical RunwayEnd on this
    airport whose normalized designation matches `physical_designation`.
    None (fail closed) on zero or more than one match; never guesses."""
    if not physical_designation:
        return None
    try:
        target = normalize_end(physical_designation)
    except AmbiguousRunwayDesignationError:
        return None
    candidates = [
        end for runway in airport.runways for end in runway.runway_ends
        if _normalize_end_safe(end.designation) == target
    ]
    return candidates[0] if len(candidates) == 1 else None


def _normalize_end_safe(designation: str) -> str | None:
    try:
        return normalize_end(designation)
    except AmbiguousRunwayDesignationError:
        return None


def _protected_direction(runway_end: RunwayEnd) -> str | None:
    """The OTHER RunwayEnd on the same canonical Runway - pure topology,
    never designation arithmetic or string tricks. Fails closed (None)
    unless the parent Runway has exactly two governed RunwayEnd rows."""
    siblings = [e for e in runway_end.runway.runway_ends if e.id != runway_end.id]
    return siblings[0].designation if len(siblings) == 1 else None


def _current_emas_item(
    *, airport: Airport, physical_designation: str | None, evidence_basis: str, cycle: str | None
) -> tuple[object, SimpleNamespace] | None:
    """Builds one public current-EMAS item plus its dedup key (the
    canonical RunwayEnd.id when resolvable, so a reviewed identity and an
    unrelated NASR assertion that resolve to the same physical bed
    collide and dedupe; a raw fallback key otherwise so an unresolvable
    item still renders standalone rather than being silently dropped).
    Returns None only when there is no physical designation at all."""
    if not physical_designation:
        return None
    canonical_end = _find_canonical_runway_end(airport, physical_designation)
    protected = _protected_direction(canonical_end) if canonical_end else None
    primary_label = f"Bana {protected}" if protected else f"Bana {physical_designation}"
    provenance = f"Fysisk placering enligt {_CURRENT_EMAS_PROVENANCE_PHRASE[evidence_basis]}: bana {physical_designation}."
    if evidence_basis == "nasr" and cycle:
        provenance += f" NASR-cykel {cycle}. Uppgiften beskriver förekomst vid banände, inte projektstatus eller fysisk historik."
    dedup_key = ("end", canonical_end.id) if canonical_end else ("raw", airport.id, evidence_basis, physical_designation)
    item = SimpleNamespace(
        primary_label=primary_label,
        physical_runway_end=physical_designation,
        protected_runway_direction=protected,
        evidence_basis=evidence_basis,
        evidence_basis_label=_CURRENT_EMAS_BASIS_LABEL[evidence_basis],
        provenance_text=provenance,
    )
    return dedup_key, item


def _current_emas_views(airport: Airport) -> list[SimpleNamespace]:
    """Merges the two governed current-EMAS pathways (reviewed
    PhysicalInstallationIdentity and raw NASR current-presence
    SourceAssertion) into one deduplicated, presentation-ready list.
    Reviewed identity is added first and always wins a dedup collision -
    it is higher-governance evidence than an unreviewed NASR assertion
    describing the same physical bed (docs/product/public-emas-protected-
    direction-presentation.md). Presentation only - does not change which
    rows are governed or reviewed."""
    by_key: dict[object, SimpleNamespace] = {}

    reviewed = [
        identity for identity in airport.physical_installation_identities
        if any(link.outcome == "SAME_PHYSICAL_INSTALLATION" for link in identity.assertion_links)
    ]
    for identity in reviewed:
        result = _current_emas_item(
            airport=airport, physical_designation=identity.runway_end, evidence_basis="reviewed", cycle=None
        )
        if result:
            key, item = result
            by_key[key] = item  # reviewed always wins; nothing has been inserted yet for this key

    nasr = [
        assertion for assertion in airport.source_assertions
        if assertion.source and assertion.source.external_id
        and assertion.source.external_id.startswith("faa_nasr:airport_csv:")
        and assertion.assertion_type == "runway_end" and assertion.runway_end
    ]
    for assertion in nasr:
        source = assertion.source
        cycle = source.external_id.split(":")[2] if source and source.external_id else None
        result = _current_emas_item(
            airport=airport, physical_designation=assertion.runway_end, evidence_basis="nasr", cycle=cycle
        )
        if result:
            key, item = result
            by_key.setdefault(key, item)  # reviewed (inserted above) always wins on collision

    return sorted(by_key.values(), key=lambda item: item.primary_label)


def _is_public_signal(signal: Signal) -> bool:
    """Whether a Signal is eligible for public/static export.

    Historically a hardcoded exclusion of the two baseline-identified
    unnormalized airport Signals (ids 52, 54). Slice 9A
    (docs/architecture/signal-publication-separation-slice9a-report.md)
    replaced that hardcoding with the explicit Signal.published column,
    backfilled so this function's output is unchanged for every pre-existing
    Signal - only Signal.published now decides publication.
    """
    return signal.published


# ("RWI - Juicy Design Mission #4" mission) Public information model /
# semantic recon findings for the lower Airport Detail page, reproduced here
# as the single source of truth (full matrix: docs/architecture, final
# report):
#
#   INSTALLATION (Installation, persisted)
#     install_year, when present, is a real dated historical EVENT (a
#     physical system going in) - correctly chronological. When ABSENT
#     (69 of 149 real rows database-wide), the row is not "missing a date
#     that should be filled in" - it is a different KIND of record: a raw
#     current-physical-presence snapshot (e.g. BOS installation #33, source
#     "FAA EMAS Incidents and Installations map", itself has no
#     published_date either - there is no date to recover). Mixing both
#     kinds into one merged Tidslinje/"Odaterat" pair (the pre-Mission-#4
#     behavior) is exactly what produced the BOS "EMASMAX appears both
#     dated and undated" appearance investigated in Section 3 of this
#     mission - see BOS_APPARENT_DUPLICATION_CAUSE in the mission's final
#     report. RESOLUTION: Installation rows no longer feed this
#     chronological timeline at all - they remain fully presented,
#     source-and-all, in their own dedicated "Historisk EMAS-kontext"
#     disclosure below (_installation_view, unchanged), which already
#     honestly labels an undated row as current physical state rather than
#     a historical event (airport_detail.html's own dated/undated label).
#
#   INCIDENT (Incident, persisted)
#     incident_date is a required (non-nullable) column - always a real
#     event date, always chronological, always historical by definition
#     (something that already happened).
#
#   SIGNAL (Signal, persisted)
#     year source: target_year -> planning_year (the same fallback chain
#     this function already used pre-Mission-#4; unchanged). A funding-type
#     Signal (Source.source_type in the grant vocabulary) is tagged
#     event_type="funding" rather than "project" - Section 12's own
#     instruction that a grant amount must never visually imply EMAS
#     contract/procurement value. current/historical relevance reuses
#     Signal's own already-computed SLT1 lifecycle_state/label/class/tooltip
#     verbatim (signal_lifecycle.py) - Section 6's explicit instruction to
#     separate current from historical "WITHOUT inventing a new
#     business-domain status". source_published_date (the SOURCE's own
#     publication date) is carried through for display ONLY as its own,
#     separately-labeled field - never substituted for, or silently treated
#     as equivalent to, the event's own year (Section 11).
#
#   Events without a resolvable year go to a separate `undated` list, each
#   carrying an honest `date_reason` (Section 5): "no_event_date" when the
#   underlying Source itself has a real published_date but the Signal's own
#   event-year fields do not (the record is dated, just not for THIS
#   event), or "no_date" when neither exists. Never an invented date.
_GRANT_SOURCE_TYPES_TIMELINE = frozenset({"usaspending_grant", "aip_grant", "iija_grant"})

_FUNDING_CAVEAT = (
    "Ett bidragsbelopp anger inte automatiskt ett EMAS-kontraktsvärde, en leverantörsintäkt, "
    "en total projektkostnad eller en genomförd upphandling - se \"Bedömd EMAS-del\" på signalens "
    "egen sida för vad som faktiskt är fastställt."
)


def _timeline_event(
    *, kind: str, id: int, year: int | None, day: date | None,
    event_type: str, category_class: str, category_label: str, title: str, subtitle: str | None,
    lifecycle_label: str | None = None, lifecycle_class: str | None = None, lifecycle_tooltip: str | None = None,
    source_type_label=None, source_type_anchor=None, source_type_tooltip=None,
    source_url=None, source_published_date=None, source_title=None,
    financial_total_usd=None, financial_caveat=None,
    date_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        id=id,
        year=year,
        day=day,
        event_type=event_type,
        category_class=category_class,
        category_label=category_label,
        title=title,
        subtitle=subtitle,
        lifecycle_label=lifecycle_label,
        lifecycle_class=lifecycle_class,
        lifecycle_tooltip=lifecycle_tooltip,
        source_type_label=source_type_label,
        source_type_anchor=source_type_anchor,
        source_type_tooltip=source_type_tooltip,
        source_url=source_url,
        source_published_date=source_published_date,
        source_title=source_title,
        financial_total_usd=financial_total_usd,
        financial_caveat=financial_caveat,
        date_reason=date_reason,
    )


def _intelligence_history_view(
    incidents: list[SimpleNamespace],
    signals: list[SimpleNamespace],
) -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    """Chronological PROJECT/EVENT history for airport_detail.html's
    "Intelligenshistorik" section - Signals and Incidents only (see this
    module's own "public information model" note above for why Installation
    rows were removed from this merge in Mission #4; they keep their own
    dedicated, unchanged "Historisk EMAS-kontext" disclosure instead).
    Deliberately implies no causal relationship between adjacent events -
    each event stands on its own real date and its own real source."""
    dated: list[SimpleNamespace] = []
    undated: list[SimpleNamespace] = []

    for incident in incidents:
        event = _timeline_event(
            kind="incident",
            id=incident.id,
            year=incident.incident_date.year,
            day=incident.incident_date,
            event_type="incident",
            category_class="incident",
            category_label="Incident",
            title=incident.incident_type,
            subtitle="EMAS aktiverat" if incident.emas_engaged else "EMAS inte aktiverat",
            source_url=incident.source_url,
        )
        dated.append(event)

    for signal in signals:
        year = signal.target_year or signal.planning_year
        is_funding = signal.source_type in _GRANT_SOURCE_TYPES_TIMELINE
        event_type = "funding" if is_funding else "project"
        date_reason = None
        if year is None:
            date_reason = "no_event_date" if signal.source_published_date else "no_date"
        event = _timeline_event(
            kind="signal",
            id=signal.id,
            year=year,
            day=None,
            event_type=event_type,
            category_class=signal.category_class,
            category_label=signal.category_label,
            title=signal.title,
            subtitle=signal.status_label,
            lifecycle_label=signal.lifecycle_label,
            lifecycle_class=signal.lifecycle_class,
            lifecycle_tooltip=signal.lifecycle_tooltip,
            source_type_label=signal.source_type_label,
            source_type_anchor=signal.source_type_anchor,
            source_type_tooltip=signal.source_type_tooltip,
            source_url=signal.source_url,
            source_published_date=signal.source_published_date,
            source_title=signal.source_title,
            financial_total_usd=(signal.estimated_total_value_usd if is_funding else None),
            financial_caveat=(_FUNDING_CAVEAT if is_funding else None),
            date_reason=date_reason,
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
    limit: int = 5,
    as_of: date | None = None,
) -> list[SimpleNamespace]:
    """A bounded public-evidence feed, not a raw database change log.

    Only governed-source Signals can qualify. Historical Incident rows and
    ``identified`` watch items are excluded. Active project states require a
    source published within 365 days or a current/upcoming planning year;
    completed work requires a recent source. The displayed date is always the
    governed source date, never a row-touch timestamp.
    """
    del airport_views
    as_of = as_of or date.today()
    recent_cutoff = date(as_of.year - 1, as_of.month, as_of.day)
    active_statuses = {
        "funded", "design", "procurement", "under construction",
        "environmental_review", "master_plan", "alp", "cip",
    }
    entries: list[SimpleNamespace] = []
    for signal in signal_views:
        source_date = signal.source_published_date
        if source_date is None or signal.status == "identified":
            continue
        upcoming = (signal.target_year or signal.planning_year or 0) >= as_of.year
        recent_source = source_date >= recent_cutoff
        active = signal.status in active_statuses and (recent_source or upcoming)
        completion = signal.status == "completed" and recent_source
        if not (active or completion):
            continue
        entries.append(SimpleNamespace(
            kind="signal", id=signal.id, category_label=signal.category_label,
            category_class=signal.category_class, title=signal.title,
            airport_id=signal.airport_id, airport_code=signal.airport_code,
            airport_name=signal.airport_name, evidence_date=source_date,
        ))
    entries.sort(key=lambda e: (e.evidence_date, e.id), reverse=True)
    return [SimpleNamespace(**vars(e), date_label=e.evidence_date.isoformat()) for e in entries[:limit]]


def _lifecycle_counts_view(signal_views: list[SimpleNamespace]) -> list[SimpleNamespace]:
    """One row per SignalLifecycleState, in the same relevance order as
    _LIFECYCLE_SORT_TIER, each carrying its own real count over the exact
    signal_views passed in (never a separately-queried or hardcoded number)
    - feeds the stats bar + filter option labels on signals_list.html."""
    counts = Counter(view.lifecycle_state for view in signal_views)
    return [
        SimpleNamespace(
            state=state.value,
            label=lifecycle_view(state.value)[0],
            css_class=lifecycle_view(state.value)[1],
            tooltip=lifecycle_view(state.value)[2],
            count=counts.get(state.value, 0),
        )
        for state in sorted(SignalLifecycleState, key=lambda s: _LIFECYCLE_SORT_TIER[s])
    ]


# ("RWI - Mission #7C" mission) "Marknadsläge" - the market-centric
# presentation Missions #7/#7A/#7B recon'd and designed, built entirely
# from already-governed Signal data + the already-existing SLT1 lifecycle
# derivation (derive_signal_lifecycle, unchanged) - no new domain concept,
# no new persisted field, no FH-D4 read (deliberately deferred per Mission
# #7C's own HQ decision A - this module never imports SignalDisposition).
#
# Raw published Signal counts/rows only - never a deduplicated "effort"
# count, never a canonical Signal/title/lifecycle/category chosen for a
# group, matching Mission #7B/#7C's explicit non-authorization of FH-D4
# canonical representation.
def _market_category_distribution_view(signal_views: "list[SimpleNamespace]") -> "list[SimpleNamespace]":
    """Real, deterministic Project Type (category) breakdown over exactly
    the `signal_views` the caller passes in - this function has no opinion
    about WHICH signals belong in "Efter projekttyp"; `_market_intelligence_view`
    below is the one place that decides (active_opportunity + developing_watch
    only, per Mission #7C section 2E - never the full published set, which
    would only restate the Overview's own existing stage donut). Reuses
    `_category_view()` verbatim - the same single mapping every other
    category label on the site already uses; no new project-type
    classification is introduced. Returns `[]` for an empty input (template
    shows an honest empty-state, never a fabricated category)."""
    total = len(signal_views)
    if not total:
        return []
    counts = Counter(view.category for view in signal_views)
    ordered = sorted(counts.items(), key=lambda item: -item[1])
    return [
        SimpleNamespace(
            category=category,
            label=_category_view(category)[0],
            css_class=_category_view(category)[1],
            count=count,
            pct=round(100 * count / total),
        )
        for category, count in ordered
    ]


def _market_intelligence_view(signal_views: "list[SimpleNamespace]") -> SimpleNamespace:
    """Top-level view model for marknadslage.html. `signal_views` is the
    same already-published, already-sorted (_signal_sort_key: lifecycle
    tier, then score descending, then id) list every other page on the
    site already uses - filtering it by `lifecycle_state` preserves that
    same relative order within each bucket, so no re-sort is needed here.

    `snapshot` reuses `_lifecycle_counts_view()` verbatim (same function
    signals_list.html's own stats bar already calls) - real counts across
    all five SLT1 states, never hardcoded.

    `realized_historical_count` is a bare integer, deliberately never a
    list - Mission #7C section 2/8 requires Historik to remain a
    snapshot-only count in v1, with no standalone list view for it (real
    population is 2 rows - see Mission #7B's own real-data inventory)."""
    active = [v for v in signal_views if v.lifecycle_state == "active_opportunity"]
    watch = [v for v in signal_views if v.lifecycle_state == "developing_watch"]
    stale = [v for v in signal_views if v.lifecycle_state == "stale_unresolved"]
    historical_count = sum(1 for v in signal_views if v.lifecycle_state == "realized_historical")
    return SimpleNamespace(
        snapshot=_lifecycle_counts_view(signal_views),
        active_opportunities=active,
        developing_watch=watch,
        stale_unresolved=stale,
        realized_historical_count=historical_count,
        # Mission #7C section 2E: active_opportunity + developing_watch
        # ONLY - never the full published set.
        category_distribution=_market_category_distribution_view(active + watch),
    )


# Purely typographic/decorative - a real country name already present on
# every signal_view (Airport.country) gets a flag emoji next to it, nothing
# more. Deliberately not exhaustive: an unmapped country (any real value not
# listed here) simply renders with no flag - see _market_summary_view's own
# template usage - never a placeholder/wrong flag, and never a reason to
# block the build.
_COUNTRY_FLAG = {
    "USA": "🇺🇸", "United States": "🇺🇸", "Brazil": "🇧🇷", "New Zealand": "🇳🇿",
    "South Korea": "🇰🇷", "Canada": "🇨🇦", "Mexico": "🇲🇽", "United Kingdom": "🇬🇧",
    "Sweden": "🇸🇪", "Norway": "🇳🇴", "Finland": "🇫🇮", "Denmark": "🇩🇰",
    "Germany": "🇩🇪", "France": "🇫🇷", "Japan": "🇯🇵", "China": "🇨🇳",
    "Australia": "🇦🇺", "India": "🇮🇳",
}


# ("RWI - Juicy Design Mission #1" mission, restyled by "RWI - Juicy Design
# Mission #2") Real, deterministic per-country breakdown of public Signals -
# the Overview's "clean geographic/market summary" (no fabricated map: this
# repository has no reliable, complete airport coordinate coverage). Counts
# only, from signal_views' already-computed `country` field (Airport.country,
# always present) - no geocoding, no invented region groupings.
#
# `is_dominant` marks only the single largest market (ties broken by country
# name, same as the sort itself) - Mission #2's own explicit instruction not
# to visually inflate a small market applies with equal force in the other
# direction: the real leading market must be the one visually emphasized,
# never an arbitrary top-N cutoff that could flatter a minor one. Every
# other market renders at the same, smaller, plain-list weight - genuinely
# proportional to the real gap in the data (see the module's own real-data
# recon: USA=64 vs every other real country=1).
def _market_summary_view(signal_views: list[SimpleNamespace]) -> list[SimpleNamespace]:
    counts = Counter(s.country for s in signal_views if s.country)
    if not counts:
        return []
    max_count = max(counts.values())
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        SimpleNamespace(
            country=country,
            flag=_COUNTRY_FLAG.get(country),
            count=count,
            share_pct=round(100 * count / max_count),
            pct_of_total=round(100 * count / total),
            is_dominant=(index == 0),
        )
        for index, (country, count) in enumerate(ordered)
    ]


# One representative display label per STATUS_PRESENTATION `role` (never a
# new status taxonomy - presentation.py's own existing role vocabulary,
# reused verbatim: DESIGN_BRIEF.md's "build this mapping in one place").
# Grouping by role rather than by the finer-grained raw status text is
# deliberate: several distinct real status values legitimately share one
# role (alp/cip/master_plan -> "planning"), and the reference's own donut
# is a small handful of stage buckets, not one slice per raw database
# value. "identified" is intentionally omitted here - its label is always
# the already-approved public research-watch copy (text("research_watch")),
# fetched dynamically so it stays in sync with that single source of truth
# rather than being duplicated as a second, driftable copy of the same
# string.
_STATUS_ROLE_LABEL = {
    "completed": "Färdigställd",
    "funded": "Finansierad",
    "design": "Projektering",
    "procurement": "Upphandling",
    "construction": "Under byggnation",
    "review": "Miljöprövning",
    "planning": "Planering",
    "unknown": "Ej klassificerad",
}

# ("RWI - Juicy Design Mission #2" mission) Real, deterministic distribution
# of public Signals across pipeline stage (status_view()'s own `role`
# grouping - the same one .pill.status/.hero-status color classes already
# use). Real percentages of the real total, never invented. A role-bucket
# with only a single Signal is folded into "Övrigt" (Other) whenever at
# least two such singleton buckets exist - a real-count aggregation, not a
# fabricated category (see the module's own real-data recon: without this,
# the donut would show three separate 1-of-67 slivers). Returns () for an
# empty signal_views (template shows an empty-state instead of a broken
# chart).
#
# `dash_array`/`dash_offset` are precomputed SVG stroke geometry for a pure-
# CSS/SVG donut (same "compute the geometry once, in Python, template only
# loops over ready-made numbers" division of labour _trend_view() already
# established for the install-trend chart - no chart library here either).
_DONUT_RADIUS = 54
_DONUT_CIRCUMFERENCE = round(2 * 3.14159265358979 * _DONUT_RADIUS, 2)
_STAGE_OTHER_ROLE = "unknown"  # reuses the existing neutral/muted color token; never a fabricated new role color


def _stage_distribution_view(signal_views: list[SimpleNamespace]) -> list[SimpleNamespace]:
    total = len(signal_views)
    if not total:
        return []
    role_counts = Counter(v.status_role for v in signal_views)

    singleton_roles = {role for role, count in role_counts.items() if count == 1}
    fold_singletons = len(singleton_roles) >= 2
    other_count = sum(count for role, count in role_counts.items() if fold_singletons and role in singleton_roles)

    buckets: list[tuple[str, str, int]] = []  # (label, role, count)
    for role, count in role_counts.items():
        if fold_singletons and role in singleton_roles:
            continue
        label = text("research_watch") if role == "identified" else _STATUS_ROLE_LABEL.get(role, role)
        buckets.append((label, role, count))
    if other_count:
        buckets.append(("Övrigt", _STAGE_OTHER_ROLE, other_count))

    buckets.sort(key=lambda item: -item[2])

    segments = []
    offset = 0.0
    for label, role, count in buckets:
        fraction = count / total
        arc = round(fraction * _DONUT_CIRCUMFERENCE, 2)
        segments.append(
            SimpleNamespace(
                label=label, role=role, count=count, pct=round(100 * fraction),
                dash_array=f"{arc} {_DONUT_CIRCUMFERENCE}",
                dash_offset=round(-offset, 2),
            )
        )
        offset += arc
    return segments


# ("RWI - Juicy Design Mission #2 - V2.3/V2.4" missions) Geo recon (real,
# live read against data/runway_safe.db, reproduced here as the single
# source of truth for why this is COUNTRY-LEVEL, never airport-point,
# geographic intelligence): 70 of 88 Airports carry governed latitude/
# longitude - but every one of those 70 is a USA row, populated exclusively
# by scripts/import_faa_csv.py (a US-only FAA dataset). Every non-US
# Airport with real current activity (Brazil, New Zealand, South Korea -
# the exact countries _market_summary_view's own real-data recon already
# found) has NULL latitude/longitude - zero exceptions. Airport-point mode
# would therefore render real dots for the USA only and silently omit
# every other real market from a component whose entire purpose is showing
# where RWI sees GLOBAL activity - the opposite of this mission's own
# intent. Mission Section 6 itself authorizes exactly this fallback ("If
# coordinates do NOT exist: country mode only. That is completely
# acceptable").
#
# V2.4's own map-geometry recon: no world-map SVG/GeoJSON existed anywhere
# in this repository. Rather than fall back to an abstract diagram
# (V2.4's own explicit instruction), a real world landmass silhouette was
# sourced from `world-atlas` (topojson/world-atlas, ISC license, built from
# Natural Earth's own public-domain 1:110m land-boundary data) - see
# app/static_export/geo/PROVENANCE.md for the exact fetch, license text,
# and the (lossless, standard) TopoJSON-decode/equirectangular-projection/
# antimeridian-split processing applied to produce
# app/static_export/geo/world_land_110m.path.txt, read once at import time
# below. This is a genuine, recognizable world map - never a pseudo-map,
# never downloaded silently (documented, licensed, redistributable, per
# Section 3's own requirement).
#
# Country markers still never claim per-airport precision (Section 4): each
# real country in `market_summary` is placed at one small, hand-authored,
# non-exhaustive REPRESENTATIVE country-centroid position
# (_COUNTRY_MAP_POSITION - common-knowledge approximate geographic
# centroids, not derived from or claiming to be any governed RWI Airport
# coordinate) - the exact same "small, non-exhaustive, gracefully-omit-if-
# missing" discipline _COUNTRY_FLAG already established. A real country not
# in this dict simply gets no map pin (its real numbers still appear in the
# legend, which is never gated on map placement).
_MAP_VIEWBOX_WIDTH = 900
_MAP_VIEWBOX_HEIGHT = 360
_MAP_NODE_MIN_RADIUS = 7
_MAP_NODE_MAX_RADIUS = 30
_MAP_LAT_MAX = 83  # matches the exact projection used to produce world_land_110m.path.txt
_MAP_PX_PER_DEG = _MAP_VIEWBOX_WIDTH / 360.0

_WORLD_LAND_PATH = (Path(__file__).parent / "geo" / "world_land_110m.path.txt").read_text(encoding="utf-8")

# Approximate geographic centroids (whole-degree lon/lat, common public
# knowledge - e.g. the same rough country-center points widely published in
# atlases/reference works), never a governed RWI coordinate and never
# claiming per-airport precision. Same country set as _COUNTRY_FLAG above,
# for the same "small, non-exhaustive, safe to be incomplete" reason.
_COUNTRY_MAP_POSITION = {
    "USA": (-98.5, 39.8), "United States": (-98.5, 39.8), "Brazil": (-51.9, -14.2),
    "New Zealand": (172.8, -41.5), "South Korea": (127.8, 36.5), "Canada": (-106.3, 56.1),
    "Mexico": (-102.5, 23.6), "United Kingdom": (-3.4, 55.4), "Sweden": (18.6, 60.1),
    "Norway": (8.5, 60.5), "Finland": (25.7, 61.9), "Denmark": (9.5, 56.3),
    "Germany": (10.5, 51.2), "France": (2.2, 46.2), "Japan": (138.3, 36.2),
    "China": (104.2, 35.9), "Australia": (133.8, -25.3), "India": (78.9, 20.6),
}


def _project_lon_lat(lon: float, lat: float) -> "tuple[float, float]":
    """The exact same equirectangular projection used to produce
    world_land_110m.path.txt (see PROVENANCE.md) - so a country marker and
    the land silhouette beneath it always share one coordinate system."""
    x = (lon + 180) * _MAP_PX_PER_DEG
    y = (_MAP_LAT_MAX - lat) * _MAP_PX_PER_DEG
    return round(x, 1), round(y, 1)


def _global_intelligence_view(market_summary: "list[SimpleNamespace]") -> "tuple[SimpleNamespace, ...]":
    """Deterministic node geometry for the "Global intelligens" panel, built
    entirely from `market_summary` (already-computed, already-real per-
    country activity - _market_summary_view() itself, no second data
    source, no new "activity" definition) plus the small, non-exhaustive
    `_COUNTRY_MAP_POSITION` lookup above. Node radius is
    sqrt(pct_of_total / max_pct)-scaled between a min/max pixel radius
    (sqrt, not linear, so relative AREA - the visually perceived quantity
    for a filled circle - stays proportional to the real share). The
    min-radius floor is Section header 5/6's own "smaller markets remain
    clearly visible" instruction - a real 1-Signal market is still drawn,
    just small, never zero-sized/hidden. A real market whose country is not
    in `_COUNTRY_MAP_POSITION` is simply omitted from the returned tuple -
    its real numbers still render in the template's own legend list, which
    is built from `market_summary` directly, never gated on map placement."""
    nodes = []
    if market_summary:
        max_pct = max(m.pct_of_total for m in market_summary) or 1
        for m in market_summary:
            position = _COUNTRY_MAP_POSITION.get(m.country)
            if position is None:
                continue
            x, y = _project_lon_lat(*position)
            scale = math.sqrt(m.pct_of_total / max_pct)
            radius = _MAP_NODE_MIN_RADIUS + scale * (_MAP_NODE_MAX_RADIUS - _MAP_NODE_MIN_RADIUS)
            nodes.append(
                SimpleNamespace(
                    country=m.country, flag=m.flag, count=m.count, pct_of_total=m.pct_of_total,
                    is_dominant=m.is_dominant, x=x, y=y, radius=round(radius, 1),
                )
            )
    return tuple(nodes)


# ("RWI - Juicy Design Mission #2 - V2.3" mission) "Viktiga utvecklingar" -
# a small, deterministic, EXPLAINABLE selection, never an invented
# "importance score" and never a claim of AI ranking (no such governed
# mechanism exists in this repository). The rule: real public Signals
# currently in the most advanced/committed real pipeline stages - under
# construction, in procurement, or funded - using the exact same
# status_role vocabulary .hero-status/.pill.status already color-code
# sitewide (never a new taxonomy). This is deliberately NOT the same
# selection as Signalöversikt's own top_signals (score-ranked, unfiltered
# by stage) - some overlap between the two lists is expected and honest
# (a real construction-stage Signal legitimately belongs in both a
# "highest-scored" list and a "most advanced-stage" list), not a bug to
# engineer away. Ties within a stage are broken by the same
# _signal_sort_key() every other ranked list on this page already uses -
# no new ordering rule invented. Returns () when no Signal currently
# occupies one of these stages (an honest, real possibility - the template
# shows an empty-state, never a fabricated placeholder).
_COMMITTED_PIPELINE_ROLES = frozenset({"construction", "procurement", "funded"})


def _important_developments_view(
    signal_views: "list[SimpleNamespace]", *, limit: int = 5
) -> "tuple[SimpleNamespace, ...]":
    committed = [s for s in signal_views if s.status_role in _COMMITTED_PIPELINE_ROLES]
    return tuple(sorted(committed, key=_signal_sort_key)[:limit])


# ("RWI - Juicy Design Mission #3" mission) Public information model /
# semantic recon findings, reproduced here as the single source of truth:
#
#   STATUS (Signal.status, persisted)      -> PRIMARY PROJECT PHASE
#     Already public-safe as `status_label` (public_signal_state() already
#     qualifies "identified" -> the research-watch copy) - the ONE
#     dominant badge in the airport-header (Mission #1/#2), unchanged here.
#
#   CATEGORY (Signal.category, persisted)  -> PROJECT TYPE
#     Already public-safe as `category_label` - a plain quick-fact,
#     unchanged here.
#
#   CONFIDENCE (Signal.confidence, persisted) -> EVIDENCE STRENGTH
#     Already public-safe as the confidence gauge - unchanged here.
#
#   LIFECYCLE (derived, SLT1, non-persisted) -> opportunity/temporal
#     relevance. NOT a fourth phase - it is frequently near-tautological
#     with status for a committed-pipeline Signal (signal_lifecycle.py's
#     own rule 5: a committed status like "under construction" already
#     implies ACTIVE_OPPORTUNITY unless its year is stale) - its real
#     value is distinguishing STALE from WATCH for less-advanced/older
#     evidence, not competing with the dominant phase badge for an
#     already-active one. Demoted from a colored quick-fact pill (Mission
#     #1/#2) to prose-only context inside "Varför RWI bevakar detta"
#     (airport_detail.html) - still fully visible, never hidden, just no
#     longer presented as if it were a second status dimension.
#
# No domain change was required for any of this - every one of these
# fields already existed, persisted, on Signal; only the VIEW/TEMPLATE
# layer changes in this mission. See docs/architecture (final report) for
# the full semantic matrix this recon produced.


# Six canonical, deterministic phase-progression stages, built entirely
# from the already-real STATUS_PRESENTATION `role` vocabulary (never a new
# domain value) - a presentation-only grouping of Signal.status's 9 real
# roles into the mission's own requested 6-step sequence (Identifierad ->
# Planering -> Finansierad -> Upphandling -> Under byggnation ->
# Färdigställd). "design"/"review" are grouped into "Planering" for this
# progression specifically (a deliberate simplification so the bar stays
# readable in seconds - they still keep their own distinct labels/colors
# everywhere else on the site, e.g. the Overview stage donut). "unknown"
# has no honest place on a phase sequence and is never forced onto one -
# see _project_phase_view()'s own None return for that case.
_PROJECT_PHASE_STAGE_ORDER = ("identified", "planning", "funded", "procurement", "construction", "completed")
_PROJECT_PHASE_STAGE_LABEL = {
    "identified": "Identifierad", "planning": "Planering", "funded": "Finansierad",
    "procurement": "Upphandling", "construction": "Under byggnation", "completed": "Färdigställd",
}
_STATUS_ROLE_TO_PHASE_STAGE = {
    "identified": "identified", "planning": "planning", "design": "planning", "review": "planning",
    "funded": "funded", "procurement": "procurement", "construction": "construction", "completed": "completed",
}


def _project_phase_view(status_role: "str | None") -> "tuple[SimpleNamespace, ...] | None":
    """Deterministic 6-step phase-progression strip for the primary Signal's
    real `status_role`. Returns None when the role has no honest place on
    the sequence (status_role == "unknown", e.g. a NULL/unrecognized raw
    status) - the template shows no progression at all in that case,
    rather than guessing a stage. Each returned stage carries `state`:
    "past" (already behind the current real stage), "current" (the real
    stage), or "future" (not yet reached) - purely for styling; the
    underlying order (_PROJECT_PHASE_STAGE_ORDER) is fixed and identical
    for every Signal."""
    stage_key = _STATUS_ROLE_TO_PHASE_STAGE.get(status_role or "")
    if stage_key is None:
        return None
    current_index = _PROJECT_PHASE_STAGE_ORDER.index(stage_key)
    return tuple(
        SimpleNamespace(
            key=key,
            label=_PROJECT_PHASE_STAGE_LABEL[key],
            state=("current" if i == current_index else "past" if i < current_index else "future"),
        )
        for i, key in enumerate(_PROJECT_PHASE_STAGE_ORDER)
    )


# ("RWI - Juicy Design Mission #3" mission) Airport Detail "location
# intelligence" - reuses Mission #2's own real world-map asset/projection
# verbatim (_WORLD_LAND_PATH, _project_lon_lat) rather than a second map
# implementation. Only ever plots a marker from the Airport's OWN real,
# governed latitude/longitude (scripts/import_faa_csv.py, US-only FAA
# data - see Mission #2's own geo-recon) - never a country-level
# approximation (unlike the Overview map's _COUNTRY_MAP_POSITION, which is
# explicitly a representative-country marker, a different, coarser
# concept). An airport with no persisted coordinates (every non-US
# airport today, e.g. Sacheon) gets no marker and no map at all - an
# honest text-only fallback, never a fake/approximate pin.
def _airport_location_view(airport: Airport) -> "SimpleNamespace | None":
    if airport.latitude is None or airport.longitude is None:
        return None
    x, y = _project_lon_lat(airport.longitude, airport.latitude)
    return SimpleNamespace(
        latitude=round(airport.latitude, 4),
        longitude=round(airport.longitude, 4),
        x=x, y=y,
    )


# ("RWI - Juicy Design Mission #4" mission) "Bevis" - one row per distinct
# real Source cited by this airport's public Signals or Installations
# (deduplicated by the Source's own title+URL+published_date, since neither
# view model carries the raw Source.id and two distinct real Source rows
# sharing all three is not a real risk in this dataset), each optionally
# carrying its governed Claim evidence.
#
# Claims are read via the exact same already-governed, already-reviewed
# service this repository already uses for signal_detail.html's own
# evidence card (_evidence_view -> get_manual_claims_for_source_assertion) -
# never a second claims-reading implementation. A Signal's
# supporting_source_assertions is walked from the ORM object itself
# (`orm_signals_by_id`), not re-queried. A source with no governed claim
# still renders here as a plain, source-backed citation - Section 9's own
# instruction that evidence must "feel like supporting intelligence" even
# for an airport (e.g. BOS) with no transcribed claim yet.
def _airport_evidence_view(
    orm_signals_by_id: dict[int, Signal],
    signal_views: list[SimpleNamespace],
    installation_views: list[SimpleNamespace],
    *, session: Session,
) -> tuple[SimpleNamespace, ...]:
    order: list[tuple] = []
    items: dict[tuple, SimpleNamespace] = {}

    for view in signal_views:
        if not view.source_title:
            continue
        key = (view.source_title, view.source_url, view.source_published_date)
        if key in items:
            continue
        orm_signal = orm_signals_by_id.get(view.id)
        claims = _evidence_view(orm_signal, session=session) if orm_signal is not None else ()
        items[key] = SimpleNamespace(
            source_title=view.source_title,
            source_publisher=view.source_publisher,
            source_type=view.source_type,
            source_type_label=view.source_type_label,
            source_type_anchor=view.source_type_anchor,
            source_type_tooltip=view.source_type_tooltip,
            source_url=view.source_url,
            source_published_date=view.source_published_date,
            claims=claims,
        )
        order.append(key)

    for view in installation_views:
        if not view.source_title:
            continue
        key = (view.source_title, view.source_url, view.source_published_date)
        if key in items:
            continue
        items[key] = SimpleNamespace(
            source_title=view.source_title,
            source_publisher=view.source_publisher,
            source_type=view.source_type,
            source_type_label=view.source_type_label,
            source_type_anchor=view.source_type_anchor,
            source_type_tooltip=view.source_type_tooltip,
            source_url=view.source_url,
            source_published_date=view.source_published_date,
            claims=(),
        )
        order.append(key)

    # Sources with governed claims (richer, higher-value evidence) first;
    # ties broken by most-recently-published, then title, for a stable,
    # deterministic order never dependent on dict/insertion happenstance.
    return tuple(sorted(
        (items[k] for k in order),
        key=lambda it: (
            0 if it.claims else 1,
            -(it.source_published_date.toordinal() if it.source_published_date else 0),
            it.source_title,
        ),
    ))


# ("RWI - Juicy Design Mission #4 - Visual Polish Checkpoint" mission)
# Section-level counts for Underlag's own heading ("6 källor" /
# "1 källa - 4 granskade sakuppgifter") - purely derived, deterministic
# sums over the already-computed `evidence` tuple (source_count is just
# len(evidence); claim_count sums each source's own already-governed
# `claims` tuple). Adds no new evidence-eligibility, ordering, or
# claim-retrieval logic of its own.
def _evidence_summary_view(evidence: "tuple[SimpleNamespace, ...]") -> SimpleNamespace:
    return SimpleNamespace(
        source_count=len(evidence),
        claim_count=sum(len(s.claims) for s in evidence),
    )


def _airport_view(
    airport: Airport, *, today: date, session: Session
) -> "tuple[SimpleNamespace, tuple[SimpleNamespace, ...]]":
    public_orm_signals = [s for s in airport.signals if _is_public_signal(s)]
    orm_signals_by_id = {s.id: s for s in public_orm_signals}
    signal_views = sorted(
        (_signal_view(s, today=today) for s in public_orm_signals),
        key=_signal_sort_key,
    )
    installation_views = [_installation_view(i) for i in airport.installations]
    current_emas = _current_emas_views(airport)
    incident_views = [
        SimpleNamespace(
            id=i.id,
            incident_date=i.incident_date,
            incident_type=i.incident_type,
            emas_engaged=i.emas_engaged,
            updated_at=i.updated_at,
            # ("RWI - Juicy Design Mission #4" mission) Already-real, already-
            # public Incident field, not previously threaded onto this view -
            # feeds the new Intelligenshistorik event's own source link.
            source_url=i.source_url,
        )
        for i in airport.incidents
    ]
    primary_signals = [s for s in signal_views if s.source_type not in {"usaspending_grant", "aip_grant", "iija_grant"}]
    funding_signals = [s for s in signal_views if s not in primary_signals]
    # ("RWI - Juicy Design Mission #4" mission) Replaces the former merged
    # Installation+Incident+Signal Tidslinje - see _intelligence_history_view's
    # own docstring and this module's "public information model" note above
    # for why Installations no longer feed this chronological view.
    intelligence_history_dated, intelligence_history_undated = _intelligence_history_view(
        incident_views, signal_views
    )
    evidence = _airport_evidence_view(orm_signals_by_id, signal_views, installation_views, session=session)
    view = SimpleNamespace(
        id=airport.id,
        # ("RWI - Juicy Design Mission #1" mission) The single headline
        # signal for this airport's new "project summary" hero card - the
        # first (highest-ranked) entry of `primary_signals` (already sorted
        # by _signal_sort_key: lifecycle tier, then score descending, then
        # id - the exact same ordering the "Projekt och bevakning" table
        # below already uses), with funding/grant-only signals already
        # excluded from that list. No new ranking logic - purely a pick of
        # an already-computed list's own top element. None when the airport
        # has no non-funding public signal at all.
        primary_signal=(primary_signals[0] if primary_signals else None),
        # ("RWI - Juicy Design Mission #3" mission) Deterministic 6-step
        # phase-progression strip for the primary Signal's real
        # status_role - None when there is no primary_signal, or its role
        # has no honest place on the sequence. See _project_phase_view()'s
        # own docstring.
        project_phase=(_project_phase_view(primary_signals[0].status_role) if primary_signals else None),
        # Real-coordinate-only location marker (see _airport_location_view()'s
        # own docstring) - None whenever this Airport has no persisted
        # latitude/longitude (every non-US airport today).
        location=_airport_location_view(airport),
        name=airport.name,
        iata_code=airport.iata_code,
        icao_code=airport.icao_code,
        city=airport.city,
        state_region=airport.state_region,
        country=airport.country,
        # ("RWI - Juicy Design Mission #2" mission) Purely decorative -
        # reuses the exact same _COUNTRY_FLAG mapping the Overview market
        # summary already uses; None (no flag rendered) for any country not
        # in that small, non-exhaustive dict, never a wrong/placeholder flag.
        country_flag=_COUNTRY_FLAG.get(airport.country),
        latitude=airport.latitude,
        longitude=airport.longitude,
        website_url=airport.website_url,
        # Governed, currently-ADMITTED alternate/local names
        # (app.services.airport_alias) - see _alias_view()'s own docstring
        # for why this is one plain, undifferentiated list rather than a
        # local-name/alternate-name distinction the underlying model does
        # not represent. Empty tuple (never None) when the airport has no
        # admitted alias, or the alias table has never been migrated.
        local_names=_alias_view(airport, session=session),
        signal_count=len(signal_views),
        # Governed canonical runway inventory (docs/domain/canonical-runway-
        # runway-end-design.md) - complete for all 76 U.S. airports as of
        # docs/domain/allegheny-76-of-76-canonical-apply-report.md. The
        # older `docs/ui/mdw-runway-diagnosis.md` exclusion (the `runways`
        # table was once a non-exhaustive, one-row-per-airport placeholder)
        # no longer applies and is superseded by this. Deliberately says
        # nothing about EMAS presence/current status - that is entirely
        # separate (current_emas below), unchanged by this - see
        # docs/product/public-canonical-runway-inventory-report.md.
        runways=sorted((_runway_view(r) for r in airport.runways), key=lambda r: r.designation),
        signals=primary_signals,
        funding_signals=funding_signals,
        installations=installation_views,
        # Replaces the former separate reviewed_identities/nasr_presence
        # fields (docs/product/public-emas-protected-direction-presentation.md)
        # with one deduplicated, protected-direction-led list - both old
        # fields exposed the raw physical designation under the ambiguous
        # name `runway_end` with no protected-direction context and no
        # deduplication between the two pathways.
        current_emas=current_emas,
        current_status_unverified=(airport.id == 6 and not current_emas),
        incidents=incident_views,
        # ("RWI - Juicy Design Mission #4" mission) See
        # _intelligence_history_view's own docstring - Signal/Incident
        # chronology only, Installation history moved out entirely (now
        # exclusively in the "Historisk EMAS-kontext" disclosure below).
        intelligence_history_dated=intelligence_history_dated,
        intelligence_history_undated=intelligence_history_undated,
    )
    # ("RWI - Juicy Design Mission #4" mission) `evidence` (distinct, real
    # Source citations backing this airport, each with its governed Claim
    # evidence when one exists - _airport_evidence_view's own docstring) is
    # deliberately returned SEPARATELY from the airport view object, never
    # attached to it - the exact same discipline _build()'s own "DATA.JSON"
    # note already documents for signal evidence: airport_views feeds
    # data.json directly, so anything attached to the view itself is
    # published there too. A literal claim excerpt must never be duplicated
    # into that payload (see test_data_json_excludes_literal_claim_evidence_-
    # but_includes_alias, the exact regression this split prevents).
    return view, evidence


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


def build_site(output_dir: Path, *, session: Session | None = None, today: date | None = None) -> None:
    """`today` defaults to the real current date - the same real-clock
    default `datetime.now(UTC)` already uses for `generated_at` just below.
    Callers (chiefly the SLT1 test suite) may pass a fixed date so
    lifecycle-derivation results stay stable across real-calendar time
    instead of drifting as the actual date advances."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        _build(output_dir, session, today=today or date.today())
    finally:
        if owns_session:
            session.close()


def _build(output_dir: Path, session: Session, *, today: date) -> None:
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "airports").mkdir()
    (output_dir / "signals").mkdir()
    shutil.copy2(STATIC_DIR / "style.css", output_dir / "style.css")
    shutil.copy2(STATIC_DIR / "watch.js", output_dir / "watch.js")
    shutil.copytree(STATIC_DIR / "images", output_dir / "images")

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.globals["t"] = text
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    airports = session.scalars(
        select(Airport).options(
            selectinload(Airport.signals).selectinload(Signal.airport),
            selectinload(Airport.signals).selectinload(Signal.runway),
            selectinload(Airport.signals).selectinload(Signal.source),
            # ("RWI - Juicy Design Mission #4" mission) Needed by
            # _airport_evidence_view's per-signal _evidence_view() call -
            # same eager-load discipline the standalone signals query below
            # already applies for signal_detail.html's own evidence card.
            selectinload(Airport.signals).selectinload(Signal.supporting_source_assertions),
            selectinload(Airport.runways).selectinload(Runway.runway_ends),
            selectinload(Airport.installations).selectinload(Installation.source),
            selectinload(Airport.incidents),
            selectinload(Airport.physical_installation_identities).selectinload(PhysicalInstallationIdentity.assertion_links),
            selectinload(Airport.source_assertions).selectinload(SourceAssertion.source),
        ).order_by(Airport.name)
    ).all()
    # ("RWI - Juicy Design Mission #4" mission) `evidence` is threaded
    # per-airport, separately from `airport_views` (which feeds data.json
    # directly) - see _airport_view's own "DATA.JSON" note.
    airport_pairs = [_airport_view(a, today=today, session=session) for a in airports]
    airport_views = [view for view, _evidence in airport_pairs]
    airport_evidence_by_id = {view.id: evidence for view, evidence in airport_pairs}

    all_signals = session.scalars(
        select(Signal).options(
            selectinload(Signal.airport),
            selectinload(Signal.runway),
            selectinload(Signal.source),
            # ("RWI - Sacheon Evidence Surfacing" mission) Bounded by the
            # number of governed (Slice 9C) SourceAssertions linking to a
            # Signal, not by total Signal count - see _evidence_view()'s
            # own docstring. Every pre-existing (legacy) Signal has zero
            # supporting_source_assertions, so this eager-load costs one
            # extra empty-result query batch, never a per-row explosion.
            selectinload(Signal.supporting_source_assertions),
        )
    ).all()
    public_signals = [s for s in all_signals if _is_public_signal(s)]
    # Paired (ORM signal, public view) list, sorted together, so the
    # signal_detail.html render loop below can still reach the ORM
    # object's own supporting_source_assertions for evidence - never by
    # re-querying, never by attaching evidence onto the shared view object
    # itself (see the render loop's own "DATA.JSON" note for why).
    signal_pairs = sorted(
        ((s, _signal_view(s, today=today)) for s in public_signals),
        key=lambda pair: _signal_sort_key(pair[1]),
    )
    signal_views = [view for _source_signal, view in signal_pairs]

    def render(name: str, path: Path, **context) -> None:
        template = env.get_template(name)
        path.write_text(template.render(generated_at=generated_at, **context), encoding="utf-8")

    # ("RWI - Juicy Design Mission #2 - V2.3" mission) Computed once, fed to
    # BOTH Marknadsläge and the new Global intelligens map - one single
    # definition of "activity" for the whole Overview, never two different
    # country-count computations that could silently drift apart.
    market_summary = _market_summary_view(signal_views)

    render(
        "index.html",
        output_dir / "index.html",
        root=".",
        airport_count=len(airport_views),
        installation_count=sum(len(a.installations) for a in airport_views),
        signal_count=len(signal_views),
        high_confidence_count=sum(1 for s in signal_views if s.confidence_level == "high"),
        # ("RWI - Juicy Design Mission #1" mission) Replaces the former,
        # more ambiguous raw signal_count stat on the hero metrics bar - a
        # real, already-computed SLT1 read (signal.lifecycle_state, never
        # re-derived here), answering the shareholder's actual first
        # question ("what's live right now") more directly than a total
        # that mixes active/watch/historical/stale together. signal_count
        # itself is kept (used by the trend caption and available in
        # data.json) - only the hero bar's own stat selection changed.
        active_opportunity_count=sum(1 for s in signal_views if s.lifecycle_state == "active_opportunity"),
        # ("RWI - Juicy Design Mission #2" mission) Real count of Signals
        # currently in an explicit, committed pipeline status - the exact
        # same status vocabulary app.static_export.signal_lifecycle's own
        # _ACTIVE_TRACK_STATUSES already treats as "real, recent evidence of
        # intent" (that module-private constant is not imported here to
        # avoid a cross-module coupling on a private name; the six literal
        # values are the entire real vocabulary and have not changed since
        # SLT1 shipped). Never inferred from lifecycle_state (a different,
        # broader read that also includes e.g. future-dated identified rows).
        in_progress_count=sum(
            1 for s in signal_views
            if s.status in {"design", "procurement", "under construction", "cip", "alp", "funded"}
        ),
        top_signals=signal_views[:5],
        market_summary=market_summary,
        stage_distribution=_stage_distribution_view(signal_views),
        donut_circumference=_DONUT_CIRCUMFERENCE,
        donut_radius=_DONUT_RADIUS,
        global_intelligence=_global_intelligence_view(market_summary),
        map_viewbox_width=_MAP_VIEWBOX_WIDTH,
        map_viewbox_height=_MAP_VIEWBOX_HEIGHT,
        world_land_path=_WORLD_LAND_PATH,
        important_developments=_important_developments_view(signal_views),
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
            # ("RWI - Juicy Design Mission #3" mission) The exact same
            # real, licensed world-map asset the Overview's Global
            # intelligens already uses (Mission #2) - reused verbatim for
            # the airport-level location panel, never a second map asset.
            map_viewbox_width=_MAP_VIEWBOX_WIDTH,
            map_viewbox_height=_MAP_VIEWBOX_HEIGHT,
            world_land_path=_WORLD_LAND_PATH,
            # ("RWI - Juicy Design Mission #4" mission) Template-only, never
            # attached to `airport` itself - see the DATA.JSON note above.
            evidence=airport_evidence_by_id[airport.id],
            evidence_summary=_evidence_summary_view(airport_evidence_by_id[airport.id]),
        )

    render(
        "signals_list.html",
        output_dir / "signals" / "index.html",
        root="..",
        # ("RWI - Mission #7E" mission) One row per published Signal, always
        # - the former (airport_id, category) presentation grouping
        # (_group_signal_views(), removed) had no governed real-world-effort
        # semantics: it never consulted FH-D4 SignalDisposition, and could
        # visually collapse Signals a human reviewer explicitly confirmed
        # DISTINCT (see Mission #7D's own recon - 7 of the 13 real groups it
        # produced exactly matched a real DISTINCT disposition). Each row
        # now keeps its own title/lifecycle/status/category/confidence/
        # year/source/Score - never inherited from another Signal.
        signals=signal_views,
        statuses=[
            SimpleNamespace(value=status, label=status_view(status)[0])
            for status in sorted({s.status for s in signal_views if s.status})
        ],
        countries=sorted({s.country for s in signal_views if s.country}),
        # SLT1: real, computed counts for the four lifecycle states (design
        # doc S11/S18's own "current opportunity count is correct"
        # requirement) - never invented, never hardcoded.
        lifecycle_counts=_lifecycle_counts_view(signal_views),
    )

    # ("RWI - Mission #7C" mission) "Marknadsläge" - see
    # _market_intelligence_view()'s own docstring. Built from the same
    # already-published, already-sorted signal_views every other page
    # already uses - no second Signal query, no FH-D4 read.
    render(
        "market.html",
        output_dir / "marknadslage.html",
        root=".",
        market=_market_intelligence_view(signal_views),
    )
    # ("RWI - Sacheon Evidence Surfacing" mission) DATA.JSON: `evidence` is
    # deliberately passed as a SEPARATE template context variable here,
    # never attached to the shared `signal` view object itself - the exact
    # same discipline this module's own _signal_view() already documents
    # for `notes`/`manual_year_estimate` ("anything added to it is
    # published"). `signal_views/data.json` below are therefore completely
    # unaffected by this slice: no literal claim excerpt, however small,
    # is duplicated into the global search/data payload.
    for source_signal, signal in signal_pairs:
        render(
            "signal_detail.html",
            output_dir / "signals" / f"{signal.id}.html",
            root="..",
            signal=signal,
            evidence=_evidence_view(source_signal, session=session),
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
