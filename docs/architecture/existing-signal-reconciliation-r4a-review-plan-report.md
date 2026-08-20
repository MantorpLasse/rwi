# Existing-Signal Reconciliation — R4A Review-Plan/Fingerprint Report

Implements R4A of `docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md`'s
own S20 roadmap (commit `fa53785c58692334dbee764ab3c0b0ecb001fced`): the pure,
deterministic `ReconciliationReviewPlan`/fingerprint contract that will bind a future
human `CONFIRM_DISTINCT_SIGNAL` decision to the exact blocking reconciliation state
reviewed. No `ReviewerAction` change, no schema/migration, no R1/R2/R3 modification,
no real DB write.

## 1. Starting HEAD and origin/main verification

`fa53785c58692334dbee764ab3c0b0ecb001fced` — confirmed matching `origin/main` before
starting. Baseline full-suite, run fresh before any change: **1402 passed**.

## 2. Files read fresh before implementation

`docs/architecture/existing-signal-reconciliation-r4-human-resolution-design.md`
(full), `app/services/existing_signal_reconciliation.py` (full — the exact
`ExistingSignalReconciliationSubject`/`ExistingSignalReconciliationDecision` field
shapes), and confirmed via `git status`/prior-session knowledge that
`app/services/existing_signal_reconciliation_candidates.py`,
`app/services/governed_signal_creation.py`, `app/services/reviewer_action_persistence.py`,
and `app/models/reviewer_action.py` remain byte-identical to their last-reviewed
state (no local modifications pending).

## 3. Exact files changed/created

- `app/services/existing_signal_reconciliation_review.py` (new)
- `tests/test_existing_signal_reconciliation_review_plan.py` (new)
- `docs/architecture/existing-signal-reconciliation-r4a-review-plan-report.md` (new,
  this file)

No other file was modified. `git status` confirms `app/services/existing_signal_reconciliation.py`,
`app/services/existing_signal_reconciliation_candidates.py`,
`app/services/governed_signal_creation.py`, `app/models/reviewer_action.py`, and
`app/services/reviewer_action_persistence.py` all show as unchanged (not listed).

**Placement decision, made fresh rather than inherited from the design doc's own
suggestion**: a new, separate module (`existing_signal_reconciliation_review.py`),
not added to R1's own file. R1 has been independently reviewed and hardened multiple
times as a single, narrow, already-closed contract, and every prior review checkpoint
in this project has used "R1's own file is unchanged" as a direct, zero-ambiguity
signal that reconciliation semantics have not shifted. The plan/fingerprint core
answers a genuinely different question (what did a human review, and is that review
still current?) with no anchor/compatibility/disconfirming logic of its own — mixing
it into R1 would make that "unchanged" signal meaningless for future reviewers
without changing what either module actually does. This mirrors exactly why R2's
adapter got its own file rather than being folded into R1.

## 4. Exact `ReconciliationReviewPlan` shape

```python
@dataclass(frozen=True)
class ReconciliationReviewPlan:
    reconciliation_plan_version: int
    source_assertion_id: int
    subject_airport_id: int | None
    subject_runway_id: int | None
    subject_physical_installation_ids: tuple[int, ...]
    subject_source_id: int | None
    subject_artifact_identity: str | None
    candidate_signal_ids: tuple[int, ...]
    anchor_reasons: tuple[str, ...]
```

Matches the reviewed design's own Section 6 shape exactly — no additional identity
concept was introduced. `source_assertion_id` is supplied as an explicit parameter to
`build_reconciliation_review_plan()` (not derivable from
`ExistingSignalReconciliationSubject`, which R1 confirmed fresh has no row-identity
field of its own at all).

## 5. Canonicalization rules

Applied in two places, both defensively: inside `build_reconciliation_review_plan()`
(so a plan built through the intended path is already canonical) and again inside
`compute_reconciliation_fingerprint()` (so the fingerprint is correct even for a plan
constructed any other way):

1. `subject_physical_installation_ids` — deduplicated (`set(...)`), sorted ascending.
2. `candidate_signal_ids` — deduplicated, sorted ascending.
3. `anchor_reasons` — deduplicated, sorted lexicographically.

Scalar fields (`reconciliation_plan_version`, `source_assertion_id`,
`subject_airport_id`, `subject_runway_id`, `subject_source_id`,
`subject_artifact_identity`) need no canonicalization — each is already a single
value, not a collection.

## 6. Exact serialization contract used before hashing

