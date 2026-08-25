# RWI ERG4 — Canonical Airport Admission Gate

Status: IMPLEMENTED. NOT COMMITTED, NOT PUSHED - a separate, explicitly-authorized adversarial review checkpoint governs this change, mirroring ERG1/ERG2/ERG3's own pattern.

## 1. Starting HEAD

`6ce749cdefa6ede6e57eb7f3412797e88b42c07f` - confirmed `== origin/main` at mission start.

## 2. Real DB proof

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened a writable connection to the real database anywhere - every test uses an isolated in-memory or tmp_path-scoped SQLite database.

## 3. Files read fresh

`app/services/unknown_airport_candidate_resolution.py` (UAC4, full), `scripts/review_unknown_airport_candidate.py` (UAC5A CLI, the CREATE_ACTION/MATCH_ACTION execute branches), `app/models/unknown_airport_candidate.py` (UAC1), `app/services/unknown_airport_candidate_relevance_persistence.py` (ERG2, full), `app/services/unknown_airport_candidate_relevance_review_persistence.py` (ERG3, full - re-confirmed via the already-current-session ERG3 review), `app/services/emas_relevance_evaluation.py` (ERG1 evidence-class-to-boolean mapping, to derive exact evidence recipes for inventory-only/watch-only/both/neither test fixtures), the existing UAC4 test suite (`tests/test_unknown_airport_candidate_resolution.py`) and UAC5 CLI test suite (`tests/test_review_unknown_airport_candidate.py`) in full, to map every existing CREATE_NEW_AIRPORT call site that a new precondition would affect.

## 4. UAC4 creation call chain

`UnknownAirportCandidate` + `UnknownAirportCandidateReview(action=CREATE_NEW_AIRPORT)` -> `create_airport_from_approved_candidate(session, candidate_id, review_id, name, country, ...)` -> one `Airport` row inserted, `candidate.resolved_airport_id` set, linked `SourceAssertion`s moved. This is the ONLY code path in the entire repository that ever inserts an `Airport` row from an `UnknownAirportCandidate` - confirmed by `TestCanonicalSideEffectFirewall.test_no_orm_construction_of_runway_installation_signal_in_module_source` (pre-existing) and by direct read of `scripts/review_unknown_airport_candidate.py`, which imports and calls this exact function (never reimplements it).

## 5. Authoritative enforcement seam

`create_airport_from_approved_candidate()` itself, immediately after the pre-existing identity-review gate (`_require_current_review()`, UNCHANGED) and before any Airport row is constructed. A new `_require_admission_eligible(session, candidate_id)` call was inserted at exactly this point, raising a new `RelevanceGateRefusedError` (a `RuntimeError` subclass, matching `AlreadyResolvedError`/`StaleReviewError`/`InconsistentCandidateStateError`'s own convention) when ineligible. `resolve_candidate_to_existing_airport()` (MATCH_EXISTING_AIRPORT - links to an EXISTING Airport, never creates one) is untouched, correctly out of ERG4's scope.

## 6. Exact ERG4 rule implemented

Canonical Airport admission requires ALL of: (1) a current/latest ERG2 automatic relevance assessment exists; (2) `is_inventory_relevant OR is_watch_worthy` on that assessment is True; (3) the ERG3 effective human relevance review state is CURRENT; (4) that CURRENT review's `basis_assessment_id` matches the assessment named in (1) exactly (guaranteed by ERG3's own CURRENT-state definition); (5) the CURRENT review's action is `CONFIRM_EMAS_RELEVANT`. Priority order for the REPORTED reason (not the boolean, which is always correct regardless of order) follows the mission's own Section 3 enumeration exactly: automatic relevance (steps 1-2) is checked before review-currency (steps 3-5) - so a candidate that is BOTH automatically non-relevant AND has no/stale/wrong-action review always reports `AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE`, never a review-side reason, matching the mission's own "human confirmation cannot manufacture EMAS relevance" framing.

## 7. Eligibility/result contract

