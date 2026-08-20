from __future__ import annotations

"""Fleet Health Check — FHC1 pure hard-invariant rule core.

Implements exactly the 11 rules the fleet-health design's critical-review
checkpoint approved as genuinely interpretation-free `DETERMINISTIC_ERROR`
invariants (see
docs/architecture/fleet-health-check-design-and-real-db-reconnaissance.md
§6, §19-20, and its "Critical Review Corrections" section):

FH-A2, FH-B1, FH-B2, FH-C1, FH-C2, FH-C5, FH-D1, FH-D2, FH-E3, FH-G2, FH-G3

FH-E1 and FH-E2 were reviewed and rejected for this tier (their significance
requires business/lifecycle inference the model cannot support) and must
never appear here. FH-H2 requires running the static export and is not a
pure function of persisted fields, so it also stays out of this module.

This module is deliberately pure: no SQLAlchemy, no ORM, no Session, no
database path, no filesystem, no network, no clock, no random/UUID identity,
no provider-specific (FAA/USAspending/vendor) logic. Every rule operates on
small, frozen, rule-specific fact dataclasses that the caller (a future
FHC2 DB adapter) is responsible for building from a single already-joined
read - the join itself, and any "latest reviewer action" recency
derivation, happens outside this module so no rule here ever has to
re-interpret raw history. Same input always produces an equality-identical
output; there is no hidden state.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Callable, Mapping, Optional


class HealthClassification(str, Enum):
    """The five-value classification vocabulary from the reviewed design.

    FHC1 only ever emits DETERMINISTIC_ERROR; the other four members exist
    here only so HealthFinding.classification has a stable, non-drifting
    type shared with later slices (FHC3 will emit DETERMINISTIC_WARNING/
    REVIEW_REQUIRED/INFORMATIONAL, FHC4/FHC5 may touch
    NOT_CURRENTLY_DETECTABLE) - this is a label enum, not a scoring engine.
    """

    DETERMINISTIC_ERROR = "DETERMINISTIC_ERROR"
    DETERMINISTIC_WARNING = "DETERMINISTIC_WARNING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INFORMATIONAL = "INFORMATIONAL"
    NOT_CURRENTLY_DETECTABLE = "NOT_CURRENTLY_DETECTABLE"


@dataclass(frozen=True)
class HealthFinding:
    """One hard-invariant violation. Never scored, never a repair instruction."""

    rule_id: str
    classification: HealthClassification
    entity_type: str
    entity_ids: tuple[int, ...]
    airport_id: Optional[int]
    summary: str
    structured_evidence: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Pure input fact dataclasses.
#
# Each rule receives only the exact, already-joined fields it needs - never
# a whole ORM object, never a raw dict, never a Session. Cross-entity rules
# (C2, C5, D1, D2) take BOTH sides of the comparison pre-resolved (e.g. a
# Signal's own airport_id and the airport_id of the Runway it points at)
# rather than an id to look up, so this module can never itself perform an
# unsafe/implicit join or guess at a relationship.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AirportCodeFact:
    """FH-A2 input: one Airport's three identity codes, exactly as stored."""

    airport_id: int
    iata_code: Optional[str]
    icao_code: Optional[str]
    faa_code: Optional[str]


@dataclass(frozen=True)
class RunwayEndCountFact:
    """FH-B1 input: how many RunwayEnd rows a Runway actually has."""

    runway_id: int
    airport_id: int
    runway_end_count: int


@dataclass(frozen=True)
class RunwayDesignationFact:
    """FH-B2 input: one Runway's designation within its airport."""

    runway_id: int
    airport_id: int
    designation: str


@dataclass(frozen=True)
class InstallationYearFact:
    """FH-C1 input: one Installation's own install/replacement years."""

    installation_id: int
    airport_id: int
    install_year: Optional[int]
    replacement_year: Optional[int]


