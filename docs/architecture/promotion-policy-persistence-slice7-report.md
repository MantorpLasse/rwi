# Promotion policy persistence — Slice 7 report

Implements Slice 7 of the roadmap in
[signal-promotion-policy-slice5-design.md](signal-promotion-policy-slice5-design.md)
§25, persisting the already-committed, unmodified Slice 6 core
([promotion-policy-core-slice6-report.md](promotion-policy-core-slice6-report.md)),
following the exact additive discipline Slice 4 established
([intelligence-review-persistence-slice4-report.md](intelligence-review-persistence-slice4-report.md)).

## 1. Starting HEAD

`adbf100284b148181274bd113a199ee74b192c81` ("Add promotion policy evaluation core"),
branch `main`, matched `origin/main`. Baseline full pytest: 982 passed, verified
before implementation began.

## 2. Real DB pre-state

Read-only inspection (`mode=ro`), before any development work:

```
sha256: 6be9c6f16b6e84fd67ccba7da3d7ac33bfd72c8d5479ea0dca046b9560771de0
size:   1,761,280 bytes
mtime:  1787086520.3335173
source_assertions count: 222
sources count: 70
signals count: 68
intelligence_review_decision column exists: False
promotion_policy_decision column exists: False
```

Slice 4's own migration has **not** been applied to the real database
either — only to isolated test fixtures, exactly matching every prior
slice's own STOP boundary. This was inspected, not assumed, per this task's
own explicit instruction.

## 3. Schema additions

Two new nullable columns on `source_assertions`
(`app/models/source_assertion.py`), in the exact same additive style as the
two existing pairs:

```python
promotion_policy_decision: Mapped[Optional[str]] = mapped_column(String(30))
promotion_policy_reason: Mapped[Optional[str]] = mapped_column(Text)
```

No default, no backfill, no `CHECK` constraint — matching the established
convention for both prior governed-decision column pairs (`identity_guard_*`,
`intelligence_review_*`) exactly, for the identical reason: a `CHECK` would
require the same full-table-rebuild `downgrade()` procedure already needed
for `DROP COLUMN`, more than a plain additive `ADD COLUMN` calls for. The
persistence service is the sole writer and only ever writes a real
`PromotionPolicyOutcome.value`, enforced in Python.

## 4. Migration design

`scripts/migrate_promotion_policy_persistence_slice7.py` — a near-verbatim
copy of `scripts/migrate_intelligence_review_persistence_slice4.py`'s own
proven structure: `backup_database()`, `inspect()`, `upgrade()` (idempotent
`ADD COLUMN`, guarded by `PRAGMA table_info` checks), `downgrade()` (reuses
the same `_drop_column_via_rebuild()` full-table-rebuild procedure, needed
because `source_assertions` is the target of a real incoming foreign key,
`installation_assertion_links.assertion_id`), and the identical CLI
(`--database`, `--downgrade`, `--allow-database-write`, `--skip-backup`). Not
run against `data/runway_safe.db` at any point in this task — every
migration test builds its own disposable temp-file database.

## 5. Migration tests

`tests/test_promotion_policy_persistence_migration.py` mirrors
`tests/test_intelligence_review_persistence_migration.py` exactly, with the
hand-built pre-Slice-7 schema now correctly including **both** prior additive
pairs (`identity_guard_*` from Slice 1, `intelligence_review_*` from Slice
4) as the current baseline this slice starts from:

- `test_upgrade_adds_only_the_expected_columns` — adds exactly the two new
  columns; existing row's prior four decision-pair values (identity +
  intelligence) preserved unchanged; new pair `NULL`.
- `test_upgrade_is_idempotent_when_run_twice`.
- `test_downgrade_is_exactly_reversible` — schema and row byte-identical to
  pre-upgrade.
- `test_downgrade_succeeds_against_the_full_realistic_schema_with_an_incoming_foreign_key`
  — seeds a `PhysicalInstallationIdentity` + `InstallationAssertionLink`
  referencing the assertion (the exact shape that broke naive `DROP COLUMN`
  elsewhere in this repository), proves upgrade → write → downgrade →
  re-upgrade all succeed with zero foreign-key violations and all three
  surviving indexes intact.

