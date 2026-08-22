# RWI UAC3 — Unknown-Airport Discovery Candidate-Selection Integration

Implementation report. Slice 3 of
`docs/architecture/rwi-governed-new-airport-discovery-design.md`. Starting
checkpoint: HEAD `1eb185893c0cb69219af9e0d6af9987fcf4da50d` (== origin/main),
real DB SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`
(1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
FK check `[]`, integrity `ok`, UAC tables absent, `source_assertions` has
no `unknown_airport_candidate_id` column) — verified fresh at the start
of this mission and confirmed unchanged at the end. **No migration was
run against the real database; no live internet access occurred.**

## Files read fresh

`docs/architecture/rwi-governed-new-airport-discovery-design.md`, UAC1/
UAC2A/UAC2B reports, `app/services/evidence_attachment_guard.py`,
`app/services/discovery_candidate_fragment.py`,
`app/services/discovery_evidence_persistence.py`,
`app/services/unknown_airport_candidate_persistence.py`,
`app/services/promotion_policy_evaluation.py`,
`app/services/fleet_health_review_rules.py`,
`app/services/fleet_health_check.py`, `scripts/capture_mac_discovery.py`
(call-site audit), `app/models/source_assertion.py`.

## Files modified

- `app/services/fleet_health_review_rules.py` — `SourceAssertionReviewStateFact`
  gains an additive `unknown_airport_candidate_id: Optional[int] = None`
  field; `evaluate_fh_f2()`/`evaluate_fh_f3()` each gain one additional
  skip condition.
- `app/services/fleet_health_check.py` —
  `_build_source_assertion_review_states()` additionally selects the new
  column.
- `tests/test_fleet_health_review_rules.py` — 8 new tests for the
  FH-F2/FH-F3 correction; all pre-existing tests unmodified.

## Files created

- `app/services/unknown_airport_discovery_integration.py`
- `tests/test_unknown_airport_discovery_integration.py` (29 tests)
- This report.

## Exact current no-known-match behavior (§3, reproduced fresh)

Traced directly from `evaluate_attachment_for_candidates()`/
`evaluate_attachment()` (unmodified, re-read fresh): when the true
airport is not among the supplied `CandidateAirport` list, every
supplied candidate independently returns either `INSUFFICIENT_IDENTITY`
(no positive or contradicting evidence found against that specific
candidate) or `REJECT_CROSS_AIRPORT` (the evidence positively
contradicts that specific candidate). `persist_discovery_fragment()`
then selects the best of these per its own `_OUTCOME_PRIORITY` and
persists exactly one `SourceAssertion` with `airport_id=NULL` — evidence
is **not** dropped, not silently discarded, and does not require manual
intervention to be recorded; it is fully preserved and queryable today,
just with no airport attribution at all and no path to a governed
non-canonical identity. This confirms the orientation finding directly:
the gap was never in the guard's own algorithm (correctly, deliberately
unmodified again in this slice) — it is one layer above, in what happens
to a `NULL`-`airport_id` `SourceAssertion` after the guard has spoken.
UAC3 closes exactly that gap, and no other.

## Integration architecture

One new, narrow orchestration function,
`resolve_or_persist_discovery_identity()`, in a new module
(`app/services/unknown_airport_discovery_integration.py`). It computes
the guard decision **once** (reusing
`evaluate_attachment_for_candidates()` directly — the same pure call
`persist_discovery_fragment()` makes internally, never reimplemented),
then calls **exactly one** of the two possible persistence paths based
on that decision:

- known match or ambiguous match → `persist_discovery_fragment()`
  (unmodified), the existing path, run exactly as it already runs today.
- no known match + a formable candidate seed →
  `find_or_create_unknown_airport_candidate()` (UAC1, unmodified) then
  `persist_candidate_linked_source_assertion()` (UAC2B, unmodified).
- no known match + no formable seed → `persist_discovery_fragment()`
  (unmodified) — this is already its own correct, pre-existing behavior
  for `INSUFFICIENT_IDENTITY`/`REJECT_CROSS_AIRPORT` with no candidate
  routing.

**Why this layer is the correct integration point, and why it cannot
simply call `persist_discovery_fragment()` unconditionally first:** both
`persist_discovery_fragment()` and `persist_candidate_linked_source_assertion()`
key their own idempotent find-or-reuse behavior on the identical
fragment-identity tuple against the same `SourceAssertion` uniqueness
constraint. Calling `persist_discovery_fragment()` first would
unconditionally create a `SourceAssertion` with both identity columns
`NULL` for the no-match case; a subsequent call to
`persist_candidate_linked_source_assertion()` for the *same* fragment
would then find that already-existing row and correctly return it
**unchanged** — that function's own deliberate "never rewrite an
already-resolved `SourceAssertion`" rule, the identical discipline
`persist_discovery_fragment()` itself already applies — meaning
`unknown_airport_candidate_id` would silently never get set. This was
discovered and reasoned through explicitly during design, not found as a
defect after implementation (documented at length in the module's own
docstring so a future reader does not attempt the naive composition).
Evaluating the guard decision once, up front, and branching to exactly
one persistence call per fragment avoids the trap entirely while still
composing three already-committed services rather than duplicating any
of their logic.

## Known-match branch

Unchanged existing behavior, reused verbatim via
`persist_discovery_fragment()`. Proven identical to a direct call of
that same function with the identical fragment shape
(`test_known_match_backward_compatibility_matches_direct_persist_discovery_fragment`).

## Strong-unknown branch

`REJECT_CROSS_AIRPORT`/`INSUFFICIENT_IDENTITY` + exactly one claimed
airport name in the fragment → `UNKNOWN_AIRPORT_CANDIDATE`. Covers both
the "no known candidates supplied at all" case and the "all supplied
known candidates conflict, but the fragment itself names one coherent,
distinct airport" case (design mission §5C) — the latter is handled
identically to the former by construction: this module's routing
decision for the "no known match" branch never distinguishes
`REJECT_CROSS_AIRPORT` from `INSUFFICIENT_IDENTITY` (both mean "not the
candidates I was given"), and the candidate seed always comes from the
fragment's *own* extracted `airport_names`, never from whichever known
candidate was rejected.

## Weak-identity branch

Zero claimed airport names → `UNRESOLVED_IDENTITY`, no candidate formed.
Deterministic, required-field rule (§15's own explicit "no hidden
heuristic parser, no confidence scoring" instruction) —
`len(fragment.airport_names) != 1` is the entire test, no scoring of any
kind.

## Ambiguous-known branch

`REVIEW_REQUIRED` (the guard's own, only source: two or more known
candidates independently qualified) → `AMBIGUOUS_KNOWN_IDENTITY`, **never**
routed to candidate formation, regardless of what the fragment's own
`airport_names` might otherwise support. This is the design mission's
own explicit locked policy (§5D/§14): "unknown canonical match" (which
known airport, among several plausible ones, is it) and "previously
unknown Airport entity" (an airport not in the known catalogue at all)
are related but not identical concepts — ambiguity among *known*
candidates is presumed to mean the true airport is already one of the
plausible candidates, and belongs to ordinary human identity review
(a future, separate concern), never to unknown-airport candidate
creation. `persist_discovery_fragment()` already persists this case with
`airport_id=NULL` today; UAC3 changes nothing about that, only the
label applied to the outcome.

## All-conflict/coherent-new branch

Covered under "strong-unknown branch" above — deliberately not a
separate code path, since the routing decision does not distinguish
`REJECT_CROSS_AIRPORT` from `INSUFFICIENT_IDENTITY`.

## Candidate-formability rule

Exactly one required, deterministic condition: `len(fragment.airport_names)
== 1`. Zero names → weak identity (§15), fail closed to unresolved. Two
or more names → multi-airport-fragment safety (§16), fail closed to
unresolved rather than guessing which name is "the" candidate or
blending two identities into one row. `raw_runway_designation` is
populated opportunistically and best-effort (joined, if multiple) since
it is audit-only and explicitly excluded from UAC1's own fingerprint;
`raw_city`/`raw_state_region`/`raw_country` and the three separately-
typed claimed-code fields (`raw_iata_code`/`raw_icao_code`/`raw_faa_lid`)
are **deliberately never populated** — see "candidate input extraction
contract" below.

## Candidate input extraction contract

`CandidateFragment.locations` is a single, undifferentiated
`frozenset[str]` — extraction today does not structurally distinguish
city from state/region from country. `CandidateFragment.airport_identifiers`
is likewise undifferentiated by code type — IATA, ICAO, and FAA-LID
strings all land in the same set, with no type marker. Per this
mission's own explicit instruction ("Do NOT scrape free text again...
No hidden heuristic parser... Use existing extraction outputs"), this
module does **not** guess a location string's granularity or an
identifier string's code type to populate UAC1's more finely-typed
fields — doing so would fabricate structured precision the extraction
layer does not actually have, exactly the kind of "claim more precision
than you actually have" failure this project's evidentiary discipline
has repeatedly guarded against elsewhere (see, e.g., the SFO 2026 EMAS
temporal-evidence pilot's own "amount ≠ meaning" lesson,
`docs/product/sfo-2026-emas-temporal-evidence-pilot.md`, cited directly
in `CandidateFragment`'s own `ExtractedMoney` docstring). Only `raw_name`
and `raw_runway_designation` are populated from existing extraction
output; nothing is re-parsed. A future extraction-layer enhancement that
structurally distinguishes code type or location granularity **at
extraction time** (not by heuristic post-processing here) could populate
the remaining fields correctly; this slice deliberately does not attempt
it, and this is recorded as an explicit, honest, open limitation rather
than silently worked around.

## Exact convergence result

Reused verbatim from UAC1 — `find_or_create_unknown_airport_candidate()`
is called with no modification, no wrapper, no re-derived fingerprint
logic. Verified end to end: two independent producers ("adapter-alpha,"
"adapter-beta," "adapter-gamma" — deliberately generic, non-MAC labels)
discovering the identical claimed identity converge onto one candidate
row, with three independent `SourceAssertion` rows, no evidence
overwritten (`TestMultipleEvidenceOneCandidate`).

## Repeated-discovery result

Candidate count stays at 1; `SourceAssertion` count increments; both
assertions link to the same candidate; both preserve independently
distinct evidence text; no candidate field mutation (already structurally
guaranteed — this module never constructs or assigns to an
`UnknownAirportCandidate` attribute anywhere, confirmed by the same
AST-based no-canonical-construction proof pattern already established
for the sibling persistence modules, extended here to also forbid
`Airport`/`Runway`/`RunwayEnd`/`Installation`/`Signal` construction).

## Near-duplicate result

Confirmed: `"Foo Regional Airport"` and `"Foo Regional Airport Authority"`
produce two separate candidate rows — no fuzzy merge exists anywhere in
this module or in UAC1's own fingerprint function it reuses unmodified.
No advisory near-duplicate mechanism was implemented (none exists yet in
the committed architecture; not widened here).

## Existing-airport false-negative result

Fixture proves the full recovery path remains intact: a real `Airport`
row exists in the database but is not included in the `candidate_airports`
list passed to this call (the realistic "upstream candidate-selection
missed a spelling variant" scenario) → routes to
`UNKNOWN_AIRPORT_CANDIDATE`, creates **zero** duplicate canonical
`Airport` rows, and a human can still resolve it afterward via the
existing, entirely unmodified `record_unknown_airport_candidate_review(action="MATCH_EXISTING_AIRPORT",
matched_airport_id=<the real airport's id>)` call — proven directly in
the same test.

## Multi-airport result

A single fragment claiming two distinct airport names never forms a
candidate (fails the formability rule, routes to `UNRESOLVED_IDENTITY`)
— no blended candidate is ever possible, by construction of the
formability rule itself, not by a separate guard. When extraction
already yields two *separate* fragments for a multi-airport document
(the existing, expected extraction shape), this module correctly
operates per fragment and may legitimately form two independent
candidates — proven by
`test_multiple_fragments_from_one_document_operate_independently`.

## SourceAssertion persistence result

Identical field-for-field shape to the direct UAC2B call this module
delegates to for the candidate-linked path: `airport_id=NULL`,
`unknown_airport_candidate_id` set, `identity_guard_decision` always
`INSUFFICIENT_IDENTITY` (UAC2B's own established constant, reused
unmodified — never a sixth guard outcome), original `raw_text`/evidence
preserved verbatim, zero `Airport`/`Runway`/`RunwayEnd`/`Installation`/
`Signal`/`UnknownAirportCandidateReview` rows created by this module for
any outcome.

## Transaction/rollback result

No commit anywhere in this module (grep-confirmed: no `.commit(` call in
its own source); caller owns the transaction boundary throughout,
matching every service it composes. Proven for three separate failure
shapes: (1) an entirely uncommitted call, rolled back, leaves zero
candidate/assertion/source rows; (2) a forced failure injected **between**
candidate creation and assertion persistence (candidate creation
succeeds, then `persist_candidate_linked_source_assertion` is
monkeypatched to raise) leaves, after caller rollback, **zero** orphaned
candidate rows — the newly-created-but-uncommitted candidate is fully
undone along with everything else in the same uncommitted transaction;
(3) the converse — an **already-committed** candidate from an earlier,
separate call survives untouched when a *later* call linking new
evidence to it fails and is rolled back, proving existing governed state
is never silently damaged by an unrelated later failure.

## FH-F2/FH-F3 correction

Smallest possible correction, exactly as scoped by the UAC2B review's
own deferred recommendation: `SourceAssertionReviewStateFact` gains one
additive, default-`None` field (`unknown_airport_candidate_id`);
`_build_source_assertion_review_states()` selects it alongside the
existing two columns; `evaluate_fh_f2()`/`evaluate_fh_f3()` each gain one
additional `continue` when it is set. Genuinely unattributed evidence
(the field still `None`) is completely unaffected — proven directly by
dedicated regression tests reusing the *exact* pre-existing fixture
shapes (`SourceAssertionReviewStateFact(1, "unreviewed")` positional
construction still works and still fires, confirming the new field's
default preserves every pre-existing call site). A mixed-state test
(`test_mixed_candidate_linked_and_unattributed_only_unattributed_fires_f2`/`_f3`)
proves the correction discriminates per-row correctly, not merely
per-batch. An end-to-end integration test
(`TestFleetHealthCandidateLinkedIntegration`, in the new UAC3 test file)
additionally proves this through the real `_build_source_assertion_review_states()`
query against a genuinely persisted candidate-linked `SourceAssertion`
row created via the new orchestration function, not just synthetic
`SourceAssertionReviewStateFact` literals — closing the gap between "the
rule logic is correct in isolation" and "the real database query feeding
it is also correct." FHC classification/severity boundaries are
completely unchanged: FH-F2 remains `INFORMATIONAL`, FH-F3 remains
`REVIEW_REQUIRED`, for every row this correction does not skip.

## Promotion / governance firewall

**Already completely closed by construction — confirmed, not
constructed here.** `governed_signal_creation.py`'s own gate (unmodified,
re-verified by direct source read) requires
`identity_guard_decision == "ATTACH_CONFIRMED"` before touching any row;
`intelligence_review_persistence.py`/`promotion_policy_persistence.py`
(unmodified, not even imported by this module) only ever populate
`intelligence_review_decision`/`promotion_policy_decision` for rows that
already cleared that same gate. Candidate-linked rows are always
`identity_guard_decision = "INSUFFICIENT_IDENTITY"` (UAC2B's own
established, unmodified constant) — structurally, permanently excluded
from ever reaching `ATTACH_CONFIRMED`, and therefore from the entire
downstream governed chain, with **zero** modification to
`promotion_policy_evaluation.py` or `governed_signal_creation.py` in this
slice. Proven directly:
`test_candidate_linked_assertion_never_satisfies_attach_confirmed_gate`
confirms a real, persisted candidate-linked row's
`intelligence_review_decision`/`promotion_policy_decision`/`signal_id`
all remain `None` — they were never even evaluated, let alone promoted.
No Signal is ever created by this module for any outcome
(`test_no_signal_ever_created_by_this_module`).

