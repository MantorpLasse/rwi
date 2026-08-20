from __future__ import annotations

"""Fleet Health Check — FHC3 warning/review/informational rule core.

Implements the subset of the reviewed design's remaining 21 non-FHC1 rules
(see docs/architecture/fleet-health-check-design-and-real-db-reconnaissance.md
§6's 32-row catalogue) that are genuinely detectable from current, persisted
database structure without a keyword/text heuristic, an external-data
dependency, or a fabricated detector:

DETERMINISTIC_WARNING: FH-A3, FH-C3, FH-E1, FH-E2, FH-G1
REVIEW_REQUIRED:       FH-C4, FH-D3, FH-D4, FH-E4, FH-F3
INFORMATIONAL:         FH-A1, FH-F1, FH-F2

Deliberately NOT implemented as automated detectors in this module (see the
FHC3 report's "deferred rules" section for the full reasoning on each):

- FH-A4 (REVIEW_REQUIRED): the reviewed design's own detection method is a
  name-token-absence heuristic on `Airport.name` - exactly the class of
  keyword-in-name matching this project's own false-positive analysis (4 of
  7 real triage hits were legitimate) already proved unsafe as automated
  production logic. FH-A4 remains a reconnaissance/manual-review catalogue
  entry, never a fabricated detector, per this slice's own explicit
  instruction to honor that boundary exactly.
- FH-B3/FH-B4 (DETERMINISTIC_WARNING): require parsing/normalizing
  `Runway`/`RunwayEnd.designation` text via `app.services.runway_identity`
  - deferred to a future slice; not attempted here.
- FH-B5 (INFORMATIONAL): requires cross-referencing external NASR import
  planning state (`app.services.runway_inventory`'s own batch
  classification), not current database state alone - architecturally out
  of reach for a DB-only adapter.
- FH-H1/FH-I1/FH-I2 (NOT_CURRENTLY_DETECTABLE): represented in the FHC3
  report's catalogue only, never as a fabricated evaluator. There is no
  function here named after any of them, and no HealthFinding this module
  can ever emit claims "not detectable" for any entity.
- FH-H2 (DETERMINISTIC_ERROR): outside this module's classification scope
  entirely (requires the static export, not persisted state) - belongs to a
  future FHC4 presentation-cross-check slice per the reviewed design's own
  roadmap (§19).

CENTRAL DISCIPLINE (mirrors FHC1 exactly): DETECTION != JUDGMENT !=
REMEDIATION. A `DETERMINISTIC_WARNING` states an observed structural
pattern, never an unsupported conclusion. A `REVIEW_REQUIRED` finding is
explicitly a candidate for a human, never a confirmed defect - none of this
module's summaries ever use the words "duplicate," "corrupt," "wrong," or
"invalid" unless the underlying rule actually proves that statement (none
of them do). An `INFORMATIONAL` finding describes coverage/state, never
alarm. No rule here ever concludes an existing-Signal reconciliation
decision (`POSSIBLE_EXISTING_SIGNAL_MATCH`/`CLEAR_TO_CREATE`/
`ALREADY_LINKED` remain exclusively R1's authority) or a governed human
decision (`CONFIRM_DISTINCT_SIGNAL`/`MARK_DUPLICATE` remain exclusively
R4's authority) - this module only ever surfaces candidates for a human to
review through those existing, unmodified pathways.

PURITY (same discipline as fleet_health_rules.py, deliberately not
extended - see that module for why it stays a separate, frozen artifact):
no SQLAlchemy, no ORM, no Session, no filesystem, no network, no clock, no
random/UUID identity, no provider-specific logic, no scoring/ranking/
threshold/weight/probability computation anywhere. Every rule operates on
small, frozen, rule-specific fact dataclasses the caller (FHC2's adapter)
builds from a single already-joined read. `HealthFinding`/
`HealthClassification` are reused, unmodified, from
`app.services.fleet_health_rules` - not redefined here.

DEDUP: every evaluator here inherits the exact `_dedupe_preserve_order()`-
style defense FHC1's own review checkpoint established - a duplicated input
row for one entity can never produce two findings or make an entity look
like it "collides with itself" in a grouping rule. This is a second,
independent line of defense; FHC2's adapter queries must remain dedup-safe
by construction on their own (see the FHC3 report's cardinality section).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from app.services.fleet_health_rules import (
    AirportCodeFact,
    HealthClassification,
    HealthFinding,
    SignalRunwayAirportFact,
)

__all__ = [
    "AirportRunwayCountFact",
    "InstallationAirportLinkageFact",
    "InstallationAssertionLinkRetractionFact",
    "SignalLifecycleFact",
    "SignalProvenanceFact",
    "SourceAssertionGovernanceDecisionFact",
    "SourceAssertionReviewStateFact",
    "FleetReviewSnapshot",
    "REVIEW_RULE_IDS",
    "evaluate_review_findings",
]


# ---------------------------------------------------------------------------
# Pure input fact dataclasses (new to this module). AirportCodeFact and
# SignalRunwayAirportFact are reused, unmodified, from fleet_health_rules -
# FH-A3 and FH-D3/FH-D4 need exactly the same structured facts FH-A2/FH-D1
# already read; building a second, duplicate fact type for the same
# persisted columns would be a needless second domain model, not a safer
# one.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AirportRunwayCountFact:
    """FH-A1 input: how many Runway rows an Airport actually has."""

    airport_id: int
    runway_count: int


@dataclass(frozen=True)
class InstallationAirportLinkageFact:
    """FH-C3 input: one Installation's own airport/runway/runway-end linkage.

    runway_end is Installation's own free-text column (distinct from the
    canonical RunwayEnd relationship) - read verbatim, never interpreted.
    """

    installation_id: int
    airport_id: int
    runway_id: Optional[int]
    runway_end: Optional[str]


@dataclass(frozen=True)
class InstallationAssertionLinkRetractionFact:
    """FH-C4 input: one SourceAssertion's InstallationAssertionLink history,
    already reduced by the adapter to exactly the two facts the rule needs -
    never the raw ordered history itself (which would smuggle multi-row
    review context into what must stay a narrow structured comparison).

    Built only for assertion_ids with two or more link rows (a single link
    can have no "earlier" history to retract); `had_earlier_same_physical_
    installation` is True only if some link OTHER than the current latest
    one has outcome == "SAME_PHYSICAL_INSTALLATION".
    """

    assertion_id: int
    latest_outcome: str
    had_earlier_same_physical_installation: bool


@dataclass(frozen=True)
class SignalLifecycleFact:
    """FH-E1/FH-E2/FH-E4 input: one Signal's own lifecycle year/status/
    completion fields - deliberately excludes construction_start/
    completion_date's own FH-E3 comparison (that stays FHC1's alone) and
    every free-text/vendor/financial field.
    """

    signal_id: int
    airport_id: int
    status: Optional[str]
    planning_year: Optional[int]
    procurement_year: Optional[int]
    target_year: Optional[int]
    completion_date: Optional[date]


@dataclass(frozen=True)
class SignalProvenanceFact:
    """FH-F1 input: one Signal's own source_id plus whether ANY governed
    SourceAssertion currently links to it (`SourceAssertion.signal_id ==
    this Signal's id`, computed by the adapter - never re-derived here).
    """

    signal_id: int
    source_id: Optional[int]
    has_governed_supporting_assertion: bool


@dataclass(frozen=True)
class SourceAssertionReviewStateFact:
    """FH-F2/FH-F3 input: one SourceAssertion with airport_id NULL, plus its
    own review_state. Built only for airport_id-NULL rows - there is
    nothing to report for the vast majority of rows that do have an
    attributed airport (mirrors FHC1's own "only actual linked
    relationships become facts" discipline for FH-D2).
    """

    assertion_id: int
    review_state: str


@dataclass(frozen=True)
class SourceAssertionGovernanceDecisionFact:
    """FH-G1 input: one SourceAssertion's three governed-pipeline decision
    columns. Built only for promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"
    rows - FH-G1 can never fire otherwise, mirroring the bounding discipline
    already established for FH-G2/FH-G3's own governance facts in FHC1.
    """

    assertion_id: int
    identity_guard_decision: Optional[str]
    intelligence_review_decision: Optional[str]
    promotion_policy_decision: Optional[str]


def _dedupe_preserve_order(facts: tuple[Any, ...], key: Callable[[Any], Any]) -> tuple[Any, ...]:
    """Identical discipline to fleet_health_rules._dedupe_preserve_order -
    kept as a private copy rather than imported, so this module never
    depends on FHC1's own private (underscore-prefixed) internals; both
    copies are trivial, reviewed, and independently tested."""
    seen: dict[Any, Any] = {}
    for fact in facts:
        fact_key = key(fact)
        if fact_key not in seen:
            seen[fact_key] = fact
    return tuple(seen.values())


# Mirrored literal vocabulary values (not imported, for the same purity
# reason FHC1 mirrors REVIEWER_ACTIONS rather than importing
# app.models.reviewer_action): the three governed-decision expected values
# reviewer_action_persistence.py itself gates APPROVE_SIGNAL/
# CONFIRM_DISTINCT_SIGNAL on, and human_review_queue.py's own
# _QUEUE_DECISION.
_EXPECTED_IDENTITY_GUARD_DECISION = "ATTACH_CONFIRMED"
_EXPECTED_INTELLIGENCE_REVIEW_DECISION = "REVIEW_REQUIRED"
_HUMAN_REVIEW_REQUIRED_PROMOTION_DECISION = "HUMAN_REVIEW_REQUIRED"
_SAME_PHYSICAL_INSTALLATION = "SAME_PHYSICAL_INSTALLATION"
_UNREVIEWED = "unreviewed"
_REVIEWED = "reviewed"
_COMPLETED_STATUS = "completed"


# ---------------------------------------------------------------------------
# Rule evaluators.
# ---------------------------------------------------------------------------


def evaluate_fh_a1(facts: tuple[AirportRunwayCountFact, ...]) -> tuple[HealthFinding, ...]:
    """Airport with zero Runway rows - a coverage/completeness observation,
    never a structural error. Wording deliberately avoids "missing" or
    "broken": absence of canonical runway inventory is a known, legitimate
    state for reference-only airport rows.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.airport_id):
        if fact.runway_count != 0:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-A1",
                classification=HealthClassification.INFORMATIONAL,
                entity_type="Airport",
                entity_ids=(fact.airport_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Airport {fact.airport_id} has no persisted Runway rows "
                    "(no canonical runway inventory recorded for this airport)"
                ),
                structured_evidence={"airport_id": fact.airport_id, "runway_count": 0},
            )
        )
    return tuple(findings)


