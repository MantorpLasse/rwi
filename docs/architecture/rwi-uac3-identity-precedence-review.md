# RWI UAC3 / Identity Guard Precedence Review

DESIGN-ONLY. No code changed. No DB write. No commit, no push. This report is the sole artifact of this mission.

## 1. Files read fresh

`app/services/evidence_attachment_guard.py` (full), `app/services/unknown_airport_discovery_integration.py` (full), `app/services/discovery_candidate_fragment.py`, `scripts/capture_mac_discovery.py` (the `plan_governed_persistence()` mirror logic specifically), the 5F report and its own adversarial-review findings (§10 of `rwi-source-identity-evidence-5f-report.md`), and the UAC3 test suite's own structure (`tests/test_unknown_airport_discovery_integration.py`).

## 2. Current precedence model (re-derived from source, not prior prose)

**Per-candidate evaluation (`evaluate_attachment`)**, pure and single-candidate:

- **Positive evidence** — five categories, each counts once regardless of how many tokens matched:
  - `IDENTIFIER`: a bag identifier matching the candidate's own IATA/ICAO/FAA code.
  - `NAME`: a bag name matching the candidate's own name or alias, exact normalized-string equality only (casefold + strip; no fuzzy matching).
  - `ISSUER`: a bag issuer matching the candidate's own known-issuer list.
  - `LOCATION`: a bag location matching the candidate's own city, exact string equality.
  - `RUNWAY_TOPOLOGY`: a bag runway end/pair present in the candidate's own canonical topology.