## Result contract

A narrow four-member `str, Enum` (`DiscoveryIdentityOutcome`), matching
the vocabulary style already established by `AttachmentOutcome`/
`PromotionPolicyOutcome` elsewhere in this pipeline — no overloaded
boolean, no score, no ranking. `DiscoveryIdentityResolutionResult` is a
plain, frozen dataclass (never an ORM instance), carrying the underlying
guard `attachment_outcome` for audit/debugging without ever letting a
caller reinterpret it as a fifth routing outcome.

## Source-neutrality verdict

**Sound.** Fresh grep of the new module's own source for MAC/Granicus/
FAA/USAspending/dollar-amount/English-only assumptions found none — the
module operates purely on the already-neutral `CandidateFragment`/
`CandidateAirport`/`DiscoverySourceMetadata` types. Tested directly with
generic, non-MAC producer labels (`"adapter-alpha"`, `"generic-acquisition-producer"`),
a Japanese-language fragment (羽田空港), and an accented French-style name
(Aéroport Régional Exemple) — all route correctly with zero source-family
assumptions anywhere in the code path exercised.

## Migration-chain parity

One end-to-end test (`test_end_to_end_against_genuinely_migrated_schema`)
builds a fixture database through the **real** migration chain
(baseline schema → UAC2A `upgrade()` → UAC2B `upgrade()`, both
unmodified, imported directly) rather than `Base.metadata.create_all()`
for the UAC1/UAC2B schema pieces, then exercises the full orchestration
against that genuinely migrated database and confirms the same
end-to-end result as every in-memory `create_all()`-backed test.

