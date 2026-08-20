# R4B: ReviewerAction Persistence Extension — Report

Slice R4B of
[existing-signal-reconciliation-r4-human-resolution-design.md](existing-signal-reconciliation-r4-human-resolution-design.md)
(Section 19 "Schema verdict", Section 20 "Recommended implementation slices").
This slice is **only** the persistence/schema layer for a human reviewer
recording `CONFIRM_DISTINCT_SIGNAL`. It does not integrate the action into
`create_signal_from_approved_review()` — that is R4C.

## 1. Schema change

`app/models/reviewer_action.py`:

1. `REVIEWER_ACTIONS` gains one member: `"CONFIRM_DISTINCT_SIGNAL"` (now six
   values total). The `ck_reviewer_actions_action` CHECK constraint's
   literal list is updated to match exactly.
2. One new nullable column:
   `reconciliation_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)`.
3. Two new CHECK constraints, exact mirrors of the existing
   `duplicate_of_signal_id` pair:
   - `ck_reviewer_actions_fingerprint_required`:
     `(action != 'CONFIRM_DISTINCT_SIGNAL') OR reconciliation_fingerprint IS NOT NULL`
   - `ck_reviewer_actions_fingerprint_only_for_confirm_distinct`:
     `(action = 'CONFIRM_DISTINCT_SIGNAL') OR reconciliation_fingerprint IS NULL`

No new table, no new model, no workflow-state machinery — exactly the
design's Section 19 verdict, implemented as proposed with no deviation.

## 2. Action semantics

`CONFIRM_DISTINCT_SIGNAL` means: a human reviewed a specific existing-Signal
reconciliation block (R1's `POSSIBLE_EXISTING_SIGNAL_MATCH` outcome, R4A's
`ReconciliationReviewPlan`) and confirmed the proposed Signal is a genuinely
distinct real-world project from the blocking candidate(s).

It does **not** mean: publish; auto-promote; ignore reconciliation forever;
mutate an existing Signal; mark duplicate; approve arbitrary future
reconciliation states; bypass governance; or create a Signal. No other
action value was added; all five pre-existing actions and their semantics
are unchanged (verified by the pre-existing `tests/test_reviewer_action_persistence.py`
suite passing unmodified except for one count-of-actions assertion, see
§15).

## 3. Fingerprint validation

`record_reviewer_action()` requires, for `CONFIRM_DISTINCT_SIGNAL`, that
`reconciliation_fingerprint` is a `str` matching `^[0-9a-f]{64}$` exactly:

- Exactly 64 characters, lowercase hex only.
- No stripping, no case-folding — a malformed value (uppercase, wrong
  length, non-hex characters, any whitespace) is rejected outright, never
  silently normalized into something that would pass.
- The validated value is persisted byte-for-byte, unmodified.

For every other action, supplying any `reconciliation_fingerprint` value
(including a syntactically valid one) raises `ValueError` rather than
silently discarding it.

This module never recomputes a fingerprint, never imports or calls
`build_reconciliation_review_plan()` / `compute_reconciliation_fingerprint()`
from `app.services.existing_signal_reconciliation_review`, and never
compares the stored value against a live reconciliation plan. See §8 for
why.

## 4. CHECK constraints

Implemented exactly as designed (§1 above). Verified independently at the
DB level bypassing `record_reviewer_action()` entirely (direct `ReviewerAction(...)`
construction + `session.commit()`) in
`tests/test_reviewer_action_confirm_distinct_signal.py::test_db_check_constraints_independently_enforce_both_relationships_bypassing_the_service`:
missing fingerprint on `CONFIRM_DISTINCT_SIGNAL`, a stray fingerprint on
`DEFER`, and fingerprint+`duplicate_of_signal_id` supplied together are all
three rejected by SQLite itself, independent of Python-level validation.

## 5. Governance-gate result

`CONFIRM_DISTINCT_SIGNAL` is gated by the exact same three governance
preconditions `APPROVE_SIGNAL` already requires:

```
identity_guard_decision       == "ATTACH_CONFIRMED"
intelligence_review_decision  == "REVIEW_REQUIRED"
promotion_policy_decision     == "HUMAN_REVIEW_REQUIRED"
```

Rationale, matching the design's own Section 14 framing ("`CONFIRM_DISTINCT_SIGNAL`
supersedes the `APPROVE_SIGNAL` that triggered the blocked attempt and
itself carries forward the 'yes, still authorized to create' meaning, in
addition to resolving reconciliation"): this action is a follow-on
resolution to an already-approved governed creation attempt, not a
replacement for the original human approval, so the same base governance
state that legitimized the original `APPROVE_SIGNAL` must still hold. A
`SourceAssertion` that could never legally receive `APPROVE_SIGNAL`
(`AUTO_ELIGIBLE`, `DO_NOT_PROMOTE`, or a malformed/`NULL` governance state)
can never receive `CONFIRM_DISTINCT_SIGNAL` either — verified directly by
`test_confirm_distinct_signal_rejected_for_auto_eligible_attack_J` and
`test_confirm_distinct_signal_rejected_for_do_not_promote_attack_K`.

This check is entirely derivable from already-persisted `SourceAssertion`
columns; it required no R4C reconciliation recomputation and stays fully
within R4B's "persistence now" scope.

**Review-checkpoint addition**: `CONFIRM_DISTINCT_SIGNAL` also now requires
`supersedes_action_id` to be set (non-`None`). `create_signal_from_approved_review()`
(unmodified by R4B) still hard-requires `latest.action == "APPROVE_SIGNAL"`
before it will even attempt reconciliation — so in every real path through
the currently-committed system, a `POSSIBLE_EXISTING_SIGNAL_MATCH` block
(the only thing `CONFIRM_DISTINCT_SIGNAL` exists to resolve) can only ever
arise after an `APPROVE_SIGNAL` action already exists as latest. Every
worked example in the design's own Section 13 shows `CONFIRM_DISTINCT_SIGNAL`
superseding something (an `APPROVE_SIGNAL`, or a `NEEDS_MORE_EVIDENCE`
recorded after it). A row with no `supersedes_action_id` at all would be a
rootless confirmation never tied to any actual reviewed block — this
implementation initially permitted that degenerate case; the review
checkpoint closed it. R4B deliberately does **not** further require the
superseded row's own `action` to be a specific value (e.g. specifically
`APPROVE_SIGNAL`) — the design's own worked example shows a
`NEEDS_MORE_EVIDENCE` predecessor too, and validating the full chain's
shape would mean chain-walking, which every other part of this table's
design explicitly forbids.

## 6. Supersession result

