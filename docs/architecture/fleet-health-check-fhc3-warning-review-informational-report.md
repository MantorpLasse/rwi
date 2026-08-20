# Fleet Health Check — FHC3 Warning/Review/Informational Rules

Status: implementation complete, **not committed, not pushed** (per this
task's own stop boundary — a separate adversarial review checkpoint is
required first, matching the FHC1/FHC2 precedent).

## 1. Starting HEAD

`2aab1aa760f2b8fde9c076ff79a7ed25f3ed034d`, local main == origin/main,
confirmed before any work began.

## 2. Exact Final FHC3 Rule Catalogue (Reconstructed From the Committed Design)

The committed design's own §6 table (32 rows, re-read fresh line-by-line for
this task, not from any prior summary) classifies as follows. FHC1 already
owns the 12 `DETERMINISTIC_ERROR` rows (11 implemented as pure rules +
FH-H2, which needs the static export and stays deferred). The remaining 21
rows are FHC3's scope:

| Classification | Rule IDs | Count |
|---|---|---|
| `DETERMINISTIC_WARNING` | A3, B3, B4, C3, E1, E2, G1 | 7 |
| `REVIEW_REQUIRED` | A4, C4, D3, D4, E4, F3 | 6 |
| `INFORMATIONAL` | A1, B5, F1, F2 | 4 |
| `NOT_CURRENTLY_DETECTABLE` | H1, I1, I2 | 3 |

**Full implementation matrix** (rule_id, classification, entity scope,
required structured facts, trigger semantics, real reconnaissance hit
count, implementable in this slice, false-positive caveat):

