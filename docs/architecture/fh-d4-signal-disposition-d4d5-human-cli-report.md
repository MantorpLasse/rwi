# FH-D4 Signal Disposition — D4D5: Human Disposition CLI — Report

D4D5 of docs/architecture/fh-d4-signal-disposition-design.md's own §19
slice table (the human disposition-recording CLI, deferred from D4D4).
Implementation only — no commit, no push, no real database access, no
D4D6 (real migration) work — per this mission's own explicit stop
boundary.

## 1. Files read fresh

`fh-d4-signal-disposition-design.md`, the D4D1/D4D2/D4D3/D4D4 reports,
`app/services/signal_disposition_persistence.py`,
`app/services/signal_disposition_resolution.py`,
`app/services/fh_d4_disposition_resolution.py`,
`scripts/migrate_signal_disposition_d4d2.py`,
`scripts/review_reconciliation_item.py` (the explicit safety-philosophy
precedent), `scripts/run_data_health_check.py`.

## 2. Exact files created

- `scripts/review_signal_disposition.py`
- `tests/test_review_signal_disposition.py` (49 tests at implementation
  time; 63 after the critical-review checkpoint below)
- `docs/architecture/fh-d4-signal-disposition-d4d5-human-cli-report.md`
  (this file)

**Note**: this report's earlier sections (§4-§34) describe the
implementation-time shape of the CLI, retained unedited as originally
written. The critical-review checkpoint appended at the end of this
report documents two additions made during review (an explicit output
mode label, and an honestly-documented concurrency/TOCTOU boundary) plus
14 new regression tests closing real test-coverage gaps.

## 3. Exact files modified

None. D4D1-D4D4, the CLI (`run_data_health_check.py`), and the pure FH-D4
detector are all untouched.

## 4. CLI/API contract

```python
def run_review(config: SignalDispositionReviewConfig) -> SignalDispositionReviewResult: ...
def render_result(result: SignalDispositionReviewResult) -> str: ...
def check_schema_readiness(database: Path) -> dict: ...
def build_engine(database: Path, *, writable: bool): ...

@dataclass(frozen=True)
class SignalDispositionReviewConfig:
    database: Path
    signal_ids: tuple[int, ...] = ()   # empty = overview mode
    decision: str | None = None         # "DISTINCT" | "SAME_REAL_WORLD_EFFORT"
    reviewer: str | None = None
    reason: str | None = None
    allow_database_write: bool = False
```

One testable entrypoint (`run_review()`), mirroring
`scripts/review_reconciliation_item.py`'s own `run_review()`/`ReviewConfig`/
`ReviewResult` shape exactly — CLI argument parsing (`_parser()`, `main()`)
is fully separate from orchestration, and both `main()` and the test suite
consume the same `run_review()`/`render_result()` functions, so there is
no parallel/duplicated code path.

**Two deliberate departures from the `review_reconciliation_item.py`
precedent**, both documented at length in the module's own top-of-file
docstring and summarized in §16/§14 below: no confirmation-fingerprint
handshake (a `SignalDisposition` has no fingerprint field, and this
project's own D4D1/D4D3 design explicitly rejects fingerprinting for this
domain), and a mandatory "target must be a CURRENT raw FH-D4 group" check
that `review_reconciliation_item.py` has no equivalent of.

## 5. Invocation tiers

- **Overview** (`signal_ids` empty): read-only list of every current
  attention-required group plus resolved groups, shown separately. Never
  writes.
- **Targeted inspect** (`signal_ids` given, `decision` omitted): full
  disposition-aware state for exactly that Signal set. Never writes.
- **Dry-run** (`signal_ids` + `decision` given, `allow_database_write`
  omitted): computes eligibility and the exact planned disposition
  (including `planned_supersedes_id`). Never writes.
- **Write** (`allow_database_write=True`): re-verifies eligibility against
  a fresh, second read immediately before persisting; records exactly one
  `SignalDisposition` (+ member rows) via `record_signal_group_disposition()`;
  one commit.

## 6. Schema-gate verdict