New module `app/services/unknown_airport_candidate_admission_eligibility.py` (pure, read-only, no writes anywhere in the module). `AdmissionEligibilityReason(str, Enum)`: `NO_RELEVANCE_ASSESSMENT`, `AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE`, `NO_CURRENT_HUMAN_REVIEW`, `HUMAN_REVIEW_STALE`, `HUMAN_REVIEW_DEFERRED`, `HUMAN_REVIEW_MARKED_NOT_RELEVANT`, `ELIGIBLE` - exactly the mission's own suggested vocabulary. `AdmissionEligibilityResult` (frozen dataclass): `candidate_id`, `eligible: bool`, `reason`, `latest_assessment_id`, `is_automatic_admission_relevant`, `review_state` (the ERG3 `RelevanceReviewState` enum, reused not duplicated), `latest_review_id`, `latest_review_action`. `evaluate_unknown_airport_candidate_admission_eligibility(session, candidate_id)` composes `get_latest_unknown_airport_candidate_relevance_assessment()` (ERG2, unmodified) and `resolve_effective_unknown_airport_candidate_relevance_review_state()` (ERG3, unmodified) - no independent sorting/tiebreak/staleness logic of its own. `eligible` is always exactly `reason == ELIGIBLE`, derived from ONE ordered elif chain with an explicit fail-closed default branch for any unrecognized review action (reachable only via a malformed in-memory-only ORM state).

## 8. Automatic-admission derivation verdict

Derived fresh on every call as `assessment.is_inventory_relevant or assessment.is_watch_worthy`, never a persisted column - mirrors `UnknownAirportCandidateRelevanceAssessmentResult.is_canonical_admission_relevant`'s own existing convention exactly. No new column, no new table, no migration.

## 9. No-assessment verdict

Confirmed BLOCK, `reason=NO_RELEVANCE_ASSESSMENT`, both at the pure-evaluator layer and via the real authoritative service (`create_airport_from_approved_candidate` raises `RelevanceGateRefusedError`). Zero Airport/Runway/RunwayEnd/Signal/Installation created; candidate/history unchanged.

## 10-13. Anoka core regression (A/B/C/D)

All four human states (no review, DEFER, MARK_NOT, CONFIRM) correctly BLOCK via the real authoritative service, proven in `TestErg4AnokaRegressionViaAuthoritativeService`. Case D (automatic false + human CONFIRM) reports `AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE`, proving human confirmation cannot manufacture EMAS relevance - the central product rule, independently re-derived (not merely asserted) at both the pure-evaluator layer (`TestAnokaCoreRegression`) and the service layer.

## 14-17. Automatic-positive review-state matrix

No review -> `NO_CURRENT_HUMAN_REVIEW` (BLOCK). DEFER -> `HUMAN_REVIEW_DEFERRED` (BLOCK). MARK_NOT -> `HUMAN_REVIEW_MARKED_NOT_RELEVANT` (BLOCK). Current CONFIRM -> `ELIGIBLE`, and via the authoritative service this genuinely creates the Airport (`TestErg4EligibleAdmissionSucceeds`).

## 18. Stale-CONFIRM verdict

Assessment #1 (positive) + CONFIRM, then assessment #2 (still positive) with no new review -> `HUMAN_REVIEW_STALE`, BLOCK (reported reason follows the automatic-relevance-first priority rule of §6 only when automatic relevance is ALSO false; here automatic relevance is true, so staleness is correctly the reported reason). CONFIRM against #2 -> `ELIGIBLE`. Proven at both layers.

## 19. Positive -> negative rediscovery verdict

Assessment #1 positive + CONFIRM, assessment #2 negative. Before any new review: BLOCK, reason `AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE` (automatic-relevance-first priority - see §6's own documented ordering rationale; this is a deliberate, derived choice, not an oversight). After an explicit new CONFIRM against #2: still BLOCK, same reason - automatic admission=false always wins regardless of what the human says.

## 20. Negative -> positive rediscovery verdict

Assessment #1 negative + MARK_NOT, assessment #2 positive. Before new review: `HUMAN_REVIEW_STALE`, BLOCK. After CONFIRM against #2: `ELIGIBLE`. The old MARK_NOT review row remains fully present in history (append-only, proven directly by row count).