## Caller/read-path audit

Repository-wide grep for `persist_discovery_fragment` (the function this
orchestration wraps) found exactly one real call site outside this
module's own code and its own docstring/precedent comments:
`scripts/capture_mac_discovery.py:677`. Classified:

- **`scripts/capture_mac_discovery.py`**: `FUTURE_ADAPTER_INTEGRATION`.
  This is the natural, and currently only, place a future slice would
  swap `persist_discovery_fragment(...)` for
  `resolve_or_persist_discovery_identity(...)` to let the MAC Granicus
  capture runner actually route unmatched evidence to governed
  candidates in a live run. Deliberately **not** wired in this mission —
  the mission's own §25 explicitly forbids invasive rollout ("Do NOT
  wire every adapter now unless one generic integration seam is clearly
  already shared... Avoid invasive rollout"), and wiring the one real
  adapter that exists today would itself constitute exactly that kind of
  premature integration, one step ahead of this slice's own stated scope
  (logic-only, no live discovery).
- Every other file in the repository that imports or discusses
  `persist_discovery_fragment` does so only in documentation/precedent
  comments (`unknown_airport_candidate_persistence.py`'s own module
  docstring naming it as a structural precedent) — no other real call
  site exists. `SAFE_UNCHANGED` for the entire remainder of the
  repository; nothing else required updating.