**4 passed**, 0 failed.

## 6. Persistence API

```python
def persist_promotion_policy(
    session: Session, source_assertion: SourceAssertion, claims: tuple[Claim, ...], context: PromotionPolicyContext,
) -> PromotionPolicyPersistenceResult
```

in `app/services/promotion_policy_persistence.py`. Matches the task's own
suggested 4-argument shape exactly (no explicit `signal_candidate` parameter
— see §7 for why). Never commits (`TestTransactionOwnership.test_service_never_commits`,
monkeypatch-proven); calls `session.flush()` once so a constraint violation
surfaces immediately, matching the established discipline. Never imports
`app.database.SessionLocal`.

## 7. Composition with Slice 3/4/6

**Composes the committed cores; duplicates none of their substantial logic.**
Calls `evaluate_signal_candidate()` (Slice 3, unmodified) and
`evaluate_promotion_policy()` (Slice 6, unmodified) directly. The one small
piece of shared logic needed — mapping `source_assertion.identity_guard_decision`
(a free-text column) to a real `AttachmentOutcome`, fail-closed — is reused
**verbatim** by importing
`app.services.intelligence_review_persistence._identity_decision_from_assertion()`
rather than re-implementing the same 10-line mapping a second time (single
source of truth; if the mapping rule ever changes, both slices stay in sync
automatically).

**Deliberately does NOT call `persist_intelligence_review()`.** That
function's own contract always (re-)writes `intelligence_review_decision`/
`intelligence_review_reason` as a side effect (Slice 4's idempotent-recompute
discipline) — calling it here would mean `persist_promotion_policy()` also
mutates fields outside its own declared scope on every call, which this
task's own explicit instruction forbids ("do not overwrite... intelligence
review fields," §16). Instead, `evaluate_signal_candidate()` is called
directly, in-memory only, purely to obtain the `SignalCandidateDecision`
`evaluate_promotion_policy()` needs — a pure recomputation with no database
write of its own, never touching either intelligence-review column.

**Consequence, deliberate and tested**
(`test_promotion_policy_does_not_require_intelligence_review_to_run_first`):
`persist_promotion_policy()` does not require `persist_intelligence_review()`
to have been called first — it depends only on
`source_assertion.identity_guard_decision` (identical to what Slice 4 itself
depends on) and the caller-supplied `claims`, never on Slice 4's own
persisted output. Calling both functions in sequence, in the same
transaction, is how a caller builds the full three-field audit trail on one
row (§10 below) — but they remain two independently-callable, independently-
correct functions, exactly the "narrowest dependency boundary" the task's
own §7 invited.

## 8. Source-authority handling

`PromotionPolicyContext.source_authority_tier` must be supplied by the
caller — this module never imports `app.models.Source` at all (AST-verified,
`test_source_module_never_imports_source_model`), and never references
`reliability_level` anywhere in its own code (AST-verified via
`ast.Attribute` node inspection, not substring search, since the module's
own docstring legitimately explains this in prose —
`test_source_reliability_level_official_never_implies_tier1`). A behavioral
test constructs a real `Source` row with `reliability_level="official"` and
proves the resulting decision is never `AUTO_ELIGIBLE` when no
`source_authority_tier` is supplied, regardless of that field's value — the
hard safety invariant task §8 required. No `Source.reliability_level`
column, migration, or redesign was touched.

## 9. Outcome persistence

All three `PromotionPolicyOutcome` values persist verbatim
(`.outcome.value`) with no rewriting. `AUTO_ELIGIBLE` is tested explicitly as
never creating a `Signal` row
(`test_auto_eligible_creates_no_signal_row`,
`test_completion_auto_eligible_creates_no_signal_row`) — persisting this
value is exactly and only a column write; no other code path in this
repository reads or acts on it.

## 10. MSP #222 full audit trail

Real chain (isolated in-memory DB): PDF fixture → `extract_candidate_fragment()`
→ `extract_mac_claims()` → `persist_discovery_fragment()` (Slice 1-era,
unmodified) → `persist_intelligence_review()` (Slice 4, unmodified) →
`persist_promotion_policy()` (this slice) with `TIER_1_PRIMARY_OFFICIAL`
context:

```
identity_guard_decision:      ATTACH_CONFIRMED
intelligence_review_decision: REVIEW_REQUIRED
promotion_policy_decision:    HUMAN_REVIEW_REQUIRED
promotion_policy_reason:      HUMAN_REVIEW_REQUIRED: material evidence is
    supported (SignalCandidate REVIEW_REQUIRED), but automatic eligibility is
    blocked by: financial semantic role(s) not on the auto-safe allowlist:
    advance_deposit_purchase_order, cip_project_ceiling; relationship
    role(s) not an explicit award/vendor-confirmation role:
    installation_oversight, requested_sole_source_vendor. This evidence is
    not discarded - human review determines whether it should become a
    Signal.
```

All three field pairs coexist on the **same** row, three genuinely distinct
values (`test_all_three_decision_pairs_coexist_on_the_same_row`). MSP never
becomes `DO_NOT_PROMOTE` merely for requiring human review
(`test_msp_is_never_do_not_promote`) — it remains material intelligence.

## 11. Explicit-award result

Synthetic claim (`contract_award_amount`, `awarded_contractor`,
`HISTORICAL_FACT`) with `TIER_1_PRIMARY_OFFICIAL` context: **`AUTO_ELIGIBLE`**
persisted verbatim. Zero `Signal` rows before/after.

## 12. Completion result

Synthetic claim (`COMPLETED`, no financial/relationship fact) with
`TIER_1_PRIMARY_OFFICIAL` context: **`AUTO_ELIGIBLE`** persisted verbatim.
Zero `Signal` rows before/after.

## 13. SFO-$40M result

