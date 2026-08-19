# Existing-Signal Reconciliation — R3 Governed-Creation Integration Report

Implements R3 of `docs/architecture/existing-signal-reconciliation-guard-design.md`'s
own S16 roadmap (commit `6e5b46ee769af38219c9fae6fc7f9976d21d3161`): wires the
already-reviewed, already-committed R1 pure core and R2 read-only adapter into
`create_signal_from_approved_review()` as a mandatory pre-creation gate. No R1/R2
logic was reimplemented or modified; no schema change; no migration; no
ReviewerAction vocabulary change; no queue/UI change; no real DB write.

## 1. Starting HEAD

`6e5b46ee769af38219c9fae6fc7f9976d21d3161` — confirmed matching `origin/main` before
starting. Baseline full-suite: 1359 passed.

## 2. Integration point

Inside `create_signal_from_approved_review()`, exactly where the reviewed design
(S12) specified: after `_validate_human_selected_fields()`, `_check_governance_gates()`,
and the `runway_id`-belongs-to-airport check, but before the pre-existing
`if source_assertion.signal_id is not None:` branch is reached. One new block:

```python
reference_year = _resolve_reference_year(
    target_year=target_year, planning_year=planning_year, procurement_year=procurement_year,
    construction_start=construction_start, completion_date=completion_date,
)
reconciliation_subject = build_reconciliation_subject(
    source_assertion, claims, category=category, reference_year=reference_year,
)
reconciliation_candidates = find_reconciliation_candidates(session, source_assertion)
reconciliation_decision = evaluate_existing_signal_reconciliation(
    reconciliation_subject, reconciliation_candidates,
)
if reconciliation_decision.outcome == ExistingSignalReconciliationOutcome.POSSIBLE_EXISTING_SIGNAL_MATCH:
    raise ExistingSignalPossibleMatchError(reconciliation_decision)
```

No anchor rule, compatibility rule, latest-installation-link-supersession logic,
candidate-discovery SQL, or disconfirming-evidence rule was copied or reimplemented
— `build_reconciliation_subject`, `find_reconciliation_candidates`, and
`evaluate_existing_signal_reconciliation` are called exactly as R1/R2 already commit
them, unmodified (confirmed by `git status`, Section 27).

## 3. Reconciliation execution order

`_validate_human_selected_fields` → `_check_governance_gates` (identity/intelligence/
promotion/airport_id/latest-ReviewerAction) → `runway_id`-belongs-to-airport check →
**reconciliation gate (new)** → pre-existing `signal_id is not None` idempotent-reuse
branch → `Signal(...)` construction. Every existing governance failure still raises
before reconciliation is ever evaluated — verified directly (Section 10;
`TestGovernanceGatesFirst`), including the adversarial case the mission specifically
calls out: an *anchor-backed* existing Signal combined with an *invalid* approval
state raises the pre-existing governance `ValueError`, never
`ExistingSignalPossibleMatchError`.

**Key structural finding**: R1's own `ALREADY_LINKED` short-circuit fires
unconditionally whenever `subject.existing_signal_id` (← `source_assertion.signal_id`)
is set, before it ever inspects candidates. This means `POSSIBLE_EXISTING_SIGNAL_MATCH`
is *structurally unreachable* whenever `source_assertion.signal_id` is already set —
so the new reconciliation call can be inserted **once, unconditionally**, immediately
before the pre-existing idempotent-reuse branch, with no special-casing needed for
`ALREADY_LINKED` at all: the branch that already existed already does the right thing,
exactly as the design doc's own S12 pseudocode states ("redundant with that check by
construction, not a new behavior").

## 4. category/reference_year mapping

`category`: the create call's own human-selected `category` argument, passed
verbatim into `build_reconciliation_subject(..., category=category)` — it is
literally the value about to be written onto the proposed Signal, so comparing it
against existing candidates' own `category` is exactly the right question.

