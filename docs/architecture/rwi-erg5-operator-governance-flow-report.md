# RWI ERG5 — Operator / CLI Governance Flow

Status: IMPLEMENTED. NOT COMMITTED, NOT PUSHED — a separate, explicitly-authorized adversarial review checkpoint governs this change, mirroring ERG1-4/UAC-H1's own pattern.

## 1. Starting HEAD

`4531c60df9e6352b86eef0b54b594ea78df52054` — confirmed `== origin/main` at mission start.

## 2. Real DB proof / operational finding

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened a writable connection to the real database anywhere. **Independently confirmed via direct read-only PRAGMA inspection at mission start: the real database has the UAC2A/UAC2B identity schema ready, but the ERG2 (`unknown_airport_candidate_relevance_assessments`) and ERG3 (`unknown_airport_candidate_relevance_reviews`) tables do NOT exist yet**, and `unknown_airport_candidates` holds exactly 1 row. This made §31's "schema-absent compatibility" requirement a genuine, present-day operational concern, not a hypothetical - the fix was built and tested against exactly this real starting condition (via `_make_erg_pre_migration_db()`, a fixture that reproduces it structurally).

## 3. Files read

`scripts/review_unknown_airport_candidate.py` (full, before any change), `app/services/unknown_airport_candidate_persistence.py`, `app/services/unknown_airport_candidate_resolution.py`, `app/services/unknown_airport_candidate_relevance_persistence.py`, `app/services/unknown_airport_candidate_relevance_review_persistence.py`, `app/services/unknown_airport_candidate_admission_eligibility.py`, `app/models/unknown_airport_candidate_relevance_assessment.py` (for the evidence-link table shape), `scripts/migrate_unknown_airport_candidate_relevance_assessments_erg2.py` and `..._erg3.py` (both `inspect()` functions), the existing CLI test suite (`tests/test_review_unknown_airport_candidate.py`, full, pre-change).

## 4. Existing CLI capability inventory (before ERG5)

Three modes: pure inspect (`--candidate-id` alone, read-only engine), identity review recording (`--decision {MATCH_EXISTING_AIRPORT,CREATE_NEW_AIRPORT,REJECT_CANDIDATE,DEFER}`, dry-run/write-gated), and identity resolution execution (`--execute --review-id N`, dry-run/write-gated, calling UAC4's `resolve_candidate_to_existing_airport()`/`create_airport_from_approved_candidate()` directly - the latter already ERG4-gated as of the prior mission). Inspect exposed only identity-layer facts: raw claimed fields, resolution state, full identity review history, linked SourceAssertion evidence, deterministic canonical-code matches. **Nothing about ERG2 automatic relevance, ERG3 human relevance review, or ERG4 admission eligibility was visible anywhere in the CLI** - an operator had to query the database directly to answer "why can/can't this become canonical."

## 5. Chosen ERG5 architecture

A new, read-only service module, `app/services/unknown_airport_candidate_governance_view.py`, exposing `get_unknown_airport_candidate_governance_view(session, candidate_id)` — composes ERG2's `get_latest_unknown_airport_candidate_relevance_assessment()`, ERG3's `resolve_effective_unknown_airport_candidate_relevance_review_state()` and `get_latest_unknown_airport_candidate_relevance_review()`, and ERG4's `evaluate_unknown_airport_candidate_admission_eligibility()` into one frozen, plain-scalar result tree (`AutomaticRelevanceView`, `HumanRelevanceReviewView`, `CanonicalAdmissionView`). Chosen over inline CLI composition (mission's own §25 option) because the view genuinely needed MORE raw fields than any single existing helper's own result contract exposes (full assessment row, full latest-review row, evidence-link ids) while still composing three separately-governed modules - a dedicated, testable, read-only view service keeps that composition auditable and independently unit-testable, mirroring the existing `app/services/effective_identity_guard_decision.py` precedent for exactly this "compose several already-governed pieces into one queryable view" shape. The CLI (`_read_candidate_state()`) calls this service exactly once per invocation and does no additional derivation of its own.

## 6. Operator output contract

