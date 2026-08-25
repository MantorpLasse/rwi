# RWI ERG1 — Pure EMAS Relevance Evaluator

Status: IMPLEMENTED, INDEPENDENTLY ADVERSARIALLY REVIEWED, NARROWLY CORRECTED, COMMITTED, PUSHED. See §31 (Adversarial Review Findings) for the review's own independent re-derivation and findings, appended below the original implementation record (§1-§30, left intact as the historical implementation record).

## 1. Starting HEAD

`f9ba7f0d87120df7e6736d82e860d4036be114dd` — confirmed `== origin/main` at mission start.

## 2. Real DB proof

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened a writable connection to the real database and never imported `app.database`/`app.models` from any production or test file it added — see §17 and §25.

## 3. Files read (this mission, fresh)

`docs/architecture/rwi-emas-relevance-gate-design.md` (full), `app/services/discovery_candidate_fragment.py` (full, re-confirmed `CandidateFragment`'s field shape and its own "audit-only, never guard input" discipline for `document_title`/`terminology_hits`), `app/services/evidence_claim_semantics.py` (targeted — `TemporalQualifier`'s exact six-member vocabulary, confirmed reusable verbatim per the design doc's own instruction), `tests/test_promotion_policy_evaluation.py` (targeted — the exact AST-based purity/no-current-time-dependency test precedent this slice's own firewall tests mirror). `app/services/promotion_policy_evaluation.py`, UAC4 (`unknown_airport_candidate_resolution.py`), `app/models/unknown_airport_candidate.py`, and the UAC5 CLI were already read fresh in full during the immediately-preceding EMAS Relevance Gate design mission in this same session and are cited, not re-read verbatim, per that mission's own report.

## 4. Evaluator input contract

`evaluate_emas_relevance(observations: tuple[EmasEvidenceObservation, ...], context: EmasRelevanceContext = EmasRelevanceContext()) -> EmasRelevanceDecision`.

**Decision: multiple, already-classified evidence observations for one candidate — never a SourceAssertion/EvidenceBag, never raw text.** `EmasEvidenceObservation` is a frozen dataclass: `evidence_class: EvidenceClass`, `basis: str` (audit-only description of what was matched, never itself parsed by this module), `temporality: TemporalQualifier = UNKNOWN` (reused verbatim from `evidence_claim_semantics`, per the design doc's own explicit instruction), `polarity: ObservationPolarity = POSITIVE` (new — see §13). Rejected shapes: a raw `CandidateFragment`/`EvidenceBag` (would force this module to parse or interpret extraction-layer fields it has no business owning — the design doc's own Section 15/17 explicitly assigns concept-to-class matching to the extraction layer, never the evaluator); a single normalized fact per call (the design doc's own Section 8/9 worked examples and Section 11's "multi-evidence aggregation" requirement both assume one candidate's evidence arrives as a set, mirroring `evaluate_promotion_policy(claims: tuple[Claim, ...], ...)`'s own tuple-of-facts shape exactly).

