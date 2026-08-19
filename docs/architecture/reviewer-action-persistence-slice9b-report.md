# Reviewer Action Persistence — Slice 9B Report

Implements Slice 9B of
`docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md`:
an immutable, append-only audit trail recording what a human reviewer
decided about a governed `SourceAssertion`. No Signal is created, modified,
or referenced except by a read-only existence check for `MARK_DUPLICATE`. No
`SourceAssertion.signal_id`. No real DB write.

## 1. Starting HEAD

`main` at `4362a209b4aaaf469a4731a6d17c139b4060a9b9`, matched `origin/main`.
Baseline: 1084 passed.

## 2. ReviewerAction model

`app/models/reviewer_action.py`. Modeled directly on
`app.models.physical_installation_identity.InstallationAssertionLink` — the
exact, already-shipped structural precedent for a human-reviewed, append-only
decision about a `SourceAssertion` — rather than inventing a new audit
pattern:

```python
REVIEWER_ACTIONS = ("APPROVE_SIGNAL", "REJECT_SIGNAL", "DEFER", "NEEDS_MORE_EVIDENCE", "MARK_DUPLICATE")

class ReviewerAction(Base):
    __tablename__ = "reviewer_actions"
    id: Mapped[int]
    source_assertion_id: Mapped[int]           # FK -> source_assertions.id, required
    action: Mapped[str]                        # CHECK-constrained to REVIEWER_ACTIONS
    reason: Mapped[str]                         # required, free text
    reviewer: Mapped[str]                       # required, free text - no auth FK
    created_at: Mapped[datetime]
    supersedes_action_id: Mapped[Optional[int]] # self-FK, nullable
    duplicate_of_signal_id: Mapped[Optional[int]] # FK -> signals.id, nullable
```

`action` and `reviewer` are plain `String`/`Text`, not a Python `str` Enum,
deliberately: `InstallationAssertionLink.outcome` — the exact structurally
analogous column — is also a plain `String` + DB `CHECK`, not an Enum. Typed
`str` Enums in this codebase (`SignalCandidateOutcome`,
`PromotionPolicyOutcome`, `AttachmentOutcome`) belong to a different class of
code: deterministic *evaluation* outputs, not *persisted human decisions*.
Following the more specific, structurally identical precedent rather than
the more general Enum convention.

## 3. Action semantics

| Action | Meaning | Effect |
|---|---|---|
| `APPROVE_SIGNAL` | Human authorizes a later, separate, not-yet-built governed Signal-creation operation (Slice 9C). | No Signal created, updated, or published. No promotion/intelligence field touched. |
| `REJECT_SIGNAL` | Reviewer decides current evidence should not produce a Signal. | Evidence preserved; no Signal. |
| `DEFER` | No decision yet. | Evidence preserved; no Signal. |
| `NEEDS_MORE_EVIDENCE` | Material but insufficient; reviewer wants more before deciding. | Evidence preserved; no Signal. |
| `MARK_DUPLICATE` | This evidence concerns an existing Signal. | Must carry `duplicate_of_signal_id`; no new Signal. |

`ReviewerAction` never rewrites evidence, `raw_relevant_text`, Claims,
`identity_guard_decision`, `intelligence_review_decision`, or
`promotion_policy_decision` — proven by
`test_recording_an_action_never_mutates_source_assertion_fields` and the
evidence-preservation assertions in every action-type test.

## 4. Append-only design

Immutability enforced at the ORM level, not by convention, mirroring
`InstallationAssertionLink` exactly:

```python
@event.listens_for(ReviewerAction, "before_update")
def _prevent_reviewer_action_update(...): raise ValueError(...)

@event.listens_for(ReviewerAction, "before_delete")
def _prevent_reviewer_action_delete(...): raise ValueError(...)
```

A reviewer changing their mind appends a new row with `supersedes_action_id`
pointing at the prior one; the prior row is never edited or deleted. Proven
by `test_immutable_update_is_blocked`, `test_immutable_delete_is_blocked`,
and the multi-step supersession tests.

**Cascade / delete behavior** (verified in the Slice 9B review checkpoint,
`test_deleting_a_source_assertion_with_reviewer_actions_fails_safely` and
`test_deleting_a_duplicate_target_signal_fails_safely_with_fk_enforced`).
Neither relationship declares `cascade="delete"` or `"delete-orphan"`, and
audit history cannot silently disappear through either parent it references,
though the two protections work through different mechanisms:

- Deleting a `SourceAssertion` that still has `ReviewerAction` rows is
  blocked by SQLAlchemy's own relationship-aware dependency resolution — the
  `back_populates="reviewer_actions"`/`"source_assertion"` pair means the ORM
  attempts to re-persist the still-attached child before the parent delete,
  which hits this table's own `before_update` immutability listener and
  raises. Identical mechanism to the already-shipped
  `InstallationAssertionLink` → `SourceAssertion` precedent, reproduced and
  confirmed to apply here too.
- Deleting a `Signal` referenced by `duplicate_of_signal_id` has no such
  ORM-level awareness (`duplicate_of_signal` has no `back_populates` on
  `Signal`), so protection comes entirely from the raw DB-level `FOREIGN KEY`
  constraint, which requires `PRAGMA foreign_keys=ON` — always true in
  production via `app/database.py`'s own connect-event listener, but *not*
  automatic for a bare `create_engine("sqlite:///:memory:")` test engine (a
  well-known SQLAlchemy/SQLite gotcha, and true of every in-memory test
  engine in this codebase, including the pre-existing
  `test_physical_installation_reconciliation.py`). This was verified
  explicitly during review with a test engine that reproduces the
  production connect-listener; the deletion is correctly rejected with a
  `FOREIGN KEY constraint failed` error, and no row is lost.

## 5. Validation rules

All enforced in `app/services/reviewer_action_persistence.py::record_reviewer_action()`,
fail-closed (raises `ValueError`, never silently normalizes):

- **A.** `source_assertion` must already be persisted and exist in the session.
- **B/C/D.** `APPROVE_SIGNAL` requires exactly `identity_guard_decision == "ATTACH_CONFIRMED"`
  and `promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"`. `AUTO_ELIGIBLE`
  and `DO_NOT_PROMOTE` are both refused — see §22 for why this narrows the
  Slice 9 design doc's earlier, broader suggestion.
- **E.** `MARK_DUPLICATE` requires `duplicate_of_signal_id`, and the
  referenced `Signal` must exist.
- **F.** Any other action supplying `duplicate_of_signal_id` is refused.
  Also enforced redundantly at the DB level by two `CHECK` constraints
  (`ck_reviewer_actions_duplicate_target_required`,
  `ck_reviewer_actions_duplicate_target_only_for_duplicate`), proven by
  `test_duplicate_check_constraint_enforced_at_db_level_bypassing_the_service`.
- **G/H.** `supersedes_action_id`, if given, must reference an existing
  `ReviewerAction` belonging to the *same* `SourceAssertion` — cross-assertion
  supersession is refused (`test_cross_assertion_supersession_is_rejected`).
- `reason` and `reviewer` must both be non-blank.

## 6. Persistence API

`app/services/reviewer_action_persistence.py`:

```python
record_reviewer_action(session, source_assertion, *, action, reason, reviewer,
                        supersedes_action_id=None, duplicate_of_signal_id=None) -> ReviewerAction
```

Never commits; calls `session.flush()` only so a constraint violation
surfaces immediately. Never imports `app.database.SessionLocal`. Creates
exactly one `ReviewerAction` row per call. Never mutates `SourceAssertion` or
`Signal`. Modeled directly on
`app.services.physical_installation_reconciliation.record_reconciliation_decision`.

## 7. Latest/effective action semantics

