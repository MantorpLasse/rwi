# Fleet Health Check CLI — `scripts/run_data_health_check.py`

## 1. Purpose and scope

This is the operational "cockpit" layer over the already-reviewed and
committed Fleet Health Check system:

- FHC1 — `app.services.fleet_health_check.run_fleet_hard_invariant_check()`
  (11 `DETERMINISTIC_ERROR` rules; see
  `fleet-health-check-fhc1-pure-core-report.md` /
  `fleet-health-check-fhc2-db-adapter-report.md`)
- FHC3 — `app.services.fleet_health_check.run_fleet_review_check()`
  (13 `DETERMINISTIC_WARNING` / `REVIEW_REQUIRED` / `INFORMATIONAL` rules;
  see `fleet-health-check-fhc3-warning-review-informational-report.md`)
- FHC4 — `app.services.fleet_health_presentation_check.run_fleet_presentation_check()`
  (1 `DETERMINISTIC_ERROR` rule, FH-H2; see
  `fleet-health-check-fhc4-presentation-cross-check-report.md`)

The CLI contains **no health-rule logic of its own**. It opens a database
read-only, runs a schema-readiness gate, calls the three functions above
exactly as they are already reviewed and tested, groups/sorts/renders their
combined `HealthFinding` output, and exits with a deterministic status code.
This is verified structurally by `tests/test_run_data_health_check.py`'s
`TestNoHealthLogicInCli` class (AST-based: the only `fleet_health*` names
imported are the three `run_*` functions, `HealthClassification`/
`HealthFinding`, and the three already-public rule-ID registries
(`RULE_IDS`/`REVIEW_RULE_IDS`/`PRESENTATION_RULE_IDS`, used only to
validate `--rule` - see §10.2; the only classes defined in the module are
the two dataclasses).

Explicitly out of scope, per the implementation mission: FHC5 (no
disposition workflow — no acknowledge/resolve/suppress/ignore/repair/
mark-false-positive), any new health rule, any repair, any write to
`ReviewerAction`/`Signal`/`Airport`/`Runway`, any schema migration, any
auto-publish, any change to the repository's canonical `site/` output.

## 2. Usage

```
python -m scripts.run_data_health_check --database data/runway_safe.db
python -m scripts.run_data_health_check --database data/runway_safe.db --output-dir /tmp/site_check
python -m scripts.run_data_health_check --database data/runway_safe.db --classification DETERMINISTIC_ERROR
python -m scripts.run_data_health_check --database data/runway_safe.db --rule FH-C3 --details
python -m scripts.run_data_health_check --database data/runway_safe.db --json
```

### Arguments

| Flag | Required | Default | Effect |
|---|---|---|---|
| `--database` | yes | none | Path to the SQLite database to check, opened read-only. Deliberately has **no default** — diverges from `scripts/list_human_review_queue.py`'s own defaulted-to-real-DB convention, matching this mission's explicit stronger safety requirement (and matching `scripts/review_reconciliation_item.py`'s own `required=True` pattern for anything that could plausibly target the wrong database). |
| `--output-dir` | no | `None` | If supplied, also runs FHC4 (FH-H2) by exporting into this directory. Never defaults to `site/`; the directory is DELETED and fully recreated by the real static exporter, so callers must always supply their own disposable path. The repository's own canonical `site/` output is refused outright regardless of how the path is spelled (`CANONICAL_SITE_DIR`/`_is_canonical_site_output()` - see §10.1(b)). Without this flag, FHC4 does not run at all. |
| `--rule` | no | `None` | Show only findings for this exact `rule_id` (e.g. `FH-C3`). Validated against `ALL_RULE_IDS` (the union of all 25 real rule ids) - an unknown value is a usage error, never silently zero findings. |
| `--classification` | no | `None` | Show only findings of this exact classification (`DETERMINISTIC_ERROR`, `DETERMINISTIC_WARNING`, `REVIEW_REQUIRED`, `INFORMATIONAL`). |
| `--details` | no | off | Also print each finding's `structured_evidence` (omitted by default to keep output compact). |
| `--json` | no | off | Emit a machine-readable JSON report instead of the human-readable text report. |

No `--fix`/`--repair`/`--approve`/`--duplicate`/`--publish`/`--resolve`/
`--suppress`/`--ignore` flag exists anywhere in the parser (verified by
`test_no_repair_or_disposition_flags`).

## 3. Schema readiness gate

Composes FIVE read-only, `sqlite3.connect(..., mode=ro)`-based `inspect()`
functions - the three `scripts/list_human_review_queue.py`'s own
`check_schema_readiness()` uses, plus two more this CLI's own critical
review added after finding the three-inspector composition alone did not
cover every column FHC1/FHC3 actually read (see §10.1(a)):

- `scripts.migrate_discovery_governed_evidence_slice1.inspect()` (Slice 1)
- `scripts.migrate_governed_signal_creation_slice9c.inspect()` (Slice 9C)
- `scripts.migrate_intelligence_review_persistence_slice4.inspect()` (Slice 4)
- `scripts.migrate_promotion_policy_persistence_slice7.inspect()` (Slice 7)
- `scripts.migrate_reconciliation_confirmation_slice_r4b.inspect()` (R4B)

These are the exact columns FHC1's own D2/G2/G3 rules and FHC3's own G1
rule actually read (FHC1's `_build_source_assertion_governance()` selects
the whole `ReviewerAction` row, which includes `reconciliation_fingerprint`;
FHC1's own D2 fact builder and FHC3's
`_build_source_assertion_governance_decisions()` both read
`SourceAssertion.signal_id`/`identity_guard_decision`/
`intelligence_review_decision`/`promotion_policy_decision` directly).

If any required column is missing, `run_data_health_check()` returns a
report with `blockers = (SCHEMA_MIGRATION_REQUIRED_BLOCKER,)` —
`"FLEET_HEALTH_SCHEMA_MIGRATION_REQUIRED"` — and does **not** open an ORM
session or run any of FHC1/FHC3/FHC4. The CLI prints `BLOCKED: ...` and
exits `2`. No migration, repair, or partial run is ever attempted.

## 4. Read-only guarantee

- `build_readonly_engine()` opens SQLite in its own driver-level read-only
  URI mode (`?mode=ro&uri=true`) — a real guarantee, not merely convention;
  a write attempt through this engine raises at the driver level (see
  `test_db_only_mode_readonly_engine_refuses_writes`).
- `run_data_health_check()` calls `session.rollback()` defensively before
  closing, exactly like `list_human_review_queue.py`'s own
  `run_review_queue()`, even though nothing in this script's call graph ever
  adds or flushes.
- FHC2/FHC3/FHC4's own `session.no_autoflush` guarantees are theirs to keep
  (already verified in their own test suites) - this standalone CLI opens
  its own read-only engine/session every run and never receives or touches
  a caller-supplied `Session`, so there is no "caller pending session" for
  it to autoflush at its own boundary. What this CLI's own test suite
  verifies instead is ordinary SQLite/SQLAlchemy transaction isolation: an
  external writer's pending, uncommitted change on the same database file
  is neither disturbed nor made visible by the CLI's own, separate
  connection (`test_o_external_pending_writer_isolated_from_cli_own_connection`
  - renamed during critical review from a name that overclaimed a
  CLI-level `no_autoflush` guarantee; see §10.3).
- SQL-instrumentation test (`before_cursor_execute` listener) asserts zero
  `INSERT`/`UPDATE`/`DELETE` statements are ever emitted during a full run
  including FHC4 (`test_no_write_sql_statements_emitted_instrumented`).
- The real database file is verified byte-identical (SHA-256) before and
  after both a DB-only run and a full presentation-mode run against it (see
  §8 below).

## 5. Presentation-mode gating

`run_fleet_presentation_check()` (FHC4) is called if and only if
`config.output_dir is not None`. This is the CLI's **one** deliberate,
narrow `try/except Exception` — converting a real exporter crash (FH-H1's
own "run it and see" failure mode, e.g. a malformed persisted row a
template cannot render) into a reported `presentation_error` string and
exit code `2`, rather than letting it propagate as an uncaught traceback
*or* silently becoming "0 findings". Every other exception anywhere else in
the CLI (a genuine, unexpected bug in FHC1/FHC3, whose schema readiness has
already been confirmed) is left to propagate uncaught — this is the only
catch block in the file.

The report and rendered output always distinguish three states, never
conflating "not run" with "passed":

- `Presentation check: NOT RUN (no --output-dir supplied)` — `output_dir`
  was `None`.
- `Presentation check: PASS (0 findings)` — FHC4 ran and found nothing.
- `Presentation check: ERROR (N finding(s))` — FHC4 ran and FH-H2 fired.
- `Presentation check: ERROR (export failed: ...)` — the real exporter
  raised.

## 6. Classification grouping and rendering

`render_report()` produces the mission's own suggested summary shape, with
one critical-review wording fix (§10.3): every count is explicitly labeled
"finding(s)" - a single finding can cover many entities (a real FH-F1
finding covers 67 Signals), so a bare number risked being misread as an
affected-entity count.

```
RWI FLEET HEALTH

Hard errors ............. N finding(s)
Warnings ................ N finding(s)
Review required ......... N finding(s)
Informational ........... N finding(s)
Presentation errors ..... N finding(s)
```

If `--rule`/`--classification` is active, a `NOTE: display filtered ...`
line appears directly under the presentation-status line, making clear the
counts below are scoped to the filter while `Overall status` above is
always the complete, unfiltered result (§10.2).

Findings are grouped first by classification (Hard errors → Warnings →
Review required → Informational → Presentation errors, in that fixed
order) then by `rule_id` (alphabetical), each finding line showing entity
type/ids and the finding's own `summary` text. A finding with more than 15
entity ids is truncated in default rendering (`... (+N more, see --json for
the full list)`, `_MAX_INLINE_ENTITY_IDS` - §10.3); the full list is always
present in `--json` output regardless of this cap. `structured_evidence`
is shown only under `--details`, one `key=value` line per evidence field,
sorted by key — no raw HTML/text blobs are ever dumped by default.

Presentation findings (`FH-H2`, itself `DETERMINISTIC_ERROR`) are rendered
in their own separate "Presentation errors" section and counted in their
own separate summary line — never folded into "Hard errors" — even though
both classifications and both count toward exit code 1 / overall status
`ERROR` identically.

## 7. Overall status and exit codes

`overall_status()` (text, non-scored):

| Status | Condition |
|---|---|
| `SCHEMA_MIGRATION_REQUIRED` | schema gate blocked the run |
| `OPERATIONAL_FAILURE` | the presentation check crashed or was refused (`report.presentation_error is not None`) - checked immediately after the schema-blocker check, before any finding is examined (critical-review fix, §10.1(c)) |
| `ERROR` | any `DETERMINISTIC_ERROR` finding present (from FHC1 and/or, when it ran and completed cleanly, FHC4) |
| `ATTENTION_REQUIRED` | zero `DETERMINISTIC_ERROR` findings, but at least one `DETERMINISTIC_WARNING` or `REVIEW_REQUIRED` finding |
| `HEALTHY` | no hard errors, no warnings, no review-required findings; presentation check clean if it ran |

`INFORMATIONAL` findings alone never move the status away from `HEALTHY`
(`test_informational_alone_never_makes_fleet_unhealthy`). A presentation
check that did not run (never requested) does not itself prevent `HEALTHY`
status, but is always reported separately via the "Presentation check"
line so a reader never mistakes "not run" for "passed" - and a presentation
check that DID run but crashed/was refused always reports
`OPERATIONAL_FAILURE`, never `HEALTHY`, even if FHC1/FHC3 were perfectly
clean (this exact combination was a real, reproduced defect before the
critical review - see §10.1(c)). This status is always computed from the
complete, unfiltered result - `--rule`/`--classification` never change it
(§10.2).

`exit_code_for()`:

| Code | Constant | Condition |
|---|---|---|
| `0` | `EXIT_HEALTHY_OR_ATTENTION` | no hard/presentation `DETERMINISTIC_ERROR` findings, schema ready, no export crash — covers both a genuinely clean fleet *and* one with only warning/review-required/informational findings |
| `1` | `EXIT_DETERMINISTIC_ERROR` | at least one real `DETERMINISTIC_ERROR` finding (FHC1 and/or FHC4) |
| `2` | `EXIT_OPERATIONAL_FAILURE` | schema not ready, or the real exporter crashed during a requested presentation check |

Warning/review-required/informational findings never cause a nonzero exit
on their own, per the mission's explicit instruction.

## 8. Real-database smoke run (read-only, no mutation)

Run against `data/runway_safe.db`
(SHA-256 `4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
size 1,794,048 bytes) in two modes:

**DB-only** (`--database data/runway_safe.db`): exit `0`, overall status
`ATTENTION_REQUIRED`, presentation check `NOT RUN`.

- Hard errors: **0**
- Warnings: **19** (FH-C3 = 18, FH-E1 = 1)
- Review required: **13** (FH-D4 = 12, FH-E4 = 1)
- Informational: **16** (FH-A1 = 10, FH-F1 = 1 finding covering 67 Signals, FH-F2 = 5)

This matches the mission's own stated known health-state expectations
exactly (FH-A1=10, FH-C3=18, FH-D4=12, FH-E1=1, FH-E4=1, FH-F1 covering 67
Signals, FH-F2=5; 48 FHC3 findings total).

**Full mode** (`--database data/runway_safe.db --output-dir <temp dir>`):
same FHC1/FHC3 findings as above, plus `Presentation check: PASS
(0 findings)` — 67 published Signals, 67 rendered signal detail pages,
identical sets, no FH-H2 finding.

**Safety proof (both runs):**

| Check | Before | After (DB-only) | After (full mode) |
|---|---|---|---|
| DB SHA-256 | `4aa8c25f...` | `4aa8c25f...` (identical) | `4aa8c25f...` (identical) |
| DB `PRAGMA foreign_key_check` | `[]` | — | — |
| DB `PRAGMA integrity_check` | `ok` | — | — |
| Canonical `site/` content hash (160 files) | `63a43386...` | n/a (not touched in DB-only mode) | `63a43386...` (identical) |

The real database was never opened by anything other than the CLI's own
read-only engine, and the repository's canonical `site/` output was never
written to — the full-mode run exported only into an explicit temporary
directory (`--output-dir`), never `site/` itself.

## 9. Synthetic test coverage

`tests/test_run_data_health_check.py` (28 tests) covers, using isolated
`tmp_path`-scoped databases only:

- No-health-logic-in-CLI (AST-based import/class-surface checks)
- Read-only guarantee (SQL instrumentation, read-only-engine write refusal,
  real-file-unchanged)
- Scenarios A (healthy), B (informational-only), C (warning-only), G
  (presentation clean), H (presentation missing page, via a doctored
  snapshot), I (presentation not requested), J (schema missing — via
  `Base.metadata.create_all()` + a real `downgrade()` of slice 4, matching
  the established pattern in `tests/test_human_review_queue.py`), K
  (exporter failure — monkeypatched crash), L (wrong-DB isolation — two
  independent databases with non-overlapping distinctive ids), M
  (Unicode/international data), N (duplicate-findings/order determinism —
  two runs produce byte-identical rendered output), O (caller-pending ORM
  change / no-autoflush)
- A separate hard-error scenario (cross-airport runway reference) proving
  `ERROR` status / exit code `1`
- Filtering (`--rule`, `--classification`)
- `main()` / argparse behavior: `--database` required with no default, no
  repair/disposition flags exist, `--json` mode, `--output-dir` gating

All 28 pass; combined with the existing FHC1–FHC4 and static-export suites
(364 total) and the full repository suite (**2023 passed** — the prior
1995-test baseline plus these 28 new tests, zero regressions).

## 10. Critical review checkpoint

A fresh adversarial review of this CLI (before commit/push) explicitly
distrusted every claim in this report's own §1–§10 and independently
re-verified them against the code and a real DB smoke run. Three genuine
defects were found and fixed; everything else re-checked out. This section
documents the review honestly - it does not rewrite the implementation
history above.

### 10.1 Genuine defects found and fixed

**(a) Schema gate did not cover every column FHC1/FHC3 actually read.**
The original `check_schema_readiness()` composed the same three inspectors
`scripts/list_human_review_queue.py`'s own gate uses (Slice 4, Slice 7,
R4B) - but FHC1's own D2 fact builder and FHC3's own G1 rule both also read
`SourceAssertion.signal_id` (added by Slice 9C) and
`SourceAssertion.identity_guard_decision`/`.identity_guard_reason` (added
by Slice 1) unconditionally. A real, reproduced attack (a full current-ORM
schema with `identity_guard_decision` or `signal_id` then dropped) crashed
with a raw, uncaught `sqlalchemy.exc.OperationalError` instead of the clean
`FLEET_HEALTH_SCHEMA_MIGRATION_REQUIRED` refusal the mission requires. This
is a real (if narrow, chronologically-improbable-for-this-repo's-actual-DB)
gap inherited from copying the established R4D pattern verbatim - fixed by
extending `check_schema_readiness()` to compose five inspectors instead of
three, reusing two more already-existing, already-tested `inspect()`
functions (`migrate_discovery_governed_evidence_slice1.inspect()`,
`migrate_governed_signal_creation_slice9c.inspect()`) rather than
reimplementing anything. Two permanent regression tests added
(`TestSchemaGateCompleteness`), each proving `run_data_health_check()` does
not raise and returns the correct blocker instead. `list_human_review_queue.py`'s
own gate was left untouched - out of scope for this CLI's own review.

**(b) `--output-dir` had no protection against the repository's own
canonical `site/` output.** `build_site()` deletes and fully recreates
whatever directory it is given (see
`app/static_export/build.py`'s own `_build()`); nothing stopped a user (or
a script) from running `--output-dir site` from the repo root and silently
destroying the real generated production output. Reproduced for real,
unmocked, against the actual repository: `python -m
scripts.run_data_health_check --database data/runway_safe.db --output-dir
site` before the fix would have wiped `site/`. Fixed by adding
`CANONICAL_SITE_DIR` (`Path(__file__).resolve().parent.parent / "site"`)
and `_is_canonical_site_output()`, checked before FHC4/`build_site()` is
ever called - a match sets `presentation_error` to a
`CANONICAL_SITE_OUTPUT_REFUSED` message and returns, never invoking the
exporter. Re-verified for real against the repository after the fix: the
same command now exits `2`, reports `Overall status: OPERATIONAL_FAILURE`,
and `site/`'s content hash is unchanged. Permanent regression tests added
(`TestCanonicalSiteProtection`), including a same-basename-elsewhere
negative case and a symlink/traversal-equivalent positive case.

**(c) A clean FHC1/FHC3 result plus a crashed FHC4 rendered "Overall
status: HEALTHY" while the CLI still exited 2.** The original
`overall_status()` only consulted `hard_findings`/`review_findings`
(falling through to `presentation_findings` only when not `None`) and never
looked at `presentation_error` at all - so a real exporter crash after a
clean database produced a report whose headline line claimed the fleet was
healthy while the process exited with an operational-failure code. This is
exactly the "partial success without clearly signaling operational
failure" trap the review explicitly warned about. Reproduced directly
(mocked exporter crash + a fully healthy synthetic fleet) before fixing.
Fixed by making `overall_status()` check `report.presentation_error is not
None` first (immediately after the schema-blocker check) and return
`OPERATIONAL_FAILURE` - `exit_code_for()` already treated
`presentation_error` correctly and needed no change. Two permanent
regression tests added (`TestOverallStatusPresentationCrashConsistency`),
one asserting the status value directly and one asserting the literal
string `"Overall status: HEALTHY"` never appears in rendered output
alongside a `"Presentation check: ERROR"` line.

### 10.2 Re-verified sound (no change needed)

- **Filtering is genuinely display-only.** `overall_status()` and
  `exit_code_for()` both compute from the complete, unfiltered
  `hard_findings`/`review_findings`/`presentation_findings` - `config.rule`/
  `config.classification` are consulted only inside `_apply_filters()`,
  which is called solely from `render_report()`/`render_json_report()`
  for *display*. A DB seeded with a genuine cross-airport hard-invariant
  violation still reports `Overall status: ERROR` / exit `1` even when
  `--classification INFORMATIONAL` or an unrelated `--rule` is supplied
  (`TestFilteringNeverChangesGlobalHealthState`, both new tests). The
  `config` parameter's docstring on `overall_status()` was strengthened to
  say this explicitly, since an unused-looking parameter on a status
  function is exactly the kind of thing a future maintainer could
  misinterpret as filter-scoped.
- **Database isolation is real, not just textual.** Strengthened
  `test_l_wrong_db_isolation` to the FHC4 review's own established
  target/protected pattern: two databases with a deliberately distinctive,
  identifiable difference (one has an uncoded, FH-A3-triggering airport;
  the other doesn't), asserting the target's own finding appears only in
  the target's report, the protected database's byte content is
  unchanged both before and after either run, and the protected DB never
  shows the target-only finding.
- **Schema gate runs before any FHC4/export attempt.** Confirmed
  structurally (the gate's early-return happens before
  `build_readonly_engine()` is even called) and now also confirmed by a
  spy test (`TestSchemaGateRunsBeforeExport`) proving
  `run_fleet_presentation_check` is never invoked and no export artifact
  directory is ever created when the schema is not ready.
- **No CLI-level dedup layer exists or is needed.** A mocked service
  returning two identical `HealthFinding` objects is rendered as two
  separate lines by the CLI, unmodified - `TestDuplicateFindingsPassthrough`
  confirms the CLI never invents deduplication, consistent with the
  mission's stated preference that the service layer (already reviewed,
  already guaranteed duplicate-free by FHC1-4's own contracts) owns that
  concern.
- **`--rule` now fails loud on an unknown rule_id** instead of silently
  rendering zero findings, exactly mirroring how `--classification` already
  used `choices=`. `ALL_RULE_IDS` is the union of the three already-public,
  already-reviewed rule-ID registries (`fleet_health_rules.RULE_IDS`,
  `fleet_health_review_rules.REVIEW_RULE_IDS`,
  `fleet_health_presentation_rules.PRESENTATION_RULE_IDS`) - reused, not
  reimplemented; the CLI still contains no knowledge of what any rule
  *means*, only its own id string. `test_only_composes_existing_service_entry_points`
  was extended to allow (and require) these three additional imports.
- **JSON mode never mixes human prose with JSON on stdout**, in every mode
  tested (clean, blocked, exporter-crash) - `TestJsonErrorModes` parses
  `capsys` stdout directly with `json.loads()` in all three cases.
  Argparse's own usage errors (e.g. missing `--database`, invalid
  `--rule`) go to stderr with a `SystemExit`, never mixed with stdout.

### 10.3 Wording corrections (no functional change)

- **Summary-count wording.** "Warnings ................ 19" was
  ambiguous between "19 things wrong" and "19 finding *groups*" - a single
  real finding (FH-F1) covers 67 Signals. Changed every summary line to
  read "N finding(s)" explicitly, and added a one-line docstring/inline
  comment clarifying these are finding counts, never affected-entity
  counts.
- **Long entity-id lists are now capped in default (non-`--json`)
  rendering.** The real FH-F1 finding inlines 67 Signal ids on one line;
  capped default rendering at 15 ids plus a "+N more, see --json for the
  full list" suffix (`_MAX_INLINE_ENTITY_IDS`) - the full, untruncated list
  remains available via `--json` regardless of this cap; `--details` is
  unaffected (it only ever gated `structured_evidence`, never entity ids).
- **Autoflush test wording corrected.** The original
  `test_o_caller_pending_orm_change_no_autoflush` implied the CLI itself
  relies on a `session.no_autoflush` guarantee at its own boundary - but
  this standalone CLI opens its own read-only engine/session every run and
  never receives or touches a caller-supplied `Session`, so there is no
  "caller pending session" for the CLI to autoflush; that scenario is
  structurally impossible here (the real `no_autoflush` guarantee that
  matters is FHC2/FHC3/FHC4's own, on their own `session` parameter,
  already verified in their own test suites). Renamed to
  `test_o_external_pending_writer_isolated_from_cli_own_connection` with a
  docstring stating precisely what it does prove: ordinary SQLite/
  SQLAlchemy transaction isolation between the CLI's own independent
  connection and an external writer's pending, uncommitted transaction on
  the same database file.

### 10.4 Real operational result (post-fix)

Re-run for real against `data/runway_safe.db` (unchanged SHA-256
`4aa8c25f...`, size 1,794,048 bytes) after every fix above:

- **DB-only**: exit `0`, `Overall status: ATTENTION_REQUIRED`,
  `Presentation check: NOT RUN` - identical finding counts to the original
  implementation run (0 hard / 19 warnings / 13 review-required / 16
  informational; FH-A1=10, FH-C3=18, FH-D4=12, FH-E1=1, FH-E4=1, FH-F1
  covering 67 Signals, FH-F2=5) - the schema-gate extension does not affect
  this database (it is fully migrated).
- **Full mode** (isolated temp `--output-dir`): identical findings plus
  `Presentation check: PASS (0 findings)`; the real FH-F1 line now renders
  truncated (`1, 2, 3, ..., 15, ... (+52 more, see --json for the full
  list)`).
- **Canonical-site attack, for real**: `--database data/runway_safe.db
  --output-dir site` (run from the repo root, an unmocked, real
  invocation) now exits `2`, reports `Overall status: OPERATIONAL_FAILURE`
  and `Presentation check: ERROR (export failed: CANONICAL_SITE_OUTPUT_REFUSED:
  ...)`, and `site/`'s content hash is confirmed unchanged
  (`63a43386...`) both before and after the attempt.
- Real database SHA-256/size/mtime unchanged across every one of the three
  runs above; canonical `site/` content hash (160 files) unchanged across
  all three.

### 10.5 Final test totals

- Focused (new CLI + FHC1-4 + static export + human review queue): **459
  passed** (up from 364 before the review - 21 new CLI tests plus the human
  review queue suite included this time to cross-check the schema-gate
  extension doesn't regress its own precedent).
- CLI test file alone: **49 passed** (28 original + 21 critical-review
  additions: `TestSchemaGateCompleteness` ×3, `TestCanonicalSiteProtection`
  ×4, `TestOverallStatusPresentationCrashConsistency` ×2,
  `TestFilteringNeverChangesGlobalHealthState` ×2,
  `TestLongEntityListRendering` ×2, `TestSchemaGateRunsBeforeExport` ×1,
  `TestDuplicateFindingsPassthrough` ×1, `TestRuleFilterValidation` ×3,
  `TestJsonErrorModes` ×3, plus the strengthened `test_l_wrong_db_isolation`
  and renamed autoflush test in place).
- Full pytest: see final validation run recorded at commit time - expected
  2023 (post-implementation baseline) + 21 new = 2044, zero regressions.
- `py_compile`: clean. `git diff --check`: clean.

## 11. Limitations / explicitly deferred

- No `--fix`/repair/disposition workflow (FHC5, not built).
- No score or ranking of findings — text-classified only.
- Filtering is exact-match only (`--rule`, `--classification`); no
  fuzzy/partial matching.
- JSON mode's schema is intentionally narrow (findings + counts + status +
  `presentation_check_executed` flag) and not yet versioned — should gain
  an explicit schema version field if consumed by another system later.
- No historical/trend tracking between runs — each invocation is a fresh,
  independent snapshot.