`reference_year`: resolved from the create call's own `target_year`, `planning_year`,
`procurement_year`, `construction_start`, `completion_date` arguments via a new
private helper, `_resolve_reference_year()`, applying the **same priority order**
R2 already established for reading an *existing* Signal
(`target_year` → `planning_year` → `procurement_year` → `construction_start.year` →
`completion_date.year`, first non-null wins; `manual_year_estimate` excluded).
Deliberately reimplemented, not imported (Section 27's own reasoning), with a
dedicated consistency test (`test_resolve_reference_year_matches_r2s_own_signal_reference_year`)
that constructs a real ORM `Signal` from the same field values and asserts R3's
helper and R2's private `_signal_reference_year()` agree, across five representative
cases including every fallback tier — guarding against the two ever silently drifting
apart without requiring R2 to be touched.

**A real, subtle finding surfaced while smoke-testing this** (Section 27): the
reconciliation subject's `runway_id` comes from `SourceAssertion.runway_id` (the
*evidence's own*, already-resolved canonical runway — R2's existing, unmodified
behavior), **not** from the `runway_id` keyword argument `create_signal_from_approved_review()`
accepts (which assigns a runway to the *proposed Signal*, a separate, downstream,
human-selected field). The two are conceptually different axes — one describes what
runway the *evidence* concerns, the other describes what runway a human is choosing
to *label the new Signal with* — and only the former is safe to treat as a
structural fact about the evidence for reconciliation purposes. This was not obvious
from the design doc's own text and was only caught by an adversarial smoke test that
initially (and incorrectly) assumed the create call's own `runway_id` parameter would
feed reconciliation.

## 5. Result/error API changes

`GovernedSignalCreationResult` gained one new, optional field:
`reconciliation_decision: Optional[ExistingSignalReconciliationDecision] = None`
— populated on every successful call from `create_signal_from_approved_review()`
(both the newly-created and the idempotent-reuse paths); left `None` for
`link_source_assertion_to_duplicate_signal()`, which the reconciliation gate does not
run for at all (a human has already recorded `MARK_DUPLICATE` by the time that
function runs — there is nothing left for a pre-creation guard to evaluate). This is
Option B from the mission's own menu ("include advisory ids/reasons directly as
optional result fields... prefer composition with `ExistingSignalReconciliationDecision`")
— composition, not a parallel/duplicated field set.

One new exception class, `ExistingSignalPossibleMatchError(ValueError)` — the single
narrowest addition needed, and the only new exception type this module now has.
Subclasses `ValueError` so any existing `except ValueError` handler still catches it
(preserving this module's own established fail-closed-via-`ValueError` convention for
every other governance failure), while carrying the full
`ExistingSignalReconciliationDecision` as a structured `.decision` attribute so
`candidate_signal_ids`/`reasons` are directly inspectable without a raw DB query or
string-parsing the exception message.

## 6. `CLEAR_TO_CREATE` behavior

Creation proceeds exactly as before this slice — no field, gate, or code path in the
pre-existing `Signal(...)` construction changed. Verified: `test_no_candidates_creates_normally`.

## 7. Advisory-only behavior

Maximum compatibility (category, vendor, temporal, year all agreeing) with zero
anchor still creates the Signal — `advisory_candidate_signal_ids`/`advisory_reasons`
travel through on the result's `reconciliation_decision` for explainability only,
never inspected as a gating condition anywhere in this function (verified directly by
reading the code: the `if` statement checks `.outcome ==
POSSIBLE_EXISTING_SIGNAL_MATCH` only, never `advisory_candidate_signal_ids`).
`TestAdvisoryOnlyPermitsCreation`, `TestCompatibilityOnlyCaseCreates` both confirm
this — the rejected "2+ compatibility categories blocks" rule is not resurrected.

## 8. `POSSIBLE_EXISTING_SIGNAL_MATCH` behavior

Fails closed: `ExistingSignalPossibleMatchError` raised before any `Signal(...)` is
constructed, before `session.add()`, before `session.flush()`, before
`source_assertion.signal_id` is touched. No candidate is auto-selected; no
`MARK_DUPLICATE` is auto-recorded; no link is auto-created — human reconciliation via
the existing, unchanged `ReviewerAction` vocabulary remains the only resolution path.
Verified structurally (`session.new` empty and `assertion.signal_id is None` after the
error, `TestPossibleMatchBlocksCreation`) and via the two required anchor-family
scenarios plus a real multi-candidate case (Section 13-15).

## 9. `ALREADY_LINKED` behavior

Unchanged existing idempotent-reuse-or-raise-on-drift logic, confirmed to still fire
correctly (`TestAlreadyLinkedReuse`): a second call for an already-linked
`SourceAssertion` reuses the existing Signal (`created=False`), and
`result.reconciliation_decision.outcome == ALREADY_LINKED` on that repeat call — R2
candidates are never required to "re-prove" the existing link (R1's own
`existing_signal_id`-first short-circuit means candidates are irrelevant here by
construction). A dangling `signal_id` (pointing at a Signal that no longer exists)
still fails closed via the pre-existing drift check, untouched by this slice.