`CONFIRM_DISTINCT_SIGNAL` participates in the existing append-only
`supersedes_action_id` mechanism exactly like every other action — no new
chain-walking logic was written; one new, narrow requirement was added at
the review checkpoint (§5): `supersedes_action_id` must be provided at all
for this action (see §5's "Review-checkpoint addition"). `APPROVE_SIGNAL ->
CONFIRM_DISTINCT_SIGNAL` history works (both rows preserved, the earlier
row's fields untouched, the later row becomes `get_latest_reviewer_action()`'s
return value); so does `APPROVE_SIGNAL -> NEEDS_MORE_EVIDENCE ->
CONFIRM_DISTINCT_SIGNAL`, matching the design's own Section 13 worked
example. Cross-`SourceAssertion` supersession is rejected by the existing,
unmodified check (`previous.source_assertion_id != source_assertion.id`).
`get_latest_reviewer_action()` itself was not touched — same file, same
ordering (`created_at DESC, id DESC`), no chain-walking added.

## 7. Append-only result

`CONFIRM_DISTINCT_SIGNAL` rows are immutable via the exact same
`before_update`/`before_delete` event listeners every other `ReviewerAction`
row already uses — no new immutability mechanism was added, and none was
needed. Verified directly (attacks G, H, §14).

## 8. No-Signal / no-SourceAssertion-mutation proof

- AST-level: `test_confirm_distinct_signal_never_constructs_a_signal_ast`
  parses the module's own source and asserts zero `Signal(...)` call nodes
  exist anywhere in it (the module's only reference to `Signal` remains the
  pre-existing read-only existence check for `duplicate_of_signal_id`).
- Behavioral: `test_confirm_distinct_signal_does_not_change_signal_count`
  and `test_confirm_distinct_signal_does_not_mutate_source_assertion` record
  a `CONFIRM_DISTINCT_SIGNAL` action and assert `Signal` row count and every
  read `SourceAssertion` governance/evidence field are unchanged before vs.
  after.
- `test_confirm_distinct_signal_never_commits` confirms the service still
  never calls `session.commit()` itself — a `session.rollback()` after a
  successful `record_reviewer_action()` call removes the pending row.

No `create_signal_from_approved_review()` call, no R1/R2 evaluation call, no
R4A plan-building call, and no publication code exists anywhere in this
module (also confirmed by
`test_governed_signal_creation_module_does_not_reference_confirm_distinct_signal`,
which asserts the string `"CONFIRM_DISTINCT_SIGNAL"` does not appear
anywhere in `app/services/governed_signal_creation.py`'s source — proof R3
was not touched).

## 9. Migration

New script: `scripts/migrate_reconciliation_confirmation_slice_r4b.py`.

SQLite cannot `ALTER TABLE ... ADD CONSTRAINT`, and `ALTER TABLE ... ADD
COLUMN` may only add a constraint scoped to the new column alone — it
cannot widen the existing `action` CHECK's literal list. Both the widened
action vocabulary and the two new fingerprint CHECKs therefore require a
full table rebuild, using the same documented SQLite 12-step ALTER TABLE
procedure (`_column_defs_and_fks()` + `_rebuild()`) already established by
`scripts/migrate_canonical_runway_runway_end_slice1.py`'s
`_drop_column_via_rebuild()` for the equivalent DROP COLUMN case: build the
replacement table under a temporary name first, copy rows, drop the
original, rename the replacement into place, recreate indexes — in that
exact order, so no other table's foreign key into `reviewer_actions` is ever
left dangling mid-migration.

Unlike that helper, this script writes its target CHECK-constraint text
literally (`_CHECK_CLAUSES_PRE_R4B` / `_CHECK_CLAUSES_POST_R4B`), not derived
from live `Base.metadata` — deliberately, so this migration's own
`upgrade()`/`downgrade()` behavior can never silently drift if
`app/models/reviewer_action.py` gains further, later, unrelated schema
changes. `upgrade()` requires the `reviewer_actions` table to already exist
(created by `scripts/migrate_reviewer_action_slice9b.py`) and raises
`RuntimeError` otherwise, rather than attempting to create it itself.

Follows the established convention: explicit `--database`, explicit
`--allow-database-write` (refused otherwise), timestamped backup before any
write (skippable only via `--skip-backup`, documented "isolated/temp DBs
only"), `PRAGMA foreign_keys=OFF` before `BEGIN` (SQLite ignores mid-transaction
changes to that pragma) with a `PRAGMA foreign_key_check` verification before
commit, and `DEFAULT_DATABASE = Path("data/runway_safe.db")` never silently
resolved without the write flag.

## 10. Existing-row preservation

Every existing `reviewer_actions` row — including the real MSP shape
(`id=1 APPROVE_SIGNAL`, `id=2 MARK_DUPLICATE -> Signal #67`, reproduced with
synthetic ids in the migration test fixtures) — is preserved byte-for-byte
across `upgrade()`: `action`, `reason`, `reviewer`, `created_at`,
`supersedes_action_id`, `duplicate_of_signal_id` are copied unchanged; the
new `reconciliation_fingerprint` column lands `NULL` on every pre-existing
row, which the new CHECK constraints already permit for every action other
than `CONFIRM_DISTINCT_SIGNAL`. Verified by
`test_existing_rows_and_all_other_tables_preserved_exactly` (row counts and
column values across every table, not just `reviewer_actions`) and
`test_upgrade_with_existing_approve_signal_row` /
`test_upgrade_with_existing_mark_duplicate_row` (exact tuple equality
per row).

