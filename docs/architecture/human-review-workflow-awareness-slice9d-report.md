# Human Review Queue Workflow Awareness — Slice 9D Report

Implements Slice 9D of
`docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md`:
upgrades the read-only Human Review Queue (Slice 8) to understand immutable
`ReviewerAction` history (Slice 9B) and `SourceAssertion.signal_id` (Slice
9C), so resolved items stop appearing forever. Strictly read-only — no
write of any kind, no schema change, no migration.

## 1. Starting HEAD

`main` at `6a49a00570c880fc5af2e43a2087c59235b9284e`, matched `origin/main`.
Baseline: 1192 passed.

## 2. Existing queue defect

`app.services.human_review_queue.list_human_review_items()` filtered purely
on `SourceAssertion.promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"` —
an append-only classification computed once and never revisited. It had no
concept of `ReviewerAction` or `signal_id` at all. Real `SourceAssertion
#222` proved this exactly: resolved by a human as `MARK_DUPLICATE` (linked
to existing Signal #67), it kept appearing in the active queue forever,
because nothing about that resolution ever changes
`promotion_policy_decision`.

## 3. Workflow-state vocabulary

`ReviewWorkflowState` (`app/services/human_review_queue.py`), a `str, Enum`
— purely derived, never persisted, no workflow engine, no transitions:

`ACTIVE_REVIEW`, `DEFERRED`, `NEEDS_MORE_EVIDENCE`,
`APPROVED_PENDING_SIGNAL`, `RESOLVED_REJECTED`, `RESOLVED_DUPLICATE`,
`RESOLVED_SIGNAL_CREATED` — exactly the task's own candidate vocabulary,
adopted unchanged as the smallest set that distinguishes every
`ReviewerAction` outcome plus the never-reviewed case.

## 4. Active-queue semantics

`derive_workflow_state(latest_action, signal_id)` — a pure function, no I/O:

| Latest action | signal_id | State |
|---|---|---|
| none | — | `ACTIVE_REVIEW` |
| `DEFER` | — | `DEFERRED` |
| `NEEDS_MORE_EVIDENCE` | — | `NEEDS_MORE_EVIDENCE` |
| `APPROVE_SIGNAL` | NULL | `APPROVED_PENDING_SIGNAL` |
| `APPROVE_SIGNAL` | set | `RESOLVED_SIGNAL_CREATED` |
| `REJECT_SIGNAL` | — | `RESOLVED_REJECTED` |
| `MARK_DUPLICATE` | — | `RESOLVED_DUPLICATE` |

Default active queue = `{ACTIVE_REVIEW, NEEDS_MORE_EVIDENCE}` only.
`NEEDS_MORE_EVIDENCE` is deliberately **included**: this slice adds no
acquisition trigger and no scheduler, so excluding it would make an item
vanish from all visibility the moment a reviewer asks for more evidence,
with no committed path back into view. `DEFERRED` and
`APPROVED_PENDING_SIGNAL` are excluded per the task's own explicit
recommendation — a human already made a decision (defer, or approve); what
remains is not a second review. All `RESOLVED_*` states are excluded.

## 5. `get_latest_reviewer_action()` reuse

No reviewer-ordering logic is duplicated. `_latest_actions_by_assertion()`
is a batched equivalent — one query for the whole candidate id set, ordered
by the identical `(created_at DESC, id DESC)` tiebreak
`get_latest_reviewer_action()` uses, keeping only the first (= latest) row
per `source_assertion_id` — avoiding N+1 across a queue page without
re-implementing the ordering rule. `TestLatestReviewerActionReuse` proves
the batched result agrees with `get_latest_reviewer_action()` row-for-row.

## 6. Queue item changes

`HumanReviewItem` gained three read-only fields: `latest_reviewer_action`
(the action string, or `None`), `linked_signal_id`
(`SourceAssertion.signal_id`, verbatim), `review_workflow_state` (the
derived state's `.value`). No second domain model — same flat, frozen
dataclass shape as before.

## 7. DEFER decision

Excluded from the default active queue (§4). Still visible via
`list_review_workflow_items()` (the "all" view) or `--state all`/`--state
deferred`-equivalent inspection. No automatic time-based resurfacing — no
scheduler exists or is added in this slice, per instruction.

## 8. NEEDS_MORE_EVIDENCE decision

Included in the default active queue (§4), visually distinguished via its
own `review_workflow_state` value in both the DTO and the CLI's "Reviewer
workflow" section. No acquisition trigger added.

## 9. APPROVE_SIGNAL semantics

Split into two states by `signal_id`: `APPROVED_PENDING_SIGNAL` (decision
made, governed Signal creation still outstanding — a distinct operation, not
a second review) and `RESOLVED_SIGNAL_CREATED` (fully resolved). Both are
excluded from the default active queue, matching the task's own strong
preference that the default queue contain only items still needing a
*decision*.

## 10. MARK_DUPLICATE semantics

Always `RESOLVED_DUPLICATE`, excluded from the default active queue. The
target Signal is never mutated by the queue — only read, to display
`linked_signal_id`. Consistency between `duplicate_of_signal_id` (on the
`ReviewerAction`) and `SourceAssertion.signal_id` (the actual link) is
checked and surfaced, never silently trusted or repaired (§12).

## 11. REJECT semantics

Always `RESOLVED_REJECTED`, excluded from the default active queue. Evidence
remains fully preserved and queryable — nothing about `REJECT_SIGNAL`
deletes or hides the underlying `SourceAssertion`.

## 12. Invariant warnings

Two new classes, both fail-visible, never auto-repaired, never mutate the
database:

- `MARK_DUPLICATE` whose `duplicate_of_signal_id` disagrees with the actual
  `signal_id` link (`"target and link disagree"`).
- `DEFER`/`NEEDS_MORE_EVIDENCE`/`REJECT_SIGNAL` coexisting with a non-NULL
  `signal_id` (`"these should be mutually exclusive"`) — none of those three
  actions should ever have authorized a Signal link.

`APPROVE_SIGNAL` linked to an "unrelated/drifted" Signal is explicitly
**not** checked here — there is no second, independent source of truth at
the queue layer for which Signal *should* be linked; that drift check
already lives at Slice 9C's own creation-time compatibility-signature
comparison (`create_signal_from_approved_review()`).

## 13. MSP #222 regression

`TestMSP222ExactRegression` reproduces the exact real resolution
(`ReviewerAction #1 APPROVE_SIGNAL` → superseded by `#2 MARK_DUPLICATE`,
linked via `link_source_assertion_to_duplicate_signal()`) against an
isolated fixture using the real MSP PDF and real extraction path. Result:
default active queue = `()`; full workflow view shows exactly one item,
`RESOLVED_DUPLICATE`, `latest_reviewer_action="MARK_DUPLICATE"`,
`linked_signal_id` matching the target, zero invariant warnings; evidence
(`raw_relevant_text`, `identity_guard_decision`) fully preserved.

## 14. Real DB result

Ran the updated CLI against the real database, read-only:
`--state active` → *"Human review queue is empty. Nothing currently
requires a human decision."* — exactly the fix this slice exists to
deliver. `--state resolved` → exactly one item, `SourceAssertion #222`,
`review_workflow_state=RESOLVED_DUPLICATE`,
`latest_reviewer_action=MARK_DUPLICATE`, `linked_signal_id=67`, zero
invariant warnings, all original evidence/claim rendering preserved
unchanged. sha256 identical before and after every read
(`71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`).

One console-only cosmetic issue hit during manual verification, already
diagnosed in this project as harmless and pre-existing: printing MSP's raw
`☐`/`☒` checkbox characters directly to a Windows `cp1252` console crashes
`print()` — unrelated to this slice's own code (it happens in the original,
unmodified evidence-text rendering, and was never reachable via
`--state active`'s empty-message path before now). Verified via a UTF-8 file
capture instead; the real database was confirmed byte-unchanged immediately
before and after.

## 15. Read-only safety

AST-verified (`TestNoWrites::test_queue_module_never_constructs_a_reviewer_action_or_mutates_session`):
zero `add`/`flush`/`commit`/`delete`/`update` attribute accesses anywhere in
`app/services/human_review_queue.py`. Behaviorally verified: `SourceAssertion`
and `Signal` row sets are byte-identical before/after every queue function
call, in isolated tests and against the real database. The CLI's own
pre-existing read-only-engine/no-backup/no-migration-import guarantees
(Slice 8) are untouched and still hold.

## 16. International readiness

`derive_workflow_state()` reads only `action`/`signal_id` — no MAC, MSP, or
US-specific logic anywhere in `app/services/human_review_queue.py`
(confirmed by the pre-existing AST import-boundary test, unchanged).
`TestInternationalWorkflowReadiness` proves a synthetic non-MAC,
non-US-parser item (`parser_identifier="haneda-authority-v1"`) classifies
identically to the MSP case.

## 17. Focused tests

`tests/test_human_review_queue.py`: 74 tests total (41 pre-existing,
unmodified and still passing, + 31 new Slice 9D tests + 2 review-checkpoint
regression tests) covering all 7 derived states in isolation, the exact MSP
#222 regression, default-queue inclusion/exclusion for every state, both
invariant-warning classes, batched-vs-single-lookup equivalence,
ordering/limit preservation (including the adversarial
newer-rows-resolved/older-rows-active shape for both the service and the
CLI's `--state resolved` path), malformed/`AUTO_ELIGIBLE`/`DO_NOT_PROMOTE`
exclusion, no-DB-write and no-Signal-write proofs (behavioral + AST), CLI
read-only + `--state` filtering, and international readiness.

Command: `python -m pytest -q tests/test_human_review_queue.py
tests/test_reviewer_action_persistence.py tests/test_reviewer_action_migration.py
tests/test_governed_signal_creation.py tests/test_governed_signal_creation_migration.py
tests/test_signal_publication_migration.py tests/test_model_contract.py
tests/test_capture_mac_discovery.py tests/test_static_export.py` → **278 passed**.

## 18. Full pytest

**1225 passed** (1192 baseline + 33 new: 31 from implementation + 2 from
review-checkpoint corrections), 0 failed, in 119.79s.

## 19. py_compile

Clean across all three changed files.

## 20. git diff --check

Clean; only benign LF→CRLF advisory warnings.

## 21. Exact files changed

- `app/services/human_review_queue.py` — modified (workflow-state vocabulary, batched latest-action lookup, extended `HumanReviewItem`, new `list_review_workflow_items()`, refined `list_human_review_items()` semantics)
- `scripts/list_human_review_queue.py` — modified (`--state active|all|resolved`, "Reviewer workflow" report section, state-aware empty message)
- `tests/test_human_review_queue.py` — modified (31 new tests, 3 new imports)
- `docs/architecture/human-review-workflow-awareness-slice9d-report.md` — new (this file)

No new module was needed — the workflow-state helper lives directly in
`app/services/human_review_queue.py` rather than a separate file, since it
is small, single-purpose, and used only by that module and its own tests.

## 22. git status

All four changes are unstaged modifications/additions in the working tree;
no commit was made. Pre-existing untracked documentation/UI files from prior
sessions remain untouched and unrelated to this task.

## 23. Design corrections discovered

One genuine engineering trade-off, made deliberately in the original
implementation and documented in the code itself: `list_human_review_items(limit=N)`
can no longer apply `LIMIT N` directly in SQL, because determining workflow
state requires reading `ReviewerAction` history first (the same batched
lookup `list_review_workflow_items()` performs) — the SQL-level `LIMIT` from
Slice 8's own version would risk returning `N` rows that all turn out to be
resolved, silently truncating the real active list. `limit` now bounds the
result *after* workflow-state filtering. Ordering remains fully
deterministic either way; real-world row counts (single digits to low
dozens) make this an acceptable, correctness-preserving cost.

**Review-checkpoint correction (real defect, found and fixed):**
`scripts/list_human_review_queue.py::run_review_queue()`'s `"resolved"`
branch had exactly the same failure shape the queue service was carefully
built to avoid, but the CLI's own wiring didn't inherit the fix: it passed
`config.limit` straight into `list_review_workflow_items()`, whose `limit`
bounds the *raw* `HUMAN_REVIEW_REQUIRED` SQL query before any state
filtering happens. If the newest rows (fetched first) were all
non-resolved, a small limit could consume them entirely and leave the
resolved-state filter with nothing to find — reproduced directly:
`--state resolved --limit 3` against a fixture with 3 newer active rows and
1 older resolved row returned **0 items** instead of the 1 that actually
existed. `--state all` was unaffected (it does not sub-filter by state, so
SQL-level limiting there is correct and intended). Fixed by fetching the
full `HUMAN_REVIEW_REQUIRED` set unfiltered for the `"resolved"` branch,
filtering to resolved states, and applying `limit` in Python — mirroring
`list_human_review_items()`'s own established fix exactly. Two new
regression tests added:
`TestOrderingLimitAndExclusions::test_limit_does_not_truncate_active_items_when_newer_rows_are_resolved`
(service-level, proves the pattern the service already avoided) and
`TestCLIReadOnlyAndStateFilter::test_resolved_state_limit_does_not_truncate_when_newer_rows_are_active`
(CLI-level, proves the specific bug found and fixed this review).

## 24. Ready for checkpoint

Yes. All required verification passed: full suite (**1225/1225**, 1223 +
2 review-added regression tests), focused suite (278/278), py_compile, git
diff --check, real-DB read-only pilot re-run fresh this review (active
queue correctly empty, resolved view correctly shows #222 as
`RESOLVED_DUPLICATE` linked to Signal #67, zero invariant warnings), real DB
confirmed byte-unchanged throughout (sha256
`71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710` before
and after every read). No `ReviewerAction`, `Signal`, or `SourceAssertion`
write occurred anywhere in this task or its review.

## 25. Recommended next intelligence-engine step

A future, separately-authorized reviewer-CLI/workflow slice to actually
*record* reviewer actions interactively (this slice only reads); until then,
`record_reviewer_action()`/`link_source_assertion_to_duplicate_signal()`
remain callable only via direct, explicitly-authorized scripts, matching
this project's established one-write-boundary-per-task discipline. A
smaller, optional follow-up worth considering separately: a `--state
deferred` / `--state needs-more-evidence` CLI split (currently folded into
`--state all`) if the pilot's real usage shows a need for finer-grained
default views.
