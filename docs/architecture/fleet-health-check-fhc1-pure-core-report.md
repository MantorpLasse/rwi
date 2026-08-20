# Fleet Health Check — FHC1 Pure Hard-Invariant Core

Status: implementation complete, not committed/pushed (per this task's own stop
boundary). No database access of any kind occurred in this task.

## 1. Starting HEAD

`44ab598b150a74f63851c4b2e5887462aaa3cf1f`, local main == origin/main,
confirmed before any work began.

## 2. Exact 11 Rules Implemented

FH-A2, FH-B1, FH-B2, FH-C1, FH-C2, FH-C5, FH-D1, FH-D2, FH-E3, FH-G2, FH-G3 —
exactly the set the reviewed design's "Critical Review Corrections" section
names as the corrected FHC1 scope (FH-E1/E2 were explicitly reviewed and
rejected for this tier; FH-H2 requires the static export and is not a pure
function of persisted fields). No other rule from the 32-rule catalogue is
implemented here.

## 3. HealthFinding Shape

```python
@dataclass(frozen=True)
class HealthFinding:
    rule_id: str
    classification: HealthClassification
    entity_type: str
    entity_ids: tuple[int, ...]
    airport_id: Optional[int]
    summary: str
    structured_evidence: Mapping[str, Any]
```

`HealthClassification` is a small `str` `Enum` with the design's own five
values (`DETERMINISTIC_ERROR`, `DETERMINISTIC_WARNING`, `REVIEW_REQUIRED`,
`INFORMATIONAL`, `NOT_CURRENTLY_DETECTABLE`) — a label type shared forward
with FHC3/FHC4, not a scoring engine. FHC1 only ever constructs
`DETERMINISTIC_ERROR` findings; this is asserted by test
(`test_all_findings_classify_deterministic_error`). No score, confidence
percentage, recommendation ranking, or auto-repair instruction field exists —
verified by `TestHealthFindingShape.test_expected_field_set` enumerating the
dataclass's exact field set.

## 4. Input Dataclass Shapes

Ten frozen, rule-scoped fact dataclasses, each carrying only the exact
already-joined fields its rule needs (no ORM object, no Session, no raw dict,
no raw SQL row):

- `AirportCodeFact` (FH-A2): `airport_id, iata_code, icao_code, faa_code`
- `RunwayEndCountFact` (FH-B1): `runway_id, airport_id, runway_end_count`
- `RunwayDesignationFact` (FH-B2): `runway_id, airport_id, designation`
- `InstallationYearFact` (FH-C1): `installation_id, airport_id, install_year, replacement_year`
- `InstallationRunwayAirportFact` (FH-C2): `installation_id, installation_airport_id, runway_id, runway_airport_id`
- `PhysicalInstallationIdentityAirportFact` (FH-C5): `identity_id, identity_airport_id, runway_id, runway_airport_id, runway_end_id, runway_end_airport_id`
- `SignalRunwayAirportFact` (FH-D1): `signal_id, signal_airport_id, runway_id, runway_airport_id`
- `SourceAssertionSignalAirportFact` (FH-D2): `assertion_id, assertion_airport_id, signal_id, signal_airport_id`
- `SignalConstructionDateFact` (FH-E3): `signal_id, airport_id, construction_start, completion_date`
- `SourceAssertionGovernanceFact` (FH-G2/FH-G3, shared): `assertion_id, signal_id, latest_action, latest_action_duplicate_of_signal_id`

Every cross-entity rule (C2, C5, D1, D2) receives **both sides of the
comparison already resolved** (e.g. a Signal's own `airport_id` *and* the
`airport_id` of the Runway it points at) rather than an id to look up — the
join is the caller's job, never this module's, so the pure layer can never
itself perform an implicit/unsafe join. `SourceAssertionGovernanceFact`
deliberately takes an already-resolved `latest_action` rather than a list of
`ReviewerAction` rows — recency derivation (`created_at desc, id desc`, never
chain-walking `supersedes_action_id`) is explicitly left to the future FHC2
adapter, exactly as the design's own R4D precedent keeps that logic out of
presentation-layer consumers.