| Rule | Class | Entities | Facts needed | Trigger | Real hits | Implementable now? |
|---|---|---|---|---|---|---|
| A1 | INFORMATIONAL | Airport, Runway | `airport_id`, `COUNT(Runway)` | count == 0 | 10 | **Yes** |
| A3 | WARNING | Airport | iata/icao/faa codes | all NULL/`""` | 0 | **Yes** (reuses FH-A2's own fact type) |
| A4 | REVIEW_REQUIRED | Airport | `Airport.name` text + codes | name-token absence | 3 confirmed real | **No — deferred, see §5** |
| B3 | WARNING | Runway, RunwayEnd | parsed heading pair | delta != 18 | 0 | No — deferred, see §18 |
| B4 | WARNING | Runway, RunwayEnd | designation vs. `runway_identity` normalization | mismatch | 0 | No — deferred, see §18 |
| B5 | INFORMATIONAL | Runway | external NASR import state | no NASR counterpart | N/A | No — architecturally out of reach, see §18 |
| C3 | WARNING | Installation | `airport_id`, `runway_id`, `runway_end` | ≥2 rows, both NULL | 18 | **Yes** |
| C4 | REVIEW_REQUIRED | InstallationAssertionLink | ordered `(reviewed_at, id)` history per assertion | latest outcome retracts an earlier SAME_PHYSICAL_INSTALLATION | 0 | **Yes** |
| D3 | REVIEW_REQUIRED | Signal | `airport_id`, `runway_id` | ≥2 share both, runway_id not NULL | 0 | **Yes** (reuses FH-D1's own fact type) |
| D4 | REVIEW_REQUIRED | Signal | `airport_id`, `runway_id` | ≥2 share airport, runway_id NULL | 12 groups / 30 signals | **Yes** (same reuse) |
| E1 | WARNING | Signal | planning_year, procurement_year | planning > procurement | 1 | **Yes** |
| E2 | WARNING | Signal | procurement_year, target_year | procurement > target | 0 | **Yes** |
| E4 | REVIEW_REQUIRED | Signal | status, completion_date | status=="completed", date NULL | 1 | **Yes** |
| F1 | INFORMATIONAL | Signal | source_id, governed-link presence | source_id set, no link | 67 | **Yes**, bucketed (see §14) |
| F2 | INFORMATIONAL | SourceAssertion | airport_id, review_state | airport_id NULL, unreviewed | 5 | **Yes** |
| F3 | REVIEW_REQUIRED | SourceAssertion | airport_id, review_state | airport_id NULL, reviewed | 0 | **Yes** |
| G1 | WARNING | SourceAssertion | 3 governance decision columns | HUMAN_REVIEW_REQUIRED with upstream mismatch | 0 | **Yes** |
| H1 | NOT_CURRENTLY_DETECTABLE | Signal | static export output | N/A | N/A | Doc-only |
| I1 | NOT_CURRENTLY_DETECTABLE | Signal | real-world identity oracle | N/A | N/A | Doc-only |
| I2 | NOT_CURRENTLY_DETECTABLE | Installation | lifecycle field (doesn't exist) | N/A | N/A | Doc-only |

**No ambiguity or contradiction requiring a STOP was found in the committed
design's own rule definitions** — every trigger semantic above is stated
plainly enough to implement without inventing new meaning. The one genuine
judgment call (FH-A4, §5 below) was resolved by following the mission's own
explicit, repeated prohibition rather than the design table's literal
detection-method column, since the two are in tension for that rule alone;
this is documented as a deliberate scope decision, not an ambiguity in the
design itself.

## 3. Exact Implemented Rule IDs

**13 rules**: FH-A1, FH-A3, FH-C3, FH-C4, FH-D3, FH-D4, FH-E1, FH-E2, FH-E4,
FH-F1, FH-F2, FH-F3, FH-G1.

## 4. Exact Deferred/Not-Currently-Detectable Rule IDs

**8 rules, none implemented as detectors**: FH-A4, FH-B3, FH-B4, FH-B5,
FH-H1, FH-I1, FH-I2 (all reasoned individually below/§18), plus FH-H2
(`DETERMINISTIC_ERROR`, outside FHC3's classification scope entirely —
belongs to a future FHC4 presentation-cross-check slice per the design's
own roadmap).

## 5. Architecture Choice

**New, narrowly-separated pure module**: `app/services/fleet_health_review_rules.py`
— not added to `app/services/fleet_health_rules.py`. Reasoning: FHC1's
module was explicitly reviewed and committed as "exactly these 11 hard
rules, nothing else"; growing that same file/registry with a different
classification mix would blur an already-shipped, already-reviewed
boundary and re-expose a frozen artifact to risk for no benefit. This
mirrors the codebase's own established precedent
(`human_review_reconciliation.py` being "DELIBERATELY A SEPARATE MODULE
FROM app.services.human_review_queue" for structurally identical reasons).
`HealthFinding`/`HealthClassification` are imported and reused, unmodified,
from FHC1 — never redefined. `AirportCodeFact` and `SignalRunwayAirportFact`
are also imported and reused verbatim (FH-A3 and FH-D3/FH-D4 need exactly
the same structured facts FH-A2/FH-D1 already read).

`app/services/fleet_health_check.py` (FHC2) is **extended, not replaced**:
new `_build_*` functions and `build_fleet_review_snapshot()`/
`run_fleet_review_check()` are appended after the existing, untouched
FHC1-adjacent functions — mission §21 explicitly authorizes this
("Extend FHC2's read-only adapter only as needed"). A thin
`run_full_fleet_health_check()` convenience wrapper concatenates both
tiers' findings in fixed order, for the natural future "give me everything"
CLI call — it is not a new code path, just composition of the two existing,
independently-tested functions.

`tests/test_fleet_health_check.py` (FHC2's own already-reviewed test file)
is **left completely untouched** — a new file,
`tests/test_fleet_health_check_review_findings.py`, covers only the new
adapter surface.

## 6. HealthFinding Contract Verdict

**Reused unchanged.** No numeric score, confidence score, ranking,
auto-repair instruction, or raw evidence blob was added anywhere. Verified
by test that no FHC3 finding is ever classified `DETERMINISTIC_ERROR` or
`NOT_CURRENTLY_DETECTABLE`, and that `DETERMINISTIC_WARNING`/
`REVIEW_REQUIRED`/`INFORMATIONAL` are kept genuinely distinct per rule
(never collapsed to one generic severity).

## 7. Pure Input Dataclasses Added

`AirportRunwayCountFact`, `InstallationAirportLinkageFact`,
`InstallationAssertionLinkRetractionFact`, `SignalLifecycleFact`,
`SignalProvenanceFact`, `SourceAssertionReviewStateFact`,
`SourceAssertionGovernanceDecisionFact` — 7 new, each narrower than the
entity it describes, containing only the fields the one rule (or small
rule group) reading it actually needs. `AirportCodeFact` and
`SignalRunwayAirportFact` reused from FHC1, not redefined.

## 8. Airport-Name Rule Result (FH-A4)

**Deliberately not implemented as an automated detector.** The committed
design's own detection method for FH-A4 is a name-token-absence check on
`Airport.name` ("absence of {Airport, Field, International, Regional,
...}") — this is, structurally, exactly the class of keyword-in-name
heuristic this mission explicitly and repeatedly prohibits as production
logic ("DO NOT create a hardcoded list of airport IDs. DO NOT special-case
the three real airports. DO NOT implement provider-specific name matching.
DO NOT use fuzzy matching."). The design's own false-positive analysis (4
of 7 real triage hits were legitimate "County Airport" names) already
proved this detection method unsafe as a standalone automated classifier —
which is precisely why the design classifies it `REVIEW_REQUIRED` rather
than any deterministic tier. Given the mission's own explicit fallback
instruction — "If current structured data cannot distinguish organization
name from legitimate airport name without textual heuristics, preserve the
REVIEW_REQUIRED/manual boundary" — FH-A4 remains a reconnaissance/manual-
review catalogue entry only, exactly like the 3
`NOT_CURRENTLY_DETECTABLE` rules: documented here, never fabricated as a
function. No hardcoded airport-ID list, no provider-specific matching, and
no fuzzy matching exist anywhere in this codebase as a result of this task.

## 9. Installation-Pattern Result (FH-C3)

**Implemented exactly as reviewed.** Trigger: `airport_id` shared by ≥2
`Installation` rows, both `runway_id` and `runway_end` NULL for each.
Multiple `Installation` rows at one airport are **not** automatically
flagged — only the specific structural shape (both link fields absent) the
design names. Tested explicitly that legitimate multi-installation
patterns (distinct `runway_id` per row, or distinct free-text `runway_end`
per row) never escalate. Wording states the structural pattern
("neither a canonical runway link nor a free-text runway_end value"),
never "duplicate installation." Real result: **18 findings**, exact
airport-ID match to the original reconnaissance (3, 4, 12, 13, 14, 27, 30,
38, 40, 41, 42, 47, 50, 59, 62, 69, 70, 71).

## 10. Signal D3/D4 Result

**Both implemented, reusing FHC1's own `SignalRunwayAirportFact` type — no
new adapter query.** FH-D3 (shared `runway_id`, an R1 anchor field) and
FH-D4 (airport-only co-location, deliberately weak evidence) both remain
`REVIEW_REQUIRED`, never claim "duplicate," and use wording explicitly
framed as "candidate for human review" / "co-located Signals" / "possible
existing-signal relationship." Real result: FH-D3 = 0 findings; FH-D4 =
**12 groups**, exact match to the original reconnaissance's "12 groups / 30
Signals."

## 11. MSP-Shaped Synthetic Result

A dedicated synthetic fixture
(`test_msp_shaped_two_signal_colocation_generic_wording`,
`test_msp_shaped_governed_fixture_produces_review_required_not_confirmed_duplicate`)
reproduces the #41/#67 structural shape (one legacy Signal, one governed
Signal, both airport-only at the same synthetic airport) using fully
synthetic IDs and asserts the summary text contains none of "MSP", "41",
"67", "FAA", "Runway Safe" — production code is generic. Confirmed directly
by AST-identifier check that `fleet_health_review_rules.py` contains no
provider/MSP-specific identifier at all. The real pair was **not**
hardcoded anywhere; §29 confirms it surfaced naturally from the real DB
smoke test as one of the 12 real D4 groups (airport 45: signals `(41, 67)`).

## 12. Temporal E1/E2 Result

Both implemented on a new, combined `SignalLifecycleFact` (also serving
E4). `construction_start` is deliberately absent from this fact type —
verified by test — so E3 (FHC1's own hard rule, unchanged) can never be
touched or duplicated here. NULL semantics preserved exactly (either side
NULL never fires); no current wall-clock time is used anywhere (E1/E2/E4
compare only persisted fields to each other or to a literal string). A
dedicated multi-phase regression fixture reproduces Signal #3's own real
shape (`planning_year=2026, procurement_year=2025` on an "under
construction" Signal) and asserts it classifies `DETERMINISTIC_WARNING`,
never `DETERMINISTIC_ERROR`. Real result: E1 = **1 finding** (Signal #3,
exact match); E2 = 0 findings (exact match).

## 13. Provenance F-Rule Result

FH-F1, FH-F2, FH-F3 all freshly reconstructed from the committed design
(not from any prior summary). Legacy provenance is never treated as an
error: F1 and F2 are `INFORMATIONAL`, F3 (a *reviewed*-but-unattributed
row — materially more concerning than F2's "still pending" state) is
`REVIEW_REQUIRED`. **F1 is emitted as ONE bucketed finding, not one per
Signal** — a direct, explicit instruction embedded in the design's own
text for this rule ("tracked as legacy-provenance-tier bucketing, not a
per-row finding"), and independently required by this mission's own §3
warning against turning a model-capability observation into per-row
placeholder findings. A dedicated test proves a 67-signal-shaped input
never produces more than one finding. F2/F3 remain per-row (both naturally
low-count by definition; no equivalent bucketing instruction exists for
them in the design). Real result: F1 = **1 finding covering exactly 67
signal IDs** (all of 1–68 except 67, the one governed Signal — exact match
to "67/68 legacy-provenance-only"); F2 = **5 findings** (assertion IDs
71–75, exact match); F3 = 0 findings (exact match — all 5 real
airport-NULL assertions are unreviewed, none reviewed).

## 14. Governance G1 Result

Re-read `human_review_queue.py` fresh before implementation (per mission
§12). FH-G1 covers exactly the two `invariant_warnings` checks NOT already
owned by FHC1's G2/G3 (`identity_guard_decision`/`intelligence_review_decision`
mismatch at `promotion_policy_decision=HUMAN_REVIEW_REQUIRED`) — reused in
spirit (same expected literal values, mirrored not imported, matching
FHC1's own REVIEWER_ACTIONS-mirroring precedent), never by importing
`human_review_queue.py` itself (which carries a `Session` dependency this
pure module must not have). A dedicated test proves G1 fires from fields
G2/G3 never see (no `latest_action`/`duplicate_of_signal_id` in a G1
finding's evidence) — the three governance rules operate on disjoint fact
shapes and can never be conflated. Real result: 0 findings (exact match).

## 15. Zero-Runway Result (FH-A1)

Implemented exactly as reviewed: unconditional `INFORMATIONAL` for any
Airport with zero `Runway` rows — the design's own "would need to be
re-escalated to REVIEW_REQUIRED only if such an airport ever also carried
a Signal" is a documented *future* consideration, not part of the current
reviewed catalogue, and was **not** implemented as a new conditional rule
(inventing one would repeat exactly the conditional-classification mistake
the FHC1 design-review checkpoint already found and corrected for FH-F2).
Wording avoids "missing"/"broken." Real result: **10 findings**, exact ID
match (airports 76–85).

## 16. Vocabulary/Model-Gap Result

No FH rule ID in the 32-row catalogue corresponds to "Signal category/
status/confidence vocabulary drift" — the design documents this exclusively
as a §2 model-capability observation ("weak/overloaded fields"), never as a
numbered rule. Per the mission's own explicit fallback ("If this is merely
a model-gap observation in the design, do not promote it to a detector"),
**no detector was implemented for this.**

## 17. NOT_CURRENTLY_DETECTABLE Handling

FH-H1, FH-I1, FH-I2 are represented **only** in this report's catalogue
(§2, §4) — there is no function, fact dataclass, or `HealthFinding`
anywhere in this codebase claiming to detect any of them. No evaluator can
accidentally "detect" one, because none exists to call.

## 18. Why B3/B4/B5 Were Deferred

Not discussed in any of the mission's own detailed per-rule guidance
sections (§6–§14 cover A1, A4, C3, D3/D4, E1/E2, F1/F2/F3, G1 explicitly;
B3/B4/B5 appear nowhere in that list) — read as a deliberate signal that
this slice does not require them. Independently, each carries materially
higher implementation risk than the implemented 13:

- **FH-B3** requires parsing a numeric heading out of `Runway`/`RunwayEnd`
  designation text (handling L/R/C suffixes) — new text-interpretation
  logic this task chose not to introduce under time pressure, even though
  it could in principle reuse `app.services.runway_identity`'s
  already-reviewed pure functions in the adapter layer.
- **FH-B4** requires the same `runway_identity.normalize_end()`/
  `normalize_pair()` reuse, one layer removed from a pure-DB-state
  comparison.
- **FH-B5** is **architecturally out of reach for this adapter**: it
  requires cross-referencing `app.services.runway_inventory`'s own
  external-NASR-CSV-derived batch classification (`PARTIAL_MATCH`), not
  current database state alone. FHC2's entire adapter contract is
  "map current database state" — there is no external-file input anywhere
  in its design, and adding one would be a materially different kind of
  adapter, not an extension of this one.

A future slice can implement B3/B4 by extending FHC2 to call
`runway_identity`'s existing functions in the adapter (never re-parsing
text inside the pure core); B5 would need a genuinely new adapter shape
that also reads NASR import state, out of scope for any DB-only slice.

## 19. Dedup Verdict

Every FHC3 evaluator inherits FHC1's exact `_dedupe_preserve_order()`
discipline (kept as an independent, small, private copy — not imported, so
this module never depends on FHC1's private internals). Verified by
dedicated duplicate-input-row tests for every batch/per-record rule (FH-A1,
FH-C3, FH-D3, FH-D4, FH-F1). **Adapter queries are also independently
dedup-safe by construction**, not merely relying on the evaluator-level
defense as a bandage: every FHC3 join is either a to-one FK (same
fan-out-immune shape FHC2's review checkpoint already established) or an
explicit Python-side reduction over ordered history
(`_build_installation_assertion_link_retractions`, mirroring
`existing_signal_reconciliation_candidates.py`'s own
`_latest_installation_links_by_assertion_id()` pattern). A dedicated
adversarial fixture (3 InstallationAssertionLinks across 3 separate
assertions) confirms no false retraction is manufactured; a 3-Signal
same-airport-same-runway fixture confirms one D3 group of 3, not three
separate pairwise findings.

## 20. Determinism Verdict

Sound. Every grouping rule sorts `entity_ids` (`tuple(sorted(...))`) before
emitting a finding — content is independent of input order, verified
directly (FH-C3, FH-D4 order-reversal tests) and at the adapter level
(opposite DB insertion order produces identical `run_fleet_review_check()`
output). No `set` iteration, no reliance on SQLite's implicit row order —
every adapter query is explicitly `ORDER BY <primary id> ASC`.

## 21. No-Score/Ranking Verdict

Confirmed structurally: AST-identifier scan finds no `score`, `rank`,
`weight`, `probability`, or `threshold` identifier anywhere in
`fleet_health_review_rules.py`. `Signal.confidence` is read nowhere in this
module (no rule needs it); `Signal.status`/`category` are read only as
exact literal-string comparisons (FH-E4's `status == "completed"`), never
converted into a count-based or weighted decision. No rule anywhere uses
"N similarities implies M severity" logic of any kind.

## 22. R1–R4 Authority-Boundary Verdict

Confirmed by direct AST check: no identifier for
`record_reviewer_action`, `create_signal_from_approved_review`,
`link_source_assertion_to_duplicate_signal`, or
`record_reconciliation_decision` exists anywhere in
`fleet_health_review_rules.py` or the FHC2 extension. FH-D3/FH-D4/FH-C4
only ever *surface* a candidate (`REVIEW_REQUIRED`) — they never compute or
reference `POSSIBLE_EXISTING_SIGNAL_MATCH`/`CLEAR_TO_CREATE`/
`ALREADY_LINKED` (R1's exclusive authority) and never create a
`ReviewerAction`, link a `SourceAssertion`, or create a `Signal` (R4's
exclusive authority).

## 23. DB Adapter Changes

`app/services/fleet_health_check.py` extended (append-only) with: 7 new
`_build_*` query functions, `build_fleet_review_snapshot()`,
`run_fleet_review_check()`, `run_full_fleet_health_check()`. Every existing
FHC1-adjacent function/line is byte-for-byte unchanged (verified by `git
diff` — the diff is a pure addition, no line inside the original functions
was touched).

## 24. Query/Cardinality Result

10 queries for `run_fleet_review_check()` alone (measured directly,
independent of entity count at 3 vs. 30 synthetic entities): 1 each for
`airport_runway_counts`, `airport_codes` (reused), `installation_airport_linkages`,
`installation_assertion_link_retractions`, `signal_runway_airports`
(reused), `signal_lifecycles`, `source_assertion_review_states`,
`source_assertion_governance_decisions`, plus 2 for `signal_provenance`
(one for Signals, one bounded distinct-id lookup for linked assertions).
**Reuse of `_build_airport_codes`/`_build_signal_runway_airports` is
code-level (no duplicate query function), not result-level** — calling
`run_fleet_hard_invariant_check()` and `run_fleet_review_check()`
separately (or via `run_full_fleet_health_check()`) issues each shared
query twice, since the two snapshots are built independently. Measured
directly against the real database: **21 total queries** for hard+review
combined (11 hard, matching FHC2's own documented "11 when ReviewerActions
exist" behavior, + 10 review). This is stated plainly here rather than
implying a lower number the "reuse" framing might suggest — a legitimate,
documented tradeoff favoring independent testability of the two tiers over
a marginal query-count reduction.

Every new join is a to-one FK (same fan-out-immune shape as FHC2's
original 10) except the one Python-side reduction
(`_build_installation_assertion_link_retractions`), which explicitly
groups-then-reduces rather than relying on SQL-level uniqueness.

## 25. No-Autoflush Result

`build_fleet_review_snapshot()` wraps its entire body in
`session.no_autoflush`, identical to FHC2's own review-checkpoint fix.
Re-verified directly: a caller's pending, uncommitted `Airport.name` edit
remains exactly pending (never flushed) both when calling
`run_fleet_review_check()` alone and when calling
`run_full_fleet_health_check()` (the combined hard+review path) — a
dedicated new regression test
(`test_full_health_check_never_autoflushes_callers_pending_mutation`)
covers the combined call specifically, since that is the one path FHC2's
own original autoflush test could not have covered (it didn't exist yet).

## 26. False-Positive Attack Results

All attacks listed in mission §25 were constructed:

- **A/B (legitimate "County" / authority-like naming)**: not applicable to
  any implemented rule — FH-A4 (the only rule that would ever read
  `Airport.name`) is not implemented (§8); no fact dataclass in this module
  has a `name` field at all, verified by test.
- **C (multiple legitimate Installations)**: FH-C3 confirmed to skip rows
  with distinct `runway_id`/`runway_end` values (§9).
- **D (separate Signals, same airport/runway, different real projects)**:
  D3/D4 both correctly classify `REVIEW_REQUIRED`, never assert
  duplication — verified real-world by the 10-of-12-legitimate D4 groups
  in §29/§30.
- **E (multi-phase project dates)**: Signal #3-shaped regression fixture
  classifies `DETERMINISTIC_WARNING`, never `ERROR` (§12).
- **F (legacy Signal, legacy-only provenance)**: FH-F1 classifies
  `INFORMATIONAL`, bucketed (§13).
- **G (zero-runway airport)**: FH-A1 classifies `INFORMATIONAL` (§15).
- **H (same title, no structural relationship)**: `title` is structurally
  absent from every FHC3 fact dataclass — verified by test.
- **I (same vendor/category/year, no anchor)**: `vendor`/`category`/
  `confirmed_vendor`/`likely_supplier` are structurally absent from every
  FHC3 fact dataclass — verified by test.
- **J (international naming patterns)**: synthetic Sweden/Japan fixtures
  behave identically to any other fixture — no country/provider-specific
  branch exists anywhere in either new module.
- **K (Unicode names)**: moot by construction — no fact dataclass has
  anywhere to put a name at all (verified by test), so there is no
  Unicode-handling code path to attack in the first place.
- **L (duplicated adapter rows)**: covered exhaustively in §19/§24's fan-out
  attacks.

## 27. Structured-Evidence Verdict

Every finding's `structured_evidence` contains only already-persisted
scalar values or small ID tuples (entity IDs, `airport_id`, `runway_id`,
years, `completion_date`, counts, literal `outcome`/`status`/`decision`
strings) — no raw source text, no full ORM serialization, no giant nested
object. Evidence key sets are fixed per rule (not input-order-dependent);
entity-ID tuples inside evidence are the same pre-sorted tuples used for
`entity_ids`.

## 28. International Verdict

Confirmed: no US/provider-specific assumption anywhere in either new
module (AST-checked for MSP/MAC/FAA/USAspending/RunwaySafe identifiers);
synthetic Sweden/Japan fixtures produce identical adapter behavior to any
domestic fixture.

## 29. Real DB Grouped Findings

Read-only, via the established `mode=ro` SQLite URI pattern. Pre-check
hash `4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`
(1,794,048 bytes) — matched the expected checkpoint exactly.

| Rule | Classification | Count |
|---|---|---|
| FH-A1 | INFORMATIONAL | 10 |
| FH-C3 | DETERMINISTIC_WARNING | 18 |
| FH-D4 | REVIEW_REQUIRED | 12 |
| FH-E1 | DETERMINISTIC_WARNING | 1 |
| FH-E4 | REVIEW_REQUIRED | 1 |
| FH-F1 | INFORMATIONAL | 1 (bucketing 67 signal IDs) |
| FH-F2 | INFORMATIONAL | 5 |

**48 total review findings**, zero duplicates (grouped counts sum exactly
to the flat total). A3, C4, D3, E2, F3, G1 all correctly produced **zero**
findings, matching the reconnaissance's own "0 real hits" expectation for
every one of them exactly.

## 30. Representative Real Findings

- **FH-A1**: airports 76–85, exact ID match to the original reconnaissance's
  10 zero-runway airports.
- **FH-C3**: airport IDs {3, 4, 12, 13, 14, 27, 30, 38, 40, 41, 42, 47, 50,
  59, 62, 69, 70, 71}, exact match to the original 18-airport list.
- **FH-D4**: 12 groups; airport 45's group is `(41, 67)` — **the real
  Signal #41/#67 pair surfaced naturally**, with zero hardcoding anywhere
  in the rule that produced it (confirmed by the AST identifier scan in
  §22/§28). Ten other groups (airports 3, 6, 9, 13, 19, 31, 37, 39, 44, 63,
  72) also appeared, consistent with the original reconnaissance's "12
  groups / 30 Signals."
- **FH-E1**: Signal #3, exact match.
- **FH-E4**: Signal #65, exact match.
- **FH-F1**: one finding covering signal IDs 1–68 except 67 (67 total),
  exact match to "67/68 legacy-provenance-only."
- **FH-F2**: SourceAssertion IDs 71–75, exact match to the original
  5-assertion list.

## 31. Discrepancies From Reconnaissance

**None.** Every implemented rule's real hit count and, where checked, exact
entity-ID set matches the original reconnaissance task's findings
precisely — including the airport-ID lists for A1 and C3, the assertion-ID
list for F2, and the signal-ID composition of F1's single bucketed
finding. The two reconnaissance findings this slice does **not** surface
(the 3 airport-name-leak cases, FH-A4; the "10 zero-runway airports" is
covered, but the airport-name pattern is not) are exactly the ones this
report documents as deliberately deferred (§8), not a detection gap.

## 32. Real DB Unchanged Proof

Post-check hash `4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
size 1,794,048 bytes, mtime `1787237717.1444063` — **identical** to the
pre-check values, both from this task's own probing and unchanged from the
FHC2 review checkpoint's own last-recorded state.

## 33. Defects/Corrections Found

None in FHC1 or FHC2 during this task (both re-run unmodified; all 116 + 42
pre-existing tests still pass). Test-fixture-only issues were found and
fixed in this task's **own** new test files before being reported as
passing:

1. Several `InstallationAssertionLink` test fixtures initially omitted
   `physical_installation_id` for a `SAME_PHYSICAL_INSTALLATION` outcome,
   violating the real `ck_installation_assertion_links_target_for_resolved`
   CHECK constraint (any outcome other than `UNRESOLVED` requires a real
   target) — fixed by creating a real `PhysicalInstallationIdentity` row
   and passing its id.
2. Two isolation tests (`test_fh_a1_zero_runway_airport`, part of
   `TestLegacyRealisticFixture`) initially built airports with no code at
   all, which correctly and independently triggered FH-A3 alongside the
   intended rule — not an adapter defect, an incomplete test fixture. Fixed
   by giving the relevant airports an ICAO code to isolate the intended
   rule.
3. An over-strict combined-fixture assertion (`len(rule_ids) ==
   len(REVIEW_RULE_IDS)`) incorrectly assumed every rule fires exactly
   once; FH-A3 legitimately fires once per code-less airport and FH-D4
   legitimately groups multiple co-located Signals into one finding —
   both correct behavior, not duplication. Corrected to the semantically
   right check (`set(rule_ids) == set(REVIEW_RULE_IDS)`).

**Explicit no-remediation statement**: no finding produced by this slice —
real or synthetic — was acted upon in any way. No `ReviewerAction` was
created, no `Signal`/`Airport`/`Installation`/`SourceAssertion` row was
modified, no reconciliation decision was made, and no CLI or automated
follow-up was triggered. Every REVIEW_REQUIRED finding remains exactly
that: a surfaced candidate for a human, routed through the existing,
unmodified R4 workflow if and when a human chooses to act on it.

## 34. Focused Test Result

`tests/test_fleet_health_review_rules.py`: **87 passed**.
`tests/test_fleet_health_check_review_findings.py`: **29 passed**.
`tests/test_fleet_health_rules.py` (FHC1, re-run unmodified): **116 passed**.
`tests/test_fleet_health_check.py` (FHC2, re-run unmodified): **42 passed**.
Combined: **274 passed**, 0 failed.

## 35. Full Pytest Result

**1940 passed**, 0 failed (1824 pre-existing baseline + 116 new FHC3 tests
— matches exactly, no regressions anywhere).

## 36. py_compile Result

Clean on all new/modified files (`fleet_health_review_rules.py`,
`fleet_health_check.py`, `test_fleet_health_review_rules.py`,
`test_fleet_health_check_review_findings.py`).

## 37. git diff --check Result

Exit 0, clean.

## 38. Exact Files Changed/New

Modified (extension only, append-only diff):
- `app/services/fleet_health_check.py`

New:
- `app/services/fleet_health_review_rules.py`
- `tests/test_fleet_health_review_rules.py`
- `tests/test_fleet_health_check_review_findings.py`
- `docs/architecture/fleet-health-check-fhc3-warning-review-informational-report.md` (this file)

Untouched: `app/services/fleet_health_rules.py`, `tests/test_fleet_health_rules.py`,
`tests/test_fleet_health_check.py`.

## 39. git status

Only the files listed in §38 (plus the same pre-existing, unrelated
untracked files present since before this task began). No commit made.

## 40. READY_FOR_FHC3_REVIEW_CHECKPOINT

**Yes.**

## 41. Exact Recommended Next Step

A fresh adversarial review checkpoint of this FHC3 implementation (per the
project's established two-phase pattern: FHC1 review → commit/push, FHC2
review → commit/push, now FHC3 review → commit/push), covering in
particular: (1) the FH-A4/B3/B4/B5 deferral decisions under adversarial
scrutiny; (2) the FH-F1 bucketing decision vs. F2/F3's per-row decision, to
confirm the distinction is well-founded and not merely convenient; (3) the
query-count/reuse framing in §24, to confirm "21 total, not fewer" is
accurately and honestly stated; (4) a fresh real-DB smoke re-run. Only
after that checkpoint should commit/push occur, followed by a CLI slice
(`scripts/run_data_health_check.py`) built on top of the now-complete
`run_full_fleet_health_check()`.

---

## Critical Review Checkpoint (RWI_FLEET_HEALTH_CHECK_FHC3_CRITICAL_REVIEW_COMMIT_PUSH)

A fresh, adversarial review of this implementation was performed
independently of the report above — every claim re-verified against the
actual code and against fresh, direct probes (including independent raw-SQL
cross-checks of the real DB), not trusted from the report's own prose.

### Scope verification (Phase 1)

Confirmed exactly the 13 intended rule IDs present, no deferred rule
executable: `hasattr`/registry checks confirm FH-A4/B3/B4/B5/H1/I1/I2 have
no evaluator function anywhere; FHC1's `fleet_health_rules.py` is
byte-for-byte unchanged (`git diff` shows zero lines touched), its own 116
tests re-run unmodified and still pass, and the real DB hard-invariant
check still returns 0 findings.

### Per-rule adversarial attack (Phase 2)

Every rule was re-attacked with the specific counterexamples the mission
named. FH-A1/C3/D3/D4/E1/E2 all held up exactly as documented in §9–§12 of
the report above — no new issue found. Two rules received closer scrutiny
than the original implementation gave them:

- **FH-C4**: attacked with an identical-`reviewed_at`-timestamp fixture (two
  `InstallationAssertionLink` rows on one assertion sharing the exact same
  timestamp) — the adapter's `(reviewed_at ASC, id ASC)` ordering correctly
  breaks the tie by id, matching
  `existing_signal_reconciliation_candidates.py`'s own established
  precedent for this exact table. This was previously verified only by
  direct probe during this review, **not** by a permanent test — fixed,
  see "Regression tests added" below.
- **FH-E4**: independently checked the rule's predicate against the
  reviewed design's literal wording ("no completion_date **and no other
  year field consistent with 'done'**") — confirmed the implementation
  deliberately omits the second clause, and confirmed this is the
  *correct* choice, not a gap: honoring that clause literally would
  require judging whether a year field is "consistent with done," which is
  only meaningful relative to the current date — exactly the
  current-date-inference dependency this project's own FH-E3 precedent and
  every mission since have explicitly forbidden. Strengthened the
  function's docstring to state this reasoning explicitly rather than
  leaving it implicit (a documentation correction, not a behavior change).

### Dedup / join fan-out (Phase 3)

Independently constructed adversarial fixtures beyond the original
suite's own: multiple `InstallationAssertionLink` rows across multiple
*different* assertions (confirmed no cross-assertion pollution — already
present); the identical-timestamp case above (new). Re-verified by direct
probe that `_build_installation_assertion_link_retractions`,
`_build_signal_provenance` (its `.distinct()` on linked Signal ids), and
every to-one-FK join in the FHC3 extension cannot fan out by construction,
independent of FHC1's own defensive dedup (FHC1's dedup was not relied
upon as evidence for this — the FHC3 adapter's own queries were checked
directly).

### Determinism (Phase 4)

Confirmed by direct object-equality check against the real database (not
merely count comparison): re-running `run_fleet_review_check()` against
the real DB and comparing the full `HealthFinding` tuples for exact
equality; zero exact-duplicate `(rule_id, entity_ids)` pairs found among
the 48 real findings.

### Information firewalls (Phase 5)

Re-verified independently via a fresh AST identifier scan (not reusing the
implementation's own test code) across both `fleet_health_review_rules.py`
and the FHC2 extension in `fleet_health_check.py`, checking every category
named in the mission (financial fields, title/vendor/provider names,
scoring/ranking/threshold identifiers, clock/random calls, write-path
imports) — **all clean**, independently confirmed.

### Adapter safety (Phase 6)

`session.no_autoflush` confirmed to cover `build_fleet_review_snapshot()`'s
entire body (already verified behaviorally by the existing autoflush
regression tests, re-read fresh and confirmed still present and correct);
no `add`/`flush`/`commit`/`delete`/`merge` identifier anywhere in the
extension (independently AST-confirmed, not reused from the
implementation's own claim); query-failure and schema-mismatch behavior
inherited unchanged from FHC2 (no new `try`/`except` was added anywhere in
the extension).

### Real-DB verification (Phase 7)

Performed fully fresh, independent of the implementation report:

- Pre-check: SHA-256 `4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
  size 1,794,048 bytes, mtime `1787237717.1444063`, `PRAGMA integrity_check`
  = `ok`, `PRAGMA foreign_key_check` = `[]` — all confirmed before any
  further access this review performed.
- Re-ran `run_fleet_review_check()` read-only: **identical result** to the
  implementation report (FH-A1=10, FH-C3=18, FH-D4=12, FH-E1=1, FH-E4=1,
  FH-F1=1 bucket/67 signals, FH-F2=5, total=48; A3/C4/D3/E2/F3/G1=0).
- **Independently cross-validated three of the counts via raw SQL**,
  bypassing the adapter/rule code entirely: C3's 18-airport count, F1's
  67-signal count, and F2's 5-assertion count all matched the adapter's own
  output exactly via hand-written SQL queries against the real tables.
- Post-check hash/size/mtime: **identical** to pre-check, both immediately
  after the review's own re-run and again after the additional raw-SQL
  cross-validation queries.
- No discrepancy from the original reconnaissance or the implementation
  report was found anywhere.

### Test quality (Phase 8)

Reviewed all 87 + 30 FHC3 tests for the specific weaknesses the mission
named. Found and fixed the one genuine gap (C4's identical-timestamp
tiebreak, above). Confirmed the existing `set(rule_ids) ==
set(REVIEW_RULE_IDS)`-style assertions in the combined fixtures are the
*correct* check, not a duplicate-hiding shortcut: several rules (FH-A3,
FH-D4) legitimately produce more than one finding from a single combined
fixture, so a stricter "exactly N findings" assertion would be wrong, not
stronger — this was verified by constructing the combined fixture by hand
and inspecting every individual finding, not merely asserting a count. No
other test was found to assert only a count where identity/evidence should
also have been checked, to use an impossible ORM fixture, or to encode a
provider-specific assumption.

### Defects found

None in production rule *logic*. One genuine test-coverage gap (FH-C4's
same-timestamp tiebreak, previously verified only by an ad hoc probe, not
a permanent test).

### Corrections made

1. Added a permanent regression test for FH-C4's identical-`reviewed_at`
   tiebreak (`test_identical_reviewed_at_timestamps_break_tie_by_id_matching_r2_precedent`
   in `tests/test_fleet_health_check_review_findings.py`).
2. Strengthened `evaluate_fh_e4`'s docstring to explicitly document why the
   design's "no other year field consistent with 'done'" clause is
   deliberately not implemented (current-date-inference conflict with this
   project's own established principle) — a documentation clarification,
   not a behavior change.

### Regression tests added

1 new test (above). No production logic was changed as a result of this
review — both corrections were either a test addition or a docstring
clarification.

### Final test totals

`tests/test_fleet_health_review_rules.py`: 87 passed (unchanged).
`tests/test_fleet_health_check_review_findings.py`: **30 passed** (29 + 1
new). Combined FHC1+FHC2+FHC3+governance focused run (`test_fleet_health_rules.py`,
`test_fleet_health_check.py`, `test_fleet_health_review_rules.py`,
`test_fleet_health_check_review_findings.py`, `test_reviewer_action_persistence.py`,
`test_human_review_queue.py`): **394 passed**, 0 failed. Full suite:
**1941 passed**, 0 failed (1940 pre-review total + 1 new regression test).

### READY_FOR_FHC4

Not applicable to this checkpoint's own scope (FHC3 review, not FHC4
planning) — see the Final Report's own FHC4 recommendation for that
question, answered fresh in this review's own final report rather than
repeated here.

RWI_FLEET_HEALTH_CHECK_FHC3_WARNING_REVIEW_INFORMATIONAL_COMPLETE