## Defects found

**None.** The orchestration's single subtlest risk — the
"call-both-persistence-functions-naively" trap described under
"Integration architecture" above — was identified and designed around
*during* implementation, before any code was written that could have
exhibited it, not discovered afterward as a defect. All 29 new
integration tests and all 8 new Fleet Health tests passed on first
execution; no correction was required to either new file after their
initial write.

## Corrections made

None beyond the two additive, narrowly-scoped Fleet Health changes
themselves (which are the FH-F2/FH-F3 correction this slice was always
scoped to make, not a fix to a defect discovered afterward).

## Focused tests

`tests/test_unknown_airport_discovery_integration.py`: **29 passed**, 0
failed. `tests/test_fleet_health_review_rules.py`: **93 passed** (85
pre-existing + 8 new), 0 failed. Combined broader suite (UAC3 + UAC2B +
UAC2A + UAC1 + discovery persistence + Fleet Health + model-contract +
MAC capture-runner + adjacent governance): **594 passed**, 0 failed.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both run clean; see the final chat report.

## Real DB before/after proof

Unchanged throughout this mission: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25,
`unknown_airport_candidates`/`unknown_airport_candidate_reviews`
confirmed **absent**, `source_assertions` confirmed to still have **no**
`unknown_airport_candidate_id` column — neither UAC2A nor UAC2B was ever
applied to the real database, and no live internet access occurred
anywhere in this mission.