```python
canonical_payload = {
    "reconciliation_plan_version": plan.reconciliation_plan_version,
    "source_assertion_id": plan.source_assertion_id,
    "subject_airport_id": plan.subject_airport_id,
    "subject_runway_id": plan.subject_runway_id,
    "subject_physical_installation_ids": sorted(set(plan.subject_physical_installation_ids)),
    "subject_source_id": plan.subject_source_id,
    "subject_artifact_identity": plan.subject_artifact_identity,
    "candidate_signal_ids": sorted(set(plan.candidate_signal_ids)),
    "anchor_reasons": sorted(set(plan.anchor_reasons)),
}
canonical_json = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
```

Explicit field names, explicit dict (not `dataclasses.asdict()` alone relied upon for
ordering — `sort_keys=True` makes key order irrelevant regardless, but the dict is
still built with named keys for readability and to guarantee every field is
accounted for). Every value is already a plain JSON-native type (`int`, `str`,
`None`, `list`) — no float, `Decimal`, or `date`/`datetime` ever reaches this
function. Verified by AST inspection (not text search) that no `repr()`, `hash()`,
or non-`json` `.dumps()` call exists anywhere in the module.

## 7. Fingerprint format

`hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()` — a 64-character
lowercase hexadecimal string (Python's `hexdigest()` is always lowercase; verified
directly: `fp == fp.lower()` and `int(fp, 16)` both hold in the test suite).

## 8. Fields that affect the fingerprint

Every field on `ReconciliationReviewPlan` (Section 4) — proven independently, not as
one generic "something changed" test:

- Subject airport identity — implicitly covered (a differing `subject_airport_id`
  changes the payload); not independently isolated as its own test since R1 itself
  already treats airport as a hard disqualifier upstream of this module (a differing
  airport means the candidate would never reach `POSSIBLE_EXISTING_SIGNAL_MATCH` in
  the first place), but the field's presence in the hashed payload is structural and
  verified by the serialization contract test.
- Subject runway identity — `test_lost_anchor_axis_changes_fingerprint`-adjacent
  coverage; the runway-anchor scenarios throughout the suite exercise this directly.
- Subject physical-installation identity — `test_lost_anchor_axis_changes_fingerprint`
  (an installation id no longer shared removes the anchor entirely, correctly
  producing `CLEAR_TO_CREATE`, i.e. no plan at all — the strongest possible proof
  that this field is load-bearing).
- Subject governed provenance identity (`subject_source_id`) —
  `TestCandidateReasonAssociationNeverPooled.test_swapped_anchor_types_between_two_candidates_differ`
  exercises provenance-anchor presence/absence directly.
- Blocking candidate added — `test_candidate_added_changes_fingerprint`.
- Blocking candidate removed — `test_candidate_removed_changes_fingerprint`.
- Blocking candidate Signal ID changed — `test_candidate_signal_id_changed_changes_fingerprint`.
- Structural anchor reason added — `test_additional_anchor_axis_on_same_candidate_changes_fingerprint`.
- Structural anchor reason removed/changed — `test_lost_anchor_axis_changes_fingerprint`
  (removal), `test_swapped_anchor_types_between_two_candidates_differ` (changed/reassigned).

## 9. Fields structurally excluded from it

`category`, `vendor_names`, `evidence_date`, `reference_year` (all compatibility-only
on `ExistingSignalReconciliationSubject`, never anchor-bearing) and
`advisory_candidate_signal_ids`/`advisory_reasons` (on
`ExistingSignalReconciliationDecision`) are never read by
`build_reconciliation_review_plan()` at all — not merely ignored, structurally absent
from the resulting plan. Also structurally absent from `ReconciliationReviewPlan`
itself, verified by field-name inspection: any financial/title/notes/text/confidence/
similarity-shaped field name. Proven behaviorally in Section 12 below.

## 10. Complete-blocking-set result

**Confirmed.** `(candidate_signal_ids=(67,))` and `(candidate_signal_ids=(67, 91))`
never fingerprint the same, even when Signal 67's own anchor reason is byte-identical
in both scenarios (`test_same_reasons_different_candidate_set_never_collide`) — the
candidate-set membership itself, not just the reasons, is part of the hashed payload.
`build_reconciliation_review_plan()` also fails closed (raises `ValueError`) if any
candidate in `candidate_signal_ids` has zero reasons attributed to it
(`test_candidate_with_no_attributed_reason_raises`) — a malformed decision can never
silently produce a plan that looks like it covers a candidate it doesn't actually
have evidence for.

## 11. Candidate/reason association result