Reuses `scripts.migrate_signal_disposition_d4d2.inspect()` verbatim via
`check_schema_readiness()` — never reimplemented, never a migration-
execution import anywhere. Refuses with
`SIGNAL_DISPOSITION_SCHEMA_MIGRATION_REQUIRED` (a structured `blockers`
tuple, never an uncaught exception) before FH-D4 detection, D4D4
resolution, or any write planning runs. Verified: `TestSchemaGate` (5
tests) — missing schema blocks overview/targeted-inspect/write alike; a
migration-created schema (`migrate_signal_disposition_d4d2.upgrade()`, not
`create_all()`) is correctly reported ready.

## 7. Target-group identity verdict

`--signal-id` is repeatable; at least 2 distinct ids required (reuses
`app.services.signal_disposition_persistence.MINIMUM_GROUP_CARDINALITY`,
never a locally redefined constant). Normalized via `tuple(sorted(set(...)))`
— the identical canonical form D4D1/D4D3 already use, so a read query and
a write always agree on shape. Duplicate/reversed input ids collapse to
the same canonical target. Verified: `TestTargetedInspect`.

## 8. Current-group validation verdict

Every targeted call runs `run_disposition_aware_fh_d4_review()` fresh and
searches all four D4D4 primary buckets for an exact `signal_ids` match. No
match → `TARGET_GROUP_NOT_A_CURRENT_FH_D4_GROUP` blocker; the CLI can
never disposition an arbitrary, detector-unrelated Signal set. Verified:
`test_target_not_a_current_group_rejected`.

## 9. `attention_required` validation verdict

The current-group search spans ALL four buckets (not merely
`attention_required`), so an already-`confirmed_distinct`/
`confirmed_same_effort` target can still be inspected/targeted (needed for
idempotency detection, §17) — but a decision-bearing action against an
already-resolved group with the SAME decision is refused as a duplicate
(§17), and against an ambiguous group is refused outright (§18/§19).

## 10. Decision-vocabulary verdict

`argparse` `choices=SIGNAL_DISPOSITION_DECISIONS` (D4D1's own tuple,
imported, never redefined) — exactly `DISTINCT`/`SAME_REAL_WORLD_EFFORT`.
No `MERGE`/`DELETE`/`CANONICAL`/`IGNORE`/`SUPPRESS`/`DUPLICATE_SIGNAL`
value exists anywhere in this file.

## 11. Reviewer/reason verdict

Both required whenever `decision` is supplied — at DRY-RUN time too, not
merely at write time (a deliberate departure from
`review_reconciliation_item.py`'s own narrower precedent — see the module
docstring's own reasoning: this mission's own §13 requires dry-run output
to preview the exact planned reviewer/reason, which requires them as
input). Blank/whitespace-only values rejected. Unicode round-trips exactly
(`test_unicode_reviewer_reason_round_trip`, Swedish reviewer/reason).

## 12. Inspect verdict

Read-only engine (`build_engine(writable=False)`, SQLite's own `mode=ro`
URI) whenever `allow_database_write` is not set — even a coding mistake
that tried to write is refused at the driver level. Instrumented: zero
INSERT/UPDATE/DELETE for both overview and targeted inspect
(`test_overview_is_read_only`, `test_inspect_uses_readonly_engine`).

## 13. Dry-run verdict

Shows exact Signal ids, raw FH-D4 summary, current status, latest
disposition (if any), related/stale history, `independent_root_count`,
planned decision/reviewer/reason, and `planned_supersedes_id` — never
writes (verified via SQL instrumentation and direct row-count checks).

## 14. Re-read-before-write verdict

`run_review()`'s write branch calls `run_disposition_aware_fh_d4_review()`
a SECOND time, immediately before `record_signal_group_disposition()`, and
compares the freshly-found group against the one used for eligibility
planning — any difference (including the group disappearing entirely)
refuses the write with `STATE_CHANGED_BEFORE_WRITE`, zero rows persisted.
Verified directly by monkeypatching the resolution call to return a
mutated result on its second invocation
(`TestReReadBeforeWrite::test_state_changed_between_reads_refuses_write`,
`test_group_disappears_between_reads_refuses_write`) — proving the guard
is a real, exercised code path, not merely an inferred consequence.