## 5. Rule Registry / Evaluator API

A static tuple `_RULE_EVALUATORS: tuple[(rule_id, snapshot_field_name,
evaluator_fn), ...]` in fixed order (A2, B1, B2, C1, C2, C5, D1, D2, E3, G2,
G3) — not dynamic plugin machinery. `RULE_IDS` is derived from it (no
separately-maintained list to drift). `FleetHardInvariantSnapshot` is a thin
frozen container bundling the ten fact tuples above (all defaulting to `()`),
built by a future FHC2 adapter from one read-only pass over the database.
`evaluate_hard_invariants(snapshot) -> tuple[HealthFinding, ...]` iterates the
registry in order and concatenates each rule's own findings — the single
public batch entry point.

## 6. Each Rule's Exact Invariant

- **FH-A2**: two or more Airports share the same non-null, non-empty-string
  `iata_code`, `icao_code`, or `faa_code` (checked independently per code
  type).
- **FH-B1**: a Runway's `RunwayEnd` row count is not exactly 2.
- **FH-B2**: two or more Runways at the same airport share the exact same
  (literal, uncased) `designation`.
- **FH-C1**: an Installation has both `install_year` and `replacement_year`
  populated, and `replacement_year < install_year`.
- **FH-C2**: an Installation's `runway_id` is set and the referenced Runway's
  `airport_id` differs from the Installation's own `airport_id`.
- **FH-C5**: a PhysicalInstallationIdentity's `runway_id` and/or
  `runway_end_id` is set and the referenced Runway's/RunwayEnd's `airport_id`
  differs from the identity's own `airport_id`.
- **FH-D1**: a Signal's `runway_id` is set and the referenced Runway's
  `airport_id` differs from the Signal's own `airport_id`.
- **FH-D2**: a SourceAssertion has both an attributed `airport_id` and a
  linked `signal_id`, and the linked Signal's `airport_id` differs from the
  assertion's own `airport_id`.
- **FH-E3**: a Signal's `construction_start` and `completion_date` are both
  set, and `construction_start > completion_date` (equal dates are not a
  violation).
- **FH-G2**: a SourceAssertion's latest `ReviewerAction` is `MARK_DUPLICATE`
  and the assertion's own `signal_id` does not equal that action's
  `duplicate_of_signal_id`.
- **FH-G3**: a SourceAssertion's latest `ReviewerAction` is `DEFER`,
  `NEEDS_MORE_EVIDENCE`, or `REJECT_SIGNAL`, and the assertion's own
  `signal_id` is non-null.

## 7. NULL/Legacy Behavior

Every rule treats a missing (`None`) required field as **absence, never
contradiction** — no rule ever upgrades incompleteness into a finding:

- FH-C2/C5/D1: a `None` `runway_id`/`runway_end_id` (airport-level-only
  Installation/Identity/Signal — the dominant real shape, e.g. 56 of 68 real
  Signals) short-circuits with no finding.
- FH-D2: a `None` `signal_id` (67 of 68 real Signals) or a `None`
  `assertion_airport_id` (5 of 222 real SourceAssertions, all pre-identity-
  guard) both short-circuit with no finding.
- FH-C1/E3: either side `None` short-circuits — most concretely, `FH-C1` is
  exercised by **zero** real `Installation` rows today (`replacement_year` is
  populated on 0 of 149), and the corrected design doc is explicit that this
  is "zero informative coverage," not "0/149 tested-and-passed"; the rule's
  NULL-handling is what makes that currently-vacuous state safe rather than
  alarming.
- FH-G2/G3: `latest_action=None` (no ReviewerAction ever recorded — the
  overwhelming majority of real SourceAssertions) short-circuits both rules.