@dataclass(frozen=True)
class InstallationRunwayAirportFact:
    """FH-C2 input: an Installation's airport vs. its linked Runway's airport.

    runway_id/runway_airport_id are both None when the Installation has no
    runway link at all (a legitimate, airport-level-only Installation) -
    that is absence of a relationship, not a contradiction, and must not
    trigger this rule.
    """

    installation_id: int
    installation_airport_id: int
    runway_id: Optional[int]
    runway_airport_id: Optional[int]


@dataclass(frozen=True)
class PhysicalInstallationIdentityAirportFact:
    """FH-C5 input: a PhysicalInstallationIdentity's airport vs. its linked
    Runway's and RunwayEnd's airports. Either link may legitimately be
    absent (both fields None together) without triggering this rule.
    """

    identity_id: int
    identity_airport_id: int
    runway_id: Optional[int]
    runway_airport_id: Optional[int]
    runway_end_id: Optional[int]
    runway_end_airport_id: Optional[int]


@dataclass(frozen=True)
class SignalRunwayAirportFact:
    """FH-D1 input: a Signal's airport vs. its linked Runway's airport.

    runway_id/runway_airport_id both None means an airport-level-only
    Signal (no runway claimed) - absence, not contradiction.
    """

    signal_id: int
    signal_airport_id: int
    runway_id: Optional[int]
    runway_airport_id: Optional[int]


@dataclass(frozen=True)
class SourceAssertionSignalAirportFact:
    """FH-D2 input: a SourceAssertion's airport vs. its linked Signal's airport.

    signal_id/signal_airport_id None means the assertion has not (yet)
    produced/linked a Signal - not itself a contradiction. assertion_airport_id
    None means the assertion has no attributed airport at all (a legitimate,
    pre-identity-guard state) - there is nothing to compare against, so this
    also never triggers the rule.
    """

    assertion_id: int
    assertion_airport_id: Optional[int]
    signal_id: Optional[int]
    signal_airport_id: Optional[int]


@dataclass(frozen=True)
class SignalConstructionDateFact:
    """FH-E3 input: one Signal's own construction_start/completion_date.

    Deliberately narrower than FH-E1/E2's rejected year-only comparison:
    both dates describe the same construction effort on the same row, so
    there is no multi-phase, asynchronously-updated-field risk here.
    """

    signal_id: int
    airport_id: int
    construction_start: Optional[date]
    completion_date: Optional[date]


@dataclass(frozen=True)
class SourceAssertionGovernanceFact:
    """FH-G2/FH-G3 input: one SourceAssertion's own signal_id plus the
    already-resolved *latest* ReviewerAction recorded against it.

    "Latest" recency derivation (created_at desc, id desc, never chain-
    walking supersedes_action_id - see reviewer_action_persistence.py's own
    get_latest_reviewer_action()) is deliberately NOT reimplemented in this
    pure module; the caller must supply the already-resolved latest action,
    exactly as FH-D4's R4D precedent resolves reconciliation facts outside
    the presentation layer that consumes them. latest_action is None when no
    ReviewerAction has ever been recorded for this assertion.
    """

    assertion_id: int
    signal_id: Optional[int]
    latest_action: Optional[str]
    latest_action_duplicate_of_signal_id: Optional[int]


# Mirrored, not imported, from app.models.reviewer_action.REVIEWER_ACTIONS -
# importing that module would pull in SQLAlchemy transitively, violating
# this module's purity. Same precedent as R4D's human_review_reconciliation.py
# (see the reviewed design doc §2). Keep in sync by hand if the upstream
# vocabulary ever changes.
_ACTION_MARK_DUPLICATE = "MARK_DUPLICATE"
_ACTION_DEFER = "DEFER"
_ACTION_NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
_ACTION_REJECT_SIGNAL = "REJECT_SIGNAL"
_TERMINAL_NON_CREATING_ACTIONS = (
    _ACTION_DEFER,
    _ACTION_NEEDS_MORE_EVIDENCE,
    _ACTION_REJECT_SIGNAL,
)


