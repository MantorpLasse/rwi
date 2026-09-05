"""Read-only human review queue report
(docs/architecture/human-review-workflow-awareness-slice9d-report.md, Slice
9D; built on Slice 8's own docs/architecture/human-review-queue-slice8-report.md).

    python -m scripts.list_human_review_queue --database data/runway_safe.db --limit 20
    python -m scripts.list_human_review_queue --state all
    python -m scripts.list_human_review_queue --state resolved
    python -m scripts.list_human_review_queue --state staged
    python -m scripts.list_human_review_queue --state staged-active
    python -m scripts.list_human_review_queue --state reconciliation

Default (`--state active`): prints only SourceAssertion rows that still need
a human DECISION right now - promotion_policy_decision ==
"HUMAN_REVIEW_REQUIRED" AND derived review_workflow_state is ACTIVE_REVIEW
or NEEDS_MORE_EVIDENCE (see app.services.human_review_queue's own docstring
for the full state vocabulary and the Slice 9D defect this fixes: a row a
human already resolved via MARK_DUPLICATE/REJECT_SIGNAL/a linked
APPROVE_SIGNAL no longer appears here forever, the way it did under Slice
8's own promotion-policy-only filter). `--state all` shows every
HUMAN_REVIEW_REQUIRED row regardless of state, annotated with its derived
state, for audit; `--state resolved` shows only the resolved subset
(RESOLVED_REJECTED, RESOLVED_DUPLICATE, RESOLVED_SIGNAL_CREATED); `--state
reconciliation` (R4D, docs/architecture/existing-signal-reconciliation-r4d-
review-queue-report.md) shows governed rows where a FRESH R1/R2
reconciliation recomputation currently requires human attention - either
blocked (no matching CONFIRM_DISTINCT_SIGNAL confirmation exists, or one
exists but is stale) or DISTINCT_CONFIRMED_PENDING_SIGNAL (a confirmation
exists, currently matches, but no Signal has been created yet). Never
AUTO_ELIGIBLE, never DO_NOT_PROMOTE, never NULL/unevaluated rows, in any
mode. Performs ZERO writes: the database connection is opened in SQLite's
own read-only URI mode (`mode=ro`), so even a coding mistake that tried to
write would be refused at the driver level, not merely by convention. Never
creates a backup, never runs or imports an `upgrade()`/`downgrade()`
function from any migration script, never fetches network, never touches
`Signal` (reads a Signal's own row only insofar as SourceAssertion.signal_id
is displayed - never creates, updates, or deletes one).

SCHEMA GATE: before running any ORM query, this script inspects the target
database's raw schema (via the existing migration scripts' own read-only
`inspect()` functions, reused rather than reimplemented) and refuses with
`REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED` if the Slice 4
(`intelligence_review_*`), Slice 7 (`promotion_policy_*`), or R4B
(`reviewer_actions.reconciliation_fingerprint`) columns are missing - it
does not attempt to migrate, and it does not silently query a schema that
would raise an ORM-level `OperationalError` instead of a clear, typed
refusal.

R4D CORRECTION (docs/architecture/existing-signal-reconciliation-r4d-review-
queue-report.md): the R4B column check above was added by this slice after
fresh inspection proved the pre-existing claim below was already false for
EVERY state, not just a new reconciliation-aware one. `app.models.reviewer_action.
ReviewerAction` has declared `reconciliation_fingerprint` as a mapped column
since R4B - any ORM query against that table (which `list_review_workflow_items()`,
and therefore every existing `--state active|all|resolved` call, already
performs unconditionally) now emits a `SELECT` naming that column, which
raises `sqlite3.OperationalError: no such column: reviewer_actions.
reconciliation_fingerprint` against any database that has not had R4B's own
migration applied - including, as of this slice's own fresh, read-only
inspection, the real `data/runway_safe.db` itself. The paragraph below
described a narrower, different failure mode (no ReviewerAction ROWS yet
recorded for one assertion, which does return `None` gracefully) and did not
in fact cover the schema-missing-column case; this is a genuine, independently
-proven defect this slice fixes, not a design choice being revisited.

(Pre-existing text, now only accurate for the narrower case it actually
describes:) Slice 9D deliberately does NOT add a further gate for "no
ReviewerAction rows exist yet for this SourceAssertion" - that case is read
defensively (`get_latest_reviewer_action()` naturally returns `None`) and
needs no schema check at all, since it is a data condition, not a schema
one.

`run_review_queue()` is the one function that does the work (schema check,
then query) and returns a single, importable report - `main()` and the
tests both consume it, so there is exactly one code path, not two.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.human_review_claim_enrichment import enrich_claims
from app.services.human_review_queue import (
    GOVERNANCE_STAGE_STAGED_UNREVIEWED,
    STAGED_ATTENTION_WORKFLOW_STATE_VALUES,
    HumanReviewItem,
    ReviewWorkflowState,
    StagedEvidenceItem,
    list_human_review_items,
    list_review_workflow_items,
    list_staged_evidence_items,
    list_staged_evidence_needing_attention_items,
)
from app.services.human_review_reconciliation import (
    ReconciliationReviewItem,
    ReconciliationReviewState,
    list_reconciliation_review_items,
)
from scripts.migrate_intelligence_review_persistence_slice4 import inspect as inspect_intelligence_review_schema
from scripts.migrate_promotion_policy_persistence_slice7 import inspect as inspect_promotion_policy_schema
from scripts.migrate_reconciliation_confirmation_slice_r4b import (
    inspect as inspect_reconciliation_confirmation_schema,
)

DEFAULT_DATABASE = Path("data/runway_safe.db")
DEFAULT_LIMIT = 20
DEFAULT_STATE_FILTER = "active"
SCHEMA_MIGRATION_REQUIRED_BLOCKER = "REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED"

_RESOLVED_STATES = frozenset(
    {
        ReviewWorkflowState.RESOLVED_REJECTED.value,
        ReviewWorkflowState.RESOLVED_DUPLICATE.value,
        ReviewWorkflowState.RESOLVED_SIGNAL_CREATED.value,
    }
)


@dataclass(frozen=True)
class ReviewQueueConfig:
    database: Path
    limit: "int | None" = DEFAULT_LIMIT
    state: str = DEFAULT_STATE_FILTER


@dataclass(frozen=True)
class ReviewQueueReport:
    """The one result shape both `main()` and the test suite consume - no
    parallel/duplicated code path between CLI output and importable,
    testable behavior.

    `items` holds `HumanReviewItem` for state in {"active", "all",
    "resolved"}, `ReconciliationReviewItem` for state == "reconciliation",
    and `StagedEvidenceItem` (RWI HQ "Commander Review Queue - Staged
    Evidence Lane") for state == "staged" - a plain, untyped tuple rather
    than a generic/union wrapper, matching this module's own existing "no
    parallel code path" preference; the item shapes are only ever handled
    together in `render_report()`/`render_item_report()` below, which
    dispatch on `isinstance`."""

    database: str
    schema_readiness: dict
    blockers: "tuple[str, ...]" = ()
    items: "tuple[HumanReviewItem, ...] | tuple[ReconciliationReviewItem, ...] | tuple[StagedEvidenceItem, ...]" = ()


def build_readonly_engine(database: Path):
    """Builds a SQLAlchemy engine bound to EXACTLY the resolved `database`
    path, opened in SQLite's own read-only URI mode - the only
    database-binding function in this module. Never
    app.database.SessionLocal/engine, never a process-global; every
    session used in this script is built from this engine, explicitly."""
    resolved = database.resolve()
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def check_schema_readiness(database: Path) -> dict:
    """Read-only, via `sqlite3.connect(..., mode=ro)` inside each reused
    `inspect()` (never this script's own ORM engine, and never a write
    connection) - reuses the migration scripts' own already-proven,
    already-tested schema inspection rather than reimplementing it.

    R4D CORRECTION: now also requires `reviewer_actions.reconciliation_fingerprint`
    (R4B) to exist, for every state - see the module docstring's own "SCHEMA
    GATE" section for why this was a genuine, proven gap, not a new
    requirement invented for the new `reconciliation` state alone."""
    intelligence = inspect_intelligence_review_schema(database)
    promotion = inspect_promotion_policy_schema(database)
    reconciliation_confirmation = inspect_reconciliation_confirmation_schema(database)
    ready = (
        intelligence["intelligence_review_decision_column_exists"]
        and intelligence["intelligence_review_reason_column_exists"]
        and promotion["promotion_policy_decision_column_exists"]
        and promotion["promotion_policy_reason_column_exists"]
        and reconciliation_confirmation["reconciliation_fingerprint_column_exists"]
    )
    return {
        "intelligence_review_decision_column_exists": intelligence["intelligence_review_decision_column_exists"],
        "intelligence_review_reason_column_exists": intelligence["intelligence_review_reason_column_exists"],
        "promotion_policy_decision_column_exists": promotion["promotion_policy_decision_column_exists"],
        "promotion_policy_reason_column_exists": promotion["promotion_policy_reason_column_exists"],
        "reconciliation_fingerprint_column_exists": reconciliation_confirmation["reconciliation_fingerprint_column_exists"],
        "source_assertions_count": promotion["source_assertions_count"],
        "ready": ready,
    }


_VALID_STATE_FILTERS = ("active", "all", "resolved", "reconciliation", "staged", "staged-active")

# The reconciliation-aware states that actually mean "a human has something
# new to look at" - CLEAR_TO_CREATE items list_reconciliation_review_items()
# also returns (reconciliation_review_state is None on those) are not shown
# by this state's default view, matching "avoid turning the queue into a
# global Signal dedup scanner" (R4D mission) - a caller who wants the
# complete reconciliation picture, including CLEAR items, can call
# app.services.human_review_reconciliation.list_reconciliation_review_items()
# directly.
_RECONCILIATION_ATTENTION_STATES = frozenset(
    {ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED.value, ReconciliationReviewState.DISTINCT_CONFIRMED_PENDING_SIGNAL.value}
)


def run_review_queue(config: ReviewQueueConfig) -> ReviewQueueReport:
    """Never writes. Refuses (via `blockers`, never an exception) rather
    than querying an unready schema. The ONLY function that does the actual
    work - `main()` calls this and renders it; tests call this directly.

    `config.state` selects which slice of the workflow picture to show:
    "active" (default) - list_human_review_items(), items still needing a
    human decision now; "all" - list_review_workflow_items() unfiltered,
    every HUMAN_REVIEW_REQUIRED row annotated with its derived state, SQL
    `LIMIT` applied directly since no state sub-filtering happens afterward;
    "resolved" - list_review_workflow_items() filtered to just the
    RESOLVED_* states, for confirming an item (e.g. real MSP #222) was
    correctly resolved and why; "reconciliation" (R4D,
    app.services.human_review_reconciliation.list_reconciliation_review_items())
    - governed rows where fresh R1/R2 reconciliation currently requires
    attention (RECONCILIATION_REVIEW_REQUIRED, including stale confirmations,
    or DISTINCT_CONFIRMED_PENDING_SIGNAL) - CLEAR_TO_CREATE items that
    function also evaluates are not shown here, since reconciliation has
    nothing new to say about them (matches the R4D mission's own "avoid
    turning the queue into a global Signal dedup scanner" instruction);
    "staged" (RWI HQ "Commander Review Queue - Staged Evidence Lane") -
    app.services.human_review_queue.list_staged_evidence_items() - preserved
    candidate evidence (e.g. stage-only or known-airport-staged funding/
    discovery SourceAssertions) that has never entered the governed
    promotion-review workflow at all. A COMPLETELY SEPARATE population from
    every state above - never merged with, and never widening,
    the promotion_policy_decision == HUMAN_REVIEW_REQUIRED predicate the
    other states all share. Answers Question 1 ("what evidence is still
    structurally staged") - EVERY row remains visible here no matter its
    ReviewerAction history (RWI HQ "Staged Evidence Attention States");
    "staged-active" - app.services.human_review_queue.
    list_staged_evidence_needing_attention_items() - answers Question 2
    ("what staged evidence needs Commander attention now"), the SAME
    population filtered to ACTIVE_REVIEW/NEEDS_MORE_EVIDENCE/
    APPROVED_PENDING_SIGNAL only. A row a human already resolved
    (RESOLVED_REJECTED/RESOLVED_DUPLICATE) drops out of "staged-active" but
    is never hidden from "staged" - reviewing evidence once is never a
    reason to make it harder to find again.

    REVIEW-CHECKPOINT FIX: "resolved" must NOT pass `config.limit` into the
    inner list_review_workflow_items() call - that function's own `limit`
    bounds the raw HUMAN_REVIEW_REQUIRED SQL query before any state
    filtering, so if the newest rows (fetched first) all happen to be
    non-resolved, the SQL-level limit could consume them and leave zero rows
    for the resolved-state filter to find, even when resolved items exist
    further down (the identical failure shape
    app.services.human_review_queue.list_human_review_items() itself
    already guards against for "active" - this script's own "resolved" path
    had not received the same fix until now). Fetches everything, filters to
    resolved states, then applies `limit` in Python instead. "reconciliation"
    (R4D) follows the identical discipline: list_reconciliation_review_items()
    is called with no limit, filtered to the attention-needing states, THEN
    limited in Python - never the reverse."""
    database_str = str(config.database.resolve())
    if config.state not in _VALID_STATE_FILTERS:
        raise ValueError(f"state must be one of {_VALID_STATE_FILTERS!r}, got {config.state!r}")
    schema = check_schema_readiness(config.database)
    if not schema["ready"]:
        return ReviewQueueReport(
            database=database_str, schema_readiness=schema, blockers=(SCHEMA_MIGRATION_REQUIRED_BLOCKER,),
        )

    engine = build_readonly_engine(config.database)
    try:
        with Session(engine) as session:
            if config.state == "active":
                items = list_human_review_items(session, limit=config.limit)
            elif config.state == "all":
                items = list_review_workflow_items(session, limit=config.limit)
            elif config.state == "resolved":
                all_items = list_review_workflow_items(session)
                resolved = tuple(item for item in all_items if item.review_workflow_state in _RESOLVED_STATES)
                items = resolved[: config.limit] if config.limit is not None else resolved
            elif config.state == "reconciliation":
                all_reconciliation_items = list_reconciliation_review_items(session)
                attention = tuple(
                    entry for entry in all_reconciliation_items
                    if entry.reconciliation_review_state in _RECONCILIATION_ATTENTION_STATES
                )
                items = attention[: config.limit] if config.limit is not None else attention
            elif config.state == "staged":
                # A SEPARATE population from every state above: preserved
                # candidate evidence that was never routed through IdentityGuard/
                # intelligence review/promotion policy at all - never the
                # governed-review predicate widened, never merged with it. See
                # app.services.human_review_queue's own module section for the
                # exact predicate and why it is intentionally narrow. The
                # FULL structural inventory - Question 1, "what evidence is
                # still structurally staged" - every row a human has ever
                # acted on remains visible here too (RWI HQ "Staged Evidence
                # Attention States"), enriched with its own derived
                # review_workflow_state; never filtered by that state.
                items = list_staged_evidence_items(session, limit=config.limit)
            else:  # "staged-active" (RWI HQ "Staged Evidence Attention States")
                # Question 2: "what staged evidence needs Commander attention
                # NOW" - the same structural population as "staged" above,
                # filtered to ACTIVE_REVIEW/NEEDS_MORE_EVIDENCE/
                # APPROVED_PENDING_SIGNAL only. RESOLVED_REJECTED/
                # RESOLVED_DUPLICATE rows are excluded from THIS view only -
                # they remain fully visible under "staged", never hidden.
                items = list_staged_evidence_needing_attention_items(session, limit=config.limit)
            session.rollback()  # defensive - this session never adds/flushes anything
    finally:
        engine.dispose()

    return ReviewQueueReport(database=database_str, schema_readiness=schema, items=items)


# ---------------------------------------------------------------------------
# Human-readable report rendering - text formatting only, no new data.
# ---------------------------------------------------------------------------


def _format_financial(claim) -> "str | None":
    if claim.financial is None:
        return None
    return f"{claim.financial.amount} {claim.financial.currency} — {claim.financial.semantic_role}"


def _format_relationship(claim) -> "str | None":
    if claim.relationship is None:
        return None
    scope = f" ({claim.relationship.scope})" if claim.relationship.scope else ""
    return f"{claim.relationship.party} — {claim.relationship.role}{scope}"


def _format_temporal(claim) -> "str | None":
    if claim.temporal is None:
        return None
    as_of = f", as of {claim.temporal.as_of_date}" if claim.temporal.as_of_date else ""
    detail = f" ({claim.temporal.detail})" if claim.temporal.detail else ""
    return f"{claim.temporal.qualifier.value}{as_of}{detail}"


def _render_reconciliation_section(entry: ReconciliationReviewItem) -> "list[str]":
    """R4D (mission Section 20's own example format):

        Reviewer workflow: <state>
        Reconciliation: POSSIBLE_EXISTING_SIGNAL_MATCH
        Blocking Signals: 10, 20
        Anchor reasons:
          Signal 10: ...
          Signal 20: ...
        Current fingerprint: <64 hex>

    plus, when a CONFIRM_DISTINCT_SIGNAL confirmation exists:
    "Confirmation: CURRENT" or "Confirmation: STALE - RE-REVIEW REQUIRED".
    Text formatting only - every value already exists on `entry`, nothing
    computed here."""
    lines: "list[str]" = []
    lines.append("")
    lines.append("Reconciliation (R4D, derived - fresh R1/R2 recomputation, never persisted)")
    lines.append(f"  Reconciliation: {entry.reconciliation_outcome}")
    if entry.reconciliation_review_state is not None:
        lines.append(f"  Reconciliation review state: {entry.reconciliation_review_state}")
    if entry.reconciliation_candidate_signal_ids:
        lines.append(f"  Blocking Signals: {', '.join(str(i) for i in entry.reconciliation_candidate_signal_ids)}")
        lines.append("  Anchor reasons:")
        for reason in entry.reconciliation_anchor_reasons:
            lines.append(f"    {reason}")
    if entry.reconciliation_fingerprint:
        lines.append(f"  Current fingerprint: {entry.reconciliation_fingerprint}")
    if entry.stored_reconciliation_fingerprint is not None:
        lines.append(f"  Stored fingerprint: {entry.stored_reconciliation_fingerprint}")
        if entry.reconciliation_review_state == ReconciliationReviewState.DISTINCT_CONFIRMED_PENDING_SIGNAL.value:
            lines.append("  Confirmation: CURRENT")
        elif entry.reconciliation_outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH":
            lines.append("  Confirmation: STALE - RE-REVIEW REQUIRED")
    if entry.reconciliation_warnings:
        lines.append("")
        lines.append("  !! RECONCILIATION WARNINGS !!")
        for warning in entry.reconciliation_warnings:
            lines.append(f"    {warning}")
    return lines


_NOT_EVALUATED = "not evaluated (this evidence class is not routed through IdentityGuard/intelligence review/promotion policy)"

# RWI HQ "Staged Evidence Attention States": one human-readable label and
# one truthful next-action sentence per ReviewWorkflowState value this
# staged population can (or, for completeness, theoretically could) show -
# never a new vocabulary, only presentation text keyed on the EXISTING,
# unmodified ReviewWorkflowState members. `{funding_hint}` is filled in by
# _staged_next_action() below only for ACTIVE_REVIEW, since that is the
# one state whose truthful next step genuinely depends on whether this row
# is funding-provenance evidence at all.
_STAGED_LABEL_BY_STATE = {
    "ACTIVE_REVIEW": "STAGED — UNREVIEWED",
    "NEEDS_MORE_EVIDENCE": "STAGED — NEEDS MORE EVIDENCE",
    "APPROVED_PENDING_SIGNAL": "STAGED — AWAITING SIGNAL CREATION",
    "RESOLVED_REJECTED": "STAGED — REVIEWED, NO FURTHER ACTION",
    "RESOLVED_DUPLICATE": "STAGED — REVIEWED, NO FURTHER ACTION",
}
_STAGED_LABEL_FALLBACK = "STAGED — OTHER"

_STAGED_NEXT_ACTION_FUNDING_ELIGIBLE = (
    "Run scripts/review_staged_funding_evidence.py to record a Commander review action "
    "(APPROVE_SIGNAL, MARK_DUPLICATE, NEEDS_MORE_EVIDENCE, or REJECT_SIGNAL)."
)
_STAGED_NEXT_ACTION_NOT_FUNDING_ELIGIBLE = (
    "Not eligible for the lightweight funding review path (this Source's own external_id "
    "does not carry a recognized funding provenance namespace - e.g. Research Loop / "
    "Discovery evidence). No unified staged-evidence governance CLI exists yet for this "
    "evidence class; continue appropriate Research/Discovery handling (e.g. "
    "scripts/research_airport_clue.py, scripts/fetch_discovered_url.py)."
)
_STAGED_NEXT_ACTION_BY_STATE = {
    "NEEDS_MORE_EVIDENCE": (
        "Gather additional preserved evidence (e.g. scripts/research_airport_clue.py, "
        "scripts/fetch_discovered_url.py / scripts/fetch_research_candidate.py), then "
        "re-review via scripts/review_staged_funding_evidence.py. Research does not "
        "automatically advance governance."
    ),
    "APPROVED_PENDING_SIGNAL": (
        "Signal creation remains owed for the latest APPROVE_SIGNAL ReviewerAction - "
        "re-run scripts/review_staged_funding_evidence.py with --action APPROVE_SIGNAL "
        "to complete it. Commander intervention required."
    ),
    "RESOLVED_REJECTED": "No further Commander action expected. Evidence remains preserved for audit.",
    "RESOLVED_DUPLICATE": (
        "No further Commander action expected. Evidence remains preserved and linked by "
        "review history (see the latest ReviewerAction's duplicate_of_signal_id)."
    ),
}
_STAGED_NEXT_ACTION_FALLBACK = "No standard next-action guidance defined for this derived state - review manually."


def _staged_next_action(item: StagedEvidenceItem) -> str:
    """Pure presentation lookup, keyed on the item's own already-derived
    `review_workflow_state`/`funding_provenance` fields - never a second
    eligibility authority (that remains
    app.services.known_airport_funding_lightweight_path_guard.check_lightweight_funding_path_eligibility()
    alone). ACTIVE_REVIEW is the one state whose truthful next step
    depends on funding_provenance; every other state's guidance is fixed."""
    if item.review_workflow_state == "ACTIVE_REVIEW":
        return _STAGED_NEXT_ACTION_FUNDING_ELIGIBLE if item.funding_provenance else _STAGED_NEXT_ACTION_NOT_FUNDING_ELIGIBLE
    return _STAGED_NEXT_ACTION_BY_STATE.get(item.review_workflow_state, _STAGED_NEXT_ACTION_FALLBACK)


def _render_staged_item_report(item: StagedEvidenceItem) -> str:
    """Text formatting only - every value already exists on `item`
    (computed by app.services.human_review_queue itself), except the
    label/next-action TEXT, which is a pure, deterministic lookup on the
    item's own already-derived fields (see _staged_next_action() above) -
    nothing about eligibility or workflow state is decided here. Does NOT
    reuse render_item_report()'s own HumanReviewItem-shaped layout (no
    Identity/Intelligence/Promotion review sections implying heavy-pipeline
    evaluation occurred) - see module docstring "staged" state description
    for why this is a structurally separate population, not merely a
    filtered view of the same one."""
    label = _STAGED_LABEL_BY_STATE.get(item.review_workflow_state, _STAGED_LABEL_FALLBACK)
    lines: "list[str]" = []
    lines.append("=" * 78)
    lines.append(f"SourceAssertion #{item.source_assertion_id}  [{label}]")
    lines.append("=" * 78)

    lines.append("")
    lines.append("Airport")
    lines.append(f"  id={item.airport_id}  code={item.airport_code}  name={item.airport_name}")

    lines.append("")
    lines.append("Source")
    lines.append(f"  id={item.source_id}  title={item.source_title!r}")
    lines.append(f"  publisher={item.source_publisher}  url={item.source_url}")
    lines.append(f"  document_reference={item.source_document_reference}")
    lines.append(
        f"  reliability_level (existing coarse field, NOT a PromotionPolicy "
        f"SourceAuthorityTier)={item.source_reliability_level_raw!r}"
    )
    lines.append(f"  funding_provenance={item.funding_provenance or '(not a recognized funding namespace)'}")

    lines.append("")
    lines.append("Evidence")
    lines.append(f"  assertion_type={item.assertion_type}")
    lines.append(f"  evidence_quality={item.evidence_quality}")
    lines.append(f"  review_state={item.review_state}")
    lines.append(f"  artifact_identity={item.artifact_identity}")
    lines.append(f"  source_locator={item.source_locator}")
    lines.append(f"  raw_fragment_hash={item.raw_fragment_hash}")
    lines.append(f"  parser_identifier={item.parser_identifier}")
    lines.append("  raw_relevant_text:")
    for text_line in (item.raw_relevant_text or "(none preserved)").splitlines():
        lines.append(f"    {text_line}")

    lines.append("")
    lines.append("Governance review")
    lines.append(f"  governance_stage={item.governance_stage}")
    lines.append(f"  identity_guard_decision={item.identity_guard_decision if item.identity_guard_decision is not None else _NOT_EVALUATED}")
    lines.append(f"  intelligence_review_decision={item.intelligence_review_decision if item.intelligence_review_decision is not None else _NOT_EVALUATED}")
    lines.append(f"  promotion_policy_decision={item.promotion_policy_decision if item.promotion_policy_decision is not None else _NOT_EVALUATED}")
    lines.append(f"  linked_signal_id={item.linked_signal_id}")

    lines.append("")
    lines.append("Commander attention (RWI HQ \"Staged Evidence Attention States\", derived read-time - never persisted)")
    lines.append(f"  review_workflow_state={item.review_workflow_state}")
    if item.latest_reviewer_action_id is not None:
        lines.append(
            f"  latest_reviewer_action=#{item.latest_reviewer_action_id} {item.latest_reviewer_action} "
            f"(reviewer={item.latest_reviewer_action_reviewer!r}, created_at={item.latest_reviewer_action_created_at})"
        )
        lines.append(f"    reason: {item.latest_reviewer_action_reason}")
    else:
        lines.append("  latest_reviewer_action=(none - never reviewed)")

    lines.append("")
    lines.append("Next legitimate action")
    lines.append(f"  {_staged_next_action(item)}")

    return "\n".join(lines)


def render_item_report(item: "HumanReviewItem | ReconciliationReviewItem | StagedEvidenceItem") -> str:
    if isinstance(item, StagedEvidenceItem):
        return _render_staged_item_report(item)

    reconciliation_entry: "ReconciliationReviewItem | None" = None
    if isinstance(item, ReconciliationReviewItem):
        reconciliation_entry = item
        item = item.item

    lines: "list[str]" = []
    lines.append("=" * 78)
    lines.append(f"SourceAssertion #{item.source_assertion_id}")
    lines.append("=" * 78)

    lines.append("")
    lines.append("Airport")
    lines.append(f"  id={item.airport_id}  code={item.airport_code}  name={item.airport_name}")

    lines.append("")
    lines.append("Source")
    lines.append(f"  id={item.source_id}  title={item.source_title!r}")
    lines.append(f"  publisher={item.source_publisher}  url={item.source_url}")
    lines.append(f"  document_reference={item.source_document_reference}")
    lines.append(
        f"  reliability_level (existing coarse field, NOT a PromotionPolicy "
        f"SourceAuthorityTier)={item.source_reliability_level_raw!r}"
    )

    lines.append("")
    lines.append("Evidence")
    lines.append(f"  artifact_identity={item.artifact_identity}")
    lines.append(f"  source_locator={item.source_locator}")
    lines.append(f"  raw_fragment_hash={item.raw_fragment_hash}")
    lines.append(f"  parser_identifier={item.parser_identifier}")
    lines.append("  raw_relevant_text:")
    for text_line in (item.raw_relevant_text or "(none preserved)").splitlines():
        lines.append(f"    {text_line}")

    lines.append("")
    lines.append("Identity review")
    lines.append(f"  identity_guard_decision={item.identity_guard_decision}")
    lines.append(f"  identity_guard_reason={item.identity_guard_reason}")

    lines.append("")
    lines.append("Intelligence review")
    lines.append(f"  intelligence_review_decision={item.intelligence_review_decision}")
    lines.append(f"  intelligence_review_reason={item.intelligence_review_reason}")

    lines.append("")
    lines.append("Promotion policy")
    lines.append(f"  promotion_policy_decision={item.promotion_policy_decision}")
    lines.append(f"  promotion_policy_reason={item.promotion_policy_reason}")

    lines.append("")
    lines.append("Reviewer workflow (Slice 9D, derived - never persisted)")
    lines.append(f"  review_workflow_state={item.review_workflow_state}")
    lines.append(f"  latest_reviewer_action={item.latest_reviewer_action}")
    lines.append(f"  linked_signal_id={item.linked_signal_id}")

    if item.invariant_warnings:
        lines.append("")
        lines.append("!! INVARIANT WARNINGS !!")
        for warning in item.invariant_warnings:
            lines.append(f"  {warning}")

    lines.append("")
    lines.append("Claims / constraints")
    claims = enrich_claims(item)
    if claims is None:
        lines.append(
            f"  No source-specific claim extraction available for parser_identifier="
            f"{item.parser_identifier!r} - review the raw evidence text above directly."
        )
    elif not claims:
        lines.append("  Claim extraction ran but produced no claims for this text.")
    else:
        not_established: "list[str]" = []
        for claim in claims:
            lines.append(f"  - [{claim.category.value}] {claim.subject}: {claim.statement}")
            financial_line = _format_financial(claim)
            if financial_line:
                lines.append(f"      {financial_line}")
                if claim.financial.not_established:
                    lines.append(f"      NOT ESTABLISHED: {', '.join(claim.financial.not_established)}")
                    not_established.extend(claim.financial.not_established)
            relationship_line = _format_relationship(claim)
            if relationship_line:
                lines.append(f"      {relationship_line}")
            temporal_line = _format_temporal(claim)
            if temporal_line:
                lines.append(f"      {temporal_line}")

        lines.append("")
        lines.append("What is NOT established")
        if not_established:
            for label in sorted(set(not_established)):
                lines.append(f"  - {label}")
        else:
            lines.append("  (no explicit not_established constraints on the re-derived claims)")

    if reconciliation_entry is not None:
        lines.extend(_render_reconciliation_section(reconciliation_entry))

    return "\n".join(lines)


_EMPTY_MESSAGE_BY_STATE = {
    "active": "Human review queue is empty. Nothing currently requires a human decision.",
    "all": "No governed evidence is currently HUMAN_REVIEW_REQUIRED.",
    "resolved": "No governed evidence has been resolved (rejected/duplicate/signal-created) yet.",
    "reconciliation": "No governed evidence currently requires reconciliation review or re-review.",
    # Deliberately NOT the same wording as "active" - this lane's rows never
    # implied a governed "human decision" was ever computed in the first
    # place, so an empty result here must not read as if the same kind of
    # decision-readiness was checked and found clear.
    "staged": "No staged evidence is currently awaiting Commander attention.",
    # Distinct from "staged"'s own empty message on purpose - this state can
    # be empty while "staged" itself is not (every remaining row already
    # resolved), and that is a genuinely different, better outcome than an
    # empty structural inventory would be.
    "staged-active": "No staged evidence currently needs Commander attention.",
}

# Printed once, above the item list, only for states where the population's
# governance meaning could otherwise be misread. Empty string = no banner
# (every pre-existing state's own output is unchanged).
_STATE_BANNER = {
    "staged": "STAGED EVIDENCE — NOT YET GOVERNANCE-REVIEWED",
    "staged-active": "STAGED EVIDENCE — COMMANDER ATTENTION VIEW",
}


def _staged_summary_counts(items: "tuple[StagedEvidenceItem, ...]") -> "dict[str, int]":
    """SELECT-only, derived-only summary - no persisted counter anywhere.
    Grouped by the exact same review_workflow_state values render_report()
    itself displays per-item, so the two can never disagree."""
    counts = {"total": len(items), "unreviewed": 0, "needs_more_evidence": 0, "awaiting_signal": 0, "reviewed_no_further_action": 0, "other": 0}
    for item in items:
        state = item.review_workflow_state
        if state == "ACTIVE_REVIEW":
            counts["unreviewed"] += 1
        elif state == "NEEDS_MORE_EVIDENCE":
            counts["needs_more_evidence"] += 1
        elif state == "APPROVED_PENDING_SIGNAL":
            counts["awaiting_signal"] += 1
        elif state in ("RESOLVED_REJECTED", "RESOLVED_DUPLICATE"):
            counts["reviewed_no_further_action"] += 1
        else:
            counts["other"] += 1
    return counts


def render_report(report: ReviewQueueReport, *, state: str = DEFAULT_STATE_FILTER) -> str:
    if report.blockers:
        return (
            f"Database: {report.database}\n"
            f"BLOCKED: {', '.join(report.blockers)}\n"
            f"schema_readiness: {report.schema_readiness}\n"
        )
    if not report.items:
        empty_message = _EMPTY_MESSAGE_BY_STATE.get(state, _EMPTY_MESSAGE_BY_STATE["active"])
        return f"Database: {report.database}\n{empty_message}\n"

    banner = _STATE_BANNER.get(state, "")
    header = f"Database: {report.database}\n"
    if banner:
        header += f"{banner}\n"
    header += f"{len(report.items)} item(s) in the '{state}' human review view:\n"
    if state == "staged":
        counts = _staged_summary_counts(report.items)
        header += (
            f"  {counts['total']} staged total - {counts['unreviewed']} unreviewed, "
            f"{counts['needs_more_evidence']} needs more evidence, {counts['awaiting_signal']} awaiting Signal, "
            f"{counts['reviewed_no_further_action']} reviewed/no further action"
            + (f", {counts['other']} other" if counts["other"] else "")
            + "\n"
        )
    parts = [header]
    for item in report.items:
        parts.append(render_item_report(item))
        parts.append("")
    return "\n".join(parts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--state", choices=_VALID_STATE_FILTERS, default=DEFAULT_STATE_FILTER,
        help="active (default): needs a human decision now. all: every HUMAN_REVIEW_REQUIRED row, any state. "
        "resolved: only rejected/duplicate/signal-created items. reconciliation (R4D): governed rows where "
        "fresh reconciliation currently requires attention (blocked or a stale distinct confirmation). "
        "staged: ALL preserved candidate evidence never routed through governance review at all - a separate "
        "population, never merged with the governed states above; every row remains visible here regardless of "
        "ReviewerAction history, enriched with its own derived review_workflow_state. staged-active: the same "
        "staged population, filtered to rows still needing Commander attention now.",
    )
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    config = ReviewQueueConfig(database=args.database, limit=args.limit, state=args.state)
    report = run_review_queue(config)
    print(render_report(report, state=args.state))
    return 1 if report.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