- FH-A2: `None` and exact `""` are excluded from collision detection; a
  whitespace-only value is **not** collapsed to absence (compared literally),
  matching the reviewed design's explicit "do not collapse whitespace unless
  the rule says so" instruction.

## 8. Determinism

`evaluate_hard_invariants` iterates the registry in a fixed compile-time
order and every per-rule evaluator preserves input order without any
non-deterministic dict/set iteration exposed in output — for the two
collision/batch rules (A2, B2), `entity_ids` on each finding is
`tuple(sorted(...))`, making the finding's content independent of the
order facts were supplied in (verified directly:
`test_a2_finding_entity_ids_independent_of_input_order`,
`test_b2_finding_entity_ids_independent_of_input_order`). Two calls with the
same snapshot produce `==`-equal output
(`test_same_snapshot_twice_is_equal`, `test_zero_findings_is_stable_across_repeated_calls`).

## 9. False-Positive Firewalls

Verified structurally, not just by convention:

- No fact dataclass has a `name`, `title`, vendor/supplier/manufacturer,
  financial-amount, or `category`/`status`/`confidence` field at all — the
  banned inference categories are type-level impossible, not merely unused.
- No airport-name-keyword, title-similarity, or fuzzy-matching logic exists
  anywhere in the module (`difflib`/`SequenceMatcher`/`fuzz`/`similarity` all
  absent).
- No `evaluate_fh_c3`/`fh_d3`/`fh_d4`/`fh_a4` function exists — the "multiple
  rows = duplicate" and co-location/keyword-name rules are structurally
  absent from this slice (FHC3 territory).
- No reconciliation/fingerprint/candidate identifiers exist anywhere in the
  module's actual code (checked via AST identifier extraction, not a raw
  substring scan, since the module's own docstrings legitimately *describe*
  what it deliberately excludes and would otherwise false-positive a naive
  text search — the same "docstring prose vs. real usage" lesson noted
  repeatedly across the R1–R4E review checkpoints).
- A legacy, provenance-only SourceAssertion/Signal (no governed link) never
  produces a finding (`test_legacy_provenance_only_signal_produces_no_hard_error`).
- A zero-runway airport contributes zero `RunwayEndCountFact` rows and
  produces zero findings — never coerced into a fabricated B1 violation.
- Multiple legitimate airport-level Installation rows at one airport (the
  real 18-airport pattern) produce zero FH-C2 findings.

## 10. Purity

Verified both structurally (AST) and by test, not just by inspection:

- No `sqlalchemy`, `app.database`, `app.models`, `os`, `pathlib`, `socket`,
  `random`, `uuid`, or HTTP-client import anywhere (AST-walked import list).
- No `.now()`/`.utcnow()`/`.today()`/`random`/`uuid4` attribute access
  anywhere (AST-walked attribute names).
- No `Session`/`create_engine`/`sessionmaker`/`Query` identifier used
  anywhere (checked against actual AST identifiers, not raw text, for the
  same docstring-false-positive reason as §9).
- No module-level mutable state beyond the frozen registry tuple and four
  literal action-name string constants (checked against `ast.Module.body`
  top-level assignments only).
- Every rule function's only parameter is its own fact tuple — no hidden
  clock/config input is even reachable
  (`test_no_current_date_inference` inspects `evaluate_fh_e3`'s signature
  directly).

## 11. International Readiness

All fixtures use synthetic Swedish (`ARN`/`ESSA`) and Japanese (`NRT`/`RJAA`)
airports, plus fully anonymous integer-ID facts for every violation case — no
FAA, MSP, MAC, Runway Safe, USAspending, or USD-specific logic or fixture data
appears anywhere in the module or its tests. All comparisons (codes,
designations, dates, action strings) are structural/literal, not
US-format-specific.

## 12. Healthy Synthetic Baseline

