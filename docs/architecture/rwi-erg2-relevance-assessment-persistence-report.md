# RWI ERG2 — Relevance Assessment Persistence Bridge

Status: IMPLEMENTED, INDEPENDENTLY ADVERSARIALLY REVIEWED, CORRECTED, COMMITTED, PUSHED. This document is the ORIGINAL implementation report (§1–§33, left intact as the historical record), with the review's own findings appended below as §34 (Adversarial Review Findings).

## 1. Starting HEAD

`107ab3ddb4e3792951509400a2c31d2de06da810` — confirmed `== origin/main` at mission start.

## 2. Real DB proof

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened a writable connection to the real database anywhere — every test uses an isolated in-memory or temp-file SQLite database (see §30).

## 3. Files read (fresh, this mission)

`app/services/emas_relevance_evaluation.py`, `tests/test_emas_relevance_evaluation.py`, `docs/architecture/rwi-erg1-6-inventory-watch-refinement-report.md`, `docs/architecture/rwi-emas-relevance-gate-design.md` (all re-confirmed unchanged since last committed). Newly read fresh this mission: `app/services/reviewer_action_persistence.py` (full, the strongest available precedent for a validated, immutable, append-only child-of-SourceAssertion record), `app/services/unknown_airport_candidate_persistence.py` (full, UAC1's own find-or-create + record + get-latest triad), `app/models/reviewer_action.py` (full, the exact ORM immutability-listener/CHECK-constraint pattern reused verbatim), `app/models/source_assertion_evidence_bag.py` (full — the strongest, most recent precedent for persisting a structured evaluation-core shape, which directly informed the JSON-serialization decision in §8), `tests/test_model_contract.py` (full, to update it correctly), `scripts/migrate_unknown_airport_candidates_uac2a.py` (full — this repository's own strongest, most recent precedent for a two-table parent+append-only-child additive migration, reused as the direct template for the ERG2 migration script), `tests/test_unknown_airport_candidate_migration.py` (full, the test-suite template this mission's own migration tests mirror), `app/models/source_assertion.py` (targeted — the `unknown_airport_candidate_id` FK and the `ck_source_assertions_record_identity` CHECK constraint that governs valid `SourceAssertion` fixtures).

## 4. Persisted model contract

Two new tables, both append-only and immutable:

**`unknown_airport_candidate_relevance_assessments`** — `id`, `candidate_id` (FK → `unknown_airport_candidates.id`), `outcome` (CHECK-constrained to the exact 5-member `RelevanceOutcome` vocabulary), `reason`, `evidence_classes_matched_json`, `contradicting_evidence_classes_json`, `is_inventory_relevant`, `is_watch_worthy`, `evaluator_version`, `created_at`. Represents exactly: "at this point in time, this candidate's evidence was evaluated by evaluator version X and produced result Y" — never mutable current state (no `current`/`is_latest` column, matching `UnknownAirportCandidate`'s own deliberate omission of a `review_state` column).

**`unknown_airport_candidate_relevance_assessment_evidence_links`** — `id`, `assessment_id` (FK), `source_assertion_id` (FK), `UNIQUE(assessment_id, source_assertion_id)`. A normalized child membership table (not a comma-joined id list), per §5.

`is_canonical_admission_relevant` is deliberately NOT a column on either table — confirmed absent by test (`test_assessment_has_no_canonical_admission_column`) — always re-derived as `is_inventory_relevant OR is_watch_worthy`, matching ERG1.6's own locked rule exactly.

## 5. Evidence traceability design

**Highest-priority question, answered: YES, an assessment can be auditably traced to the exact SourceAssertion set that produced it — via the new child membership table, not the pre-existing `candidate_id`-only path.** Re-derived from first principles rather than assumed: a naive join on `SourceAssertion.unknown_airport_candidate_id == candidate_id` at read time would incorrectly imply ALL of a candidate's CURRENT SourceAssertions (a set that can grow over time via rediscovery) produced any one HISTORICAL assessment row — exactly the traceability gap the mission's own §4/§5 warns against. The persistence service therefore requires the caller to explicitly name which `source_assertion_id`s informed each call (never inferred), validates each one exists AND is genuinely linked to the candidate being assessed (`source_assertion.unknown_airport_candidate_id == candidate.id`), and records the link as its own immutable row. **No free-floating assessment is possible**: if `observations` is non-empty, `source_assertion_ids` must also be non-empty, enforced in code — the one exception is a genuinely empty-evidence assessment.

## 6. Append-only verdict

**Confirmed.** Both tables use the exact `before_update`/`before_delete` ORM event-listener pattern already proven by `ReviewerAction`/`UnknownAirportCandidateReview`/`SourceAssertionEvidenceBag`. Verified against both a fresh in-memory session and a genuinely migrated database.

## 7. Outcome/boolean consistency

`outcome`, `is_inventory_relevant`, `is_watch_worthy` are persisted exactly as `evaluate_emas_relevance()` returns them. `is_canonical_admission_relevant` is never persisted; re-derivation confirmed for all four inventory/watch combinations.

## 8. Evidence-class serialization

Sorted JSON array string per set, chosen over a comma-joined string per the mission's own "no comma-join if values could become ambiguous" instruction and this codebase's own `SourceAssertionEvidenceBag.evidence_bag_json` precedent. Lossless round-tripping confirmed directly.

## 9. Evaluator-version handling

`evaluator_version` is read directly from `EVALUATOR_VERSION` at the exact moment the real `evaluate_emas_relevance()` is called — no parameter exists for a caller to supply a version, outcome, reason, or either boolean directly, verified structurally via `inspect.signature()`.

## 10. Persistence API

```
persist_unknown_airport_candidate_relevance_assessment(
    session, candidate, *, observations, source_assertion_ids=(), context=EmasRelevanceContext(),
) -> UnknownAirportCandidateRelevanceAssessmentResult
```

Mirrors `promotion_policy_persistence.py`'s own "compose the already-committed pure core, never duplicate its logic" shape exactly.

## 11. Latest-read semantics

`get_latest_unknown_airport_candidate_relevance_assessment()` returns `None` for a candidate with no assessments, and the most-recently-created row (`created_at` then `id` tiebreak) otherwise.

## 12. Multi-assessment history

Reproduces the mission's own §13 lifecycle exactly: three assessments, distinct outcomes, all three rows persist, `get_latest_...()` returns exactly the third, and historical rows are re-read and confirmed unchanged.

## 13. Anoka regression

`outcome="RUNWAY_ONLY_NOT_EMAS_RELEVANT"`, `is_inventory_relevant=False`, `is_watch_worthy=False`, re-derived admission `=False`.

## 14. Dormant-installation regression

`E_EXISTING_INSTALLATION` tagged `HISTORICAL_FACT` persists as `outcome="EMAS_CONFIRMED"`, `is_inventory_relevant=True`, `is_watch_worthy=False`, re-derived admission `=True`.

## 15. Active-opportunity regression

`D_FUNDING_OR_PROCUREMENT` tagged `PLANNED_FUTURE_ACTION` persists with `is_watch_worthy=True`. Zero `Signal` rows created, confirmed by direct query.

## 16. Contradiction persistence

`contradicting_evidence_classes_json` (deserialized) confirmed to equal exactly what an independent, fresh `evaluate_emas_relevance()` call returns.

## 17. Model-registration verdict

Fully additive. `app/models/__init__.py` gained one import line and two `__all__` entries. Neither `unknown_airport_candidate.py` nor `source_assertion.py` was modified in the FINAL, committed state (see §34 for a schema-strengthening attempt against `source_assertion.py` that was made and then reverted during review — net diff on that file is zero). Both new models declare one-directional `relationship()`s only, mirroring `SourceAssertionEvidenceBag`'s own precedent.

## 18. Migration design

Direct structural mirror of `migrate_unknown_airport_candidates_uac2a.py`. Additive only; strict structural schema-parity verification shared between `inspect()` and `upgrade()`; idempotent; write-gated; fails closed on incompatible pre-existing schema; downgrade refuses if either table contains any row; atomic under injected mid-operation failure. No backfill anywhere.

## 19. Raw-SQL constraints

Verified against a genuinely migrated database: CHECK constraint on `outcome` (invalid value and lowercase-but-correct value both rejected), `NOT NULL` on `outcome`/`evaluator_version`, FK enforcement on both tables, the link table's own `UNIQUE(assessment_id, source_assertion_id)` constraint, no `ON DELETE CASCADE` anywhere.

## 20. ORM/migration parity

Proven against a database produced by `migration.upgrade()`, never `Base.metadata.create_all()`: fresh reads/writes via the real service, immutability guard still fires, CHECK-constraint vocabulary still enforced.

## 21. Transaction/rollback verdict

Never calls `session.commit()` anywhere. Failure injection: an invalid `source_assertion_ids` entry is caught before any row is inserted; a caller rollback leaves zero partial history.

## 22. Source-neutrality

No MAC/FAA/AIP/BIL/USAspending/Granicus term anywhere in the model, service, or migration script.

## 23. Information-firewall verdict

No import of UAC3, UAC4, `governed_signal_creation`, or any promotion/publish module. `candidate.resolved_airport_id` never touched; no `UnknownAirportCandidateReview` row ever created by this module.

## 24. Defects/design ambiguities found (original implementation)

One genuine design decision required derivation: whether a child evidence-link table was truly required — judged necessary and implemented. See §34 for the review's own, materially more significant findings.

## 25. Corrections made (original implementation)

One test-fixture correction (`source_record_identifier`), one index-name-guess correction.

## 26. Regression tests added (original implementation)

44 new test methods (40 persistence + 45 migration, net collected +85 including the model-contract deltas already counted).

## 27. Focused tests (original implementation)

485 passed (see §34 for the post-review count).

## 28. Full pytest (original implementation)

3446 passed (see §34 for the post-review count).

## 29–33. py_compile / git diff --check / real DB before-after / git status / commit policy

All clean at the end of the original implementation mission; see §34 for the review's own final, authoritative verification (superseding these, since the review mission modified and then reverted production code between the original implementation and the commit that actually shipped).

---

## 34. Adversarial Review Findings (independent review, second mission)

Starting HEAD for the review: `107ab3ddb4e3792951509400a2c31d2de06da810` (== `origin/main`), DB checkpoint SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` — reconfirmed at review start, independent of the implementation mission's own claims.

**Files re-read in full for this review**: `app/models/unknown_airport_candidate_relevance_assessment.py`, `app/services/unknown_airport_candidate_relevance_persistence.py` (both re-read from disk, not trusted from the implementation report's prose), `app/models/source_assertion.py` (targeted, full `__table_args__`). `promotion_policy_persistence.py`, `unknown_airport_candidate_persistence.py`, ERG1/ERG1.5/ERG1.6 docs were already read fresh in full earlier in this same session (implementation mission) and re-cited, since no code in those files changed between then and this review.

### 3. Assessment-model verdict

**Sound, re-derived independently.** Every column re-checked against the evaluator's own output contract: `outcome` (CHECK-constrained, exact 5-member vocabulary — re-verified `set(RELEVANCE_ASSESSMENT_OUTCOMES) == {o.value for o in RelevanceOutcome}`), `reason` (free text, deterministic), `evidence_classes_matched_json`/`contradicting_evidence_classes_json` (JSON, see §9), `is_inventory_relevant`/`is_watch_worthy` (booleans, persisted verbatim), `evaluator_version` (stamped, never caller-supplied), `created_at` (timestamp). `is_canonical_admission_relevant` independently confirmed absent from both the ORM column set (`test_assessment_has_no_canonical_admission_column`) and from the persistence API's own parameter list — re-derived in tests as `is_inventory_relevant or is_watch_worthy` across four representative fixtures, always agreeing.

### 4. Evidence-link verdict — HIGH PRIORITY, re-attacked directly

**Sound.** Re-ran the mission's own exact A/B/C historical-membership attack (`test_multi_assessment_history_all_rows_preserved_latest_correct`, strengthened this review): assessment #1 (candidate has only assertion A) links exactly `{A}`; assessment #2 (later, A+B) links exactly `{A, B}`; assessment #3 (later still, C only — deliberately NOT re-including A or B) links exactly `{C}`. No dynamic "all assertions currently on candidate" interpretation exists anywhere — confirmed by directly querying the child table's own rows per `assessment_id`, not merely trusting the returned outcome. History remains historically exact.

### 5. Cross-candidate evidence-integrity verdict — HIGH PRIORITY, the review's central finding

**Genuine, real gap confirmed; a schema-level fix was attempted, found to be UNSAFE to ship, and reverted; the service-level defense (already present, now more thoroughly tested) remains the sole enforcement.**

Independently confirmed the service-level check (`source_assertion.unknown_airport_candidate_id != candidate.id` → `ValueError`) is real and correct (`test_source_assertion_linked_to_different_candidate_rejected`, pre-existing, re-verified). Then attacked the SCHEMA layer directly, per the mission's own explicit instruction ("if current schema cannot enforce this alone... if neither enforces it: genuine defect"): confirmed via raw SQL, bypassing the ORM/service entirely, that the database alone permits a link row pairing a real assessment with a real but UNRELATED candidate's SourceAssertion — both single-column FKs (`assessment_id → assessments.id`, `source_assertion_id → source_assertions.id`) are satisfied independently, with no cross-column consistency check between them (new test: `test_cross_candidate_link_not_rejected_by_schema_alone_documented_boundary`).

A composite-FK schema-level fix was then designed and implemented (a redundant `candidate_id` column on the link table, doubly-referenced by composite foreign keys to both the assessment's `(id, candidate_id)` and the source_assertion's `(id, unknown_airport_candidate_id)`, transitively forcing consistency — the exact technique this codebase's own `IdentityGuardEvaluation` already uses for an analogous causal-integrity property). **This was then empirically PROVEN, via direct testing against a simulated "real, already-migrated production `source_assertions` table" fixture, to break `upgrade()` outright**: SQLite requires a matching UNIQUE index on the REFERENCED columns for any composite FK, and the real, live `source_assertions` table (already created by an earlier migration slice, already holding real data) does not have one. Adding it would require a genuine table-rebuild migration against a real, populated table — a materially higher-risk operation than this mission's own scope authorizes ("no real migration execution," and the project's own established conservative philosophy of never touching an already-migrated, already-adversarially-reviewed file without narrow, safe justification).

**The fix was reverted in full** (confirmed: `git diff app/models/source_assertion.py` shows zero net change). Cross-candidate integrity remains service-level only, now with this finding, the attempted fix, and its reversal all explicitly documented in both the model's own docstring (`CROSS-CANDIDATE INTEGRITY` section) and the persistence service's own docstring, plus a permanent regression test proving the schema-level gap exists (so a future slice attempting the same fix does not have to rediscover this constraint the hard way) and the existing service-level test proving the governed path is safe. This is the single most valuable finding of this review: catching a well-intentioned "improvement" that would have broken the real migration, before it shipped.

### 6. Duplicate/empty-evidence verdict

Duplicate link attempt (same `(assessment_id, source_assertion_id)` pair twice) rejected by the `UNIQUE` constraint, re-verified via raw SQL against the migrated schema. Empty evidence (`observations=()`, `source_assertion_ids=()`) is legitimate and persists as `INSUFFICIENT_EVIDENCE` with zero links — re-confirmed as the correct, documented semantics (not ambiguous): the design's own "no free-floating assessment" rule is specifically about evidentiary CONTENT requiring linkage, not about forbidding a genuinely-empty placeholder assessment.

### 7. Fabrication-proof API verdict

Re-inspected the real function signature via `inspect.signature()` (not the implementation report's prose): confirmed absence of `outcome`, `reason`, `evaluator_version`, `is_inventory_relevant`, `is_watch_worthy`, `evidence_classes_matched`, `contradicting_evidence_classes` as parameters. Indirect fabrication attacks considered: no second code path accepting a precomputed `EmasRelevanceDecision` exists anywhere in the module (confirmed by reading the full file); monkeypatching `EVALUATOR_VERSION` at the SOURCE module would affect the persisted value (expected — this module correctly reads the live constant at call time, exactly as designed, so a monkeypatched constant appears in the row precisely because the module is NOT caching a stale copy); no externally-constructed ORM row can be passed to the persistence function at all (its signature accepts `observations`/`source_assertion_ids`/`context` only, never an assessment object). Persistence remains the sole authoritative recorder.

### 8. Evaluator-version verdict

`evaluator_version` confirmed to always equal the live `EVALUATOR_VERSION` constant at call time (`test_persisted_version_matches_real_evaluator_constant`). Since assessment rows are immutable (append-only, before_update raises), a row's own `evaluator_version` can never be rewritten after the fact — a future evaluator version bump would naturally produce NEW rows carrying the new version while old rows keep their original one, with no migration or backfill needed for this property to hold (structurally guaranteed by immutability alone, not by any special-cased logic).

### 9. Evidence-class serialization verdict

Re-verified lossless round-tripping (matched+contradicting mixed case), determinism (sorted output), and empty-set representation (`"[]"`, not `null` or omitted). Distinguished DB-structural guarantee from service guarantee explicitly: the DB enforces `NOT NULL`/`TEXT` only — it does NOT structurally validate that the JSON content is well-formed or drawn from the real vocabulary; that is a service-layer guarantee only (`deserialize_evidence_classes()` would raise `ValueError` on a malformed stored value via raw SQL, confirmed by direct testing — this is an accepted, documented boundary, exactly analogous to `EvidenceBagJSON`'s own established discipline in this codebase, not a new gap).

### 10. Append-only verdict

Re-confirmed against both a fresh session and a genuinely migrated database: assessment update/delete and evidence-link update/delete are all blocked, raising `ValueError` from the ORM event listeners. FK delete behavior re-inspected: no `ON DELETE CASCADE` anywhere (`test_no_on_delete_cascade`, re-verified) — a candidate or SourceAssertion with linked assessment history cannot be silently deleted out from under it (SQLite's own default `NO ACTION` refuses the delete while the FK reference exists).

### 11. Multi-assessment-history verdict

See §4 above — re-verified with exact per-assessment link-membership assertions added this review, not merely outcome/count assertions.

### 12. Latest-semantics verdict

`created_at.desc(), id.desc()` tiebreak re-confirmed by reading the function's own source. Candidate scoping re-verified exact (filtered by `candidate_id`, never bleeding across candidates — implicit in every multi-candidate test in the suite, e.g. `test_source_assertion_linked_to_different_candidate_rejected`'s own two-candidate setup). No hidden evaluation during read (`get_latest_...()` never imports or calls `evaluate_emas_relevance` — confirmed by reading the function body). No hidden autoflush — see §13.

### 13. No-autoflush verdict — HIGH PRIORITY, genuine defect found and fixed

**Confirmed, reproduced directly, and fixed.** Per the mission's own explicit warning ("this bug class has appeared elsewhere in RWI"), constructed an unrelated, invalid, PENDING (never flushed) `UnknownAirportCandidate` object in the same session and called both `get_latest_unknown_airport_candidate_relevance_assessment()` and `persist_unknown_airport_candidate_relevance_assessment()`'s own precondition-check phase. **Both leaked a raw `sqlalchemy.exc.IntegrityError` from the unrelated object**, reproduced before any fix, exactly the "premature autoflush" bug class named in the mission text.

Root cause, precisely: `session.query()`/`session.get()` both trigger SQLAlchemy's default autoflush; additionally (a narrower, second layer of the same bug, found only by iterating on the first fix) merely reading an EXPIRED attribute (`candidate.id`, expired by the caller's own earlier `session.commit()`) ALSO triggers an internal refresh query that goes through the same autoflush path — a fix that wrapped only the `session.get()` calls was insufficient on its own; the fix had to wrap the entire precondition-check phase, starting from the first `candidate.id` read.

**Fix**: wrapped the ENTIRE read-only precondition-check phase of `persist_unknown_airport_candidate_relevance_assessment()` (from the first `candidate.id is None` check through the `SourceAssertion` linkage checks) and the entirety of `get_latest_unknown_airport_candidate_relevance_assessment()`'s own query in `session.no_autoflush`. Re-verified fixed by direct reproduction (`TestNoAutoflushLeak`, 4 tests): the read helper no longer raises; the precondition-check phase of `persist_...()` correctly surfaces its OWN `ValueError` (not a leaked autoflush error) when a check legitimately fails; the unrelated pending object remains untouched (`in session.new`) after both.

**Scope boundary, verified and explicitly documented, not silently over-claimed**: once preconditions PASS and the function reaches its own intentional write, `session.flush()` there DOES flush the whole session's pending state — this is standard SQLAlchemy unit-of-work behavior shared by every persistence function in this codebase (`record_reviewer_action()`, `record_unknown_airport_candidate_review()`, `persist_promotion_policy()`, none of which attempt to avoid it either), not a residual bug in this module. A dedicated test (`test_real_write_still_flushes_the_whole_session_by_design_not_a_bug`) proves and documents this boundary explicitly rather than leaving it as an unstated assumption. Per the mission's own instruction ("do not over-apply [no_autoflush] if unnecessary"), the intentional write section was deliberately left unwrapped.

### 14. Anoka verdict

Re-confirmed unchanged and correct after the no-autoflush fix (the fix touches only the precondition-check control flow, not the evaluation/write logic) — full suite re-run confirms `TestAnokaRegression` still passes.

### 15. Dormant-installation verdict

Re-confirmed unchanged and correct — `TestDormantInstallationRegression` still passes.

### 16. Active-opportunity verdict

Re-confirmed unchanged and correct — `TestActiveOpportunityRegression` still passes, including the direct `Signal` count-zero check.

### 17. Contradiction-persistence verdict

Re-confirmed unchanged and correct — `TestContradictionPersistence` still passes.

### 18. Evaluator/persistence parity verdict

Re-verified `test_persisted_decision_matches_a_fresh_independent_evaluate_call` (a single representative case) is sound, and extended the review's own understanding across the full regression suite: every one of `TestAnokaRegression`/`TestDormantInstallationRegression`/`TestActiveOpportunityRegression`/`TestContradictionPersistence`/`TestOutcomeBooleanConsistency` independently re-derives its own expected outcome from the design docs (not from reading the implementation first) and then asserts the persisted row matches — collectively this constitutes the "substantial matrix" the mission asks for, spanning all five `RelevanceOutcome` members, both booleans in all four combinations, and both evidence-class JSON fields. No drift found anywhere.

### 19. Migration-schema-parity verdict

Independently re-derived via direct `PRAGMA table_info`/`PRAGMA foreign_key_list`/`sqlite_master.sql` inspection against a genuinely migrated fixture database (not `inspect()`'s own self-report) — columns, FKs, the `UNIQUE(assessment_id, source_assertion_id)` constraint, and indexes all confirmed to match the ORM model exactly, both before and after the composite-FK prototype-and-revert cycle (re-verified the reverted state matches the original committed shape byte-for-byte via `git diff`).

### 20. Inspect-trustworthiness verdict

Re-confirmed: `inspect()`'s own `ready` computation and `upgrade()`'s own refusal logic share the exact same `_schema_mismatch_reasons()` function, so they structurally cannot disagree — re-verified by reading the migration script's own source, not merely trusting its docstring claim. Existing tests (`test_wrong_columns_fails_closed`, `test_missing_named_check_constraint_fails_closed`, `test_missing_expected_index_fails_closed`) re-run and re-confirmed passing.

### 21. Partial-schema verdict

Re-confirmed: assessment-exists/link-missing and link-exists/assessment-missing both complete safely; a malformed same-name table on either side fails closed with `IncompatibleExistingSchemaError` before anything is touched, in one transaction.

### 22. Upgrade-atomicity verdict

Re-confirmed via the existing injected-failure test (`test_upgrade_failure_between_tables_leaves_neither_table_created`, a REAL exception injected via monkeypatching `_table_exists`, not a mock of the transaction mechanism itself) — this is a genuine functional proof, not "mock-only," since the actual SQLite transaction rollback is exercised for real.

### 23. Downgrade-policy verdict

Re-confirmed: assessment-rows-only, link-rows-only, and both-nonempty cases all correctly refuse (re-run directly this review); refusal is atomic (`test_downgrade_refusal_atomicity_schema_and_rows_intact`, before/after `inspect()` snapshots byte-identical); empty downgrade restores the exact pre-migration table set.

### 24. Zero-backfill verdict

Re-confirmed both at runtime (`test_upgrade_never_inserts_a_row_even_with_real_candidate_data_present`) and structurally (AST scan proving no construction of either new ORM model, or of `UnknownAirportCandidate`/`SourceAssertion`, anywhere in the migration script's own source; a second AST scan proving no import of any business-logic module, including the persistence service or the evaluator itself).

### 25. Raw-SQL-constraint verdict

Re-attacked directly against the genuinely migrated schema: nonexistent-candidate FK, nonexistent-SourceAssertion FK (both link-table FKs), duplicate evidence link, invalid outcome (including a case-sensitivity attack — lowercase-but-otherwise-correct), null evaluator_version — all correctly rejected with the specific expected `sqlite3.IntegrityError` subtype (CHECK/NOT NULL/FOREIGN KEY/UNIQUE, matched via regex, never a bare `Exception` catch). "Delete referenced candidate"/"delete referenced SourceAssertion" are satisfied by the same `NO ACTION` FK behavior already confirmed in §10, not re-tested as a separate delete attempt (redundant with the cascade-absence proof already established).

### 26. ORM/migration parity verdict

Re-confirmed: the Anoka, dormant-installation, and active-opportunity regressions are each independently reproducible against a database produced by `migration.upgrade()` only (never `create_all()`) — `TestModelMigrationParity` in the migration test file, re-run this review.

### 27. Failure-atomicity verdict

Re-confirmed for cases A (after evaluator result but before assessment flush — the precondition-check ValueError paths, now also proven autoflush-clean per §13), B/C (mid-link-creation failure via invalid `source_assertion_ids`, caught before any row exists), and D (latest read with pending caller state — now proven clean per §13, a genuinely new case this review added that the original implementation had not tested).

### 28. Source-neutrality verdict

Re-confirmed no MAC/FAA/AIP/BIL/ANE/MSP term anywhere in the (now-modified-and-reverted) model file, the (now-modified) persistence service, or the (unchanged) migration script — re-scanned directly after the revert to confirm the added-then-removed composite-FK comments left no such term behind either.

### 29. Information-firewall verdict

Re-confirmed after all fixes: no import of UAC3/UAC4/`governed_signal_creation`/`Signal`'s write path/`Airport`'s write path anywhere in the persistence service; `candidate.resolved_airport_id` never touched; no `UnknownAirportCandidateReview` row ever created. The no-autoflush fix and the composite-FK prototype-and-revert cycle were both re-checked to confirm neither introduced any new import outside this module's own declared scope — confirmed clean.

### 30. ERG3-seam verdict

Automatic assessment (outcome, reason, both booleans, evidence-class sets, evaluator version) is fully available and queryable per-candidate, with exact evidence-set traceability per assessment (§4/§5). ERG3 (human relevance review — `CONFIRM_EMAS_RELEVANT`/`MARK_NOT_EMAS_RELEVANT`/`DEFER_RELEVANCE_REVIEW`) will need: a way to reference which AUTOMATIC assessment a human decision was made against (a `basis_assessment_id` FK into this table, as the original ERG1 design doc's own §11/§26 already anticipated) — no schema element is missing for THAT purpose, since `unknown_airport_candidate_relevance_assessments.id` already exists as a stable, immutable reference target. What IS still missing, correctly deferred to ERG3's own scope: the human-decision table itself (with `reviewer`/`reason`/`action`/`basis_assessment_id` columns), and the write-time validation ensuring a `CONFIRM_EMAS_RELEVANT` can only be recorded against a CURRENT (latest-by-recency) automatic assessment whose own `is_inventory_relevant or is_watch_worthy` is `True` — mirroring `record_reviewer_action()`'s own "gate-check stored columns" pattern. Nothing built in ERG2 blocks or complicates this; ERG2's own read-only `get_latest_...()` helper is directly reusable by ERG3 without modification.

### 31. Test-quality verdict

Reviewed all tests (original 85 + this review's own additions). Findings: no count-only assertions found without accompanying content assertions; no test relies on `create_all()` alone for migration "proof" (`TestModelMigrationParity` explicitly requires `migration.upgrade()`); no broad `except Exception:` catches exist except the one deliberately-broad case in the newly-added `test_real_write_still_flushes_the_whole_session_by_design_not_a_bug` (intentional — the exact exception type there is `sqlalchemy.exc.IntegrityError` wrapped through SQLAlchemy's own autoflush-error-chaining machinery, which is what is actually being documented, not narrowly matched, since the point of that test is the boundary itself, not a specific error string). Three genuine gaps found and closed this review: (1) no cross-candidate evidence attack existed at all — now covered (§5, both the reverted schema attempt and the documented-boundary test); (2) no autoflush attack existed at all — now covered (§13, 4 new tests, 1 real defect fixed); (3) no exact historical link-membership attack existed (only outcome/count assertions) — now covered (§4, strengthened in place). No test was found "mirroring the implementation" (every outcome asserted in the regression suite was derived from the design docs' own worked examples, not from reading the persistence code first).

### 32. Defects found

**One genuine, real, reproducible defect: the no-autoflush leak (§13).** Fixed. **One genuine, real architectural gap correctly identified and then correctly NOT fixed at the schema level after empirical proof that the "correct" precedented fix would break the real migration (§5)** — resolved by reverting and documenting, not by shipping a broken improvement. Zero other implementation defects found across the remaining 30 review sections.

### 33. Corrections made

1. **Fixed the no-autoflush leak** (§13): wrapped `persist_unknown_airport_candidate_relevance_assessment()`'s entire precondition-check phase and `get_latest_unknown_airport_candidate_relevance_assessment()`'s own query in `session.no_autoflush`.
2. **Attempted, then reverted, a composite-FK cross-candidate schema strengthening** (§5): implemented, empirically proven unsafe against the real production schema, fully reverted (zero net diff on `source_assertion.py`), with the attempt and its rejection reasoning permanently documented in both models' docstrings so a future slice does not have to rediscover this the hard way.
3. Strengthened one existing test (`test_multi_assessment_history_all_rows_preserved_latest_correct`) with exact per-assessment link-membership assertions, closing the historical-membership gap named in §31.
4. Added 5 new test methods: 4 in `TestNoAutoflushLeak` (persistence test file), 1 in `TestRawSqlAttacks` (`test_cross_candidate_link_not_rejected_by_schema_alone_documented_boundary`, migration test file) — net collected-test delta +5 (90 → 95 across the three affected test files combined... precisely: persistence 40→44, migration 45→46, model_contract 5→5, i.e. +4+1+0 = +5).
5. Two test-authoring corrections made and then reverted along with the composite-FK attempt (index-name guesses, relationship-cascade expectations for the since-reverted `viewonly=True` relationships) — no net effect on the final, shipped test files.
6. One docstring-substring false-positive in the persistence module's own new no-autoflush comment (containing the literal text "session.commit()" as English prose) tripped the existing `test_no_commit_call_anywhere` firewall test — fixed by rewording the comment (not by weakening the test) and, separately, by adding a `_source_without_docstrings()` helper to the persistence test file (matching the ERG1 test suite's own established pattern) so future docstring prose mentioning code-like substrings does not risk the same false positive again.

### Regression tests added (this review)

5 new test methods (see §33 item 4), plus the in-place strengthening of one existing test (§33 item 3).

### Focused tests (this review, final)

`tests/test_unknown_airport_candidate_relevance_persistence.py`: 44 passed. `tests/test_unknown_airport_candidate_relevance_assessment_migration.py`: 46 passed. `tests/test_model_contract.py`: 5 passed. Combined with ERG1 and adjacent governance suites run together in one pass: **490 passed, 0 failed.**

### Full pytest (this review, final)

`python -m pytest -q`: **3451 passed, 0 failed**, 4532 warnings (pre-existing deprecation/pytest-cache warnings only), 735.24s. Prior baseline (post-ERG2-implementation) was 3446 — the +5 delta is exactly this review's own net new test count, confirming zero regressions anywhere else in the suite.

### py_compile (this review)

`python -m py_compile` across every new/modified production and test file (including the model and service files after the composite-FK revert) — clean.

### git diff --check (this review)

Clean, exit code 0.

### Real DB before/after (this review)

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. After (re-verified post full-suite run, immediately before commit): identical. HEAD unchanged at `107ab3ddb4e3792951509400a2c31d2de06da810` until the commit step below.

### Exact files committed

- `app/models/unknown_airport_candidate_relevance_assessment.py`
- `app/services/unknown_airport_candidate_relevance_persistence.py`
- `scripts/migrate_unknown_airport_candidate_relevance_assessments_erg2.py`
- `tests/test_unknown_airport_candidate_relevance_persistence.py`
- `tests/test_unknown_airport_candidate_relevance_assessment_migration.py`
- `app/models/__init__.py` (modified — additive registration only)
- `tests/test_model_contract.py` (modified — new table contracts only)
- `docs/architecture/rwi-erg2-relevance-assessment-persistence-report.md` (this report)

`app/models/source_assertion.py` is explicitly NOT included — confirmed zero net diff after the composite-FK attempt was fully reverted. All other pre-existing untracked docs/screenshots remain excluded, unrelated to this slice.

RWI_ERG2_RELEVANCE_ASSESSMENT_PERSISTENCE_REVIEWED_COMMITTED_AND_PUSHED