`downgrade()` restores the original 3-constraint schema and drops the
column, also preserving existing rows exactly
(`test_downgrade_preserves_existing_rows`). It refuses (raises, no partial
change committed) if any row has `action='CONFIRM_DISTINCT_SIGNAL'` at
downgrade time — the pre-R4B `action` CHECK constraint (which does not
include that value) naturally rejects the row during the rebuild's own
`INSERT ... SELECT`, so no special-case code was needed to detect this;
SQLite's own constraint enforcement is the safety mechanism
(`test_downgrade_fails_closed_if_a_confirm_distinct_signal_row_exists`).

## 11. Adversarial attack results

| # | Attack | Result |
|---|--------|--------|
| A | Fingerprint supplied to `APPROVE_SIGNAL` | Rejected: `ValueError` ("only valid when action == CONFIRM_DISTINCT_SIGNAL"). Also tested against `MARK_DUPLICATE` specifically. |
| B | `CONFIRM_DISTINCT_SIGNAL` with `NULL` fingerprint | Rejected: `ValueError` ("requires reconciliation_fingerprint"). |
| C | Uppercase SHA-256 | Rejected — not lowercased and accepted; rejected outright. |
| D | Valid-looking 64-char non-hex string | Rejected (regex requires `[0-9a-f]` only). |
| E | Valid fingerprint + duplicate target | Rejected: `ValueError` ("only valid when action == MARK_DUPLICATE"). |
| F | Cross-assertion supersession | Rejected by the existing, unmodified check. (A related, previously-unhandled case — no `supersedes_action_id` at all — was found and closed at the review checkpoint; see §5/§12.) |
| G | UPDATE fingerprint after insert | Rejected by the existing `before_update` immutability listener. |
| H | DELETE confirmation history | Rejected by the existing `before_delete` immutability listener. |
| I | Direct SQL malformed action/fingerprint combination | Rejected by the DB CHECK constraints (both ORM-bypass and raw-SQL-post-migration cases tested). |
| J | Confirmation on `AUTO_ELIGIBLE` assertion | Rejected by the governance gate (§5). |
| K | Confirmation on `DO_NOT_PROMOTE` assertion | Rejected by the governance gate (§5). |
| L | Confirmation attempting to create/link/publish a Signal | Structurally impossible — no such code exists in this module (§8); not merely untested. |
| M | Migration containing pre-existing `MARK_DUPLICATE` history | Preserved exactly across upgrade/downgrade (§10). |
| N | Fingerprint copied from another SourceAssertion | **Accepted** — R4B cannot establish semantic fingerprint ownership without recomputing the R4A plan, which is out of this slice's scope by design. No validation was invented to simulate that check. See §8 boundary statement below; this is documented, intended behavior verified by `test_structurally_valid_fingerprint_from_a_different_context_is_accepted_by_r4b`, not a defect. |

## 12. Corrections found during implementation and at the review checkpoint

**Implementation-time (before any review checkpoint):**

- Non-behavioral test correction: `tests/test_reviewer_action_persistence.py::test_reviewer_actions_vocabulary_is_exactly_the_five_approved_actions`
  asserted a 5-tuple by name and count. It was renamed to
  `..._six_approved_actions` and its assertion extended to include
  `CONFIRM_DISTINCT_SIGNAL`, since the vocabulary now legitimately has six
  members — every other assertion in that file, and every other test in
  it, was left completely unmodified.