`get_latest_reviewer_action(session, source_assertion_id) -> ReviewerAction | None`:
the most recently recorded row, ordered by `created_at DESC, id DESC` (same
tiebreak as `app.services.human_review_queue`'s own ordering). "Latest"
means **most recently recorded**, not "the unsuperseded terminal node reached
by walking `supersedes_action_id`" — with an append-only log, recency alone
already identifies current state; `supersedes_action_id` is optional
traceability metadata, never required for correctness. Proven deterministic
and chain-length-independent by
`test_latest_action_is_deterministic_ordered_by_created_at_then_id` and
`test_third_action_in_a_chain_becomes_the_new_latest`.

## 8. MSP #222 approval result

Conceptual (isolated test fixture reproducing #222's real governance shape:
`identity_guard_decision=ATTACH_CONFIRMED`,
`intelligence_review_decision=REVIEW_REQUIRED`,
`promotion_policy_decision=HUMAN_REVIEW_REQUIRED`) —
`test_approve_signal_valid_msp_222_shape` — and, for extra confidence, a
disposable-copy rehearsal against real data (§12): exactly one
`ReviewerAction` row recorded (`action=APPROVE_SIGNAL`), `SourceAssertion`
#222 completely unchanged, `Signal` count unchanged at 68→68. No Signal #69.

## 9. Rejection/defer/more-evidence behavior

`REJECT_SIGNAL`, `DEFER`, and `NEEDS_MORE_EVIDENCE` all bypass the approval
gate entirely (tested against a deliberately *invalid*-for-approval shape to
prove this) and are recorded unconditionally, with evidence and all governed
`SourceAssertion` fields provably unchanged
(`test_non_approval_actions_are_recorded_without_the_approval_gate`).

## 10. Duplicate behavior

`MARK_DUPLICATE` requires and validates `duplicate_of_signal_id` against a
real, existing `Signal`; creates no new Signal; never mutates the referenced
Signal (`test_mark_duplicate_never_mutates_the_referenced_signal`). Missing
target and wrong-action-with-target both fail closed.

## 11. Supersession

Proven with a 2-step chain (`DEFER` → `APPROVE_SIGNAL` superseding it) and a
3-step chain (`DEFER` → `NEEDS_MORE_EVIDENCE` → `APPROVE_SIGNAL`): every row
remains in the table, none are mutated, and `get_latest_reviewer_action()`
always resolves to the newest one. Cross-assertion supersession and
superseding a nonexistent action both fail closed.

## 12. Transaction ownership

`record_reviewer_action()` never calls `session.commit()`. A caller
`session.rollback()` after a call removes the pending row entirely
(`test_record_reviewer_action_never_commits`). A failed validation raises
before any row is added, leaving zero pending state
(`test_failed_validation_leaves_no_partial_action_history`) — no partial
action history is possible.

## 13. No-Signal proof

Structural (AST) proof that `app/services/reviewer_action_persistence.py`
contains zero `Signal(` construction calls
(`test_reviewer_action_persistence_module_never_constructs_a_signal`),
plus behavioral proof that `Signal` count is unchanged after every
non-duplicate action type, and unchanged (68→68, no #69) in the real-data
disposable rehearsal.

## 14. Migration

`scripts/migrate_reviewer_action_slice9b.py`, modeled on
`scripts/migrate_evidence_identity_slice6c.py` — the precedent for creating
a brand-new table via SQLAlchemy's own `CreateTable`/`CreateIndex`
compilation from `Base.metadata.tables["reviewer_actions"]`, so the created
schema (including both `CHECK` constraints) can never drift from the model
definition. Backup discipline (`backup_database()`, `--skip-backup` for
temp/disposable targets) was added on top of that precedent, matching
`scripts/migrate_promotion_policy_persistence_slice7.py`'s convention, since
this slice's own governing instruction requires it explicitly (the slice6c
migration itself predates that discipline and has none). Creates only the
`reviewer_actions` table and its three indexes/two `CHECK` constraints — no
`SourceAssertion.signal_id`, no `Signal` schema change.

## 15. Migration tests

`tests/test_reviewer_action_migration.py`, 11 tests: table creation with
correct columns/nullability; all three foreign keys (`source_assertion_id`,
self-FK `supersedes_action_id`, `Signal` FK `duplicate_of_signal_id`); all
three indexes; both `CHECK` constraints present in the stored schema text;
idempotent upgrade; downgrade removes only `reviewer_actions` and leaves
every other table/row byte-identical; realistic FK-satisfying inserts across
all three foreign keys succeed with `foreign_key_check` clean; an
out-of-range FK reference is rejected by SQLite itself; `--allow-database-write`
gate enforced.

## 16. Real DB unchanged

Read-only checkpoint before this task: sha256
`1eb956b3a17a866af94d9e5f7b1a0f388eb68a19e6642d551616a93d5b8de736`, size
1,769,472 bytes, mtime `1787145621.2311418`, `source_assertions`=222,
`signals`=68, `reviewer_actions` table **absent** (as expected — no
migration has ever been applied to the real database). Identical at the
final checkpoint after this task's disposable-copy rehearsal (§8, §12) and
again after the Slice 9B review checkpoint's own repeated rehearsal
(APPROVE_SIGNAL on #222: 1 ReviewerAction row, Signal 68→68, #222 completely
unchanged, `foreign_key_check=[]`, `integrity_check=('ok',)`). No migration,
no write, ever touched `data/runway_safe.db` itself.

## 17. Focused tests

`tests/test_reviewer_action_persistence.py` (45 tests, including two added
during the Slice 9B review checkpoint for the cascade/delete-behavior finding
in §4) + `tests/test_reviewer_action_migration.py` (11 tests) = **56 new
tests**. Command: `python -m pytest -q tests/test_reviewer_action_persistence.py
tests/test_reviewer_action_migration.py tests/test_model_contract.py
tests/test_human_review_queue.py tests/test_promotion_policy_persistence_migration.py
tests/test_intelligence_review_persistence_migration.py
tests/test_physical_installation_reconciliation.py tests/test_source_assertions.py
tests/test_signal_publication_migration.py tests/test_static_export.py` →
**181 passed**.

## 18. Full pytest

**1140 passed** (1084 baseline + 56 new), 0 failed, in 106.10s.

## 19. py_compile

Clean across all seven changed/new Python files.

## 20. git diff --check

Clean (zero whitespace errors); only benign LF→CRLF advisory warnings on the
five new files.

## 21. Exact files changed

- `app/models/reviewer_action.py` — new
- `app/models/__init__.py` — modified (export `ReviewerAction`)
- `app/models/source_assertion.py` — modified (`reviewer_actions` relationship only)
- `app/services/reviewer_action_persistence.py` — new
- `scripts/migrate_reviewer_action_slice9b.py` — new
- `tests/test_reviewer_action_persistence.py` — new
- `tests/test_reviewer_action_migration.py` — new
- `tests/test_model_contract.py` — modified (one new table's contract + relationships)
- `docs/architecture/reviewer-action-persistence-slice9b-report.md` — new (this file)

## 22. Design corrections discovered

One deliberate narrowing relative to the Slice 9 design document, made per
this slice's own explicit governing instruction (§6D: "AUTO_ELIGIBLE belongs
to future automation/audit handling, not the active human queue route
unless the design explicitly allows audit review separately"): the design
doc's own §4 had recommended accepting `promotion_policy_decision IN
("HUMAN_REVIEW_REQUIRED", "AUTO_ELIGIBLE")` for human approval, reasoning
that a human is always allowed to be more careful than automation requires.
This slice implements the stricter reading instead — **only**
`HUMAN_REVIEW_REQUIRED` is accepted by `record_reviewer_action()`'s approval
gate; `AUTO_ELIGIBLE` is refused, reserved for a future, separately-designed
automation/audit route. This is documented directly in the persistence
module's own docstring so it is not silently inconsistent with the design
doc. No other correction was needed; the `InstallationAssertionLink` and
`Signal.installation_id` precedents the design doc pointed to translated
directly into working code with no surprises.

**Review checkpoint correction** (this task): no defect in shipped behavior
was found, but a real test-coverage gap was identified and closed — neither
the implementation task nor its own tests had explicitly verified that
deleting a `SourceAssertion` or a duplicate-target `Signal` with existing
`ReviewerAction` rows fails safely rather than silently losing audit
history. Both were verified empirically during review (§4's "Cascade /
delete behavior" addendum) and are now covered by two new tests,
`test_deleting_a_source_assertion_with_reviewer_actions_fails_safely` and
`test_deleting_a_duplicate_target_signal_fails_safely_with_fk_enforced`. No
model, service, or migration code changed as a result — the underlying
behavior was already correct.

## 23. Ready for review/commit

Yes. All required verification passed: full suite (1138/1138), focused
suite (179/179), py_compile, git diff --check, migration rehearsal
(disposable synthetic DB + disposable copy of real data), real DB confirmed
byte-unchanged. No Signal was created, modified, or published. No
`SourceAssertion` field was mutated. Awaiting the separate review/commit/push
authorization per this project's established one-task-per-write-boundary
discipline.

## 24. Exact Slice 9C prerequisites

Slice 9C (human-approved governed Signal creation, `create_signal_from_approved_review()`)
can now be built on top of exactly two already-shipped pieces: `Signal.published`
(Slice 9A, defaulting new governed Signals to `False`) and `ReviewerAction`
(this slice, providing `get_latest_reviewer_action()` to check for
`APPROVE_SIGNAL`). Its own remaining prerequisites, not yet built: the
`SourceAssertion.signal_id` nullable FK (provenance + idempotency guard,
modeled on `Signal.installation_id`), and the governed Signal-field mapping
logic itself (per the design doc's §6 field-mapping matrix — financial
fields left NULL, `confirmed_vendor` only from an explicit award-confirming
relationship claim, etc.). The transaction must be atomic: the `ReviewerAction`
check, the `Signal` insert, and the `SourceAssertion.signal_id` write all in
one bounded transaction, per the design doc's own §10 reasoning.