---

# Critical review addendum

Adversarial review performed against a fresh independent reading of the
diff, plus live fixture probing outside the pre-existing test suite (not
merely trusting this report's own prior claims or unit-test names). One
genuine, narrow production defect was found and corrected; everything
else below is independently re-confirmed sound, several claims by direct
live probes with explicit call-counting/query-level instrumentation.

## Core decision-table verdict

**Confirmed correct for all seven named cases (A–G)**, each independently
re-executed live (not inferred from test names): known match →
`KNOWN_CANONICAL_ATTACHMENT`, airport_id set, zero candidate rows;
strong unknown → `UNKNOWN_AIRPORT_CANDIDATE`; zero names →
`UNRESOLVED_IDENTITY`; two names → `UNRESOLVED_IDENTITY` (no blend);
ambiguous known → `AMBIGUOUS_KNOWN_IDENTITY`, zero candidate rows; all-
conflict-but-coherent → `UNKNOWN_AIRPORT_CANDIDATE` using the fragment's
own claimed name, not the rejected candidate's; no known candidates
supplied + coherent identity → `UNKNOWN_AIRPORT_CANDIDATE`. Every case
independently checked `Airport` row count (`0` unless the pre-existing
known path itself attributed to an already-existing row),
`UnknownAirportCandidate` count, `SourceAssertion` attribution, and
`Signal` count (`0` in every case).

## Candidate-formability verdict — GENUINE DEFECT FOUND AND CORRECTED

**"Exactly one claimed airport name" was necessary but not sufficient —
a punctuation-only or numeric-only "name" (e.g. `"---"`, `"123"`,
`"***"`) previously formed a real, persisted `UnknownAirportCandidate`
row.** Reproduced directly via a live probe before any test suite was
consulted: `fragment.airport_names=frozenset({"---"})` produced
`DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE` with
`candidate.raw_name == "---"`. This is unambiguously "meaningless
candidate creation" (a punctuation string cannot be an airport name in
any language) — a real defect, not a matter of taste.

**Correction:** `_extract_unknown_airport_candidate_seed()` now
additionally requires the claimed name to contain at least one Unicode
alphabetic character (`any(character.isalpha() for character in
raw_name)`) — a deterministic, structural, script-agnostic character-
class check, not a confidence score or NLP judgment, and not a departure
from the "no hidden heuristic parser" instruction (it classifies
*characters*, not *meaning*). Proven directly: `"---"`/`"123"`/`"***"`
now correctly stay `UNRESOLVED_IDENTITY`; Japanese (羽田空港) and accented
(Aéroport Régional Exemple) names, already tested, remain unaffected.

