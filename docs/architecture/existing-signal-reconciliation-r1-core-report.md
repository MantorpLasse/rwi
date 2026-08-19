# Existing-Signal Reconciliation — R1 Pure Core Report

Implements R1 of `docs/architecture/existing-signal-reconciliation-guard-design.md`'s
own S16 roadmap (the reviewed and hardened version, commit
`791efa58c30f8a422900dff77ed5881a6b214da5`): the pure, deterministic reconciliation
decision core only. No database adapter, no ORM integration, no governed Signal
creation integration, no ReviewerAction change, no queue/UI change, no schema change,
no migration, no real DB write, no commit, no push.

## 1. Starting HEAD

`791efa58c30f8a422900dff77ed5881a6b214da5` — confirmed matching `origin/main` before
starting. Baseline full-suite: 1225 passed.

## 2. Core API

`app/services/existing_signal_reconciliation.py`:

```python
def evaluate_existing_signal_reconciliation(
    subject: ExistingSignalReconciliationSubject,
    candidates: tuple[ReconciliationCandidateSignal, ...],
) -> ExistingSignalReconciliationDecision:
```

This is the simplified two-argument shape the R1 task itself specified
(`subject, candidates`), rather than the design doc's own four-argument sketch
(`source_assertion_snapshot, claims, candidate_signals, context`) from its Section 4.
That sketch predates this slice's own decision to bake every structured
reconciliation-relevant fact (category, vendor names, evidence date, reference year,
etc.) directly onto `ExistingSignalReconciliationSubject` rather than passing a
separate `claims: tuple[Claim, ...]` alongside it. This keeps the module fully
decoupled from `app.services.evidence_claim_semantics.Claim` — a future R2 adapter is
responsible for deriving these already-structured subject fields from claims (or from
any other future evidence shape), never this module. No `context` parameter exists
either: nothing in the reviewed design's rule needs caller-supplied policy
configuration (unlike `evaluate_promotion_policy`'s `PromotionPolicyContext`, which
carries a real external judgment call — `source_authority_tier` — this rule has none).
This is a documented, deliberate refinement of the design doc's own sketch, not a
deviation from its outcome semantics; see Section 21 for the full list of such
refinements.

## 3. Input dataclasses

**`ExistingSignalReconciliationSubject`** (frozen) — the new evidence:
`existing_signal_id`, `airport_id`, `runway_id`, `physical_installation_ids: tuple[int,
...]`, `source_id`, `artifact_identity`, `category`, `vendor_names: tuple[str, ...]`,
`evidence_date`, `reference_year`. Every field is either an anchor axis or a
compatibility axis from the design doc's own Section 6 taxonomy — no speculative field
(e.g. no project-identifier field, since none exists anywhere in the current schema).

**`ReconciliationCandidateSignal`** (frozen) — one existing Signal snapshot:
`signal_id`, `airport_id`, `runway_id`, `physical_installation_ids`,
`supporting_source_ids`, `supporting_artifact_identities`, `category`,
`confirmed_vendor`, `likely_supplier`, `evidence_date`, `reference_year`. The three
`supporting_*`/`physical_installation_ids` fields represent the *aggregate* a future
R2 adapter would compute by joining a Signal's `supporting_source_assertions` against
their own `InstallationAssertionLink` rows (design doc S6/S11) — this module never
performs that join itself, it only ever tests membership against the pre-aggregated
tuples it's handed.

Both dataclasses deliberately have **no** title, notes, raw-text, or financial/dollar
field of any kind — not merely unused, but structurally absent, so passing one raises
`TypeError` at construction (tested explicitly, Section 13/14).

## 4. Outcome model

`ExistingSignalReconciliationOutcome(str, Enum)`: `CLEAR_TO_CREATE`,
`POSSIBLE_EXISTING_SIGNAL_MATCH`, `ALREADY_LINKED` — exactly the reviewed design's
three outcomes, unchanged.

