"""Update & Report V1 — bounded watch-set planner (Part 1).

    Session
        -> plan_watch_set()
        -> tuple[WatchItem, ...] (a small, explicit, explainable population -
           NOT all 89 airports)
        -> STOP (no persistence - recomputed on demand every run, exactly
           like app.services.human_review_queue's own read-time-derived
           items)

Reuses existing, already-governed read-only services rather than a new
query surface:

  - Signal.published == False  (unpublished Signals - "did anything change
    that would let this become publishable")
  - app.services.human_review_queue.list_staged_evidence_needing_attention_items()
    (the system's own existing opinion of "look at this")

Deliberately excluded from V1 (documented, not a silent gap - see the
mission's own final report "caveats" section): airports reached only via
app.services.discovery_temporal_followup's own trigger-detection, since
that requires an already-fetched CandidateFragment (raw text) this
read-only, network-independent planner does not have access to (Part 14:
"Keep the report engine independent from live network calls"). A future
slice can fold this in once a fragment source is wired without widening
this module's own scope.

Every WatchItem names its own reason and enough context (dates/phase,
where known) to explain itself without a second lookup - never a bare id.
Read-only: every session call here is a SELECT; nothing is added, flushed,
or committed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.signal import Signal
from app.services.human_review_queue import list_staged_evidence_needing_attention_items

__all__ = [
    "WATCH_REASON_UNPUBLISHED_SIGNAL",
    "WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION",
    "WATCH_REASON_NEEDS_MORE_EVIDENCE",
    "WatchItem",
    "plan_watch_set",
]

# Plain string constants, not an Enum: these are explanatory labels for a
# non-persisted report item, never a domain decision vocabulary (compare
# app.services.update_report_vocabulary.MaterialChangeClassification, which
# IS an Enum because it drives report grouping/branching).
WATCH_REASON_UNPUBLISHED_SIGNAL = "UNPUBLISHED_SIGNAL"
WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION = "STAGED_EVIDENCE_NEEDS_ATTENTION"
WATCH_REASON_NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"

_NEEDS_MORE_EVIDENCE_WORKFLOW_STATE = "NEEDS_MORE_EVIDENCE"

# Per the mission's own recon (docs/architecture/rwi-update-and-report-v1-
# system-checkpoint.md §3): assertion_type in {"runway_end", "airport_inventory"}
# is structural/reference data from the FAA NASR/CSV acquisition pipeline -
# "never intended to become Signals," never intelligence about a project.
# list_staged_evidence_needing_attention_items() itself does not filter by
# assertion_type (it answers a different, broader question), so this
# planner narrows to the assertion types that can actually represent
# project-shaped intelligence - matching Part 1's own "prefer Signals/
# evidence that already have a concrete reason to be watched" instruction,
# not a change to the reused service itself.
_INTELLIGENCE_SHAPED_ASSERTION_TYPES = frozenset({"project_construction", "historical"})

DEFAULT_WATCH_SET_LIMIT = 50


@dataclass(frozen=True)
class WatchItem:
    """One bounded watch-set entry - explains itself, never a bare id.

    `signal_id`/`source_assertion_id` are each optional: a staged-evidence
    watch item has a `source_assertion_id` but no `signal_id` yet (no
    Signal exists for it); an unpublished-Signal watch item has a
    `signal_id` but no particular `source_assertion_id` (the whole Signal
    is being watched, not one piece of evidence)."""

    airport_id: int
    signal_id: Optional[int]
    source_assertion_id: Optional[int]
    watch_reason: str
    detail: str


def _unpublished_signal_watch_items(session: Session) -> "tuple[WatchItem, ...]":
    signals = (
        session.query(Signal)
        .filter(Signal.published.is_(False))
        .order_by(Signal.id.asc())
        .all()
    )
    return tuple(
        WatchItem(
            airport_id=signal.airport_id,
            signal_id=signal.id,
            source_assertion_id=None,
            watch_reason=WATCH_REASON_UNPUBLISHED_SIGNAL,
            detail=(
                f"Signal {signal.id} ({signal.title!r}) is unpublished - "
                f"status={signal.status!r}, target_year={signal.target_year!r}"
            ),
        )
        for signal in signals
    )


def _staged_evidence_watch_items(session: Session) -> "tuple[WatchItem, ...]":
    items = list_staged_evidence_needing_attention_items(session)
    watch_items = []
    for item in items:
        if item.airport_id is None:
            # Identity not yet resolved (stage-only lane) - cannot bound a
            # watch item to an airport; out of scope for this planner (see
            # module docstring).
            continue
        if item.assertion_type not in _INTELLIGENCE_SHAPED_ASSERTION_TYPES:
            # Structural/reference data (airport_inventory, runway_end, ...)
            # - never intended to become a Signal; see this module's own
            # _INTELLIGENCE_SHAPED_ASSERTION_TYPES docstring above.
            continue
        watch_reason = (
            WATCH_REASON_NEEDS_MORE_EVIDENCE
            if item.review_workflow_state == _NEEDS_MORE_EVIDENCE_WORKFLOW_STATE
            else WATCH_REASON_STAGED_EVIDENCE_NEEDS_ATTENTION
        )
        watch_items.append(
            WatchItem(
                airport_id=item.airport_id,
                signal_id=item.linked_signal_id,
                source_assertion_id=item.source_assertion_id,
                watch_reason=watch_reason,
                detail=(
                    f"SourceAssertion {item.source_assertion_id} "
                    f"(assertion_type={item.assertion_type!r}) - "
                    f"review_workflow_state={item.review_workflow_state!r}"
                ),
            )
        )
    return tuple(watch_items)


def _sort_key(item: WatchItem) -> "tuple[int, int, int]":
    return (item.airport_id, item.signal_id or 0, item.source_assertion_id or 0)


def plan_watch_set(session: Session, *, limit: "Optional[int]" = DEFAULT_WATCH_SET_LIMIT) -> "tuple[WatchItem, ...]":
    """Deterministic, bounded watch-set: unpublished Signals + staged
    evidence needing attention (with NEEDS_MORE_EVIDENCE named explicitly).
    Deduplicates identical (airport_id, signal_id, source_assertion_id)
    tuples. Ordered by (airport_id, signal_id, source_assertion_id) for
    deterministic output; `limit` bounds the final result (never mid-query),
    matching list_staged_evidence_needing_attention_items()'s own
    "filter first, then limit" discipline."""
    all_items = _unpublished_signal_watch_items(session) + _staged_evidence_watch_items(session)

    seen: "set[tuple[int, Optional[int], Optional[int]]]" = set()
    deduped = []
    for item in all_items:
        key = (item.airport_id, item.signal_id, item.source_assertion_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    ordered = tuple(sorted(deduped, key=_sort_key))
    if limit is not None:
        ordered = ordered[:limit]
    return ordered
