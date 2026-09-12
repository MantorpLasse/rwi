# RWI Update & Report V1 — Implementation Report

**Status:** IMPLEMENTED, NARROW SLICE. No schema migration, no production DB
write, no autonomous Signal mutation, no autonomous publication, no FH-D4
change.

Baseline: branch `main`, HEAD `62bc17c1e18b8bce1b1cfe6665974970944aeba1` at
the start of this mission. Builds directly on the trust-precondition slices
already landed (`04efd66` "Add trust preconditions for governed signal
updates", `62bc17c` "Add append-only signal amendment audit trail") and the
recon in `docs/architecture/rwi-update-and-report-v1-system-checkpoint.md`
and `docs/architecture/rwi-signal-amendment-audit-trail-design.md`.

## Scope

The first end-to-end WATCH → DISCOVER → DETECT MATERIAL CHANGE → HUMAN
VERIFY → GOVERNED UPDATE → REPORT loop. Every step is read-only except the
one, separate, explicit apply step.

## Flow

```
Session
  -> app.services.update_report_watchset.plan_watch_set()
     -> tuple[WatchItem, ...] (bounded: unpublished Signals + staged
        evidence needing attention, intelligence-shaped assertion types
        only - never all 89 airports)

  -> app.services.update_report_change_detection.classify_candidate()
     (one call per candidate SourceAssertion - from the watch set's own
     source_assertion_ids, plus any operator-supplied --candidate ids)
     -> reuses app.services.existing_signal_reconciliation_candidates
        (build_reconciliation_subject / find_reconciliation_candidates)
        and app.services.existing_signal_reconciliation
        (evaluate_existing_signal_reconciliation) UNMODIFIED for identity
     -> ChangeCandidateResult (non-persisted; classification + full
        human-review context)

  -> app.services.update_report_engine.generate_update_report()
     -> UpdateReport (five deterministic groups)
     -> render_report_text() -> str (internal text/Markdown report)

  -> [SEPARATE, EXPLICIT, OPTIONAL]
     app.services.update_report_apply.apply_field_change_candidate()
     -> app.services.signal_amendment.amend_signal()
     -> Signal mutated + durable SignalAmendmentAction/FieldChange audit
        rows, in the existing governed write seam - nothing new invented
```

CLI: `scripts/run_update_report.py` (`python -m scripts.run_update_report`).
Default mode opens the database via SQLite's own read-only URI
(`mode=ro`), the same pattern `scripts/list_human_review_queue.py` already
uses, so a coding mistake cannot write even by accident. `--apply` is a
separate invocation with its own explicit `--signal-id`/`--reviewer`/
`--reason`/`--apply-set` flags, using a normal writable session.

## What is automated

- Identifying which Signals/evidence are worth looking at right now
  (bounded watch set).
- Comparing new evidence against existing Signals using the already-built,
  already-hardened reconciliation core (never re-implemented here).
- Classifying the result into one of seven triage buckets
  (`app.services.update_report_vocabulary.MaterialChangeClassification`)
  and one of four report priorities, and grouping/rendering an internal
  report.
- Applying an *already-decided* field change through the existing governed
  `amend_signal()` seam, when an operator explicitly invokes `--apply`.

## What still requires a human

- Every field-value proposal (`CandidateEvidenceInput.proposed_changes`) is
  supplied by a human who has already read the evidence — this system has
  no generic "read this text, produce a field value" extractor (the only
  raw-text parsing in this codebase is source-family-specific, under
  `app/acquisition/`), and building one was out of scope (would be exactly
  the kind of inference the mission forbids).
- Every `CONTRADICTION_OR_CORRECTION`/`NEW_SIGNAL_CANDIDATE` classification
  requires an explicit human flag (`correction_flag`/`proposed_new_signal`)
  — never inferred from the shape of the values.
- The `--apply` write step is a separate CLI invocation with mandatory
  `--reviewer`/`--reason` — never triggered by report generation.
- `NEW_SIGNAL_CANDIDATE` and `CORROBORATION_ONLY` have **no apply path in
  V1** — both existing governed paths
  (`create_signal_from_approved_review()`/
  `link_source_assertion_to_duplicate_signal()`) require governance state
  (a matching `ReviewerAction`) this mission's candidate inputs are not
  guaranteed to have; an operator uses the existing, separate governed
  review workflow for those two cases (Part 8's own "otherwise report not
  yet wired, and stop").

## Explicit non-goals (per mission STOP conditions)

- No new DB table, no schema migration, no new persisted entity — every
  result type (`WatchItem`, `CandidateEvidenceInput`, `ChangeCandidateResult`,
  `UpdateReport`) is a frozen dataclass, recomputed on demand.
- No autonomous Signal mutation or publication.
- No new `ReviewerAction`/amendment-audit vocabulary.
- No FH-D4 change — this package never imports
  `signal_disposition_resolution`/`fh_d4_disposition_resolution`.
- No new candidate-discovery/crawling architecture — candidate evidence is
  always an already-existing `SourceAssertion`.
- No project-identity inference by same airport/runway alone — every
  identity decision is delegated to
  `app.services.existing_signal_reconciliation`, unmodified.

## Known, documented limitations (not fixed here — future, separate slices)

- The watch set does not yet include airports reached only through
  `app.services.discovery_temporal_followup`'s own trigger detection
  (requires an already-fetched `CandidateFragment`, which conflicts with
  keeping the report engine independent of live network calls in this
  slice).
- The ~43 "orphaned legacy" `SourceAssertion` rows (evidence_quality
  `direct_strong`, never routed through the staged lane's structural
  predicate — confirmed still present on the real DB during this mission,
  e.g. Greater Binghamton Signal 6's own supporting project_construction
  assertions) are not included in the automatic watch set, matching the
  checkpoint recon's own "classification pass, not 43 individual reviews"
  recommendation. They remain analyzable via `--candidate SOURCE_ASSERTION_ID`.
- No field-value auto-extraction from raw evidence text (see "What still
  requires a human" above) — deliberate, not an oversight.

## Files

- `app/services/update_report_vocabulary.py` — `MaterialChangeClassification`,
  `ReportPriority`.
- `app/services/update_report_watchset.py` — `WatchItem`, `plan_watch_set()`.
- `app/services/update_report_change_detection.py` — `CandidateEvidenceInput`,
  `ChangeCandidateResult`, `classify_candidate()`.
- `app/services/update_report_apply.py` — `apply_field_change_candidate()`.
- `app/services/update_report_engine.py` — `UpdateReport`,
  `generate_update_report()`, `render_report_text()`.
- `scripts/run_update_report.py` — operator CLI.
- `tests/test_update_report_watchset.py`,
  `tests/test_update_report_change_detection.py`,
  `tests/test_update_report_apply_and_engine.py`.
