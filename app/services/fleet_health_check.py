from __future__ import annotations

"""Fleet Health Check — FHC2 read-only database adapter.

    Session (real DB or fixture)
        -> build_fleet_hard_invariant_snapshot()
        -> FleetHardInvariantSnapshot (DB-free, from app.services.fleet_health_rules)
        -> evaluate_hard_invariants() (FHC1, unmodified)
        -> tuple[HealthFinding, ...]

    run_fleet_hard_invariant_check(session) does both steps in one call.

This module's ONLY job is to turn current database state into the exact,
narrow fact dataclasses FHC1 (app.services.fleet_health_rules) already
defines and reviewed - it never re-derives, widens, or reinterprets any of
the 11 rules' invariants themselves. Every query here is SELECT-only:
`session.query()`/`session.execute()` are the only session calls anywhere in
this module. No `session.add()`, no `session.flush()`, no `session.commit()`,
no `session.delete()`, no attribute assignment on any ORM-mapped instance,
anywhere - the same read-only discipline every prior adapter in this
pipeline (existing_signal_reconciliation_candidates.py,
human_review_reconciliation.py) already established.

QUERY SHAPE, why fan-out is structurally impossible here: every join this
module performs is on a to-one foreign key (Installation.runway_id ->
Runway, Signal.runway_id -> Runway, PhysicalInstallationIdentity.runway_id/
runway_end_id -> Runway/RunwayEnd, SourceAssertion.signal_id -> Signal) -
never a join across a one-to-many/reverse relationship. A to-one join can
never multiply rows on its own (left) side, so none of these queries can
return more than one row per source entity regardless of how many *other*,
unrelated one-to-many relationships that entity happens to participate in
(verified directly by the join-fan-out attack fixtures in this module's own
test suite). The one aggregate query (RunwayEnd counts per Runway) uses
GROUP BY specifically because that relationship IS one-to-many by design;
GROUP BY collapses it back to one row per Runway, which is exactly the count
FH-B1 needs, not a source of duplication.

DEDUP: FHC1's own rule evaluators already defensively deduplicate by entity
id (found and fixed at the FHC1 review checkpoint) - this module does not
rely on that as a substitute for correct querying, but it is a second,
independent line of defense should any future maintenance change here ever
introduce an accidental fan-out.

LATEST REVIEWER ACTION: reuses the exact `(created_at DESC, id DESC)`
ordering `app.services.reviewer_action_persistence.get_latest_reviewer_action()`
and `app.services.human_review_queue._latest_actions_by_assertion()` already
use - never chain-walks `supersedes_action_id`. Governance facts are built
only for SourceAssertions that have at least one recorded ReviewerAction
(FH-G2/FH-G3 can never fire for `latest_action=None`, so there is no reason
to query or represent the other ~220 real rows that have never been
reviewed at all).

DETERMINISM: every fact tuple this module returns is sorted by its own
primary entity id ascending before being handed to FHC1 - the database's own
row-return order is never trusted.

SCHEMA READINESS: this module assumes the ORM-mapped schema it queries
against already exists (the same assumption every other read-only service in
this pipeline makes - see reviewer_action_persistence.py,
human_review_queue.py, existing_signal_reconciliation_candidates.py, none of
which self-check schema readiness either). A future FHC3 CLI, not this
module, is responsible for a `check_schema_readiness()`-style gate before
opening a session against the real database, exactly as R4D/R4E's own CLIs
do - this module does not silently auto-migrate or repair anything if a
required column is missing; it simply raises whatever SQLAlchemy itself
raises for a missing table/column, unmodified.
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from app.models import (
    Airport,
    Installation,
    PhysicalInstallationIdentity,
    ReviewerAction,
    Runway,
    RunwayEnd,
    Signal,
    SourceAssertion,
)
from app.services.fleet_health_rules import (
    AirportCodeFact,
    FleetHardInvariantSnapshot,
    HealthFinding,
    InstallationRunwayAirportFact,
    InstallationYearFact,
    PhysicalInstallationIdentityAirportFact,
    RunwayDesignationFact,
    RunwayEndCountFact,
    SignalConstructionDateFact,
    SignalRunwayAirportFact,
    SourceAssertionGovernanceFact,
    SourceAssertionSignalAirportFact,
    evaluate_hard_invariants,
)

__all__ = [
    "build_fleet_hard_invariant_snapshot",
    "run_fleet_hard_invariant_check",
]


def _build_airport_codes(session: Session) -> "tuple[AirportCodeFact, ...]":
    """One row per Airport, no joins. FH-A2 input."""
    rows = session.query(Airport).order_by(Airport.id.asc()).all()
    return tuple(
        AirportCodeFact(
            airport_id=row.id,
            iata_code=row.iata_code,
            icao_code=row.icao_code,
            faa_code=row.faa_code,
        )
        for row in rows
    )


def _build_runway_end_counts(session: Session) -> "tuple[RunwayEndCountFact, ...]":
    """One row per Runway, RunwayEnd count via LEFT JOIN + GROUP BY - never
    once per Runway in a loop. A Runway with zero RunwayEnd rows still
    appears here (count=0, a genuine potential FH-B1 violation); an Airport
    with zero Runway rows contributes nothing at all (there is no Runway row
    to aggregate), matching FH-B1's own "absence, not contradiction" contract
    for airport-level completeness. FH-B1 input.
    """
    rows = (
        session.query(Runway.id, Runway.airport_id, func.count(RunwayEnd.id))
        .outerjoin(RunwayEnd, RunwayEnd.runway_id == Runway.id)
        .group_by(Runway.id, Runway.airport_id)
        .order_by(Runway.id.asc())
        .all()
    )
    return tuple(
        RunwayEndCountFact(runway_id=runway_id, airport_id=airport_id, runway_end_count=count)
        for runway_id, airport_id, count in rows
    )


def _build_runway_designations(session: Session) -> "tuple[RunwayDesignationFact, ...]":
    """One row per Runway, no joins. FH-B2 input."""
    rows = session.query(Runway).order_by(Runway.id.asc()).all()
    return tuple(
        RunwayDesignationFact(
            runway_id=row.id, airport_id=row.airport_id, designation=row.designation
        )
        for row in rows
    )


def _build_installation_years(session: Session) -> "tuple[InstallationYearFact, ...]":
    """One row per Installation, no joins. FH-C1 input."""
    rows = session.query(Installation).order_by(Installation.id.asc()).all()
    return tuple(
        InstallationYearFact(
            installation_id=row.id,
            airport_id=row.airport_id,
            install_year=row.install_year,
            replacement_year=row.replacement_year,
        )
        for row in rows
    )


def _build_installation_runway_airports(
    session: Session,
) -> "tuple[InstallationRunwayAirportFact, ...]":
    """One row per Installation, LEFT JOIN to Runway (a to-one FK -
    Installation.runway_id - can never fan out this join). FH-C2 input."""
    rows = (
        session.query(
            Installation.id, Installation.airport_id, Installation.runway_id, Runway.airport_id
        )
        .outerjoin(Runway, Runway.id == Installation.runway_id)
        .order_by(Installation.id.asc())
        .all()
    )
    return tuple(
        InstallationRunwayAirportFact(
            installation_id=installation_id,
            installation_airport_id=installation_airport_id,
            runway_id=runway_id,
            runway_airport_id=runway_airport_id,
        )
        for installation_id, installation_airport_id, runway_id, runway_airport_id in rows
    )


def _build_physical_installation_identity_airports(
    session: Session,
) -> "tuple[PhysicalInstallationIdentityAirportFact, ...]":
    """One row per PhysicalInstallationIdentity. Two independent to-one
    joins: directly to Runway (via runway_id), and to RunwayEnd -> its own
    parent Runway (via runway_end_id -> RunwayEnd.runway_id) - a second,
    distinct Runway alias, since the same PhysicalInstallationIdentity row
    can (in principle) name a runway_id and a runway_end_id belonging to
    different runways, and FH-C5 must be able to detect that independently
    for each link. Neither join can fan out (both are to-one FKs). FH-C5
    input.
    """
    runway_via_end = aliased(Runway)
    rows = (
        session.query(
            PhysicalInstallationIdentity.id,
            PhysicalInstallationIdentity.airport_id,
            PhysicalInstallationIdentity.runway_id,
            Runway.airport_id,
            PhysicalInstallationIdentity.runway_end_id,
            runway_via_end.airport_id,
        )
        .outerjoin(Runway, Runway.id == PhysicalInstallationIdentity.runway_id)
        .outerjoin(RunwayEnd, RunwayEnd.id == PhysicalInstallationIdentity.runway_end_id)
        .outerjoin(runway_via_end, runway_via_end.id == RunwayEnd.runway_id)
        .order_by(PhysicalInstallationIdentity.id.asc())
        .all()
    )
    return tuple(
        PhysicalInstallationIdentityAirportFact(
            identity_id=identity_id,
            identity_airport_id=identity_airport_id,
            runway_id=runway_id,
            runway_airport_id=runway_airport_id,
            runway_end_id=runway_end_id,
            runway_end_airport_id=runway_end_airport_id,
        )
        for (
            identity_id,
            identity_airport_id,
            runway_id,
            runway_airport_id,
            runway_end_id,
            runway_end_airport_id,
        ) in rows
    )


def _build_signal_runway_airports(session: Session) -> "tuple[SignalRunwayAirportFact, ...]":
    """One row per Signal, LEFT JOIN to Runway (to-one FK, no fan-out).
    FH-D1 input."""
    rows = (
        session.query(Signal.id, Signal.airport_id, Signal.runway_id, Runway.airport_id)
        .outerjoin(Runway, Runway.id == Signal.runway_id)
        .order_by(Signal.id.asc())
        .all()
    )
    return tuple(
        SignalRunwayAirportFact(
            signal_id=signal_id,
            signal_airport_id=signal_airport_id,
            runway_id=runway_id,
            runway_airport_id=runway_airport_id,
        )
        for signal_id, signal_airport_id, runway_id, runway_airport_id in rows
    )


def _build_source_assertion_signal_airports(
    session: Session,
) -> "tuple[SourceAssertionSignalAirportFact, ...]":
    """Only SourceAssertions with a non-NULL signal_id - there is nothing to
    compare for the ~221 of 222 real rows that have never produced/linked a
    Signal, so no fact is built for them at all (not merely a no-op finding
    - see the module docstring's "only actual linked relationships become
    facts" contract). INNER JOIN to Signal (to-one FK via signal_id, no
    fan-out). FH-D2 input.
    """
    rows = (
        session.query(
            SourceAssertion.id,
            SourceAssertion.airport_id,
            SourceAssertion.signal_id,
            Signal.airport_id,
        )
        .join(Signal, Signal.id == SourceAssertion.signal_id)
        .filter(SourceAssertion.signal_id.isnot(None))
        .order_by(SourceAssertion.id.asc())
        .all()
    )
    return tuple(
        SourceAssertionSignalAirportFact(
            assertion_id=assertion_id,
            assertion_airport_id=assertion_airport_id,
            signal_id=signal_id,
            signal_airport_id=signal_airport_id,
        )
        for assertion_id, assertion_airport_id, signal_id, signal_airport_id in rows
    )


def _build_signal_construction_dates(
    session: Session,
) -> "tuple[SignalConstructionDateFact, ...]":
    """One row per Signal, no joins. FH-E3 input."""
    rows = session.query(Signal).order_by(Signal.id.asc()).all()
    return tuple(
        SignalConstructionDateFact(
            signal_id=row.id,
            airport_id=row.airport_id,
            construction_start=row.construction_start,
            completion_date=row.completion_date,
        )
        for row in rows
    )


def _latest_reviewer_action_by_assertion_id(
    session: Session,
) -> "dict[int, ReviewerAction]":
    """Batched equivalent of calling
    app.services.reviewer_action_persistence.get_latest_reviewer_action()
    once per SourceAssertion that has at least one ReviewerAction - fetches
    every ReviewerAction row in one query, ordered by the identical
    (source_assertion_id, created_at DESC, id DESC) tiebreak
    app.services.human_review_queue._latest_actions_by_assertion() already
    uses, then keeps only the first (= latest) row per source_assertion_id.
    Never chain-walks supersedes_action_id. Tested directly against
    get_latest_reviewer_action() for equivalence across fixtures."""
    rows = (
        session.query(ReviewerAction)
        .order_by(
            ReviewerAction.source_assertion_id,
            ReviewerAction.created_at.desc(),
            ReviewerAction.id.desc(),
        )
        .all()
    )
    latest: "dict[int, ReviewerAction]" = {}
    for row in rows:
        latest.setdefault(row.source_assertion_id, row)
    return latest


def _build_source_assertion_governance(
    session: Session,
) -> "tuple[SourceAssertionGovernanceFact, ...]":
    """One fact per SourceAssertion that has at least one ReviewerAction
    recorded (never for the ~220 real rows that have never been reviewed at
    all - FH-G2/FH-G3 can never fire for latest_action=None, so there is
    nothing to represent). Two bounded queries total, regardless of fleet
    size: (1) every ReviewerAction, reduced in Python to the latest per
    assertion; (2) the own signal_id of exactly those assertions
    (`SourceAssertion.id.in_(...)`), never one query per assertion. FH-G2/
    FH-G3 input.
    """
    latest_by_assertion = _latest_reviewer_action_by_assertion_id(session)
    if not latest_by_assertion:
        return ()
    assertion_ids = sorted(latest_by_assertion)
    signal_id_by_assertion_id = dict(
        session.query(SourceAssertion.id, SourceAssertion.signal_id)
        .filter(SourceAssertion.id.in_(assertion_ids))
        .all()
    )
    return tuple(
        SourceAssertionGovernanceFact(
            assertion_id=assertion_id,
            signal_id=signal_id_by_assertion_id.get(assertion_id),
            latest_action=latest_by_assertion[assertion_id].action,
            latest_action_duplicate_of_signal_id=(
                latest_by_assertion[assertion_id].duplicate_of_signal_id
            ),
        )
        for assertion_id in assertion_ids
    )


def build_fleet_hard_invariant_snapshot(session: Session) -> FleetHardInvariantSnapshot:
    """Builds one FleetHardInvariantSnapshot from current database state.

    Read-only: every call above is a SELECT (session.query(...)); nothing is
    added, flushed, committed, or mutated. A fixed, small number of queries
    regardless of fleet size (one per fact type, plus one extra bounded
    query for governance signal_id lookup) - never one query per entity.

    Wrapped in `session.no_autoflush` (review-checkpoint fix): SQLAlchemy's
    default `autoflush=True` would otherwise silently flush any of the
    CALLER's own pending, uncommitted changes on this session to the
    database the instant this function's first SELECT ran - a genuine
    hidden-write side effect of what is supposed to be a strictly read-only
    call (verified directly: without this guard, a caller's dirty in-memory
    edit was pushed to the database via a real UPDATE statement inside the
    caller's own transaction merely by calling this function). This context
    manager only suppresses autoflush for the duration of this one call; it
    never changes the session's own configured autoflush setting, and never
    itself flushes, commits, or discards the caller's pending changes -
    they remain exactly as pending (dirty/new) after this function returns
    as they were before it was called.
    """
    with session.no_autoflush:
        return FleetHardInvariantSnapshot(
            airport_codes=_build_airport_codes(session),
            runway_end_counts=_build_runway_end_counts(session),
            runway_designations=_build_runway_designations(session),
            installation_years=_build_installation_years(session),
            installation_runway_airports=_build_installation_runway_airports(session),
            physical_installation_identity_airports=(
                _build_physical_installation_identity_airports(session)
            ),
            signal_runway_airports=_build_signal_runway_airports(session),
            source_assertion_signal_airports=_build_source_assertion_signal_airports(session),
            signal_construction_dates=_build_signal_construction_dates(session),
            source_assertion_governance=_build_source_assertion_governance(session),
        )


def run_fleet_hard_invariant_check(session: Session) -> "tuple[HealthFinding, ...]":
    """Builds a snapshot from current database state and evaluates all 11
    FHC1 hard invariants against it, unmodified. Read-only end to end."""
    snapshot = build_fleet_hard_invariant_snapshot(session)
    return evaluate_hard_invariants(snapshot)