`ExistingSignalReconciliationDecision` (frozen): `outcome`, `candidate_signal_ids`
(anchor-backed, populated only for `POSSIBLE_EXISTING_SIGNAL_MATCH`), `reasons`
(anchor-tier only), `signal_id` (populated only for `ALREADY_LINKED`),
`advisory_candidate_signal_ids` (compatibility-only, populated only for
`CLEAR_TO_CREATE`), `advisory_reasons` (compatibility-tier only). The two id/reason
pairs are never populated on the same decision — matching the design doc's own S14
reading. All id tuples are sorted ascending.

`ALREADY_LINKED` is checked first, unconditionally, before any candidate is inspected
— `subject.existing_signal_id is not None` short-circuits the entire function.

## 5. Strong identity anchors implemented

Exactly the three anchor families the reviewed design justifies (design doc S6/S8):

- **Runway identity** — `subject.runway_id == candidate.runway_id`, both populated
  and non-null.
- **Physical-installation identity** — non-empty intersection between
  `subject.physical_installation_ids` and `candidate.physical_installation_ids`.
- **Provenance identity** — `subject.source_id` present in
  `candidate.supporting_source_ids`, OR `subject.artifact_identity` present in
  `candidate.supporting_artifact_identities`.

Each anchor is evaluated **per candidate**, independently — never pooled across the
candidate set (design doc Invariant 22). Any single anchor is sufficient to mark that
candidate anchor-backed; anchor reasons never mix with compatibility reasons for the
same candidate (a candidate with both is reported only via its anchor reasons, since
the whole decision becomes `POSSIBLE_EXISTING_SIGNAL_MATCH` once any candidate has
one).

## 6. Compatibility / advisory evidence

Category equality (case/whitespace-normalized), vendor-name equality against either
`confirmed_vendor` or `likely_supplier` (case/whitespace-normalized), evidence-date
chronology (`candidate.evidence_date >= subject.evidence_date`, phrased as "postdates"
in the reason string), and reference-year equality. Airport agreement is **not**
emitted as its own advisory reason (see Section 21 — a design correction made during
implementation): the reviewed design's own Section 9 case G expects a bare
airport-only match to carry *no* advisory metadata at all, since airport agreement is
a near-universal precondition, not a distinguishing signal.

Any number of these axes may agree for the same candidate and it is still never
sufficient to reach `POSSIBLE_EXISTING_SIGNAL_MATCH` — enforced by construction: the
function only ever considers `candidate_anchor_reasons` when deciding whether to
block, and `_compatibility_reasons()` is never consulted for that decision at all.

## 7. Disconfirming evidence