## 21-23. Inventory-only / watch-only / both-true

E-class evidence (default temporality) -> `is_inventory_relevant=True, is_watch_worthy=False` -> eligible after CONFIRM, proving canonical inventory is distinct from active opportunity (no Signal or Installation created merely because admission is allowed - ERG4 creates nothing). A-class evidence -> `is_inventory_relevant=False, is_watch_worthy=True` -> eligible after CONFIRM, proving no existing Installation is required. Both combined -> eligible, no special higher privilege (same `ELIGIBLE` reason, same boolean).

## 24. Identity-review non-regression verdict

`TestErg4IdentityReviewStillRequired`: a candidate that is fully ERG4-eligible (automatic relevance + current CONFIRM) is STILL correctly blocked by UAC4's own pre-existing `StaleReviewError` when there is no CREATE_NEW_AIRPORT identity review at all, or when the identity review is DEFER - proving ERG4 is a strictly additive precondition, never a replacement for identity governance.

## 25. Latest-assessment/tie verdict

Re-proved directly: two assessment rows constructed with an identical `created_at` (assessments are immutable, so no post-hoc `UPDATE` tie is possible) - the higher-id row wins, reusing ERG2's own helper unmodified, no duplicated sort logic anywhere in ERG4.

## 26. Latest-review/same-basis verdict

DEFER-then-CONFIRM on the same basis assessment -> latest action (CONFIRM) governs, eligible. CONFIRM-then-MARK_NOT on the same basis -> latest action (MARK_NOT) governs, blocked. Reuses ERG3's own `resolve_effective_...()` unmodified.

## 27. Cross-candidate verdict

Candidate A's positive assessment can never combine with candidate B's CONFIRM review to make either candidate eligible (`TestCrossCandidateAttack`, pure-evaluator layer, and `TestErg4CrossCandidateViaAuthoritativeService`, real service layer). A direct-ORM bypass of ERG3's own service-level same-candidate check (setting `candidate_id=A` but `basis_assessment_id` pointing at B's own current assessment) is deterministically caught by the STALE branch, not by luck: assessment ids are globally unique across the whole table, so a foreign assessment id can never equal candidate A's own current assessment id.

## 28. Malformed-state verdict