def evaluate_fh_a3(facts: tuple[AirportCodeFact, ...]) -> tuple[HealthFinding, ...]:
    """Airport with no IATA/ICAO/FAA code at all - excludes this airport from
    canonical NASR matching; a structural coverage observation, not an
    error. Empty string is treated the same as NULL here (both mean "no
    code present"), matching FH-A2's own exact "no code" definition.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.airport_id):
        has_any_code = any(
            code is not None and code != ""
            for code in (fact.iata_code, fact.icao_code, fact.faa_code)
        )
        if has_any_code:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-A3",
                classification=HealthClassification.DETERMINISTIC_WARNING,
                entity_type="Airport",
                entity_ids=(fact.airport_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Airport {fact.airport_id} has no IATA, ICAO, or FAA code "
                    "recorded (permanently excluded from canonical code-based matching)"
                ),
                structured_evidence={"airport_id": fact.airport_id},
            )
        )
    return tuple(findings)


def evaluate_fh_c3(facts: tuple[InstallationAirportLinkageFact, ...]) -> tuple[HealthFinding, ...]:
    """Two or more Installation rows at one Airport, neither linked to a
    canonical Runway nor carrying a free-text runway_end value. This is a
    structural pattern observation only: it states that the database cannot
    currently distinguish "two separate physical beds" from "one bed
    described by two rows" for this airport - it never asserts duplication.
    """
    findings: list[HealthFinding] = []
    deduped = _dedupe_preserve_order(facts, key=lambda f: f.installation_id)
    groups: dict[int, list[int]] = {}
    for fact in deduped:
        if fact.runway_id is not None or fact.runway_end is not None:
            continue
        groups.setdefault(fact.airport_id, []).append(fact.installation_id)
    for airport_id, installation_ids in groups.items():
        if len(installation_ids) < 2:
            continue
        ids = tuple(sorted(installation_ids))
        findings.append(
            HealthFinding(
                rule_id="FH-C3",
                classification=HealthClassification.DETERMINISTIC_WARNING,
                entity_type="Installation",
                entity_ids=ids,
                airport_id=airport_id,
                summary=(
                    f"Airport {airport_id} has {len(ids)} Installation rows with "
                    "neither a canonical runway link nor a free-text runway_end "
                    f"value: installations {ids}"
                ),
                structured_evidence={
                    "airport_id": airport_id,
                    "installation_ids": ids,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_c4(
    facts: tuple[InstallationAssertionLinkRetractionFact, ...],
) -> tuple[HealthFinding, ...]:
    """A SourceAssertion whose physical-installation-identity history
    includes an earlier SAME_PHYSICAL_INSTALLATION decision that the latest
    recorded link no longer confirms - the current state has changed since
    an earlier reviewer's decision and is worth a human glance to confirm
    the retraction was intentional, never itself proof of an error.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if not fact.had_earlier_same_physical_installation:
            continue
        if fact.latest_outcome == _SAME_PHYSICAL_INSTALLATION:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-C4",
                classification=HealthClassification.REVIEW_REQUIRED,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=None,
                summary=(
                    f"SourceAssertion {fact.assertion_id}'s physical-installation "
                    "identity history includes an earlier SAME_PHYSICAL_INSTALLATION "
                    f"decision, but the latest recorded outcome is now "
                    f"{fact.latest_outcome!r} - candidate for human review"
                ),
                structured_evidence={
                    "assertion_id": fact.assertion_id,
                    "latest_outcome": fact.latest_outcome,
                    "had_earlier_same_physical_installation": True,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_d3(facts: tuple[SignalRunwayAirportFact, ...]) -> tuple[HealthFinding, ...]:
    """Two or more Signals sharing the same airport AND the same canonical
    runway. A shared runway_id is one of R1's own genuine structural ANCHOR
    fields (existing_signal_reconciliation.py) - stronger evidence than
    mere co-location, so this is always REVIEW_REQUIRED, never a lower
    tier. Never asserts duplication; only surfaces the co-location for a
    human to resolve via the existing CONFIRM_DISTINCT_SIGNAL/MARK_DUPLICATE
    workflow.
    """
    findings: list[HealthFinding] = []
    deduped = _dedupe_preserve_order(facts, key=lambda f: f.signal_id)
    groups: dict[tuple[int, int], list[int]] = {}
    for fact in deduped:
        if fact.runway_id is None:
            continue
        groups.setdefault((fact.signal_airport_id, fact.runway_id), []).append(fact.signal_id)
    for (airport_id, runway_id), signal_ids in groups.items():
        if len(signal_ids) < 2:
            continue
        ids = tuple(sorted(signal_ids))
        findings.append(
            HealthFinding(
                rule_id="FH-D3",
                classification=HealthClassification.REVIEW_REQUIRED,
                entity_type="Signal",
                entity_ids=ids,
                airport_id=airport_id,
                summary=(
                    f"{len(ids)} Signals share airport {airport_id} and runway "
                    f"{runway_id} - co-located Signals, candidate for human "
                    f"reconciliation review: signals {ids}"
                ),
                structured_evidence={
                    "airport_id": airport_id,
                    "runway_id": runway_id,
                    "signal_ids": ids,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_d4(facts: tuple[SignalRunwayAirportFact, ...]) -> tuple[HealthFinding, ...]:
    """Two or more Signals sharing the same airport with no runway claimed
    by either. Deliberately weak evidence - airport-level co-location
    alone, no title/vendor/amount comparison - so this is a candidate
    surfacing mechanism only, never duplicate proof (real reconnaissance:
    12 co-location groups found, 10 of 12 were legitimate distinct events
    once their own structured fields were inspected).
    """
    findings: list[HealthFinding] = []
    deduped = _dedupe_preserve_order(facts, key=lambda f: f.signal_id)
    groups: dict[int, list[int]] = {}
    for fact in deduped:
        if fact.runway_id is not None:
            continue
        groups.setdefault(fact.signal_airport_id, []).append(fact.signal_id)
    for airport_id, signal_ids in groups.items():
        if len(signal_ids) < 2:
            continue
        ids = tuple(sorted(signal_ids))
        findings.append(
            HealthFinding(
                rule_id="FH-D4",
                classification=HealthClassification.REVIEW_REQUIRED,
                entity_type="Signal",
                entity_ids=ids,
                airport_id=airport_id,
                summary=(
                    f"{len(ids)} Signals are co-located at airport {airport_id} "
                    "with no runway claimed by any of them - possible "
                    f"existing-signal relationship, candidate for human review: "
                    f"signals {ids}"
                ),
                structured_evidence={
                    "airport_id": airport_id,
                    "signal_ids": ids,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_e1(facts: tuple[SignalLifecycleFact, ...]) -> tuple[HealthFinding, ...]:
    """Signal.planning_year later than Signal.procurement_year. Detection is
    mechanical; significance is not - these two fields have no documented
    ordering contract and real data (Signal #3-shaped: a multi-phase
    project whose fields are updated asynchronously per phase) shows this
    can be a legitimate, non-contradictory state. Warning, never error.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.signal_id):
        if fact.planning_year is None or fact.procurement_year is None:
            continue
        if fact.planning_year <= fact.procurement_year:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-E1",
                classification=HealthClassification.DETERMINISTIC_WARNING,
                entity_type="Signal",
                entity_ids=(fact.signal_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Signal {fact.signal_id} has planning_year="
                    f"{fact.planning_year} after procurement_year="
                    f"{fact.procurement_year} - structurally suspicious, "
                    "may reflect a legitimate multi-phase update"
                ),
                structured_evidence={
                    "signal_id": fact.signal_id,
                    "planning_year": fact.planning_year,
                    "procurement_year": fact.procurement_year,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_e2(facts: tuple[SignalLifecycleFact, ...]) -> tuple[HealthFinding, ...]:
    """Signal.procurement_year later than Signal.target_year - same
    field-contract gap and same multi-phase risk as FH-E1."""
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.signal_id):
        if fact.procurement_year is None or fact.target_year is None:
            continue
        if fact.procurement_year <= fact.target_year:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-E2",
                classification=HealthClassification.DETERMINISTIC_WARNING,
                entity_type="Signal",
                entity_ids=(fact.signal_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Signal {fact.signal_id} has procurement_year="
                    f"{fact.procurement_year} after target_year="
                    f"{fact.target_year} - structurally suspicious, "
                    "may reflect a legitimate multi-phase update"
                ),
                structured_evidence={
                    "signal_id": fact.signal_id,
                    "procurement_year": fact.procurement_year,
                    "target_year": fact.target_year,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_e4(facts: tuple[SignalLifecycleFact, ...]) -> tuple[HealthFinding, ...]:
    """Signal.status == "completed" (exact literal match; status is
    unconstrained free text, never normalized) with completion_date NULL -
    a claim without the field that would directly substantiate it. Worth a
    human glance, never a hard error, since completion_date is a
    documented-nullable legacy field.

    Reviewed-design note (review-checkpoint clarification): the design's
    own prose for this rule adds "...and no other year field consistent
    with 'done'" - deliberately NOT implemented here. Judging whether a
    year field (e.g. target_year) is "consistent with done" is only
    meaningful relative to the current date (a past target_year suggests
    "done," a future one doesn't) - and this project's own architectural
    discipline, established firmly by FH-E3's "no current-date inference"
    contract and restated throughout FHC2/FHC3, forbids exactly that kind
    of wall-clock dependency in a pure rule. The simpler predicate below
    (status/completion_date only) is the safer, principle-consistent
    reading of an otherwise-ambiguous design phrase, not an oversight -
    verified against the one real hit (Signal #65) this rule needs to
    catch, and produces no additional real false positives.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.signal_id):
        if fact.status != _COMPLETED_STATUS:
            continue
        if fact.completion_date is not None:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-E4",
                classification=HealthClassification.REVIEW_REQUIRED,
                entity_type="Signal",
                entity_ids=(fact.signal_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Signal {fact.signal_id} has status='completed' but no "
                    "completion_date recorded - candidate for human review"
                ),
                structured_evidence={
                    "signal_id": fact.signal_id,
                    "status": fact.status,
                    "completion_date": fact.completion_date,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_f1(facts: tuple[SignalProvenanceFact, ...]) -> tuple[HealthFinding, ...]:
    """Signals with a legacy source_id but no governed SourceAssertion
    link - expected, not a defect, for every pre-governed-pipeline Signal.
    Emitted as ONE bucketed informational finding (never one finding per
    Signal), per the reviewed design's own explicit framing of this rule as
    "legacy-provenance-tier bucketing, not a per-row finding" - avoids
    manufacturing dozens of individually-alarming-looking findings out of
    one, well-understood architectural fact.
    """
    deduped = _dedupe_preserve_order(facts, key=lambda f: f.signal_id)
    ids = tuple(
        sorted(
            fact.signal_id
            for fact in deduped
            if fact.source_id is not None and not fact.has_governed_supporting_assertion
        )
    )
    if not ids:
        return ()
    return (
        HealthFinding(
            rule_id="FH-F1",
            classification=HealthClassification.INFORMATIONAL,
            entity_type="Signal",
            entity_ids=ids,
            airport_id=None,
            summary=(
                f"{len(ids)} Signals have a legacy source_id but no governed "
                "SourceAssertion link - expected for pre-governed-pipeline "
                "evidence, not a defect"
            ),
            structured_evidence={"signal_ids": ids, "count": len(ids)},
        ),
    )


def evaluate_fh_f2(facts: tuple[SourceAssertionReviewStateFact, ...]) -> tuple[HealthFinding, ...]:
    """SourceAssertion with no attributed airport, still unreviewed -
    legitimate for raw evidence pending identity-guard processing."""
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if fact.review_state != _UNREVIEWED:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-F2",
                classification=HealthClassification.INFORMATIONAL,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=None,
                summary=(
                    f"SourceAssertion {fact.assertion_id} has no attributed "
                    "airport and is still unreviewed - pending identity-guard "
                    "processing, not a defect"
                ),
                structured_evidence={"assertion_id": fact.assertion_id},
            )
        )
    return tuple(findings)


def evaluate_fh_f3(facts: tuple[SourceAssertionReviewStateFact, ...]) -> tuple[HealthFinding, ...]:
    """SourceAssertion with no attributed airport that HAS been reviewed - a
    materially different, more concerning case than FH-F2's "still
    pending" state, worth a human glance.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if fact.review_state != _REVIEWED:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-F3",
                classification=HealthClassification.REVIEW_REQUIRED,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=None,
                summary=(
                    f"SourceAssertion {fact.assertion_id} has been reviewed but "
                    "still has no attributed airport - candidate for human review"
                ),
                structured_evidence={"assertion_id": fact.assertion_id},
            )
        )
    return tuple(findings)


def evaluate_fh_g1(
    facts: tuple[SourceAssertionGovernanceDecisionFact, ...],
) -> tuple[HealthFinding, ...]:
    """A SourceAssertion at promotion_policy_decision=HUMAN_REVIEW_REQUIRED
    whose identity_guard_decision/intelligence_review_decision don't match
    the expected upstream values - may reflect the three governed-pipeline
    stages having been evaluated at different times against different
    evidence, per the same reasoning human_review_queue.py's own
    invariant_warnings already uses for this exact check (reused in spirit,
    not by import, to keep this module free of a Session-carrying
    dependency).
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if fact.promotion_policy_decision != _HUMAN_REVIEW_REQUIRED_PROMOTION_DECISION:
            continue
        identity_mismatch = fact.identity_guard_decision != _EXPECTED_IDENTITY_GUARD_DECISION
        intelligence_mismatch = (
            fact.intelligence_review_decision != _EXPECTED_INTELLIGENCE_REVIEW_DECISION
        )
        if not identity_mismatch and not intelligence_mismatch:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-G1",
                classification=HealthClassification.DETERMINISTIC_WARNING,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=None,
                summary=(
                    f"SourceAssertion {fact.assertion_id} is at "
                    "promotion_policy_decision=HUMAN_REVIEW_REQUIRED but its "
                    "identity_guard_decision/intelligence_review_decision do "
                    "not match the expected upstream values"
                ),
                structured_evidence={
                    "assertion_id": fact.assertion_id,
                    "identity_guard_decision": fact.identity_guard_decision,
                    "intelligence_review_decision": fact.intelligence_review_decision,
                    "promotion_policy_decision": fact.promotion_policy_decision,
                },
            )
        )
    return tuple(findings)


# ---------------------------------------------------------------------------
# Snapshot container + registry + batch evaluator.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FleetReviewSnapshot:
    airport_runway_counts: tuple[AirportRunwayCountFact, ...] = ()
    airport_codes: tuple[AirportCodeFact, ...] = ()
    installation_airport_linkages: tuple[InstallationAirportLinkageFact, ...] = ()
    installation_assertion_link_retractions: tuple[
        InstallationAssertionLinkRetractionFact, ...
    ] = ()
    signal_runway_airports: tuple[SignalRunwayAirportFact, ...] = ()
    signal_lifecycles: tuple[SignalLifecycleFact, ...] = ()
    signal_provenance: tuple[SignalProvenanceFact, ...] = ()
    source_assertion_review_states: tuple[SourceAssertionReviewStateFact, ...] = ()
    source_assertion_governance_decisions: tuple[
        SourceAssertionGovernanceDecisionFact, ...
    ] = ()


_REVIEW_RULE_EVALUATORS: tuple[
    tuple[str, str, Callable[[tuple[Any, ...]], tuple[HealthFinding, ...]]], ...
] = (
    ("FH-A1", "airport_runway_counts", evaluate_fh_a1),
    ("FH-A3", "airport_codes", evaluate_fh_a3),
    ("FH-C3", "installation_airport_linkages", evaluate_fh_c3),
    ("FH-C4", "installation_assertion_link_retractions", evaluate_fh_c4),
    ("FH-D3", "signal_runway_airports", evaluate_fh_d3),
    ("FH-D4", "signal_runway_airports", evaluate_fh_d4),
    ("FH-E1", "signal_lifecycles", evaluate_fh_e1),
    ("FH-E2", "signal_lifecycles", evaluate_fh_e2),
    ("FH-E4", "signal_lifecycles", evaluate_fh_e4),
    ("FH-F1", "signal_provenance", evaluate_fh_f1),
    ("FH-F2", "source_assertion_review_states", evaluate_fh_f2),
    ("FH-F3", "source_assertion_review_states", evaluate_fh_f3),
    ("FH-G1", "source_assertion_governance_decisions", evaluate_fh_g1),
)

REVIEW_RULE_IDS: tuple[str, ...] = tuple(rule_id for rule_id, _, _ in _REVIEW_RULE_EVALUATORS)


def evaluate_review_findings(snapshot: FleetReviewSnapshot) -> tuple[HealthFinding, ...]:
    """Runs all 13 implemented FHC3 rules against a snapshot, in stable
    registry order. Never called by, and never calls into,
    evaluate_hard_invariants() - the two rule tiers remain independently
    evaluable and independently testable.
    """
    findings: list[HealthFinding] = []
    for _rule_id, field_name, evaluator in _REVIEW_RULE_EVALUATORS:
        findings.extend(evaluator(getattr(snapshot, field_name)))
    return tuple(findings)
