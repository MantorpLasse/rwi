# R4E: Reviewer-Action CLI for Existing-Signal Reconciliation — Report

Slice R4E of
[existing-signal-reconciliation-r4-human-resolution-design.md](existing-signal-reconciliation-r4-human-resolution-design.md)
(Section 12 "Reviewer presentation," Section 20 "Recommended implementation
slices"). This slice provides the first explicit, committed write path for
a human reviewer to resolve an existing-Signal reconciliation block:
`scripts/review_reconciliation_item.py`, a narrow, fail-closed CLI built
entirely on already-reviewed R1/R2/R3/R4A/R4B/R4C/R4D functions — no
production module in that chain was modified.

## 1. Starting HEAD

`060891f30abb8c3fd64d1ff80b9205360ad2e204`, confirmed matching `origin/main`
before any change. Baseline: 1605 passed.

## 2. CLI/API

New file: `scripts/review_reconciliation_item.py`. Three invocation tiers,
all through one function, `run_review(config: ReviewConfig) -> ReviewResult`
(the same "one function does the work, `main()` and tests both consume it"
convention every prior script in this pipeline uses):

1. **Pure inspection** (`--database X --source-assertion-id N`, no
   `--action`): shows current `SourceAssertion` state, latest
   `ReviewerAction`, and a fresh R1/R2/R4A reconciliation recomputation.
   Never writes.
2. **Dry-run of a specific action** (adds `--action X`, no
   `--allow-database-write`): additionally validates whether `X` is
   *currently* eligible and, for `CONFIRM_DISTINCT_SIGNAL`, shows the exact
   fingerprint a subsequent write would need. Still never writes.
3. **Write** (adds `--allow-database-write`, plus `--reviewer`/`--reason`,
   and for `CONFIRM_DISTINCT_SIGNAL` a matching `--confirm-current-plan`, or
   for `MARK_DUPLICATE` a `--duplicate-of-signal-id`): recomputes
   reconciliation one final time, re-validates, and only then records
   exactly one `ReviewerAction` (plus, for `MARK_DUPLICATE`, the existing
   duplicate-link service).

`VALID_ACTIONS = ("CONFIRM_DISTINCT_SIGNAL", "MARK_DUPLICATE", "DEFER",
"NEEDS_MORE_EVIDENCE", "REJECT_SIGNAL")`. `APPROVE_SIGNAL` is deliberately
**not** offered — it is the precondition this CLI's own reconciliation
actions assume already happened via the separate, existing governed-review
path, not something a reconciliation-focused tool should also grant.

## 3. Read/dry-run behavior

Every invocation — including a real write, immediately before it — loads
the `SourceAssertion`, its latest `ReviewerAction`, and runs fresh
`build_reconciliation_subject()`/`find_reconciliation_candidates()`/
`evaluate_existing_signal_reconciliation()` (R1/R2), and, only when blocking,
`build_reconciliation_review_plan()`/`compute_reconciliation_fingerprint()`
(R4A) — identical calls, identical `category=None`/`reference_year=None`/
empty-`claims` human-selected-context boundary R4D already established, so
this script's own fingerprint is always the same one a human would have
seen in the R4D queue view for the identical state, never a second,
drifting computation. No anchor rule, candidate SQL, canonicalization, or
hashing logic is reimplemented (AST/grep-verified,
`test_no_local_fingerprint_or_hashing_code`).

## 4. Schema readiness

`check_schema_readiness()` reuses, verbatim, the same three `inspect()`
functions R4D's own schema-gate fix already established (Slice 4, Slice 7,
R4B's `reconciliation_fingerprint`), refusing with
`RECONCILIATION_REVIEW_SCHEMA_MIGRATION_REQUIRED` before any ORM query if
any is missing. **Proven live against the real database in this task**:
`run_review()` against `data/runway_safe.db` itself (read-only, no write
flag) returns this exact blocker, zero exceptions, zero writes — not merely
inferred from a disposable-DB reproduction.

## 5. `CONFIRM_DISTINCT_SIGNAL` flow

Requires fresh `reconciliation_outcome == POSSIBLE_EXISTING_SIGNAL_MATCH`.
Displays candidate ids, anchor reasons, and the current fingerprint. The
persisted `reconciliation_fingerprint` is **always** the value this script
itself just recomputed — never a value read from `--confirm-current-plan`
or any other CLI input (verified directly,
`test_fingerprint_cannot_be_overridden_by_caller`). There is no
`--fingerprint` argument that gets persisted at all.

## 6. Fingerprint handshake

`--confirm-current-plan <value>` is the human-facing handshake: required
only for the actual write (not the dry-run, since the human hasn't seen the
fingerprint yet at that point), and must exactly equal the freshly
recomputed fingerprint — string equality, no normalization — or the write
is refused with `CONFIRMATION_FINGERPRINT_MISMATCH` before any
`ReviewerAction` is constructed. This directly implements the two-step
pattern the mission itself proposed and this task's own critical evaluation
confirmed was the safer of the two options considered (accepting no
fingerprint input at all would remove the human's own explicit "yes, this
is the state I reviewed" confirmation step; a bare `--yes` interactive
prompt was rejected per the mission's own explicit instruction against
prompts as the *only* safety boundary — the recomputation-and-comparison is
the real boundary; the prompt-equivalent handshake is an additional,
non-load-bearing UX safeguard).

## 7. Stale detection

Attacked directly: a candidate added, a candidate removed, an anchor reason
changed (same candidate, different anchor), and governed provenance changed
— all between an initial dry-run and a subsequent write attempt using that
dry-run's own fingerprint — every one produces
`CONFIRMATION_FINGERPRINT_MISMATCH`, zero writes, zero partial state.

## 8. Current-confirmation idempotency

If the latest `ReviewerAction` is already `CONFIRM_DISTINCT_SIGNAL` with a
fingerprint that still matches the current blocking plan,
`CONFIRM_DISTINCT_SIGNAL` is refused with `ALREADY_CONFIRMED_CURRENT_PLAN`
— no duplicate row, no write, deterministic (§13 of the mission, verbatim
marker name).

## 9. `MARK_DUPLICATE` flow

Requires fresh `POSSIBLE_EXISTING_SIGNAL_MATCH` and an explicit
`--duplicate-of-signal-id` that must already be a member of the current
`candidate_signal_ids` — stricter than the generic
`record_reviewer_action()`, deliberately, matching the mission's own
explicit instruction. On write: `record_reviewer_action(..., action=
"MARK_DUPLICATE", ...)` then the existing, unmodified
`link_source_assertion_to_duplicate_signal()` — both existing services,
never a direct ORM field assignment, one commit after both succeed.

## 10. Candidate-target validation

An arbitrary Signal that is not part of the current blocking set is
rejected with `DUPLICATE_TARGET_NOT_A_CURRENT_CANDIDATE`
(`test_target_must_be_a_current_blocking_candidate`), including the
specific adversarial case where an *advisory-only* (non-blocking) Signal
exists at the same airport and is deliberately targeted —
`CLEAR_TO_CREATE` refuses `MARK_DUPLICATE` entirely
(`NO_BLOCKING_PLAN`) before the candidate-membership check is even reached,
so advisory similarity can never be converted into a duplicate-identity
resolution through this CLI.

## 11. Supersession

`supersedes_action_id` is always the current latest action's own id (or
`None` if none exists) — read fresh in the same call that performs the
write, never a value passed in from outside. Cross-`SourceAssertion`
supersession cannot occur through this script by construction (the id
always belongs to the same assertion being acted on).
`test_stale_old_confirmation_allows_new_append_only_confirmation` proves
the full `APPROVE_SIGNAL → CONFIRM_DISTINCT_SIGNAL(F1) →
CONFIRM_DISTINCT_SIGNAL(F2)` history: F1's own row is never mutated, F2's
`supersedes_action_id` points at F1's id.