A populated, differing `runway_id` on both sides, or a populated, differing
`airport_id` on both sides, excludes a candidate from the decision **entirely** — not
merely from the anchor branch, but from advisory metadata too (design doc Section
9-A: "that is a disconfirming anchor-tier signal that should suppress even the
advisory note"). This is implemented as an early `continue` per candidate, checked
before any anchor or compatibility evidence is even computed for that candidate.
Disconfirming evidence never promotes a compatibility axis to anchor status (Invariant
23) — it only ever removes a candidate, never adds one.

## 8. MSP #222/#67/#41 pre-resolution result

Synthetic, DB-free fixtures reconstructing the structured shapes from the reviewed
design's own Section 8 (not live DB rows): `TestMSPGoldenCase` in the test suite.

Result: **`CLEAR_TO_CREATE`**, `candidate_signal_ids=()`, `reasons=()`,
`advisory_candidate_signal_ids=(41, 67)`. Signal #67 carries three independent
compatibility reasons (category, vendor, temporal); Signal #41 carries one
(category only — it has no vendor field populated in the synthetic fixture, matching
the real row). No anchor axis fires for either candidate, matching the design doc's
own conclusion exactly: zero identity anchors connect #222 to #67 or #41 under
current structured data, so the guard would not have blocked creation — it would have
surfaced both as advisory candidates for a human to judge, precisely as intended.

## 9. MSP current `ALREADY_LINKED` result

Same subject with `existing_signal_id=67` set: **`ALREADY_LINKED(signal_id=67)`**,
`candidate_signal_ids=()`, `advisory_candidate_signal_ids=()` — the candidate list is
never inspected once linked, tested explicitly.

## 10. Synthetic anchor positive case

`TestSyntheticAnchorPositiveCase` proves the blocking branch is reachable at all
(since no real Signal in the current corpus exercises it, per the design doc's own
Section 10 finding): a shared, populated `runway_id` between subject and candidate,
plus compatible category/vendor context, reaches `POSSIBLE_EXISTING_SIGNAL_MATCH`
with exactly that candidate's id. A companion test confirms the same shared
`runway_id` with a conflicting, populated `airport_id` does **not** qualify — the
structural safety override always wins over any anchor.

## 11. Multiple-candidate behavior

`TestMultipleAnchorBackedCandidates`: two and three independently anchor-backed
candidates (via different anchor families — runway, physical-installation, provenance)
are all returned together in `candidate_signal_ids`, sorted ascending regardless of
input order — never ranked, never "first," never "newest." A dedicated ordering test
confirms the decision is identical whether the candidate tuple is passed forward or
reversed. `TestCompatibilityNeverPooledAcrossCandidates` confirms compatibility
evidence for one candidate never contaminates another candidate's reasons.

## 12. Financial / title / provider firewalls

- **Financial**: no field on either input dataclass or the decision dataclass
  contains any of `usd`/`amount`/`value`/`cost`/`price`/`financial`/`dollar`/`money`
  (checked via `dataclasses.fields()`, not string search — airtight against renaming
  drift). Constructing either input dataclass with a money-shaped keyword argument
  raises `TypeError` (the field does not exist). The production module's source
  contains no `$` and no specific numeral from the design doc's own worked coincidence
  example — verified by direct source inspection.
- **Title/raw-text**: same field-existence check for `title`/`raw_text`/`notes`/etc.;
  constructing a subject with `title=` raises `TypeError`; the module source contains
  no fuzzy-matching, NLP, or embedding machinery of any kind (`difflib`,
  `levenshtein`, `embedding`, `cosine`, "similarity"/resemblance-style logic, `nlp`,
  LLM/API-provider names) — checked by direct source inspection.
- **Source-family/provider**: the production module's source contains no mention of
  any specific acquisition provider, source family, airport authority, or vendor name
  anywhere — not in logic, and (a correction made during implementation, see Section
  21) not even in commentary, so this property holds by simple inspection rather than
  by convention. A synthetic non-US, non-English-vendor-name case
  (`test_case_h_international_non_usd_identical_structural_path`) reaches
  `POSSIBLE_EXISTING_SIGNAL_MATCH` via the exact same code path as any domestic case.

## 13. Adversarial-case results

All cases A-K from the reviewed design's own Section 9 (renumbered to match this
task's own A-K listing) are implemented in `TestAdversarialCases`:

| Case | Result |
|---|---|
| A — same airport+category+vendor, different runway (unpopulated on both sides) | `CLEAR_TO_CREATE`, advisory only |
| B — same airport+vendor+temporal, unrelated project | `CLEAR_TO_CREATE`, advisory only |
| C — same airport+category+year, different runway ends (unpopulated) | `CLEAR_TO_CREATE` |
| D — busy airport, 5 same-vendor/category candidates | `CLEAR_TO_CREATE`, all 5 advisory, none blocking |
| E — financial fields | structurally impossible to construct (`TypeError`) |
| F — bare planning evidence, one weak axis | `CLEAR_TO_CREATE`, advisory only |
| G — ambiguous-amount coincidence | no code path exists to compare amounts at all |
| H — international/non-domestic vendor+runway anchor | `POSSIBLE_EXISTING_SIGNAL_MATCH`, identical code path |
| I — two independently anchor-backed candidates | `POSSIBLE_EXISTING_SIGNAL_MATCH`, both ids returned |
| J — compatibility split across two candidates | `CLEAR_TO_CREATE`, never pooled into a match |
| K — populated, differing runway_id (disconfirming) | `CLEAR_TO_CREATE`, candidate fully excluded (no advisory either) |

Plus the runway/airport-conflict override tests (Section 7 above) and the
physical-installation "different identity must not qualify" test (Section 5).

## 14. Purity / determinism

`TestPurity`: no forbidden imports (`sqlalchemy`, `httpx`, `requests`,
`app.database`, `app.models`, `app.acquisition` — AST-based, not substring), no
`Signal`/`SourceAssertion`/`ReviewerAction` import anywhere, no `random` import, no
`today`/`now`/`utcnow` call anywhere (AST-based), no `open()` call, all three public
dataclasses are frozen (`__dataclass_params__.frozen is True`), and mutating any
constructed instance raises `dataclasses.FrozenInstanceError`.

`TestDeterminism`: identical `(subject, candidates)` always produces an `==`-equal
decision across repeated calls; inputs are unchanged after evaluation; reason
ordering is stable across repeated calls.

`TestNoCompatibilityCountSubstitutesForAnchor`: the central hardened rule, tested both
behaviorally (every compatibility axis agreeing at once still does not block) and
structurally (an AST walk over every `Compare` node in the module confirms the only
integer literal ever compared against is `0` — i.e., there is no reachable numeric
threshold constant of the kind the design doc's own review checkpoint found and
removed).

## 15. Focused tests

```
tests/test_existing_signal_reconciliation.py       75 passed (post-checkpoint; see S23)
tests/test_governed_signal_creation.py             (existing suite, unchanged)
tests/test_governed_signal_creation_migration.py   (existing suite, unchanged)
tests/test_reviewer_action_persistence.py          (existing suite, unchanged)
tests/test_reviewer_action_migration.py            (existing suite, unchanged)
tests/test_human_review_queue.py                   (existing suite, unchanged)
tests/test_physical_installation_reconciliation.py (existing suite, unchanged)
tests/test_physical_installation_identity_linking.py (existing suite, unchanged)
tests/test_cgf_physical_installation_pilot.py      (existing suite, unchanged)
```
Combined: **276 passed**, 0 failed (re-run at the review checkpoint with a broader
focused set than the original R1 pass — reviewer-action, human-review-queue, and
physical-installation reconciliation/linking suites added per the checkpoint's own
instruction, since those are the modules closest in spirit and future-integration
surface to this one).

## 16. Full pytest

**1300 passed** (1225 baseline + 75 new), 0 failed — confirms this slice, including
the review-checkpoint correction (S23), added functionality without changing any
existing behavior.

## 17. `py_compile`

`python -m py_compile app/services/existing_signal_reconciliation.py
tests/test_existing_signal_reconciliation.py` — clean, no output.

## 18. `git diff --check`

Clean (exit 0) — no whitespace errors, no conflict markers.

## 19. Exact files changed

- `app/services/existing_signal_reconciliation.py` (new)
- `tests/test_existing_signal_reconciliation.py` (new)
- `docs/architecture/existing-signal-reconciliation-r1-core-report.md` (new, this file)

No other file was read-written, modified, or touched. No production file listed in
the task's "DO NOT modify" list (`governed_signal_creation.py`,
`reviewer_action_persistence.py`, `human_review_queue.py`, `Signal`,
`SourceAssertion` models) was opened for writing at any point — they were read-only
re-confirmed at the prior review checkpoint, not touched in this slice at all.