- One implementation bug found and fixed during the migration script's own
  development: the first version of `_rebuild()`'s column-insertion offset
  counted only `FOREIGN KEY` clauses when computing where to insert the new
  column definition, inserting it after the `PRIMARY KEY` table-constraint
  clause and producing a `sqlite3.OperationalError: syntax error` (SQLite
  requires every column definition to precede table-level constraints).
  Fixed by excluding both `PRIMARY KEY` and `FOREIGN KEY` clauses from the
  insertion-offset count; caught immediately by
  `tests/test_reconciliation_confirmation_migration.py`'s own fresh-upgrade
  test before the implementation report was written.

No implementation defect was found in the freshly-read pre-existing files
(`reviewer_action.py`, `reviewer_action_persistence.py`,
`existing_signal_reconciliation_review.py`, `source_assertion.py`,
`signal.py`); the R4 design doc's Section 19 schema verdict matched the
current committed architecture exactly, with no contradiction to report.

**Review checkpoint (this pass):**

- **One genuine governance-separation defect, fixed**: `record_reviewer_action()`
  originally permitted `CONFIRM_DISTINCT_SIGNAL` with `supersedes_action_id=None`
  — a rootless confirmation with no connection to any actual prior
  `ReviewerAction` history. Since `create_signal_from_approved_review()`
  (unmodified) requires `latest.action == "APPROVE_SIGNAL"` before it will
  even attempt reconciliation, every legitimate `CONFIRM_DISTINCT_SIGNAL`
  necessarily resolves a block that only arose after an `APPROVE_SIGNAL`
  already existed as latest — so a rootless row was always off-design, not
  merely unusual. Fixed by requiring `supersedes_action_id is not None` for
  this action specifically (see §5). Two regression tests added:
  `test_confirm_distinct_signal_without_supersedes_action_id_rejected`
  (reproduces the original gap) and
  `test_confirm_distinct_signal_may_supersede_needs_more_evidence_not_only_approve_signal`
  (proves the fix doesn't over-narrow to "must supersede specifically
  `APPROVE_SIGNAL`," which would contradict the design's own Section 13
  worked example).
- **Test-coverage gaps closed, no code defect**: the original implementation's
  CHECK-constraint tests only exercised the DB CHECK constraints on a table
  SQLAlchemy built directly from the live model (ORM-bypass in
  `tests/test_reviewer_action_confirm_distinct_signal.py`) — they never
  attacked the actual *migrated* (rebuilt) table with raw SQL. Added
  `test_migrated_table_check_constraints_attacked_via_raw_sql`, a 7-case
  parametrized attack (mission Section 17 attacks A–G) directly against the
  post-`upgrade()` schema; all constraints held. Also added
  `test_upgrade_preserves_created_at_values_exactly`,
  `test_upgrade_does_not_resequence_ids`, and
  `test_migration_never_touches_a_different_database_than_the_one_named`
  (byte-identity of an untouched sibling database), none of which had
  explicit prior coverage. Also added `test_bytes_fingerprint_rejected_not_coerced`
  and two tab-containing cases to the malformed-fingerprint parametrization
  — the original suite covered every malformed-fingerprint shape the
  mission listed except `bytes` and tab characters specifically.

