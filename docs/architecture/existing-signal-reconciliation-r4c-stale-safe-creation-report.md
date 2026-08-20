# R4C: Stale-Safe CONFIRM_DISTINCT_SIGNAL Creation Integration — Report

Slice R4C of
[existing-signal-reconciliation-r4-human-resolution-design.md](existing-signal-reconciliation-r4-human-resolution-design.md)
(Section 14 "R3 integration contract", Section 20 "Recommended implementation
slices"). This slice connects R4A's fingerprint core and R4B's persisted
`CONFIRM_DISTINCT_SIGNAL` action into
`create_signal_from_approved_review()`: a distinct-signal confirmation may
now authorize creation, but only when its stored fingerprint still matches
the freshly recomputed *current* blocking reconciliation state.

## 1. Starting HEAD

`122abd59da583aa1e29c94f584f8bb0ff4944da9`, confirmed matching `origin/main`
before any change. Baseline: 1529 passed.

## 2. Integration change

`app/services/governed_signal_creation.py`:

- `_check_governance_gates()` now returns the latest `ReviewerAction` row
  (previously returned `None`) and accepts `latest.action` being either
  `APPROVE_SIGNAL` or `CONFIRM_DISTINCT_SIGNAL` (new module-level constant
  `VALID_LATEST_ACTIONS_FOR_CREATION`). Every other governance check
  (`identity_guard_decision`, `intelligence_review_decision`,
  `promotion_policy_decision`, `airport_id`) is byte-for-byte unchanged.
  Every other latest action (`DEFER`, `NEEDS_MORE_EVIDENCE`, `REJECT_SIGNAL`,
  `MARK_DUPLICATE`, or none at all) still fails closed exactly as before.
- `create_signal_from_approved_review()` captures the latest action
  (`latest_reviewer_action`) from that call and, inside the existing
  `POSSIBLE_EXISTING_SIGNAL_MATCH` branch, adds one new conditional: if
  `latest_reviewer_action.action == "CONFIRM_DISTINCT_SIGNAL"`, build a
  fresh `ReconciliationReviewPlan` for the current blocking decision, compute
  its fingerprint, and compare against `latest_reviewer_action.reconciliation_fingerprint`
  — match falls through to the unmodified creation path; mismatch raises the
  new `StaleReconciliationConfirmationError`. Otherwise (latest is
  `APPROVE_SIGNAL`), the pre-existing `ExistingSignalPossibleMatchError` path
  is completely unchanged.
- New exception `StaleReconciliationConfirmationError(ValueError)`, exposing
  `.current_reconciliation_decision`, `.stored_fingerprint`,
  `.current_fingerprint` — no raw evidence, no financial data, no title.
- No other function, branch, or line in the module changed. `Signal(...)`
  construction, `published=False`, the idempotent `signal_id is not None`
  reuse branch, and `link_source_assertion_to_duplicate_signal()` are
  untouched.

R1 (`existing_signal_reconciliation.py`), R2
(`existing_signal_reconciliation_candidates.py`), R4A
(`existing_signal_reconciliation_review.py`), and R4B
(`app/models/reviewer_action.py`,
`app/services/reviewer_action_persistence.py`) are **not modified** — no
genuine independent defect was found in any of them during this slice's
fresh re-read.

## 3. Latest-action semantics

`VALID_LATEST_ACTIONS_FOR_CREATION = ("APPROVE_SIGNAL", "CONFIRM_DISTINCT_SIGNAL")`.
`CONFIRM_DISTINCT_SIGNAL` is **not** a general creation approval — reaching
the gate is necessary but not sufficient; the fingerprint comparison below
is the actual authorization. Every non-member latest action (including
historical `APPROVE_SIGNAL`/`CONFIRM_DISTINCT_SIGNAL` rows superseded by a
later `DEFER`/`NEEDS_MORE_EVIDENCE`/`REJECT_SIGNAL`/`MARK_DUPLICATE`) fails
closed with the same `ValueError` shape as before R4C (message widened from
naming only `'APPROVE_SIGNAL'` to naming
`VALID_LATEST_ACTIONS_FOR_CREATION`, still containing the substring `"latest
ReviewerAction"` every pre-existing test matches on).

## 4. Fresh-reconciliation requirement

Reconciliation (`build_reconciliation_subject` → `find_reconciliation_candidates`
→ `evaluate_existing_signal_reconciliation`) is computed exactly once per
call, unconditionally, for both `APPROVE_SIGNAL` and `CONFIRM_DISTINCT_SIGNAL`
latest actions — R1/R2 are never told "trust the last answer" (design doc
Section 14 step 5). No caching, no reuse of a stored decision, no shortcut
that skips recomputation because a confirmation already exists.

## 5. Fresh plan/fingerprint computation

`build_reconciliation_review_plan()` and `compute_reconciliation_fingerprint()`
(R4A, unmodified, imported verbatim) are the only fingerprint authority.
R4C contains no SHA-256 code, no canonicalization logic, and no
serialization logic of its own — verified both by code inspection and by
`test_r4c_fingerprint_calls_never_pass_financial_or_title_fields`'s AST scan
of the two new call sites.

## 6. Exact-match behavior

`compute_reconciliation_fingerprint(fresh_plan) == latest_reviewer_action.reconciliation_fingerprint`
is a plain Python string equality — byte-for-byte, no normalization, no
prefix/substring comparison. On match, execution falls through to the
existing, unmodified `Signal(...)` construction path (`published=False`)
exactly as an `APPROVE_SIGNAL` + `CLEAR_TO_CREATE` call already does.

## 7. Stale behavior

On mismatch, `StaleReconciliationConfirmationError` is raised **before** any
`Signal` is constructed, added, or flushed, and before
`source_assertion.signal_id` is touched — the same fail-closed timing
`ExistingSignalPossibleMatchError` already established. It carries the
stored fingerprint, the freshly computed one, and the full current
`ExistingSignalReconciliationDecision` (candidate ids/reasons only — the
same absent-title/absent-financial dataclass `ExistingSignalPossibleMatchError`
already exposes). It does not auto-fix anything and does not record a new
`ReviewerAction` — resolving staleness requires a new, separate, future
human review, explicitly out of this slice's scope.

Verified stale triggers: a new blocking candidate appearing
(`test_stale_when_a_new_blocking_candidate_appears_after_confirmation`), a
previously-blocking candidate being retracted
(`test_stale_when_a_previously_blocking_candidate_is_removed`), the same
candidate's anchor reason changing from runway to provenance
(`test_stale_when_anchor_reason_changes_for_the_same_candidate`), the
subject's own structural identity changing
(`test_stale_when_subject_runway_id_changes_after_confirmation`), and a
fingerprint copied verbatim from a different `SourceAssertion`'s
confirmation (`test_stale_when_fingerprint_copied_from_another_assertion`) —
the last of these works "for free," without any R4C-specific ownership
check, because R4A's `ReconciliationReviewPlan.source_assertion_id` is
already part of the hashed payload (§10 below).

## 8. Advisory churn

`build_reconciliation_review_plan()` only ever reads
`decision.candidate_signal_ids`/`decision.reasons` (R4A, unmodified) — never
`.advisory_candidate_signal_ids`/`.advisory_reasons`, which by R1's own
"mutually exclusive by outcome" construction are populated only on
`CLEAR_TO_CREATE`, never on the `POSSIBLE_EXISTING_SIGNAL_MATCH` a
confirmation is bound to. A new compatibility-only (non-anchor-backed)
Signal appearing after confirmation therefore cannot affect the fingerprint
at all — proven by `test_advisory_only_churn_does_not_invalidate_confirmation`,
and independently by `test_financial_and_title_changes_never_invalidate_a_confirmation`
for the money/title axis specifically.

## 9. Multiple-candidate behavior

The fingerprint covers the complete blocking set, never a selected subset —
`test_golden_case_multiple_candidates_full_set_match_creates` confirms a
two-candidate confirmation (one runway-anchored, one
physical-installation-anchored) authorizes creation only when both remain
current; `test_stale_when_a_new_blocking_candidate_appears_after_confirmation`
and `test_stale_when_a_previously_blocking_candidate_is_removed` confirm
that a superset or subset of the originally-reviewed set is stale, matching
the design's own Section 11 "no per-candidate confirmation" rule.

## 10. Copied-fingerprint attack

`ReconciliationReviewPlan.source_assertion_id` is populated from the
caller-supplied `source_assertion_id` argument to
`build_reconciliation_review_plan()` (R4A, unmodified) — `create_signal_from_approved_review()`
always passes the *current* `source_assertion.id`, never a value read from
the stored confirmation. A fingerprint genuinely computed for a different
SourceAssertion's plan therefore differs by construction (a different
`source_assertion_id` in the hashed payload), even when every other
structural fact happens to coincide (same airport, same runway, same
blocking Signal). This is the mechanism that resolves R4B's own documented
"ownership boundary" gap (see
[existing-signal-reconciliation-r4b-reviewer-action-report.md](existing-signal-reconciliation-r4b-reviewer-action-report.md)
§11 attack N and §12's trust-boundary note) — R4C does not add a separate
`source_assertion_id`-equality check; the existing R4A field already
provides it. Verified by `test_stale_when_fingerprint_copied_from_another_assertion`.

## 11. Supersession

`get_latest_reviewer_action()` (R4B, unmodified — same `created_at DESC, id
DESC` ordering, no chain-walking) is the sole source of "current" state.
Any later action superseding a valid `CONFIRM_DISTINCT_SIGNAL` —
`DEFER`/`NEEDS_MORE_EVIDENCE`/`REJECT_SIGNAL`/`MARK_DUPLICATE` — makes that
confirmation irrelevant regardless of whether its own fingerprint would
still match; the gate fails closed on the *later* action's identity before
reconciliation or fingerprint comparison is ever reached
(`test_later_action_supersedes_a_valid_confirmation_and_blocks`,
`test_later_mark_duplicate_supersedes_a_valid_confirmation_and_blocks_creation`).
A historically-valid, still-technically-matching confirmation earlier in
the chain is never consulted once superseded — no "find most recent
confirmation" shortcut independent of latest action exists anywhere in this
module (`test_historical_confirmation_ignored_when_a_later_action_is_latest`).

## 12. ALREADY_LINKED behavior

Unchanged and dominant regardless of latest action. R1's own
`ALREADY_LINKED` short-circuit fires whenever `source_assertion.signal_id`
is already set, before any candidate is even inspected, and the pre-existing
`source_assertion.signal_id is not None` idempotent-reuse branch below it
was not touched by this slice — a `CONFIRM_DISTINCT_SIGNAL` recorded after
linking cannot force a second `Signal` into existence
(`test_already_linked_prevents_second_creation_even_with_latest_confirm_distinct_signal`).
The existing dangling-link drift check (`signal_id` pointing to a Signal
that no longer exists) also still fires unchanged with a
`CONFIRM_DISTINCT_SIGNAL` latest action
(`test_dangling_signal_id_fails_closed_with_latest_confirm_distinct_signal`).

## 13. Transaction safety

No `session.commit()` was added anywhere (AST-verified,
`test_service_still_never_calls_session_commit`, mirroring the pre-existing
R3 test of the same name). The fingerprint comparison happens entirely
before any `Signal(...)` construction, `session.add()`, or `session.flush()`
— a stale confirmation leaves zero pending ORM objects
(`test_stale_confirmation_leaves_no_pending_signal_and_no_reviewer_action_mutation`,
which also confirms the `ReviewerAction` row count and the confirmation
row's own fields are byte-identical before and after a failed attempt). A
valid confirmation's creation, if rolled back by the caller, leaves no
`Signal` row (`test_valid_confirmation_creation_rolled_back_leaves_no_signal`).
R4C's code never mutates `action`, `reconciliation_fingerprint`, `reason`,
`reviewer`, or `supersedes_action_id` on any `ReviewerAction` - it only ever
reads `.action` and `.reconciliation_fingerprint` on the single row
`get_latest_reviewer_action()` returns.

## 14. Publication safety

`published=False` remains hardcoded in the single, unmodified `Signal(...)`
construction — reached identically whether the authorizing latest action was
`APPROVE_SIGNAL` or a matching `CONFIRM_DISTINCT_SIGNAL`. No publication
parameter was added to `create_signal_from_approved_review()`; no caller
override exists. Verified by `test_confirmed_distinct_creation_is_still_unpublished`.

## 15. MSP regression

**Read-only, real-database verification** (never written to): `data/runway_safe.db`
was inspected via a read-only SQLite connection
(`file:...?mode=ro`) before and after this entire implementation task.
SHA-256, size, and mtime were identical at both checks
(`71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`,
1789952 bytes, mtime 1787158044.8543456) - no backup was taken because none
was needed; no write of any kind occurred. The real `reviewer_actions` table
has **not** had the R4B migration applied (no `reconciliation_fingerprint`
column exists on it), confirming R4B's own migration was, as documented,
never run against the real database. The real SourceAssertion #222's
current shape was confirmed exactly as the mission described: `id=1
APPROVE_SIGNAL`, `id=2 MARK_DUPLICATE -> Signal #67` (superseding #1),
`source_assertions.signal_id = 67`. No `CONFIRM_DISTINCT_SIGNAL` was ever
recorded against the real database (impossible, since the column doesn't
exist there) - no creation path opens for this real assertion under R4C:
its latest action (`MARK_DUPLICATE`) is not in
`VALID_LATEST_ACTIONS_FOR_CREATION`, so `create_signal_from_approved_review()`
still fails exactly as it did before this slice.

**Synthetic regression tests** (never touch the real database) reproduce
both real MSP states: `test_msp_historical_pre_resolution_shape_still_creates_via_r4c_modified_code`
(the pre-resolution shape - latest `APPROVE_SIGNAL`, no anchor,
`CLEAR_TO_CREATE`, still creates through the now-R4C-modified function - the
full case remains in `TestMSPHistoricalPreResolution`,
`tests/test_governed_signal_creation_reconciliation.py`, unmodified) and
`test_msp_current_real_shape_latest_mark_duplicate_signal_67_no_creation_path`
(the current real shape, reproduced synthetically - latest `MARK_DUPLICATE`,
`signal_id` already set to a stand-in "Signal #67" - confirms no creation
path opens, matching the real read-only inspection above).

## 16. Synthetic golden cases

- **Positive** (`test_golden_case_valid_confirmation_of_current_state_creates_signal`):
  a fresh blocking case is constructed (`APPROVE_SIGNAL` → runway-anchored
  block → `ExistingSignalPossibleMatchError` confirmed), a genuine
  `CONFIRM_DISTINCT_SIGNAL` is recorded using the *actual* R4A plan/fingerprint
  for that exact state (via `build_reconciliation_review_plan`/
  `compute_reconciliation_fingerprint`, never a hand-typed string), and the
  retried call succeeds: `Signal` created, `published=False`.
- **Negative** (`test_stale_when_a_new_blocking_candidate_appears_after_confirmation`):
  identical setup, but before the retry a second candidate becomes
  anchor-backed too (via a shared `PhysicalInstallationIdentity` link) -
  `StaleReconciliationConfirmationError` is raised, no `Signal` is created.

## 17. Focused tests

- `tests/test_governed_signal_creation_distinct_confirmation.py`: 29 new
  tests, covering every item in the mission's "at minimum" list (Section
  28): non-regression of `APPROVE_SIGNAL`+`CLEAR`/`POSSIBLE`, golden/stale
  cases, candidate add/remove, anchor-reason change, subject-identity
  change, copied fingerprint, advisory churn, candidate/reason ordering
  invariance (proven at R4A, accepted through the real R4C integration
  point), full supersession matrix (`DEFER`/`NEEDS_MORE_EVIDENCE`/
  `REJECT_SIGNAL`/`MARK_DUPLICATE`/historical-ignored),
  `ALREADY_LINKED`/dangling-link safety, governance-before-fingerprint
  ordering, no-commit/rollback/no-ReviewerAction-mutation, publication
  safety, money/title irrelevance (behavioral + AST), provider-agnosticism
  (AST, scoped to the new code), an international case, and both MSP
  regression shapes.
- `tests/test_reviewer_action_confirm_distinct_signal.py`: one now-obsolete
  test removed (§21 below) - its own premise ("governed_signal_creation.py
  has zero references to CONFIRM_DISTINCT_SIGNAL") was specifically true
  only *before* R4C existed; every other test in that file, and every test
  in every other R4B/R4A/R1/R2 file, is unchanged and passing.
- Full focused run (R1, R2, R4A, R4B persistence+migration, R3 original +
  reconciliation + migration, the new R4C file, human review queue,
  physical installation reconciliation, model contract): **524 passed**.

## 18. Full pytest

**1557 passed** (baseline 1529 + 29 new tests in the new R4C file, net of
the 1 removed now-obsolete R4B test: 1529 + 29 − 1 = 1557).

## 19. py_compile

Clean on `app/services/governed_signal_creation.py`,
`tests/test_governed_signal_creation_distinct_confirmation.py`, and
`tests/test_reviewer_action_confirm_distinct_signal.py`.

## 20. git diff --check

Exit 0 - no whitespace errors (only harmless pre-existing LF→CRLF
line-ending warnings, consistent with every prior slice in this project).

## 21. Exact files changed

Modified:
- `app/services/governed_signal_creation.py`
- `tests/test_reviewer_action_confirm_distinct_signal.py` (one obsolete
  test removed - see §17/§22)

New:
- `tests/test_governed_signal_creation_distinct_confirmation.py`
- `docs/architecture/existing-signal-reconciliation-r4c-stale-safe-creation-report.md`
  (this file)

No schema, migration, or model changes of any kind. R1, R2, R4A, and every
R4B file other than the one obsolete test above are untouched.

## 22. Corrections found

One correction, non-behavioral, discovered while running the full focused
suite: `tests/test_reviewer_action_confirm_distinct_signal.py::test_governed_signal_creation_module_does_not_reference_confirm_distinct_signal`
asserted `"CONFIRM_DISTINCT_SIGNAL" not in inspect.getsource(governed_signal_creation)`
- a statement that was true and meaningful *only* while R4C did not yet
exist, and became simply false, by design, the moment this slice's own
mission (integrating that exact action into that exact module) was
completed. Removed with a clear comment explaining why, rather than
weakened or worked around; the real invariant it stood in for (correct R4C
integration behavior) is now covered exhaustively by the new R4C test file.
No defect was found in R1, R2, R4A, R4B, or the pre-existing R3 test files
- all 95 pre-existing R3-family tests and all pre-existing R4A/R4B tests
(minus the one obsolete assertion above) passed unmodified against the
R4C-integrated module.

## 23. Ready for R4C review checkpoint

Yes. Implementation, tests, and documentation are complete. No commit, no
push, no real database write, no R4D/R4E work, no auto-confirmation, and no
publication-behavior change were made, per this slice's own explicit stop
boundary.

## Review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R4C_CRITICAL_REVIEW_COMMIT_PUSH)

A fresh, adversarial re-read of every governed R4C file, plus the R4 design
doc, R1, R2, R4A, and R4B, found **no defect in the production code**
(`app/services/governed_signal_creation.py`). The gate-order, latest-action
widening, fresh-recomputation, R4A-only-authority, exact-string-comparison,
`ALREADY_LINKED`-dominance, transaction-safety, and no-mutation properties
all held under direct adversarial attack. Every finding below is a **test-
coverage gap** (an untested but already-correct behavior) or a **bug in a
newly-written review-checkpoint test itself** (caught and fixed before this
checkpoint's own tests were trusted) - not a change to
`governed_signal_creation.py`.

### Findings and corrections

1. **CLEAR_TO_CREATE-after-confirmation was implemented per the design but
   untested** (mission's own explicitly-flagged "important design edge",
   Section 14). Re-read of the design doc's Section 14 step 6 confirmed the
   implementation's interpretation was correct: "the stored confirmation
   becomes moot, not consulted" - creation proceeds via the ordinary
   unblocked path, not `StaleReconciliationConfirmationError`. Added
   `test_clear_to_create_after_confirmation_is_moot_but_creation_still_proceeds`,
   which removes the only anchor after a valid confirmation and asserts
   creation still succeeds with `outcome == CLEAR_TO_CREATE`.
2. **Malformed/non-hex stored fingerprint was never attacked** (mission
   Section 13). The production code's `fresh_fingerprint != latest_reviewer_action.reconciliation_fingerprint`
   is a plain Python string inequality - a malformed or `None` stored value
   can never equal a genuine 64-hex-character fresh fingerprint, so this
   already failed closed correctly with no code change needed. Added
   `test_malformed_stored_fingerprint_fails_closed_as_stale`, which
   constructs a `CONFIRM_DISTINCT_SIGNAL` row directly via the ORM
   (bypassing `record_reviewer_action()`'s own R4B validation, exactly the
   documented DB-level trust-boundary gap R4B's own report already names)
   and confirms `StaleReconciliationConfirmationError` fires rather than a
   crash or silent bypass.
3. **`ALREADY_LINKED` drift check was untested with a `CONFIRM_DISTINCT_SIGNAL`
   latest action** (mission Section 29/15) - only the non-drifted idempotent
   case had a test. Added
   `test_already_linked_drift_check_still_fires_with_latest_confirm_distinct_signal`,
   confirming the pre-existing R3 drift check (`"different core fields"`)
   still fires unchanged regardless of latest action, exactly as code
   inspection already showed it must (the drift check runs unconditionally
   once `signal_id is not None`, with no dependency on which action
   authorized reaching that point).
4. **Subject-identity staleness (mission Section 9) was only tested for
   `runway_id`** - `physical_installation_ids` and `source_id`/
   `artifact_identity` (provenance) were unexercised. Added
   `test_stale_when_subject_physical_installation_ids_change_after_confirmation`
   and `test_stale_when_subject_source_provenance_changes_after_confirmation`,
   both constructed with a second, independently-anchored "stable" candidate
   present throughout so the outcome stays `POSSIBLE_EXISTING_SIGNAL_MATCH`
   before and after the mutation (proving the fingerprint changed because
   candidate *membership* changed, not because the whole decision
   degenerated into `CLEAR_TO_CREATE` - a different, separately-tested
   scenario).

### A bug caught in this checkpoint's own test-writing (not production code)

The first draft of `test_stale_when_subject_source_provenance_changes_after_confirmation`
retargeted `assertion.source_id` away from a provenance-anchored candidate
whose `category` happened to still equal the proposed Signal's default
`"replacement"` category - the candidate did not drop out of consideration
entirely, it fell back to a `compatibility:category` reason, making the
fresh outcome `CLEAR_TO_CREATE` (with advisory metadata) instead of a
*different* `POSSIBLE_EXISTING_SIGNAL_MATCH`. The test's own
`pytest.raises(StaleReconciliationConfirmationError)` failed with "DID NOT
RAISE" on first run. Fixed by adding a second, independently runway-anchored
"stable" candidate that keeps the outcome `POSSIBLE_EXISTING_SIGNAL_MATCH`
throughout, isolating the provenance-anchor removal as the only variable.
Caught immediately by running the new test before trusting it, exactly the
"do not merely accept a passing suite" discipline the mission's own Section
31 requires of this checkpoint.

### Verified, not just re-asserted

- **Legacy writer non-interference** (Section 30): `git diff --stat` and
  `git status --short` against `app/services/signal_rules.py` and every file
  under `scripts/` are both empty - zero changes, confirmed directly rather
  than assumed from the report.
- **R4A single authority** (Section 17): `grep`-searched
  `governed_signal_creation.py` for `hashlib`, `json.`, `sha256`, and
  `sorted(`/`.sort(` - the only two `sorted()` calls format unrelated
  error-message vocabulary (`confidence`, disallowed statuses), not
  fingerprint data.
- **MSP real read-only state** (Section 26): re-verified via a fresh
  read-only SQLite connection (no ORM, avoiding any risk from the
  unmigrated schema) both before and after this entire checkpoint. SHA-256
  `71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`, size
  1789952 bytes, mtime 1787158044.8543456 - identical at every check across
  both the R4C implementation task and this review checkpoint. Confirmed
  directly: `reviewer_actions` has no `reconciliation_fingerprint` column
  (R4B migration still not applied to the real database), zero rows with
  `action='CONFIRM_DISTINCT_SIGNAL'` exist anywhere (the column doesn't
  exist, so none could), no Signal id 69 exists, and SourceAssertion #222
  remains linked to Signal #67 via the same `APPROVE_SIGNAL`→`MARK_DUPLICATE`
  history recorded in the original R4B implementation.

### Final validation after review-checkpoint additions

- Focused suite (R1, R2, R3 original + reconciliation + migration, R4A,
  R4B persistence + migration, R4C, human review queue, physical
  installation reconciliation, model contract, static export): **566
  passed**.
- Full pytest: **1562 passed** (1557 reported pre-review total + 5 new
  review-checkpoint tests, no removals, no weakened assertions).
- `py_compile`: clean.
- `git diff --check`: exit 0 (only pre-existing LF→CRLF warnings).

### Conclusion

R4C's production code is sound as implemented - every review-checkpoint
finding was a coverage gap or a self-caught test bug, never a behavioral
defect. `docs/architecture/existing-signal-reconciliation-r4c-stale-safe-creation-report.md`
and `tests/test_governed_signal_creation_distinct_confirmation.py` are the
only files this checkpoint modified beyond adding this section.