Additive fields on the existing, non-restructured `UnknownAirportCandidateReviewResult` dataclass (preserving every existing flat field per §28's non-regression requirement): `erg_schema_readiness: dict | None`, `automatic_relevance: AutomaticRelevanceView | None`, `human_relevance_review: HumanRelevanceReviewView | None`, `canonical_admission: CanonicalAdmissionView | None`, plus a parallel set of `proposed_relevance_*`/`relevance_action_*`/`relevance_written*` fields for the new relevance-review-recording mode - all plain, JSON-serializable scalars/tuples/nested frozen dataclasses, zero ORM objects leaked. `render_result()` gained new labeled text sections (`AUTOMATIC EMAS RELEVANCE`, `HUMAN EMAS RELEVANCE REVIEW`, `CANONICAL ADMISSION`) with explicit, non-misleading wording (see §10).

## 7. Identity-state display verdict

Unchanged fields (`resolved_airport_id`, `latest_review`, `review_history`), with section labels clarified (`RESOLUTION STATE (canonical Airport linkage, not relevance)`, `IDENTITY REVIEW STATE - LATEST: ...`) to make the identity/relevance distinction explicit in the rendered text, per §10.

## 8. Automatic-relevance display verdict

`AutomaticRelevanceView` exposes: `assessment_id`, `outcome`, `reason`, `evidence_classes_matched`/`contradicting_evidence_classes` (sorted tuples via ERG2's own `deserialize_evidence_classes()`, never hand-parsed), `is_inventory_relevant`, `is_watch_worthy`, `is_canonical_admission_relevant` (derived inline as `inventory or watch`, the same one-line derivation ERG2's own `UnknownAirportCandidateRelevanceAssessmentResult.is_canonical_admission_relevant` property already establishes as this codebase's convention - not a new rule), `evaluator_version`, `created_at`, `linked_source_assertion_ids`. `None` when no assessment exists.

## 9. Human-review-state display verdict

`HumanRelevanceReviewView` exposes ERG3's own `state` (NO_ASSESSMENT_YET/UNREVIEWED/STALE/CURRENT) and `is_current` boolean, plus the LATEST recorded review's own real fields (id, basis_assessment_id, action, reviewer, reason, created_at) - deliberately never suppressed even when `state=STALE`, satisfying the mission's own "historical CONFIRM exists but is not current authority, never silently hidden" requirement (§8/§21). Rendered text appends a `- STALE, NOT current authority` qualifier whenever `is_current` is False.

## 10. Canonical-admission display verdict

`CanonicalAdmissionView` exposes exactly `eligible: bool` and `reason: str` (the raw `AdmissionEligibilityReason` enum value, not a paraphrase - the most exact possible wording). Rendered text explicitly states: *"ELIGIBLE means the ERG4 relevance gate allows canonical-admission consideration - it does NOT mean an Airport has already been created, and does NOT mean the separate UAC4 identity-review gate has also passed."* Verified directly (`test_g_auto_positive_current_confirm_eligible`) that this exact sentence appears in rendered output.

## 11. Evidence-traceability display verdict

`linked_source_assertion_ids` on `AutomaticRelevanceView`, read via a plain, structural query against ERG2's own evidence-link table (`UnknownAirportCandidateRelevanceAssessmentEvidenceLink`) - no relevance judgment involved. Verified exact-match against the real linked SourceAssertion ids (`test_n_exact_evidence_link_ids`) and verified NEW assessments do not inherit an OLDER assessment's own evidence links (`TestEvidenceLinkIsolationAcrossAssessmentGenerations`).

## 12. Anoka negative-view result

`test_b_anoka_auto_negative_mark_not`: outcome=RUNWAY_ONLY_NOT_EMAS_RELEVANT, inventory=False, watch=False, human state=CURRENT/MARK_NOT_EMAS_RELEVANT, admission BLOCKED/AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE. Matches the mission's own §6 worked example exactly.

## 13. Anoka CONFIRM-but-auto-negative result

`test_c_anoka_auto_negative_confirm_remains_blocked`: human state CURRENT/CONFIRM_EMAS_RELEVANT, admission STILL BLOCKED/AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE - the central product rule, now independently visible at the CLI/operator layer, not just the service layer.

## 14. Unreviewed-positive result

`test_d_auto_positive_unreviewed`: watch=True, human state=UNREVIEWED, admission BLOCKED/NO_CURRENT_HUMAN_REVIEW - distinct reason from the Anoka case, so an operator can tell "no automatic relevance" apart from "no human review yet" at a glance.

## 15. Stale-review result

`test_h_stale_confirm`: assessment #2 is latest (`automatic_relevance.assessment_id == a2`), human state=STALE (`is_current=False`), the LATEST recorded review's own `action=CONFIRM`/`basis_assessment_id=a1` still shown (not hidden), admission BLOCKED/HUMAN_REVIEW_STALE, rendered text contains the explicit "STALE, NOT current authority" qualifier.

## 16. Rediscovery lifecycle result

`test_i_rediscovery_stale_then_new_confirm_eligible`: stale state confirmed BLOCKED, then after an explicit new CONFIRM against the current assessment, state flips to CURRENT and admission becomes ELIGIBLE - the full mandatory end-to-end regression the mission names.

## 17. Dormant-installation view

`test_j_dormant_inventory_only`: inventory=True, watch=False, current CONFIRM → eligible=True. Both booleans shown explicitly, distinguishing this from an active opportunity - no Signal/Installation-related field anywhere near it.

## 18. Watch-only view

`test_k_watch_only`: inventory=False, watch=True, current CONFIRM → eligible=True, no Installation implied.

## 19. No-assessment view

`test_a_no_assessment`: `automatic_relevance=None`, `human_relevance_review.state="NO_ASSESSMENT_YET"`, `canonical_admission.eligible=False/NO_RELEVANCE_ASSESSMENT`, no exception, rendered text shows "NO ASSESSMENT YET" cleanly.

## 20. Contradiction display

`test_l_contradictions_displayed_even_with_confirm`: a CONTRADICTING-polarity A-class observation alongside a POSITIVE one produces `contradicting_evidence_classes=("A_EXPLICIT_EMAS",)`, shown in rendered output even though the human CONFIRMed - never suppressed by a positive human decision, per the mission's own explicit requirement.

## 21. Evaluator-version display

`test_m_evaluator_version_displayed`: the persisted `evaluator_version` string is shown verbatim in both the structured result and rendered text - no evaluator re-invocation anywhere in this module (confirmed structurally: the governance view module never imports `evaluate_emas_relevance`).

## 22. Relevance-review recording API/CLI flow

New CLI mode `--relevance-decision {CONFIRM_EMAS_RELEVANT,MARK_NOT_EMAS_RELEVANT,DEFER_RELEVANCE_REVIEW} --basis-assessment-id N --reviewer ... --reason ...` (optionally `--supersedes-relevance-review-id`), mutually exclusive with `--decision`/`--execute` (validated in `_validate_config()`, mirroring the existing MODE SEPARATION discipline exactly). Wires `record_unknown_airport_candidate_relevance_review()` directly via a new `_run_relevance_review_write()` function that is structurally identical to the existing `_run_review_write()` - same dry-run/write-gate pattern (always call the real governed function against a writable session; commit only if `--allow-database-write`, otherwise roll back). Zero CLI-side stale-basis/cross-candidate/action-vocabulary logic - the governed function's own `ValueError` is the sole authority, caught and surfaced verbatim as `relevance_action_refusal_reason`.

## 23. Stale-basis UX

`test_o_stale_basis_ux_honest_refusal_names_current_id`: attempting `--basis-assessment-id` against a now-superseded assessment fails with the governed function's own error, which names the CURRENT assessment id explicitly (verified: the current id's string form appears in the refusal message) - never silently rewritten to the current one.

## 24. Dry-run/write-gate verdict

`test_q_dry_run_writes_zero_rows` / `test_r_authorized_write_appends_exactly_one_review`: dry-run (no `--allow-database-write`) leaves the `unknown_airport_candidate_relevance_reviews` table completely empty; an authorized write appends exactly one row with the correct action. Identical convention to the pre-existing identity-review/execute modes - no new, weaker write mechanism introduced.

## 25. Same-basis multi-review verdict

`test_s_defer_then_confirm_same_basis_shows_current_confirm`: two separate CLI write invocations (DEFER, then CONFIRM, same `--basis-assessment-id`) result in inspect showing CURRENT/CONFIRM_EMAS_RELEVANT and `canonical_admission.eligible=True` - the later action governs, reusing ERG3's own resolver, no CLI-side recency logic.

## 26. Identity/relevance separation verdict

`test_t_relevance_eligible_but_identity_defer_still_blocks_execution`: a candidate with `canonical_admission.eligible=True` (relevance gate) but identity review DEFER still correctly fails `--execute` with the identity gate's own `StaleReviewError` message - the two gates never conflated. `test_no_flattening_into_one_approved_flag` additionally proves the output contract itself keeps `latest_review` (identity), `canonical_admission` (relevance gate), and `resolved_airport_id` (actual admission state) as three independently-readable fields, never collapsed into one boolean.

## 27. Governance-helper reuse verdict

Structurally verified (`TestErg5DirectGovernanceReuse`, AST-based import inspection): the CLI module source contains no `is_inventory_relevant or`/`is_watch_worthy or` pattern and never directly queries `UnknownAirportCandidateRelevanceAssessment`/`UnknownAirportCandidateRelevanceReview` - all relevance/admission logic is reached exclusively through the imported governance-view/ERG2/ERG3/ERG4 functions. The governance-view module's own imports (parsed via `ast`, not substring-matched against its own docstring) contain no `Signal`, `Installation`, `unknown_airport_discovery_integration`, or `identity_guard` reference.

## 28. No-autoflush verdict

Attacked at three layers, all passing: (a) the governance-view service directly, against all three shapes (unrelated pending candidate object, unrelated pending review object, expired candidate attributes - `tests/test_unknown_airport_candidate_governance_view.py::TestNoAutoflush`); (b) the CLI's own inspect path with an unrelated pending object (`test_v_inspect_does_not_leak_on_unrelated_invalid_pending_object`); (c) the relevance-review-write precondition phase, attacked via an invalid basis (proving the precondition-check itself never leaks, while correctly leaving the genuinely-invalid unrelated object still pending - a real write's own intentional flush legitimately still validates it, per the UAC-H1-established "no swallowing of real write errors" boundary, not re-litigated here).

