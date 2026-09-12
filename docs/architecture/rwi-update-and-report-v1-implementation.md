# RWI Update & Report V1 / V1.1 — Implementation Report

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
- `app/services/update_report_discovery_bridge.py` — V1.1, see below.
- `scripts/run_update_report.py` — operator CLI.
- `tests/test_update_report_watchset.py`,
  `tests/test_update_report_change_detection.py`,
  `tests/test_update_report_apply_and_engine.py`,
  `tests/test_update_report_discovery_bridge.py`.

---

## V1.1 — Watch → Discovery Bridge

Connects V1's watch set to RWI's existing research/discovery loop, so a
watch item can drive discovery instead of requiring an operator to already
have a `SourceAssertion` id in hand:

```
WatchItem
  -> app.services.update_report_discovery_bridge.build_watch_discovery_subject()
     -> Airport identity (name/iata/icao), known official domain
        (app.services.official_domain_discovery.select_preferred_official_hostname,
        unmodified), and the watch item's own existing SourceAssertion
        evidence text, where one already exists - nothing fabricated.

  -> plan_watch_discovery_queries()
     -> standard EMAS/RESA/... queries (app.discovery.query.build_search_plan)
     -> + site:<official domain> queries, when a domain is already known
        (app.discovery.query.plan_official_domain_document_queries)
     -> + temporal follow-up queries, ONLY for NEEDS_MORE_EVIDENCE watch
        items with existing evidence text
        (app.services.discovery_temporal_followup, unmodified)
     -> bounded to MAX_LIVE_QUERIES_PER_WATCH_ITEM (5) for LIVE execution -
        the full plan is still shown in a plan-only run

  -> run_watch_discovery(provider=None | a live SearchProvider)
     -> app.discovery.search / dedup / triage (all unmodified)
     -> DiscoveryRunResult (non-persisted): status, planned/executed
        queries, triaged candidates, known-source matches, an optional
        ChangeCandidateResult, and (when actionable) literal
        `fetch_research_candidate.py ...` commands for an operator to run
        by hand - never executed automatically
```

**What is automatic**: query planning, live search execution (only when a
provider is explicitly supplied), dedup, triage, and — for any watch item
that already has an existing, governed `SourceAssertion` — routing that id
into the unmodified `classify_candidate()` (status `STAGED_EVIDENCE_CREATED`,
Part 9's own "the bridge's job ends at SourceAssertion ID ready for Update
& Report").

**What remains human-gated, unchanged**: fetching a document
(`scripts/fetch_research_candidate.py`, requires `--allow-live-network`
+`--allow-database-write`), selecting/keeping a fragment
(`scripts/review_fragment_selection.py`, requires `--keep`), and every
governed evidence-persistence call. This bridge module never imports or
calls any of those — verified by an architectural-safety test
(`tests/test_update_report_discovery_bridge.py::test_bridge_never_imports_fetch_or_persistence_or_hub_followup`).
A live discovery run that finds an actionable candidate prints the exact
fetch command an operator would run — it never runs it.

**SearchResult != Evidence**: nothing in this module ever constructs a
`SourceAssertion`, `Signal`, or field change from a `SearchResult`/
`TriagedResult`. The only way a `ChangeCandidateResult` appears in a
`DiscoveryRunResult` is through an *already-existing* `SourceAssertion`.

**Plan-only vs. live**: `--discover` runs the bridge with `provider=None`
— zero network calls, full query plan still shown (status `PLANNED_ONLY`
when nothing else applies). `--discover-live` is the *only* flag that
makes a live network call (via `BraveSearchProvider`); it is never implied
by any other flag. Both remain entirely separate from `--apply`.

**Typed field-change extraction remains out of scope**: discovery can
raise new evidence to `NEW_SIGNAL_CANDIDATE`/`CORROBORATION_ONLY`/
`NEEDS_MORE_EVIDENCE`/`DUPLICATE` (all reachable without a typed value),
but never to `FIELD_CHANGE_CANDIDATE` on its own — that classification
still requires an operator-supplied `proposed_changes` value via
`--candidate`/`--propose`, exactly as in V1 (see "What still requires a
human" above).

**No autonomous governed writes**: `run_watch_discovery()`/
`run_discovery_for_watch_set()` never call `session.add()`/`flush()`/
`commit()` themselves, and the CLI's `--discover`/`--discover-live` path
still opens the database via the same read-only SQLite URI as plain
report mode.

**Bounded by design**: at most 5 live queries per watch item
(`MAX_LIVE_QUERIES_PER_WATCH_ITEM`), and at most 5 fetch instructions
surfaced per watch item (`MAX_ACTIONABLE_CANDIDATES_PER_WATCH_ITEM`) — a
real finding from the live SDF benchmark: a `site:<official domain>`
query legitimately returns many pages on that domain, and the existing,
unmodified triage scoring already promotes any same-domain hit to at
least MEDIUM band, so an un-capped list would have printed dozens of
fetch commands per watch item.

**Known, documented limitations (V1.1)**:
- `app.services.official_hub_followup` (bounded hub-page follow-up) is
  not wired in — it is an internal detail of
  `run_research_loop(follow_up_official_hubs=True)`, which this bridge
  does not call.
- Known-source matching (Part 8) is airport-scoped, exact-normalized-URL
  matching only — it does not detect near-duplicate content at a
  different URL.
- A `WatchItem`'s `acquisition_notes` are keyed by `airport_id`; two watch
  items for the same airport share one note slot in the rendered report
  (an existing V1 report-engine limitation, not new to V1.1).

---

## Human KEEP → Governed Evidence Persistence Bridge

Closes the one operator-confirmed gap in the loop: a human-KEPT
`CandidateFragment` (`scripts/review_fragment_selection.py`) previously had
nowhere to go — no script wired it into a persistence service. Now:

```
Snapshot (already fetched) -> extract_document() -> select_fragments()
  -> apply_keep_decisions(keep_indices=...)  [the SAME human KEEP gate
     review_fragment_selection.py already uses]
  -> CandidateFragment(s)
  -> scripts/persist_kept_candidate_fragment.py
     (build_metadata_from_kept_fragments / preview_kept_fragments /
     persist_kept_fragments)
  -> app.services.known_airport_evidence_persistence
     .apply_known_airport_evidence_persistence()  [unmodified]
  -> Source (create/reuse) + SourceAssertion (create/reuse)
  -> python -m scripts.run_update_report --candidate <SourceAssertion id>
```

Two independent, explicit gates are both required to write anything:
`--keep INDICES` (selects which fragment(s)) and `--allow-database-write`
(a second, separate authorization, matching
`scripts/fetch_research_candidate.py`'s own existing convention) — `--keep`
alone only produces a zero-write preview. No Signal, ReviewerAction, or
publication is ever created — `known_airport_evidence_persistence` itself
never touches any of those. See
`scripts/persist_kept_candidate_fragment.py`'s own module docstring for the
full recon (exact object emitted after KEEP, exact persistence function
reused, the one adapter field mapping, and which provenance fields are
preserved vs. honestly reported as "not available" — `language` is never
populated anywhere in this pipeline today).