## 20. `git status`

Only the three new files above appear as additions (`??` for the two under
`app/`/`tests/`, and the new report). Every other untracked path in the working tree
predates this task and was not created or modified by it. No file was staged. No
commit was made.

## 21. Design corrections discovered during implementation

Two real, honest corrections surfaced while implementing R1 against the reviewed
design's own text — both narrowing/clarifying rather than weakening the rule:

1. **Airport-only compatibility must not emit an advisory reason.** The design doc's
   own Section 9 case G explicitly expects a bare airport-only match to carry *no*
   advisory metadata ("no compatibility axis reaches SUPPORTING_EVIDENCE... no
   advisory metadata at all"), but its Section 6 taxonomy table separately tags
   `airport_id` as `[COMPATIBILITY]`, which a literal reading could imply should
   always emit its own reason line whenever it agrees. Since a future R2 adapter will
   always scope candidate discovery by `airport_id` (design doc S11), every candidate
   it ever returns would trivially satisfy that condition — making a standalone
   "compatibility:airport" reason appear on *every single advisory candidate,
   always*, adding no distinguishing information and directly contradicting case G's
   explicit expected result. `_compatibility_reasons()` was written to use airport
   agreement only as part of the hard disqualifying check (a structural conflict
   still excludes a candidate entirely) and never as its own advisory reason. This is
   implementing the design doc's own explicit worked example, not a deviation from it
   — documented here because it required resolving an internal tension in the design
   doc's own text (S6's taxonomy row vs. S9's case G) rather than a straightforward
   transcription.
2. **The production module's docstrings must not name specific dollar figures,
   currencies, or source/provider families at all** — not even as explanatory color
   about why they're excluded. An early draft of this module's own docstring
   referenced the design doc's real worked example directly (a specific airport's
   coincidental dollar figures) to explain *why* financial fields are absent. That
   drafting choice directly undermined the very firewall property being documented:
   a source-family/provider/amount firewall that can only be verified by trusting the
   author's restraint in prose is weaker than one verifiable by plain inspection. The
   module was rewritten to describe every exclusion in fully generic terms (matching
   `promotion_policy_evaluation.py`'s own established discipline of never naming a
   source family in its own source), so the firewall tests (Section 12) hold by
   direct `inspect.getsource()` scanning of the *entire* module, not just its logic.
   Nothing about the actual decision algorithm changed — this was purely a
   documentation-hygiene correction.