**Confirmed never pooled.** `(Signal 67 → runway anchor, Signal 91 → provenance
anchor)` and `(Signal 67 → provenance anchor, Signal 91 → runway anchor)` produce
different fingerprints (`test_swapped_anchor_types_between_two_candidates_differ`).
This holds specifically *because* R1's own reason strings are self-describing
(always prefixed `"signal {id}: ..."`) — flattening them into one sorted tuple does
not lose the association, since the association is encoded in the string itself, not
in positional structure. `build_reconciliation_review_plan()` verifies this
precondition explicitly at construction time (every candidate id must have at least
one reason carrying its own `"signal {id}: "` prefix) rather than silently assuming
R1's string convention holds forever — if it were ever to change, plan construction
would fail closed rather than silently producing an association-losing fingerprint.

## 12. Advisory-churn result

**Confirmed the fingerprint is unaffected.** Three independent proofs:
`test_fingerprint_unchanged_when_only_advisory_evidence_changes` (subject's
compatibility-only fields — `category`, `vendor_names` — changed entirely between two
otherwise-identical scenarios; fingerprint identical); `test_extra_advisory_only_candidate_appearing_does_not_change_fingerprint`
(an additional compatibility-only candidate appears alongside the blocking one;
fingerprint identical to the blocking-candidate-alone case); `test_advisory_only_candidate_never_enters_the_plan`
(a mixed blocking+advisory-only candidate set produces a plan whose
`candidate_signal_ids` contains only the blocking one). This matches the reviewed
design's own Section 16 conclusion precisely.

## 13. `CLEAR_TO_CREATE` failure result

**Confirmed fails closed.** `test_clear_to_create_raises` and
`test_bare_clear_to_create_no_candidates_raises` both assert `ValueError` (message
naming `POSSIBLE_EXISTING_SIGNAL_MATCH`) rather than a silently-empty plan.

## 14. `ALREADY_LINKED` failure result

**Confirmed fails closed.** `test_already_linked_raises` — same `ValueError`
behavior.

## 15. Malformed-decision failure result

**Confirmed.** `test_empty_candidate_signal_ids_raises`, `test_empty_reasons_raises`,
and `test_candidate_with_no_attributed_reason_raises` all construct a synthetic,
deliberately malformed `ExistingSignalReconciliationDecision` (outcome
`POSSIBLE_EXISTING_SIGNAL_MATCH` but missing/inconsistent candidate or reason data)
directly, bypassing R1's own evaluation function entirely — proving
`build_reconciliation_review_plan()` itself validates its input rather than trusting
that R1 could never hand it something malformed.

## 16. MSP historical/current regression results