An in-memory-only review with an unrecognized `action` string (mutated directly on the ORM object without ever flushing/committing - a real commit would hit the model's own `before_update` immutability guard) is caught by ERG4's fail-closed default branch: never `ELIGIBLE`. Reachable only via a state that cannot be persisted through any governed write path.

## 29. No-autoflush verdict

ERG4's OWN precondition phase (`evaluate_unknown_airport_candidate_admission_eligibility()`) is exhaustively proven safe against unrelated pending candidate objects, unrelated pending (malformed) review objects, and expired candidate attributes (`tests/test_unknown_airport_candidate_admission_eligibility.py::TestNoAutoflush`, all passing). **One genuine defect was found during this proof and fixed**: the initial implementation wrapped only the two upstream ERG2/ERG3 helper calls in `session.no_autoflush`, but read `latest_assessment.id`/`.is_inventory_relevant`/`.is_watch_worthy` OUTSIDE that block - an expired-attribute read there could still trigger the exact autoflush leak ERG2/ERG3 already taught this codebase to guard against. Fixed by moving those attribute reads inside the SAME `no_autoflush` block, matching the "wrap starting from the very first read" discipline established in ERG2's own review.

**Separately, an ADVERSARIAL-REVIEW-STYLE FINDING was made and NOT fixed, per this mission's own explicit "if a genuine blocking defect outside ERG4's scope is found: STOP and report rather than scope-creep" instruction**: attacking the FULL `create_airport_from_approved_candidate()` entry point with an unrelated pending invalid object (added before the call) reliably leaks a raw `sqlite3.IntegrityError` - but from pre-existing, unmodified UAC4/UAC1 code, not from anything ERG4 added. `_require_current_review()` (pre-existing UAC4) calls `get_latest_unknown_airport_candidate_review()` (pre-existing UAC1, `app.services.unknown_airport_candidate_persistence`), which performs a bare `session.query(...)` with no `session.no_autoflush` wrapper - unlike every "latest" helper ERG2/ERG3 added. Confirmed directly that this pre-dates ERG4 entirely: the identical attack against `resolve_candidate_to_existing_airport()` (the MATCH_EXISTING_AIRPORT path, which ERG4 never touches) leaks the identical error. Documented in `tests/test_unknown_airport_candidate_resolution.py::TestErg4NoAutoflush`'s own class docstring; recommended as a small, separately-scoped future fix mirroring the ERG2/ERG3 precedent (wrap `get_latest_unknown_airport_candidate_review()`'s own query in `session.no_autoflush`).

## 30. Failure-atomicity verdict

Every blocked admission (`RelevanceGateRefusedError` raised before any `Airport`/`Runway`/`RunwayEnd`/`Installation`/`Signal` construction) leaves zero partial canonical objects, proven directly (`TestErg4FailureAtomicityAndHistoryPreservation.test_blocked_admission_leaves_zero_partial_canonical_objects`). Injected-failure-during-allowed-admission atomicity was already proven by the pre-existing `test_create_failure_after_airport_flush_rolls_back_completely` (now updated with the ERG4 fixture, still passing) - ERG4 adds no new commit anywhere, so this pre-existing guarantee is unaffected.

## 31. Direct-service-bypass verdict

Every test in this entire mission calls `create_airport_from_approved_candidate()` directly - none import or invoke `scripts/review_unknown_airport_candidate.py`. `TestErg4DirectServiceBypass` names this property explicitly. The CLI's own execute-dry-run path ALSO calls the real function (with a post-hoc rollback only if `--allow-database-write` is absent), so even the CLI's "preview" mode is genuinely gated, not a second, weaker code path.

## 32. CLI/caller verdict

`scripts/review_unknown_airport_candidate.py` was touched minimally: `RelevanceGateRefusedError` added to the CLI's existing `except (...)` tuple around its one call to `create_airport_from_approved_candidate()`, so a refusal surfaces as the CLI's own existing `execute_refusal_reason` field instead of an uncaught exception. Zero duplicated gate logic - the CLI never re-implements or re-checks eligibility itself.

## 33. Existing-Airport non-retroactivity verdict

No migration, no backfill, no scan of existing `Airport` rows anywhere in this slice - ERG4 adds zero new tables/columns (confirmed: `app/models/__init__.py` untouched). It only gates the one, single code path that creates a NEW canonical Airport from an `UnknownAirportCandidate`; existing Airports are never read, compared against, or touched by anything ERG4 added.

## 34. Anoka-history preservation verdict

`TestErg4FailureAtomicityAndHistoryPreservation.test_anoka_candidate_and_full_history_remain_intact_after_blocked_admission`: after a blocked CREATE_NEW_AIRPORT attempt, the candidate row, its linked SourceAssertion (still linked, `airport_id` still None), its ERG2 relevance assessment, and its ERG3 relevance review are all confirmed to remain fully queryable and unchanged.

## 35. UAC5B firewall verdict

Not touched, not fixed, not re-investigated - out of scope per the mission's own explicit instruction. ERG4 answers only "may this candidate proceed toward canonical admission," never "can its evidence subsequently reach Signal creation." `unknown_airport_candidate_admission_eligibility.py`'s own module docstring states this distinction explicitly ("ELIGIBLE DOES NOT IMPLY SIGNAL-CREATABLE").

## 36. Information-firewall verdict

`unknown_airport_candidate_admission_eligibility.py` imports only `get_latest_unknown_airport_candidate_relevance_assessment` (ERG2) and `RelevanceReviewState`/`resolve_effective_unknown_airport_candidate_relevance_review_state` (ERG3) - confirmed by direct grep of its own import block. No import of `Airport`'s write path, `Signal`, `Installation`, UAC3, or any promotion/publish code. ERG1/ERG2/ERG3/identity-guard/UAC3/EvidenceBag/SourceAssertion/Signal-creation/Installation-creation/promotion/publishing source files are all byte-for-byte unmodified (`git diff` confirms zero changes to any of them).

## 37. Test-quality verdict

Checked against the mission's own named anti-patterns: no helper-only or CLI-only tests (the authoritative service is tested directly, repeatedly, at both the pure-evaluator and full-service layers); no count-only assertions without row-level zero-write proof; Anoka's own human-CONFIRM negative case has a dedicated, explicitly-named test; the stale-positive-CONFIRM case (§13/§18) is proven at both layers; inventory-only and watch-only cases each have dedicated tests; identity-gate non-regression has two dedicated tests (no review, and DEFER); cross-candidate has both a pure-evaluator and a service-layer test, including a direct-ORM bypass proof; direct-service-bypass is named explicitly; exceptions raised are specific (`RelevanceGateRefusedError`, `ValueError`, `StaleReviewError`) never bare `except Exception`; no `create_all` migration-parity shortcut anywhere (ERG4 has no migration, so this doesn't apply); no-autoflush is exercised at the pure-evaluator layer for all three named attack shapes, with the service-layer gap honestly documented rather than papered over with a misleading passing test.

## 38. Defects found

(a) A genuine no-autoflush leak in `evaluate_unknown_airport_candidate_admission_eligibility()`'s own initial implementation (attribute reads outside the `no_autoflush` block) - found and fixed, see §29. (b) A pre-existing, out-of-ERG4-scope no-autoflush gap in UAC1's `get_latest_unknown_airport_candidate_review()`, shared by both UAC4 execution paths, unrelated to relevance governance - found, NOT fixed, documented per the mission's own "STOP and report" instruction, see §29.

## 39. Corrections made

Fixed (a) from §38 in `app/services/unknown_airport_candidate_admission_eligibility.py` before any test file was finalized. Several of my OWN new test files initially asserted incorrect expectations (reading `candidate.id`/constructing objects in an order that itself triggered ERG2/ERG3's own already-known autoflush hazard, and one incorrect assumption about reason-priority ordering in the positive-to-negative-rediscovery case) - all corrected to match the actual, verified-correct, mission-compliant behavior; none required a production-code change beyond (a).

## 40. Regression tests added

36 new tests (25 + 11 = 36; an earlier draft of this section miscounted this as "37" - corrected during the adversarial review, see that mission's own §22/§40): 25 in `tests/test_unknown_airport_candidate_admission_eligibility.py` (pure evaluator, all mission sections 7-25), 11 net new in `tests/test_unknown_airport_candidate_resolution.py` (`TestErg4AnokaRegressionViaAuthoritativeService` x4, `TestErg4EligibleAdmissionSucceeds` x1, `TestErg4IdentityReviewStillRequired` x2, `TestErg4CrossCandidateViaAuthoritativeService` x1, `TestErg4FailureAtomicityAndHistoryPreservation` x2, `TestErg4DirectServiceBypass` x1). Plus fixture-only updates (no new test count) to 12 pre-existing CREATE_NEW_AIRPORT-reaching tests across `tests/test_unknown_airport_candidate_resolution.py`, `tests/test_review_unknown_airport_candidate.py`, `tests/test_effective_identity_guard_decision.py`, and `tests/test_resolved_candidate_evidence_reevaluation.py`, all of which now supply the minimal ERG2/ERG3 state ERG4 requires - one pre-existing test (`test_create_zero_assertions_succeeds`) was intentionally converted from a success case to an explicit `RelevanceGateRefusedError`/`NO_RELEVANCE_ASSESSMENT` regression test, since a genuinely zero-evidence candidate can structurally never satisfy ERG2's own evidence-traceability rule and therefore can never become ERG4-eligible - an intended consequence of the mission's own §7, not a defect.

## 41. Focused tests

`test_emas_relevance_evaluation.py` + `test_unknown_airport_candidate_relevance_persistence.py` + `test_unknown_airport_candidate_relevance_review_persistence.py` + `test_unknown_airport_candidate_relevance_review_migration.py` + `test_unknown_airport_candidate_resolution.py` + `test_review_unknown_airport_candidate.py` + `test_unknown_airport_candidate_admission_eligibility.py` + `test_unknown_airport_candidate_persistence.py` + `test_unknown_airport_candidate_migration.py` + `test_model_contract.py`: **557 passed, 0 failed**.

## 42. Full pytest

First full run surfaced 2 ripple failures in files not covered by the focused run above (`test_effective_identity_guard_decision.py`, `test_resolved_candidate_evidence_reevaluation.py`) - both fixed with the same ERG4 fixture pattern (see §40). Final full run: **3577 passed, 0 failed**, 4532 warnings (pre-existing deprecation/pytest-cache warnings only), 438.20s. Baseline (post-ERG3) was 3541 - the +36 delta is exactly this slice's own new tests (25 + 11).

## 43. py_compile

Clean across every new/modified production and test file.

## 44. git diff --check

Clean, exit code 0.

## 45. Real DB after

SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` - byte-identical to before. This mission never opened `data/runway_safe.db`.

## 46. Files modified/created

Created: `app/services/unknown_airport_candidate_admission_eligibility.py`, `tests/test_unknown_airport_candidate_admission_eligibility.py`, this report. Modified: `app/services/unknown_airport_candidate_resolution.py` (new `RelevanceGateRefusedError`, `_require_admission_eligible()`, one call site added inside `create_airport_from_approved_candidate()`, docstring updates - `resolve_candidate_to_existing_airport()` untouched), `scripts/review_unknown_airport_candidate.py` (one import, one except-tuple entry), `tests/test_unknown_airport_candidate_resolution.py`, `tests/test_review_unknown_airport_candidate.py`, `tests/test_effective_identity_guard_decision.py`, `tests/test_resolved_candidate_evidence_reevaluation.py` (all four: ERG4 fixture helper/imports plus call-site updates only). No model, migration, or `app/models/__init__.py` changes - zero schema change.

## 47. git status

Confirmed: exactly the 8 files above (1 new production file, 1 new test file, this new report, 5 modified files) show as modified/untracked-and-relevant; every other untracked file in the working tree is a pre-existing, unrelated leftover from earlier missions (confirmed unchanged in count and name from mission start). No commit made.

## 48. READY_FOR_ERG4_REVIEW_CHECKPOINT

**YES** - the authoritative enforcement seam is proven correct via 36 new tests spanning the pure evaluator and the real, authoritative `create_airport_from_approved_candidate()` service (including a direct-service-bypass proof and a real no-autoflush fix), every mission-specified scenario (Anoka x4, automatic-positive review matrix, stale-CONFIRM, both rediscovery directions, inventory-only/watch-only/both-true, identity-gate non-regression, cross-candidate, malformed state, failure atomicity, history preservation) independently re-derived rather than assumed, zero schema change, real DB confirmed untouched throughout, and one out-of-scope pre-existing defect found and honestly documented rather than silently fixed or silently ignored.

## 49. Exact recommended next step

A separate, explicitly-authorized ERG4 adversarial review checkpoint (mirroring ERG1/ERG2/ERG3's own pattern) - independently re-deriving the eligibility-reason priority ordering (§6, especially the automatic-relevance-first choice for the "no review + automatic-negative" and "stale + automatic-negative" cases), re-attacking the cross-candidate and malformed-state boundaries with fresh eyes, and deciding whether the pre-existing UAC1 no-autoflush gap (§29/§38) should be fixed now, as a small separately-scoped follow-up, or left for a dedicated future mission - before this slice is committed. Only after that review authorizes a commit should any future ERG5/UAC4-integration work (wiring `evaluate_unknown_airport_candidate_admission_eligibility()`'s own result into the UAC5 CLI's inspect/preview output, if narrowly justified) begin.

RWI_ERG4_CANONICAL_AIRPORT_ADMISSION_GATE_IMPLEMENTATION_COMPLETE