## 10. Governance-gate ordering

Explicitly attacked with the exact adversarial combination the mission names: an
anchor-backed existing Signal (shared `runway_id`) paired with an *unapproved*
`SourceAssertion` (no `ReviewerAction` at all, or a wrong `identity_guard_decision`)
raises the pre-existing governance `ValueError` — never
`ExistingSignalPossibleMatchError` — because reconciliation is evaluated strictly
after every governance gate already passes. `TestGovernanceGatesFirst`, two tests.

## 11. MSP historical result

`TestMSPHistoricalPreResolution` reconstructs the real pre-resolution shape (real
Signal #67/#41 field values, real MAC-memo-shaped claims, the actual historically-
approved create request's own field values — `category='replacement'`,
`confidence='medium'`, `status='identified'`) via real ORM rows and the actual
`create_signal_from_approved_review()` call, not hand-built dataclasses. Result:
creation succeeds (`created=True`), `reconciliation_decision.outcome == CLEAR_TO_CREATE`,
`candidate_signal_ids == ()`, `advisory_candidate_signal_ids == {67, 41}` — exactly
R1/R2's own already-proven golden-case result, now reached through the real,
end-to-end governed-creation entry point. No anchor was fabricated merely because
history shows a human later resolved #222 → #67 — the fixture uses only structured
facts that would have existed *before* that resolution.

## 12. MSP current read-only result

Real, read-only pilot (Section 21) against the production database: `#222.signal_id
== 67`, latest `ReviewerAction` is `MARK_DUPLICATE` with `duplicate_of_signal_id=67`,
and calling `build_reconciliation_subject`/`find_reconciliation_candidates`/
`evaluate_existing_signal_reconciliation` directly (never the mutating
`create_signal_from_approved_review()` path) produces `ALREADY_LINKED(67)`. Signal
#67's own `supporting_source_ids` includes `70` (`#222`'s own `source_id`) — the same
structural provenance-anchor consequence already documented in the R2 report,
re-confirmed here read-only.

## 13. Runway-anchor result

`TestAnchorFamilies.test_runway_anchor_blocks` and the dedicated Section-15-style
smoke test: shared, populated `runway_id` between `SourceAssertion.runway_id` and an
existing Signal's own `runway_id`, same airport, no disconfirming evidence →
`ExistingSignalPossibleMatchError`, `candidate_signal_ids == (existing.id,)`, Signal
count unchanged, `source_assertion.signal_id` remains `None`.

## 14. Physical-installation result

`test_physical_installation_anchor_blocks`: a real, current
`InstallationAssertionLink` (`SAME_PHYSICAL_INSTALLATION`) on both the new subject
assertion and one of a candidate Signal's own supporting assertions, transitively
resolved by R2 exactly as committed → blocks.

## 15. Superseded-link regression

`test_superseded_physical_installation_link_no_longer_blocks` regression-tests the
R2 review-checkpoint defect through the **full R3 path**, not just R2 in isolation: a
`SAME_PHYSICAL_INSTALLATION` link later retracted via a superseding `UNRESOLVED` link
on the candidate's own supporting assertion → creation now succeeds
(`CLEAR_TO_CREATE`), confirming the R2 fix's benefit is realized end-to-end through
governed creation, not merely at the adapter layer.

## 16. Provenance anchor result

`test_provenance_anchor_blocks`: a new subject assertion citing the *exact same
source document* already governed-linked to an existing Signal (`SourceAssertion.
signal_id` on the earlier assertion, same `source_id`) → blocks.
`test_legacy_signal_source_id_alone_is_not_a_provenance_anchor` confirms the
companion negative: a candidate Signal's own legacy `Signal.source_id` column, with
*zero* governed `supporting_source_assertions`, is **not** treated as provenance —
creation succeeds. No shortcut through the legacy field, matching R2's own
already-committed, deliberate exclusion.

## 17. Multiple candidate result

`TestMultipleBlockingCandidates`: two independently anchored existing Signals (one via
`runway_id`, one via a transitive physical-installation identity) both appear in
`candidate_signal_ids`, sorted, with no candidate silently dropped and zero new
Signals created.

## 18. Financial/title/provider firewalls

