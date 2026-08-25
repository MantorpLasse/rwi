"""Human reviewer CLI for UnknownAirportCandidate (UAC5A,
docs/architecture/rwi-uac5-human-review-and-continuation-report.md,
Slice 5 of docs/architecture/rwi-governed-new-airport-discovery-design.md).

    python -m scripts.review_unknown_airport_candidate --database data/runway_safe.db \\
        --candidate-id 7
        -> pure inspection: raw claimed identity, fingerprint, resolution
           state, full review history, linked SourceAssertion evidence,
           deterministic canonical-code matches. Never writes.

    python -m scripts.review_unknown_airport_candidate --database ... --candidate-id 7 \\
        --decision MATCH_EXISTING_AIRPORT --matched-airport-id 12 \\
        --reviewer human:you --reason "..."
        -> dry-run of RECORDING this review: shows whether it would be
           accepted. Never writes.

    python -m scripts.review_unknown_airport_candidate --database ... --candidate-id 7 \\
        --decision MATCH_EXISTING_AIRPORT --matched-airport-id 12 \\
        --reviewer human:you --reason "..." --allow-database-write
        -> the only invocation shape that RECORDS a review: appends exactly
           ONE UnknownAirportCandidateReview row via UAC1's own
           record_unknown_airport_candidate_review() (imported, never
           reimplemented). Never executes any canonical consequence -
           review recording and execution are two separate CLI modes,
           never conflated (see "MODE SEPARATION" below).

    python -m scripts.review_unknown_airport_candidate --database ... --candidate-id 7 \\
        --execute --review-id 41
        -> dry-run of EXECUTING an already-recorded review: shows whether
           resolution would succeed right now. Never writes.

    python -m scripts.review_unknown_airport_candidate --database ... --candidate-id 7 \\
        --execute --review-id 41 --allow-database-write \\
        [--new-airport-name ... --new-airport-country ... (CREATE_NEW_AIRPORT only)]
        -> the only invocation shape that EXECUTES: calls UAC4's own
           resolve_candidate_to_existing_airport()/
           create_airport_from_approved_candidate() (imported, never
           reimplemented), anchored to the EXACT --review-id given -
           UAC4's own stale-review protection (StaleReviewError) refuses
           if that review is no longer current, with zero CLI-side
           reimplementation of that check.

MODE SEPARATION (mission §3/§6/§7, a locked design decision): recording a
human review decision and executing its canonical consequence are always
two SEPARATE CLI invocations (`--decision ...` XOR `--execute ...`, never
both in one call) - matching UAC4's own strict review/execution
separation exactly. A human may record MATCH_EXISTING_AIRPORT/
CREATE_NEW_AIRPORT and choose, later, whether and when to execute it; an
operator who wants a single-command "record and execute" workflow must
run this script twice, deliberately, with the review id printed by the
first invocation as the second invocation's own `--review-id` handshake.
REJECT_CANDIDATE/DEFER have no execution counterpart at all - `--execute`
against a review recording either of those is refused (see
`_run_execute()`'s own final branch) since `record_unknown_airport_candidate_review()`
and UAC4's own execution functions already establish that these two
actions carry no canonical consequence to execute (design doc §7;
`rwi-uac4-unknown-airport-resolution-report.md` §20 DEFER/REJECT verdict).

DRY-RUN IMPLEMENTATION (a deliberate, documented design choice - NOT
duplicated business logic): every decision-bearing/execute invocation,
dry-run OR write alike, calls the SAME real governed service function
(`record_unknown_airport_candidate_review()`,
`resolve_candidate_to_existing_airport()`, or
`create_airport_from_approved_candidate()`) against a genuinely writable
`Session`. The ONLY difference between a dry-run and a write is whether
this script issues `session.commit()` (write, `--allow-database-write`
given) or `session.rollback()` (dry-run) at the very end - never a
CLI-side reimplementation, preview, or simulation of what the governed
function would do. This guarantees a dry-run's "would this be eligible"
answer can never drift from the real write path's own behavior, and
reuses this repository's own established "always call the real
persistence function, gate only the final commit" convention
(`scripts/review_signal_disposition.py`'s own dry-run precedent, adapted
here from a read-only-engine dry-run to a writable-engine-with-guaranteed-
rollback dry-run, since - unlike that script's own eligibility
computation - UAC1/UAC4's own governed functions perform their
validation AS PART OF the same call that would mutate).

DETERMINISTIC-ONLY CANONICAL-MATCH DISPLAY: inspection additionally
surfaces any EXISTING Airport whose `iata_code`/`icao_code`/`faa_code`
exactly matches (case/whitespace-insensitive, the same comparison
`unknown_airport_candidate_resolution.create_airport_from_approved_candidate()`'s
own duplicate-code defense already uses) the candidate's own claimed
`raw_iata_code`/`raw_icao_code`/`raw_faa_lid` - a purely informational aid
for a human considering `MATCH_EXISTING_AIRPORT`. This is NEVER a fuzzy
name/city/country similarity ranking (mission §5's own explicit
prohibition) and NEVER auto-selected, auto-suggested as the decision, or
used to gate/skip any check - a human still supplies `--matched-airport-id`
explicitly, always.

SCHEMA GATE: reuses `scripts.migrate_source_assertion_unknown_airport_uac2b.inspect()`
verbatim (which itself delegates to UAC2A's own `inspect()` - never
reimplemented, never imports migration EXECUTION into this module or any
`app/services/*` module) - refuses with
`UNKNOWN_AIRPORT_SCHEMA_MIGRATION_REQUIRED` before ever touching
`UnknownAirportCandidate`/`SourceAssertion` if the UAC2A/UAC2B schema is
not ready.

WRITE AUTHORIZATION / DATABASE ARGUMENT: `--database` has no default
(never the real production database by accident); `--allow-database-write`
is required for any write, matching every write-capable script in this
project. Pure inspection (`--decision`/`--execute` both absent) always
opens the target database via SQLite's own read-only URI mode
(`mode=ro`) - even a coding mistake that tried to write would be refused
at the driver level. No `app.database.SessionLocal`, no process-global
engine.

TRANSACTION BOUNDARY / REVIEW-SURVIVES-EXECUTION-FAILURE POLICY (mission
§22, an explicit design decision): review recording and execution are
NEVER wrapped in one shared transaction - even within a single CLI
process, review-recording and execute are two entirely separate
invocations by construction (MODE SEPARATION above), each opening its own
engine/session and issuing its own single commit-or-rollback. This means
a recorded review's commit is never contingent on some LATER execute
invocation succeeding: if a human records MATCH_EXISTING_AIRPORT today
and the execute invocation fails tomorrow (state changed, target Airport
deleted, etc.), the review row remains fully committed, auditable
authorization history - exactly as UAC4's own review/execution separation
already requires. This script never rolls back an already-committed
review merely because a later execute attempt fails; UAC4's own execution
functions already guarantee that a FAILED execute attempt itself performs
zero partial canonical mutation (their own `resolved-airport-id`-set +
SourceAssertion-move sequence is only ever committed by this script after
BOTH complete successfully in one call).

EXECUTION-AUDITABILITY OUTPUT (mission §21): a successful `--execute
--allow-database-write` invocation prints, at minimum: candidate id, the
exact `--review-id` anchor used, the action executed, the resulting
resolved/created Airport id, every moved SourceAssertion id, and the
review's own persisted `reviewer`/`reason`/`created_at` (read back from
the row this execution was anchored to). This is operational UX only - it
does NOT create any new persisted execution-audit record, and does not
change the execution-auditability verdict already reached in
`rwi-uac4-unknown-airport-resolution-report.md` §15 (Classification B,
non-blocking): which of two content-identical repeated reviews technically
triggered a given execution remains unrecorded by anything this script
does.

DOWNSTREAM CONTINUATION (UAC5B - see this file's own report for the full
architecture investigation): after a successful MATCH/CREATE execution,
this script's inspect output honestly notes that
`SourceAssertion.identity_guard_decision` remains `INSUFFICIENT_IDENTITY`
for the moved evidence and is NOT re-evaluated by anything in this
mission - see `docs/architecture/rwi-uac5-human-review-and-continuation-
report.md`'s "guard-replay feasibility" section for why: the structured
`EvidenceBag` categories `locations`/`issuers`/every `contradicting_*`
field/both `alternate_airport_runway_*` fields are never persisted
anywhere on `SourceAssertion` by either `persist_discovery_fragment()` or
`persist_candidate_linked_source_assertion()` (only a LOSSY,
comma-joined subset of `identifiers`/`names`/`runway_pairs`/`runway_ends`
survives) - a faithful guard replay is therefore not currently possible,
and this mission does not invent a lossy/partial substitute (a partial
replay could produce an actively MISLEADING ATTACH_CONFIRMED by omitting
lost contradiction evidence, which is worse than leaving the row held).
No `reevaluate_resolved_candidate_evidence()`-shaped service exists in
this repository as a result - see the report for the exact fields a
future slice must persist to make this possible.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Airport, SourceAssertion
from app.models.unknown_airport_candidate import (
    UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS,
    UnknownAirportCandidate,
    UnknownAirportCandidateReview,
)
from app.services.unknown_airport_candidate_persistence import (
    get_latest_unknown_airport_candidate_review,
    record_unknown_airport_candidate_review,
)
from app.services.unknown_airport_candidate_resolution import (
    AlreadyResolvedError,
    InconsistentCandidateStateError,
    RelevanceGateRefusedError,
    StaleReviewError,
    create_airport_from_approved_candidate,
    resolve_candidate_to_existing_airport,
)
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration

__all__ = [
    "SCHEMA_MIGRATION_REQUIRED_BLOCKER",
    "CANDIDATE_NOT_FOUND_BLOCKER",
    "ReviewSummary",
    "LinkedAssertionSummary",
    "DeterministicCodeMatch",
    "UnknownAirportCandidateReviewConfig",
    "UnknownAirportCandidateReviewResult",
    "check_schema_readiness",
    "build_engine",
    "run_review",
    "render_result",
    "main",
]

SCHEMA_MIGRATION_REQUIRED_BLOCKER = "UNKNOWN_AIRPORT_SCHEMA_MIGRATION_REQUIRED"
CANDIDATE_NOT_FOUND_BLOCKER = "CANDIDATE_NOT_FOUND"

_MATCH_ACTION = "MATCH_EXISTING_AIRPORT"
_CREATE_ACTION = "CREATE_NEW_AIRPORT"

_EXECUTABLE_ACTION_REFUSAL = (
    "review_id={review_id} does not reference a MATCH_EXISTING_AIRPORT or CREATE_NEW_AIRPORT review "
    "for this candidate - REJECT_CANDIDATE/DEFER reviews carry no canonical execution, and an "
    "unknown or another candidate's review_id is refused identically."
)


@dataclass(frozen=True)
class ReviewSummary:
    review_id: int
    action: str
    reviewer: str
    reason: str
    created_at: str
    matched_airport_id: "int | None"
    supersedes_review_id: "int | None"


@dataclass(frozen=True)
class LinkedAssertionSummary:
    source_assertion_id: int
    source_locator: "str | None"
    artifact_identity: "str | None"
    raw_relevant_text_excerpt: "str | None"
    identity_guard_decision: "str | None"


@dataclass(frozen=True)
class DeterministicCodeMatch:
    airport_id: int
    airport_name: str
    matched_field: str
    matched_value: str


@dataclass(frozen=True)
class UnknownAirportCandidateReviewConfig:
    database: Path
    candidate_id: int
    decision: "str | None" = None
    reviewer: "str | None" = None
    reason: "str | None" = None
    matched_airport_id: "int | None" = None
    supersedes_review_id: "int | None" = None
    execute: bool = False
    review_id: "int | None" = None
    new_airport_name: "str | None" = None
    new_airport_country: "str | None" = None
    new_airport_city: "str | None" = None
    new_airport_state_region: "str | None" = None
    new_airport_iata_code: "str | None" = None
    new_airport_icao_code: "str | None" = None
    new_airport_faa_code: "str | None" = None
    allow_database_write: bool = False


@dataclass(frozen=True)
class UnknownAirportCandidateReviewResult:
    """The one result shape both `main()` and the test suite consume - no
    parallel/duplicated code path between CLI output and importable,
    testable behavior, matching every write-capable script in this
    pipeline."""

    database: str
    schema_readiness: dict
    blockers: "tuple[str, ...]" = ()

    candidate_id: "int | None" = None
    candidate_found: "bool | None" = None
    raw_name: "str | None" = None
    raw_city: "str | None" = None
    raw_state_region: "str | None" = None
    raw_country: "str | None" = None
    raw_iata_code: "str | None" = None
    raw_icao_code: "str | None" = None
    raw_faa_lid: "str | None" = None
    raw_runway_designation: "str | None" = None
    candidate_fingerprint: "str | None" = None
    resolved_airport_id: "int | None" = None
    resolved_airport_name: "str | None" = None

    review_history: "tuple[ReviewSummary, ...]" = ()
    latest_review: "ReviewSummary | None" = None

    linked_assertion_count: int = 0
    linked_assertions: "tuple[LinkedAssertionSummary, ...]" = ()

    deterministic_code_matches: "tuple[DeterministicCodeMatch, ...]" = ()
    downstream_continuation_note: "str | None" = None

    mode: str = "inspect"

    # Review-recording mode.
    proposed_action: "str | None" = None
    proposed_reviewer: "str | None" = None
    proposed_reason: "str | None" = None
    proposed_matched_airport_id: "int | None" = None
    action_eligible: "bool | None" = None
    action_refusal_reason: "str | None" = None
    written: bool = False
    written_review_id: "int | None" = None

    # Execute mode.
    execute_review_id: "int | None" = None
    execute_action: "str | None" = None
    execute_eligible: "bool | None" = None
    execute_refusal_reason: "str | None" = None
    executed: bool = False
    execution_resolved_airport_id: "int | None" = None
    execution_created_airport_id: "int | None" = None
    execution_moved_assertion_ids: "tuple[int, ...]" = ()


def check_schema_readiness(database: Path) -> dict:
    """Read-only, via UAC2B's own already-proven `inspect()` (which itself
    delegates to UAC2A's own `inspect()`) - reused verbatim, never
    reimplemented. Migration EXECUTION (`upgrade()`/`downgrade()`) is
    never imported here or anywhere in `app/services/*`."""
    result = uac2b_migration.inspect(database)
    return {
        "uac2a_ready": result["uac2a_ready"],
        "table_exists": result["table_exists"],
        "unknown_airport_candidate_id_column_present": result["unknown_airport_candidate_id_column_present"],
        "matches_expected_schema": result["matches_expected_schema"],
        "ready": result["ready"],
    }


def build_engine(database: Path, *, writable: bool):
    """Bound to EXACTLY the resolved `database` path. Read-only SQLite URI
    mode whenever `writable` is False (pure inspection only) - even a
    coding mistake that tried to write would be refused at the driver
    level, matching `review_signal_disposition.py`'s own `build_engine()`
    precedent verbatim."""
    resolved = database.resolve()
    if writable:
        return create_engine(f"sqlite:///{resolved}", future=True)
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def _validate_config(config: UnknownAirportCandidateReviewConfig) -> None:
    """Argument-combination misuse invalid regardless of database state -
    raised before any database is opened."""
    if config.execute and config.decision is not None:
        raise ValueError(
            "--execute cannot be combined with --decision - review recording and execution are "
            "separate CLI invocations (see module docstring's own MODE SEPARATION section)"
        )

    if config.execute:
        if config.review_id is None:
            raise ValueError("--execute requires --review-id (the exact review this execution is anchored to)")
        if (
            config.reviewer is not None or config.reason is not None or config.matched_airport_id is not None
            or config.supersedes_review_id is not None
        ):
            raise ValueError(
                "--reviewer/--reason/--matched-airport-id/--supersedes-review-id are only valid when "
                "recording a review (--decision), not with --execute"
            )
    else:
        if config.review_id is not None:
            raise ValueError("--review-id is only valid with --execute")
        if any((
            config.new_airport_name, config.new_airport_country, config.new_airport_city,
            config.new_airport_state_region, config.new_airport_iata_code,
            config.new_airport_icao_code, config.new_airport_faa_code,
        )):
            raise ValueError(
                "--new-airport-* fields are only valid with --execute (CREATE_NEW_AIRPORT execution)"
            )

    if config.decision is not None:
        if config.decision not in UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS:
            raise ValueError(
                f"--decision must be one of {UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS!r}, got {config.decision!r}"
            )
        if not config.reviewer or not config.reviewer.strip():
            raise ValueError("--reviewer is required when --decision is supplied")
        if not config.reason or not config.reason.strip():
            raise ValueError("--reason is required when --decision is supplied")
        if config.decision == _MATCH_ACTION and config.matched_airport_id is None:
            raise ValueError(f"--matched-airport-id is required when --decision {_MATCH_ACTION}")
        if config.decision != _MATCH_ACTION and config.matched_airport_id is not None:
            raise ValueError(f"--matched-airport-id is only valid when --decision {_MATCH_ACTION}")
    elif config.supersedes_review_id is not None:
        raise ValueError("--supersedes-review-id is only valid when --decision is supplied")

    if config.allow_database_write and config.decision is None and not config.execute:
        raise ValueError("--allow-database-write requires --decision or --execute")


def _summarize_review(review: UnknownAirportCandidateReview) -> ReviewSummary:
    return ReviewSummary(
        review_id=review.id, action=review.action, reviewer=review.reviewer, reason=review.reason,
        created_at=review.created_at.isoformat(), matched_airport_id=review.matched_airport_id,
        supersedes_review_id=review.supersedes_review_id,
    )


def _deterministic_code_matches(session: Session, candidate: UnknownAirportCandidate) -> "tuple[DeterministicCodeMatch, ...]":
    matches: "list[DeterministicCodeMatch]" = []
    for field_name, claimed_value in (
        ("iata_code", candidate.raw_iata_code),
        ("icao_code", candidate.raw_icao_code),
        ("faa_code", candidate.raw_faa_lid),
    ):
        value = claimed_value.strip() if claimed_value and claimed_value.strip() else None
        if value is None:
            continue
        target = value.casefold()
        for airport in session.query(Airport).filter(getattr(Airport, field_name).isnot(None)).all():
            existing_value = getattr(airport, field_name)
            if existing_value and existing_value.strip().casefold() == target:
                matches.append(DeterministicCodeMatch(
                    airport_id=airport.id, airport_name=airport.name,
                    matched_field=field_name, matched_value=value,
                ))
    return tuple(matches)


def _read_candidate_state(session: Session, candidate: UnknownAirportCandidate) -> dict:
    # UAC-H1 no_autoflush hardening: this entire function is read-only
    # (inspect/preview only, never writes) - wraps it all in
    # session.no_autoflush so it can never trigger a premature flush of
    # some unrelated pending object the caller happens to be holding in
    # the same session, matching get_latest_unknown_airport_candidate_review()'s
    # own hardening (app.services.unknown_airport_candidate_persistence).
    with session.no_autoflush:
        reviews = (
            session.query(UnknownAirportCandidateReview)
            .filter(UnknownAirportCandidateReview.candidate_id == candidate.id)
            .order_by(UnknownAirportCandidateReview.created_at.asc(), UnknownAirportCandidateReview.id.asc())
            .all()
        )
        review_history = tuple(_summarize_review(r) for r in reviews)
        latest = get_latest_unknown_airport_candidate_review(session, candidate.id)
        latest_summary = _summarize_review(latest) if latest is not None else None

        assertions = (
            session.query(SourceAssertion)
            .filter(SourceAssertion.unknown_airport_candidate_id == candidate.id)
            .order_by(SourceAssertion.id.asc())
            .all()
        )
        linked = tuple(
            LinkedAssertionSummary(
                source_assertion_id=a.id, source_locator=a.source_locator, artifact_identity=a.artifact_identity,
                raw_relevant_text_excerpt=((a.raw_relevant_text or "")[:200] or None),
                identity_guard_decision=a.identity_guard_decision,
            )
            for a in assertions
        )

        resolved_airport_name = None
        if candidate.resolved_airport_id is not None:
            resolved = session.get(Airport, candidate.resolved_airport_id)
            resolved_airport_name = resolved.name if resolved is not None else None

        downstream_note = None
        if candidate.resolved_airport_id is not None:
            downstream_note = (
                "This candidate is resolved - LINKED EVIDENCE above is now empty because resolution "
                "clears unknown_airport_candidate_id on every moved SourceAssertion (see Airport "
                f"#{candidate.resolved_airport_id}'s own source_assertions for that evidence now). "
                "Formerly candidate-linked evidence now carries airport_id but identity_guard_decision "
                "remains INSUFFICIENT_IDENTITY (never re-evaluated - see the UAC5 report's "
                "guard-replay-feasibility finding: faithful replay is not currently possible with the "
                "evidence persisted for this candidate). This evidence will not reach "
                "intelligence-review/promotion/Signal creation until a future, separately-designed "
                "slice addresses this."
            )

        result = dict(
            candidate_id=candidate.id, candidate_found=True,
            raw_name=candidate.raw_name, raw_city=candidate.raw_city, raw_state_region=candidate.raw_state_region,
            raw_country=candidate.raw_country, raw_iata_code=candidate.raw_iata_code,
            raw_icao_code=candidate.raw_icao_code, raw_faa_lid=candidate.raw_faa_lid,
            raw_runway_designation=candidate.raw_runway_designation,
            candidate_fingerprint=candidate.candidate_fingerprint,
            resolved_airport_id=candidate.resolved_airport_id, resolved_airport_name=resolved_airport_name,
            review_history=review_history, latest_review=latest_summary,
            linked_assertion_count=len(linked), linked_assertions=linked,
            deterministic_code_matches=_deterministic_code_matches(session, candidate),
            downstream_continuation_note=downstream_note,
        )
    return result


def _run_review_write(
    session: Session, config: UnknownAirportCandidateReviewConfig, candidate: UnknownAirportCandidate,
    base: dict, database_str: str, schema: dict,
) -> UnknownAirportCandidateReviewResult:
    kwargs = dict(
        database=database_str, schema_readiness=schema, **base,
        mode="write" if config.allow_database_write else "dry_run",
        proposed_action=config.decision, proposed_reviewer=config.reviewer, proposed_reason=config.reason,
        proposed_matched_airport_id=config.matched_airport_id,
    )
    try:
        review = record_unknown_airport_candidate_review(
            session, candidate, action=config.decision, reason=config.reason, reviewer=config.reviewer,
            matched_airport_id=config.matched_airport_id, supersedes_review_id=config.supersedes_review_id,
        )
    except ValueError as exc:
        session.rollback()
        return UnknownAirportCandidateReviewResult(**kwargs, action_eligible=False, action_refusal_reason=str(exc))

    if not config.allow_database_write:
        session.rollback()
        return UnknownAirportCandidateReviewResult(**kwargs, action_eligible=True)

    session.commit()
    return UnknownAirportCandidateReviewResult(
        **kwargs, action_eligible=True, written=True, written_review_id=review.id,
    )


def _run_execute(
    session: Session, config: UnknownAirportCandidateReviewConfig, candidate: UnknownAirportCandidate,
    base: dict, database_str: str, schema: dict,
) -> UnknownAirportCandidateReviewResult:
    kwargs = dict(
        database=database_str, schema_readiness=schema, **base,
        mode="execute_write" if config.allow_database_write else "execute_dry_run",
        execute_review_id=config.review_id,
    )

    review = session.get(UnknownAirportCandidateReview, config.review_id)
    execute_action = review.action if review is not None and review.candidate_id == candidate.id else None
    kwargs["execute_action"] = execute_action

    if execute_action == _MATCH_ACTION:
        try:
            result = resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=config.review_id)
        except (AlreadyResolvedError, StaleReviewError, InconsistentCandidateStateError, ValueError) as exc:
            session.rollback()
            return UnknownAirportCandidateReviewResult(**kwargs, execute_eligible=False, execute_refusal_reason=str(exc))
        if not config.allow_database_write:
            session.rollback()
            return UnknownAirportCandidateReviewResult(**kwargs, execute_eligible=True)
        session.commit()
        return UnknownAirportCandidateReviewResult(
            **kwargs, execute_eligible=True, executed=True,
            execution_resolved_airport_id=result.resolved_airport_id,
            execution_moved_assertion_ids=result.moved_source_assertion_ids,
        )

    if execute_action == _CREATE_ACTION:
        if not config.new_airport_name or not config.new_airport_name.strip():
            session.rollback()
            return UnknownAirportCandidateReviewResult(
                **kwargs, execute_eligible=False,
                execute_refusal_reason="--new-airport-name is required to execute a CREATE_NEW_AIRPORT review",
            )
        if not config.new_airport_country or not config.new_airport_country.strip():
            session.rollback()
            return UnknownAirportCandidateReviewResult(
                **kwargs, execute_eligible=False,
                execute_refusal_reason="--new-airport-country is required to execute a CREATE_NEW_AIRPORT review",
            )
        try:
            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=config.review_id,
                name=config.new_airport_name, country=config.new_airport_country,
                city=config.new_airport_city, state_region=config.new_airport_state_region,
                iata_code=config.new_airport_iata_code, icao_code=config.new_airport_icao_code,
                faa_code=config.new_airport_faa_code,
            )
        except (
            AlreadyResolvedError, StaleReviewError, InconsistentCandidateStateError,
            RelevanceGateRefusedError, ValueError,
        ) as exc:
            session.rollback()
            return UnknownAirportCandidateReviewResult(**kwargs, execute_eligible=False, execute_refusal_reason=str(exc))
        if not config.allow_database_write:
            session.rollback()
            return UnknownAirportCandidateReviewResult(**kwargs, execute_eligible=True)
        session.commit()
        return UnknownAirportCandidateReviewResult(
            **kwargs, execute_eligible=True, executed=True,
            execution_created_airport_id=result.created_airport_id,
            execution_moved_assertion_ids=result.moved_source_assertion_ids,
        )

    session.rollback()
    return UnknownAirportCandidateReviewResult(
        **kwargs, execute_eligible=False,
        execute_refusal_reason=_EXECUTABLE_ACTION_REFUSAL.format(review_id=config.review_id),
    )


def run_review(config: UnknownAirportCandidateReviewConfig) -> UnknownAirportCandidateReviewResult:
    """The one function that does the work (schema check, read, validate,
    optionally write) and returns a single, importable result."""
    _validate_config(config)
    database_str = str(config.database.resolve())
    schema = check_schema_readiness(config.database)
    if not schema["ready"]:
        return UnknownAirportCandidateReviewResult(
            database=database_str, schema_readiness=schema, blockers=(SCHEMA_MIGRATION_REQUIRED_BLOCKER,),
        )

    writable = config.decision is not None or config.execute
    engine = build_engine(config.database, writable=writable)
    try:
        with Session(engine) as session:
            candidate = session.get(UnknownAirportCandidate, config.candidate_id)
            if candidate is None:
                session.rollback()
                return UnknownAirportCandidateReviewResult(
                    database=database_str, schema_readiness=schema,
                    blockers=(CANDIDATE_NOT_FOUND_BLOCKER,),
                    candidate_id=config.candidate_id, candidate_found=False,
                )

            base = _read_candidate_state(session, candidate)

            if config.execute:
                return _run_execute(session, config, candidate, base, database_str, schema)
            if config.decision is not None:
                return _run_review_write(session, config, candidate, base, database_str, schema)

            session.rollback()
            return UnknownAirportCandidateReviewResult(
                database=database_str, schema_readiness=schema, mode="inspect", **base,
            )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Human-readable report rendering - text formatting only, no new data.
# ---------------------------------------------------------------------------


def _mode_label(result: UnknownAirportCandidateReviewResult) -> str:
    labels = {
        "inspect": "INSPECT (read-only)",
        "dry_run": "DRY RUN - record review (no write)",
        "write": "WRITE - review recorded (committed)",
        "execute_dry_run": "DRY RUN - execute resolution (no write)",
        "execute_write": "WRITE - resolution executed (committed)",
    }
    return labels.get(result.mode, result.mode)


def render_result(result: UnknownAirportCandidateReviewResult) -> str:
    lines: "list[str]" = [f"Database: {result.database}"]

    if result.blockers and result.candidate_found is not False:
        lines.append(f"BLOCKED: {', '.join(result.blockers)}")
        lines.append(f"schema_readiness: {result.schema_readiness}")
        return "\n".join(lines) + "\n"

    lines.append(f"Mode: {_mode_label(result)}")
    lines.append(f"Candidate id: {result.candidate_id}")
    if result.candidate_found is False:
        lines.append(f"  NOT FOUND - refused: {', '.join(result.blockers)}")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("RAW CLAIMED IDENTITY")
    lines.append(f"  raw_name: {result.raw_name!r}")
    lines.append(f"  raw_city: {result.raw_city!r}")
    lines.append(f"  raw_state_region: {result.raw_state_region!r}")
    lines.append(f"  raw_country: {result.raw_country!r}")
    lines.append(f"  raw_iata_code: {result.raw_iata_code!r}")
    lines.append(f"  raw_icao_code: {result.raw_icao_code!r}")
    lines.append(f"  raw_faa_lid: {result.raw_faa_lid!r}")
    lines.append(f"  raw_runway_designation: {result.raw_runway_designation!r}")
    lines.append(f"  candidate_fingerprint: {result.candidate_fingerprint}")

    lines.append("")
    lines.append("RESOLUTION STATE")
    if result.resolved_airport_id is not None:
        lines.append(f"  RESOLVED -> Airport #{result.resolved_airport_id} ({result.resolved_airport_name})")
    else:
        lines.append("  UNRESOLVED")

    lines.append("")
    lines.append(f"LATEST REVIEW: {result.latest_review}")
    lines.append(f"REVIEW HISTORY ({len(result.review_history)} total)")
    if not result.review_history:
        lines.append("  (none)")
    for r in result.review_history:
        lines.append(
            f"  #{r.review_id} action={r.action} reviewer={r.reviewer!r} reason={r.reason!r} "
            f"created_at={r.created_at} matched_airport_id={r.matched_airport_id} "
            f"supersedes_review_id={r.supersedes_review_id}"
        )

    lines.append("")
    lines.append(f"LINKED EVIDENCE ({result.linked_assertion_count} SourceAssertion(s))")
    for a in result.linked_assertions:
        lines.append(
            f"  SourceAssertion #{a.source_assertion_id} source_locator={a.source_locator!r} "
            f"artifact_identity={a.artifact_identity!r} identity_guard_decision={a.identity_guard_decision!r}"
        )
        if a.raw_relevant_text_excerpt:
            lines.append(f"    text: {a.raw_relevant_text_excerpt!r}")

    lines.append("")
    lines.append(f"DETERMINISTIC CANONICAL-CODE MATCHES ({len(result.deterministic_code_matches)})")
    if not result.deterministic_code_matches:
        lines.append("  (none)")
    for m in result.deterministic_code_matches:
        lines.append(
            f"  Airport #{m.airport_id} ({m.airport_name}) - {m.matched_field}={m.matched_value!r} "
            "exactly matches this candidate's own claimed value"
        )

    if result.downstream_continuation_note:
        lines.append("")
        lines.append(f"DOWNSTREAM CONTINUATION NOTE: {result.downstream_continuation_note}")

    if result.proposed_action is not None:
        lines.append("")
        lines.append(f"Proposed review: {result.proposed_action}")
        lines.append(f"  reviewer: {result.proposed_reviewer}")
        lines.append(f"  reason: {result.proposed_reason}")
        lines.append(f"  matched_airport_id: {result.proposed_matched_airport_id}")
        lines.append(f"  eligible: {result.action_eligible}")
        if result.action_refusal_reason:
            lines.append(f"  refused: {result.action_refusal_reason}")
        if result.written:
            lines.append(f"  WRITTEN: UnknownAirportCandidateReview #{result.written_review_id}")
        elif result.action_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to record this review)")

    if result.execute_review_id is not None:
        lines.append("")
        lines.append(f"Execute review_id: {result.execute_review_id} (action={result.execute_action})")
        lines.append(f"  eligible: {result.execute_eligible}")
        if result.execute_refusal_reason:
            lines.append(f"  refused: {result.execute_refusal_reason}")
        if result.executed:
            lines.append("  EXECUTED:")
            lines.append(f"    candidate_id: {result.candidate_id}")
            lines.append(f"    review_id: {result.execute_review_id}")
            lines.append(f"    action: {result.execute_action}")
            if result.execution_resolved_airport_id is not None:
                lines.append(f"    resolved_airport_id: {result.execution_resolved_airport_id}")
            if result.execution_created_airport_id is not None:
                lines.append(f"    created_airport_id: {result.execution_created_airport_id}")
            lines.append(f"    moved_source_assertion_ids: {list(result.execution_moved_assertion_ids)}")
        elif result.execute_eligible:
            lines.append("  DRY RUN - no write performed (pass --allow-database-write to execute this resolution)")

    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database", type=Path, required=True,
        help="Path to the SQLite database, read-only unless --decision/--execute is also given. "
        "No default - never the real production database by accident.",
    )
    parser.add_argument("--candidate-id", type=int, required=True, dest="candidate_id")
    parser.add_argument("--decision", choices=UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS, default=None)
    parser.add_argument("--reviewer", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--matched-airport-id", type=int, default=None, dest="matched_airport_id")
    parser.add_argument(
        "--supersedes-review-id", type=int, default=None, dest="supersedes_review_id",
        help="Optional. Only valid with --decision. Cross-references an earlier review this one "
        "supersedes (audit annotation only - get_latest_unknown_airport_candidate_review() "
        "determines currency by recency, never by walking this chain).",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--review-id", type=int, default=None, dest="review_id")
    parser.add_argument("--new-airport-name", type=str, default=None, dest="new_airport_name")
    parser.add_argument("--new-airport-country", type=str, default=None, dest="new_airport_country")
    parser.add_argument("--new-airport-city", type=str, default=None, dest="new_airport_city")
    parser.add_argument("--new-airport-state-region", type=str, default=None, dest="new_airport_state_region")
    parser.add_argument("--new-airport-iata-code", type=str, default=None, dest="new_airport_iata_code")
    parser.add_argument("--new-airport-icao-code", type=str, default=None, dest="new_airport_icao_code")
    parser.add_argument("--new-airport-faa-code", type=str, default=None, dest="new_airport_faa_code")
    parser.add_argument("--allow-database-write", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = UnknownAirportCandidateReviewConfig(
        database=args.database, candidate_id=args.candidate_id,
        decision=args.decision, reviewer=args.reviewer, reason=args.reason,
        matched_airport_id=args.matched_airport_id, supersedes_review_id=args.supersedes_review_id,
        execute=args.execute, review_id=args.review_id,
        new_airport_name=args.new_airport_name, new_airport_country=args.new_airport_country,
        new_airport_city=args.new_airport_city, new_airport_state_region=args.new_airport_state_region,
        new_airport_iata_code=args.new_airport_iata_code, new_airport_icao_code=args.new_airport_icao_code,
        new_airport_faa_code=args.new_airport_faa_code,
        allow_database_write=args.allow_database_write,
    )
    result = run_review(config)
    print(render_result(result))
    if result.blockers:
        return 1
    if result.proposed_action is not None and not result.action_eligible:
        return 1
    if result.execute_review_id is not None and not result.execute_eligible:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