Bare fact claim + a weak (`"mentioned_in_document"`) relationship claim, no
`FinancialFact` at all (structurally impossible to construct one for an
unlabeled amount, per Slice 1's own invariant), `TIER_1_PRIMARY_OFFICIAL`
context: persisted outcome is never `AUTO_ELIGIBLE`; the persisted reason
contains neither `"$40"` nor `"contract"`.

## 14. DO_NOT_PROMOTE cases

Tested via the real `persist_promotion_policy()` path for the four upstream
`SignalCandidateOutcome` values reachable through claims + identity alone:
`INSUFFICIENT_MATERIALITY`, `IDENTITY_NOT_CONFIRMED`, `CONTRADICTED`,
`DUPLICATE_WITHIN_EVIDENCE` — all persist `DO_NOT_PROMOTE`. `context.superseded
= True` is tested directly and also persists `DO_NOT_PROMOTE`, even for an
otherwise-fully-qualifying award claim
(`test_context_superseded_persists_do_not_promote_even_for_otherwise_eligible_claim`).
None of these delete the `SourceAssertion` row or its evidence
(`test_do_not_promote_never_deletes_the_source_assertion`).

**One upstream outcome is not exercised through the persistence path:
`STALE_OR_SUPERSEDED`.** This is a pre-existing, already-committed,
already-reviewed architectural boundary — `persist_intelligence_review()`
(Slice 4) itself has never been able to reach this outcome either, since it
too only ever constructs `SignalCandidateContext(identity_decision=...)`
internally and never threads through a `superseded` flag at the
`SignalCandidate` level (confirmed by inspection: Slice 4's own single
`SignalCandidateContext(...)` construction has exactly one keyword argument).
Not a defect introduced by this slice, and not modified here — see §29.

## 15. Idempotency

`test_idempotent_repeat_with_identical_inputs`: two calls with the same
claims/context produce identical outcome, identical reason, identical
persisted column values, and the `SourceAssertion` row count stays at
exactly 1.

## 16. Recompute semantics

The policy decision is derived state, recomputed fresh on every call — never
skipped when already set (matching Slice 4's own established discipline).
`test_changed_context_recomputes_only_the_two_policy_fields` proves an
`UNKNOWN_TIER` → `TIER_1` context change flips only
`promotion_policy_decision`/`promotion_policy_reason`, while every other
field (`raw_relevant_text`, `identity_guard_*`, `intelligence_review_*`,
`artifact_identity`, `source_locator`, `raw_fragment_hash`) remains
byte-identical before and after. `test_intelligence_review_fields_never_touched`
proves this holds even when `persist_intelligence_review()` was called
first — the two intelligence-review columns are untouched by the subsequent
`persist_promotion_policy()` call.

## 17. Transaction ownership

`test_service_never_commits` (monkeypatch-proven) and
`test_rollback_leaves_no_persisted_mutation` (fresh-session-proven — a
`session.rollback()` after the call leaves both new columns `NULL` when read
back from a completely separate `Session`/engine).

## 18. No-Signal proof

`app/services/promotion_policy_persistence.py` imports no name called
`Signal` anywhere (AST-verified via actual import-name inspection, not
substring search). Behaviorally proven for all four required cases: MSP
`HUMAN_REVIEW_REQUIRED`, explicit-award `AUTO_ELIGIBLE`, completion
`AUTO_ELIGIBLE`, and a `DO_NOT_PROMOTE` case — `Signal` row count is
identical before and after in every one.

## 19. No-canonical-write proof

`test_no_canonical_writes` confirms `Airport`/`Runway`/`RunwayEnd`/
`Installation`/`PhysicalInstallationIdentity` row counts are unchanged
across a full MSP-chain call. This module has no code path that could touch
`InstallationAssertionLink` or `Incident` either (neither is imported).

## 20. Backward compatibility

`test_row_never_reviewed_keeps_null_policy_fields` confirms a `SourceAssertion`
row never passed to `persist_promotion_policy()` keeps both new columns
`NULL`, no errors. `tests/test_capture_mac_discovery.py` required the same
mechanical fix Slice 4's own checkpoint already established: its
`test_apply_succeeds_after_running_the_real_migration_script` hand-builds a
pre-migration schema and now needs **all three** additive migrations applied
(Slice 1, Slice 4, and this slice) before the ORM's own unconditional
`SELECT` of every mapped column succeeds — the capture runner's own write
path still never touches any of the six additive columns; only the test
fixture's migration sequence grew. `test_apply_refuses_when_schema_not_migrated`
(the actual safety-gate test) was **not** touched and still correctly proves
the pre-migration refusal path.

## 21. International readiness

`test_international_non_usd_case_persists_auto_eligible`: a synthetic
Haneda-style claim (JPY, non-US vendor, `awarded_contractor`) persists
`AUTO_ELIGIBLE` through the identical path as the domestic case; the
persisted reason contains neither `"MAC"` nor `"MSP"`.

## 22. Focused tests

```
python -m pytest tests/test_promotion_policy_persistence.py \
    tests/test_promotion_policy_persistence_migration.py tests/test_promotion_policy_evaluation.py \
    tests/test_intelligence_review_persistence.py tests/test_intelligence_review_persistence_migration.py \
    tests/test_signal_candidate_evaluation.py tests/test_mac_granicus_claims.py \
    tests/test_discovery_evidence_persistence.py tests/test_discovery_governed_evidence_migration.py \
    tests/test_capture_mac_discovery.py tests/test_model_contract.py -q
```

Result: **224 passed**, 0 failed.

## 23. Full pytest

```
python -m pytest -q
```

Result: **1019 passed** (baseline 982 + 33 in `tests/test_promotion_policy_persistence.py`
+ 4 in `tests/test_promotion_policy_persistence_migration.py`), 0 failed, 0 skipped.

## 24. py_compile

`python -m py_compile` on all seven changed/new Python files — no output,
exit 0.

## 25. git diff --check

No output, exit 0.

## 26. Real DB unchanged proof

```
sha256: 6be9c6f16b6e84fd67ccba7da3d7ac33bfd72c8d5479ea0dca046b9560771de0
size:   1,761,280 bytes
mtime:  1787086520.3335173
```

Identical to §2's pre-state, confirmed after all development and testing.
The file was never opened outside `mode=ro` inspection calls; every test in
every new suite builds an isolated `tmp_path`/in-memory SQLite database. No
`--allow-database-write` invocation of any migration script against the
default `data/runway_safe.db` path occurred at any point.

## 27. Exact files changed

**New:**
- `app/services/promotion_policy_persistence.py`
- `scripts/migrate_promotion_policy_persistence_slice7.py`
- `tests/test_promotion_policy_persistence.py`
- `tests/test_promotion_policy_persistence_migration.py`
- `docs/architecture/promotion-policy-persistence-slice7-report.md` (this file)

**Modified:**
- `app/models/source_assertion.py` — the two new columns (§3).
- `tests/test_model_contract.py` — added `promotion_policy_decision`/
  `promotion_policy_reason` entries to the `source_assertions` contract
  table.
- `tests/test_capture_mac_discovery.py` — one pre-existing test needed the
  same class of mechanical fix Slice 4's checkpoint already established
  (§20) — added the Slice 7 migration import/call alongside the existing
  Slice 1/4 ones.

`app/services/evidence_claim_semantics.py`, `app/services/signal_candidate_evaluation.py`,
`app/services/promotion_policy_evaluation.py`, and
`app/services/intelligence_review_persistence.py` were all read for
grounding and **not modified** — no genuine defect was found in any of them
during this task.

## 28. git status

```
?? app/services/promotion_policy_persistence.py
?? scripts/migrate_promotion_policy_persistence_slice7.py
?? tests/test_promotion_policy_persistence.py
?? tests/test_promotion_policy_persistence_migration.py
?? docs/architecture/promotion-policy-persistence-slice7-report.md
```
plus `app/models/source_assertion.py`, `tests/test_model_contract.py`,
`tests/test_capture_mac_discovery.py` shown modified, plus the same
pre-existing, unrelated untracked items already present at task start.
Nothing staged, nothing committed, nothing pushed.

## 29. Design corrections discovered

No defect was found in any previously-committed file. One pre-existing,
already-committed, already-reviewed architectural limitation was newly
surfaced (not introduced) by this task's own comprehensive test list (§14):
`SignalCandidateOutcome.STALE_OR_SUPERSEDED` is unreachable through either
`persist_intelligence_review()` (Slice 4) or `persist_promotion_policy()`
(this slice), because neither function threads a `superseded` flag into the
internal `SignalCandidateContext` it constructs. This was not modified here
— Slice 4 already passed its own two checkpoint reviews without this being
flagged, and fixing it would mean changing Slice 4's own signature, which
this task's own instruction set does not authorize ("do not modify unless a
genuine defect is discovered" — this is an unexercised path, not an
incorrect one; `PromotionPolicyContext.superseded`, the mechanism this
slice's own task instructions actually named as the thing to test, works
correctly and is tested directly). Flagged transparently for whoever scopes
a future slice that needs it.

## 30. Whether ready for real Slice-4/Slice-7 migration later

Yes, mechanically — both migration scripts are proven correct, idempotent,
and reversible against disposable databases, exactly like Slice 1's real
migration was before it was actually run. Whether/when to apply either to
`data/runway_safe.db` remains its own separate, explicitly-approved decision
(matching every prior slice's own discipline) - not part of this task, and
not performed here.

## 31. Recommended Slice 8 scope

Per the design doc's own roadmap (§25): a **read-only human review queue**
surfacing `SourceAssertion` rows where `promotion_policy_decision ==
'HUMAN_REVIEW_REQUIRED'`, rendering each row's already-persisted
`intelligence_review_reason` and `promotion_policy_reason` (and, for a
richer view, the re-derived claims by re-running the appropriate
source-specific extractor on demand) for a human — no UI implementation, no
write path, no automatic promotion. Should remain read-only against the real
DB once run. Explicitly not in Slice 8's scope: the source-authority-tier
infrastructure gap (design doc's own "Slice 8.5"-shaped prerequisite,
still unaddressed), any `Signal` write, and the `CREATE_SIGNAL`/
`PUBLISH_SIGNAL` separation (still a hard prerequisite for the eventual
automated-promotion slice, not this one).

---

`RWI_PROMOTION_POLICY_PERSISTENCE_SLICE7_COMPLETE`
