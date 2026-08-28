"""Read-only candidate-discovery adapter for existing-Signal reconciliation
(docs/architecture/existing-signal-reconciliation-guard-design.md S11; R2 of
that document's own S16 roadmap - see
docs/architecture/existing-signal-reconciliation-r2-candidate-adapter-report.md).

    Session + SourceAssertion (already governed)
        -> build_reconciliation_subject()
        -> ExistingSignalReconciliationSubject (DB-free snapshot)

    Session + SourceAssertion
        -> find_reconciliation_candidates()
        -> tuple[ReconciliationCandidateSignal, ...] (DB-free snapshots)

    both feed, unmodified, into the already-reviewed, already-committed pure
    core: app.services.existing_signal_reconciliation.evaluate_existing_signal_reconciliation()
        -> STOP (no decision is made here, no Signal is written, no
           ReviewerAction is written, no governed-creation integration - all
           a future, separately-authorized slice, R3)

THIS MODULE DISCOVERS CANDIDATES; IT DOES NOT DECIDE ANYTHING. Every function
here only ever performs SELECT-shaped reads and pure-Python assembly of
already-reviewed R1 dataclasses - never a ranking, a score, a "best
candidate" pick, or any judgment about whether a candidate is a genuine
match. That judgment belongs exclusively to
app.services.existing_signal_reconciliation, unmodified by this module.
Every field this module populates is copied from a real, already-existing
column or a real, already-existing relationship - never inferred from any
free-text/narrative field, never a financial field, never a fuzzy
comparison, and this module never imports anything from
app.acquisition (no source-family-specific extraction of any kind happens
here; if claims are needed, the caller supplies an already-extracted
tuple[Claim, ...], never raw text). This property is deliberately verifiable
by plain inspection rather than by trusting this paragraph: the test suite's
own firewall checks scan this module's actual source for the literal names
of the excluded columns, so this docstring names none of them itself.

Performs SELECT only: `session.query()`/`session.get()` are the only session
calls anywhere in this module. No `session.add()`, no `session.flush()`, no
`session.commit()`, no attribute assignment on any ORM-mapped instance,
anywhere.

CANDIDATE SCOPE (design doc S11 - "candidate discovery must never scan
across airports"): `find_reconciliation_candidates()` returns Signals at the
subject SourceAssertion's own `airport_id`, plus - defensively - whatever
Signal `source_assertion.signal_id` already names, even if that Signal's own
`airport_id` happens to differ (a data-integrity anomaly that must surface,
never be silently hidden by a scoped query). If `airport_id` is `None` and
`signal_id` is also `None`, this function returns an empty tuple rather than
scanning every Signal in the database - a bounded, fail-closed default, not
a permissive one. (In practice this combination barely matters:
`evaluate_existing_signal_reconciliation()`'s own ALREADY_LINKED short-circuit
never even inspects the candidate tuple once `subject.existing_signal_id` is
set, so an empty/non-empty candidate result for an already-linked subject
changes nothing about the eventual decision - see
`build_reconciliation_subject()` below for the field that actually drives
that outcome.)

QUERY STRATEGY (design doc S11's own "avoid the obvious N+1"): candidate
Signals, their supporting SourceAssertions, and those assertions' own
installation-identity links are each fetched in one batched query apiece
(`.filter(...id.in_(...))`), never once per Signal in a loop - see
`_build_candidate()`'s own docstring for the exact three-query shape.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import InstallationAssertionLink, Signal, SourceAssertion
from app.services.evidence_claim_semantics import Claim
from app.services.existing_signal_reconciliation import (
    ExistingSignalReconciliationSubject,
    ReconciliationCandidateSignal,
)

__all__ = [
    "build_reconciliation_subject",
    "find_reconciliation_candidates",
]

_SAME_PHYSICAL_INSTALLATION = "SAME_PHYSICAL_INSTALLATION"

# Deterministic, documented priority order for collapsing a Signal's several
# independent year-shaped fields into R1's single `reference_year` axis
# (design doc S6 - "Target/planning/procurement/construction/completion year
# fields" is treated as one weak, [COMPATIBILITY]-tier concept, not five
# separately-scored ones). Ordered earliest-in-project-lifecycle first,
# purely for a stable, arbitrary-but-fixed tie-break - the choice of order
# has no safety consequence, since `reference_year` can only ever contribute
# non-blocking advisory metadata (never an anchor) in the R1 core.
# The candidate's own private, personal, unverified annotation fields are
# deliberately excluded from this priority list and from every other
# reconciliation field this module populates - an outside hunch must not
# silently become structural reconciliation evidence.
_YEAR_FIELD_PRIORITY = ("target_year", "planning_year", "procurement_year")


def _latest_installation_links_by_assertion_id(
    links: "list[InstallationAssertionLink]",
) -> "dict[int, InstallationAssertionLink]":
    """Reduces a (possibly multi-assertion) list of `InstallationAssertionLink`
    rows to the single most-recently-recorded row per `assertion_id` -
    mirroring `app.services.reviewer_action_persistence.get_latest_reviewer_action()`'s
    own "latest means most recently recorded, not chain-walking" discipline
    for the structurally identical append-only-reconciliation-history shape
    `InstallationAssertionLink` already is. `(reviewed_at, id)` is the same
    two-part tie-break `ReviewerAction`'s own ordering uses (`created_at`
    there, `reviewed_at` here - the two tables' respective timestamp
    columns), never a naive single-field sort that could tie.

    This exists because a link whose own `outcome` is
    `SAME_PHYSICAL_INSTALLATION` may since have been explicitly retracted or
    corrected by a *later* link naming it via `supersedes_link_id` (design
    doc's own domain precedent: a human "changing their mind appends a new
    row rather than editing the old one"). Reading every historical
    `SAME_PHYSICAL_INSTALLATION` row without this reduction would let a
    retracted decision keep contributing a live identity anchor forever -
    found and fixed during this module's own review checkpoint."""
    latest: "dict[int, InstallationAssertionLink]" = {}
    for link in links:
        current = latest.get(link.assertion_id)
        if current is None or (link.reviewed_at, link.id) > (current.reviewed_at, current.id):
            latest[link.assertion_id] = link
    return latest


def _signal_reference_year(signal: Signal) -> Optional[int]:
    for field_name in _YEAR_FIELD_PRIORITY:
        value = getattr(signal, field_name)
        if value is not None:
            return value
    if signal.construction_start is not None:
        return signal.construction_start.year
    if signal.completion_date is not None:
        return signal.completion_date.year
    return None


def build_reconciliation_subject(
    source_assertion: SourceAssertion,
    claims: "tuple[Claim, ...]" = (),
    *,
    category: Optional[str] = None,
    reference_year: Optional[int] = None,
) -> ExistingSignalReconciliationSubject:
    """Builds the R1 subject snapshot for one governed SourceAssertion.

    Fields read directly, structurally, from the SourceAssertion row and its
    already-loadable relationships - no claims needed for these:
    `existing_signal_id` (<- `SourceAssertion.signal_id`), `airport_id`,
    `runway_id` (the canonical, resolved column - never one of the several
    free-text runway-wording columns on the same row, which this function
    never reads), `source_id`, `artifact_identity`, and
    `physical_installation_ids` (every `physical_installation_id` from this
    assertion's own `installation_assertion_links` whose `outcome` is
    `SAME_PHYSICAL_INSTALLATION` - the transitive anchor path design doc S6
    identified; `UNRESOLVED`/`DIFFERENT_PHYSICAL_INSTALLATION` links
    contribute nothing here, matching that outcome's own meaning).

    `vendor_names` is derived from `claims`: the `party` of every claim's
    `relationship`, where present - an already-extracted, already-structured
    name (design doc S6's own "structured party names... never raw text
    scanned by this module"), never a reparse of anything. Deduplicated and
    sorted for determinism (S21).

    `evidence_date` is derived from `claims` too: the EARLIEST (minimum)
    `temporal.as_of_date` among claims that carry one - a document's claims
    typically share one `as_of_date` (the cited document's own date), so any
    consistent, deterministic tie-break is safe; earliest is chosen so this
    function never overstates how recent the evidence is. `None` if no claim
    carries a temporal context with a date.

    `category` and `reference_year` are explicit, caller-supplied context,
    not derived from `claims` or the SourceAssertion row at all - see this
    module's own report S3/S25 for why: `Claim.category` is an unrelated,
    epistemic-shape concept (explicit_document_fact/procedural_request/...),
    not a domain lifecycle category like `Signal.category`'s own
    "replacement"; and no field anywhere on `SourceAssertion` or `Claim`
    represents an independent "target/planning year" distinct from the
    document's own date. Both stay `None` (contributing no advisory
    evidence, never a fabricated one) unless a caller who genuinely has this
    information - e.g. the same human-selected `category` a governed-
    creation caller is about to pass to `create_signal_from_approved_review()`
    - supplies it explicitly.
    """
    latest_links_by_assertion = _latest_installation_links_by_assertion_id(
        list(source_assertion.installation_assertion_links)
    )
    latest_link = latest_links_by_assertion.get(source_assertion.id)
    physical_installation_ids = (
        (latest_link.physical_installation_id,)
        if latest_link is not None
        and latest_link.outcome == _SAME_PHYSICAL_INSTALLATION
        and latest_link.physical_installation_id is not None
        else ()
    )

    vendor_names = tuple(
        sorted({claim.relationship.party for claim in claims if claim.relationship is not None})
    )

    evidence_dates = [
        claim.temporal.as_of_date
        for claim in claims
        if claim.temporal is not None and claim.temporal.as_of_date is not None
    ]
    evidence_date = min(evidence_dates) if evidence_dates else None

    return ExistingSignalReconciliationSubject(
        existing_signal_id=source_assertion.signal_id,
        airport_id=source_assertion.airport_id,
        runway_id=source_assertion.runway_id,
        physical_installation_ids=physical_installation_ids,
        source_id=source_assertion.source_id,
        artifact_identity=source_assertion.artifact_identity,
        category=category,
        vendor_names=vendor_names,
        evidence_date=evidence_date,
        reference_year=reference_year,
    )


def _build_candidate(
    signal: Signal,
    supporting_assertions: "list[SourceAssertion]",
    installation_ids_by_assertion_id: "dict[int, set[int]]",
    signal_count_by_source_id: "dict[int, int]",
) -> ReconciliationCandidateSignal:
    supporting_source_ids = tuple(sorted({a.source_id for a in supporting_assertions}))
    supporting_artifact_identities = tuple(
        sorted({a.artifact_identity for a in supporting_assertions if a.artifact_identity})
    )
    physical_installation_ids = tuple(
        sorted(
            {
                physical_installation_id
                for a in supporting_assertions
                for physical_installation_id in installation_ids_by_assertion_id.get(a.id, set())
            }
        )
    )
    return ReconciliationCandidateSignal(
        signal_id=signal.id,
        airport_id=signal.airport_id,
        runway_id=signal.runway_id,
        physical_installation_ids=physical_installation_ids,
        supporting_source_ids=supporting_source_ids,
        supporting_artifact_identities=supporting_artifact_identities,
        category=signal.category,
        confirmed_vendor=signal.confirmed_vendor,
        likely_supplier=signal.likely_supplier,
        # Signal has no field representing "the date of the evidence/event
        # this Signal is about" - `created_at`/`updated_at` describe when
        # this ROW was written to our own database, never the underlying
        # evidence's own date (the same "wall-clock time is never evidence
        # meaning" discipline app.services.evidence_claim_semantics already
        # establishes for claims). `last_verified_at` is the one column
        # whose documented meaning ("as of this date, this Signal's
        # information was confirmed") is close enough to serve this role
        # without conflating storage time with evidence time.
        evidence_date=signal.last_verified_at,
        reference_year=_signal_reference_year(signal),
        source_id=signal.source_id,
        # See R1's own ReconciliationCandidateSignal.is_uniquely_sourced
        # docstring for why this is required, not merely convenient: a
        # single reference document (e.g. a bulk incidents dataset) can
        # legitimately back more than one distinct real-world event, so
        # direct source_id equality alone must never anchor. Computed from
        # `signal_count_by_source_id` - a fresh, DATABASE-WIDE (not
        # airport-scoped) count fetched by `find_reconciliation_candidates()`
        # for exactly this purpose (see its own docstring) - never from this
        # call's own airport-scoped candidate set, which would undercount.
        is_uniquely_sourced=(
            signal.source_id is not None and signal_count_by_source_id.get(signal.source_id, 0) == 1
        ),
    )


def find_reconciliation_candidates(
    session: Session, source_assertion: SourceAssertion,
) -> "tuple[ReconciliationCandidateSignal, ...]":
    """Returns every existing Signal worth presenting to the R1 pure core
    for one governed SourceAssertion - airport-scoped, plus (defensively)
    whatever Signal `source_assertion.signal_id` already names. Ordered by
    `Signal.id` ascending for deterministic tuple equality (S21). Read-only:
    every session call here is a `session.query(...)` SELECT; nothing is
    added, flushed, committed, or mutated.

    Four batched queries, never one query per Signal:
    (1) the candidate Signal rows themselves; (2) every SourceAssertion
    currently linked to any of those Signals (`SourceAssertion.signal_id.
    in_(...)`) in one query, grouped in Python by `signal_id`; (3) EVERY
    `InstallationAssertionLink` for any of those assertions
    (`InstallationAssertionLink.assertion_id.in_(...)`, no outcome filter -
    see `_latest_installation_links_by_assertion_id()`'s own docstring for
    why an outcome pre-filter would hide a later retraction) in one query,
    reduced to the single latest link per `assertion_id`, kept only when
    that latest link's own outcome is `SAME_PHYSICAL_INSTALLATION`; (4) a
    `GROUP BY Signal.source_id` count, restricted to the distinct source_id
    values the candidates themselves carry, but DELIBERATELY NOT restricted
    to this call's own airport-scoped candidate set - the whole point of
    `is_uniquely_sourced` is "is this source_id used by any OTHER Signal
    ANYWHERE," and scoping that count to one airport would silently
    undercount a source_id also used at a different airport, corrupting the
    very uniqueness fact R1's new anchor depends on. `_build_candidate()`
    then assembles each `ReconciliationCandidateSignal` purely from these
    already-fetched, in-memory groupings - no further query per Signal.
    """
    filters = []
    if source_assertion.airport_id is not None:
        filters.append(Signal.airport_id == source_assertion.airport_id)
    if source_assertion.signal_id is not None:
        filters.append(Signal.id == source_assertion.signal_id)
    if not filters:
        return ()

    condition = filters[0] if len(filters) == 1 else or_(*filters)
    signals = session.query(Signal).filter(condition).order_by(Signal.id.asc()).all()
    if not signals:
        return ()

    signal_ids = [signal.id for signal in signals]
    supporting_assertions = (
        session.query(SourceAssertion).filter(SourceAssertion.signal_id.in_(signal_ids)).all()
    )
    assertions_by_signal_id: "dict[int, list[SourceAssertion]]" = {}
    for assertion in supporting_assertions:
        assertions_by_signal_id.setdefault(assertion.signal_id, []).append(assertion)

    installation_ids_by_assertion_id: "dict[int, set[int]]" = {}
    assertion_ids = [assertion.id for assertion in supporting_assertions]
    if assertion_ids:
        # Fetch every link regardless of outcome - not pre-filtered to
        # SAME_PHYSICAL_INSTALLATION - because determining what the CURRENT
        # decision for an assertion actually is requires seeing the whole
        # history for that assertion; a later UNRESOLVED/DIFFERENT_PHYSICAL_
        # INSTALLATION link can supersede (retract) an earlier SAME one, and
        # filtering by outcome up front would hide that a retraction ever
        # happened, silently resurrecting a decision a human corrected away.
        all_links = (
            session.query(InstallationAssertionLink)
            .filter(InstallationAssertionLink.assertion_id.in_(assertion_ids))
            .all()
        )
        latest_by_assertion = _latest_installation_links_by_assertion_id(all_links)
        for assertion_id, latest_link in latest_by_assertion.items():
            if latest_link.outcome == _SAME_PHYSICAL_INSTALLATION and latest_link.physical_installation_id is not None:
                installation_ids_by_assertion_id.setdefault(assertion_id, set()).add(
                    latest_link.physical_installation_id
                )

    candidate_source_ids = {signal.source_id for signal in signals if signal.source_id is not None}
    signal_count_by_source_id: "dict[int, int]" = {}
    if candidate_source_ids:
        counts = (
            session.query(Signal.source_id, func.count(Signal.id))
            .filter(Signal.source_id.in_(candidate_source_ids))
            .group_by(Signal.source_id)
            .all()
        )
        signal_count_by_source_id = dict(counts)

    return tuple(
        _build_candidate(
            signal, assertions_by_signal_id.get(signal.id, []),
            installation_ids_by_assertion_id, signal_count_by_source_id,
        )
        for signal in signals
    )