No corrections were needed to the outcome model, the anchor/compatibility taxonomy
itself, the disconfirming-evidence rule, the multiple-candidate fail-closed behavior,
or the `ALREADY_LINKED` short-circuit — all matched the reviewed design's own text
exactly on first implementation.

## 22. Correction made at the R1 review checkpoint (2026-08-19, same HEAD)

The review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R1_REVIEW_COMMIT_PUSH)
found one genuine, real R1-scope defect via the checklist's own "construct additional
attacks... duplicate candidate inputs if the API permits them" instruction:

**Defect**: `evaluate_existing_signal_reconciliation()`'s original implementation
accumulated per-candidate anchor/compatibility reasons in a plain
`dict[int, tuple[str, ...]]` keyed by `candidate.signal_id`, writing
`anchor_reasons_by_id[candidate.signal_id] = anchor_reasons` on each matching
candidate row. Nothing in the type signature (`candidates: tuple[ReconciliationCandidateSignal,
...]`) prevents a caller from passing two or more rows sharing the same `signal_id`
(e.g. an imperfectly-deduplicated future R2 adapter result). When that happened with
each duplicate row carrying a *different* anchor (or different compatibility) reason,
the plain dict assignment silently discarded every row's reasons except whichever was
evaluated last — making the decision's own `reasons`/`advisory_reasons` depend on
input order, even though the `outcome` and `candidate_signal_ids` stayed correct.
Reproduced directly:

```python
c_via_runway = ReconciliationCandidateSignal(signal_id=10, airport_id=1, runway_id=5)
c_via_installation = ReconciliationCandidateSignal(signal_id=10, airport_id=1, physical_installation_ids=(9,))
evaluate_existing_signal_reconciliation(s, (c_via_runway, c_via_installation)).reasons
# -> only the physical-installation reason survived
evaluate_existing_signal_reconciliation(s, (c_via_installation, c_via_runway)).reasons
# -> only the runway reason survived - same logical evidence, different result
```