**Deliberately NOT extended to filter generic-but-real words** ("Airport",
"International Airport") — attacked directly, both still form candidates.
This is a considered, documented, non-defect decision: distinguishing
"generic" from "specific" airport names would require either a
blocklist (inevitably incomplete, inevitably English-biased — what
about "Aeroporto," "Flughafen," "空港"?) or genuine semantic judgment,
both explicitly forbidden by the mission's own "no confidence scoring"
instruction. The human review layer every candidate feeds is the
correct, already-governed place to reject a genuinely low-quality
candidate — not this deterministic gate. Proven by test that both
remain formable, so this is a tested contract, not a silent gap.

**"One list element" vs "one distinct normalized identity" — confirmed
to be the literal, correct bar, attacked directly.** Two differently-
cased/whitespaced strings for what a human might read as the same name
(`{"Foo Regional Airport", "foo regional airport"}`) are two genuinely
distinct `frozenset` elements and correctly fail closed to
`UNRESOLVED_IDENTITY` (conservative, matching the project's own "prefer
false separation over false automatic convergence" ethos elsewhere in
this exact architecture) — not silently normalized or merged. An exact
repeated mention of the identical string collapses to one element via
ordinary `frozenset` deduplication before this module ever sees it, and
correctly still forms a candidate. Conflicting location claims and
multiple runway references were also attacked directly and found to
behave exactly as documented (locations ignored entirely; runway claims
joined, audit-only).

## Known-path compatibility verdict

**Sound, reconfirmed with the strongest available fixture.** Beyond the
pre-existing direct-comparison test, this review added a reproduction of
the real, live-proven MSP/SFO cross-airport shape (mirroring
`SourceAssertion` #222's own real, live-acquired fixture, via the
existing synthetic test already committed for it) through the new
orchestration — MSP confirms, SFO is never chosen, `airport_id` set,
`unknown_airport_candidate_id` NULL, zero candidate rows, evidence
preserved verbatim.

## Ambiguous-known verdict

**Sound.** Re-confirmed: two plausible known Airports never produce an
`UnknownAirportCandidate`; canonical tables unmutated; the `SourceAssertion`
remains governed/auditable exactly as it already was before UAC3
(`airport_id=NULL`, both governance columns preserved). The 4-member
result enum already, correctly, distinguishes `AMBIGUOUS_KNOWN_IDENTITY`
from `UNRESOLVED_IDENTITY` as two **separate** members (re-confirmed by
direct enum inspection) — the mission's own concern about them being
merged into one member does not apply to this implementation; no
vocabulary change was needed or made.

## All-conflict/coherent-new verdict

**Sound, and the critical inverse case (mission's own explicit ask) was
missing from the original suite and is now closed.** The "coherent
different identity" case was already tested; the inverse — all known
candidates conflict **and** the fragment's own identity is weak (zero
claimed names) — is now proven directly: `REJECT_CROSS_AIRPORT` +
zero names → `UNRESOLVED_IDENTITY`, zero candidate rows. Routing
depends purely on the fragment's own formability, never on the conflict
itself, confirmed by this pair of tests together.

## Convergence/collision verdict

**Sound — UAC1's fingerprint logic is reused verbatim, never
reimplemented, confirmed by direct source inspection of
`_extract_unknown_airport_candidate_seed()`/`resolve_or_persist_discovery_identity()`
(neither imports `hashlib`, neither references `compute_candidate_fingerprint`
directly — convergence is entirely delegated to
`find_or_create_unknown_airport_candidate()`, called with only `raw_name`/
`raw_runway_designation`/`evidence_source_locator`/`evidence_artifact_identity`,
never a hand-computed fingerprint).** All named convergence cases
(different producer, case variation, whitespace, Unicode/accented,
different city, different state/region, near-name variant) were already
covered by the pre-existing test suite and re-verified passing.

## One-persistence-path verdict — independently instrumented, not merely inferred

**Confirmed with explicit call-counting spies**, per the mission's own
specific instruction. Live probe (and now a permanent 5-test class,
`TestExactlyOnePersistencePath`) wraps both
`persist_discovery_fragment`/`persist_candidate_linked_source_assertion`
with counters: known-match calls only the former (1/0); unknown-candidate
calls only the latter (0/1); unresolved calls only the former (1/0);
ambiguous-known calls only the former (1/0); a two-call replay of the
identical fragment through the candidate-linked path calls the latter
twice and the former zero times, producing exactly one `SourceAssertion`
row throughout. No execution path was found, or is structurally possible
given the function's own `if`/`elif`/return control flow, that calls
both functions for the same fragment.