`EmasRelevanceContext` is included, currently empty, as a forward-compatible seam — kept as its own object rather than omitted because the design doc's own Section 20/26 literally lists `EmasRelevanceContext` as an expected ERG1 artifact, and because `PromotionPolicyContext`'s own precedent is exactly "a small explicit object for future policy inputs that are not evidence facts themselves." No field was added because no additional non-evidence-class policy input was justified by anything in the design doc (unlike promotion policy's own need for a caller-asserted `source_authority_tier`).

## 5. Evaluator output contract

`EmasRelevanceDecision`: `outcome: RelevanceOutcome`, `reason: str` (deterministic, template-built, never LLM-generated), `evidence_classes_matched: frozenset[EvidenceClass]` (positive-polarity, non-discounted classes only), `contradicting_evidence_classes: frozenset[EvidenceClass]`, `is_watch_worthy: bool`, `is_canonical_admission_relevant: bool`. No score, no probability, no confidence field anywhere — matching the design doc's own locked "no scores" instruction and RWI's own pre-existing, deliberate invariant (capability #22 in the strategic-orientation doc).

## 6. Relevance vocabulary

`RelevanceOutcome`: `EMAS_CONFIRMED` / `EMAS_STRONG_SIGNAL` / `EMAS_PLAUSIBLE_SIGNAL` / `RUNWAY_ONLY_NOT_EMAS_RELEVANT` / `INSUFFICIENT_EVIDENCE` — taken directly from the design doc's own Section 5, unmodified.

## 7. Evidence classes

`EvidenceClass`: `A_EXPLICIT_EMAS`, `B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED`, `C_PLANNING_OR_FEASIBILITY`, `D_FUNDING_OR_PROCUREMENT`, `E_EXISTING_INSTALLATION`, `F_INCIDENT_DRIVEN`, `G_GENERIC_RUNWAY_WORK` — the design doc's A-G, unmodified. Precedence (design doc's own literal wording, "with no confirmed installation yet" for STRONG): `E`/`F` present → `EMAS_CONFIRMED` (checked first, unconditionally); else `A`/`D` present → `EMAS_STRONG_SIGNAL`; else `B`/`C` present → `EMAS_PLAUSIBLE_SIGNAL`; else `G` present → `RUNWAY_ONLY_NOT_EMAS_RELEVANT`; else `INSUFFICIENT_EVIDENCE`. Class C's "sometimes explicit" dual nature (design doc row C) needs no special-cased enum member: C co-occurring with A already resolves to `EMAS_STRONG_SIGNAL` via the `A`-triggers-STRONG branch (checked before the `B`/`C`-triggers-PLAUSIBLE branch), and C without A falls through to the PLAUSIBLE branch — ordinary elif precedence, not a new rule.

## 8. Anoka verdict

`RUNWAY_ONLY_NOT_EMAS_RELEVANT`, `is_watch_worthy=False`, `is_canonical_admission_relevant=False`, `evidence_classes_matched=frozenset({G})` — locked as a permanent regression (`TestAnokaLockedRegression`), exactly matching the design doc's Section 8 verdict.

## 9. Early-signal verdicts

All five design-doc-listed early signals (RSA Alternatives Analysis, Runway End Safety Improvement Feasibility Study, Overrun Mitigation Alternatives, Insufficient RSA — Engineering Alternatives, Arresting System Feasibility) classify as `EMAS_PLAUSIBLE_SIGNAL` — watch-worthy, canonical-admission-relevant (subject to a future human approval step), never requiring the literal word "EMAS." Confirmed by explicit test that class B/C alone caps at `PLAUSIBLE`, never escalates to `STRONG`/`CONFIRMED` merely from RSA-shaped wording.

## 10. Explicit-EMAS verdicts

EMAS feasibility study (A+C) → `STRONG`. EMAS procurement/material acquisition (D) → `STRONG`. EMAS installation/replacement/maintenance (E) → `CONFIRMED`. "Engineered Materials Arresting System design" (A) → `STRONG`. Capitalization/punctuation variants of the `basis` string (`"EMAS"`, `"emas"`, `"E.M.A.S."`, full spelled-out name, all-caps, extra whitespace) never change the outcome — proven directly, since classification depends only on `evidence_class`, never on parsing `basis` text (a deliberate consequence of the input-contract decision in §4, not incidental).

## 11. Generic-runway false-positive verdict

Class G, alone or in any quantity/combination (tested up to 12 simultaneous G observations), never escalates past `RUNWAY_ONLY_NOT_EMAS_RELEVANT`. An empty observation tuple (representing extraction correctly declining to produce any observation for unrelated "RSA acronym in unrelated prose"/"arresting used in unrelated context"/"another airport's EMAS mention" text) safely defaults to `INSUFFICIENT_EVIDENCE`, never a positive outcome. Precision for those specific text-level attacks remains, per the design doc's own Section 15, an extraction-layer responsibility this pure evaluator has no raw text to get wrong.

## 12. Historical/current distinction

No date engine added (design doc's own instruction). `TemporalQualifier` is reused verbatim; a positive observation explicitly tagged `HISTORICAL_FACT` is excluded from contributing to the outcome UNLESS its class is `E_EXISTING_INSTALLATION` (an install's existence is a present-tense structural fact regardless of install date — "installed in 2008" remains `EMAS_CONFIRMED` today) or another positive observation in the same evaluation carries a non-historical, non-unknown temporality (a current follow-on corroborates the historical one is still live). `UNKNOWN` (the default) is NEVER discounted — only an explicit historical tag triggers the discount, so omitted temporal information never silently loses evidence weight. Result: "installed EMAS in 2008" → `CONFIRMED`; "2027 EMAS replacement project" → `STRONG`; a bare, uncorroborated decades-old overrun with no EMAS mention → `INSUFFICIENT_EVIDENCE` (discounted); the same overrun tagged current → `PLAUSIBLE`, matching the design doc's own Section 8 Scenario 5 verdict exactly.

## 13. Contradiction semantics (derived, not pre-specified by the design doc)

The design doc named "contradictory evidence" as a threat/test case (Section 24 items D/G, mission Section 11 item C) without fully specifying evaluator-level semantics. Derived rule, documented in the module's own docstring: a `CONTRADICTING`-polarity observation NEVER changes the outcome computed from `POSITIVE`-polarity evidence (an document's own self-serving negative claim must not silently override structurally-matched positive evidence — the same reasoning the design doc itself applies against "free-text-only, untraceable" relevance decisions, Section 13). It is always surfaced, never hidden, via `contradicting_evidence_classes` and a reason-string note, mirroring this codebase's existing "advisory, never gating, never hidden" pattern (`ExistingSignalReconciliationDecision.advisory_candidate_signal_ids`). Contradicting evidence alone (no positive evidence) produces `INSUFFICIENT_EVIDENCE`, never a positive or negative-confirmed outcome — this module has no "confirmed absent" state, matching the five-member vocabulary's own closed set.

## 14. Multi-evidence semantics

Generic-runway-only evidence classifies `RUNWAY_ONLY_NOT_EMAS_RELEVANT`; adding a later class-C observation to the same evidence set deterministically raises the outcome to `EMAS_PLAUSIBLE_SIGNAL` (order-independent — see §16). Positive + contradicting evidence of the same class: outcome from positive evidence is preserved, contradiction surfaced (see §13). Multiple weak `PLAUSIBLE`-tier observations (several B/C facts) never combine into `STRONG`/`CONFIRMED` — there is no counting, weighting, or threshold-crossing logic anywhere in the evaluator, only set-membership over which classes are present.

## 15. International/source-neutral verdict

