"""RWI Update & Report V1 — operator CLI (Part 15).

    python -m scripts.run_update_report --watch-only
    python -m scripts.run_update_report
    python -m scripts.run_update_report --candidate 123 --propose 123:target_year=2030
    python -m scripts.run_update_report --candidate 123 --propose 123:target_year=2030 --correction 123
    python -m scripts.run_update_report --note 40:"Cloudflare-blocked - could not verify"
    python -m scripts.run_update_report --apply --signal-id 6 --apply-set target_year=2030 \\
        --reviewer "operator@example.test" --reason "AMPU Table 8-1 correction"
    python -m scripts.run_update_report --discover
    python -m scripts.run_update_report --discover-live

DEFAULT BEHAVIOR IS READ-ONLY: with no `--apply`, this script opens the
target database via SQLite's own read-only URI mode (`mode=ro`) - the same
`build_readonly_engine()` pattern `scripts/list_human_review_queue.py`
already uses - so even a coding mistake in the report/detection path could
not write, not merely by convention. Watch-set planning
(app.services.update_report_watchset.plan_watch_set), candidate
classification (app.services.update_report_change_detection.classify_candidate),
and report rendering (app.services.update_report_engine) never call
`session.add()`/`flush()`/`commit()` themselves either - see each module's
own docstring.

`--apply` is a SEPARATE, explicit mode requiring `--signal-id`, `--reviewer`,
`--reason`, and at least one `--apply-set FIELD=VALUE` - it opens a normal,
writable session (`app.database.SessionLocal`) and calls
`app.services.signal_amendment.amend_signal()` directly, then commits. No
`--apply` invocation is ever implied by running the default report mode; an
operator must re-invoke the script explicitly, with explicit reviewer
attribution, to write anything. There is no apply path for NEW_SIGNAL_CANDIDATE
or CORROBORATION_ONLY candidates in V1 - see
app.services.update_report_apply's own module docstring for why.

CANDIDATE INPUT (Part 2): the watch set's own `source_assertion_id`s are
included automatically; `--candidate SOURCE_ASSERTION_ID` adds further,
explicit, already-existing SourceAssertion ids (e.g. ones discovered through
a separate research-loop run, or reviewed/unlinked evidence an operator
already knows about). `--propose "SA_ID:field=value"` represents an
operator who has already read that evidence and determined an explicit new
value - this script never extracts a field value from raw text itself (see
app.services.update_report_change_detection's own module docstring for why).

DISCOVERY BRIDGE (V1.1, Part 12): `--discover` runs
app.services.update_report_discovery_bridge for every watch item with
`provider=None` - PLAN ONLY, zero network calls, same convention as
app.services.research_loop.run_research_loop(provider=None). `--discover-live`
runs the SAME bridge with a real `BraveSearchProvider` - the ONLY way this
script ever makes a live network call; it is never implied by any other
flag, including plain `--discover`. Neither flag ever fetches a document,
persists a SourceAssertion, or bypasses the existing human fetch/KEEP gates
(scripts/fetch_research_candidate.py, scripts/review_fragment_selection.py)
- a live discovery run that finds an actionable candidate prints the exact
`fetch_research_candidate.py` command an operator would run by hand, and
stops there. See app.services.update_report_discovery_bridge's own module
docstring for the full integration path and human-gate boundary.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.signal_amendment import amend_signal
from app.services.signal_amendment_serialization import (
    deserialize_amendment_value,
    expected_python_type_for_field,
)
from app.services.update_report_change_detection import CandidateEvidenceInput, classify_candidate
from app.services.update_report_engine import generate_update_report, render_report_text
from app.services.update_report_watchset import DEFAULT_WATCH_SET_LIMIT, plan_watch_set

DEFAULT_DATABASE = Path("data/runway_safe.db")

EXIT_OK = 0
EXIT_ERROR = 1


def _safe_print(*values: object, file=None, sep: str = " ", end: str = "\n") -> None:
    """print() that can never raise UnicodeEncodeError on a restrictive
    terminal encoding (e.g. a Windows console bound to cp1252) - real
    evidence text (SourceAssertion.raw_relevant_text) can legitimately
    contain any Unicode character. Output-only: never mutates the
    underlying value, only what reaches this one stream may fall back to a
    readable backslash-escape. Mirrors scripts/fetch_research_candidate.py's
    own `_safe_print()` exactly."""
    stream = file if file is not None else sys.stdout
    text = sep.join(str(value) for value in values) + end
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace"))


def build_readonly_engine(database: Path):
    """The only read-mode database-binding function in this script - opens
    SQLite in its own read-only URI mode, exactly matching
    scripts/list_human_review_queue.py's own `build_readonly_engine()`."""
    resolved = database.resolve()
    return create_engine(f"sqlite:///file:{resolved.as_posix()}?mode=ro&uri=true", future=True)


