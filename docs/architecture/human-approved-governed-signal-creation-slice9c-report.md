# Human-Approved Governed Signal Creation — Slice 9C Report

Implements Slice 9C of
`docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md`:
the first slice allowed to create a `Signal` through the governed
discovery/intelligence pipeline. Fail-closed on every governance gate;
creates exactly one internal (`published=False`) Signal per approved
`SourceAssertion`; idempotent; atomic; never touches the six pre-existing
Signal-write paths.

## 1. Starting HEAD

`main` at `20f5420a53aefafac682ee22c0e9e65936c873e0`, matched `origin/main`.
Baseline: 1140 passed.

## 2. `SourceAssertion.signal_id` design

`app/models/source_assertion.py`: `signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)`.
NULL means no Signal has been created from this governed evidence yet;
non-NULL both proves which evidence produced the Signal it points at and
serves as this slice's idempotency guard. Modeled directly on
`Signal.installation_id`'s already-shipped cardinality precedent: many
`SourceAssertion` rows may point at the same Signal (a later corroborating
piece of evidence), but one `SourceAssertion` points at, at most, one
Signal. No generalized evidence-link table was needed — the design doc's
own reasoning (repeated here after re-verifying it against the current
schema) held: a plain FK on the "many" side answers both required questions
("which evidence caused this Signal" via reverse query, "has this evidence
already produced a Signal" via a direct `IS NOT NULL` check) without
inventing new infrastructure. A reverse relationship,
`Signal.supporting_source_assertions`, was added (matching the design doc's
own suggested name) since it directly answers the first question without
any extra cost.

**Cascade/delete-behavior finding, review checkpoint** (not caught during
implementation): without `passive_deletes=True` on
`Signal.supporting_source_assertions`, SQLAlchemy's default relationship
management issues `UPDATE source_assertions SET signal_id=NULL` for every
referencing row *before* deleting the parent Signal — silently discarding
the governed provenance link instead of blocking the delete, verified
empirically (a raw `session.delete(signal)` + `commit()` succeeded and left
`source_assertion.signal_id` reset to `NULL`). This is a real defect
relative to this slice's own stated purpose: a durable, evidence-preserving
provenance link. Fixed by adding `passive_deletes=True` to the relationship,
which makes SQLAlchemy leave the FK alone on delete and defer entirely to
the real database-level `FOREIGN KEY` constraint — which, with no `ON
DELETE` clause, correctly refuses the delete outright while any
`SourceAssertion` still references it (re-verified empirically: the same
delete now raises `IntegrityError`, and both the Signal and the
`signal_id` link survive a caller rollback). No code path in this
repository deletes a Signal today, so this was never reachable in practice,
but the invariant must hold for a future caller regardless — matching the
same class of protection Slice 9B already established between
`ReviewerAction` and `SourceAssertion`. Now covered by
`test_deleting_a_signal_with_supporting_source_assertions_fails_safely`.

## 3. Migration

`scripts/migrate_governed_signal_creation_slice9c.py`, modeled on the
additive-column pattern (`migrate_promotion_policy_persistence_slice7.py`):
explicit `--database`, `--allow-database-write` gate, backup unless
`--skip-backup`, idempotent `upgrade()` (`ALTER TABLE source_assertions ADD
COLUMN signal_id INTEGER REFERENCES signals(id)` + a dedicated index),
`downgrade()` via the established table-rebuild technique. Adds only this
one column and its index — no `Signal` schema change, no other table
touched.

**A real defect was found and fixed during implementation**, not review: the
reused `_drop_column_via_rebuild()` helper (copied structurally from
`migrate_reviewer_action_slice9b.py`) blindly replayed every existing
index's stored `CREATE INDEX` text when rebuilding the table for downgrade.
Every column this specific rebuild technique had previously been used to
drop (Slices 1/4/7/9B's own additive columns) was itself unindexed, so this
never mattered before. `signal_id` is the first case where the column being
dropped is also indexed — the naive replay tried to recreate
`ix_source_assertions_signal_id` referencing a column that no longer
existed, and downgrade failed with `sqlite3.OperationalError: no such
column: signal_id`. Fixed by determining index membership from
`PRAGMA index_info(name)` (real column membership) rather than text-matching
the stored SQL, and excluding any index that references the dropped column.
Caught immediately by a direct isolated rehearsal before any test was
written; `test_downgrade_is_exactly_reversible` now explicitly asserts the
three surviving indexes and the absence of the dropped one.

## 4. Governed creation API

`app/services/governed_signal_creation.py`:

```python
create_signal_from_approved_review(
    session, source_assertion, *,
    title, category, confidence,                      # required
    status=None, runway_id=None,
    likely_supplier=None, supplier=None, supplier_reason=None,
    notes=None, source_notes=None,
    target_year=None, planning_year=None, procurement_year=None,
    construction_start=None, completion_date=None,
    manual_year_estimate=None, last_verified_at=None,
) -> GovernedSignalCreationResult
```

No caller-suppliable `published` parameter and no financial-value parameter
of any kind exist — not merely defaulted safely, but structurally absent
from the signature (§9). No raw `Signal` ORM object is accepted from the
caller. `confirmed_vendor` is likewise not a parameter in this slice: no
evidence pathway currently governed by this pipeline produces an
award-confirming relationship claim, and this slice does not invent a way to
validate one.

A second, small, explicit function, `link_source_assertion_to_duplicate_signal()`,
handles `MARK_DUPLICATE` (§8) — see §9.

## 5. Governance gates

`create_signal_from_approved_review()` fails closed unless **all** hold:

1. `identity_guard_decision == "ATTACH_CONFIRMED"`
2. `intelligence_review_decision == "REVIEW_REQUIRED"`
3. `promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"` — exactly this
   value; `AUTO_ELIGIBLE` and `DO_NOT_PROMOTE` are both refused, matching
   Slice 9B's own narrowing of the design doc's earlier broader suggestion.
4. The latest `ReviewerAction` (via Slice 9B's `get_latest_reviewer_action()`)
   is `APPROVE_SIGNAL` and belongs to this `SourceAssertion`.
5. `source_assertion.airport_id is not None` (defensive; should always hold
   once `ATTACH_CONFIRMED`).

All five checked against the SourceAssertion's *current* persisted state at
call time, not assumed from the fact that an approval was once recorded —
proven by `test_invalid_identity_decision_blocks_creation` and its three
siblings, which record a valid approval first and then simulate drift by
mutating the governance field afterward.

## 6. Reviewer-action dependency

Reuses `app.services.reviewer_action_persistence.get_latest_reviewer_action()`
verbatim — no action-ordering logic is duplicated. A historical
`APPROVE_SIGNAL` superseded by a later action (e.g. `REJECT_SIGNAL`) is not
sufficient (`test_historical_approval_superseded_by_later_reject_blocks_creation`).
`ReviewerAction` rows are never mutated by this service — verified
field-for-field unchanged after a successful creation
(`test_reviewer_action_rows_unchanged_by_signal_creation`).

## 7. Human-selected fields

`title`, `category`, `confidence` are required; the rest optional. No field
is inferred from Claims or raw text — no MAC-specific keyword logic exists
in this module. `confidence` is validated against the real, already-existing
closed vocabulary `Signal.DEFAULT_SCORE_BY_CONFIDENCE` (`high`/`medium`/`low`);
`probability_score` is derived from it, not independently invented. `title`
and `category` are validated non-blank — matching this repository's own
established convention that `category` is a loosely-typed string with
graceful UI fallback (`app/static_export/build.py::_category_view()` never
raises on an unrecognized value), not a closed enum anywhere else in the
codebase, so this slice does not invent one either. `status`, if given, is
rejected when it names a state mere review approval cannot establish (§21).

## 8. Signal field mapping

Directly follows the design doc's own S6 matrix, re-verified against the
current model rather than assumed:

| Field | Source |
|---|---|
| `airport_id` | `source_assertion.airport_id` (direct) |
| `source_id` | `source_assertion.source_id` (direct) |
| `title`, `category`, `confidence`, `status` | human-selected (required/optional) |
| `probability_score` | derived from `confidence` via `DEFAULT_SCORE_BY_CONFIDENCE` |
| `likely_supplier`, `supplier`, `supplier_reason` | human-selected, optional |
| `runway_id` | human-selected, validated to belong to the same airport |
| `notes`, `source_notes`, `manual_year_estimate`, `last_verified_at` | human-selected, optional |
| `target_year`, `planning_year`, `procurement_year`, `construction_start`, `completion_date` | human-selected, optional |
| `confirmed_vendor` | **not a parameter in this slice** — see §4 |
| `estimated_total_value_usd`, `estimated_emas_value_usd` | **not parameters at all** — see §4, §20 |
| `installation_id` | not applicable at creation time |
| `published` | always `False`, hardcoded — see §9 |

## 9. MSP #222 mapping

Real-data disposable rehearsal (§19), not just an isolated fixture: with
`#222`'s real governance shape (`ATTACH_CONFIRMED`/`REVIEW_REQUIRED`/
`HUMAN_REVIEW_REQUIRED`) and a recorded `APPROVE_SIGNAL`, the created Signal
(#69 in the disposable copy) has `airport_id=45`, `source_id=70`,
`likely_supplier="Runway Safe"`, `confirmed_vendor=None`,
`estimated_total_value_usd=None`, `estimated_emas_value_usd=None`,
`published=0`. `source_assertion.signal_id=69`. Exactly matches the expected
mapping.

## 10. Financial safety

No parameter exists through which any dollar amount — safe-looking or not —
can reach a created Signal (`test_service_has_no_parameter_capable_of_setting_a_financial_value`
inspects the function signature directly). Verified for the MSP case
($1.59M advance-deposit / $19M CIP ceiling: both `None`) and the SFO-style
adversarial case (`test_sfo_style_unlabeled_large_amount_never_reaches_a_financial_field`:
a $40,000,000 figure embedded in an adversarial title/notes string, still
`None`).

## 11. Procedural/temporal safety

`_DISALLOWED_INITIAL_STATUSES = {"completed", "awarded", "executed", "contracted"}`
(case-insensitive) is refused — a mere human review approval cannot
establish that something was built, awarded, or executed. `"completed"` in
particular carries a separate, load-bearing meaning elsewhere
(`scripts/graduate_signal_to_installation.py`'s own idempotency check reads
`status == "completed"`), so allowing `APPROVE_SIGNAL` alone to set it would
misrepresent "worth tracking" as "this happened." MSP's own pending/planned
status (`"identified"`) is accepted unchanged
(`test_planned_pending_status_is_accepted_unchanged`).

## 12. Publication safety

`published=False` is hardcoded inside the service, never a caller-suppliable
default. `test_service_has_no_parameter_capable_of_setting_a_financial_value`'s
sibling for publication is structural too: `published` simply does not
appear in the function signature at all — there is no argument to forget.
Behaviorally confirmed both by direct attribute check
(`test_created_signal_is_explicitly_unpublished`) and by running the real
static export against the created Signal
(`test_public_export_excludes_governed_signal`, and the real-data rehearsal
in §19 below).

## 13. Provenance

`signal.source_id == source_assertion.source_id` and
`source_assertion.signal_id == signal.id`, both verified. Reverse lookup via
`Signal.supporting_source_assertions` returns exactly the originating
`SourceAssertion` (`test_reverse_provenance_via_supporting_source_assertions`).

## 14. Idempotency

A second call with the same request against a `SourceAssertion` whose
`signal_id` is already set reuses the existing Signal (`created=False`, same
`Signal.id`, no new row) — proven both in isolation
(`test_idempotent_repeat_reuses_the_same_signal`) and in the real-data
disposable rehearsal (§19: signal count stayed 69 on the second call).

## 15. Drift handling

Compatibility is judged on `(title, category, confidence)` — the three
required, human-selected identity fields. A second call for the same
`SourceAssertion` with a different value for any of them fails closed
(`test_incompatible_existing_signal_id_fails_closed_on_drift`), never
silently overwriting the link or creating a second Signal. A `signal_id`
pointing at a Signal that no longer exists also fails closed
(`test_signal_id_pointing_at_deleted_signal_fails_closed`).

## 16. Transaction atomicity

Signal creation, the `session.flush()` to obtain its id, and the
`source_assertion.signal_id` write all happen inside the caller's single
uncommitted transaction — no `session.commit()` anywhere in the service. A
caller `session.rollback()` after a call leaves neither a Signal nor a link
(`test_rollback_leaves_no_signal_and_no_link`,
`test_service_never_commits`).

## 17. Non-approval blockers

`DEFER`, `REJECT_SIGNAL`, `NEEDS_MORE_EVIDENCE`, and (via this function
specifically) `MARK_DUPLICATE` as the latest action all block creation —
tested individually, plus the historical-approval-superseded case (§6).

## 18. AUTO_ELIGIBLE separation

`promotion_policy_decision == "AUTO_ELIGIBLE"` is refused through this
human-approved route (`test_auto_eligible_blocks_the_human_route`), matching
Slice 9B's own already-committed narrowing. No automation was built in this
slice; a future Slice 10 may reuse this module's lower-level Signal-write
shape, but does not exist yet.

## 19. Disposable real-data rehearsal

Performed against a disposable copy of the real database only (deleted
after verification):

1. Applied, in order, on the disposable copy: Slice 9A (`published`
   column), Slice 9B (`reviewer_actions` table), Slice 9C
   (`source_assertions.signal_id`) — all three succeeded cleanly,
   `foreign_key_check=[]` at every step.
2. Pre-approval snapshot: `signals`=68, `reviewer_actions`=0,
   `source_assertions[222].signal_id`=NULL.
3. Recorded `ReviewerAction(action=APPROVE_SIGNAL)` for real `#222`'s real
   governance shape, then called `create_signal_from_approved_review()` with
   the exact MSP mapping (§9).
4. **Result: `signals` 68 → 69** (new Signal id 69), `reviewer_actions`
   0 → 1, `source_assertions[222].signal_id = 69`.
5. New Signal #69: `airport_id=45`, `source_id=70`, `published=0`,
   `confirmed_vendor=NULL`, both financial fields `NULL`,
   `likely_supplier='Runway Safe'`.
6. `PRAGMA foreign_key_check` → `[]`; `PRAGMA integrity_check` → `('ok',)`.
7. Ran the real `build_site()` static export against this disposable
   database: **66 signals in the generated `data.json`** (matching the
   `published=1` count exactly), no `signals/69.html` detail page, no trace
   of the governed title anywhere in `index.html` or `data.json`.
8. **Duplicate second run**: called `create_signal_from_approved_review()`
   again with the identical request. Result: `created=False`, same Signal
   id 69, `signals` count **stayed at 69** — no second Signal, no second
   link.
9. Exactly one `source_assertions` row (`#222`) had `signal_id` set;
   verified `0` other rows were touched.

## 20. 68→69 internal result

Confirmed in §19 point 4 — the new Signal exists and is internally
queryable (`session.get(Signal, 69)` succeeds in the isolated test suite's
equivalent, `test_internal_query_includes_the_governed_signal`).

## 21. Public-set unchanged result

Confirmed in §19 point 7: the public/static-exported signal set is exactly
the same 66 ids as before governed creation — the new Signal is absent, and
no previously-public Signal's content changed.

## 22. Duplicate second-run result

Confirmed in §19 point 8: `signals` count stayed at 69, the same Signal was
returned, no second provenance link was created.

## 23. No canonical-write proof

`test_no_canonical_airport_or_source_mutation_from_governed_creation`
confirms `Airport`/`Source` fields are byte-identical before and after a
governed creation call. The real-data rehearsal's own row-count comparison
(§19 point 9) confirms no table other than `signals`, `reviewer_actions`,
and the single targeted row of `source_assertions` was touched.

## 24. Real DB unchanged

Read-only checkpoint, before and after this entire task: sha256
`1eb956b3a17a866af94d9e5f7b1a0f388eb68a19e6642d551616a93d5b8de736`, size
1,769,472 bytes, mtime `1787145621.2311418`. `signals`=68 (never 69 on the
real database), `source_assertions.signal_id` column **absent**,
`signals.published` column **absent**, `reviewer_actions` table **absent** —
none of Slice 9A/9B/9C has ever been applied to the real database, exactly
as expected and not assumed. `SourceAssertion #222`'s governance fields
unchanged. No migration, no write, ever touched `data/runway_safe.db`
itself.

## 25. Focused tests

`tests/test_governed_signal_creation.py` (42 tests, including one added
during the Slice 9C review checkpoint for the cascade/delete-behavior
finding above) + `tests/test_governed_signal_creation_migration.py` (10
tests) = **52 new tests**. One pre-existing test,
`tests/test_capture_mac_discovery.py::test_apply_succeeds_after_running_the_real_migration_script`,
required updating (not counted as new) — see §31.

## 26. Full pytest

**1192 passed** (1140 baseline + 52 new), 0 failed, in 119.64s.

## 27. py_compile

Clean across all eight changed/new Python files.

## 28. git diff --check

Clean (zero whitespace errors); only benign LF→CRLF advisory warnings on the
five new files.

## 29. Exact files changed

- `app/models/signal.py` — modified (`supporting_source_assertions` relationship only)
- `app/models/source_assertion.py` — modified (`signal_id` column + `signal` relationship)
- `app/services/governed_signal_creation.py` — new
- `scripts/migrate_governed_signal_creation_slice9c.py` — new
- `tests/test_governed_signal_creation.py` — new
- `tests/test_governed_signal_creation_migration.py` — new
- `tests/test_model_contract.py` — modified (one new column + two relationship entries)
- `tests/test_capture_mac_discovery.py` — modified (added the Slice 9C migration to an existing fixture's migration chain — see §31)
- `docs/architecture/human-approved-governed-signal-creation-slice9c-report.md` — new (this file)

## 30. git status

All nine changes are unstaged modifications/additions in the working tree;
no commit was made. Pre-existing untracked documentation/UI files from prior
sessions remain untouched and unrelated to this task.

## 31. Design corrections discovered

Three real findings:

1. **Migration downgrade bug** (§3, found during implementation): the
   reused table-rebuild helper did not filter indexes on the column being
   dropped — the first time in this family that mattered, since
   `signal_id` is the first additive column that is itself indexed. Fixed
   before any test was written, and now explicitly regression-tested.
2. **Pre-existing test fixture staleness** (§25, found during
   implementation): `tests/test_capture_mac_discovery.py`'s `unmigrated_db`
   rehearsal test hand-applies the additive `source_assertions` migrations
   to prove its own schema-readiness gate; it needed the new Slice 9C
   migration added to that chain, exactly the same maintenance its own
   docstring already anticipated when Slice 7 landed ("any SELECT against
   SourceAssertion... requires the physical table to carry all [N] too").
   This is not a design corner cut to make the task look complete — it is
   the same, already-documented, expected consequence of adding another
   additive column to a model this test's fixture rebuilds by hand; no
   assertion in that test was weakened, only its own migration chain
   extended by one line to match.
3. **Cascade/delete-behavior defect** (§2, found during this review
   checkpoint): `Signal.supporting_source_assertions` lacked
   `passive_deletes=True`, so SQLAlchemy's default relationship management
   would silently null out `SourceAssertion.signal_id` before deleting a
   Signal, discarding the governed provenance link instead of blocking the
   delete. Fixed and now regression-tested — see §2 for the full
   before/after verification.

No correction was needed to `create_signal_from_approved_review()`'s own
core design; the two design-doc precedents it draws on
(`Signal.installation_id`, and Slice 9B's `get_latest_reviewer_action()`)
translated directly into working code.

## 32. Ready for checkpoint

Yes. All required verification passed: full suite (1191/1191), focused
suite, py_compile, git diff --check, real-data disposable rehearsal
(three migrations applied in order, MSP #222 approval and creation, 68→69,
duplicate-run idempotency, public-set unchanged, FK/integrity clean), real
DB confirmed byte-unchanged. No real Signal was created; no real
ReviewerAction was recorded; no real migration was applied. Awaiting the
separate review/commit/push authorization per this project's established
one-task-per-write-boundary discipline.

## 33. Exact prerequisites before first REAL Signal #69 operation

1. Slice 9A migration (`scripts/migrate_signal_publication_slice9a.py`)
   applied to the real database — currently unapplied.
2. Slice 9B migration (`scripts/migrate_reviewer_action_slice9b.py`)
   applied to the real database — currently unapplied.
3. Slice 9C migration (`scripts/migrate_governed_signal_creation_slice9c.py`,
   this slice) applied to the real database — currently unapplied.
4. A real, explicitly-authorized `ReviewerAction(action=APPROVE_SIGNAL)`
   recorded against the real `SourceAssertion #222` — not performed by this
   task.
5. A real, explicitly-authorized call to
   `create_signal_from_approved_review()` against the real database — not
   performed by this task; this would be the first real Signal ever created
   by the governed discovery/intelligence pipeline (real Signal count would
   become 69).

Each of these five remains a separately reviewable, separately authorized
write boundary, matching this project's established discipline.