- **Contradicting evidence** — asymmetric by category:
  - `IDENTIFIER`: **automatic** — any bag identifier that does *not* match the candidate is unconditionally treated as contradiction. No pre-classification needed; a non-matching identifier is "self-evidently not the candidate's own" by the module's own stated reasoning (identifier codes are globally unique by convention).
  - `NAME` / `ISSUER` / `LOCATION`: **never automatic** — a non-matching value contributes neither a positive nor a negative signal. Contradiction is only recognized when the *caller* has already pre-classified a value into `contradicting_names`/`contradicting_issuers`/`contradicting_locations` — the guard "never guesses that a found name belongs to some other, unspecified airport." No current extractor (including 5F's new title-mining) ever populates these fields.
  - `RUNWAY_TOPOLOGY`: a non-matching token is contradiction *only* if the fragment also independently names a different airport (via `contradicting_names/issuers/locations`, a non-matching identifier, or explicit `alternate_airport_runway_*` evidence) — otherwise it's silently ignored (absence, not contradiction).
- **Outcome**: any contradiction present → `REJECT_CROSS_AIRPORT` unconditionally (vetoes all positive evidence). Else: `IDENTIFIER` positive alone → `ATTACH_CONFIRMED` (definitive). Else ≥2 independent positive categories → `ATTACH_CONFIRMED`. Else exactly 1 → `ATTACH_PROVISIONAL`. Else → `INSUFFICIENT_IDENTITY`.

**Cross-candidate resolution (`evaluate_attachment_for_candidates`)**: any candidate reaching `ATTACH_CONFIRMED` *or* `ATTACH_PROVISIONAL` is "qualifying." If more than one candidate qualifies (regardless of relative strength — a 2-category `ATTACH_CONFIRMED` and a 1-category `ATTACH_PROVISIONAL` are treated identically here), **all** qualifying candidates are downgraded to `REVIEW_REQUIRED`. `REJECT_CROSS_AIRPORT`/`INSUFFICIENT_IDENTITY` candidates are never touched by this step.

**UAC3 routing (`resolve_or_persist_discovery_identity`)**: picks `best_outcome` across all supplied candidates by bucket priority — `{ATTACH_CONFIRMED, ATTACH_PROVISIONAL}` (0) < `REVIEW_REQUIRED` (1) < `REJECT_CROSS_AIRPORT` (2) < `INSUFFICIENT_IDENTITY`/none-supplied (3). Buckets 0–1 route to the existing known-airport/ambiguous persistence path (`KNOWN_CANONICAL_ATTACHMENT` / `AMBIGUOUS_KNOWN_IDENTITY`). Buckets 2–3 check `_extract_unknown_airport_candidate_seed()` (exactly one well-formed `airport_names` claim) — formable → `UNKNOWN_AIRPORT_CANDIDATE`; not formable → `UNRESOLVED_IDENTITY`.

## 3. Real Anoka case — reproduced

```
EvidenceBag(names={"Anoka County-Blaine Airport"}, runway_pairs={"18/36"}, issuers={"Metropolitan Airports Commission"})
Candidates: 5 real airports (Bill and Hillary Clinton National, Waterbury-Oxford, Reading Regional, McAllen International, San Francisco International) — none named "Anoka County-Blaine", none in Minnesota.
```
4 candidates independently reach `ATTACH_PROVISIONAL` (runway_topology only — none has "Anoka County-Blaine" as name/alias, so `NAME` never contributes positively for any of them); SFO reaches `INSUFFICIENT_IDENTITY`. Cross-candidate resolution finds 4 qualifying candidates → all downgraded to `REVIEW_REQUIRED`. `best_outcome = REVIEW_REQUIRED` → UAC3 routes `AMBIGUOUS_KNOWN_IDENTITY`. Exactly reproduces the real 5E/5F preview.

## 4. Single-wrong-topology case — reproduced, HIGH PRIORITY confirmed

```python
EvidenceBag(names={"Example New Airport"}, runway_pairs={"18/36"})
candidates = [CandidateAirport(id=1, name="Some Unrelated Airport", canonical_runway_pairs={"18/36"})]
```
Result: `ATTACH_PROVISIONAL` (`runway_topology` only; `NAME` doesn't match, contributes nothing — not a contradiction). Only one candidate → no cross-candidate downgrade. `best_outcome` lands in the known-match bucket → UAC3 routes `KNOWN_CANONICAL_ATTACHMENT`, `persist_discovery_fragment()` sets `attached_airport_id = 1` — **the wrong airport, silently, with no human review at all.** This is genuinely worse than the ambiguous case, which at least stops for a human.

## 5. Full precedence matrix (empirically run against the real, unmodified guard)

| # | Setup | Per-candidate outcome(s) | Multi-candidate outcome | UAC3 routing (current) |
|---|---|---|---|---|
| A | exact name + exact topology, 1 candidate | `ATTACH_CONFIRMED` (name+topology) | — | `KNOWN_CANONICAL_ATTACHMENT` (correct) |
| B | exact name matches X; topology also matches unrelated Y | X: `ATTACH_CONFIRMED`→ Y: `ATTACH_PROVISIONAL`→ | both → `REVIEW_REQUIRED` | `AMBIGUOUS_KNOWN_IDENTITY` — **X's strong match is discarded** |
| C | unknown name; topology matches 1 unrelated | `ATTACH_PROVISIONAL` | — (single) | `KNOWN_CANONICAL_ATTACHMENT` to the **wrong** airport |
| D | unknown name; topology matches several unrelated | each `ATTACH_PROVISIONAL` | all → `REVIEW_REQUIRED` | `AMBIGUOUS_KNOWN_IDENTITY` (the real Anoka case) |
| E1 | name doesn't match (not pre-classified); topology matches | `ATTACH_PROVISIONAL` | — | same as C |
| E2 | name **explicitly pre-classified contradicting**; topology matches | `REJECT_CROSS_AIRPORT` (topology positive vetoed) | — | `UNRESOLVED_IDENTITY` or `UNKNOWN_AIRPORT_CANDIDATE` (correct — proves the veto mechanism itself works when fed) |
| F | no name; 1 topology match | `ATTACH_PROVISIONAL` | — | `KNOWN_CANONICAL_ATTACHMENT` (correct — no contradicting evidence to raise doubt) |
| G | no name; several topology matches | each `ATTACH_PROVISIONAL` | all → `REVIEW_REQUIRED` | `AMBIGUOUS_KNOWN_IDENTITY` (correct) |
| H | two names, one matches; topology matches | `ATTACH_CONFIRMED` (name+topology) | — | `KNOWN_CANONICAL_ATTACHMENT` (correct — non-matching second name contributes nothing) |
| I | exact identifier match + unrelated non-matching name present | `ATTACH_CONFIRMED` (identifier alone) | — | `KNOWN_CANONICAL_ATTACHMENT` (correct) |
| J | exact name match + a **different, non-matching identifier** present | `REJECT_CROSS_AIRPORT` (identifier auto-contradicts) | — | routes away from this candidate — **identifier mismatch already vetoes even a genuine name match** |
| K | location match + topology match, 1 candidate | `ATTACH_CONFIRMED` (location+topology) | — | `KNOWN_CANONICAL_ATTACHMENT` (correct) |
| L | issuer match + topology match, 1 candidate | `ATTACH_CONFIRMED` (issuer+topology) | — | `KNOWN_CANONICAL_ATTACHMENT` — **reachable even with an unrelated, unmatched explicit name present in the same bag, since name-mismatch is never checked here** |

Row J is the existing system's own proof that automatic-mismatch-as-contradiction is a real, working, already-relied-upon pattern — just currently applied only to identifiers, never to names.

## 6. Evidence-strength verdict: a full numeric hierarchy is NOT needed; a narrow, single, deterministic addition is

The current model already has an implicit two-tier structure: `IDENTIFIER` is strong (alone-sufficient, auto-vetoing on mismatch); `NAME`/`ISSUER`/`LOCATION`/`RUNWAY_TOPOLOGY` are a flat, equally-weighted second tier (positive-only, additive, only rejected via explicit pre-classification). Introducing a full ranked hierarchy (STRONG/MEDIUM/WEAK, confidence scores, or reweighting how categories combine) was considered and **rejected**: it would touch the combination algorithm itself (rows A/H/K/L, all currently correct), risks changing outcomes for already-proven MSP/SFO workflows, and edges toward exactly the "probabilistic/heuristic" territory this mission explicitly forbids.

**Why treating NAME like IDENTIFIER (automatic mismatch = contradiction) is unsafe and was rejected**: identifier codes are unique by international convention — a non-matching code is unambiguous proof of a different airport. Names are not: legitimate formatting variance (`"Minneapolis-St. Paul International Airport"` vs. a candidate's own stored `"Minneapolis-Saint Paul International"`), abbreviation, or punctuation differences would trigger false `REJECT_CROSS_AIRPORT` verdicts on exact-string comparison alone — and this mission explicitly forbids fuzzy matching that could otherwise absorb that variance. This is not an oversight in the original design; it is a correct, deliberate trade-off that should not be reversed.

**What is actually needed** is narrower than a strength hierarchy: a single, deterministic UAC3-level check, described in §16.

## 7. Contradiction semantics verdict

Can positive topology evidence ever override an explicit contradictory *identifier*? **No** — confirmed empirically (row J): even a full name+topology combination is unconditionally vetoed by one non-matching identifier. Can positive topology evidence ever override an explicit contradictory *name*? Only when the name has been explicitly pre-classified into `contradicting_names` (row E2) — which no current extractor does. The fail-closed behavior for identifiers is already correct and complete. The gap is entirely upstream: nothing currently feeds `contradicting_names` from a plain "doesn't match" observation — and, per §6, it should not, because doing so via exact-string comparison would be unsafe.

## 8. Unknown-airport routing verdict: **B, with a precise gate — not unconditionally**

When source evidence explicitly names an airport matching no canonical Airport, and runway topology overlaps one or more known Airports, UAC3 should prefer creating/reusing an `UnknownAirportCandidate` **specifically when no supplied candidate's positive evidence includes a NAME match** — i.e., when zero corroboration exists anywhere in the candidate set for the topology-driven outcome. This is derived, not assumed: rows C and D are exactly the cases where an explicit, well-formed name claim exists and is completely uncorroborated by every candidate the topology search happened to surface; in both, the *only* evidence supporting the current known/ambiguous routing is a runway heading shared by many airports worldwide — precisely the "low-uniqueness evidence" the mission's own §14 anticipates.

## 9. Exact-name-match-vs-topology-ambiguity verdict (row B): current behavior is arguably too conservative, but is NOT the recommended fix target

An exact name match on candidate X, with topology also matching unrelated Y, currently produces `REVIEW_REQUIRED` for both — including X, which has an independently strong (2-category) match. This is a real, separate finding, but fixing it would require changing `evaluate_attachment_for_candidates()`'s own cross-candidate resolution (letting a stronger match "win" over a weaker one instead of treating all qualifying candidates identically) — a change to the **guard itself**, with broader blast radius than the UAC3-level fix in §8/§16, and not proven necessary by any real observed case (unlike rows C/D, which are the real Anoka scenario). **Recommended: leave row B's behavior unchanged in the immediate next implementation mission; flag it as a distinct, smaller, separately-decidable follow-on** (see §15 Option 2 for the shape such a fix would take if ever pursued).

## 10. Identifier precedence verdict

Identifiers already, correctly, outrank runway topology, issuer, and name — both by being alone-sufficient for `ATTACH_CONFIRMED` and by auto-vetoing on any mismatch (row J). No change needed or recommended here; this is the one category where the current model is already exactly as strict as it should be.

## 11. Known-airport compatibility assessment

The recommended fix (§16) only changes behavior when a fragment carries a well-formed name claim (per the existing, unmodified `_extract_unknown_airport_candidate_seed()` formability rule) that fails to positively match *any* supplied candidate. Every currently-proven MSP/SFO-style workflow either (a) carries no name evidence at all (pre-5F extraction, or any non-MAC source), in which case the check never engages, or (b) carries a name that *does* match the real candidate, in which case `name_corroborated` is true and routing is unchanged. **No regression path was found for any existing proven scenario** — this must still be confirmed by the exact regression suite in §18 before implementation, not assumed from this analysis alone.

## 12. Worldwide-discovery impact

Qualitative, not statistical (no dataset-level measurement exists or was attempted). Runway headings are drawn from a small set of compass-derived values (a two-digit heading, optionally with an L/R/C parallel-runway suffix) dictated by prevailing wind and geography, not chosen per-airport — the same handful of common headings (09/27, 18/36, 14/32, etc.) recur across thousands of airports worldwide by physical necessity, not coincidence. This makes runway topology inherently low-uniqueness evidence at global scale: as RWI's own canonical Airport set grows, the probability that *any* newly-discovered airport's topology coincidentally overlaps at least one existing candidate rises, not falls. Under current semantics (§8), that overlap alone is sufficient to block unknown-candidate formation regardless of how explicitly and confidently the source names the true airport — this is a structural, not incidental, limitation on RWI's stated worldwide-EMAS-discovery goal.

## 13. Options considered

**Option 1 — contradiction veto in the guard (treat name-mismatch like identifier-mismatch).** Behavior: any non-matching name automatically contradicts. Files: `evidence_attachment_guard.py`. Compatibility risk: **high** — exact-string name comparison would misclassify legitimate formatting variance as contradiction, likely breaking real MSP/SFO-style attachments the moment a name is present but spelled slightly differently. Test impact: every existing name-evidence test needs re-verification. Schema change: none. UAC3 change: none directly, but routing would shift as a side effect. Guard change: yes, to the core algorithm. New vocabulary: none. **Rejected** — the very trade-off the original design deliberately avoided (§6).

**Option 2 — evidence-strength precedence in the guard (let a stronger qualifying match win over a weaker one during cross-candidate resolution).** Behavior: in `evaluate_attachment_for_candidates()`, if exactly one qualifying candidate has strictly more positive categories than every other qualifying candidate, do not downgrade it to `REVIEW_REQUIRED`. Files: `evidence_attachment_guard.py`. Compatibility risk: **medium** — changes an already-relied-upon ambiguity rule; needs careful proof it can't be gamed by coincidental extra categories. Test impact: moderate, targeted at row B. Schema/UAC3/guard change: guard only. New vocabulary: none. Addresses row B (§9), not rows C/D (the actual Anoka case). **Not recommended as the primary fix** — solves a smaller, non-observed problem while leaving the real one (§8) unaddressed.

**Option 3 — UAC3-level uncorroborated-name override (recommended, §16).** Behavior: after the guard runs, if the fragment carries a formable name claim (existing `_extract_unknown_airport_candidate_seed()` check) that never appears as `NAME` in any candidate's own positive evidence, treat routing as "no known match" regardless of the topology/issuer-driven `best_outcome`. Files: `unknown_airport_discovery_integration.py` only, plus a required consistency update to `scripts/capture_mac_discovery.py`'s `plan_governed_persistence()` mirror (§19). Compatibility risk: **low** — provably inert whenever no name evidence exists or the name matches (§11). Test impact: targeted, enumerated in §18. Schema change: none. UAC3 change: yes (the routing bucket-selection logic). Guard change: **none**. New vocabulary: none required (the existing four `DiscoveryIdentityOutcome` values are sufficient; only the `reason` string needs updating).

**Option 4 — do nothing; treat this as accepted, documented risk.** Behavior: none. Risk: the single-wrong-topology case (§4) remains a live, silent misattachment risk with no human review gate, and worldwide discovery remains structurally constrained by topology collisions. **Rejected** — §4's finding is materially more severe than "merely" failing to discover a new airport; it silently attaches evidence to the wrong real airport.

## 14. Recommended architecture: Option 3

Satisfies every constraint in the mission's own §16 checklist: explicit contradictory identity (identifiers) remains unconditionally vetoing, unchanged; strong identity (identifier match, or a corroborated name) still behaves exactly as before; unknown-airport discovery becomes reachable in the two real, evidenced gap cases (§4, §8) without any fuzzy matching, canonical auto-creation, or new persisted vocabulary; ambiguous evidence still fails closed (rows F/G/B unaffected, or in B's case, unchanged pending §9's separate follow-on); minimal file scope (one production file, plus one required consistency update); and is explainable in one sentence to a human reviewer: *"if the source names an airport that matches none of the plausible known candidates, don't let a merely-coincidental runway-heading overlap silently claim this evidence — check whether a governed unknown-airport candidate should form instead."*

## 15. Proposed Anoka routing under the recommended rule

Fragment: `airport_names={"Anoka County-Blaine Airport"}`, `runway_pairs={"18/36"}`, current canonical DB (5 candidates, none named Anoka). Under Option 3: `name_corroborated` = False (no candidate's positive evidence ever includes `NAME` — confirmed in §3). `_extract_unknown_airport_candidate_seed(fragment)` returns a valid seed (exactly one well-formed name). The known/ambiguous bucket is therefore bypassed regardless of the topology-driven `REVIEW_REQUIRED` result, and routing becomes **`UNKNOWN_AIRPORT_CANDIDATE`** — a governed, non-canonical candidate seeded with `raw_name="Anoka County-Blaine Airport"`, pending ordinary human review via the existing, unmodified UAC4/UAC5 workflow. No Airport, Runway, or Signal is created by this routing change itself — exactly the same downstream firewall as every other `UNKNOWN_AIRPORT_CANDIDATE` outcome today.

## 16. Required regression tests before implementation

1. Real Anoka case (§3) → must become `UNKNOWN_AIRPORT_CANDIDATE`, not `AMBIGUOUS_KNOWN_IDENTITY`.
2. Single wrong-topology candidate + contradictory name (§4) → must become `UNKNOWN_AIRPORT_CANDIDATE`, not `KNOWN_CANONICAL_ATTACHMENT` to the wrong airport.
3. Exact-name match + topology ambiguity (row B) → must remain `AMBIGUOUS_KNOWN_IDENTITY` (confirms Option 3 does not touch row B; a future Option 2 mission would target this separately).
4. No-name, topology-only, single match (row F) → must remain `KNOWN_CANONICAL_ATTACHMENT` (proves existing MSP/SFO-style behavior untouched).
5. No-name, topology-only, ambiguous (row G) → must remain `AMBIGUOUS_KNOWN_IDENTITY`.
6. Multiple explicit names (row H, and the 2-name UAC3-formability case) → must remain governed by the existing "exactly one name" formability rule, unaffected by this change.
7. Identifier contradiction (row J) → must remain `REJECT_CROSS_AIRPORT`/routed away, completely unaffected.
8. Issuer-only + topology, single match (row L) → must remain `KNOWN_CANONICAL_ATTACHMENT` when no name evidence is present; must become `UNKNOWN_AIRPORT_CANDIDATE` when an uncorroborated name is *also* present (the "issuer can't rescue a contradicted name" case).
9. Location-only + topology, single match (row K) → same shape as L.
10. Replay of a fragment already routed under the *old* rule (a persisted `KNOWN_CANONICAL_ATTACHMENT` row) — confirm the "never rewrite an already-resolved SourceAssertion" rule still applies, and that this is the reason the fix is not retroactive (§19).
11. UAC7 preview/apply consistency — `plan_governed_persistence()`'s `would_form_unknown_airport_candidate` mirror must be updated in lockstep and a test must prove preview and apply agree post-fix, exactly as the original UAC7 review required.

## 17. Fingerprint impact

`guard_outcome`, `attached_airport_code`, and `would_form_unknown_airport_candidate` are all already part of `compute_plan_fingerprint()`'s hashed material. Since Option 3 changes *only* how `would_form_unknown_airport_candidate` and the routing bucket are computed — not the evidence itself — the existing fingerprint architecture already correctly captures the change: a fragment whose routing flips under the new rule will fingerprint differently before vs. after the fix lands, which is exactly the desired TOCTOU protection. **No new fingerprint field is needed.** The implementation must, however, update `plan_governed_persistence()`'s own mirror computation (§16 item 11) — if forgotten, the preview would silently drift from what apply actually does, which would itself be a new defect distinct from anything found in this review.

## 18. Replay/idempotency impact

No change to EvidenceBag content, so EB3's replay-conflict detection (keyed on content equality) is entirely unaffected. However: this fix is **not retroactive**. Any fragment already persisted under the current (pre-fix) routing — e.g., a real single-wrong-topology misattachment, if one exists in production — would be found by `_get_existing_assertion()`'s fragment-identity dedup on replay and returned **unchanged**, per the deliberate "never rewrite an already-resolved SourceAssertion" rule both persistence functions already enforce. Confirmed: no MAC document has ever been actually applied to the real database with `airport_names` populated (5F is the first mission to populate that field at all, and no apply has occurred since), so no currently-persisted data is known to be affected — but this should be explicitly re-checked as part of implementing the fix, not assumed.

## 19. Schema change required: **No.** New vocabulary required: **No.**

## 20. STOP findings

None that block this review from completing. Two findings materially informed the recommendation and are flagged for the implementation mission's own attention: (a) the required `plan_governed_persistence()` consistency update (§16 item 11, §17); (b) row B (§9) is a real, separate, smaller finding deliberately left unaddressed by the recommended option.

## 21. READY_FOR_UAC3_PRECEDENCE_IMPLEMENTATION: **YES**, scoped exactly to Option 3.

## 22. Exact recommended next implementation mission

A narrowly-scoped implementation mission: add the uncorroborated-name-override check to `resolve_or_persist_discovery_identity()` (`unknown_airport_discovery_integration.py`), update `plan_governed_persistence()`'s mirror in `capture_mac_discovery.py` to match, and implement all 11 regression tests from §16 — including a live, read-only re-verification that the real Anoka fragment now routes to `UNKNOWN_AIRPORT_CANDIDATE` under the corrected code, still without any real database write until a separate, explicitly-authorized apply mission follows.