# ---------------------------------------------------------------------------
# Rule evaluators. Each takes a tuple of its own fact type and returns a
# tuple of HealthFinding (possibly empty) - never mutates its input, never
# raises on well-typed input, never consults anything but the fields given.
#
# Every evaluator is defensive against a duplicated input row for the same
# entity (e.g. a future FHC2 adapter's JOIN fan-out returning the same
# Runway/Airport/Installation/etc. twice) - a repeated fact for one entity
# must never produce two findings, and must never make an entity look like
# it "collides with itself" in a batch/grouping rule (found and fixed during
# this checkpoint's own adversarial review; see the FHC1 report's Critical
# Review Corrections section).
# ---------------------------------------------------------------------------


def _dedupe_preserve_order(
    facts: tuple[Any, ...], key: Callable[[Any], Any]
) -> tuple[Any, ...]:
    """Keep only the first fact seen per key, preserving input order."""
    seen: dict[Any, Any] = {}
    for fact in facts:
        fact_key = key(fact)
        if fact_key not in seen:
            seen[fact_key] = fact
    return tuple(seen.values())


def evaluate_fh_a2(facts: tuple[AirportCodeFact, ...]) -> tuple[HealthFinding, ...]:
    """Duplicate IATA/ICAO/FAA code across airports.

    Only NULL and the exact empty string "" are treated as "no code" and
    excluded from collision detection - per the reviewed design's own
    corrected FH-A2 spec. A whitespace-only value is a real, literal code
    value here and is compared as-is (never trimmed/normalized), since the
    design does not authorize collapsing it.
    """
    findings: list[HealthFinding] = []
    deduped_facts = _dedupe_preserve_order(facts, key=lambda f: f.airport_id)
    for code_type, getter in (
        ("iata_code", lambda f: f.iata_code),
        ("icao_code", lambda f: f.icao_code),
        ("faa_code", lambda f: f.faa_code),
    ):
        groups: dict[str, list[int]] = {}
        for fact in deduped_facts:
            value = getter(fact)
            if value is None or value == "":
                continue
            groups.setdefault(value, []).append(fact.airport_id)
        for value, airport_ids in groups.items():
            if len(airport_ids) < 2:
                continue
            ids = tuple(sorted(airport_ids))
            findings.append(
                HealthFinding(
                    rule_id="FH-A2",
                    classification=HealthClassification.DETERMINISTIC_ERROR,
                    entity_type="Airport",
                    entity_ids=ids,
                    airport_id=None,
                    summary=(
                        f"{len(ids)} airports share {code_type}={value!r}: "
                        f"airports {ids}"
                    ),
                    structured_evidence={
                        "code_type": code_type,
                        "code_value": value,
                        "airport_ids": ids,
                    },
                )
            )
    return tuple(findings)


