"""Update & Report V1 — internal report assembly and rendering (Parts 9-11).

    tuple[WatchItem, ...] (Part 1) + tuple[ChangeCandidateResult, ...]
    (Part 4, already classified by app.services.update_report_change_detection)
        -> generate_update_report()
        -> UpdateReport (five deterministically-ordered groups)
        -> render_report_text()
        -> str (internal, Markdown/text - never published to the public
           static site; see app/static_export/build.py, which this module
           is never imported by)
        -> STOP

NO-CHANGE SEMANTICS (Part 11): this module performs zero writes - it never
imports Session in a way that lets it query anything beyond what its own
`generate_update_report()` signature is given, and touches no ORM object at
all. "Checked - no material change" is representable purely by a
NO_MATERIAL_CHANGE-classified ChangeCandidateResult with no Signal created,
no Signal amended, no ReviewerAction created, no amendment audit row
created, and no SourceAssertion state altered - see
app.services.update_report_change_detection's own module docstring for why
detection itself never writes either.

REPORT PRIORITY (Part 9) groups sections, but priority itself is computed
per-candidate in app.services.update_report_change_detection - this module
only groups and orders what it is given, never re-derives a classification
or priority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.services.update_report_change_detection import ChangeCandidateResult
from app.services.update_report_vocabulary import MaterialChangeClassification
from app.services.update_report_watchset import WatchItem

__all__ = [
    "WatchNextEntry",
    "UpdateReport",
    "generate_update_report",
    "render_report_text",
]

_MATERIAL_CLASSIFICATIONS = frozenset(
    {
        MaterialChangeClassification.FIELD_CHANGE_CANDIDATE,
        MaterialChangeClassification.NEW_SIGNAL_CANDIDATE,
        MaterialChangeClassification.CONTRADICTION_OR_CORRECTION,
    }
)
_NO_CHANGE_CLASSIFICATIONS = frozenset(
    {MaterialChangeClassification.NO_MATERIAL_CHANGE, MaterialChangeClassification.DUPLICATE}
)

_DEFAULT_WATCH_NEXT_NOTE = "no new evidence gathered this run"


@dataclass(frozen=True)
class WatchNextEntry:
    watch_item: WatchItem
    note: str


@dataclass(frozen=True)
class UpdateReport:
    generated_at: datetime
    material_changes: "tuple[ChangeCandidateResult, ...]"
    needs_human_review: "tuple[ChangeCandidateResult, ...]"
    corroboration_only: "tuple[ChangeCandidateResult, ...]"
    no_material_change: "tuple[ChangeCandidateResult, ...]"
    watch_next: "tuple[WatchNextEntry, ...]"


def _candidate_sort_key(candidate: ChangeCandidateResult) -> "tuple[int, int, int]":
    return (
        candidate.airport_id or 0,
        candidate.existing_signal_id or 0,
        candidate.source_assertion_id or 0,
    )


def _watch_item_sort_key(entry: WatchNextEntry) -> "tuple[int, int, int]":
    item = entry.watch_item
    return (item.airport_id, item.signal_id or 0, item.source_assertion_id or 0)


def _watch_item_is_addressed(item: WatchItem, candidates: "tuple[ChangeCandidateResult, ...]") -> bool:
    if item.source_assertion_id is not None:
        return any(candidate.source_assertion_id == item.source_assertion_id for candidate in candidates)
    if item.signal_id is not None:
        return any(candidate.existing_signal_id == item.signal_id for candidate in candidates)
    return any(candidate.airport_id == item.airport_id for candidate in candidates)


def generate_update_report(
    *,
    watch_items: "tuple[WatchItem, ...]",
    candidates: "tuple[ChangeCandidateResult, ...]",
    acquisition_notes: "Optional[dict[int, str]]" = None,
    generated_at: "Optional[datetime]" = None,
) -> UpdateReport:
    """Groups already-computed results deterministically - performs no
    lookup, no query, no write of any kind (Part 14: this engine is
    independent of live network calls and of the database entirely).

    `acquisition_notes`: optional, operator-observed, per-airport status
    text (e.g. "Cloudflare-blocked - could not verify") surfaced verbatim
    under WATCH NEXT for a watch item this run gathered no evidence for -
    never fabricated by this module itself (Part 16 benchmark item E:
    "fail closed / WATCH NEXT, no fabricated evidence")."""
    notes = acquisition_notes or {}

    material_changes = tuple(sorted((c for c in candidates if c.classification in _MATERIAL_CLASSIFICATIONS), key=_candidate_sort_key))
    needs_human_review = tuple(
        sorted((c for c in candidates if c.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE), key=_candidate_sort_key)
    )
    corroboration_only = tuple(
        sorted((c for c in candidates if c.classification == MaterialChangeClassification.CORROBORATION_ONLY), key=_candidate_sort_key)
    )
    no_material_change = tuple(
        sorted((c for c in candidates if c.classification in _NO_CHANGE_CLASSIFICATIONS), key=_candidate_sort_key)
    )

    watch_next = tuple(
        sorted(
            (
                WatchNextEntry(watch_item=item, note=notes.get(item.airport_id, _DEFAULT_WATCH_NEXT_NOTE))
                for item in watch_items
                if not _watch_item_is_addressed(item, candidates)
            ),
            key=_watch_item_sort_key,
        )
    )

    return UpdateReport(
        generated_at=generated_at or datetime.now(),
        material_changes=material_changes,
        needs_human_review=needs_human_review,
        corroboration_only=corroboration_only,
        no_material_change=no_material_change,
        watch_next=watch_next,
    )


def _render_candidate(candidate: ChangeCandidateResult) -> "list[str]":
    lines = []
    header = candidate.airport_display_name or f"airport {candidate.airport_id}"
    if candidate.existing_signal_id is not None:
        header += f" (Signal {candidate.existing_signal_id})"
    lines.append(f"{header}")
    lines.append(f"  classification: {candidate.classification.value}  priority: {candidate.report_priority.value}")
    if candidate.proposed_changes:
        for field_name, new_value in sorted(candidate.proposed_changes.items()):
            old_value = candidate.old_values.get(field_name)
            lines.append(f"  - {field_name}: {old_value!r} -> {new_value!r}")
    if candidate.supporting_evidence_excerpt:
        lines.append(f"  evidence: {candidate.source_title or 'unknown source'} ({candidate.source_reliability_level or 'unknown reliability'})")
        lines.append(f"    {candidate.supporting_evidence_excerpt}")
        if candidate.source_url:
            lines.append(f"    {candidate.source_url}")
    if candidate.unresolved_identity_notes:
        lines.append("  unresolved:")
        for note in candidate.unresolved_identity_notes:
            lines.append(f"    - {note}")
    lines.append(f"  source_assertion_id: {candidate.source_assertion_id}")
    return lines


def render_report_text(report: UpdateReport) -> str:
    lines = [f"RWI UPDATE - {report.generated_at.isoformat()}", ""]

    lines.append("MATERIAL CHANGES")
    if not report.material_changes:
        lines.append("(none)")
    for candidate in report.material_changes:
        lines.extend(_render_candidate(candidate))
    lines.append("")

    lines.append("NEEDS HUMAN REVIEW")
    if not report.needs_human_review:
        lines.append("(none)")
    for candidate in report.needs_human_review:
        lines.extend(_render_candidate(candidate))
    lines.append("")

    lines.append("CORROBORATION ONLY")
    if not report.corroboration_only:
        lines.append("(none)")
    for candidate in report.corroboration_only:
        lines.extend(_render_candidate(candidate))
    lines.append("")

    lines.append("NO MATERIAL CHANGE")
    if not report.no_material_change:
        lines.append("(none)")
    for candidate in report.no_material_change:
        lines.extend(_render_candidate(candidate))
    lines.append("")

    lines.append("WATCH NEXT")
    if not report.watch_next:
        lines.append("(none)")
    for entry in report.watch_next:
        item = entry.watch_item
        header = f"airport {item.airport_id}"
        lines.append(header)
        lines.append(f"  - reason: {item.watch_reason} - {item.detail}")
        lines.append(f"  - {entry.note}")

    return "\n".join(lines)