Both confirmed to **not** produce a blocking plan, using the real MSP field shapes
(airport 45, category "replacement", vendor "Runway Safe", the real #67/#41 shapes)
as regression fixtures only — R1/R2 semantics were not touched or reinterpreted to
reach this result:

- **Pre-resolution shape** (`test_pre_resolution_msp_shape_never_produces_a_blocking_plan`):
  `evaluate_existing_signal_reconciliation()` returns `CLEAR_TO_CREATE` with
  `advisory_candidate_signal_ids == {41, 67}` — exactly R1's own already-proven
  golden-case result — and `build_reconciliation_review_plan()` correctly raises
  `ValueError` when handed that decision.
- **Current (`ALREADY_LINKED`) shape** (`test_current_msp_already_linked_never_produces_a_blocking_plan`):
  same raise.

A **synthetic** MSP-shaped case (`test_synthetic_msp_shaped_anchor_backed_case_produces_a_blocking_plan`)
— identical airport/category/vendor context, but with a genuine shared canonical
`runway_id` deliberately added (never present in the real, unresolved data) — proves
the blocking path is reachable end-to-end without fabricating an anchor for the real
case.

## 17. Synthetic anchor-backed positive result

Confirmed via the synthetic MSP-shaped case above and the module-wide
`_single_runway_anchor_scenario()` fixture used throughout the suite: a genuine
`POSSIBLE_EXISTING_SIGNAL_MATCH` produces a valid plan and a 64-character fingerprint
on the first attempt.

## 18. Determinism/permutation result

**Confirmed across every dimension the mission named**: candidate order (A),
reason order within one candidate — including reasons supplied in explicitly reversed
order via `dataclasses.replace()`, independent of R1's own construction order (B),
duplicate candidate rows (C), duplicate reasons (D), physical-installation-id order
and duplicates (E), a large (20-candidate) set under both simple reversal and five
random shuffles (`random.Random(1234)`, R), and all `3! = 6` permutations of a
3-candidate set collapsing to exactly one fingerprint via a set-comprehension proof
(`TestLargeCandidateSetDeterminism.test_all_permutations_of_three_candidates_identical`).
Repeated calls against identical input produce `==`-equal plans and byte-identical
fingerprints (`TestDeterminismAcrossRepeatedCalls`); inputs (the R1 decision/subject)
are unchanged after plan/fingerprint construction.

## 19. Structural-firewall result

**Confirmed.** No field name on `ReconciliationReviewPlan` contains any
financial/title/notes/text/confidence/similarity-shaped token
(`test_no_financial_title_or_raw_text_fields_on_plan`); constructing one with a
`title=` keyword raises `TypeError` (`test_cannot_construct_plan_with_financial_or_title_kwarg`);
the module's own source contains no `$`, no specific dollar figure, and no
MAC/MSP/FAA/Runway Safe/USAspending/Granicus token
(`test_module_source_never_mentions_dollar_figures_or_provider_names`); no import of
`app.acquisition`, `sqlalchemy`, or any ORM module
(`test_module_never_imports_acquisition_or_orm`); no dependency, textual or
otherwise, on any human-decision-recording concept
(`test_no_reviewer_action_import`, satisfied after a docstring-wording correction —
Section 21).

## 20. Purity/no-I/O result

**Confirmed.** AST-verified: no forbidden module import (`sqlalchemy`, `httpx`,
`requests`, `app.database`, `app.models`, `app.acquisition`); no `random`/`uuid`
import; no `today()`/`now()`/`utcnow()` call; no `open()`/`socket()` call; no
`repr()`/`hash()` call and no non-`json` `.dumps()` call anywhere in the module.
`ReconciliationReviewPlan` is frozen (`__dataclass_params__.frozen is True`) and
raises `dataclasses.FrozenInstanceError` on any post-construction attribute
assignment.

## 21. Defects discovered and exact corrections

**Implementation pass (this section as originally written)**: no production defect
was found in R1, R2, or R3 — all three were read fresh and used entirely unmodified;
every real-DB-shaped and synthetic scenario attacked produced the structurally
correct result on the first implementation pass. Two test-authoring corrections, the
same class of self-referential-docstring mistake already learned and fixed once each
during the R1, R2, and R3 review checkpoints, were caught and fixed during this
slice's own first test run (generic docstring wording; AST-based checks for
`repr()`/`hash()`/non-`json` `.dumps()` calls in place of whole-text scans).

**Review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R4A_REVIEW_PLAN_REVIEW_COMMIT_PUSH)
— one real R4A-scope defect found and fixed**, per Phase 4's own explicit "reason
referring to a candidate not in candidate_signal_ids" attack:

**Defect**: `build_reconciliation_review_plan()`'s original validation checked only
the forward direction — every declared `candidate_signal_ids` entry must have at
least one matching reason — but never the reciprocal: that every reason in
`decision.reasons` is itself attributed to a *declared* candidate. Constructing
`ExistingSignalReconciliationDecision(candidate_signal_ids=(1,), reasons=("signal 1:
...", "signal 2: ..."))` directly (bypassing R1's own evaluation function, exactly as
Phase 4 instructs) produced a *valid* plan whose `anchor_reasons` silently included
the orphaned signal-2 reason — evidence for a candidate no human reviewing
`candidate_signal_ids=(1,)` would ever see, yet the fingerprint depended on it. This
is not reachable through R1's own real construction today (its `reasons` tuple is
built strictly by iterating `candidate_signal_ids`, so it can never itself emit an
orphaned reason) — but R4A's own stated design philosophy is to validate its input
explicitly rather than assume that invariant holds forever, and this gap directly
contradicted that: a plan is supposed to represent *exactly* what a human is shown,
and this defect meant it could, under a malformed or future-drifted decision, silently
represent more than that.

**Fix**: added the reciprocal check — every reason in `decision.reasons` must start
with the `"signal {id}: "` prefix of at least one *declared* candidate in
`candidate_signal_ids`; otherwise `build_reconciliation_review_plan()` raises
`ValueError` naming the orphaned reason. No change to the forward check, no change to
canonicalization, no change to the fingerprint function itself, no change to R1/R2/R3.

**Regression tests added**: `test_reason_attributed_to_a_non_blocking_candidate_raises`
(the minimal reproduction — single declared candidate, one orphaned reason),
`test_reason_referring_to_unrelated_candidate_with_otherwise_valid_set_raises` (a
fully-valid multi-candidate set with one *additional* orphaned reason, proving the new
check fires even when the forward check alone would have passed).