def evaluate_fh_b1(facts: tuple[RunwayEndCountFact, ...]) -> tuple[HealthFinding, ...]:
    """A Runway must have exactly two RunwayEnd rows."""
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.runway_id):
        if fact.runway_end_count == 2:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-B1",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="Runway",
                entity_ids=(fact.runway_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Runway {fact.runway_id} has {fact.runway_end_count} "
                    "RunwayEnd rows (expected exactly 2)"
                ),
                structured_evidence={
                    "runway_id": fact.runway_id,
                    "runway_end_count": fact.runway_end_count,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_b2(facts: tuple[RunwayDesignationFact, ...]) -> tuple[HealthFinding, ...]:
    """Duplicate runway designation within one airport.

    Designation is compared literally (exact string equality) - no case
    folding, no whitespace trimming, no text parsing of heading/suffix.
    """
    findings: list[HealthFinding] = []
    deduped_facts = _dedupe_preserve_order(facts, key=lambda f: f.runway_id)
    groups: dict[tuple[int, str], list[int]] = {}
    for fact in deduped_facts:
        key = (fact.airport_id, fact.designation)
        groups.setdefault(key, []).append(fact.runway_id)
    for (airport_id, designation), runway_ids in groups.items():
        if len(runway_ids) < 2:
            continue
        ids = tuple(sorted(runway_ids))
        findings.append(
            HealthFinding(
                rule_id="FH-B2",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="Runway",
                entity_ids=ids,
                airport_id=airport_id,
                summary=(
                    f"Airport {airport_id} has {len(ids)} runways sharing "
                    f"designation {designation!r}: runways {ids}"
                ),
                structured_evidence={
                    "airport_id": airport_id,
                    "designation": designation,
                    "runway_ids": ids,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_c1(facts: tuple[InstallationYearFact, ...]) -> tuple[HealthFinding, ...]:
    """An Installation's replacement_year cannot precede its own install_year."""
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.installation_id):
        if fact.install_year is None or fact.replacement_year is None:
            continue
        if fact.replacement_year >= fact.install_year:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-C1",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="Installation",
                entity_ids=(fact.installation_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Installation {fact.installation_id} has "
                    f"replacement_year={fact.replacement_year} before "
                    f"install_year={fact.install_year}"
                ),
                structured_evidence={
                    "installation_id": fact.installation_id,
                    "install_year": fact.install_year,
                    "replacement_year": fact.replacement_year,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_c2(
    facts: tuple[InstallationRunwayAirportFact, ...],
) -> tuple[HealthFinding, ...]:
    """An Installation's linked Runway must belong to the same Airport."""
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.installation_id):
        if fact.runway_id is None:
            continue
        if fact.runway_airport_id == fact.installation_airport_id:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-C2",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="Installation",
                entity_ids=(fact.installation_id,),
                airport_id=fact.installation_airport_id,
                summary=(
                    f"Installation {fact.installation_id} belongs to airport "
                    f"{fact.installation_airport_id} but its runway "
                    f"{fact.runway_id} belongs to airport "
                    f"{fact.runway_airport_id}"
                ),
                structured_evidence={
                    "installation_id": fact.installation_id,
                    "installation_airport_id": fact.installation_airport_id,
                    "runway_id": fact.runway_id,
                    "runway_airport_id": fact.runway_airport_id,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_c5(
    facts: tuple[PhysicalInstallationIdentityAirportFact, ...],
) -> tuple[HealthFinding, ...]:
    """A PhysicalInstallationIdentity's linked Runway/RunwayEnd must belong
    to the same Airport as the identity itself.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.identity_id):
        runway_mismatch = (
            fact.runway_id is not None
            and fact.runway_airport_id != fact.identity_airport_id
        )
        runway_end_mismatch = (
            fact.runway_end_id is not None
            and fact.runway_end_airport_id != fact.identity_airport_id
        )
        if not runway_mismatch and not runway_end_mismatch:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-C5",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="PhysicalInstallationIdentity",
                entity_ids=(fact.identity_id,),
                airport_id=fact.identity_airport_id,
                summary=(
                    f"PhysicalInstallationIdentity {fact.identity_id} belongs "
                    f"to airport {fact.identity_airport_id} but its linked "
                    f"runway/runway-end belongs to a different airport"
                ),
                structured_evidence={
                    "identity_id": fact.identity_id,
                    "identity_airport_id": fact.identity_airport_id,
                    "runway_id": fact.runway_id,
                    "runway_airport_id": fact.runway_airport_id,
                    "runway_end_id": fact.runway_end_id,
                    "runway_end_airport_id": fact.runway_end_airport_id,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_d1(facts: tuple[SignalRunwayAirportFact, ...]) -> tuple[HealthFinding, ...]:
    """A Signal's linked Runway must belong to the same Airport as the Signal."""
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.signal_id):
        if fact.runway_id is None:
            continue
        if fact.runway_airport_id == fact.signal_airport_id:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-D1",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="Signal",
                entity_ids=(fact.signal_id,),
                airport_id=fact.signal_airport_id,
                summary=(
                    f"Signal {fact.signal_id} belongs to airport "
                    f"{fact.signal_airport_id} but its runway "
                    f"{fact.runway_id} belongs to airport "
                    f"{fact.runway_airport_id}"
                ),
                structured_evidence={
                    "signal_id": fact.signal_id,
                    "signal_airport_id": fact.signal_airport_id,
                    "runway_id": fact.runway_id,
                    "runway_airport_id": fact.runway_airport_id,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_d2(
    facts: tuple[SourceAssertionSignalAirportFact, ...],
) -> tuple[HealthFinding, ...]:
    """A SourceAssertion's linked Signal must belong to the same Airport as
    the assertion itself (when the assertion has an attributed airport).
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if fact.signal_id is None or fact.assertion_airport_id is None:
            continue
        if fact.signal_airport_id == fact.assertion_airport_id:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-D2",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=fact.assertion_airport_id,
                summary=(
                    f"SourceAssertion {fact.assertion_id} belongs to airport "
                    f"{fact.assertion_airport_id} but its linked Signal "
                    f"{fact.signal_id} belongs to airport "
                    f"{fact.signal_airport_id}"
                ),
                structured_evidence={
                    "assertion_id": fact.assertion_id,
                    "assertion_airport_id": fact.assertion_airport_id,
                    "signal_id": fact.signal_id,
                    "signal_airport_id": fact.signal_airport_id,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_e3(
    facts: tuple[SignalConstructionDateFact, ...],
) -> tuple[HealthFinding, ...]:
    """A Signal's construction_start cannot be after its own completion_date.

    Equal dates are allowed (construction starting and completing on the
    same recorded date is not a contradiction). No current-date/today
    inference is used anywhere in this comparison.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.signal_id):
        if fact.construction_start is None or fact.completion_date is None:
            continue
        if fact.construction_start <= fact.completion_date:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-E3",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="Signal",
                entity_ids=(fact.signal_id,),
                airport_id=fact.airport_id,
                summary=(
                    f"Signal {fact.signal_id} has construction_start="
                    f"{fact.construction_start.isoformat()} after "
                    f"completion_date={fact.completion_date.isoformat()}"
                ),
                structured_evidence={
                    "signal_id": fact.signal_id,
                    "construction_start": fact.construction_start,
                    "completion_date": fact.completion_date,
                },
            )
        )
    return tuple(findings)


def evaluate_fh_g2(
    facts: tuple[SourceAssertionGovernanceFact, ...],
) -> tuple[HealthFinding, ...]:
    """If the latest ReviewerAction is MARK_DUPLICATE, the SourceAssertion's
    own signal_id must equal that action's duplicate_of_signal_id.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if fact.latest_action != _ACTION_MARK_DUPLICATE:
            continue
        if fact.signal_id == fact.latest_action_duplicate_of_signal_id:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-G2",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=None,
                summary=(
                    f"SourceAssertion {fact.assertion_id}'s latest action is "
                    f"MARK_DUPLICATE targeting Signal "
                    f"{fact.latest_action_duplicate_of_signal_id}, but "
                    f"signal_id={fact.signal_id}"
                ),
                structured_evidence={
                    "assertion_id": fact.assertion_id,
                    "latest_action": fact.latest_action,
                    "signal_id": fact.signal_id,
                    "latest_action_duplicate_of_signal_id": (
                        fact.latest_action_duplicate_of_signal_id
                    ),
                },
            )
        )
    return tuple(findings)


def evaluate_fh_g3(
    facts: tuple[SourceAssertionGovernanceFact, ...],
) -> tuple[HealthFinding, ...]:
    """If the latest ReviewerAction is DEFER/NEEDS_MORE_EVIDENCE/REJECT_SIGNAL,
    the SourceAssertion's own signal_id must be NULL.
    """
    findings: list[HealthFinding] = []
    for fact in _dedupe_preserve_order(facts, key=lambda f: f.assertion_id):
        if fact.latest_action not in _TERMINAL_NON_CREATING_ACTIONS:
            continue
        if fact.signal_id is None:
            continue
        findings.append(
            HealthFinding(
                rule_id="FH-G3",
                classification=HealthClassification.DETERMINISTIC_ERROR,
                entity_type="SourceAssertion",
                entity_ids=(fact.assertion_id,),
                airport_id=None,
                summary=(
                    f"SourceAssertion {fact.assertion_id}'s latest action is "
                    f"{fact.latest_action}, but signal_id={fact.signal_id} "
                    "is set"
                ),
                structured_evidence={
                    "assertion_id": fact.assertion_id,
                    "latest_action": fact.latest_action,
                    "signal_id": fact.signal_id,
                },
            )
        )
    return tuple(findings)


# ---------------------------------------------------------------------------
# Snapshot container + registry + batch evaluator.
#
# FleetHardInvariantSnapshot is a thin bundle of the already-narrow,
# rule-specific fact tuples above - not a monolithic mega-object built from
# raw entity dumps. A future FHC2 adapter builds one of these from a single
# read-only pass over the database (doing the joins/latest-action
# resolution itself) and hands it to evaluate_hard_invariants().
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FleetHardInvariantSnapshot:
    airport_codes: tuple[AirportCodeFact, ...] = ()
    runway_end_counts: tuple[RunwayEndCountFact, ...] = ()
    runway_designations: tuple[RunwayDesignationFact, ...] = ()
    installation_years: tuple[InstallationYearFact, ...] = ()
    installation_runway_airports: tuple[InstallationRunwayAirportFact, ...] = ()
    physical_installation_identity_airports: tuple[
        PhysicalInstallationIdentityAirportFact, ...
    ] = ()
    signal_runway_airports: tuple[SignalRunwayAirportFact, ...] = ()
    source_assertion_signal_airports: tuple[SourceAssertionSignalAirportFact, ...] = ()
    signal_construction_dates: tuple[SignalConstructionDateFact, ...] = ()
    source_assertion_governance: tuple[SourceAssertionGovernanceFact, ...] = ()


# Stable, deterministic-order registry: (rule_id, snapshot field name,
# evaluator function). A simple static tuple, not dynamic plugin machinery -
# adding a rule later means adding one more row here.
_RULE_EVALUATORS: tuple[
    tuple[str, str, Callable[[tuple[Any, ...]], tuple[HealthFinding, ...]]], ...
] = (
    ("FH-A2", "airport_codes", evaluate_fh_a2),
    ("FH-B1", "runway_end_counts", evaluate_fh_b1),
    ("FH-B2", "runway_designations", evaluate_fh_b2),
    ("FH-C1", "installation_years", evaluate_fh_c1),
    ("FH-C2", "installation_runway_airports", evaluate_fh_c2),
    ("FH-C5", "physical_installation_identity_airports", evaluate_fh_c5),
    ("FH-D1", "signal_runway_airports", evaluate_fh_d1),
    ("FH-D2", "source_assertion_signal_airports", evaluate_fh_d2),
    ("FH-E3", "signal_construction_dates", evaluate_fh_e3),
    ("FH-G2", "source_assertion_governance", evaluate_fh_g2),
    ("FH-G3", "source_assertion_governance", evaluate_fh_g3),
)

RULE_IDS: tuple[str, ...] = tuple(rule_id for rule_id, _, _ in _RULE_EVALUATORS)


def evaluate_hard_invariants(
    snapshot: FleetHardInvariantSnapshot,
) -> tuple[HealthFinding, ...]:
    """Run all 11 FHC1 rules against a snapshot, in stable registry order."""
    findings: list[HealthFinding] = []
    for _rule_id, field_name, evaluator in _RULE_EVALUATORS:
        findings.extend(evaluator(getattr(snapshot, field_name)))
    return tuple(findings)