This is a real violation of the checkpoint's own item M ("same logical input must
always produce byte/equality-identical decisions... candidate input order must not
cause a different semantic decision") and, more importantly, a real explainability
defect: a human reviewing a `POSSIBLE_EXISTING_SIGNAL_MATCH` could see only one of
two independently-true reasons a candidate was flagged, depending on an
implementation detail (iteration order) with no bearing on the actual evidence.

**Fix** (within R1 scope, no design-contract change): the two accumulator dicts now
map to `set[str]` and use `.setdefault(id, set()).update(reasons)` (union, never
overwrite) so every duplicate row's reasons are preserved regardless of order. The
final reason tuples are built with `sorted(...)` per candidate id — not raw set
iteration — because Python's per-process string hash randomization would otherwise
make set iteration order (and therefore the assembled tuple) vary between process
runs even for the exact same logical input, which would have reintroduced a subtler
version of the same determinism defect one layer down.

**Regression tests added**: `test_duplicate_signal_id_rows_with_different_anchors_are_merged_not_overwritten`
and `test_duplicate_signal_id_rows_with_different_compatibility_axes_are_merged` (both
assert forward- and backward-ordered duplicate rows produce `==`-identical decisions
with every expected reason present). Also added during the same pass, for the
checklist's remaining named attacks: `test_empty_and_whitespace_only_optional_fields_never_produce_spurious_compatibility`
(malformed/empty optional fields, item L.5) and
`test_four_candidate_permutations_all_produce_identical_decision` (all 24 orderings of
four candidates - one anchor-free, one disconfirmed by airport - produce one identical
decision, item L.7). Test count: 71 → 75. All other sections of this report (S1-S14,
S19-S22) were independently re-verified against the corrected code at the checkpoint
and required no further change.

**Also examined and explicitly accepted, not a defect**: `category` (`str`) and
`vendor_names` (`tuple[str, ...]`) are plain string fields with no length or content
validation, so nothing in this module can mechanically stop a caller from passing an
oversized or prose-like string into them instead of a genuine short structured name.
This is the same trust boundary already accepted by every existing pure-core
precedent in this codebase (`evaluate_signal_candidate`, `evaluate_promotion_policy`
equally trust that `Claim.relationship.party` etc. are already-extracted structured
values, never raw text, and validate neither length nor shape) — holding this new
module to a stricter standard than its own siblings would be inconsistent rather than
safer. Enforcing "this string was genuinely extracted, not smuggled prose" is an
extraction-time responsibility, upstream of this module, in every precedent this
design follows.

## 23. Readiness for R2

**Ready.** The pure core's public contract (`ExistingSignalReconciliationSubject`,
`ReconciliationCandidateSignal`, `ExistingSignalReconciliationDecision`,
`evaluate_existing_signal_reconciliation`) is stable, fully unit-tested against the
real golden case (as synthetic fixtures) and the full adversarial case set, and has
zero DB/ORM dependency to design around. R2's own scope (design doc S11,
`find_reconciliation_candidates(session, source_assertion) ->
tuple[ReconciliationCandidateSignal, ...]`) is now concretely specified by this
module's own `ReconciliationCandidateSignal` field list: an R2 implementer's job is
exactly "populate these eleven fields correctly from ORM state," with no remaining
ambiguity about what the adapter needs to produce. The one open engineering note for
R2, flagged but not addressed here (out of this slice's scope): computing
`physical_installation_ids`/`supporting_source_ids`/`supporting_artifact_identities`
for a candidate requires joining `Signal.supporting_source_assertions` against each
one's own `InstallationAssertionLink` rows — a multi-hop query worth writing
carefully and testing against a real DB fixture set, exactly as the design doc's own
Section 11 anticipates.

## Real DB safety

No real DB access was required or performed for R1 (the pure core has zero I/O). For
completeness, the real DB's hash/size/mtime were captured before this task began and
again after the full test run, both identical to every prior checkpoint in this
session: `sha256=71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`,
size `1789952` bytes, mtime `1787158044.8543456`.

No real DB access was required or performed at the review checkpoint either — the
same hash/size/mtime were re-captured before and after the checkpoint's own
validation pass and matched exactly.