## 12. Transaction behavior

One target-specific engine per invocation (`build_engine()`), never
`app.database.SessionLocal`. When not writable
(`--allow-database-write` absent), the engine is opened in SQLite's own
read-only URI mode — the identical driver-level guarantee R4D's own CLI
established — so even a coding mistake could not write. For a write, the
full sequence (`record_reviewer_action()`, and for `MARK_DUPLICATE`,
`link_source_assertion_to_duplicate_signal()`) happens inside one
`try`/`except`/`session.commit()`/`rollback()` block: any failure —
including a governance-gate rejection from `record_reviewer_action()`
itself, verified directly with a degraded-governance fixture,
`test_failed_action_leaves_zero_partial_write` — rolls back to zero
partial state before propagating. Neither service itself commits;
this script is the sole transaction owner.

**No backup**: unlike every migration script in this project, R4E does not
back up the database before writing. This is a deliberate, documented
choice (see the script's own module docstring) — backups here are reserved
for irreversible schema changes; this script only ever appends at most one
`ReviewerAction` row (fully reversible via a further superseding action,
the same append-only discipline this whole pipeline already relies on
instead of file-level backups for ordinary data writes).

## 13. Wrong-DB safety

`--database` has **no default value** in this script (`required=True`) —
a deliberate divergence from every read-only script in this project
(including R4D's own CLI, which may safely default since it can never
write): this is the first write-capable CLI this project has shipped, so
every invocation, read or write, must name its target explicitly.
`test_only_the_target_database_is_touched` confirms a `target.db`/
`protected.db` pair: acting on `target.db` leaves `protected.db`
byte-identical.

## 14. No-Signal/publication behavior

`create_signal_from_approved_review()` is never imported (AST-verified,
`test_no_create_signal_from_approved_review_import`). `CONFIRM_DISTINCT_SIGNAL`
creates zero Signals (`test_confirm_distinct_creates_no_signal`).
`MARK_DUPLICATE` links to an *existing* Signal only, leaving its own
`published` flag and every other field untouched
(`test_target_signal_published_flag_untouched`). No `published` parameter
exists anywhere in this script's own surface.

## 15. Financial/title firewall

Behaviorally verified: changing an existing candidate's
`estimated_total_value_usd` and `title` between two calls produces
byte-identical `current_fingerprint` and `action_eligible`
(`test_money_and_title_never_affect_fingerprint_or_eligibility`).
AST-verified: none of the four R1/R2/R4A call sites this script makes ever
pass a financial or title-shaped argument.

## 16. International readiness

A non-US, non-USD case (Haneda Airport, a Japanese vendor name) writes
successfully with Unicode `--reviewer`/`--reason` values, preserved
byte-for-byte in the persisted row
(`test_non_us_international_case_identical_workflow`). The script's own
source names no MAC/MSP/FAA/Runway Safe/USAspending/Granicus token
anywhere.

## 17. MSP result

**Synthetic** (never the real database in the test suite):
`test_msp_shaped_resolved_duplicate_refuses_further_reconciliation_action`
reproduces the real MSP #222 shape (`APPROVE_SIGNAL` → `MARK_DUPLICATE` →
Signal #67, linked) and confirms `CONFIRM_DISTINCT_SIGNAL` is refused with
`ALREADY_LINKED`.

**Real, read-only, outside the test suite** (this task's own investigation):
`data/runway_safe.db` was inspected via a fresh, read-only `sqlite3`
connection before and after this entire task. SHA-256
`71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`, size
1789952 bytes, mtime 1787158044.8543456 — identical at both checks. Direct
SQL reconfirmed `source_assertions.id=222` → `signal_id=67`, latest
`reviewer_actions` row for #222 is `MARK_DUPLICATE(duplicate_of_signal_id=67)`.
**Also proven live, not just inferred**: `run_review()` called against the
real database with `--source-assertion-id 222 --action CONFIRM_DISTINCT_SIGNAL`
(no write flag) returned `RECONCILIATION_REVIEW_SCHEMA_MIGRATION_REQUIRED`
— the real database still lacks R4B's migration, so the schema gate refuses
before ever reaching the `ALREADY_LINKED` logic; both outcomes (schema
refusal today, `ALREADY_LINKED` refusal once R4B is eventually migrated)
are fail-closed and neither wrote anything. No real migration, no real
write, no real `ReviewerAction`, confirmed by the identical hash before and
after this live probe.

## 18. Focused tests

New file `tests/test_review_reconciliation_item.py`: 52 tests, covering
every item in the mission's "at minimum" list (Section 24): argument
validation (missing `--database`, missing write authorization components,
invalid action, malformed argument combinations), schema gate (missing R4B
column, gate-before-ORM-access, not-found), dry-run output (blocking state,
fingerprint provenance, no local hashing), the full `CONFIRM_DISTINCT_SIGNAL`
flow (valid write, fingerprint match required, three distinct staleness
triggers between dry-run and write), current-confirmation idempotency,
stale-then-new-confirmation supersession, fingerprint non-overridability,
malformed input rejection, `MARK_DUPLICATE` (target required, must be a
current candidate, arbitrary/advisory target rejected, exactly `+1`
`ReviewerAction`, `signal_id` set, target Signal unchanged, no new Signal),
transaction safety (no `SessionLocal`, dry-run never commits, read-only
engine, one commit on success, rollback on governance failure), refusal
states (`CLEAR_TO_CREATE`, `ALREADY_LINKED` for both an action request and
pure inspection), the three generic actions (recorded correctly, usable
even when reconciliation is clear), wrong-DB isolation, no-default-database,
publication/Signal safety, financial/title firewall, provider/international
independence, determinism, multiple-candidate display with no auto-selection,
and the MSP synthetic case.

Full focused suite (R4E, R4D, R1, R2, R4A, R4B persistence+migration, R3
original+reconciliation+migration, R4C, physical installation
reconciliation, static export, model contract): **661 passed**.

## 19. Full pytest

**1657 passed** (baseline 1605 + 52 new tests; no existing test modified or
removed).

## 20. py_compile

Clean on `scripts/review_reconciliation_item.py` and
`tests/test_review_reconciliation_item.py`.

## 21. git diff --check

Exit 0 (no changes to any pre-existing tracked file at all in this task —
see §22).

## 22. Exact files changed

New only:
- `scripts/review_reconciliation_item.py`
- `tests/test_review_reconciliation_item.py`
- `docs/architecture/existing-signal-reconciliation-r4e-reviewer-action-cli-report.md`
  (this file)

**No pre-existing file was modified** — `git status`/`git diff --stat`
against every tracked file show zero changes; this is the first R4-series
slice in this project's history that required no correction to prior,
already-committed code. R1, R2, R3, R4A, R4B, R4C, R4D production modules
are byte-for-byte unchanged.

## 23. Defects/corrections

None found in R1-R4D production code during fresh re-inspection. Two
implementation-time bugs in this slice's own newly-written test file were
caught and fixed before being trusted (both the same, familiar class this
project's own review discipline has repeatedly named): four tests accessed
an ORM attribute (`.id`) on an object after its own session had already
been closed/disposed, raising `DetachedInstanceError` — fixed by capturing
the needed scalar values as plain Python variables inside the `with
Session(...)` block, before it closes, in every case. No production-code
defect.

## 24. Ready for R4E review checkpoint

Yes. Implementation, tests, and documentation are complete. No commit, no
push, no real database write or migration, no autonomous action, no Signal
creation, no publication change — per this slice's own explicit stop
boundary.

## 25. Recommended next operational step

This CLI is now the sole committed write path for reconciliation
resolution; the natural next step is **operational, not another R-series
design slice**: use `scripts/review_reconciliation_item.py` (once R4B's
migration is actually applied to the real database — still not done, by
every prior slice's own explicit instruction not to perform it) to resolve
real, currently-blocking `POSSIBLE_EXISTING_SIGNAL_MATCH` items surfaced by
`scripts/list_human_review_queue.py --state reconciliation` (R4D). No
further R-series design work is implied by the reviewed design doc's own
Section 20 roadmap beyond R4E; R1 through R4E together are the complete,
already-reviewed existing-Signal reconciliation guard.

## Review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R4E_CRITICAL_REVIEW_COMMIT_PUSH)

A fresh, adversarial re-read of the complete CLI, both R4D and R4A/R4B/R4C's
own contracts, and every one of the 52 originally-reported tests found **no
defect in the production code** (`scripts/review_reconciliation_item.py`).
The three invocation tiers, schema-gate ordering, fresh-recomputation
discipline, fingerprint handshake, MARK_DUPLICATE target validation,
transaction atomicity, and no-Signal/no-publication guarantees all held
under direct adversarial attack. Every finding below is a genuine
**test-coverage gap** the mission's own exhaustive attack list named
explicitly - none required a production code change.

### Independent architecture verification

Re-read `app/services/human_review_reconciliation.py` (R4D),
`app/services/existing_signal_reconciliation_review.py` (R4A),
`app/services/reviewer_action_persistence.py` (R4B), and
`app/services/governed_signal_creation.py` (R4C) fresh. Confirmed this
script's own reconciliation calls use byte-identical arguments to R4D's own
(`category=None`, `reference_year=None`, empty `claims`) - the fingerprint
a human sees in an R4D dry-run and the fingerprint this script recomputes
for the same state are provably the same computation, not two independently
-maintained ones that could drift.

### Schema-gate verification

Re-confirmed `check_schema_readiness()` runs before `build_engine()`/
`Session()` are ever constructed, for every code path including a write
attempt. **Proven live against the real database in this checkpoint** for
all three action shapes (no action, `CONFIRM_DISTINCT_SIGNAL`,
`MARK_DUPLICATE`): every one returns
`RECONCILIATION_REVIEW_SCHEMA_MIGRATION_REQUIRED`, zero exceptions, zero
writes, database hash unchanged.

### Handshake verification

Direct string-equality comparison confirmed (no `.strip()`, no `.lower()`,
no prefix matching anywhere in `_validate_confirm_distinct_signal()`).
**Closed a real gap**: the original 52 tests attacked a syntactically
malformed string and an unrelated hand-typed value, but never an uppercase
copy of the *genuinely correct* fingerprint, a whitespace-padded copy, a
truncated copy, or - most importantly - a *real, freshly-computed R4A
fingerprint taken from a different SourceAssertion's own current blocking
state* (the exact "fingerprint copied from another SourceAssertion" attack
the mission named explicitly, and the one R4A/R4C's own design specifically
engineered `source_assertion_id`-in-the-payload to defeat). All four now
have dedicated regression tests; all four correctly refuse.

### Atomicity verification

**Closed a real gap**: the original suite proved atomicity only for a
governance failure that occurs *before* any row is ever inserted
(`test_failed_action_leaves_zero_partial_write`) - a materially weaker
guarantee than "failure after the `ReviewerAction` insert but before the
link succeeds," which the mission named explicitly (Section 11). Added
`test_link_phase_failure_rolls_back_the_already_inserted_reviewer_action`,
which monkeypatches `link_source_assertion_to_duplicate_signal` to raise
after the `MARK_DUPLICATE` row has genuinely been inserted and flushed, and
confirms the whole transaction - including that already-flushed row - rolls
back to zero rows.

### Target validation verification

**Closed a real gap**: `MARK_DUPLICATE`'s own fresh-recomputation guarantee
(mirroring `CONFIRM_DISTINCT_SIGNAL`'s fingerprint-mismatch protection) had
zero direct test coverage - only the fingerprint side had a "candidate
removed between dry-run and write" test. Added
`test_candidate_removed_between_dry_run_and_write_refuses_mark_duplicate`
(a second, independently-anchored "stable" candidate keeps the outcome
`POSSIBLE_EXISTING_SIGNAL_MATCH` throughout, isolating the removed
candidate as the only variable - the same pattern already proven correct in
the R4C/R4D test suites). Also strengthened target-Signal-immutability
coverage from a single `title` check to a full ten-field snapshot
(`published`, `updated_at`, `confirmed_vendor`, `likely_supplier`, both
financial fields, `status`, `category`, `confidence`) compared byte-for-
byte before and after a successful write.

### Additional gaps closed

- **Schema readiness re-checked at write time, not trusted from an earlier
  dry-run** (mission Section 23): added
  `test_schema_degraded_between_dry_run_and_write_is_rechecked_not_trusted`,
  downgrading R4B's migration on the very database a successful dry-run
  just ran against, immediately before the write attempt - the write
  correctly re-blocks with the schema marker rather than trusting the
  passed dry-run.
- **No backup file created** (mission Section 21): the module docstring
  already documented this as a deliberate choice, but nothing proved it
  directly. Added `test_no_backup_file_created_by_dry_run_or_write`
  (directory-listing before/after both a dry-run and a real write) and
  `test_no_backup_function_imported` (structural).

### Evaluated, not duplicated

Per-field subject-identity staleness (runway/physical-installation/
provenance change, individually) was deliberately **not** re-proven a third
time in this CLI's own suite - R4C and R4D's test suites already prove this
exhaustively, field by field, and R4E reimplements none of that mechanism;
re-testing every individual field-change scenario here would be pure
duplication with no new coverage value, since R4E's own responsibility is
only "does it correctly detect *any* mismatch and refuse" - already proven
via the candidate-added, candidate-removed, and anchor-changed tests.
Similarly, `get_latest_reviewer_action()`'s own `created_at`/`id` tie-break
determinism (mission Section 8's "attack order/timestamp ties") is already
exhaustively tested in R4B's own suite and is called, never reimplemented,
here.

### Final validation after review-checkpoint additions

- Focused suite (same set as the implementation task, now including all 9
  new tests): **670 passed**.
- Full pytest: **1666 passed** (1657 reported pre-review total + 9 new
  review-checkpoint tests; no existing test removed or weakened).
- `py_compile`: clean.
- `git diff --check`: exit 0.
- Real database: unchanged throughout (identical SHA-256 across every check
  in this session, including the live multi-action schema-gate proof).

### Conclusion

R4E's production code is sound as implemented. Every review-checkpoint
finding was a coverage gap the mission's own exhaustive attack list named
explicitly - never a behavioral defect.
`docs/architecture/existing-signal-reconciliation-r4e-reviewer-action-cli-report.md`
and `tests/test_review_reconciliation_item.py` are the only files this
checkpoint modified; `scripts/review_reconciliation_item.py` itself was not
changed at all.

### Operational next step (unchanged from the implementation report)

`scripts/review_reconciliation_item.py` is ready to resolve real
reconciliation blocks once R4B's migration is eventually applied to the
real database - still not done, deliberately, by every prior slice's own
explicit instruction. No further design slice is implied.