## 15. Stale-between-read/write verdict

Additionally verified with NO monkeypatching, across two real, separate
`run_review()` invocations: a dry-run against `{A,B}`, a real DB mutation
growing the group to `{A,B,C}` (or removing a member), then a real write
invocation against the original `{A,B}` — refused via
`TARGET_GROUP_NOT_CURRENT` automatically, since every invocation
independently recomputes current state from scratch (there is no cached
dry-run state a later invocation could ever trust). Verified:
`test_natural_cross_invocation_staleness_group_grew`,
`test_natural_cross_invocation_staleness_group_disappeared`.

## 16. Supersession-policy verdict

`UNREVIEWED` target → `supersedes_id=None`. A resolved, non-ambiguous
target requesting a DIFFERENT decision → supersedes its own single latest
exact-set disposition (`group.latest_disposition_id`). Ambiguous target
(`independent_root_count > 1`) → refused outright, never guesses which
competing root to supersede (§16's own explicit "fail closed" preference,
honored literally). Verified: `TestIdempotencyAndReReview::
test_changed_decision_supersedes_latest` (asserts the new header's own
`supersedes_id` equals the prior latest disposition's id).

## 17. Same-decision idempotency verdict

Requesting the SAME decision the target's latest exact disposition already
records is refused with `ALREADY_CONFIRMED_CURRENT_DECISION` — no new row.
Verified for both write attempts and via row-count assertion
(`test_same_decision_again_refused_no_new_row`).

## 18. Changed-decision re-review verdict

A different decision is treated as a genuine re-review and correctly
supersedes the prior latest disposition (§16 above) — verified end-to-end,
including the resulting two-row history with the correct `supersedes_id`
linkage.

## 19. Ambiguous-group policy verdict

Inspect/dry-run of an ambiguous target remain fully available (a human can
always see the competing-root state); any decision-bearing action
(dry-run's own eligibility check, and write) is refused unconditionally
with `AMBIGUOUS_HISTORY_REQUIRES_EXPLICIT_RESOLUTION` — this CLI never
silently picks one competing root to supersede. No override flag is
offered in this slice (this mission's own explicit "do not add it unless
genuinely needed" honored — see §33 "Defects/design ambiguities found"
for the deferred-scope note). Verified: `TestAmbiguousGroups` (3 tests) —
visible in inspect, dry-run refused, write refused with zero new rows.

## 20. DISTINCT-write verdict

Exactly one `SignalDisposition` header (decision=`DISTINCT`) + one
`SignalDispositionMember` row per target Signal id, via
`record_signal_group_disposition()` only — no duplicate persistence logic
in this CLI. Verified: `TestDistinctWrite` (row-count assertions).

## 21. SAME-write verdict

Identical to DISTINCT except `decision="SAME_REAL_WORLD_EFFORT"`. No
canonical Signal field anywhere on the result or the persisted row; no
Signal mutation, no unpublish, no provenance movement (see §24/§25).
Verified: `TestSameWrite`.

## 22. Transaction verdict

Exactly one writable session per write invocation; the two disposition-
resolution reads plus the single `record_signal_group_disposition()` call
all happen inside it; exactly one `session.commit()`; any exception
anywhere triggers `session.rollback()` and re-raises. Verified directly by
monkeypatching `record_signal_group_disposition` to perform its real
flush-based insert and then raise — proving the flushed-but-uncommitted
rows are fully rolled back, zero durable state
(`TestTransactionBoundary::test_failure_after_flush_before_commit_rolls_back_completely`).

## 23. Wrong-DB verdict

A `protected.db` with its own real FH-D4 group is byte-identical
(SHA-256) before and after running inspect/dry-run/write against a
completely separate `target.db` — no global DB leakage, no
`app.database.SessionLocal`, no process-global engine anywhere in this
module. Verified: `TestWrongDbSafety`.

## 24. Signal-immutability verdict

Every column of every target Signal row snapshotted before and after a
successful write — byte-identical (`TestSignalImmutability`, full
column-dict comparison, not merely the columns this mission happened to
name).

## 25. Provenance-immutability verdict

`Source`/`SourceAssertion`/`ReviewerAction` rows snapshotted before and
after a successful write — unchanged, and zero `ReviewerAction` rows
exist at all (this CLI never imports or calls
`record_reviewer_action()`). Verified: `TestProvenanceImmutability`.

## 26. Group-disappears verdict

After a disposition is recorded, giving one member Signal a runway claim
(so FH-D4 no longer includes it — never a Signal delete, which the D4D1
FK would refuse anyway once a Signal is a disposition member) removes the
group from both the overview's `attention_required` AND
`confirmed_distinct` — current detection drives the operational view, not
persisted history; the disposition itself remains queryable directly
against `SignalDisposition` (row count unchanged). Verified:
`TestGroupDisappearsAfterDecision`.

## 27. Group-grows verdict

After a `DISTINCT {A,B}` write, adding a third Signal to the same airport
(no runway) makes the CURRENT FH-D4 group `{A,B,C}` — the overview now
shows `{A,B,C}` as `attention_required` with `related_history` pointing at
the old `{A,B}` (`relation="SUBSET"`), and `{A,B}` no longer appears in
`confirmed_distinct` at all (it is not a current group). A future decision
naturally targets the new exact set. Verified:
`TestGroupGrowsAfterDecision`.

## 28. Deterministic-output/result verdict

Repeated read-only calls with identical input produce field-equal
`SignalDispositionReviewResult` objects (dataclass equality); `render_result()`
is a pure function of its `result` argument, producing identical text
across repeated calls. Verified: `TestDeterministicOutput`.

## 29. No-auto-decision verdict

AST-verified: zero references to any forbidden Signal content attribute
(`title`/`notes`/`category`/`confidence`/`status`/`published`/any
financial field/etc.), and the source contains no scoring/ranking/text-
similarity logic of any kind — `decision` is passed through verbatim from
`config.decision` at the one call site, confirmed both by AST source
inspection and a behavioral test proving a deliberately misleading raw
FH-D4 summary never changes what gets persisted. Verified:
`TestNoAutoDecision` (3 tests).

## 30. Information-firewall verdict

The CLI displays `raw_finding.summary` (already-emitted FH-D4 text) for
human context only; `record_signal_group_disposition()` receives exactly
`signal_ids`/`decision`/`reviewer`/`reason`/`supersedes_id` — no scoring,
no ranking, no content-derived field. Same firewall discipline D4D3/D4D4
already established, reused, not reinvented.

## 31. Schema-failure verdict

Missing D4D2 tables refuse via the structured `blockers` field for every
tier (overview/targeted-inspect/write) — never a raw SQLAlchemy stack
trace for this expected, common case (§34 of the mission, verified
directly).

## 32. Detector/resolution-failure verdict

No `try`/`except` exists anywhere in this module (matching D4D3/D4D4's own
"fail loud" discipline) — a genuine FHC3/D4D4 query failure (e.g. a
corrupted schema beyond the gate's own coverage) propagates as an ordinary
uncaught exception; there is no code path that could convert an
operational failure into a fabricated "all active" or "all resolved"
result, and no write occurs on any exception path (the write itself is
wrapped in `try/except Exception: session.rollback(); raise`, which
re-raises rather than swallowing).

## 33. Defects/design ambiguities found

**No defect found.** Two genuine design questions were investigated and
resolved with documented, narrow decisions rather than guessed:

- **Confirmation-fingerprint handshake**: considered and explicitly
  rejected in favor of "every invocation independently recomputes fresh
  state" plus an intra-write-flow re-read — see the module's own
  top-of-file docstring, point 1, and §14/§15 above for the full
  reasoning and the resulting test coverage proving this closes the same
  gap a fingerprint handshake would have, without introducing a field
  concept (`fingerprint`) this project's own D4D1/D4D3 design explicitly
  rejected for this domain.
- **Ambiguous-group override flag**: NOT implemented in this slice, per
  this mission's own explicit "do not add it unless genuinely needed."
  Deliberately deferred as an explicit, separate, future decision — if a
  real operational need for resolving genuine competing-root ambiguity
  through this CLI ever arises, it should be its own reviewed slice
  (narrow, explicit, auditable), not a speculative addition here.

## 34. Corrections made

Three test-authoring corrections during this slice's own development (not
affecting production code): (1) `Source`/`SourceAssertion` test fixtures
initially used non-existent column names (`name` instead of `title`;
missing `source_type`/`assertion_type`/`source_record_identifier`) —
corrected against the real model definitions and an existing test file's
own working fixture. No production-code defect was involved.

## 35. Focused tests

`tests/test_review_signal_disposition.py` (49) +
`tests/test_fh_d4_disposition_resolution.py` +
`tests/test_signal_disposition_resolution.py` +
`tests/test_signal_disposition_persistence.py` +
`tests/test_signal_disposition_migration.py` +
`tests/test_run_data_health_check.py` +
`tests/test_review_reconciliation_item.py`: **413 passed**, 0 failed.

## 36. Full pytest

Baseline 2298 (D4D4 checkpoint) + 49 new D4D5 tests = **2347 passed**, 0
failed, 0 regressions (269.41s) — exact match to the expected delta.

## 37. py_compile

Clean on `scripts/review_signal_disposition.py` and
`tests/test_review_signal_disposition.py`.

## 38. `git diff --check`

Clean (exit 0, no whitespace errors).

## 39. Explicit real-DB no-access proof

Every test uses an isolated `tmp_path`-scoped SQLite database file — never
`data/runway_safe.db`. Verified both by direct code inspection and a
dedicated AST-based test (`TestNoRealDatabaseAccess`) confirming no
literal reference to the real database filename appears anywhere in the
module's own compiled source; `--database` has no default anywhere in the
argument parser (`test_no_default_database_argument` confirms `argparse`
itself refuses to run without it). `data/runway_safe.db`'s SHA-256/size/
mtime were captured immediately before and after the full pytest run for
this slice and matched the checkpoint established at the start of this
session. No real disposition was created anywhere in this task.

## 40. Conclusion (implementation-time)

D4D5 implements the human disposition-recording CLI exactly per this
mission's own scope — mirroring `review_reconciliation_item.py`'s proven
three-tier safety philosophy, adapted to FH-D4 Signal groups with two
deliberate, documented departures (no fingerprint handshake, mandatory
current-group validation). No defect was found; two genuine design
questions (confirmation mechanism, ambiguity override) were resolved by
investigation and explicit, narrow decisions rather than guessed. Ready
for its own adversarial D4D5 review checkpoint before D4D6 (real database
migration, not yet authorized) begins.

---

## Critical Review Checkpoint (D4D5, post-implementation)

Per this project's own standing two-phase discipline, the implementation
report above was **not trusted**. A fresh adversarial review was performed
against the actual committed code and actual instrumented behavior, per a
58-section review mission. Two genuine gaps were found and closed — one a
real usability/observability gap, one an honesty-of-documentation gap
around a real (small, accepted) concurrency boundary; no functional
correctness defect was found in the write path itself. Every CRITICAL
item the review flagged (stale-history supersession, ambiguity-write
refusal, re-read-before-write, exact-set targeting) was independently
re-derived from the code and confirmed correct, several for the first
time with a directly-executed regression test rather than inference.

### Gap 1 (CONFIRMED, fixed): no explicit mode label in human output

Mission §33 explicitly requires the operator to "clearly see: READ ONLY /
DRY RUN / WRITE" at a glance. The original `render_result()` distinguished
these implicitly (by which fields were populated) but never stated the
mode outright. **Fix applied**: a new `_mode_label()` helper computes
`"INSPECT (read-only)"` / `"DRY RUN (no write)"` / `"WRITE (committed)"`
from the result's own `written`/`proposed_decision` fields, printed as the
second line of every non-blocked rendered result. **Regression tests
added** (`TestOutputModeLabel`, 4 tests): one per mode, including the
overview (no-target) case.

### Gap 2 (CONFIRMED, documented): TOCTOU window between the pre-write re-read and commit — honestly assessed, not silently assumed

Investigated mission §10's own explicit challenge: after the write path's
own fresh, pre-write re-read confirms the target group's state, a narrow
window remains before `session.commit()` during which `record_signal_
group_disposition()` itself performs no re-check against a concurrent
writer (it validates Signal existence and `supersedes_id` target validity,
never "has another process just written a competing disposition for this
exact set"). Two genuinely concurrent operators targeting the identical
exact Signal set could, in principle, both pass their own independent
fresh re-read and both successfully commit, producing exactly the
independent-competing-roots shape D4D3/D4D4 already detect (as
`ambiguous_groups`) and this CLI's own ambiguity refusal already protects
the *next* decision-bearing action against.

**Decision**: per the mission's own explicit alternative ("if that is the
chosen boundary, ensure it is honest... do not overengineer... do not
build distributed locking unnecessarily"), this window is NOT closed with
cross-process locking — for a low-frequency, human-paced review CLI, that
would be real over-engineering against a small, already-mitigated risk
(the worst outcome is an extra, visible `ambiguous_groups` entry a future
decision must explicitly resolve, never silently corrupted or
mis-superseded history). **Fix applied**: this exact boundary is now
explicitly documented in the module's own top-of-file docstring (a new
"CONCURRENCY / OPERATIONAL ASSUMPTION" section) rather than left as a
silent, undocumented assumption — stating plainly that this CLI assumes a
single human reviewer operates against a given database at a time, and
what the safe failure mode is if that assumption is ever violated. No
functional code change; a synthetic multi-threaded concurrency test was
deliberately NOT added, per the mission's own "do not overengineer"
instruction — SQLite's own file-locking behavior under genuine concurrent
writers is well-established platform behavior, not something this
project's own test suite needs to re-prove.

### Sections re-verified with dedicated new regression tests (not merely re-read)

- **§17/§18 stale-history supersession (CRITICAL)**: re-derived from the
  code (confirmed `_evaluate_eligibility()` only ever supersedes
  `group.latest_disposition_id`, which D4D3's own exact-match resolution
  guarantees is never a subset/superset disposition) and given its own
  explicit, end-to-end regression tests for BOTH directions -
  `test_grown_group_decision_never_supersedes_old_narrower_disposition`
  and the new `TestGroupShrinksAfterDecision::
  test_shrunk_group_decision_never_supersedes_old_wider_disposition` -
  both asserting the OLD disposition's own `supersedes_id` is untouched
  and the NEW disposition's own `supersedes_id` is `None`, not merely that
  a write succeeded.
- **§14/§44 "re-read test mocking the same object twice"**: the original
  `test_state_changed_between_reads_refuses_write` used
  `dataclasses.replace()` to simulate a changed second read - a legitimate
  but synthetic technique. Strengthened with a genuinely non-mocked
  companion test,
  `test_real_mid_call_mutation_via_separate_connection_refuses_write`,
  which commits a REAL write via a completely separate engine/connection
  to the same file database between the two `run_disposition_aware_fh_d4_review()`
  calls inside one `run_review()` invocation, proving the guard against
  genuine state divergence, not merely a crafted mock.
- **§22 commit-count not asserted**: the original transaction tests proved
  rollback-on-failure but never asserted commit COUNT on the success path.
  Added `TestCommitCount` (2 tests) using SQLAlchemy's own `Engine`-level
  `"commit"` event (the correct instrumentation point - `before_cursor_
  execute` does not reliably observe `sqlite3`'s own DBAPI-level
  `connection.commit()`, confirmed by first attempting SQL-statement
  sniffing and finding it silently captured zero commits): exactly one
  commit on a successful write, exactly zero on an idempotent refusal.
- **§4 argument-contract edge cases**: added `TestArgumentContractEdgeCases`
  (4 tests) - an out-of-vocabulary decision rejected even when
  `SignalDispositionReviewConfig` is constructed directly (bypassing
  `argparse`'s own `choices=` restriction), `argparse` itself rejecting an
  unknown `--decision` value, `--signal-id` repeatability confirmed
  directly, and a duplicate-ONLY id set (`--signal-id 7 --signal-id 7`,
  collapsing to a single distinct id after normalization) correctly
  rejected as below the minimum cardinality - not silently accepted.
- **§36 no-auto-decision AST test "too weak"**: the original test was a
  plain substring search (`"decision=config.decision" in source`), which
  could not distinguish a genuine keyword argument from a coincidental
  string match. Replaced with a real AST inspection
  (`test_decision_comes_only_from_config_ast`) that locates the exact,
  single call site for `record_signal_group_disposition()`, extracts its
  `decision=` keyword argument's AST node, and asserts it is literally the
  attribute expression `config.decision` - not a string literal, ternary,
  or any other derived value.
- **§42 wrong-DB "content proof"**: strengthened beyond a hash-only check -
  the CLI's own returned result is now asserted to reflect TARGET's own
  Signal ids (never protected's), and protected's own `signal_dispositions`
  row count is independently confirmed still zero, alongside the original
  byte-identical file hash.
- **§43 migration-schema parity for the full write path**: the original
  migration-created-schema test only exercised inspect/stale-context
  reads. Added `TestMigrationCreatedSchemaFullWritePath` - a genuine
  `DISTINCT` write against a `migrate_signal_disposition_d4d2.upgrade()`
  -created database (never `create_all()` for the two disposition
  tables), with row-count verification after the write.

### Sections re-verified with no defect and no new test needed (already adequately covered)

§3 CLI boundary (confirmed: `record_signal_group_disposition()` is the
sole write call, one call site, AST-verified in this checkpoint too) ·
§5-§8 schema gate / DB isolation / exact-target / member-id-source
(re-derived from the code, unchanged, already well-covered) · §9 current-
group revalidation (the write path's own second read is the mechanism;
already directly tested, strengthened further above) · §11-§12 same/
changed-decision (already covered end-to-end) · §13 ambiguous-group write
refusal (already covered: `test_ambiguous_group_write_refused_no_new_row`)
· §15 `attention_required` contract (confirmed the target search spans all
four D4D4 buckets, not merely `attention_required`, so idempotency against
already-resolved groups works correctly) · §16 supersedes-target source
(confirmed by construction - `group.latest_disposition_id` can only ever
be an exact-set disposition, since it comes from D4D3's own exact-match
resolution) · §19-§21 read-only tiers / write authorization (unchanged,
re-run) · §25-§27 Signal/provenance immutability, exact row shape
(unchanged, re-run) · §29-§31 pair/triple/quintuple, group-disappears/
grows (unchanged, re-run) · §34 error output (confirmed: every expected
blocker is a structured field, never a raised exception) · §35 JSON mode
(confirmed absent, correctly deferred, not added) · §37-§39 information
firewall / no static export / schema-before-detector (unchanged,
re-confirmed by direct code reading) · §40 D4D4-failure / §41
persistence-failure propagation (no `try`/`except` exists anywhere except
the write path's own documented rollback wrapper, confirmed by direct
source reading).

### Final validation

- `tests/test_review_signal_disposition.py`: **63 passed** (49 original +
  14 new: 4 mode-label, 2 stale-supersession growth/shrink, 1 real-
  mutation re-read, 2 commit-count, 4 argument-contract, 1 migration-
  write-path; the wrong-DB content-proof strengthening extended an
  existing test rather than adding a new one).
- Focused: D4D4 + D4D3 + D4D1 + D4D2 + `run_data_health_check`/
  `review_reconciliation_item` tests: **427 passed**.
- Full `pytest`: baseline 2347 (pre-review) + 14 new tests = **2361
  passed**, 0 failed, 0 regressions (454.58s) — exact match to the
  expected delta.
- `py_compile` clean on both changed Python files.
- `git diff --check` clean (exit 0).
- `data/runway_safe.db` SHA-256/size/mtime confirmed unchanged
  (`4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
  1,794,048 bytes) before and after this entire review.

### Conclusion (post-review)

No functional correctness defect was found in the write path, the
targeting contract, the ambiguity policy, or the supersession policy -
every CRITICAL item the review mission named was either already correctly
implemented (and is now backed by a directly-executed regression test
rather than inference alone) or, in the one case where a real, small,
already-mitigated concurrency boundary exists (§10), the honest resolution
is documentation, not new locking machinery this low-frequency human tool
does not need. One real usability gap (missing explicit mode label in
output) was found and fixed. D4D5 is sound for commit.