def _parse_propose(raw: str) -> "tuple[int, str, object]":
    try:
        sa_id_text, field_value = raw.split(":", 1)
        field_name, raw_value = field_value.split("=", 1)
        source_assertion_id = int(sa_id_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--propose must look like SA_ID:field=value, got {raw!r}") from exc
    value = deserialize_amendment_value(raw_value, expected_python_type_for_field(field_name))
    return source_assertion_id, field_name, value


def _parse_note(raw: str) -> "tuple[int, str]":
    try:
        airport_id_text, text = raw.split(":", 1)
        return int(airport_id_text), text
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--note must look like AIRPORT_ID:text, got {raw!r}") from exc


def _parse_apply_set(raw: str) -> "tuple[str, object]":
    try:
        field_name, raw_value = raw.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--apply-set must look like field=value, got {raw!r}") from exc
    value = deserialize_amendment_value(raw_value, expected_python_type_for_field(field_name))
    return field_name, value


@dataclass(frozen=True)
class ReportRunConfig:
    database: Path
    limit: "Optional[int]" = DEFAULT_WATCH_SET_LIMIT
    watch_only: bool = False
    explicit_candidate_ids: "tuple[int, ...]" = ()
    proposed_changes_by_id: "dict[int, dict[str, object]]" = None  # type: ignore[assignment]
    correction_ids: "frozenset[int]" = frozenset()
    new_signal_ids: "frozenset[int]" = frozenset()
    acquisition_notes: "dict[int, str]" = None  # type: ignore[assignment]
    discover: bool = False
    discover_live: bool = False


def _discovery_note(discovery_result) -> str:
    parts = [discovery_result.status.value, *discovery_result.notes, *discovery_result.fetch_instructions]
    return " | ".join(parts)


def run_report(session: Session, config: ReportRunConfig) -> str:
    """The one function that does the work for report/watch-only/discover
    mode - both `main()` and tests call this. Read-only: performs no
    add/flush/commit regardless of the session it is given, and regardless
    of `config.discover`/`config.discover_live` (see
    app.services.update_report_discovery_bridge's own module docstring -
    it never writes either)."""
    watch_items = plan_watch_set(session, limit=config.limit)
    if config.watch_only:
        lines = [f"WATCH SET ({len(watch_items)} item(s))"]
        for item in watch_items:
            lines.append(f"airport {item.airport_id}: {item.watch_reason} - {item.detail}")
        return "\n".join(lines) if watch_items else "WATCH SET (0 items)"

    proposed_by_id = config.proposed_changes_by_id or {}
    acquisition_notes = dict(config.acquisition_notes or {})

    seen: "set[int]" = set()
    results = []
    discovery_summary_lines: "list[str]" = []

    if config.discover or config.discover_live:
        from app.services.update_report_discovery_bridge import run_discovery_for_watch_set

        provider = None
        if config.discover_live:
            from app.discovery.brave_search_provider import BraveSearchProvider

            provider = BraveSearchProvider()

        discovery_results = run_discovery_for_watch_set(session, watch_items, provider=provider)
        for discovery_result in discovery_results:
            discovery_summary_lines.append(
                f"airport {discovery_result.watch_item.airport_id}: {discovery_result.status.value} "
                f"(planned={len(discovery_result.queries_planned)}, executed={len(discovery_result.queries_executed)}, "
                f"candidates={len(discovery_result.triaged_candidates)}, known={len(discovery_result.known_source_matches)})"
            )
            if discovery_result.change_candidate is not None:
                source_assertion_id = discovery_result.change_candidate.source_assertion_id
                if source_assertion_id not in seen:
                    seen.add(source_assertion_id)
                    results.append(discovery_result.change_candidate)
            else:
                acquisition_notes[discovery_result.watch_item.airport_id] = _discovery_note(discovery_result)
    else:
        candidate_ids = [item.source_assertion_id for item in watch_items if item.source_assertion_id is not None]
        for source_assertion_id in candidate_ids:
            is_duplicate = source_assertion_id in seen
            seen.add(source_assertion_id)
            results.append(
                classify_candidate(
                    session, CandidateEvidenceInput(source_assertion_id=source_assertion_id),
                    is_duplicate_in_batch=is_duplicate,
                )
            )

    for source_assertion_id in config.explicit_candidate_ids:
        is_duplicate = source_assertion_id in seen
        seen.add(source_assertion_id)
        evidence_input = CandidateEvidenceInput(
            source_assertion_id=source_assertion_id,
            proposed_changes=proposed_by_id.get(source_assertion_id),
            correction_flag=source_assertion_id in config.correction_ids,
            proposed_new_signal=source_assertion_id in config.new_signal_ids,
        )
        results.append(classify_candidate(session, evidence_input, is_duplicate_in_batch=is_duplicate))

    report = generate_update_report(watch_items=watch_items, candidates=tuple(results), acquisition_notes=acquisition_notes)
    output = render_report_text(report)
    if discovery_summary_lines:
        output = "DISCOVERY SUMMARY\n" + "\n".join(discovery_summary_lines) + "\n\n" + output
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, default=DEFAULT_WATCH_SET_LIMIT)
    parser.add_argument("--watch-only", action="store_true")
    parser.add_argument("--discover", action="store_true", help="plan discovery for the watch set - zero network calls")
    parser.add_argument(
        "--discover-live", action="store_true",
        help="run discovery for the watch set with a live BraveSearchProvider - the only flag that makes a live network call",
    )
    parser.add_argument("--candidate", action="append", type=int, default=[], dest="candidates")
    parser.add_argument("--propose", action="append", type=_parse_propose, default=[], dest="proposals")
    parser.add_argument("--correction", action="append", type=int, default=[], dest="corrections")
    parser.add_argument("--new-signal", action="append", type=int, default=[], dest="new_signals")
    parser.add_argument("--note", action="append", type=_parse_note, default=[], dest="notes")

    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--signal-id", type=int)
    parser.add_argument("--reviewer", type=str)
    parser.add_argument("--reason", type=str)
    parser.add_argument("--source-assertion-id", type=int, default=None)
    parser.add_argument("--apply-set", action="append", type=_parse_apply_set, default=[], dest="apply_sets")
    return parser


