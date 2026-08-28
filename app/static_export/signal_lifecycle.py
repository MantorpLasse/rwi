"""SLT1 — presentation-only Signal opportunity-lifecycle derivation
(docs/architecture/rwi-signal-temporal-relevance-opportunity-lifecycle-design.md,
Slice SLT1 of that design's own S16 roadmap).

Answers exactly one question, for one Signal, at export time: "is this
currently a live/future economic opportunity, still-developing, already
realized, or old enough to need research without any confirmation either
way?" It never answers "how old is this document?" - age is used as an
input for exactly the two evidence shapes the design doc's own real-data
analysis (S3, S6) found it to be the *only* remaining differentiator for
(incident-derived signals with otherwise uniform evidence, and federal-grant
fiscal years), never as a blanket rule.

READ-ONLY / PRESENTATION-ONLY: this module never queries a database, never
opens a session, never mutates a Signal, and is called only from
app.static_export.build - never from any governed/persistence pipeline
(ERG, KAR, UAC, EB4/EB5, governed Signal creation). `derive_signal_lifecycle()`
takes an already-loaded ORM Signal (its `.source` relationship must already
be loaded by the caller, matching every other _view() helper in build.py)
and a `today` date, and returns a plain, ORM-free result - the same
"deterministic, ORM-free summary" convention this pipeline already uses
(IdentityGuardReevaluationResult, MatchExistingAirportResult, ...).

NEVER MUTATES: `probability_score`, `confidence`, `status`, or `category`
are read, never written, and never reinterpreted as lifecycle inputs beyond
what the design doc's own dimension-separation section (S5) established -
lifecycle is an *additional*, independent read, not a replacement for any
of them.

NOT A BARE AGE THRESHOLD: most of the vocabulary below is evidence-shape
based (installation link, explicit pipeline status, explicit year fields,
federal grant fiscal year) - `today` is consulted only inside the two
narrow, explicitly-labeled branches (incident-derived, grant-derived) where
the design document's own real-data analysis found age to be the only
remaining differentiator, and even there only relative to *evidence dates*
(an incident date, a grant fiscal year), never a Signal's row-creation date.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models import Signal

__all__ = [
    "SignalLifecycleState",
    "SignalLifecycleAssessment",
    "derive_signal_lifecycle",
    "INCIDENT_RESEARCH_WINDOW_YEARS",
    "GRANT_DEVELOPING_WINDOW_YEARS",
    "ACTIVE_STATUS_GRACE_YEARS",
]


class SignalLifecycleState(str, Enum):
    """The design document's own S11 vocabulary, explicitly not locked
    there - kept exactly as proposed since the real 68-row inventory (S4)
    needed no additional member. OTHER is retained defensively even though
    0/68 real rows required it (design doc S4's own empirical finding)."""

    ACTIVE_OPPORTUNITY = "active_opportunity"
    DEVELOPING_WATCH = "developing_watch"
    REALIZED_HISTORICAL = "realized_historical"
    STALE_UNRESOLVED = "stale_unresolved"
    OTHER = "other"


@dataclass(frozen=True)
class SignalLifecycleAssessment:
    """Result-only, explainability metadata - never persisted anywhere (this
    slice adds no schema; see the design doc's own S8 recommendation to
    ship the machine-derived read before any persistence layer)."""

    state: SignalLifecycleState
    reason: str


# Design doc S3a: the illustrative ~5-year incident research-window boundary,
# reproduced here as the one narrow, explicitly-labeled place age is used as
# a primary input - chosen because the design document's own real-data
# analysis found ALL 26 incident-derived signals otherwise evidentially
# uniform (same confidence, same score, same status, same missing
# installation/replacement-year fields). Provisional and product-owned, not
# empirically validated against any real replacement-latency data (design
# doc S17 - RWI has none yet); a future product decision may replace this
# constant, but it must never be replaced by "current_year - year > N"
# applied to every category indiscriminately.
INCIDENT_RESEARCH_WINDOW_YEARS = 5.0

# Design doc S4/S3a: a federal grant (USAspending/AIP/IIJA) obligated 1-2
# fiscal years ago has execution status genuinely uncertain (still very
# plausibly in progress); 3+ years ago, execution has very likely already
# concluded even though RWI has no captured completion evidence either way.
GRANT_DEVELOPING_WINDOW_YEARS = 2

# Design doc S4 (id 68/MDW): a Signal in an explicit, committed pipeline
# status (design/procurement/CIP/ALP/funded/under construction) does not
# stop being active the instant its own stated year passes by a small
# margin - the status itself is real, recent evidence of intent. Beyond this
# grace window with no newer evidence, the stated status is itself stale.
ACTIVE_STATUS_GRACE_YEARS = 2

_ACTIVE_TRACK_STATUSES = frozenset({
    "design", "procurement", "under construction", "cip", "alp", "funded",
})
_WATCH_TRACK_STATUSES = frozenset({"environmental_review", "master_plan"})
_GRANT_SOURCE_TYPES = frozenset({"usaspending_grant", "aip_grant", "iija_grant"})

# Matches the exact, single place this ISO date is ever embedded in a real
# title - app.models.incident._replacement_signal_title(), always
# "... (YYYY-MM-DD)" at the very end. Deliberately the only mechanism this
# module uses to recover an incident date: Signal carries no FK back to the
# Incident that produced it (design doc S1's own documented schema gap;
# source_id is shared, non-unique, across every real incident-derived row).
# A title a human later hand-edits away from this shape is expected to fail
# to match - see the conservative fallback below, never an exception.
_INCIDENT_DATE_IN_TITLE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)\s*$")