**Financial**: `create_signal_from_approved_review()`'s own signature was already
verified to accept no `estimated_total_value_usd`/`estimated_emas_value_usd`
parameter (unchanged, pre-existing Slice 9C property); the reconciliation call sites
themselves were additionally checked via AST to confirm neither field's name ever
appears in the arguments passed to `build_reconciliation_subject`/
`find_reconciliation_candidates`/`evaluate_existing_signal_reconciliation`.

**Title/raw-text**: the reconciliation call sites never pass `title` or `notes`
(checked via AST source-segment inspection of the exact call expressions); an
identical-title, zero-anchor case (`test_identical_titles_no_anchor_still_creates`)
confirms title similarity has no bearing on the outcome.

**Provider**: the R3-specific code added by this slice (everything from the new
"RECONCILIATION GATE" docstring section onward — imports, the new exception class,
`_resolve_reference_year`, and the modified `create_signal_from_approved_review`
body) contains no MAC/MSP/FAA/Runway Safe/USAspending/Granicus token. (The module's
own pre-existing Slice 9C docstring, predating this slice, already legitimately
named "MSP" once as historical rationale for why financial fields were never
accepted — a whole-file scan would have false-positived on that untouched prose; see
Section 27 for the test-authoring correction this required.)

## 19. Transaction behavior

Still never calls `session.commit()` anywhere (verified via a targeted AST check on
calls made specifically to a variable named `session`, avoiding the same
false-positive class the R2 checkpoint already found and fixed once for `set.add()`).
`CLEAR_TO_CREATE` followed by `session.rollback()` leaves zero persisted Signals.
`POSSIBLE_EXISTING_SIGNAL_MATCH` leaves `session.new` empty even *before* any
rollback is issued — there was never anything pending to roll back, because the
error is raised before any object is added to the session at all.

## 20. Publication safety

Every successfully created Signal is still hard-coded `published=False`, unchanged.
A blocked (`POSSIBLE_EXISTING_SIGNAL_MATCH`) attempt creates zero new rows of any
kind — the pre-existing candidate Signal's own legacy `published=True` default
(unrelated to this guard, `app/models/signal.py`'s own documented
backward-compatibility default for rows not created through the governed path) is
correctly left untouched, not conflated with the guard's own behavior.

## 21. Real DB read-only pilot

Read-only (`mode=ro` SQLite connection — no write possible). For SourceAssertion
#222:

```
#222.signal_id = 67
latest ReviewerAction = MARK_DUPLICATE (duplicate_of_signal_id=67)
R1/R2 decision (called directly, not via create_signal_from_approved_review):
    ALREADY_LINKED(signal_id=67)
Signal #67 supporting_source_ids: (70,)   [== #222's own source_id]
```

No mutating governed-creation path was ever invoked against the real database.
Hash/size/mtime identical before and after (Section "Real DB safety").

## 22. Focused tests