**Source-neutral by construction, confirmed by test.** The evaluator consumes only `EvidenceClass` tags and `basis` audit strings never parsed for content — proven directly with a Portuguese-language and a Japanese-language `basis` string, both classifying identically to their English equivalents. A dedicated test (`test_no_us_specific_terminology_referenced_anywhere_in_executable_source`, word-boundary regex over the module's source with all docstrings stripped) confirms `MAC`/`FAA`/`USAspending`/`AIP`/`BIL`/`MSP`/`ANE`/`Granicus` appear nowhere in the evaluator's executable code (only in its own explanatory docstring prose, which legitimately references them as *rationale for what this module deliberately does not do* — exactly the same pattern `promotion_policy_evaluation.py`'s own docstring already uses).

## 16. Determinism verdict

Confirmed by test: identical evidence supplied in different call order produces an equal `EmasRelevanceDecision` (set-membership + `dict.fromkeys` deduplication, mirroring `promotion_policy_evaluation.py`'s own `tuple(dict.fromkeys(claims))` pattern verbatim); duplicate identical or near-identical same-class observations never change the outcome or `evidence_classes_matched`; the same input always produces an equal decision object across repeated calls. No current-time dependency anywhere (AST-verified — no `.today()`/`.now()`/`.utcnow()` call exists in the module).

## 17. Information-firewall verdict

**Confirmed, AST- and source-verified.** No import of `sqlalchemy`, `httpx`, `requests`, `urllib`, `socket`, `app.database`, `app.models`, or any UAC1-5/persistence/signal-creation service module. No `Session`/`Airport`/`SourceAssertion`/`UnknownAirportCandidate`/`Signal`/`SessionLocal` name imported anywhere. No `.commit(`/`.flush(`/`.query(`/`.add(`/`session.` token anywhere in the source. No `open(`/network-call token anywhere. This module creates, reads, updates, or deletes nothing — no `UnknownAirportCandidate`, no `Airport`, no `SourceAssertion`, no relevance-assessment row (that table does not exist — ERG2 is explicitly out of scope), and calls no UAC4/UAC5/Signal/promotion/publish code path at all.

## 18. Files created

- `app/services/emas_relevance_evaluation.py` (production, pure logic only — no ORM, no model, no migration, no persistence, no UAC4 wiring, no CLI change).
- `tests/test_emas_relevance_evaluation.py` (68 tests).
- `docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md` (this report).

No other production file was touched or was necessary — no STOP/justification required for additional production files.

## 19. Defects/design ambiguities found

None are code defects (this is a from-scratch new module with no prior behavior to regress). Three genuine design ambiguities the design doc left underspecified were resolved during implementation, each documented in the module's own docstring at the point of decision:

1. **Contradiction semantics** (design doc named the threat, never specified evaluator-level behavior) — resolved per §13 above: never gating, always surfaced.
2. **Whether `UNKNOWN` temporality should be discounted like `HISTORICAL_FACT`** — resolved as NO: only an explicit historical tag triggers the discount, so a caller/extractor that has not yet determined temporal context never silently loses evidence weight (a footgun the design doc's own "do not add a date engine unless justified" instruction implicitly warns against; the alternative — discounting on absence of information — would have made every simple, temporal-agnostic test fixture across the whole matrix silently fail closed, an unjustifiably large behavior change from a design doc that never asked for it).
3. **Whether class E (existing installation) should be exempt from the historical-fact discount** — resolved as YES: an installation's existence is a present-tense structural fact regardless of install date, distinguishing it from B/F's genuinely time-sensitive "is this still an active safety concern/opportunity" framing — directly required to produce the design doc's own worked example ("installed in 2008" must not read identically to a genuinely stale, no-longer-relevant historical mention).

## 20. Corrections made

Two test-authoring corrections during this slice (not production-code defects): the initial `TestInternationalSourceNeutral`/`TestInformationFirewall` source-substring checks false-positived on the module's own legitimate docstring prose (which explains, in English, what the module deliberately avoids, naming FAA/MAC/app.models as the very things being avoided) and on an accidental substring match (`"BIL"` inside `"FEASIBILITY"`). Fixed by scanning only the module's executable source with all docstrings stripped, and by switching to word-boundary regex matching instead of raw substring containment — corrected in the test file, not the production module, which required no change.

## 21. Focused tests

`tests/test_emas_relevance_evaluation.py`: **68 passed, 0 failed** (final run, after the two test-authoring corrections in §20).

## 22. Full pytest

`python -m pytest -q`: **3294 passed, 0 failed**, 4532 warnings (deprecation/pytest-cache warnings only, pre-existing and unrelated to this slice), 772.23s. Prior baseline (before this slice) was 3226 passed — the +68 delta is exactly `tests/test_emas_relevance_evaluation.py`'s own test count, confirming zero regressions anywhere else in the suite.

## 23. py_compile

`python -m py_compile app/services/emas_relevance_evaluation.py tests/test_emas_relevance_evaluation.py` — clean, no errors.

## 24. git diff --check

Clean (no whitespace-error findings; both files are new/untracked, so no tracked-diff hunks existed to flag).

## 25. Real DB before/after

Before (§2): SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. After (re-verified at mission end, post full-suite run): identical — SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. HEAD unchanged at `f9ba7f0d87120df7e6736d82e860d4036be114dd`. This mission never opened the real database at all, read-only or otherwise; every test in this slice and in the full suite runs entirely in-process against plain Python objects or ephemeral in-memory SQLite fixtures.

## 26. Future ERG2 persistence seam

A future append-only `unknown_airport_candidate_relevance_assessments` row (design doc §21 schema sketch) would be populated, per assessment, from exactly: `candidate_id` (supplied by the ERG2 persistence bridge, never by this evaluator — mirrors `PromotionPolicyPersistenceResult.source_assertion_id` being attached by `persist_promotion_policy()`, never by `evaluate_promotion_policy()`), `outcome.value` → `outcome` column, `reason` → `reason` column (already a complete, deterministic, human-readable string — no further formatting needed), `evidence_classes_matched` → `evidence_classes_matched` column (join `sorted(c.value for c in decision.evidence_classes_matched)`, mirroring the existing comma-joined-`frozenset` convention already used for `identifiers`/`names`/`runway_pairs` elsewhere), `is_watch_worthy`/`is_canonical_admission_relevant` → either persisted directly as two boolean columns, or left un-persisted and recomputed on read from `outcome` alone (both are pure functions of `outcome`, so persisting them is redundant-but-harmless denormalization, not a requirement) - recommend NOT persisting them as separate columns (avoid a redundant column at ERG2, since ERG2's own `source` column will always be `AUTOMATIC` for evaluator-produced rows and `outcome` alone reconstructs both booleans deterministically). `source="AUTOMATIC"` (the human-sourced counterpart, `CONFIRM_EMAS_RELEVANT`/`MARK_NOT_EMAS_RELEVANT`/`DEFER_RELEVANCE_REVIEW`, is ERG3's job, not this evaluator's). `contradicting_evidence_classes` — recommend persisting alongside `evidence_classes_matched` in its own comma-joined column, since it is operator-visible information this evaluator computes but ERG2 has no other way to reconstruct without re-running the evaluator against the same observation set. `assessed_at` is supplied by the persistence bridge (this module reads no current time, per §17), mirroring every other timestamped table in this codebase.

## 27. Future UAC4 gate seam

Per the design doc's own Section 6/8 rule (Option B), the future `_require_emas_relevance_approved()` precondition on `create_airport_from_approved_candidate()` (ERG4) should require: the candidate's LATEST relevance assessment (by recency, mirroring `_require_current_review()`'s own staleness discipline) is a HUMAN-sourced approval action (`CONFIRM_EMAS_RELEVANT`, ERG3's own vocabulary) whose underlying automatic classification was one of `{EMAS_CONFIRMED, EMAS_STRONG_SIGNAL, EMAS_PLAUSIBLE_SIGNAL}` — i.e. `EmasRelevanceDecision.is_canonical_admission_relevant is True` at the time of approval. `RUNWAY_ONLY_NOT_EMAS_RELEVANT` and `INSUFFICIENT_EVIDENCE` (`is_canonical_admission_relevant is False`) must NEVER become eligible for a `CONFIRM_EMAS_RELEVANT` approval in the first place — that refusal belongs in ERG3's own write-time validation (mirroring `_require_current_review()`'s fail-closed shape), not in ERG4's gate, so that a stale/tampered approval record cannot retroactively "grandfather in" evidence that would never have been approvable at write time. This module implements neither check — it only defines the exact boolean (`is_canonical_admission_relevant`) both future checks must key off of.

## 28. git status

New, untracked files only: `app/services/emas_relevance_evaluation.py`, `tests/test_emas_relevance_evaluation.py`, `docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md` (plus the pre-existing untracked docs/screenshots from earlier missions in this session, unrelated to and untouched by this slice). No tracked file modified. No commit made.

## 29. READY_FOR_ERG1_REVIEW_CHECKPOINT

**YES** — pure logic only, zero schema/DB/network/ORM footprint (AST- and behavior-verified), 68 focused tests plus a full-suite run covering every attack-matrix letter the mission specified (A-T), the real DB confirmed untouched, all files scoped exactly to the mission's expected file list with no undeclared production changes.

## 30. Exact recommended next step

A separate, explicitly-authorized ERG1 adversarial review checkpoint (per this mission's own Section 21 commit policy) — independently re-deriving the evaluator contract against the design doc, re-running the full attack matrix, and specifically re-checking the three derived-not-specified design ambiguities in §19 (contradiction semantics, the UNKNOWN-vs-HISTORICAL_FACT discount asymmetry, and class E's discount exemption) before this slice is committed. Only after that review authorizes a commit should ERG2 (persistence bridge + new append-only table + migration) begin.

RWI_ERG1_EMAS_RELEVANCE_EVALUATOR_IMPLEMENTATION_COMPLETE

---

## 31. Adversarial Review Findings (independent review, second mission)

Starting HEAD for the review: `f9ba7f0d87120df7e6736d82e860d4036be114dd` (== `origin/main`), DB checkpoint SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` — reconfirmed at review start, independent of the implementation mission's own claims.

**Files re-read in full for this review** (not trusted from the implementation report's summaries): `app/services/emas_relevance_evaluation.py`, `tests/test_emas_relevance_evaluation.py`, `docs/architecture/rwi-emas-relevance-gate-design.md`. `app/services/promotion_policy_evaluation.py`, `app/services/discovery_candidate_fragment.py`, UAC4, and the UAC5 CLI were already read fresh in full earlier in this same session (design mission + implementation mission) and re-cited rather than re-opened, since no code in those files changed between then and this review.

### Independent input-contract re-derivation

`evaluate_emas_relevance(observations: tuple[EmasEvidenceObservation, ...], context: EmasRelevanceContext = ...) -> EmasRelevanceDecision`. Verdict: **sound.** No ORM leakage (no `app.models`/`app.database` import, verified by AST + docstring-stripped source scan). No candidate id anywhere in the input or logic — candidate scoping is entirely the future persistence layer's job, never this evaluator's, so candidate identity cannot leak into a decision. No source-specific vocabulary in the evaluator itself (only in its own explanatory docstring prose, confirmed by a corrected word-boundary regex test, not raw substring). Malformed combinations (non-enum `evidence_class`/`temporality`/`polarity`, `None`, empty/whitespace `basis`) all raise `EmasRelevanceInputError` at construction — confirmed by direct testing, including cases the implementation's own test suite had not yet covered (`None` for each of the three enum fields). Evidence provenance is preserved via `basis` (audit-only, never parsed) — nothing is lost, only left uninterpreted, which is correct for a pure evaluator. Identity evidence and relevance evidence are never conflated: the evaluator has no field resembling a name/identifier/runway-topology match — those remain the guard's own governed vocabulary (`EvidenceCategory.IDENTIFIER`/`NAME`/`RUNWAY_TOPOLOGY`/`ISSUER`/`LOCATION`), entirely untouched and unimported here.

### Independent output-contract re-derivation

Six fields, all justified, none redundant. `outcome` (5-member vocabulary, matches design doc verbatim). `reason` (deterministic, template-built). `evidence_classes_matched` / `contradicting_evidence_classes` (frozensets, positive vs. negative evidence, kept separate rather than one field with a sign — correct, since a class present in BOTH sets simultaneously is a real, meaningful, testable state — contradiction alongside confirmation — that a single field could not represent). `is_watch_worthy` / `is_canonical_admission_relevant` — two fields, currently computed identically; kept separate per the design doc's own explicit S6/S7 distinction, which this review independently re-derives as correct (a future ERG4 admission bar COULD tighten beyond the watch bar without a breaking rename — see the dormant-installation finding below for exactly why this separation already matters even before any divergence is implemented). No hidden score anywhere — confirmed structurally (the outcome derivation is pure set-intersection against three frozensets, never a counter, sum, or threshold compare).

### Anoka — reproduced independently

`RUNWAY_ONLY_NOT_EMAS_RELEVANT`, `is_watch_worthy=False`, `is_canonical_admission_relevant=False`, `evidence_classes_matched=frozenset({G})`. Reproduced by direct interactive execution against the exact evidence shape (pavement reconstruction + electrical vault, no other classes) — matches both the design doc's own worked answer and the implementation's locked regression test. **Confirmed sound, unchanged.**

### Explicit-EMAS strong positives — attacked, sound

All eleven listed strong-positive phrasings (feasibility study, both EMAS/"Engineered Material(s) Arresting System" spellings, procurement, material acquisition, installation, replacement, maintenance, repair, bed replacement, arresting-system design tied to runway safety) map correctly to `EMAS_STRONG_SIGNAL` or `EMAS_CONFIRMED` per class. Two items (repair, bed replacement) were **not yet tested** by the implementation's own suite — added as regression tests (`TestAdditionalExplicitEmasRegressions`), both confirmed `EMAS_CONFIRMED` under class E, consistent with "replacement/maintenance of a known bed" already being the design doc's own literal class-E definition.

### Early signals — attacked, sound, correctly conservative

All seven mission-listed early signals (RSA Alternatives Analysis, Insufficient RSA, Runway End Safety Improvement Feasibility Study, Overrun Mitigation Alternatives, Arresting System Feasibility, declared-distance reduction, safety-area-specific compliance study) classify `EMAS_PLAUSIBLE_SIGNAL` — never higher (no early signal, alone, reaches `STRONG`/`CONFIRMED`, correctly requiring an explicit EMAS/funding/installation class to cross that line) and never lower (none silently drop to `INSUFFICIENT_EVIDENCE` or get miscategorized as `RUNWAY_ONLY`). Declared-distance reduction and the safety-area-specific compliance study were **not yet tested** — added (`TestRsaFalsePositiveDefense`). Verdict: the implementation is neither too aggressive (G never leaks upward) nor too conservative (early B/C signals are never suppressed to `INSUFFICIENT_EVIDENCE`).

### Generic-runway false positives — attacked, sound

All twelve mission-listed generic-work items, and the specifically-attacked mixed title ("Runway 18-36 Reconstruction and Safety Improvements") when correctly tagged G-only, stay at `RUNWAY_ONLY_NOT_EMAS_RELEVANT` regardless of quantity (tested up to 12 simultaneous G observations, and now also the mixed-title case explicitly — `test_mixed_title_generic_safety_words_do_not_elevate_generic_runway_work`, added this review). Generic "safety" words alone never elevate relevance — confirmed structurally, since the word "safety" appearing in a `basis` string has zero effect on classification (only `evidence_class` matters).

### RSA false-positive defense — HIGH-PRIORITY FINDING, addressed by documentation + tests, not logic change

**This is the review's most substantive finding.** Class B is a flat bucket: any observation tagged B (mowing contract, signage replacement, routine inspection, or a genuine deficiency) is treated identically — `EMAS_PLAUSIBLE_SIGNAL`. The evaluator has no raw text and therefore cannot itself distinguish "RSA mowing contract" from "RSA deficiency requiring mitigation." **Verdict: this is correctly an extraction-layer responsibility, not an ERG1 defect** — the design doc's own Section 15 already assigns exactly this precision requirement to extraction for the closely analogous "arresting used in unrelated context" case, and routine RSA administrative work is squarely the same category of problem: it must be extraction-tagged as G (or produce no observation at all), never as B, in the first place. Re-deriving this independently (not merely trusting the design doc's own division of labor): a pure evaluator over already-classified tags structurally cannot recover information the classification step itself discarded, so pushing this precision requirement downstream is the only coherent design short of parsing raw text inside the evaluator, which the design doc's own S15/S17 explicitly forbids. **Correction made:** strengthened `EvidenceClass.A`/`EvidenceClass.B`'s own docstrings with explicit, prominent warnings against exactly this leakage (arresting-gear/military conflation for A; routine/administrative RSA work for B), and added `TestRsaFalsePositiveDefense` (6 tests) + `TestContextLeakageDefense` (3 tests) that lock the safe-by-construction behavior for both the "correctly tagged G" and "no observation produced" extraction outcomes, and one positive control proving a genuine deficiency still correctly reaches `EMAS_PLAUSIBLE_SIGNAL`. No evaluator logic changed — this is a documentation and regression-coverage correction only, deliberately, per the mission's own correction policy (a defect that would require new evidence sub-classes to fix would be a "STOP before widening" case; this one does not require that, since the fix is entirely at the extraction-classification boundary, not the evaluator's own set-membership logic).

### Context leakage (arresting gear, cross-airport, historical background) — attacked, correctly out of scope

Military aircraft arresting gear, another airport's EMAS mention, and a purely-background historical EMAS mention about an unrelated topic are all, independently re-derived, extraction/identity-layer concerns: (a) civil-EMAS-vs-military-arrestor disambiguation is a class-A extraction precision requirement (now explicitly documented in the class's own docstring, per the correction above); (b) cross-airport leakage is structurally impossible for the evaluator to introduce, since it only ever receives observations ALREADY scoped to one candidate by the guard/UAC3 layer beneath it — re-verified this is consistent with the design doc's own Section 13/15 per-candidate evidence-scoping principle, unchanged and unaffected by anything in this module; (c) historical background mentions fall under the already-analyzed temporal-discount rules (§12), no new case. No evaluator change required; three tests added (`TestContextLeakageDefense`) documenting these boundaries explicitly rather than leaving them as unstated assumptions.

### Historical vs. current — three implementation decisions independently attacked

**(A) "EMAS installed 2008" → `EMAS_CONFIRMED`, unaffected by historical tagging.** Re-derived as correct: an installation's existence is a fact about the present, not merely the past, absent an explicit removal/decommission claim (which this evidence-class vocabulary has no member for and should not invent speculatively).
**(B) "EMAS replacement planned for 2027" → `EMAS_STRONG_SIGNAL`.** Correct — `PLANNED_FUTURE_ACTION` is not `HISTORICAL_FACT`, so no discount applies regardless of class.
**(C) "Airport currently has an EMAS bed" → `EMAS_CONFIRMED`** (tagged `CURRENT_STATE_AS_OF_DOCUMENT_DATE` or `UNKNOWN`, class E either way) — verified directly; unaffected by temporality since E is exempt from the discount outright.
**(D) Undated "EMAS replacement project"** (temporality `UNKNOWN`, class D) → `EMAS_STRONG_SIGNAL`, since `UNKNOWN` is never discounted. **(E) Historical article about an old installation** → depends entirely on how it is class-tagged; if tagged E, `EMAS_CONFIRMED` regardless of temporality (per A); if tagged B/F and explicitly marked `HISTORICAL_FACT` with no current follow-on, correctly discounted to `INSUFFICIENT_EVIDENCE`/`RUNWAY_ONLY` per the design doc's own Scenario 5 verdict.

**Direct answer to the mission's own posed question — "can UNKNOWN temporality accidentally make clearly stale material look current?": YES, if and only if the extraction layer fails to tag a genuinely determinable historical fact as `HISTORICAL_FACT` and instead leaves it at the `UNKNOWN` default.** This is a real, non-hypothetical risk, independently confirmed by direct execution (see the probe run in this review's own transcript: an untagged "decades-old overrun, temporality not tagged" observation classifies `EMAS_PLAUSIBLE_SIGNAL`, identical to a genuinely fresh incident). **Verdict: not an implementation defect, but a real, load-bearing risk that was under-documented before this review.** The alternative (discounting `UNKNOWN` the same as `HISTORICAL_FACT`) would make the evaluator fragile to ordinary extraction incompleteness in the opposite, arguably worse direction (silently losing weight for genuinely current evidence any time an extractor simply omits temporality) — re-derived independently as still the correct tradeoff, not merely trusted from the implementation report. **Correction made:** added an explicit "KNOWN, FLAGGED, NOT-FIXED-HERE OPEN QUESTION #2" section to the module's own docstring making this asymmetry and its risk explicit and load-bearing for every future extraction-layer caller, and confirmed (rather than merely asserted) the risk scenario is now directly demonstrated by `test_unknown_temporality_is_never_discounted` reasoning (already present) — no new test was strictly required since the risk is a *documentation* gap, not an untested code path (the behavior itself was already correctly tested).

### Existing-installation semantics / inventory vs. opportunity — HIGH-PRIORITY FINDING, flagged, not fixed

Independently re-derived, this review confirms a genuine, real conflation: `EMAS_CONFIRMED` currently means both "this airport demonstrably has/had EMAS infrastructure" (an inventory fact, arguably permanent) and "this deserves the same automatic watch-queue surfacing and canonical-admission eligibility as an active opportunity" (`is_watch_worthy`/`is_canonical_admission_relevant`, both unconditionally `True` for `EMAS_CONFIRMED`) — even for a dormant, 15-year-old installation with zero current activity, confirmed by direct execution during this review. **Verdict: this is NOT an ERG1 implementation defect** — the parent design doc's own Section 7 defines watch-worthiness as `EMAS_CONFIRMED`/`STRONG`/`PLAUSIBLE` unconditionally, with no dormancy carve-out anywhere in its text; ERG1 correctly implements that locked definition exactly as specified. It IS a genuine, real, currently-unresolved design question the parent design doc did not think through to this level of granularity. Per this mission's own correction policy ("if the issue is a design ambiguity requiring new vocabulary: STOP before widening into ERG2" and "it is acceptable to revise ERG1 vocabulary if genuinely necessary, but document why"): this review deliberately does **NOT** invent a third boolean or a "dormant" outcome member at this slice, since doing so would be new vocabulary the parent design doc never specified and would risk conflicting with a future, deliberate design-level resolution. **Correction made:** added an explicit "KNOWN, FLAGGED, NOT-FIXED-HERE OPEN QUESTION" to the module's own docstring, plus `TestDormantInstallationFlaggedFinding` (1 test) that documents current, accepted behavior explicitly rather than silently treating it as settled — and this finding is carried into §37 (recommended next mission) below as a concrete blocker to resolve, at the design level, before ERG4 (the UAC4 canonical-admission gate) is built, since ERG4 will otherwise inherit this ambiguity unexamined.

### Contradiction semantics — re-attacked with all four mission scenarios

**(A) EMAS feasibility study + cancellation** and **(B) EMAS planned + no-alternative-selected** are both structurally the same shape (positive class-A/D/C evidence + a `CONTRADICTING`-polarity observation of the same class) — confirmed: outcome is preserved from positive evidence, contradiction surfaced in `contradicting_evidence_classes` and `reason`, never silently hidden. **(C) "airport has EMAS" contradicted by canonical evidence naming a different airport** — re-derived as out of scope for this evaluator entirely: that is an IDENTITY dispute (which airport does this evidence belong to), not an EMAS-relevance dispute, and per the design doc's own per-candidate evidence-scoping principle, the guard/identity layer beneath this evaluator is responsible for ensuring this evaluator only ever receives evidence already resolved to one candidate — inventing contradiction-handling for a cross-identity dispute inside this evaluator would blur exactly the "identity truth vs. business relevance" separation the whole design doc's governing principle forbids. **(D) weak RSA signal + explicit evidence a standard RSA was constructed instead** — added as `TestContradictionScenarioD`: confirmed `EMAS_PLAUSIBLE_SIGNAL` is preserved (not silently downgraded to `RUNWAY_ONLY`/`INSUFFICIENT_EVIDENCE`), contradiction surfaced. **Direct answer to the mission's own explicit question — "should contradictions truly never affect relevance class/watch/admission relevance? If 'surfaced only' allows a clearly disproven EMAS project to remain admission-relevant, this is a genuine semantic defect":** re-derived independently, NOT a defect, for a specific, defensible reason: `is_canonical_admission_relevant` does not mean "admission is justified" — it means "admission COULD be justified, subject to a SEPARATE, MANDATORY future human approval step" (§5, §27) that this module does not implement or bypass. A contradicted project remaining `is_canonical_admission_relevant=True` does not grant it canonical admission — a human reviewer, seeing both the positive evidence AND the surfaced contradiction (never hidden), makes that call at the ERG3/ERG4 layer. Silently suppressing the outcome instead would hide the positive evidence's own existence from that human reviewer entirely, which is strictly worse for traceability (design doc S13) than surfacing both and trusting the human gate that already exists by design. **Confirmed sound, no code change.**

### Multi-evidence progression — re-attacked, sound

Generic → RSA alternatives → EMAS feasibility → EMAS procurement, and the same sequence reversed, produce identical final decisions (order-independence re-verified directly). Duplicate evidence (identical and near-identical same-class observations, up to 5x) never changes the outcome. No score accumulation anywhere — re-confirmed by inspection of the outcome-derivation code, which performs only frozenset intersection tests, never a length/count comparison.

### Multiple weak signals — re-attacked, sound

Several individually-weak class-B/C observations together still cap at `EMAS_PLAUSIBLE_SIGNAL` — confirmed they never "vote" their way to `STRONG`/`CONFIRMED`. This is structurally guaranteed (not merely tested) by the elif-precedence design: `STRONG`/`CONFIRMED` require specific OTHER classes (A/D or E/F) to be present at all; no quantity of B/C evidence can ever satisfy that membership test.

### Inventory vs. opportunity distinction — see existing-installation finding above (not repeated)

### Signal-eligibility firewall — re-verified, sound

No field named or aliased `signal_eligible` (or any synonym) exists anywhere in `EmasRelevanceDecision` or the module — confirmed by direct field-name inspection (`outcome`, `reason`, `evidence_classes_matched`, `contradicting_evidence_classes`, `is_watch_worthy`, `is_canonical_admission_relevant`). No import of `app.services.governed_signal_creation` or `Signal` anywhere (AST-verified). Canonical admission and Signal creation remain structurally two, later, separate, human-gated steps this module has no path to short-circuit.

### International / source-neutral — re-attacked, sound, strengthened

Re-confirmed the Portuguese and Japanese `basis`-text fixtures classify identically to English equivalents (proving `basis` content never participates in classification). The source-neutrality AST/regex test was already corrected once during implementation (docstring-stripped scan, word-boundary regex) — re-verified this correction is itself sound (re-ran it, confirmed `MAC`/`FAA`/`USAspending`/`AIP`/`BIL`/`MSP`/`ANE`/`Granicus` genuinely absent from executable code, present only in explanatory docstring prose). No stronger non-US case was added beyond what already existed — the two Unicode fixtures (pt-BR, ja) were judged sufficient, since the property under test (basis text never affects classification) is already fully proven by either one; a third language would not add coverage, only volume.

### Determinism — re-attacked across every listed permutation, sound

Reversed order, arbitrary order, duplicates, Unicode, and empty evidence all re-verified to produce identical/expected results. No `.today()`/`.now()`/`.utcnow()` call anywhere (AST-verified, re-run this review).

### Malformed input — re-attacked, one real test gap found and closed

The implementation's own test suite covered wrong-type strings for all three enum fields and empty/whitespace `basis`, but had **not** tested `None` for `evidence_class`, `temporality`, or `polarity` explicitly, nor `basis=None`. **Correction made:** added five tests (`test_evidence_class_cannot_be_none`, `test_temporality_cannot_be_none`, `test_polarity_cannot_be_none`, `test_basis_cannot_be_none`, `test_wrong_object_type_entirely_raises_at_construction`) — all pass; the existing `isinstance()` guards already correctly reject `None` (since `isinstance(None, EvidenceClass)` is `False`), so this was a genuine test-coverage gap, not a code defect. No malformed input anywhere silently coerces to a positive classification — confirmed for every listed attack shape.

### Information firewall — re-verified independently, sound

Re-ran the full AST-based import/name/current-time/network/session-call checks directly against the (now docstring-strengthened) module; all pass. Independently confirmed by direct source inspection (not merely trusting the existing tests) that no `Session`, ORM model, database call, network call, or governed-service import exists anywhere in the 200+-line module.

### ERG2 persistence seam — re-derived, ERG1 output judged sufficient, one addition made

Independently re-derived the minimum durable fields (candidate id — supplied externally, never by this evaluator; `outcome`; `reason`; `evidence_classes_matched`; `contradicting_evidence_classes`; `source="AUTOMATIC"`; `assessed_at` — supplied externally). Confirms the implementation report's own §26 analysis is correct and complete, with one addition: the mission's own §22 explicitly names "evaluator version" as a likely persisted concern — **correction made:** added a bare `EVALUATOR_VERSION = "1"` module-level string constant (no behavior implication, purely a stamping seam for a future persistence bridge), confirmed present and correctly exported. `is_watch_worthy`/`is_canonical_admission_relevant` remain correctly recommended as NOT independently persisted (both are pure, always-reconstructible functions of `outcome`).

### UAC4 gate seam — re-derived, one refinement to the implementation report's own claim

Independently re-derived from the design doc's own Section 6/8: the future gate should require a HUMAN-sourced approval whose underlying automatic classification had `is_canonical_admission_relevant=True` at approval time. **Refinement over the implementation report's own §27 wording:** the mission's own §23 explicitly warns "these may not all justify the same admission semantics" across explicit-current-EMAS-project / existing-installation / early-signal / runway-only / historical-only cases — the implementation report's §27 treated all three positive outcomes uniformly, which this review confirms is exactly what the CURRENT design doc specifies (Option B draws no distinction among the three), but which this review's own existing-installation finding above shows is a real, open question worth resolving BEFORE ERG4 is built, not an oversight in ERG1 itself. Flagged, not fixed, consistent with the finding above.

### Test quality — re-reviewed all tests (68 original + 25 added this review = 93)

No test was found to merely mirror the implementation's own internal logic back at itself as an assertion (every outcome asserted was independently derivable from the design doc's own worked examples or from first-principles class-membership reasoning, not from reading the implementation code first). No keyword-only fixtures — `basis` strings are deliberately varied (including nonsense/Unicode/punctuation-noise) specifically to prove they carry zero classification weight, the opposite failure mode. Contradiction and historical/current attacks were judged insufficient by this review (only 3 and 7 tests respectively) and were extended (contradiction: +1 scenario-D test; historical/current: coverage judged adequate after re-derivation, no gap found beyond the already-covered cases). No inventory-vs-opportunity distinction existed before this review — added. No multi-weak-evidence attack gap found (already covered: `test_multiple_weak_plausible_signals_do_not_combine_into_strong_or_confirmed`). The AST-based tests were found to have TWO real false-positive bugs during the implementation mission itself (already fixed then, re-verified sound now — see §20). No overly-broad `except:` clauses exist anywhere in either file (confirmed by inspection — every `pytest.raises()` names a specific exception type). No test name overclaims its own assertions (spot-checked a sample of ~15 test names against their bodies; all matched).

### Defects found (this review)

**Zero implementation-code defects.** Two genuine, real, previously under-documented risks (RSA-bucket granularity dependent on extraction precision; `UNKNOWN`-temporality asymmetry) and one genuine, real, unresolved design-level question inherited from the parent design doc (dormant-installation watch/admission conflation) — none are ERG1 bugs, all are now explicitly documented rather than silently assumed.

### Corrections made (this review)

1. Strengthened `EvidenceClass.A`/`EvidenceClass.B` docstrings with explicit extraction-layer boundary warnings (arresting-gear/military conflation; routine RSA administrative work).
2. Added two "KNOWN, FLAGGED, NOT-FIXED-HERE" sections to the module's own top-level docstring (dormant-installation conflation; `UNKNOWN`-temporality asymmetry).
3. Added `EVALUATOR_VERSION = "1"` module constant (ERG2 seam, per mission §22).
4. Added 25 new regression tests: `TestRsaFalsePositiveDefense` (6), `TestContextLeakageDefense` (3), `TestContradictionScenarioD` (1), `TestAdditionalExplicitEmasRegressions` (3), `TestDormantInstallationFlaggedFinding` (1), `TestEvaluatorVersionSeam` (1), plus 5 `None`-input malformed-input tests appended to the existing `TestMalformedEvidence` class, plus 5 additional early-signal/false-positive tests folded into the new classes above (declared-distance, safety-area-specific compliance study, mixed-title false positive).

No evaluator outcome-computation logic was changed anywhere in this review — every correction is documentation (docstrings), a version-stamping constant, or test coverage.

### Focused tests (this review, final)

`tests/test_emas_relevance_evaluation.py`: **93 passed, 0 failed** (68 from the implementation mission + 25 added this review).

### Full pytest (this review, final)

`python -m pytest -q`: **3319 passed, 0 failed**, 4532 warnings (pre-existing deprecation/pytest-cache warnings only). Prior baseline (post-implementation-mission) was 3294 — the +25 delta is exactly this review's own added test count, confirming zero regressions anywhere else in the suite.

### py_compile (this review)

`python -m py_compile app/services/emas_relevance_evaluation.py tests/test_emas_relevance_evaluation.py` — clean.

### git diff --check (this review)

Clean, exit code 0.

### Real DB before/after (this review)

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. After (re-verified post full-suite run, immediately before commit): identical on every dimension. HEAD unchanged at `f9ba7f0d87120df7e6736d82e860d4036be114dd` until the commit step below.

### Files committed and design-doc inclusion decision

Independently determined: `docs/architecture/rwi-emas-relevance-gate-design.md` **belongs in this commit** — it is ERG1's own direct, sole authoritative parent specification (cited by name throughout `emas_relevance_evaluation.py`'s own docstring and both reports), and leaving it permanently untracked would break the evidence-traceability chain the design doc's own Section 13 requires for the architecture it describes. All other pre-existing untracked docs/screenshots in the working tree (from unrelated, earlier missions in this session — `evidence-to-signal-semantics-design.md`, `existing-signal-reconciliation-r4-human-resolution-design.md`, `fh-d4-signal-disposition-design.md`, `reviewer-action-human-signal-promotion-slice9-design.md`, `rwi-full-evidencebag-persistence-design.md`, `rwi-governed-new-airport-discovery-design.md`, `rwi-post-d4d8-strategic-orientation.md`, `sfo-2026-emas-temporal-evidence-pilot.md`, `docs/research/`, `docs/ui/*`) are explicitly excluded, per the mission's own "do not include unrelated docs" instruction — none of them are ERG1's own parent specification.

Exact files committed:
- `app/services/emas_relevance_evaluation.py`
- `tests/test_emas_relevance_evaluation.py`
- `docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md`
- `docs/architecture/rwi-emas-relevance-gate-design.md`

RWI_ERG1_EMAS_RELEVANCE_EVALUATOR_REVIEWED_COMMITTED_AND_PUSHED