def _parse_incident_date_from_title(title: Optional[str]) -> Optional[date]:
    if not title:
        return None
    match = _INCIDENT_DATE_IN_TITLE.search(title)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _best_year(signal: "Signal") -> Optional[int]:
    """The same target_year -> planning_year -> procurement_year ->
    construction_start.year -> completion_date.year precedence
    app.services.governed_signal_creation._resolve_reference_year() and
    app.services.existing_signal_reconciliation_candidates already each
    independently apply. Reimplemented here rather than imported, matching
    those two modules' own explicit, already-reviewed precedent for this
    exact five-line field-precedence convention (governed_signal_creation.py's
    own docstring: "this is a five-line field-precedence convention, not
    reconciliation decision logic") - not a new duplication pattern, the
    third instance of an already-accepted one. manual_year_estimate is
    excluded for the identical reason both of those modules exclude it: a
    private, unverified personal guess must never drive a public lifecycle
    read."""
    for value in (signal.target_year, signal.planning_year, signal.procurement_year):
        if value is not None:
            return value
    if signal.construction_start is not None:
        return signal.construction_start.year
    if signal.completion_date is not None:
        return signal.completion_date.year
    return None


def derive_signal_lifecycle(signal: "Signal", *, today: date) -> SignalLifecycleAssessment:
    """Deterministic, side-effect-free. Evaluated in this fixed priority
    order (design doc S9's own state table, same ordering: strongest,
    least ambiguous evidence first):

    1. REALIZED_HISTORICAL - installation_id set, or status == "completed"
       (design doc S9: "only the unambiguous completed/installation-linked
       case should ever be machine-derived"; both are, by construction,
       co-occurring for every real governed graduation -
       scripts/graduate_signal_to_installation.py always sets both in the
       same operation, and governed_signal_creation.py's own
       _DISALLOWED_INITIAL_STATUSES already refuses "completed" as an
       initial human-approved status for exactly this reason).
    2. Incident-derived (category == "replacement_after_incident") - the one
       evidence-uniform category (design doc S3); age-gated against
       INCIDENT_RESEARCH_WINDOW_YEARS, or STALE_UNRESOLVED with an explicit
       "could not determine" reason if the title cannot be parsed - never a
       silent guess.
    3. Federal-grant-derived (Source.source_type in usaspending_grant/
       aip_grant/iija_grant) - fiscal-year-gated against
       GRANT_DEVELOPING_WINDOW_YEARS, using the same planning_year field the
       design doc's own real-data reading already established holds the
       grant fiscal year for these rows.
    4. Explicitly watch-track evidence (category == "replacement_watch",
       confidence == "speculative", or an early/uncertain status) ->
       DEVELOPING_WATCH outright, regardless of any year present (design doc
       S4: HYA's own environmental_review row has a future year, 2027, and
       is still DEVELOPING_WATCH - status maturity, not year, decides here).
    5. Committed pipeline status (design/procurement/CIP/ALP/funded/under
       construction) or an in-progress/future construction window ->
       ACTIVE_OPPORTUNITY, unless its own best available year is stale
       beyond ACTIVE_STATUS_GRACE_YEARS with nothing newer -> DEVELOPING_WATCH.
    6. Fallback for everything else (no recognized status, no grant/incident
       shape) - an explicit future/current year alone is enough for
       ACTIVE_OPPORTUNITY; a clearly stale year alone is STALE_UNRESOLVED;
       otherwise DEVELOPING_WATCH. This deliberately never inspects title or
       notes text - no per-row/per-airport narrative reading, matching this
       mission's explicit prohibition on hardcoded, text-sniffed special
       cases. A Signal whose only real evidence is free-text prose (e.g. a
       vendor-confirmation or investment-announcement headline with no
       structured status/year/installation link) is classified
       DEVELOPING_WATCH here, not ACTIVE_OPPORTUNITY - "fail conservatively
       when evidence is insufficient" (mission hard boundary 15), and see
       this module's own test suite / the SLT1 mission report for the exact,
       intentional 3-row divergence this produces from the design document's
       own human-analyst judgment calls on ids 64/66/67.
    """
    if signal.installation_id is not None or (signal.status or "").strip().lower() == "completed":
        return SignalLifecycleAssessment(
            SignalLifecycleState.REALIZED_HISTORICAL,
            "graduated to a real Installation (installation_id set) or explicitly marked completed - "
            "the only unambiguous, machine-derivable realized case.",
        )

    category = (signal.category or "").strip().lower()
    status = (signal.status or "").strip().lower()
    confidence = (signal.confidence or "").strip().lower()
    source_type = ((signal.source.source_type if signal.source else None) or "").strip().lower()

    if category == "replacement_after_incident":
        incident_date = _parse_incident_date_from_title(signal.title)
        if incident_date is None:
            return SignalLifecycleAssessment(
                SignalLifecycleState.STALE_UNRESOLVED,
                "incident-derived, but the originating incident date could not be determined from the "
                "title - conservative default, never guessed.",
            )
        age_years = (today - incident_date).days / 365.25
        if age_years >= INCIDENT_RESEARCH_WINDOW_YEARS:
            return SignalLifecycleAssessment(
                SignalLifecycleState.STALE_UNRESOLVED,
                f"incident-derived, {age_years:.1f}y old ({incident_date.isoformat()}), no replacement "
                "confirmation exists (no installation link, no funding/procurement evidence) - past the "
                f"{INCIDENT_RESEARCH_WINDOW_YEARS:.0f}y research window with nothing more recent captured.",
            )
        return SignalLifecycleAssessment(
            SignalLifecycleState.DEVELOPING_WATCH,
            f"incident-derived, {age_years:.1f}y old ({incident_date.isoformat()}), no replacement "
            f"confirmation yet - still within the {INCIDENT_RESEARCH_WINDOW_YEARS:.0f}y research window, "
            "too soon to call unresolved.",
        )

    if source_type in _GRANT_SOURCE_TYPES:
        fiscal_year = signal.planning_year
        if fiscal_year is None:
            return SignalLifecycleAssessment(
                SignalLifecycleState.STALE_UNRESOLVED,
                "federal-grant-derived, but no fiscal year was recorded - conservative default.",
            )
        delta = today.year - fiscal_year
        if delta <= 0:
            return SignalLifecycleAssessment(
                SignalLifecycleState.ACTIVE_OPPORTUNITY,
                f"federal grant obligated FY{fiscal_year}, current or future fiscal year.",
            )
        if delta <= GRANT_DEVELOPING_WINDOW_YEARS:
            return SignalLifecycleAssessment(
                SignalLifecycleState.DEVELOPING_WATCH,
                f"federal grant obligated FY{fiscal_year}, {delta}y ago - execution status not yet confirmed.",
            )
        return SignalLifecycleAssessment(
            SignalLifecycleState.STALE_UNRESOLVED,
            f"federal grant obligated FY{fiscal_year}, {delta}y ago, no completion evidence captured.",
        )

    if category == "replacement_watch" or confidence == "speculative":
        return SignalLifecycleAssessment(
            SignalLifecycleState.DEVELOPING_WATCH,
            "explicitly categorized/confidenced as a watch item, not yet a committed project.",
        )

    if status in _WATCH_TRACK_STATUSES:
        return SignalLifecycleAssessment(
            SignalLifecycleState.DEVELOPING_WATCH,
            f"status={status!r} is an early/uncertain pipeline stage, regardless of any stated year.",
        )

    best_year = _best_year(signal)
    in_progress = (
        signal.construction_start is not None
        and signal.completion_date is not None
        and signal.construction_start <= today <= signal.completion_date
    )
    future_construction = (
        (signal.construction_start is not None and signal.construction_start >= today)
        or (signal.completion_date is not None and signal.completion_date >= today)
    )

    if status in _ACTIVE_TRACK_STATUSES or in_progress or future_construction:
        if in_progress or future_construction:
            return SignalLifecycleAssessment(
                SignalLifecycleState.ACTIVE_OPPORTUNITY,
                "construction window overlaps today or is in the future.",
            )
        if best_year is not None and (today.year - best_year) > ACTIVE_STATUS_GRACE_YEARS:
            return SignalLifecycleAssessment(
                SignalLifecycleState.DEVELOPING_WATCH,
                f"status={status!r} is a committed pipeline stage, but its own year ({best_year}) is "
                f"{today.year - best_year}y stale with nothing newer captured.",
            )
        return SignalLifecycleAssessment(
            SignalLifecycleState.ACTIVE_OPPORTUNITY,
            f"status={status!r} is a committed pipeline stage"
            + (f", year={best_year}." if best_year is not None else ", no stale year evidence found."),
        )

    if best_year is not None and best_year >= today.year:
        return SignalLifecycleAssessment(
            SignalLifecycleState.ACTIVE_OPPORTUNITY,
            f"explicit future/current year ({best_year}) with no other disqualifying evidence.",
        )
    if best_year is not None and (today.year - best_year) > ACTIVE_STATUS_GRACE_YEARS:
        return SignalLifecycleAssessment(
            SignalLifecycleState.STALE_UNRESOLVED,
            f"only year evidence found is {today.year - best_year}y old ({best_year}), no status or "
            "completion evidence beyond it.",
        )

    return SignalLifecycleAssessment(
        SignalLifecycleState.DEVELOPING_WATCH,
        "insufficient structured evidence (no recognized status, no year, no installation link) to "
        "classify as active or historical with confidence - conservative default, never fabricated.",
    )