No other implementation defect was found. The fingerprint's DB-level
enforcement covers only the `action`/`NULL` pairing (§3's two CHECK
constraints) — it does **not** re-validate the 64-lowercase-hex-character
shape at the database level; that remains Python-only
(`_FINGERPRINT_PATTERN` in `reviewer_action_persistence.py`). This is
intentional, not an oversight: it mirrors the exact precedent already
established for `identity_guard_decision` / `intelligence_review_decision` /
`promotion_policy_decision` on `SourceAssertion` (each documented in that
model as "deliberately NOT DB-CHECK-constrained... the persistence service
is the sole writer and only ever writes a real ... value, enforced in
Python, not the database") — and, unlike those three columns, `reconciliation_fingerprint`
*is* additionally protected by the `NOT NULL`/`NULL` CHECK pairing the
design's Section 19 explicitly asked for. The trust boundary is: the
database guarantees a `CONFIRM_DISTINCT_SIGNAL` row always has *some*
fingerprint value and no other action ever has one; only
`record_reviewer_action()` (the sole intended write path) guarantees that
value is 64 lowercase hex characters. A caller that bypasses the service
entirely (direct `ReviewerAction(...)` construction, as the "belt-and-
suspenders" tests already do for the null/pairing cases) could in principle
insert a syntactically malformed but non-NULL string as a "fingerprint" —
this is a known, accepted, and now explicitly documented gap, not a new one
introduced by this slice; it exists for the same reason the analogous gap
already exists for the three governance-decision columns.

## 13. R4B / R4C boundary

**R4B stores authorization evidence. R4C decides whether that evidence is
still valid.**

This module validates only what is provable from already-persisted state:
the fingerprint's syntactic shape (64 lowercase hex characters) and the
governance columns already on `SourceAssertion`. It deliberately does
**not** know, and cannot know without recomputing the full
R1 (`evaluate_existing_signal_reconciliation`) → R2 (`find_reconciliation_candidates`)
→ R4A (`build_reconciliation_review_plan` / `compute_reconciliation_fingerprint`)
pipeline fresh, whether a given fingerprint currently matches the real
blocking reconciliation state for that `SourceAssertion` — or even whether
it was ever generated from that `SourceAssertion`'s own data at all (attack
N, §11).

**`CONFIRM_DISTINCT_SIGNAL` does not itself authorize Signal creation. Its
fingerprint must be recomputed and matched by R4C before it can affect the
creation path.** The design's own Section 14 sketches that future
comparison (`compute_reconciliation_fingerprint(fresh_plan) ==
latest.reconciliation_fingerprint`, raising `StaleReconciliationConfirmationError`
on mismatch) — none of it is implemented here. `_check_governance_gates()`
in `app/services/governed_signal_creation.py` still rejects any latest
action other than `APPROVE_SIGNAL` unchanged; `CONFIRM_DISTINCT_SIGNAL` is,
today, recordable but functionally inert from R3's point of view. That is
the intended state of the world after this slice.

## 14. Test summary

- `tests/test_reviewer_action_confirm_distinct_signal.py`: 50 tests
  (vocabulary, fingerprint validation incl. all required sub-cases plus
  `bytes` and tab-character attacks added at the review checkpoint,
  duplicate-target interaction, supersession incl. the review-checkpoint's
  own `supersedes_action_id`-required fix and its NEEDS_MORE_EVIDENCE-
  predecessor counter-test, governance, no-mutation, model contract, attack
  N, R3 non-interference).
- `tests/test_reconciliation_confirmation_migration.py`: 24 tests
  (fresh/existing-row upgrade, preservation, direct-SQL CHECK enforcement
  incl. the review checkpoint's 7-case raw-SQL attack matrix against the
  actual migrated table, `created_at`/id-preservation, idempotency,
  downgrade incl. fail-closed on a `CONFIRM_DISTINCT_SIGNAL` row present,
  round-trip, FK/integrity checks, write-authorization gate,
  slice-9b-prerequisite gate, cross-database isolation).
- `tests/test_reviewer_action_persistence.py`: one pre-existing test's name
  and assertion updated (§12); every other test unmodified and passing.
- `tests/test_model_contract.py`: one column added to the `reviewer_actions`
  snapshot; every other table's snapshot unmodified.
- Full adjacent suite (`test_reviewer_action_persistence.py`,
  `test_reviewer_action_migration.py`,
  `test_reviewer_action_confirm_distinct_signal.py`,
  `test_reconciliation_confirmation_migration.py`,
  `test_existing_signal_reconciliation_review_plan.py`,
  `test_existing_signal_reconciliation.py`,
  `test_existing_signal_reconciliation_candidates.py`,
  `test_governed_signal_creation.py`, `test_model_contract.py`): 364 passed.
- Full repository suite: 1529 passed (baseline 1455 + 74 new tests across
  both new test files, net of the one renamed-not-added pre-existing test
  and the one in-place model-contract update).

No real database was written to at any point during this implementation or
its tests — every test uses an isolated in-memory or `tmp_path` SQLite
database.