`_healthy_snapshot()` builds a small, internally consistent, multi-country
fleet (3 airports, 2 runways, 2 installations, 2 physical-installation
identities, 2 signals, 2 source assertions, 2 governance facts) exercising
every "valid, non-triggering" shape per rule, including the NULL/absent-link
shapes that dominate the real fleet. `evaluate_hard_invariants(healthy) == ()`
is asserted directly. Each of the 11 rules' violation shapes was then injected
**one at a time** onto this baseline and asserted to produce **only** that
rule's `rule_id` in the output set — proving no hidden cross-rule coupling —
plus one combined test injecting all 11 at once, asserting the full rule-ID
set fires together.

## 13. Per-Rule Adversarial Results

All pass; see `tests/test_fleet_health_rules.py` for the full matrix. Highlights:

- **FH-A2**: NULL/empty-string never collide; whitespace values compared
  literally (not collapsed); case is significant (not folded); a 3-way
  collision yields one finding with all 3 ids; independently checked per
  code type (an IATA collision does not implicate ICAO/FAA).
- **FH-B1**: 0, 1, and 3 ends all violate; 2 does not; an airport contributing
  zero facts (no Runway rows) produces zero findings.
- **FH-B2**: same designation at different airports never collides; trailing
  whitespace is a different literal string (not normalized).
- **FH-C1**: equal years is not a violation; either side `None` never
  triggers (covers the real 0/149-populated state).
- **FH-C2/D1**: absent runway link is absence, not contradiction; multiple
  legitimate airport-only Installation rows never trigger.
- **FH-C5**: both-links-absent (the real, deliberate-by-design state for all
  10 real `PhysicalInstallationIdentity` rows) never triggers; runway
  mismatch and runway-end mismatch are each independently detected.
- **FH-D2**: no linked Signal is legacy/informational, never an error (covers
  the real 67/68 and 221/222 states); no attributed assertion airport never
  triggers.
- **FH-E3**: equal dates allowed; either side `None` never triggers; no
  reachable current-date input exists at all.
- **FH-G2/G3**: non-matching action strings never trigger the wrong rule;
  no action recorded yet never triggers; the two evaluators never call into
  each other (source-inspected directly).

## 14. Focused Tests

`tests/test_fleet_health_rules.py`: **102 passed**, 0 failed.

## 15. Full Pytest

**1768 passed**, 0 failed (1666 pre-existing baseline + 102 new FHC1 tests —
matches exactly, no regressions).

## 16. py_compile

Clean on both new files (`app/services/fleet_health_rules.py`,
`tests/test_fleet_health_rules.py`).

## 17. git diff --check

Exit 0, clean.

## 18. Exact Files Changed

New only:
- `app/services/fleet_health_rules.py`
- `tests/test_fleet_health_rules.py`
- `docs/architecture/fleet-health-check-fhc1-pure-core-report.md` (this file)

No existing production file was modified.

## 19. Defects/Corrections Found

None in the reviewed design's FHC1 rule definitions themselves — the 11 rules
as specified in the "Critical Review Corrections" section were implementable
exactly as written, with no further semantic gaps found during
implementation. Two defects were found and fixed **in this task's own test
code** before they could mask real coverage:

1. An AST-based purity test initially walked *all* nodes rather than only
   `ast.Module.body`, which would have flagged every local variable inside a
   rule function as "module-level mutable state." Fixed to inspect only
   top-level statements.