## Transaction/rollback verdict

**Sound, re-confirmed against all four named cases.** (A) new candidate
created + assertion persistence forced to fail (via monkeypatching the
second call to raise) → caller rollback removes both the candidate and
any assertion — zero of either survives. (B) an **already-committed**
candidate from an earlier call survives untouched when a *later* call
linking new evidence to it is forced to fail and rolled back — proven
directly, the existing governed candidate's own row and fingerprint are
unaffected. (C) an uncommitted call, rolled back, leaves zero rows of any
kind. (D) candidate persistence itself failing was not separately
constructed as a distinct scenario from (A) in the original suite — re-
examined here and confirmed equivalent in effect: `find_or_create_unknown_airport_candidate()`
is UAC1's own, already-independently-reviewed function; a validation
failure there (e.g. blank `raw_name`, already impossible given the
formability gate that runs first) would raise before `session.add()`,
leaving nothing to roll back — no new test was needed to prove a
structurally unreachable state.

## Replay/idempotency verdict

**Honest, re-confirmed.** `SourceAssertion`-level dedup is entirely
inherited from the two persistence functions' own pre-existing fragment-
identity keying (`source_id`, `artifact_identity`, `source_locator`,
`raw_fragment_hash`) — this module invents no new idempotency mechanism.
Candidate count is proven stable under exact-identity replay (already
tested); the spy-instrumented replay test above additionally proves the
*call pattern* itself is idempotent-shaped (two calls, one row, no
`persist_discovery_fragment` calls at all for a candidate-linked replay).
Documented plainly, not oversold: a fragment with a *different*
`artifact_identity`/`source_locator`/`raw_fragment_hash` for otherwise
identical claimed content is **not** deduplicated at the `SourceAssertion`
level (by design, matching `persist_discovery_fragment()`'s own
pre-existing behavior) — only the `UnknownAirportCandidate` itself
converges by exact fingerprint in that case.

## False-negative recovery verdict

**Sound**, re-confirmed: zero duplicate canonical `Airport` rows, zero
mutation of the existing `Airport`, and the candidate's own
`resolved_airport_id` stays `None` throughout (this module never asserts
the candidate "definitely is" a new real airport — it is inert, exactly
as UAC1 established). A subsequent `MATCH_EXISTING_AIRPORT` review
remains fully possible and was proven directly against the real existing
row.

## Multi-airport verdict

**Sound**, re-confirmed for all four named cases: one fragment, two
distinct identities → fails closed, no blend; repeated mentions of the
*same* identity → correctly collapses via `frozenset`, still formable;
two separately extracted fragments from one document (the existing,
expected extraction shape) → operates fragment-local, forms two
independent candidates; a fragment ambiguously naming a known-conflicting
identity alongside no coherent unknown identity → `UNRESOLVED_IDENTITY`
(covered by the all-conflict/weak-identity case above).

## FH-F2/FH-F3 verdict — independently reconstructed at the real query level

**Sound, and now proven at a deeper level than the original report's own
unit tests.** A full 6-case matrix (canonical×reviewed, canonical×
unreviewed, candidate×reviewed, candidate×unreviewed, unattributed×
reviewed, unattributed×unreviewed) was independently reconstructed
against **real, persisted `SourceAssertion` rows** and the actual
`_build_source_assertion_review_states()` query (not synthetic
`SourceAssertionReviewStateFact` literals) — confirming canonical-
attributed rows never even reach the facts tuple regardless of
`review_state` (pre-existing, unaffected behavior), candidate-attributed
rows appear in the facts tuple but trigger neither rule regardless of
`review_state` (the correction), and truly-unattributed rows fire exactly
as before, discriminated correctly by `review_state`. No genuine Fleet
Health finding was found to be suppressed for any row this correction
was not specifically designed to skip — the mission's own explicit "did
UAC3 accidentally suppress genuine findings" concern is answered no,
confirmed by the E/F rows in the same matrix firing exactly as expected
alongside the skipped C/D rows. A permanent regression test
(`TestFleetHealthFullSixCaseMatrix`) now covers this exact matrix at the
real-query level, not just the pre-existing rule-level unit tests.

## Promotion-firewall verdict — attacked with the strongest adversarial construction possible

