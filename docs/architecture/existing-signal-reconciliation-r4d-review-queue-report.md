# R4D: Reconciliation-Aware Human Review Queue — Report

Slice R4D of
[existing-signal-reconciliation-r4-human-resolution-design.md](existing-signal-reconciliation-r4-human-resolution-design.md)
(Section 15 "Workflow states", Section 20 "Recommended implementation
slices"). This slice is **read-only**: it makes reconciliation state
(R1/R2/R4A/R4B/R4C) visible to a human reviewer through a new, deliberately
separate query module and an extension to the existing review-queue CLI. It
creates no `ReviewerAction`, no `Signal`, mutates no `SourceAssertion`, and
performs no schema change or migration.

## 1. Starting HEAD

`08e2d2c126f6a9a583c70189997aa0b1d9c77e20`, confirmed matching `origin/main`
before any change. Baseline: 1562 passed.

## 2. Existing 9D semantics

`app/services/human_review_queue.py` (Slice 9D, unmodified by this slice)
already provides: `ReviewWorkflowState` (`ACTIVE_REVIEW`, `DEFERRED`,
`NEEDS_MORE_EVIDENCE`, `APPROVED_PENDING_SIGNAL`, `RESOLVED_REJECTED`,
`RESOLVED_DUPLICATE`, `RESOLVED_SIGNAL_CREATED`), `derive_workflow_state()`
(a pure classifier reading only the latest `ReviewerAction` and `signal_id`),
`list_review_workflow_items()` (the complete, unfiltered
`HUMAN_REVIEW_REQUIRED` population, each row annotated with its derived
state), and `list_human_review_items()` (the narrower "needs a decision now"
default: `ACTIVE_REVIEW`/`NEEDS_MORE_EVIDENCE` only, `limit` applied *after*
derived filtering — the fix for the original Slice 8 defect this project has
now had to re-guard against twice more, see §14/§28 below). None of this was
touched.

## 3. R4D derived-state model

Following the design doc's own Section 15 **exactly**, not the mission
prompt's own looser phrasing: `ReviewWorkflowState` is **not** widened.
Instead, a new, separate module (`app/services/human_review_reconciliation.py`)
exposes exactly the one function the design doc names —
`list_reconciliation_review_items()` — and a new, small enum,
`ReconciliationReviewState`, with exactly the two members the design doc
names:

- **`RECONCILIATION_REVIEW_REQUIRED`** — latest action does not resolve a
  currently-blocking `POSSIBLE_EXISTING_SIGNAL_MATCH` (no confirmation
  exists, or one exists but its fingerprint no longer matches).
- **`DISTINCT_CONFIRMED_PENDING_SIGNAL`** — latest action is
  `CONFIRM_DISTINCT_SIGNAL`, its fingerprint currently matches, but no
  Signal has been created yet.

A stale confirmation is **not** a third top-level state, per the design's
own explicit instruction — it is `RECONCILIATION_REVIEW_REQUIRED` plus a
`reconciliation_warnings` entry (mirroring `HumanReviewItem.invariant_warnings`
in spirit, kept as a separate tuple since this module never mutates or
extends the base item it wraps).

**Naming decision, documented as the mission's own process requires**: the
mission's suggested file scope listed `app/services/human_review_queue.py`
as "likely modified." After reading the design doc fresh, that file is
**not** modified — the design's own Section 15 explicitly rejects folding
reconciliation awareness into it ("not widened... a separate, new read-only
function... mirroring `human_review_claim_enrichment`'s own already-
established precedent of being 'a deliberately separate concern, composed
by the caller'"). `human_review_claim_enrichment.py` is itself a separate
*module* (not a function added to `human_review_queue.py`), so this slice
follows that precedent literally: a new file, not a new function in the
existing one. `tests/test_human_review_queue.py`'s 74 pre-existing tests
therefore remain completely untouched, unmodified.

## 4. Reconciliation evaluation path

For each eligible row (§5 below), `list_reconciliation_review_items()` calls,
verbatim, in this order: `build_reconciliation_subject()` and
`find_reconciliation_candidates()` (R2), `evaluate_existing_signal_reconciliation()`
(R1), and — only when the outcome is `POSSIBLE_EXISTING_SIGNAL_MATCH` —
`build_reconciliation_review_plan()` and `compute_reconciliation_fingerprint()`
(R4A). No anchor rule, compatibility rule, disconfirming rule,
latest-installation-link semantics, candidate-discovery SQL, canonicalization,
or hashing logic is reimplemented; grep and AST checks (`TestR4AIsTheOnlyAuthority`)
confirm zero `hashlib`/`json`/`sha256` usage and zero scoring/ranking fields
anywhere in the new module.

**Eligibility** (design-independent, but load-bearing): reconciliation is
computed only for rows where `create_signal_from_approved_review()` (R4C)
would itself even attempt it — `source_assertion.signal_id` still `None`
and the latest `ReviewerAction` in `{None, "APPROVE_SIGNAL",
"CONFIRM_DISTINCT_SIGNAL"}`, exactly R4C's own `VALID_LATEST_ACTIONS_FOR_CREATION`
plus "never reviewed yet." A drift-guard test
(`test_eligibility_actions_match_r4c_valid_latest_actions_exactly`) compares
this module's own eligibility tuple directly against
`governed_signal_creation.VALID_LATEST_ACTIONS_FOR_CREATION` so the two can
never silently diverge. Rows whose latest action is `DEFER`,
`NEEDS_MORE_EVIDENCE`, `REJECT_SIGNAL`, or `MARK_DUPLICATE`, or whose
`signal_id` is already set, are excluded entirely — reconciliation is never
computed for them, and they never appear in this function's output;
`list_review_workflow_items()` remains the complete, unfiltered picture for
those states.

## 5. Human-selected-context limitation

`category` and `reference_year` are human-selected creation-time inputs to
`create_signal_from_approved_review()` — nothing on a governed
`SourceAssertion` row (or its re-derivable `claims`) represents them before
a human actually chooses them. This module therefore always calls
`build_reconciliation_subject(assertion, (), category=None, reference_year=None)`
— an empty `claims` tuple, no category, no year, never fabricated or
inferred from `Claim.category`, free text, or anything else.

**Direct, mechanical consequence, verified by construction**: `category`,
`vendor_names`, `evidence_date`, and `reference_year` are all
COMPATIBILITY-tier fields in R1's own taxonomy — never anchor-tier. Since
every one of them is always empty/`None` in this module's own subject,
`_compatibility_reasons()` can never fire, meaning this module's own
reconciliation evaluation can **never** produce non-empty advisory
metadata. Rather than expose an always-empty `reconciliation_advisory_*`
field pair "because the prompt listed it" (the mission's own explicit
warning against exactly this), no advisory field exists on
`ReconciliationReviewItem` at all — `TestAdvisoryNeverBlocks` proves the
structural point directly (a compatibility-shaped existing Signal never
appears in `reconciliation_candidate_signal_ids`/`reconciliation_anchor_reasons`).

Critically, this boundary can **never** cause a false blocking result: the
ANCHOR-tier fields that actually determine `POSSIBLE_EXISTING_SIGNAL_MATCH`
(`runway_id`, `physical_installation_ids`, `source_id`, `artifact_identity`,
`airport_id`) are all already-persisted, structural `SourceAssertion`
fields, entirely independent of `category`/`claims`/`reference_year` — so
this module's own blocking determination is byte-identical to what R3/R4C
would compute given the same governed evidence. The queue can under-report
non-blocking context; it can never over-report a block.

## 6. Blocking review-plan presentation

`ReconciliationReviewItem` exposes, when `reconciliation_outcome ==
"POSSIBLE_EXISTING_SIGNAL_MATCH"`: `reconciliation_candidate_signal_ids`
and `reconciliation_anchor_reasons` (both copied verbatim from R1's own
`ExistingSignalReconciliationDecision`), and `reconciliation_fingerprint`
(the freshly computed R4A value for the current state). Nothing is
translated, summarized, or reworded — the exact structural reasons R1
produced are shown as-is (`ReportsBlockingReviewPlan` in the test suite;
mission's own "do not fabricate explanations" instruction).

## 7. Advisory presentation

Deliberately absent — see §5. `CLEAR_TO_CREATE` items are still returned
(with `reconciliation_review_state=None`, `reconciliation_candidate_signal_ids=()`),
so a caller can distinguish "checked, and clear" from "never checked," but
no advisory candidate list is exposed, since this module's own construction
can never populate one meaningfully.

## 8. Distinct-confirmed-current semantics

Given latest action `CONFIRM_DISTINCT_SIGNAL`, `signal_id` still `None`,
fresh reconciliation `POSSIBLE_EXISTING_SIGNAL_MATCH`, and stored fingerprint
== fresh fingerprint: `reconciliation_review_state ==
"DISTINCT_CONFIRMED_PENDING_SIGNAL"`, `reconciliation_warnings == ()`. Both
`reconciliation_fingerprint` (fresh) and `stored_reconciliation_fingerprint`
(the confirmation's own value) are exposed side by side, letting a caller
verify the match itself rather than trusting a boolean.

## 9. Stale semantics

Given the same latest action and outcome, but stored fingerprint != fresh
fingerprint: `reconciliation_review_state == "RECONCILIATION_REVIEW_REQUIRED"`
plus exactly one `reconciliation_warnings` entry, e.g.:

    STALE_RECONCILIATION_CONFIRMATION: the latest CONFIRM_DISTINCT_SIGNAL
    confirmation's stored fingerprint no longer matches the current blocking
    reconciliation state (current candidate_signal_ids=(88,)) - a new human
    review is required.

**Honest deviation from the design doc's own illustrative wording, documented
as the mission's own process requires**: Section 15's example text shows
*both* the previously-confirmed and current candidate sets
(`"previously confirmed distinct against candidate_signal_ids=(67,), current
candidate_signal_ids=(67, 104)"`). This cannot be reproduced literally:
R4B/R4A deliberately persist only the fingerprint, never the originally-
reviewed plan or candidate set (R4B's own explicit "compact immutable
reference" design choice — see its own report's Section 1). R4D can
therefore honestly show only the *current* candidate set and the fact that
the stored value no longer matches it, never what was previously reviewed.
This is a genuine, structural, already-decided-elsewhere architectural
limitation, not an oversight in this slice.

Verified stale triggers, each with its own dedicated test: a new blocking
candidate appearing, a previously-blocking candidate being retracted, the
same candidate's anchor reason changing (runway → provenance), and the
subject's own structural identity changing.

## 10. CLEAR-after-confirmation semantics

Per the R4C review checkpoint's own established, design-verified reading
(Section 14 step 6: "the stored confirmation becomes moot, not consulted"):
when fresh reconciliation returns `CLEAR_TO_CREATE` while the latest action
is `CONFIRM_DISTINCT_SIGNAL`, `reconciliation_review_state` stays `None` —
**not** relabeled as a stale/blocking state, matching the mission's own
explicit "do not label it 'stale blocking confirmation' if no blocking plan
exists" instruction. `stored_reconciliation_fingerprint` is still shown (for
audit context — a reviewer/auditor can see a confirmation exists), and the
base item's own `latest_reviewer_action` field (`"CONFIRM_DISTINCT_SIGNAL"`,
unchanged, from the wrapped `HumanReviewItem`) remains visible and honest,
rather than hidden. `test_anchor_disappears_entirely_after_confirmation_yields_clear_not_stale`
verifies this exact shape.

## 11. Resolved semantics

`ALREADY_LINKED`, `MARK_DUPLICATE` (regardless of whether reconciliation
could independently discover the same or a different target Signal via a
genuine anchor — verified directly,
`test_mark_duplicate_excluded_even_though_reconciliation_could_discover_the_target`,
the mission's own mandatory case), and a resolved created-Signal are all
excluded from `list_reconciliation_review_items()`'s output entirely — the
base 9D `review_workflow_state` (`RESOLVED_DUPLICATE`/`RESOLVED_SIGNAL_CREATED`)
stands, completely unchanged, exactly as `list_review_workflow_items()`
already computed it.

## 12. Invariant warnings

Existing 9D warnings (`HumanReviewItem.invariant_warnings`, from the
unmodified `human_review_queue.py`) are preserved by construction — this
module never touches that file or that field, only reads the base item it
already produced (`TestExisting9DInvariantWarningsPreserved`).

New reconciliation-specific warnings, evaluated one at a time per the
mission's own list:

- **"CONFIRM_DISTINCT_SIGNAL with NULL fingerprint"** — evaluated, found
  **provably unreachable**: `ReviewerAction`'s own DB CHECK constraint
  (`ck_reviewer_actions_fingerprint_required`, R4B) enforces `NOT NULL` for
  this exact action, for every writer including a direct ORM bypass of
  `record_reviewer_action()`'s own Python validation —
  `test_null_fingerprint_confirmation_is_rejected_at_the_database_level`
  proves the `INSERT` itself fails with `CHECK constraint failed`. No
  warning-generating code exists for it, because none is needed.
- **A syntactically malformed (non-`NULL`, non-64-hex) stored fingerprint**
  — the DB CHECK only enforces the `NULL` pairing, never the hex-shape (R4B's
  own documented trust boundary) — this *is* constructible via direct ORM
  bypass, and is handled correctly: treated as an ordinary stale
  confirmation (never matches a genuine 64-hex fresh fingerprint), carrying
  the same `STALE_RECONCILIATION_CONFIRMATION` warning any other mismatch
  would, never a separate "corruption" classification — matching the
  mission's own explicit "do not classify ordinary stale confirmation as
  database corruption" instruction.
- **"`MARK_DUPLICATE` target disagrees with `signal_id`"** — already covered
  by the existing, unmodified `human_review_queue.py`'s own
  `_reviewer_action_invariant_warnings()`; nothing new needed.
- **"Blocking decision but malformed review plan"** — `build_reconciliation_review_plan()`'s
  own `ValueError` (empty candidates, empty reasons, reciprocal mismatch) is
  never wrapped in a `try`/`except` here, matching R4C's own established
  precedent: R1 never actually produces a malformed `POSSIBLE_EXISTING_SIGNAL_MATCH`
  given its own construction, so this exception firing would indicate a
  genuine R1/R4A defect, which should crash loudly, not be silently
  absorbed into a queue warning.

**Documented, not fixed** (design-doc-respecting, not a defect): a
`CONFIRM_DISTINCT_SIGNAL` row whose `signal_id` *is* already set (the normal,
expected post-creation terminal state after a successful R4C-authorized
creation, not an anomaly) falls through `human_review_queue.py`'s own
unmodified `derive_workflow_state()` to its generic `ACTIVE_REVIEW`
fallback, since that function does not recognize `CONFIRM_DISTINCT_SIGNAL`
at all. This is a pre-existing 9D display imprecision (parallel to how
`APPROVE_SIGNAL`+`signal_id` correctly becomes `RESOLVED_SIGNAL_CREATED`),
and this task's own governing design doc explicitly instructs `ReviewWorkflowState`
not be widened — fixing it here would violate that instruction. Left as an
explicitly documented limitation for a possible narrow future slice, not
addressed in R4D.

## 13. CLI behavior

`scripts/list_human_review_queue.py` gains one new `--state reconciliation`
choice (alongside the existing `active`/`all`/`resolved`, all three of which
are otherwise completely unchanged in behavior). It shows exactly the rows
where `list_reconciliation_review_items()`'s own `reconciliation_review_state`
is not `None` (`RECONCILIATION_REVIEW_REQUIRED` or
`DISTINCT_CONFIRMED_PENDING_SIGNAL`) — `CLEAR_TO_CREATE` items that function
also evaluates are not shown by this state, since reconciliation has nothing
new to say about them (the mission's own "avoid turning the queue into a
global Signal dedup scanner" instruction). A caller who wants the complete
picture, including `CLEAR` items, can call `list_reconciliation_review_items()`
directly.

Per-item rendering (`render_item_report()`, extended via `isinstance`
dispatch on the two possible item shapes) appends a "Reconciliation"
section to the existing, unmodified base report, containing: outcome,
review state (when set), blocking Signal ids, anchor reasons, current
fingerprint, stored fingerprint (when a confirmation exists), a
`Confirmation: CURRENT` / `Confirmation: STALE - RE-REVIEW REQUIRED` line,
and any `reconciliation_warnings`. No `--approve`/`--confirm-distinct`/
`--mark-duplicate`/`--reject`/`--defer` flag exists anywhere, and no
interactive prompt persists anything — this script only shows.

## 14. Filters/limit behavior

`--state reconciliation`'s `limit` is applied **after** eligibility
filtering and reconciliation evaluation, never as a raw SQL `LIMIT` before
either — the identical discipline `list_human_review_items()` and the CLI's
own `--state resolved` path already established, guarded by a dedicated
regression test
(`test_reconciliation_limit_does_not_truncate_when_newer_rows_are_clear`)
constructing the exact "newer non-attention rows would consume the limit
first" shape this project has now had to fix three times total.

## 15. Query behavior

Each eligible row costs one `session.get()`, one `get_latest_reviewer_action()`
query, and `find_reconciliation_candidates()`'s own already-established
three-batched-query shape (R2) — the same per-subject cost
`create_signal_from_approved_review()` itself already pays for one row.
**Not** batched across the whole eligible set (unlike the base queue's own
`_latest_actions_by_assertion()`), documented honestly rather than silently
accepted: batching would require either reimplementing R2's own candidate-
discovery logic here (forbidden) or changing R2 itself to accept a batch of
subjects (out of this slice's scope). For the realistic queue sizes this
pipeline has always assumed (single digits to low dozens, per
`human_review_queue.py`'s own documented trade-off), this is acceptable and
untested-regression-free; a future slice revisiting R2 itself could remove
this bound if queue sizes ever grow enough to matter.

## 16. Read-only proof

AST-verified: zero `session.add`/`.flush`/`.commit`/`.delete`/`.add_all`
calls anywhere in `human_review_reconciliation.py`
(`test_module_never_mutates_session`). Behaviorally verified: calling
`list_reconciliation_review_items()` leaves `session.new`/`session.dirty`
empty (`test_calling_the_function_leaves_no_pending_orm_changes`); the CLI's
own existing read-only-engine/schema-gate/no-backup/no-migration-import
tests (`tests/test_human_review_queue.py`, unmodified) continue to cover
the `--state reconciliation` path identically, since it reuses the same
`build_readonly_engine()`/`run_review_queue()` entry point; a
target/protected-database cross-contamination test
(`test_only_the_target_database_is_touched`) confirms byte-identical SHA-256
before/after for both databases.

## 17. Financial/title firewall

Behaviorally verified (`test_money_and_title_changes_never_affect_fingerprint_or_outcome`):
changing an existing candidate's `estimated_total_value_usd` and `title`
after an item is evaluated produces byte-identical `reconciliation_fingerprint`/
`reconciliation_outcome` on a second evaluation. AST-verified
(`test_no_financial_or_title_field_reaches_the_reconciliation_calls`): the
four R1/R2/R4A call sites this module makes never pass any financial or
title-shaped argument, mirroring the identical checks already established
in `tests/test_governed_signal_creation_reconciliation.py` for R3's own call
sites.

## 18. International independence

A non-US, non-USD, non-Latin-vendor-name synthetic case (Haneda Airport,
`Taiyo Safety Materials KK`) classifies identically to every domestic case
(`TestInternationalIndependence`). Structural, not prose-based: the module
never imports anything from `app.acquisition`
(`test_no_acquisition_import`, mirroring the identical existing check for
`human_review_queue.py` itself) and its own source names no MAC/MSP/FAA/
Runway Safe/USAspending/Granicus token anywhere.

## 19. MSP result

**Synthetic** (never the real database in the test suite):
`test_msp_shaped_resolved_duplicate_excluded_from_reconciliation_view`
reproduces the real, current MSP #222 shape (`APPROVE_SIGNAL` →
`MARK_DUPLICATE` → Signal #67, linked) and confirms it is excluded from
`list_reconciliation_review_items()`'s output entirely, with the base
`review_workflow_state` remaining `RESOLVED_DUPLICATE`.

**Real, read-only, outside the test suite** (this task's own investigation,
not a pytest test — see §26 of the mission and the discovery in §20 below):
`data/runway_safe.db` was inspected via a fresh, read-only `sqlite3`
connection (never the ORM, never `mode=rw`) three times across this task —
before any change, mid-task after discovering the ORM-compatibility defect
(§20), and after all work completed. SHA-256
`71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`, size
1789952 bytes, mtime 1787158044.8543456 — identical at every check. Direct
SQL confirmed: `reviewer_actions` has no `reconciliation_fingerprint` column
(R4B's migration has still not been applied to the real database — expected,
and exactly why this slice's own schema-gate fix in §20 matters);
`source_assertions.id=222` still has `signal_id=67`; the real
`reviewer_actions` history for #222 remains exactly `id=1 APPROVE_SIGNAL`,
`id=2 MARK_DUPLICATE(duplicate_of_signal_id=67, supersedes_action_id=1)`,
unchanged from every prior slice's own inspection.

## 20. A genuine, independently-discovered defect in the *existing* Slice 9D CLI (fixed)

Before writing any R4D code, this task's own "read fresh, do not trust
previous reports" instruction led to a direct, read-only probe: does the
*current, already-committed, unmodified* `scripts/list_human_review_queue.py`
actually work against the real database as it stands today? It does not.

`app/models/reviewer_action.py` has declared `reconciliation_fingerprint`
as a mapped `ReviewerAction` column since R4B (already committed at
`122abd5`). **Any** ORM query against that table — which
`list_review_workflow_items()` (and therefore every existing
`--state active|all|resolved` call, unconditionally) already performs —
now emits a `SELECT` naming that column. Verified directly, read-only,
against the real database:

    sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
    no such column: reviewer_actions.reconciliation_fingerprint

This means the currently-committed, in-production human review queue CLI
has been silently broken for **every** state since the R4B commit landed —
not a defect this slice introduces, but one it discovered and, since both
affected files (`app/services/human_review_queue.py`'s own caller,
`scripts/list_human_review_queue.py`) are within this slice's own explicit
file scope, fixed: `check_schema_readiness()` now also requires
`reviewer_actions.reconciliation_fingerprint` to exist (reusing R4B's own
already-proven, read-only `inspect()` from
`scripts/migrate_reconciliation_confirmation_slice_r4b.py`, never
reimplemented), refusing with the existing `REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED`
blocker — for every state, not only the new `reconciliation` one — instead
of crashing with an uncaught `OperationalError`. Two regression tests
(`TestRealMSPSchemaCompatibility`) reproduce the real database's exact
current shape (Slice 4/7 columns present, R4B column absent, via
`scripts/migrate_reconciliation_confirmation_slice_r4b.py`'s own
`downgrade()`) and confirm all four states now refuse gracefully.

**Scope note**: `app/services/human_review_queue.py` itself has, by
design, zero schema-gating logic (that responsibility belongs entirely to
the CLI script wrapping it — its own docstring already says so). This fix
is therefore scoped entirely to `scripts/list_human_review_queue.py`, and
`app/services/human_review_queue.py` remains completely unmodified,
consistent with §3's naming decision above. A narrower, theoretical
variant of this same class of gap (a database with Slice 4/7 columns but
predating Slice 9B's `reviewer_actions` table entirely) was considered but
not fixed — it has no empirical basis against the real database (which
does have the table) and no test in this project's history has ever
exercised it; flagged here as a known, pre-existing, undemonstrated
theoretical gap, not newly introduced or newly proven by this slice.

## 21. Focused tests

New file `tests/test_human_review_queue_reconciliation.py`: 39 tests,
covering every item in the mission's "at minimum" list (Section 32):
blocking case (candidate ids/anchor reasons/fingerprint exposed), clear
case, advisory-never-blocks (structural proof), current/stale/candidate-
added/candidate-removed/anchor-changed staleness, unrelated-churn-not-stale,
CLEAR-after-confirmation-is-moot, `ALREADY_LINKED`/`MARK_DUPLICATE`/resolved-
created exclusion, `DEFER`/`NEEDS_MORE_EVIDENCE` supersession, latest-action-
only (no historical lookup) plus an R4C-eligibility drift guard, multiple-
candidate determinism, no-ranking/no-local-fingerprint/no-acquisition-import
structural checks, the malformed-fingerprint evaluation (both the
unreachable-NULL case and the constructible-malformed-string case), existing
9D warnings preserved, CLI state-filter semantics (`reconciliation`/`all`/
`resolved`), limit-after-derived-filtering, three CLI text-output checks,
read-only/no-mutation/no-commit/cross-database-isolation, the financial/title
firewall (behavioral + AST), international independence, MSP synthetic
resolved-duplicate, and the two real-DB-schema-compatibility regression
tests for the defect found in §20.

Full focused suite (`test_human_review_queue.py`,
`test_human_review_queue_reconciliation.py`, R1, R2, R4A, R4B
persistence+migration, R3 original+reconciliation+migration, R4C,
physical installation reconciliation, static export, model contract):
**605 passed**.

## 22. Full pytest

**1601 passed** (baseline 1562 + 39 new tests in the new R4D file; no
existing test modified or removed).

## 23. py_compile

Clean on `app/services/human_review_reconciliation.py`,
`scripts/list_human_review_queue.py`, and
`tests/test_human_review_queue_reconciliation.py`.

## 24. git diff --check

Exit 0 — no whitespace errors (only harmless pre-existing LF→CRLF
line-ending warnings, consistent with every prior slice in this project).

## 25. Exact files changed

Modified:
- `scripts/list_human_review_queue.py` (new `--state reconciliation`;
  schema-gate fix, §20)

New:
- `app/services/human_review_reconciliation.py`
- `tests/test_human_review_queue_reconciliation.py`
- `docs/architecture/existing-signal-reconciliation-r4d-review-queue-report.md`
  (this file)

**Not modified**, contrary to the mission's own initial file-scope guess,
after fresh design-doc inspection (§3): `app/services/human_review_queue.py`,
`tests/test_human_review_queue.py`. No schema, model, or migration file
touched. R1, R2, R3, R4A, R4B, R4C production modules untouched.

## 26. Corrections/defects found

One genuine, independently-discovered, pre-existing defect in already-
committed code (§20): the Slice 9D queue CLI's schema gate did not account
for R4B's `reconciliation_fingerprint` column, making every state of the
*existing, unmodified* `scripts/list_human_review_queue.py` crash against
the real database as it stands today. Fixed, in-scope (the CLI script is
explicitly in this slice's own file list), with two regression tests
reproducing the exact real-DB schema shape.

One evaluated-and-rejected scenario, documented rather than coded (§12): a
`CONFIRM_DISTINCT_SIGNAL`+`NULL`-fingerprint row, found to be provably
unreachable given R4B's own DB CHECK constraint — no code added, one
regression test proving the constraint fires.

One documented, deliberately-not-fixed limitation (§12): `CONFIRM_DISTINCT_SIGNAL`+
`signal_id`-set (the normal post-creation state) still displays as generic
`ACTIVE_REVIEW` under the unmodified 9D classifier — the reviewed design
explicitly forbids widening that classifier in this slice, so this is left
as a documented gap for a possible future, narrowly-scoped slice.

No defect was found in R1, R2, R3, R4A, R4B, or R4C — all pre-existing
tests for every one of those slices passed unmodified against this slice's
own additions.

## 27. Readiness for R4D review checkpoint

Yes. Implementation, tests, and documentation are complete. No commit, no
push, no real database write or migration, no R4E work, per this slice's
own explicit stop boundary.

## 28. Recommended R4E scope

Per the design doc's own Section 20: a Reviewer-action CLI/API for
recording `CONFIRM_DISTINCT_SIGNAL`/`MARK_DUPLICATE` against the
reconciliation-aware view R4D now provides — the design doc itself notes
this would be the *first* such tool in the repository (no existing script
calls `record_reviewer_action()` today; every real `ReviewerAction` so far
was written via one-off interactive Python), so its own scope should be
sized accordingly small: likely a single new script accepting an explicit
`--source-assertion-id`, `--action`, `--reason`, `--reviewer`, and (for
`CONFIRM_DISTINCT_SIGNAL`) either a `--fingerprint` the reviewer copies from
R4D's own CLI output or a mode that recomputes it itself immediately before
recording (to minimize the window for a stale confirmation) — with the same
explicit, non-defaulted write-authorization flag convention every migration
script in this project already establishes, and the same "never commits
itself" discipline `record_reviewer_action()` already has.

## Review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R4D_CRITICAL_REVIEW_COMMIT_PUSH)

A fresh, adversarial re-read of every governed R4D file, the complete diff,
and the design doc's Section 15 (re-fetched verbatim, not from memory) found
**no defect in the core reconciliation logic** of
`app/services/human_review_reconciliation.py` — the architecture separation,
state vocabulary, eligibility rules, staleness detection, and
`CLEAR`-after-confirmation semantics all held under direct adversarial
attack. Findings below are two small documentation-drift corrections and
several genuine test-coverage gaps (untested but already-correct behavior) -
not behavioral defects in the production module.

### Architecture verification

Design doc Section 15 was re-fetched and compared word-for-word against the
implementation: `list_reconciliation_review_items()` (exact name),
`ReconciliationReviewState.RECONCILIATION_REVIEW_REQUIRED`/
`DISTINCT_CONFIRMED_PENDING_SIGNAL` (exact names), stale represented as an
annotation not a third state, `ReviewWorkflowState` not widened — a byte-for-
byte match. Verified `list_reconciliation_review_items()`'s eligible
population is a strict *subset* of `list_review_workflow_items()`'s own
output (built by filtering that function's own return value, never a
separate SQL query with independent population rules) — so no item can ever
"disappear" between the two systems; `list_review_workflow_items()` remains
the single complete picture, and R4D is a purely additive lens on top.

### Schema-gate re-verification (mission Section 16)

Re-confirmed by direct inspection: `check_schema_readiness()` runs before
`build_readonly_engine()`/`Session()` are ever constructed in
`run_review_queue()` (gate strictly precedes any ORM query, for every
state), checks Slice 4 + Slice 7 + R4B columns via three reused, already-
proven `inspect()` functions (no reimplementation), never imports or calls
`upgrade`/`downgrade` (grep-verified), never creates a backup, and the CLI's
own read-only engine remains unchanged. **Concretely proven against the real
database in this checkpoint** (not just a synthetic reproduction): calling
`run_review_queue()` for all four states (`active`/`all`/`resolved`/
`reconciliation`) against `data/runway_safe.db` itself now returns
`REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED` for every one of them, with zero
items and zero exceptions - the exact crash this slice's own fix eliminates,
demonstrated live rather than only inferred from a disposable-DB test.

### Real DB re-verification (mission Sections 17-18)

Read-only SQL only, three checks in this session (before any review-
checkpoint work, after the live schema-gate proof above, and after all
fixes): SHA-256 `71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`,
size 1789952 bytes, mtime 1787158044.8543456, identical at every check.
Direct SQL reconfirmed `source_assertions.id=222` → `signal_id=67`;
`reviewer_actions` history for #222 is exactly `id=1 APPROVE_SIGNAL`,
`id=2 MARK_DUPLICATE(duplicate_of_signal_id=67, supersedes_action_id=1)`;
Signal id 69 does not exist. Conceptual R4D state: `RESOLVED_DUPLICATE` via
the unmodified 9D workflow (confirmed structurally by
`test_msp_shaped_resolved_duplicate_excluded_from_reconciliation_view`,
never against the real database itself).

### Documentation-drift corrections (found and fixed)

Two small inconsistencies between the module's own docstrings and its
actual, already-correct code, both introduced during the implementation
task's own last-minute fix for the "NULL fingerprint is unreachable" finding
(the code was fixed at that time; two docstring references were not):

1. `ReconciliationReviewItem.reconciliation_warnings`'s own docstring still
   described a `CONFIRM_DISTINCT_SIGNAL`+`NULL`-fingerprint row as "only
   reachable by bypassing `record_reviewer_action()`'s own R4B validation
   directly via the ORM" - false; a dedicated test in the original
   implementation already proved even a direct ORM bypass is rejected by
   the DB CHECK constraint at commit time. Corrected to state the case is
   provably unreachable, not merely rare.
2. A module-level comment referenced a test named
   `TestLatestActionEligibilityMatchesR4C`, which does not exist - the real
   guard is `TestLatestActionOnly::test_eligibility_actions_match_r4c_valid_latest_actions_exactly`.
   Corrected to the real name.

### Test-coverage gaps found and closed (mission Sections 7, 22, 30)

1. **Staleness attack-matrix Sections D/E/F were entirely missing** from the
   R4D-specific test file (only A/B/C - candidate added/removed/anchor-
   reason-change - were covered, despite equivalent fixtures already having
   been built for the sibling R4C test suite and never ported over). Added
   three tests: `test_subject_runway_change_to_a_different_candidate_is_stale`,
   `test_subject_physical_installation_identity_change_is_stale`,
   `test_subject_governed_provenance_change_is_stale` - each isolates its
   one variable using a second, independently-anchored "stable" candidate
   present throughout, exactly the pattern already proven correct in the
   R4C test suite.
2. **Multiple-candidate determinism only checked candidate ID equality, not
   full result equality or that both candidates' own anchor reasons were
   present** (mission Section 30's own explicit "multiple-candidate test
   only asserting count, not identities/reasons" warning). Strengthened
   `test_two_independently_anchored_candidates_both_surfaced_sorted` to
   assert full dataclass equality across two calls (including the
   fingerprint) and that each candidate's own anchor-reason string is
   present (not just IDs, and not just one of them - no ranking, no
   truncation, proven directly).
3. **No CLI-level (`render_report()`) determinism test existed for
   `--state reconciliation`** (mission Section 22's own explicit "CLI output
   ordering" ask) - only the underlying module function had a determinism
   check. Added `test_cli_reconciliation_output_is_identical_across_repeated_reads`,
   mirroring the existing 9D `test_repeated_reads_are_identical` pattern for
   the new state.

### Evaluated, not fixed (structural, not a gap)

Mission Section 8's "advisory churn" scenario (a second, non-blocking,
compatibility-matched candidate whose fields then change, with the
confirmation remaining valid) cannot be meaningfully constructed in R4D at
all: since `category`/`claims`/`reference_year` are always empty in this
module's own subject construction (§12/§13 above), no candidate can ever
become compatibility-matched in the first place - `TestAdvisoryNeverBlocks`
already proves this structurally. The existing financial/title-firewall and
unrelated-field-churn tests cover the *spirit* of Section 8 (irrelevant data
changes never invalidate a confirmation) as completely as this module's own
architecture permits; no new test was added for a scenario this module
cannot produce.

### Final validation after review-checkpoint fixes

- Focused suite (same set as the implementation task, all now including the
  4 new tests): **609 passed**.
- Full pytest: **1605 passed** (1601 reported pre-review total + 4 new
  review-checkpoint tests: 3 staleness-attack-matrix + 1 CLI-determinism;
  no existing test removed or weakened).
- `py_compile`: clean.
- `git diff --check`: exit 0 (only pre-existing LF→CRLF warnings).
- Real database: unchanged throughout (proof above).

### Conclusion

R4D's production code is sound as implemented. Every review-checkpoint
finding was either a small, already-latent docstring inaccuracy (now fixed)
or a genuine test-coverage gap (now closed) - never a behavioral defect in
`app/services/human_review_reconciliation.py` or the
`scripts/list_human_review_queue.py` schema-gate fix.
`docs/architecture/existing-signal-reconciliation-r4d-review-queue-report.md`
and `tests/test_human_review_queue_reconciliation.py` are the only files
this checkpoint modified beyond the two docstring corrections in
`app/services/human_review_reconciliation.py` itself (both are still within
this slice's own already-governed file set - no new file entered scope).