**Three further review-checkpoint additions, not defects but genuine coverage gaps
identified by Phase 3's explicit item list** (H, M, N) that the original 46 tests
exercised only indirectly via the serialization-contract test, never with a direct
before/after fingerprint comparison: `TestScalarIdentityFieldsAffectFingerprint`
(4 tests — `source_assertion_id` change, plan-version change, subject-airport change,
subject-`artifact_identity` change, each independently proven to change the
fingerprint). Also added `test_numeric_prefix_collision_between_candidate_ids_does_not_false_positive`
(candidates 1, 10, and 11, each anchored via a different family) — an adversarial
check that the `"signal {id}: "` prefix delimiter genuinely prevents numeric-prefix
collisions (e.g. `"signal 1: "` never matching a reason actually belonging to
`"signal 11: ..."`), confirmed correct by construction (the colon-space delimiter
immediately follows the number) but not previously exercised by a dedicated test.

**Total new regression tests from this checkpoint**: 7 (46 → 53).

## 22. Focused test result

Re-run at the review checkpoint, after the fix and the 7 new tests:

```
tests/test_existing_signal_reconciliation_review_plan.py     53 passed (46 original + 7 at review checkpoint)
tests/test_existing_signal_reconciliation.py                 (unchanged, R1)
tests/test_existing_signal_reconciliation_candidates.py      (unchanged, R2)
tests/test_governed_signal_creation.py                       (unchanged, R3)
tests/test_governed_signal_creation_reconciliation.py        (unchanged, R3)
tests/test_reviewer_action_persistence.py                    (unchanged)
tests/test_reviewer_action_migration.py                      (unchanged)
tests/test_human_review_queue.py                              (unchanged)
```
Combined: **402 passed**, 0 failed.

## 23. Full pytest result

**1455 passed** (1448 pre-review + 7 new at the review checkpoint), 0 failed.

## 24. `py_compile` result

`python -m py_compile app/services/existing_signal_reconciliation_review.py
tests/test_existing_signal_reconciliation_review_plan.py` — clean, no output.

## 25. `git diff --check` result

Clean (exit 0).

## 26. Real DB unchanged/no-access result

No real database access was performed or required anywhere in this slice — every
scenario, including the MSP regression fixtures, is built entirely from synthetic
in-memory dataclass construction (`ExistingSignalReconciliationSubject`/
`ReconciliationCandidateSignal`), never a live session or the real
`data/runway_safe.db` file. Nothing to hash before/after.

## 27. `git status`

```
?? app/services/existing_signal_reconciliation_review.py
?? tests/test_existing_signal_reconciliation_review_plan.py
?? docs/architecture/existing-signal-reconciliation-r4a-review-plan-report.md
```
plus the same set of pre-existing, unrelated untracked paths present since before
this task began.

## 28. Review checkpoint summary (2026-08-20, same HEAD)

The dedicated adversarial review checkpoint
(RWI_EXISTING_SIGNAL_RECONCILIATION_R4A_REVIEW_PLAN_REVIEW_COMMIT_PUSH) re-read all
three R4A files fresh, independently re-derived the design doc's own field/
canonicalization/serialization requirements, and attacked the module directly
(bypassing R1's own evaluation function to construct malformed decisions, exactly as
its own mission instructed) rather than trusting the 46 pre-existing tests as proof.
It found **one real R4A-scope defect** (Section 21 — an orphaned-reason attribution
gap in the malformed-decision validation), fixed it narrowly (one reciprocal check
added, nothing else touched), and added 7 permanent regression tests total — 2 for
the defect itself, 4 for previously-untested scalar identity fields
(`source_assertion_id`, plan version, subject airport, subject `artifact_identity`),
and 1 for a numeric-prefix-collision edge case confirmed safe by construction but not
previously exercised. R1, R2, R3, and `ReviewerAction` were re-confirmed unmodified
throughout. Full suite after the fix: 1455 passed (1448 + 7).

## 29. READY_FOR_R4B

**Yes**, following this checkpoint's fix and expanded regression coverage.

## 30. Exact recommended next step

R4B — the `ReviewerAction` schema/persistence extension (design doc Section 20): add
`CONFIRM_DISTINCT_SIGNAL` to `REVIEWER_ACTIONS` and its CHECK constraint, add the
nullable `reconciliation_fingerprint` column with its own paired CHECK constraints
(mirroring `duplicate_of_signal_id`'s exact pattern), a real migration, and
`record_reviewer_action()` validation for the new action mirroring `MARK_DUPLICATE`'s
own. No R3 change yet in that slice — R4C (the actual governed-creation integration)
remains a separate, later step per the design doc's own sequencing.