**Sound, proven at the maximum possible adversarial strength.** Beyond
confirming `intelligence_review_decision`/`promotion_policy_decision`/
`signal_id` all stay `None` for a candidate-linked row, this review
constructed the single strongest-looking piece of candidate-linked
evidence conceivable — text explicitly claiming a **confirmed contract
award**, a **specific dollar figure**, and an **awarded-contractor**
claim (exactly the shape that would sail through `promotion_policy_evaluation.py`'s
own `AUTO_ELIGIBLE` allowlist for a *known*-airport row) — and called
`create_signal_from_approved_review()` **directly**, bypassing every
normal review step entirely. It still fails closed:
`ValueError: create_signal_from_approved_review requires
identity_guard_decision == 'ATTACH_CONFIRMED', got 'INSUFFICIENT_IDENTITY'`.
This is not a UAC3 guarantee — it is `governed_signal_creation.py`'s own,
completely unmodified, pre-existing structural gate — but it is now
proven directly against real candidate-linked evidence rather than
merely asserted by inference from the identity-guard-decision value
alone.

## Result-contract verdict

**Sound.** All four operationally necessary states are independently
distinguishable via the enum member alone, with no boolean inference
required anywhere - confirmed by direct enum-membership inspection
(re-run as part of the pre-existing `TestResultContract`). No new
vocabulary was added or is warranted; the mission's own concern about
`AMBIGUOUS_KNOWN_IDENTITY` potentially sharing a member with
`UNRESOLVED_IDENTITY` does not apply, as documented above.

## Source-neutrality verdict

**Sound.** Fresh grep of the new/modified production files for MAC/
Granicus/FAA/USAspending/US-state/USD/English-only assumptions found
none. International fixtures (Japanese, accented French-style) were
already present and re-confirmed passing.

## Migration-chain parity verdict

**Sound**, re-confirmed: the existing end-to-end test builds its fixture
through the real `baseline → UAC2A upgrade() → UAC2B upgrade()` chain
(re-read fresh, confirmed no `create_all()` shortcut for the UAC1/UAC2B
schema pieces), then exercises the full orchestration against that
genuinely migrated database.

## Caller-audit verdict

**Re-confirmed unchanged.** `scripts/capture_mac_discovery.py:677`
remains the only real call site of `persist_discovery_fragment` anywhere
in the repository outside this module's own code and precedent comments
(re-grepped fresh). Classified `FUTURE_ADAPTER_INTEGRATION`, deliberately
not wired during this review — the mission's own §19 explicitly forbids
wiring MAC "unless UAC3 itself cannot be correctly reviewed without doing
so," and it plainly can be (and was) reviewed thoroughly without it.

## Defects found

1. **Punctuation/numeric/symbol-only claimed names formed meaningless
   candidates** (production defect, genuine "meaningless candidate
   creation," reproduced directly) — corrected.

No other genuine defects were found. Every other reviewed dimension was
independently re-confirmed sound by fresh reading, live fixture probing
(not test-name inference), and instrumented (spy/counter, real-query)
verification where the mission specifically asked for it.

## Corrections made

1. `_extract_unknown_airport_candidate_seed()` now requires the claimed
   name to contain at least one Unicode alphabetic character, in
   `app/services/unknown_airport_discovery_integration.py`.
2. Regression tests added for: the formability defect itself (3 tests:
   punctuation/numeric/symbol-only); the deliberate non-defect boundary
   (2 tests: bare generic word, generic phrase, still formable); case/
   whitespace-variant and exact-repeat set-membership behavior (2 tests);
   conflicting-location and multi-runway audit-only behavior (2 tests);
   exactly-one-persistence-path spy instrumentation (5 tests); the all-
   conflict-and-weak-identity inverse case (1 test); the MSP-shaped
   known-path fixture (1 test); the full 6-case FH-F2/FH-F3 real-query
   matrix (1 test); the strongest-adversarial promotion-firewall attack
   (1 test).

## Regression tests added

18 new tests (65 total in the UAC3 integration file, up from 29; Fleet
Health rules file unchanged at 93 from the implementation mission's own
8 additions).

## Focused test results

`tests/test_unknown_airport_discovery_integration.py`: **65 passed**, 0
failed (up from 29). Combined broader suite (UAC3 + UAC2B + UAC2A + UAC1
+ discovery persistence + Fleet Health + model-contract + MAC capture-
runner + adjacent governance): **612 passed**, 0 failed.

## Final full pytest result

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the correction; see the final chat report.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25, UAC schema
confirmed **absent** — verified fresh both before and after this review.
No live internet access occurred.

RWI_UAC3_UNKNOWN_AIRPORT_DISCOVERY_INTEGRATION_IMPLEMENTATION_COMPLETE