## 29. Schema-absent verdict

`check_erg_schema_readiness()` (mirrors `check_schema_readiness()`'s own `inspect()`-reuse pattern, composing ERG2's and ERG3's own `inspect()` functions) is checked SEPARATELY from the pre-existing identity schema gate. Verified against a fixture reproducing the REAL database's own current state (identity schema ready, ERG2/ERG3 absent): plain inspect and identity-review/execute modes all continue working unchanged, with the governance-view fields simply `None` and `erg_schema_readiness["ready"]=False` visible in the result (`TestErg5SchemaAbsent`, 3 tests) - `--relevance-decision` alone hard-blocks with `ERG_RELEVANCE_SCHEMA_MIGRATION_REQUIRED`, since it structurally cannot function without those tables. No migration executed anywhere in this module.

## 30. Existing CLI non-regression

All 48 pre-existing CLI tests still pass unchanged in behavior (2 needed mechanical updates: one error-message regex, one direct internal-function call site gaining a new required keyword argument from the UAC-H1 mission's own prior signature change - neither is an ERG5-introduced behavior change). Full existing MATCH/CREATE/DEFER/REJECT/inspect/execute flows re-run and confirmed identical.

## 31. Signal/Installation firewall verdict

`test_x_no_signal_or_installation_created_across_full_flow`: canonical counts (Signal, Installation, Runway, RunwayEnd) unchanged across a full inspect + relevance-review-write + re-inspect flow. `test_no_signal_eligible_field_invented`: confirmed no `signal_eligible` (or similarly-named) field exists anywhere on the result or its nested views - ERG5 invents no new downstream-eligibility vocabulary.

## 32. Test-quality verdict

Checked against the mission's own §32 letter matrix (A-X) - every letter has at least one dedicated test, cross-referenced in this report's own section numbering. Checked against common anti-patterns: no count-only assertions without content verification; the stale-basis test asserts the CURRENT id appears in the refusal message, not just that it fails; the cross-candidate test uses two genuinely distinct candidates with distinct assessment ids; the no-autoflush tests assert the pending object remains genuinely pending (`in session.new`), not just "no exception raised"; identity-vs-relevance separation is tested via an actual `--execute` attempt, not merely by inspecting field names.

## 33. Defects found

None in the pre-existing ERG1-4/UAC1/UAC4/UAC-H1 code. Two test-authoring mistakes were found and fixed during this mission's own test-writing (not production defects): an initial no-autoflush test incorrectly expected a genuinely successful relevance-review write to succeed despite an unrelated invalid pending object (the write's own intentional flush legitimately surfaces that violation, per the already-established UAC-H1 boundary); an initial import-check test substring-matched a service module's own docstring instead of parsing its actual `import` statements.

## 34. Corrections made

Both fixed before this report was written - see §33. No production code required correction.

## 35. Regression tests added

37 new tests: 31 in `tests/test_review_unknown_airport_candidate.py` (`TestErg5GovernanceViewInspect` x14, `TestErg5RelevanceReviewRecording` x4, `TestErg5SameBasisMultiReview` x1, `TestErg5IdentityVsRelevanceSeparation` x2, `TestErg5SchemaAbsent` x3, `TestErg5NoAutoflush` x2, `TestErg5SignalInstallationFirewall` x2, `TestErg5DirectGovernanceReuse` x2, plus one mechanical fix to a pre-existing test), 6 in the new `tests/test_unknown_airport_candidate_governance_view.py`.

## 36. Focused tests

`test_emas_relevance_evaluation.py` + `test_unknown_airport_candidate_relevance_persistence.py` + `test_unknown_airport_candidate_relevance_review_persistence.py` + `test_unknown_airport_candidate_relevance_review_migration.py` + `test_unknown_airport_candidate_resolution.py` + `test_review_unknown_airport_candidate.py` + `test_unknown_airport_candidate_admission_eligibility.py` + `test_unknown_airport_candidate_governance_view.py` + `test_unknown_airport_candidate_persistence.py` + `test_unknown_airport_candidate_migration.py` + `test_model_contract.py` + `test_effective_identity_guard_decision.py` + `test_resolved_candidate_evidence_reevaluation.py`: **673 passed, 0 failed**.

## 37. Full pytest

**3623 passed, 0 failed**, 4542 warnings (pre-existing deprecation/pytest-cache warnings only), 470.62s. Baseline (post-UAC-H1) was 3586 - the +37 delta is exactly this slice's own new tests (31 + 6).

## 38. py_compile

Clean across all four touched/created files.

## 39. git diff --check

Clean, exit code 0.

## 40. Real DB before/after

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened `data/runway_safe.db` at all - all fixtures, including the schema-absent reproduction, use isolated `tmp_path` SQLite files.

## 41. Files touched

Created: `app/services/unknown_airport_candidate_governance_view.py`, `tests/test_unknown_airport_candidate_governance_view.py`, this report. Modified: `scripts/review_unknown_airport_candidate.py` (new imports, `check_erg_schema_readiness()`, new config/result fields, `_run_relevance_review_write()`, `_read_candidate_state()` gained the governance-view composition and an `erg_schema_ready` parameter, `_validate_config()`/`_parser()`/`main()` gained the new mode, `render_result()` gained new sections), `tests/test_review_unknown_airport_candidate.py`. No model, migration, or schema changes anywhere.

## 42. git status

Confirmed: exactly the files in §41 show as modified/new; every other untracked file in the working tree is a pre-existing, unrelated leftover from earlier missions (same set confirmed at every prior mission's own starting-state check in this session).

## 43. READY_FOR_ERG5_REVIEW_CHECKPOINT

**YES** - the operator governance flow is implemented additively (zero regression to any pre-existing CLI mode, zero schema change, zero new admission logic - every relevance/admission fact is read directly off ERG2/ERG3/ERG4's own already-governed helpers), the mission's full A-X test matrix is covered with dedicated tests, the identity/relevance/admission distinction is explicit both in the data contract and the rendered text, the real database's own current (ERG2/ERG3-absent) state was independently discovered and specifically designed for rather than assumed, and the Signal/Installation/UAC5B firewall holds throughout.

## 44. READY_FOR_OPERATIONAL_READINESS_REVIEW

**NOT YET** - this mission's own governing instruction requires a separate, explicitly-authorized ERG5 adversarial review checkpoint first (mirroring ERG1-4/UAC-H1's own established pattern) before any commit/push, and this implementation has not yet been exercised against the real, currently-unmigrated production database at all (by design - no real DB migration/write was in scope here). Operational readiness additionally requires a decision on when ERG2/ERG3's own migrations will actually be run against `data/runway_safe.db`, which remains entirely outside this mission's scope.

## 45. Exact recommended next mission

A separate, explicitly-authorized ERG5 adversarial review checkpoint (mirroring ERG1-4/UAC-H1's own pattern) - independently re-deriving the mode-separation validation (especially the new `--relevance-decision` exclusivity rules), re-attacking the schema-absent fixture and no-autoflush claims with fresh eyes, and re-confirming the rendered-text wording genuinely cannot mislead an operator into believing ELIGIBLE means "already admitted" - before this slice is committed. Only after that review authorizes a commit should the actual ERG2/ERG3 migration against `data/runway_safe.db` be considered as a separate, explicitly-authorized operational mission.

RWI_ERG5_OPERATOR_GOVERNANCE_FLOW_IMPLEMENTATION_COMPLETE

---

## Adversarial Review Findings (independent review/commit/push mission)

Governing instruction: "DO NOT TRUST THE IMPLEMENTATION REPORT. Independently attack the operator contract, governance-helper composition, schema-absent behavior, write gates and misleading-output risks." Every claim below was independently re-derived against the running code.

**§1 Starting state.** Confirmed fresh: HEAD `4531c60... == origin/main`; working tree scope matched expectations exactly. Real DB SHA-256 `126f3161...`, 2,097,152 bytes, `FK=[]`, `integrity=ok` - unchanged. **Schema reality independently re-confirmed via direct PRAGMA/sqlite_master inspection** (not trusted from the report): `unknown_airport_candidates`/`unknown_airport_candidate_reviews` present; `unknown_airport_candidate_relevance_assessments`, its evidence-link table, and `unknown_airport_candidate_relevance_reviews` all absent.

**§3 Governance-view architecture (HIGH PRIORITY).** Read the service fresh. Confirmed read-only (no `session.add`/`flush`/`commit` anywhere) and composition-only for four of five derived facts. **One genuine, LOW-severity structural finding**: `is_canonical_admission_relevant` on `AutomaticRelevanceView` was computed via an inline `assessment.is_inventory_relevant or assessment.is_watch_worthy` even though `evaluate_unknown_airport_candidate_admission_eligibility()` (called two lines later in the same function) already exposes the identical value as `is_automatic_admission_relevant`. Values could never actually diverge (both are the same one-line OR of the same immutable assessment row, and this exact formula is ERG1.6's own permanently locked convention, already duplicated by design in `UnknownAirportCandidateRelevanceAssessmentResult.is_canonical_admission_relevant`) - so this was never a behavioral risk, but it contradicted the module's own docstring claim ("never re-derives... is_canonical_admission_relevant") and was the one place the letter of "must not independently decide the admission OR rule" was violated. **Fixed**: reordered to call ERG4's evaluator first and reuse `admission.is_automatic_admission_relevant` verbatim. Re-ran the full governance-view and CLI suites after the fix - zero test changes required, confirming the value was always identical (85 + 6 governance-view tests, all still passing).

**§4 Output-contract misinterpretation attack.** Live-rendered the Anoka auto-negative + human-CONFIRM case end-to-end and inspected the literal text: automatic relevance shows `is_inventory_relevant: False` / `is_watch_worthy: False` with no adjacent Signal/Installation wording anywhere; human review shows `CURRENT ... action=CONFIRM_EMAS_RELEVANT` verbatim; canonical admission shows `BLOCKED (reason=AUTOMATIC_RELEVANCE_NOT_ADMISSION_ELIGIBLE)` plus an unconditional (shown on BOTH eligible and blocked outcomes, confirmed by reading the render code) explanatory note distinguishing ELIGIBLE from "already created." No `signal_eligible`-shaped field exists anywhere (independently re-confirmed via `hasattr`). No wording change was needed - the existing three-section split with the raw ERG4 reason string already prevents every confusion the mission names.

**§5-§11 Anoka/unreviewed/stale/dormant/watch-only worked examples.** All re-derived directly against fresh in-memory fixtures and the real CLI `render_result()` output (not merely re-read from the report): negative-view, CONFIRM-but-auto-negative, auto-positive+UNREVIEWED, stale-CONFIRM (latest assessment correctly shown as the newer one, STALE qualifier present, old review's own real fields never hidden), dormant-inventory-only (`inventory=True/watch=False/ELIGIBLE`), watch-only (`inventory=False/watch=True/ELIGIBLE`) - every case matches the mission's own worked expectations exactly.

**§9 Same-basis multi-review, second direction - gap found and closed.** The implementation's own test suite covered DEFER→CONFIRM but NOT the mission's own explicitly-named reverse direction (CONFIRM→MARK_NOT, same basis). Added `test_confirm_then_mark_not_same_basis_shows_current_mark_not`, confirming `state=CURRENT`, `action=MARK_NOT_EMAS_RELEVANT`, `canonical_admission.eligible=False/HUMAN_REVIEW_MARKED_NOT_RELEVANT` - a human reversing an earlier CONFIRM correctly flips admission back to BLOCKED.

**§12 Identity vs relevance separation.** Re-derived directly: a candidate with `canonical_admission.eligible=True` and identity review DEFER still correctly fails `--execute` with UAC4's own `StaleReviewError`/DEFER-refusal message - the two gates never merge into one boolean, confirmed both via the CLI result's own separate fields and via an actual attempted execution.

**§13 Evidence-traceability history attack.** Independently reproduced end-to-end: assessment #1 linked only to SourceAssertion A; candidate later gains SourceAssertion B (unrelated to #1); re-querying the view while #1 is still latest shows `linked_source_assertion_ids=(A,)` only - never `(A, B)`. A later assessment #2 explicitly linked to A+B correctly shows both. Confirmed the evidence-link query is genuinely scoped by `assessment_id`, not a dynamic "all evidence this candidate currently has" join.

**§14 Contradiction display.** Confirmed directly: a CONTRADICTING-polarity observation alongside a POSITIVE one on the same assessment produces a non-empty `contradicting_evidence_classes` tuple that remains visible in both the structured result and rendered text even when the human's current review is CONFIRM - never hidden by a positive human decision.

**§15 Evaluator version.** Confirmed the persisted `evaluator_version` column value is read and displayed verbatim, with zero evaluator re-invocation anywhere in the governance-view module (structurally confirmed: no import of `evaluate_emas_relevance` or `EVALUATOR_VERSION`).

**§16 No-assessment state.** Confirmed clean: `automatic_relevance=None`, `human_relevance_review.state="NO_ASSESSMENT_YET"`, `canonical_admission.eligible=False/NO_RELEVANCE_ASSESSMENT`, zero exceptions.

**§17-§18 Schema-absent / partial / malformed schema (HIGH PRIORITY).** Independently reproduced all four named partial/malformed shapes via fresh, isolated fixtures NOT present in the original implementation's own test suite (a genuine coverage gap, now closed): (A) ERG2 present/ERG3 absent, (B) ERG3 present/ERG2 absent, (C) ERG2 present but missing an expected column, (D) ERG3 present but missing an expected column. All four correctly report `ready=False` via `check_erg_schema_readiness()`, never misreport partial readiness as full readiness, and never repair anything. End-to-end through `run_review()`: a partial schema still cleanly hard-blocks `--relevance-decision` with `ERG_RELEVANCE_SCHEMA_MIGRATION_REQUIRED` (no `OperationalError` leakage) and plain inspect still leaves ALL THREE governance fields `None` (never attempts a misleading ERG2-only partial view) - confirmed the gating is on the COMBINED `erg2_ready AND erg3_ready`, not either table individually. 5 new permanent regression tests added (`TestErg5PartialOrMalformedSchemaAttack`).

**§19-§23 Relevance-review recording / stale-basis / cross-candidate / dry-run / write-gate.** Re-derived directly: `record_unknown_airport_candidate_relevance_review()` is called with zero CLI-side pre-validation of same-candidate/current-basis/stale/action-vocabulary rules - the governed function's own `ValueError` is the sole authority, confirmed by reading `_run_relevance_review_write()`'s own body (no logic between the call and its `except ValueError` clause). Stale-basis refusal message independently confirmed to name the CURRENT assessment id. Dry-run confirmed to leave the `unknown_airport_candidate_relevance_reviews` table at zero rows via direct table count (not just checking the result flag); authorized write confirmed to append exactly one row. Write-gate discipline (`--allow-database-write` required, dry-run always calls the real governed function against a writable session and only differs in commit-vs-rollback) reconstructed independently from source and matches the pre-existing identity-review mode's own convention exactly - no weaker new path.

**§24 No-autoflush.** Re-attacked at all three layers named in the mission (governance view directly, CLI inspect path, relevance-review-write precondition phase) with all three shapes (unrelated pending candidate object, unrelated pending review object, expired candidate attributes) - all pass. The precondition-phase test correctly attacks an INVALID basis (not a genuinely successful write) to isolate the precondition-check phase from the function's own intentional write flush, which legitimately still validates any unrelated invalid pending object once a real write actually happens (the same "no swallowing of real write errors" boundary UAC-H1 already established, correctly not weakened here).

**§25 Error honesty.** Confirmed via direct grep: zero `except Exception`/bare `except:` anywhere in either touched file. All four `except` clauses in the CLI name specific exception types (`ValueError`, or explicit tuples of the UAC4 exception classes).

**§26 Old CLI non-regression.** All 48 pre-existing CLI tests (inspect/DEFER/REJECT/MATCH/CREATE/dry-run/write-gate) still pass unchanged.

**§27 Canonical-create authority.** Confirmed structurally: `--execute --new-airport-*` still calls `create_airport_from_approved_candidate()` directly (the same ERG4-gated authoritative function from the prior mission) - no `eligible=True -> insert Airport` shortcut exists anywhere in the CLI; the governance view never constructs an `Airport` row.

**§28 Signal/Installation firewall.** Re-confirmed via AST-based import inspection (not naive substring matching against the module's own docstring, which the ORIGINAL test for this incorrectly did - see Test Quality below) that the governance-view module imports no `Signal`, `Installation`, UAC3, or identity-guard symbol.

**§29 Serialization safety.** Independently verified via `dataclasses.asdict()` + `json.dumps()` on both the governance view and the full top-level CLI result - both fully JSON-serializable, and a recursive walk confirmed zero `_sa_instance_state`-bearing (ORM-attached) objects anywhere in the tree.

**§31/§32 Test quality - two genuine test-authoring defects found and fixed** (in the implementation's OWN test suite, not production code): (a) a no-autoflush test incorrectly expected a genuinely successful relevance-review write to succeed despite an unrelated invalid pending object - fixed to attack the precondition phase specifically via an invalid basis, matching the correct, already-established UAC-H1 boundary; (b) an import-firewall test substring-matched a module's own DOCSTRING text (which legitimately mentions the forbidden module names in prose explaining what's NOT imported) instead of parsing actual `import` statements via `ast` - fixed to use real AST import-node inspection. Both were fixed during the implementation mission itself, independently re-verified here as genuinely correct fixes (not re-introduced bugs).

**§32 Test-count reconciliation.** Independently re-derived: pre-ERG5 baseline was 48 CLI tests; the implementation's own claimed "+37" (31 CLI + 6 governance-view) is verified EXACTLY correct via direct `pytest --collect-only` counts (48+31=79, +6=85 CLI tests before this review's own additions) - unlike ERG4's prior mission, no arithmetic error was found in ERG5's own report.

**Verdict: SOUND**, with one production-code correction (the `is_canonical_admission_relevant` reuse fix, LOW severity, zero behavioral change) and two genuine test-coverage gaps closed (CONFIRM→MARK_NOT same-basis reverse direction; the four partial/malformed-schema shapes) - 6 new regression tests added during this review, on top of the implementation's own 37. Authorized for commit and push.

RWI_ERG5_OPERATOR_GOVERNANCE_FLOW_REVIEWED_COMMITTED_AND_PUSHED