def main(argv: "Optional[list[str]]" = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.apply:
        if not args.signal_id or not args.reviewer or not args.reason or not args.apply_sets:
            _safe_print("--apply requires --signal-id, --reviewer, --reason, and at least one --apply-set", file=sys.stderr)
            return EXIT_ERROR
        from app.database import SessionLocal

        with SessionLocal() as session:
            try:
                result = amend_signal(
                    session,
                    signal_id=args.signal_id,
                    changes=dict(args.apply_sets),
                    reason=args.reason,
                    reviewer=args.reviewer,
                    source_assertion_id=args.source_assertion_id,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
                session.rollback()
                _safe_print(f"apply refused: {exc}", file=sys.stderr)
                return EXIT_ERROR
            session.commit()
        _safe_print(f"applied: Signal {result.signal_id}, action {result.signal_amendment_action_id}")
        for change in result.changes:
            _safe_print(f"  {change.field}: {change.old_value!r} -> {change.new_value!r}")
        return EXIT_OK

    proposed_by_id: "dict[int, dict[str, object]]" = {}
    for source_assertion_id, field_name, value in args.proposals:
        proposed_by_id.setdefault(source_assertion_id, {})[field_name] = value

    config = ReportRunConfig(
        database=args.database,
        limit=args.limit,
        watch_only=args.watch_only,
        explicit_candidate_ids=tuple(args.candidates),
        proposed_changes_by_id=proposed_by_id,
        correction_ids=frozenset(args.corrections),
        new_signal_ids=frozenset(args.new_signals),
        acquisition_notes=dict(args.notes),
        discover=args.discover,
        discover_live=args.discover_live,
    )

    engine = build_readonly_engine(config.database)
    with Session(engine) as session:
        output = run_report(session, config)
        session.rollback()
    engine.dispose()

    _safe_print(output)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
