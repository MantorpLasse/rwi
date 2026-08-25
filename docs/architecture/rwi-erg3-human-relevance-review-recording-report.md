# RWI ERG3 — Human EMAS Relevance Review Recording

Status: IMPLEMENTED. NOT COMMITTED, NOT PUSHED — a separate, explicitly-authorized adversarial review checkpoint governs this change (mission's own Section 34 commit policy).

## 1. Starting HEAD

`17dcfcc1661fe04e481062719cf955d772ef1d41` — confirmed `== origin/main` at mission start.

## 2. Real DB proof

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened a writable connection to the real database anywhere — every test uses an isolated in-memory or temp-file SQLite database.

## 3. Files read (fresh, this mission)

`app/models/unknown_airport_candidate_relevance_assessment.py` and `app/services/unknown_airport_candidate_relevance_persistence.py` (both re-read from disk in full, the direct ERG2 precedent this slice composes). `UnknownAirportCandidateReview`/`record_unknown_airport_candidate_review()`, `ReviewerAction`/`record_reviewer_action()`, and UAC4/UAC5 were all read fully in earlier missions of this same session (unchanged since) and re-cited rather than re-opened verbatim, since no code in those files has changed.

## 4. Review model

`UnknownAirportCandidateRelevanceReview` — a new, separate table, NOT an extension of `UnknownAirportCandidateReview`. Represents exactly: "human reviewer X reviewed automatic assessment Y and recorded decision Z with reason R." Columns: `id`, `candidate_id` (FK), `basis_assessment_id` (FK to `unknown_airport_candidate_relevance_assessments.id`), `action` (CHECK-constrained), `reviewer`, `reason`, `created_at`, `supersedes_review_id` (optional, self-referential, audit-only). Never mutates the assessment it reviews, never mutates candidate identity truth, never creates a canonical Airport.

## 5. Action vocabulary

`RELEVANCE_REVIEW_ACTIONS = ("CONFIRM_EMAS_RELEVANT", "MARK_NOT_EMAS_RELEVANT", "DEFER_RELEVANCE_REVIEW")` — the exact three-member vocabulary the mission recommends, independently assessed as sufficient: every downstream-effect action the mission explicitly warns against (`WATCH`, `CREATE_AIRPORT`, `CREATE_SIGNAL`, `PROMOTE`) belongs to a later, separate, human-gated governance layer (a future ERG4 UAC4 gate, or Signal creation), never to this recording layer — confirmed by direct test (`test_no_downstream_effect_actions_in_vocabulary`).

## 6. Basis-assessment binding

Every review carries exactly one `basis_assessment_id`, an ordinary (non-composite) FK guaranteeing the referenced assessment row exists. The service additionally requires, before recording: (a) the referenced assessment belongs to the SAME candidate being reviewed, and (b) the referenced assessment is the candidate's CURRENT latest automatic assessment (per `get_latest_unknown_airport_candidate_relevance_assessment()`, reused verbatim from ERG2, never a second "latest" definition invented). Both checks fail closed with a clear `ValueError` before any row is constructed.

## 7. Same-candidate integrity — service-level only, deliberately, decided BEFORE writing any code

Per the mission's own explicit instruction not to repeat ERG2's own composite-FK mistake blindly, this was analyzed structurally first: a composite-FK guarantee (mirroring `IdentityGuardEvaluation`'s own technique) would require adding a `UNIQUE(id, candidate_id)` constraint to the ALREADY-COMMITTED `UnknownAirportCandidateRelevanceAssessment` model — the same category of "retroactively alter an already-committed table's schema" risk ERG2's own review already proved unsafe against `source_assertions`. **Critically, this reasoning does NOT depend on the table's real row count today**: even though `unknown_airport_candidate_relevance_assessments` currently holds zero real rows (ERG2's migration has never been run for real), a later ERG3 migration cannot safely assume it will STILL be empty at the time it actually runs against the real database — ERG2's whole purpose is to start persisting real automatic assessments, so by the time an operator runs ERG3's migration, ERG2's own migration may already have run and the table may already hold real data. This determination was made analytically, before any composite-FK code was written (unlike ERG2, where the unsafe design was built first and reverted after empirical proof) — directly following the mission's own "do not repeat that mistake blindly" instruction. Same-candidate integrity is therefore service-level only, exactly mirroring ERG2's own already-accepted, already-tested pattern, with the same honest documentation and a permanent regression test (`test_cross_candidate_basis_rejected_g`) proving the service correctly refuses it.

## 8. Current-assessment / stale-basis semantics

The central discipline of this slice. `record_unknown_airport_candidate_relevance_review()` refuses outright if `basis_assessment_id` is not the candidate's current latest assessment at the moment of recording — reproduced directly: assessment #10 exists, human intends to review #10, a new assessment #11 is recorded before the review is submitted, the attempt to record against #10 raises `ValueError` naming the current id, never silently rebinding to #11 (`test_stale_basis_refused_e`, `test_no_silent_rebinding_to_new_assessment`). "Latest" reuses ERG2's own `created_at DESC, id DESC` tiebreak exactly — verified directly with a same-timestamp tie constructed at INSERT time (assessment rows are immutable, so the tie could not be simulated via a post-hoc `UPDATE`; two rows were constructed with an identical `created_at` at creation time instead) — the higher-id row correctly wins (`test_same_timestamp_higher_id_wins_f`).

## 9. Review eligibility

**Deliberately unrestricted by automatic outcome.** All three actions are recordable against any of the five `RelevanceOutcome` values, including `RUNWAY_ONLY_NOT_EMAS_RELEVANT` and `INSUFFICIENT_EVIDENCE` — confirmed directly (`TestReviewEligibility`, three tests spanning a negative automatic outcome with `CONFIRM`, a strong positive automatic outcome with `MARK_NOT`, and an insufficient-evidence outcome with `DEFER`). Derived, not assumed: constraining which actions a human may record based on the automatic classification would be exactly the kind of "automatically constrain humans based on evaluator output" the mission explicitly forbids, and blurs this recording layer with the genuinely separate, later, human-gated admission-eligibility question (ERG4's own future scope). "Evidence-grounded" is satisfied structurally — every review is bound to one specific, real, CURRENT automatic assessment — not by outcome-filtering which conclusion a human may draw from it.

## 10. Human review does not change automatic facts

Confirmed structurally (the service never writes to `UnknownAirportCandidateRelevanceAssessment` at all — it only reads it for the current-basis check) and by direct test (`test_confirm_does_not_rewrite_assessment_fields`): outcome, both booleans, and evaluator_version are re-read after a `CONFIRM` and found byte-identical to before.

## 11. Append-only history

Immutable via the exact `before_update`/`before_delete` ORM event-listener pattern already proven by `ReviewerAction`/`UnknownAirportCandidateReview`/ERG2's own assessment table. Multi-review histories (`#1 DEFER` → new assessment → `#2 CONFIRM` → new assessment → `#3 MARK_NOT`) preserve all rows, verified directly (`test_multi_review_history_i`).

## 12. Supersession decision

Included, as optional, nullable, audit-only metadata (`supersedes_review_id`) — mirroring `ReviewerAction.supersedes_action_id`/`UnknownAirportCandidateReview.supersedes_review_id`'s own identical, already-proven-low-cost precedent exactly. NOT required for correctness: "current" state is always derived by recency (`get_latest_unknown_airport_candidate_relevance_review()`), never by walking this chain — the field exists purely to let a human record an explicit audit narrative ("this CONFIRM explicitly reverses that earlier DEFER") at effectively zero marginal cost (one nullable FK column + one same-candidate validation check, both already-proven-cheap per the established precedent). Validated identically to the existing pattern: must exist and belong to the same candidate.

## 13. Effective current-review-state contract

`resolve_effective_unknown_airport_candidate_relevance_review_state()` returns an `EffectiveRelevanceReviewState` with a deterministic four-member `RelevanceReviewState` vocabulary: `NO_ASSESSMENT_YET`, `UNREVIEWED`, `STALE`, `CURRENT`. Composes `get_latest_unknown_airport_candidate_relevance_assessment()` (ERG2) and `get_latest_unknown_airport_candidate_relevance_review()` (this module) — the latest review CHRONOLOGICALLY is never treated as authoritative for "current" unless its own `basis_assessment_id` equals the candidate's current latest assessment id exactly. Directly proves the mission's own worked example (`test_stale_after_new_assessment_j`): review #5 confirms assessment #10, assessment #11 is recorded, state is `STALE` (never `CURRENT`, never silently reporting the old `CONFIRM` as still authoritative) — `latest_review_action` is still exposed (informational), but `state` and `is_current`/`review_required` correctly reflect staleness.

## 14. Stale human review semantics

See §8/§13 — directly tested twice, once for a stale `CONFIRM` (`test_stale_after_new_assessment_j`) and once for a stale `MARK_NOT` (`test_stale_after_new_assessment_mark_not_k`), confirming this holds regardless of which action the stale review recorded.

## 15. Anoka negative review

`TestAnokaRegression::test_anoka_negative_review_a`: the real Anoka evidence shape (`RUNWAY_ONLY_NOT_EMAS_RELEVANT`) persists a `MARK_NOT_EMAS_RELEVANT` review; effective state is `CURRENT` with `latest_review_action == MARK_NOT`. `test_anoka_defer_b` confirms `DEFER_RELEVANCE_REVIEW` is equally recordable. `test_no_airport_signal_or_candidate_resolution_created` confirms zero `Airport`/`Signal` rows and `candidate.resolved_airport_id is None` throughout.

## 16. Anoka rediscovery lifecycle

`TestRediscoveryLifecycle::test_rediscovery_then_new_confirm_l`: an old `MARK_NOT` (basis = assessment #1) becomes `STALE` the moment a new assessment (simulating "Anoka Runway 18-36 EMAS Feasibility Study" evidence, `is_watch_worthy=True`) is recorded; a new `CONFIRM` is recorded against the new current assessment (optionally naming the old review via `supersedes_review_id`); effective state becomes `CURRENT`; both review rows remain (`len(all_reviews) == 2`); exactly one candidate row still exists for the identity (no duplicate candidate created) — confirmed directly by count.

## 17. Dormant-installation review

`TestDormantInstallationReview`: an `E_EXISTING_INSTALLATION`/`HISTORICAL_FACT` assessment (`is_inventory_relevant=True`, `is_watch_worthy=False`) accepts a `CONFIRM_EMAS_RELEVANT` review, reaching `CURRENT` state — confirmed no active watch is invented (the assessment's own `is_watch_worthy` is never touched by the review) and zero `Signal` rows exist afterward.

## 18. Contradiction review

`TestContradictionReview`: an assessment carrying non-empty `contradicting_evidence_classes_json` accepts all three review actions (`CONFIRM`/`MARK_NOT`/`DEFER`), each recording the human's own reason; the contradiction evidence itself is confirmed unchanged (re-read after review recording) — this module never erases or reinterprets it. Future UAC4 gate design (not implemented here) may choose how much human confirmation is sufficient in the presence of contradiction; this slice only proves recording remains possible and non-destructive.

## 19. No-autoflush verdict

Carried forward directly from the ERG2 lesson, applied proactively (not rediscovered the hard way this time): the entire read-only precondition-check phase of `record_unknown_airport_candidate_relevance_review()` — starting from the first `candidate.id` attribute read, exactly matching ERG2's own two-layer fix (plain `session.get()` calls AND bare expired-attribute reads both trigger SQLAlchemy's default autoflush) — is wrapped in `session.no_autoflush`, as is the entirety of both read-only helpers (`get_latest_unknown_airport_candidate_relevance_review()`, `resolve_effective_unknown_airport_candidate_relevance_review_state()`). Verified directly with the exact ERG2-style attack (an unrelated, invalid, pending `UnknownAirportCandidate` in the same session): all three functions correctly avoid leaking an autoflush error (`TestNoAutoflushLeak`, 3 tests). The intentional write section (`session.add(record); session.flush()`) remains unwrapped, matching ERG2's own documented SCOPE boundary exactly.

## 20. Persistence API

```
record_unknown_airport_candidate_relevance_review(
    session, candidate, *, basis_assessment_id, action, reviewer, reason, supersedes_review_id=None,
) -> UnknownAirportCandidateRelevanceReview
```

Validates candidate exists, action vocabulary, reviewer/reason non-empty, basis assessment exists, same-candidate, current-basis, and (if supplied) supersession same-candidate — all within one `no_autoflush` block — before constructing and flushing exactly one row. Never commits.

## 21. Latest/current read semantics

`get_latest_unknown_airport_candidate_relevance_review()` mirrors every other "latest" helper in this pipeline exactly (`created_at DESC, id DESC`). `resolve_effective_unknown_airport_candidate_relevance_review_state()` is the genuinely new contract (§13) — the exact, minimal result shape a future ERG4/UAC4 gate needs, with `is_current`/`review_required` convenience properties layered over the four-member state enum, deliberately not overbuilt (no extra fields beyond `candidate_id`/`state`/`latest_assessment_id`/`latest_review_id`/`latest_review_basis_assessment_id`/`latest_review_action`).

## 22. Migration design

`scripts/migrate_unknown_airport_candidate_relevance_reviews_erg3.py` — a direct structural mirror of the ERG2 single-table migration (itself mirroring UAC2A). Additive only, touches no existing table (confirmed: no `ALTER TABLE` anywhere, and — per §7 — deliberately does NOT touch `unknown_airport_candidate_relevance_assessments` either, even though it holds zero real rows today). Idempotent, write-gated, fails closed on incompatible pre-existing schema, downgrade refuses if any row exists, atomic under injected failure, zero backfill (verified both at runtime and via AST scan proving no business-logic import and no ORM row construction anywhere in the script's own source).

## 23. Raw-SQL constraints

Verified against a genuinely migrated database: CHECK constraint on `action` (invalid value and lowercase-but-correct both rejected), FK enforcement on `candidate_id`/`basis_assessment_id`/`supersedes_review_id`, `NOT NULL` on `reviewer`/`reason`, no `ON DELETE CASCADE` (confirmed both structurally via `PRAGMA foreign_key_list` and by directly attempting to delete a referenced candidate and a referenced assessment while a review exists — both correctly refused with `FOREIGN KEY constraint failed`, going one step further than merely inspecting the absence of a cascade clause).

## 24. ORM/migration parity

`TestModelMigrationParity` proves the persistence service works correctly against a database produced by `migration.upgrade()` only (never `create_all()`): fresh reads/writes succeed, the immutability guard still fires, the CHECK-constraint vocabulary is still enforced via raw SQL.

## 25. Transaction/rollback

Never calls `session.commit()` anywhere (confirmed both by direct test and by a docstring-aware source scan). A failed precondition check (invalid `basis_assessment_id`) is caught before any row is inserted; a caller rollback leaves zero partial history.

## 26. Information firewall

No import of UAC3, UAC4, `governed_signal_creation`, `emas_relevance_evaluation` (the ERG1 evaluator itself — this module never re-evaluates, only records human judgment against an already-persisted ERG2 assessment), or any promotion/publish module. No `Signal`/`Airport`/`Installation` write-path name imported. `candidate.resolved_airport_id` never touched; no `UnknownAirportCandidateReview` row ever created — identity review history, automatic relevance-assessment history, and human relevance-review history remain three entirely separate, non-interacting append-only logs.

## 27. Future UAC4 gate seam

The exact deterministic query a future ERG4/UAC4 gate should use before `CREATE_NEW_AIRPORT` may execute, built entirely from what ERG3 already exposes:

1. Call `resolve_effective_unknown_airport_candidate_relevance_review_state(session, candidate_id)`.
2. Require `state.is_current is True` (equivalently, `state.state == RelevanceReviewState.CURRENT`) — a stale or missing review must refuse.
3. Require `state.latest_review_action == "CONFIRM_EMAS_RELEVANT"`.
4. Likely ALSO require the underlying assessment's own automatic canonical-admission relevance (`is_inventory_relevant OR is_watch_worthy`, ERG1.6's own locked rule) to be `True` at the time of that CURRENT assessment — reachable via `session.get(UnknownAirportCandidateRelevanceAssessment, state.latest_assessment_id)`.

**Recommendation on step 4, challenged directly (not assumed)**: both automatic-positive AND human-CONFIRM should be required together, not either alone. Automatic-positive alone is insufficient (that's exactly the ERG1-review-era gap this whole epic exists to close — a human must affirmatively judge, not merely let the evaluator's own classification silently authorize admission). Human-CONFIRM alone, WITHOUT requiring the underlying assessment to itself be inventory/watch-relevant, is also insufficient and arguably dangerous: nothing in ERG3 restricts CONFIRM from being recorded against a `RUNWAY_ONLY_NOT_EMAS_RELEVANT`/`INSUFFICIENT_EVIDENCE` assessment (§9's own deliberate design — a human MAY have out-of-band knowledge), so requiring human-CONFIRM alone would let a reviewer's own good-faith override of clearly-negative automatic evidence become the SOLE gate, with no structural check that the override is well-founded. Requiring BOTH gives the future ERG4 gate two independent signals that must agree, while `EMAS_PLAUSIBLE_SIGNAL`-and-weaker automatic states (which are still `is_watch_worthy=True` under ERG1.6, so still satisfy step 4) remain reachable, preserving the "early discovery, not just already-announced EMAS" principle the whole epic is built around. This is a recommendation for ERG4's own design mission to adopt or challenge further — **not implemented here**.

## 28. Defects/design ambiguities found

None. This slice's own design derivation (§7) explicitly worked through, and avoided, the exact category of mistake ERG2's adversarial review found and reverted — analytically, before writing any composite-FK code, directly per the mission's own instruction.

## 29. Corrections made

One test-authoring correction (not a production defect): the initial same-timestamp-tie test (§8) attempted to simulate the tie via a post-hoc `UPDATE` of an already-persisted assessment's `created_at`, which the (correct, intentional) immutability guard rejected — fixed by constructing both tied rows directly at INSERT time instead, which is both a more realistic simulation of a genuine same-timestamp tie and a further incidental confirmation that assessment immutability holds even under adversarial test-authoring pressure.

## 30. Focused tests

`tests/test_unknown_airport_candidate_relevance_review_persistence.py`: 39 passed. `tests/test_unknown_airport_candidate_relevance_review_migration.py`: 42 passed. `tests/test_model_contract.py`: 5 passed. Combined with ERG1/ERG2 and adjacent governance suites (`test_unknown_airport_candidate_relevance_persistence.py`, `test_unknown_airport_candidate_relevance_assessment_migration.py`, `test_emas_relevance_evaluation.py`, `test_unknown_airport_candidate_persistence.py`, `test_unknown_airport_candidate_migration.py`, `test_reviewer_action_persistence.py`) run together in one pass: **515 passed, 0 failed.**

## 31. Full pytest

`python -m pytest -q`: **3532 passed, 0 failed**, 4532 warnings (pre-existing deprecation/pytest-cache warnings only), 531.46s. Prior baseline (post-ERG2-review commit) was 3451 — the +81 delta is exactly this slice's own new test count (39 + 42 + 0, since the model-contract file's own 5 tests were modified in place, not added), confirming zero regressions anywhere else in the suite.

## 32. py_compile

`python -m py_compile` across all seven new/modified production and test files — clean.

## 33. git diff --check

Clean, exit code 0.

## 34. Real DB before/after

Before (§2): SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened `data/runway_safe.db` at all.

## 35. git status

New, untracked files: `app/models/unknown_airport_candidate_relevance_review.py`, `app/services/unknown_airport_candidate_relevance_review_persistence.py`, `scripts/migrate_unknown_airport_candidate_relevance_reviews_erg3.py`, `tests/test_unknown_airport_candidate_relevance_review_persistence.py`, `tests/test_unknown_airport_candidate_relevance_review_migration.py`, this report. Modified: `app/models/__init__.py` (additive registration only), `tests/test_model_contract.py` (new table contract only). No other tracked file touched. No commit made.

## 36. READY_FOR_ERG3_REVIEW_CHECKPOINT

**YES** — additive-only schema, fully append-only/immutable, stale-basis gate directly proven (including a same-timestamp tie), same-candidate integrity enforced and tested at the service level with the composite-FK alternative explicitly analyzed and rejected BEFORE any risky code was written, effective-review-state contract proven against every worked lifecycle in the mission text (Anoka negative review, rediscovery, dormant installation, contradiction), real DB confirmed untouched throughout.

## 37. Exact recommended next step

A separate, explicitly-authorized ERG3 adversarial review checkpoint (per this mission's own Section 34 commit policy) — independently re-deriving the basis-binding/stale-gate/effective-state logic against this report's own claims, specifically re-attacking the same-candidate integrity boundary (§7) and the stale-basis gate (§8) with fresh eyes, before this slice is committed. Only after that review authorizes a commit should ERG4 (the actual UAC4 `CREATE_NEW_AIRPORT` gate, consuming `resolve_effective_unknown_airport_candidate_relevance_review_state()` per §27's own recommended query) begin.

RWI_ERG3_HUMAN_RELEVANCE_REVIEW_RECORDING_IMPLEMENTATION_COMPLETE

---

## Adversarial Review Findings (independent review/commit/push mission)

Governing instruction: "DO NOT TRUST THE REPORT. Independently attack the full ERG3 semantics, migration, stale-handshake, cross-candidate integrity and future ERG4 seam." Every claim below was independently re-derived against the running code, not read from the implementation report above.

**§1 Starting state.** Confirmed fresh: HEAD `17dcfcc1661fe04e481062719cf955d772ef1d41 == origin/main`; working tree scope matched expectations exactly (2 modified tracked files, 5 new ERG3 files + report, all other untracked docs/screenshots pre-existing and unrelated); real DB SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` — all matched exactly, read-only throughout.

**§2-3 Fresh reads / review-model boundary.** `app/models/unknown_airport_candidate_relevance_review.py` and `app/services/unknown_airport_candidate_relevance_review_persistence.py` re-read in full from disk. Confirmed the review model is genuinely separate from `UnknownAirportCandidateReview` (identity/canonical-resolution) — no shared table, no shared action vocabulary, no mutation of either the candidate or the assessment it reviews.

**§4 Vocabulary attacks.** Live-probed the public service with `action = None`, `"confirm_emas_relevant"` (lowercase), `" CONFIRM_EMAS_RELEVANT "` (whitespace-padded), `"MATCH_EXISTING_AIRPORT"` (an identity-review action from the other vocabulary), `123` (int), `b"CONFIRM_EMAS_RELEVANT"` (bytes) — all six correctly raised `ValueError`, uniformly, via the `action not in RELEVANCE_REVIEW_ACTIONS` membership check.

**§5 Basis-assessment binding (HIGH PRIORITY).** Attempted cross-candidate binding via the public service (correctly refused, `test_cross_candidate_basis_rejected_g`), and independently via **direct ORM construction bypassing the service function entirely** — confirmed this SUCCEEDS at `session.flush()` with no DB-level block. This is not a surprise: it is the exact, honestly-documented "service-level only" boundary the model and service docstrings both describe. The composite-FK alternative was correctly rejected in advance (§7 of the original report) for a sound, non-empirical reason (a future migration cannot assume `unknown_airport_candidate_relevance_assessments` stays empty) — independently re-confirmed as still the correct call now, since the real table still holds zero rows and the same forward-looking risk still applies.

**§6-7 Current-latest / tiebreak.** Re-proved the `created_at DESC, id DESC` tiebreak by constructing two assessment rows with an identical `created_at` at INSERT time (assessments are immutable, so no post-hoc `UPDATE` trick is possible) — the higher-id row wins, exactly matching ERG2's own semantics.

**§8-9 Stale review state / NO_ASSESSMENT_YET vs UNREVIEWED.** Independently re-derived `RelevanceReviewState` transitions for all three actions (CONFIRM/MARK_NOT/DEFER) against fresh, stale, and absent assessments — matches the report's own claims exactly.

**§10-14 Human override / worked scenarios.** Re-derived the RUNWAY_ONLY + CONFIRM case directly (see §28 below, same underlying mechanism) — a human CONFIRM never mutates the automatic assessment's own booleans. Anoka-style negative-review, rediscovery, dormant-installation, and contradiction scenarios all reduce to combinations already covered by the general-purpose tests below; no scenario-specific defect found.

**§15 Immutability — all fields, not just `reason`.** The pre-existing test suite only attacked `.reason` on both the assessment and review rows. **Gap closed**: added `TestImmutability.test_review_field_mutation_blocked`, a `pytest.mark.parametrize` over `action`, `reviewer`, `basis_assessment_id`, and `candidate_id` (delete was already covered) — all four rejected identically by the column-agnostic `before_update` listener, as expected, but now with explicit, permanent regression coverage instead of an inferred guarantee.

**§16-17 Supersession / effective-state contract.** Re-derived `resolve_effective_unknown_airport_candidate_relevance_review_state()`'s four-way branch (`NO_ASSESSMENT_YET` / `UNREVIEWED` / `STALE` / `CURRENT`) directly against the running code for every combination the mission names — matches.

**§18 CRITICAL — same-basis multi-review resolution.** Live-probed DEFER-then-CONFIRM against the SAME `basis_assessment_id`: confirmed `resolve_effective_...()` returns `state=CURRENT, latest_review_action=CONFIRM_EMAS_RELEVANT` — the later-recorded review wins by plain recency. This is safe specifically BECAUSE the stale-basis gate structurally prevents an older-basis review from ever being recorded after a newer assessment exists, so recency alone (no `basis_assessment_id` filtering) is sufficient. Derived, not assumed, exactly per the mission's own instruction.

**§19 Cross-candidate leak via bad join.** `get_latest_unknown_airport_candidate_relevance_review()` is a plain `.filter(candidate_id == ...)` query with no join at all, structurally ruling out a join-based leak. Independently re-confirmed empirically with two candidates and a review recorded only against candidate A: candidate B's query correctly returned `None`.

**§20 no_autoflush re-attack.** The three pre-existing tests all used an unrelated *candidate* row as the pending-invalid object. **Gap closed**: added `test_get_latest_review_does_not_autoflush_unrelated_invalid_review_row` (pending-invalid object is itself a malformed `UnknownAirportCandidateRelevanceReview` violating the action CHECK constraint, not a candidate row) and `test_record_precondition_check_isolated_expired_candidate_attribute_alone` (isolates the expired-candidate-attribute-read sub-case with no unrelated pending object present at all, for documentation clarity, since it was previously only implicitly covered in combination).

**§21 Migration schema parity — independently re-verified via raw SQL, not `inspect()`.** Built a genuinely pre-ERG3 temp database (full `create_all` schema, then `DROP TABLE unknown_airport_candidate_relevance_reviews` to simulate the pre-migration state), ran the real `migration.upgrade()` against it, then compared `PRAGMA table_info`, `PRAGMA foreign_key_list`, and `PRAGMA index_list` output directly against a genuinely fresh `create_all`-built copy of the same table — **columns, FKs, and indexes matched exactly**, confirmed via raw `sqlite3`, independent of the model's own `inspect()` helper or the existing test suite's own assertions.

**§22 inspect/upgrade agreement under malformed schema.** Confirmed already covered by three distinct existing tests raising `IncompatibleExistingSchemaError` for column mismatch, missing named constraint, and missing index respectively — re-read and confirmed these exercise real SQLite schema variants, not mocks.

**§23-26 Atomicity / downgrade / raw SQL constraints / ORM-migration parity / failure atomicity.** Independently re-verified via a fresh raw-SQL probe: (a) a raw `INSERT` with `action='BOGUS'` is blocked by the CHECK constraint at the SQLite level, not merely by the ORM; (b) `downgrade()` with zero rows succeeds and drops the table; (c) `downgrade()` against a database holding one real (raw-SQL-inserted) review row correctly refuses with `RuntimeError` naming the row count, and the row is confirmed still present and intact afterward — real atomicity, not a mocked assertion. Confirmed the existing 42-test migration suite contains **zero mock usage** (`grep` for `mock|Mock|patch(` returned no matches) and does not use `create_all` as a substitute for the real `upgrade()` call under test (the one `create_all` call in the suite is explicitly used only to build the pre-migration fixture state, with an explicit `test_upgrade_creates_table`-adjacent comment noting "genuinely migrated (not create_all'd) database").

**§27 ERG4 seam.** Independently re-derived the exact gate contract: a future gate must check, on the CURRENT assessment, `(is_inventory_relevant OR is_watch_worthy)` AND, from `resolve_effective_...state()`, `state == CURRENT AND latest_review_action == CONFIRM_EMAS_RELEVANT`. Both conditions are independently queryable and neither alone is sufficient — confirmed directly by §28-§30 below.

**§28 Auto-negative + human CONFIRM.** Live-probed: G-class-only evidence (`is_inventory_relevant=False, is_watch_worthy=False`) + human `CONFIRM_EMAS_RELEVANT` → the re-read CURRENT assessment still shows both booleans `False`; a future gate computing `state.is_current and action==CONFIRM and (inventory or watch)` correctly evaluates `False`. **Gap closed**: promoted from an ad-hoc probe into a permanent test, `TestErg4SeamDataContractPreservation.test_auto_negative_plus_human_confirm_s28`. Confirms: a human cannot manufacture EMAS evidence merely by confirming.

**§29 Auto-positive + human MARK_NOT.** Live-probed: D-class evidence (`is_watch_worthy=True`) + human `MARK_NOT_EMAS_RELEVANT` → `latest_review_action == MARK_NOT_EMAS_RELEVANT`, never silently treated as a confirmation. Promoted to `test_auto_positive_plus_human_mark_not_s29`.

**§30 Auto-positive + STALE human CONFIRM.** Live-probed: assessment #1 (D-class) + human CONFIRM against #1, then a new assessment #2 (A-class, still auto-positive) recorded with no new review → `resolve_effective_...().state == STALE`, never `CURRENT`. A future gate checking `state.is_current` alone correctly blocks, with no additional gate-side bookkeeping required. Promoted to `test_auto_positive_plus_stale_confirm_s30`.

**§31 Information firewall.** Re-read the service module's own imports: only `UnknownAirportCandidate`, `UnknownAirportCandidateRelevanceAssessment`, the review model itself, and ERG2's `get_latest_unknown_airport_candidate_relevance_assessment()`. No import of any signal-creation, installation-creation, or graduation-pipeline module — structurally confirmed as governance-only, no downstream side effects reachable from this slice.

**§32 Test quality.** Checked against the mission's own named anti-patterns: no count-only assertions found beyond genuine multi-case loops; no helper-only tests; the three previously-missing S28/S29/S30 gates now have dedicated tests rather than relying on inference from the stale-basis tests alone; the cross-candidate test (`test_cross_candidate_basis_rejected_g`) genuinely attempts and rejects a real cross-candidate `basis_assessment_id`, not a weak same-candidate variant; the same-basis multi-review case (§18) now has explicit coverage beyond what existed; migration atomicity tests use real SQLite, not mocks; no `create_all` parity shortcut; exceptions raised are specific (`ValueError`, `IncompatibleExistingSchemaError`, `RuntimeError`), never bare `except Exception`; the no-autoflush expired-attribute attack now has an isolated, explicitly-labeled test. Three genuine, mission-justified gaps were found and closed (§15, §20, §28-§30 above) — no behavior defects were found in any of them; all three closures are coverage-only.

**§33 Test strategy.** Added tests compiled cleanly (`python -m py_compile`). Focused run (`test_unknown_airport_candidate_relevance_review_persistence.py` + `test_unknown_airport_candidate_relevance_review_migration.py` + `test_model_contract.py`): **95 passed** (48 + 42 + 5, the +9 delta over the implementation phase's 39 being exactly this review's own new tests: 3 ERG4-seam + 4 immutability-parametrize + 2 no-autoflush). `git diff --check`: clean. One final, complete full-suite run (`python -m pytest -q`): **3541 passed, 0 failed**, 4532 warnings (pre-existing deprecation/pytest-cache warnings only), 447.81s. Implementation-phase baseline was 3532 — the +9 delta is exactly this review's own new tests, confirming zero regressions anywhere else in the 3400+ pre-existing test suite.

**§34 Real DB safety.** Re-verified immediately before commit: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` — unchanged throughout the entire review.

**Verdict: SOUND.** No behavior defects found anywhere in the ERG3 implementation. Three genuine test-coverage gaps identified and closed (all coverage-only, zero production-code changes). The ERG4 seam (§27-§30) — the single most consequential property in this review, since it governs whether a future admission gate can be fooled by a human's own CONFIRM action — is proven to preserve exactly the raw materials a future gate needs to correctly block all three dangerous cases. Authorized for commit and push.

RWI_ERG3_HUMAN_RELEVANCE_REVIEW_RECORDING_REVIEWED_COMMITTED_AND_PUSHED