2. Two purity/firewall tests initially did raw substring search against the
   whole file's source text, which false-positived on this module's own
   docstring prose (e.g. "no Session," "resolves reconciliation facts
   outside...") — the same docstring-vs-real-usage class of test bug noted
   repeatedly in the R1–R4E review checkpoints. Fixed by extracting actual
   AST identifiers (names, attributes, function/class names, arguments,
   import aliases) and checking against those instead of raw text.

## 20. READY_FOR_FHC1_REVIEW_CHECKPOINT

**Yes.**

## 21. Exact Recommended FHC2 Scope

Implement `app/services/fleet_health_check.py`: a read-only DB adapter that
opens the real database via the same `mode=ro` URI pattern already used by
`scripts/list_human_review_queue.py`/`scripts/review_reconciliation_item.py`,
runs the schema-readiness gate pattern from R4D/R4E, performs the necessary
joins (Airport/Runway/RunwayEnd codes and structure; Installation↔Runway;
PhysicalInstallationIdentity↔Runway/RunwayEnd; Signal↔Runway;
SourceAssertion↔Signal; SourceAssertion↔latest-ReviewerAction via the
existing `get_latest_reviewer_action()` recency logic) to build one
`FleetHardInvariantSnapshot`, and calls this task's `evaluate_hard_invariants()`
unmodified. Add `scripts/run_data_health_check.py` as a thin CLI wrapper
(text/JSON report of the returned findings) only after the adapter itself has
its own DB-adapter test suite (in-memory SQLite fixtures, per the reviewed
design's §13). FHC2 must not add new rules, must not write to the database,
and must not change `fleet_health_rules.py`'s public surface.

---

## Critical Review Checkpoint (RWI_FLEET_HEALTH_CHECK_FHC1_CRITICAL_REVIEW_COMMIT_PUSH)

A fresh, adversarial review of this implementation was performed independently
of the implementation report above — every claim re-verified against the
actual code, not trusted from the report's own prose.

### Independent findings

- **Scope/registry**: confirmed exactly the 11 intended rule IDs, no FHC3/FHC4
  rule leaked in (`evaluate_fh_a4`/`fh_c3`/`fh_d3`/`fh_d4`/`fh_h2` all absent,
  confirmed by `hasattr`), registry order stable, `RULE_IDS` correctly derived
  from the single registry tuple (no separately-maintained list to drift).
- **HealthFinding/input contracts**: confirmed frozen, confirmed no
  score/confidence/ranking/repair/raw-text field exists on `HealthFinding` or
  any of the 10 fact dataclasses (checked against the actual
  `__dataclass_fields__` sets, not by convention).
- **Per-rule adversarial attack**: each of the 11 rules was independently
  re-derived from the committed design's final corrected catalogue and
  attacked for a legitimate real-world state that could false-positive it.
  Ten held up exactly as implemented. FH-E3 (the only temporal rule) was
  scrutinized hardest: could a multi-phase project (the same asynchronous-
  field-update risk that got FH-E1/E2 downgraded) also corrupt
  `construction_start`/`completion_date`? Conclusion: **no** — Signal #3's own
  real notes show that when a project enters a new phase, RWI creates a
  **new Signal row** for that phase (Phase 2 is Signal #3, distinct from
  whatever represents Phase 1), so one Signal's own `construction_start`/
  `completion_date` pair is scoped to one construction effort by the model's
  own real usage convention — unlike `planning_year`/`procurement_year`,
  which the same Signal #3 notes show accreting multiple funding events
  **on the same row** over time. FH-E3 is confirmed sound as
  `DETERMINISTIC_ERROR`, but is explicitly flagged here as the closest call
  in the rule set and worth first-class attention once FHC2 exposes it to
  real data.
- **Trust-boundary review**: `runway_airport_id`/`signal_airport_id`/etc.
  being `None` while the corresponding `*_id` is set (e.g. `runway_id` set
  but `runway_airport_id=None`) is not reachable from real data today (every
  relevant ORM column is `NOT NULL`) but is not defended against structurally
  — if a future FHC2 adapter ever produced this shape via a broken join, the
  affected rule would fail loud (report a spurious mismatch) rather than
  silently pass. Reviewed and accepted as the correct failure mode (fail loud
  beats silent pass for a health check), not a defect requiring a fix.
- **Governance semantics cross-check**: FH-G2/FH-G3's conditions were
  compared line-by-line against `human_review_queue.py`'s own
  `invariant_warnings` checks (already-shipped, already-reviewed logic) — no
  divergence found. `CONFIRM_DISTINCT_SIGNAL` and `APPROVE_SIGNAL` correctly
  never trigger either rule; `MARK_DUPLICATE` is exclusively FH-G2's
  territory, the three terminal-non-creating actions exclusively FH-G3's —
  confirmed by direct test as well as by inspection.

### Defects found

**One genuine production defect**, found by directly attacking the
mission's own explicitly-named risk (§17: "duplicate input rows"): **none of
the 11 evaluators deduplicated their input by entity id.** Concretely:

- `evaluate_fh_b1` given the same `RunwayEndCountFact` twice produced **two**
  `HealthFinding` objects for one runway (a plain duplicate-finding defect).
- `evaluate_fh_b2` given the same `RunwayDesignationFact` (one runway) twice
  produced **one finding claiming that runway collides with itself**
  (`entity_ids=(10, 10)`) — a false positive, not merely a duplicate.
- `evaluate_fh_a2` given the same `AirportCodeFact` twice produced the same
  false "self-collision" shape (`entity_ids=(1, 1)`).

This is a realistic failure mode, not a contrived one: a future FHC2 adapter
SQL query with an unintended `JOIN` fan-out (e.g. joining through a
one-to-many relationship without an explicit `DISTINCT`/dedup step) is a
common, easy-to-introduce bug class, and every one of these 11 rules would
have silently amplified it into spurious or duplicated findings against the
real database.

### Corrections made

Fixed at the source: added a small `_dedupe_preserve_order(facts, key)`
helper (first-occurrence-wins, order-preserving) and applied it as the first
step of all 11 evaluators — per-record rules dedupe by their own entity id
(`runway_id`, `installation_id`, `identity_id`, `signal_id`, `assertion_id`);
the two batch/grouping rules (FH-A2, FH-B2) dedupe their contributing facts
by entity id *before* grouping, so a repeated entity can never inflate or
fabricate a collision group. Verified directly:

```
B1 duplicate-row findings: 2 -> 1
B2 duplicate-row (same runway twice): self-collision finding -> ()
A2 duplicate-row (same airport twice): self-collision finding -> ()
A2 genuine 2-airport collision with one row duplicated: still correctly (1, 2), not (1, 1, 2)
```

A second, minor test-quality issue was also fixed: `test_no_title_similarity_
or_keyword_logic` still did a raw substring scan of the module's source text
(the same docstring-false-positive risk already fixed for two other firewall
tests during implementation, per the mission's explicit instruction not to
rely on "naive docstring substring scans as sole proof"). Strengthened to use
the same AST-identifier extraction as the other firewall tests, plus a direct
check that `difflib` was never imported.

### Regression tests added

14 new tests in a dedicated `TestDuplicateInputRowDefense` class: one
duplicate-row test per rule (all 11), plus two tests proving a genuine
collision is still correctly detected even when one contributing row is
duplicated (FH-A2, FH-B2), plus one test proving the dedup itself is
deterministic (first-seen-wins, not order-dependent) when two *different*
facts happen to share the same entity id.

### Final rule contract

Unchanged from the implementation report: exactly FH-A2, FH-B1, FH-B2, FH-C1,
FH-C2, FH-C5, FH-D1, FH-D2, FH-E3, FH-G2, FH-G3, all `DETERMINISTIC_ERROR`,
now additionally guaranteed duplicate-input-safe.

### Final test totals

`tests/test_fleet_health_rules.py`: **116 passed** (102 original + 14 new
regression tests), 0 failed. Full suite: **1782 passed** (1666 baseline +
116), 0 failed — the delta from the pre-review total of 1768 is exactly the
14 new regression tests; no test was removed or weakened.

### READY_FOR_FHC2

**Yes**, conditional on FHC2 explicitly documenting (not just assuming) that
its own SQL joins are dedup-safe per entity, since FHC1's own dedup defense
is a safety net, not a substitute for a correct adapter query.

RWI_FLEET_HEALTH_CHECK_FHC1_PURE_HARD_INVARIANT_CORE_COMPLETE