```
tests/test_existing_signal_reconciliation.py               (unchanged, R1)
tests/test_existing_signal_reconciliation_candidates.py    (unchanged, R2)
tests/test_governed_signal_creation.py                     52 passed (unchanged - regression net)
tests/test_governed_signal_creation_migration.py           (unchanged)
tests/test_governed_signal_creation_reconciliation.py      43 passed (new, R3; +5 at the review checkpoint, S30)
tests/test_reviewer_action_persistence.py                  (unchanged)
tests/test_reviewer_action_migration.py                    (unchanged)
tests/test_human_review_queue.py                            (unchanged)
tests/test_physical_installation_reconciliation.py         (unchanged)
tests/test_physical_installation_identity_linking.py       (unchanged)
tests/test_cgf_physical_installation_pilot.py               (unchanged)
tests/test_static_export.py                                 (unchanged)
tests/test_signal_publication_migration.py                  (unchanged; added to the focused set at the review checkpoint)
```
Combined: **438 passed**, 0 failed (re-run at the review checkpoint after S30's additions).

## 23. Full pytest

**1402 passed** (1359 baseline + 43 new), 0 failed — confirms this slice, including the
review checkpoint's own 5 additional regression tests, added functionality without
changing any existing behavior, including the 52 pre-existing
`test_governed_signal_creation.py` tests passing byte-for-byte unchanged.

## 24. `py_compile`

`python -m py_compile app/services/governed_signal_creation.py
tests/test_governed_signal_creation_reconciliation.py` — clean, no output.

## 25. `git diff --check`

Clean (exit 0).

## 26. Exact files changed

- `app/services/governed_signal_creation.py` (modified — reconciliation gate added)
- `tests/test_governed_signal_creation_reconciliation.py` (new)
- `docs/architecture/existing-signal-reconciliation-r3-governed-creation-report.md`
  (new, this file)

`app/services/existing_signal_reconciliation.py` (R1) and
`app/services/existing_signal_reconciliation_candidates.py` (R2) were **not**
modified — no independent defect was found in either during this slice. None of the
six legacy Signal-write paths (`app.services.signal_rules` and five `scripts/*.py`
importers) were touched.

## 27. `git status`

`app/services/governed_signal_creation.py` shows modified; the new test file and this
report show as additions. Every other untracked path in the working tree predates
this task. No file was staged. No commit was made.

## 28. Corrections discovered

**A real, subtle design finding, caught by an adversarial smoke test before any test
suite existed**: the first draft of the runway-anchor scenario assumed the create
call's own `runway_id` keyword argument would feed the reconciliation subject's
`runway_id`. It does not, and should not — `build_reconciliation_subject()` (R2,
unmodified) reads `SourceAssertion.runway_id` (the evidence's own resolved runway),
which is a structurally different fact from the runway a human is choosing to *assign
to the new Signal*. The smoke test raised `2` Signals instead of raising
`ExistingSignalPossibleMatchError`, immediately exposing the mistake before it reached
any committed test or code. Corrected by setting `SourceAssertion.runway_id` directly
in every anchor-scenario test fixture, matching how real evidence would actually carry
this information (populated upstream, by identity/runway resolution, before governed
creation ever runs) — no code change to R3's own subject-building call was needed
once this was understood; R2's own behavior was correct throughout.

**Two test-authoring corrections**, both the same class of mistake the R1 and R2
review checkpoints already found and fixed once each, now recognized and avoided
proactively rather than needing a separate checkpoint to catch them: (1) a firewall
test scanning this module's *entire* source for "MSP"/`estimated_total_value_usd`
would have false-positived on this file's own pre-existing Slice 9C docstring prose
(which predates R3 and legitimately names both as historical rationale) — narrowed to
scan only the R3-added code and the actual reconciliation call sites, respectively.
(2) An initial determinism test called `create_signal_from_approved_review()` twice
against two *different* SourceAssertions, not realizing the first call's own Signal
creation became a new candidate for the second call - not a bug, but a test
misunderstanding its own side effects. Replaced with a read-only, side-effect-free
repetition of `evaluate_existing_signal_reconciliation()` against one unchanging DB
state.

No correction was needed to `app/services/existing_signal_reconciliation.py` or
`app/services/existing_signal_reconciliation_candidates.py` — both were read fresh at
the start of this slice and used entirely unmodified.

## 29. Review checkpoint (2026-08-19, same HEAD)

The review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R3_CRITICAL_REVIEW_COMMIT_PUSH)
re-read every governed R3 file, R1, R2, and the design doc fresh, and independently
attacked the specific properties the original implementation report claimed rather
than trusting them. **No production defect was found in
`app/services/governed_signal_creation.py`** — every adversarial scenario attacked
below produced the correct, expected result on the first try, against the code as
already committed. This checkpoint's own contribution is closing a real **test-
coverage gap**: several of the properties the original report described in prose had
no dedicated, permanent regression test proving them — most importantly the
runway_id trust boundary, the single most subtle finding from the original
implementation.

**Runway_id trust-boundary, attacked directly and confirmed correct (no code
change needed)**: constructed both directions from scratch, outside any existing test,
before adding permanent coverage. (1) `SourceAssertion.runway_id = NULL`, create-call
`runway_id = X` matching an existing candidate, no other anchor → confirmed
`CLEAR_TO_CREATE`, zero anchor reasons — the proposed Signal's own runway selection
cannot manufacture an anchor. (2) `SourceAssertion.runway_id = X` (matching a
candidate), create-call proposes a *different* `runway_id = Y` for the new Signal →
confirmed `POSSIBLE_EXISTING_SIGNAL_MATCH` still fires off the evidence-owned `X` —
the human's differing choice for the new Signal cannot suppress a genuine anchor.
Both now permanent tests: `TestRunwayIdTrustBoundary` (2 tests).

**ALREADY_LINKED drift check, attacked in combination with an unrelated anchor-backed
candidate**: confirmed a drifted repeat request (different title) against an
already-linked `SourceAssertion` still raises the pre-existing drift `ValueError`
— not `ExistingSignalPossibleMatchError` — even when a *completely unrelated*,
independently anchor-backed Signal also exists at the same airport. This is the
exact "reconciliation must not become an alternate route around existing governance"
property the checkpoint's own mission names, verified for the drift path specifically
(the original report's own `test_already_linked_ignores_anchor_backed_other_candidates`
tested reuse, not drift, against an anchor-backed distractor — a real, if narrow, gap).
Now a permanent test: `TestAlreadyLinkedDriftNotBypassedByAnchor` (1 test).

**Financial firewall, attacked with a real, large, coincidentally-matching dollar
claim (SFO-$40M-style)**: a `Claim` carrying `FinancialFact(amount=Decimal(
"40000000.00"), ...)` was attached to a `SourceAssertion` whose candidate Signal's own
`estimated_total_value_usd` was set to the *exact same* figure. Confirmed the amount
never appears in any reason, never contributes to the outcome, and the created
Signal's own `estimated_total_value_usd` stays `None` — the only advisory reason
present is the unrelated, genuine `category` match. Now a permanent test (added
alongside a companion "wildly different titles with a genuine anchor still blocks"
test, proving title irrelevance in both directions):
`test_large_matching_dollar_claim_never_becomes_advisory_or_anchor_evidence`,
`test_wildly_different_titles_with_genuine_anchor_still_blocks`.

**Reference-year priority, attacked with the remaining untested ambiguous
combinations** (planning-over-procurement, procurement-over-construction-start,
construction-start-over-completion-date, all-`None`): all four confirmed
byte-identical between `_resolve_reference_year()` and R2's own
`_signal_reference_year()`, extending the pre-existing consistency test's coverage
(which already covered target-over-planning, manual_year_estimate exclusion, and the
construction-start fallback) rather than needing a code change — R3's priority order
was already correct.

**Test count**: 38 → 43 (5 new regression tests: 2 runway trust-boundary, 1 drift-
not-bypassed-by-anchor, 2 title/financial-irrelevance). Full pytest: 1397 → 1402.

## 30. Ready for R4

**Ready.** The full pre-creation safety property (no governed Signal may be created
while a genuine structural identity anchor to an existing Signal is unresolved) now
holds end-to-end, proven against synthetic anchor-bearing fixtures for all three
anchor families, the real historical MSP pre-resolution case, and a real read-only
pilot of the current, already-resolved MSP state. R4 (human reconciliation
workflow/queue presentation, design doc S16) has a concrete, stable contract to build
against: `ExistingSignalPossibleMatchError.decision` already carries everything a
review-queue surface would need to display (`candidate_signal_ids`, `reasons`), and
`GovernedSignalCreationResult.reconciliation_decision` already carries the
non-blocking advisory metadata for the `CLEAR_TO_CREATE` case. The one open question
R4 will need to resolve (flagged, not solved here, per this slice's own scope): how a
caller of `create_signal_from_approved_review()` should catch
`ExistingSignalPossibleMatchError` and surface it into the human review queue's own
`ReviewWorkflowState` vocabulary (`app/services/human_review_queue.py`, untouched by
this slice) — likely a new derived state distinguishing "blocked pending
reconciliation" from the existing `ACTIVE_REVIEW`/`APPROVED_PENDING_SIGNAL` states,
but that is a queue-presentation design decision, not a governed-creation one, and
this slice's own explicit scope boundary (no queue/UI change) leaves it to R4.

## Real DB safety

Read-only pilot only (`mode=ro` SQLite connection) — no write was possible, let alone
attempted; the mutating `create_signal_from_approved_review()` path was never invoked
against the real database anywhere in this slice. Hash/size/mtime captured before and
after the pilot, identical to each other and to every prior checkpoint in this
session: `sha256=71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`,
size `1789952` bytes, mtime `1787158044.8543456`.

Re-captured, and the read-only pilot re-run, at the review checkpoint: identical
hash/size/mtime before and after, and an identical result for #222
(`signal_id=67`, latest `ReviewerAction=MARK_DUPLICATE`, `ALREADY_LINKED(67)` via a
fresh direct R1/R2 call, Signal #67's own `supporting_source_ids=(70,)`) — confirming
nothing in the review checkpoint touched or needed to touch the real database.
