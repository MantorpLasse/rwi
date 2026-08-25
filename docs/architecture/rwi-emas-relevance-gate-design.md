# RWI EMAS Relevance Gate — Design Review

Status: DESIGN ONLY. NO CODE CHANGE. NO DATABASE WRITE. NO MIGRATION. NO COMMIT. NO PUSH. NO LIVE APPLY.

Starting HEAD: `f9ba7f0d87120df7e6736d82e860d4036be114dd` (confirmed == `origin/main`). Starting DB checkpoint: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `FK=[]`, `integrity=ok` — confirmed read-only, unchanged throughout this mission.

## 0. Governing principle

Restated from `rwi-governed-new-airport-discovery-design.md`: *"EXTERNAL DISCOVERY MAY PROPOSE IDENTITY. IT MAY NOT CREATE CANONICAL IDENTITY BY ITSELF."* This mission extends that principle one clause further, in RWI's own already-written words (`rwi-post-d4d8-strategic-orientation.md` §2): human judgment is required "at every point where identity **or materiality** is genuinely ambiguous." Materiality is EMAS relevance. This document designs the missing gate between UNKNOWN AIRPORT IDENTITY DISCOVERY (done — UAC1-5 + Option 3) and CANONICAL RWI AIRPORT ADMISSION.

**IDENTITY DISCOVERY ≠ EMAS BUSINESS RELEVANCE ≠ CANONICAL AIRPORT ADMISSION.** RWI is not a general airport catalog.

## 1. Section 2 — Fresh code reading, this mission

Read fresh in full, this mission window: `rwi-governed-new-airport-discovery-design.md`, `rwi-post-d4d8-strategic-orientation.md`, `app/services/unknown_airport_candidate_resolution.py` (UAC4), `app/models/unknown_airport_candidate.py`, `app/services/promotion_policy_evaluation.py`, `app/services/promotion_policy_persistence.py`, `app/services/intelligence_review_persistence.py`, `scripts/review_unknown_airport_candidate.py` (UAC5 CLI, full), `rwi-uac3-identity-precedence-option3-implementation-report.md`. Confirmed via targeted grep (already deeply known from the immediately-preceding Option 3 missions in this same session, re-confirmed structurally current): `EvidenceCategory` (`IDENTIFIER`, `NAME`, `RUNWAY_TOPOLOGY`, `ISSUER`, `LOCATION`) in `evidence_attachment_guard.py`; `CandidateFragment.airport_names`/`.document_title`/`.raw_text` in `discovery_candidate_fragment.py`; `SourceAssertion` model presence; `governed_signal_creation.py`'s fail-closed exception/gate shape.

## 2. Current canonical-admission gap (Section 3)

**YES — confirmed from code.** `create_airport_from_approved_candidate()` (`app/services/unknown_airport_candidate_resolution.py`) gates on exactly four things: candidate exists, `_require_unresolved()` (not already resolved), `_require_current_review()` (the supplied `review_id` is genuinely the latest review and carries `action="CREATE_NEW_AIRPORT"`), `_require_no_linked_assertion_already_canonical()`, plus a case/whitespace-insensitive duplicate-code defense on `iata_code`/`icao_code`/`faa_code`. **No field, check, or import in this file references EMAS, materiality, relevance, or anything resembling it.** It never reads `candidate.raw_*` for value purposes beyond identity/dedup. `scripts/review_unknown_airport_candidate.py` (UAC5 CLI), read fresh in full, confirms the same at the operator layer: `--decision CREATE_NEW_AIRPORT --reviewer ... --reason ...` followed by `--execute --review-id N --new-airport-name ... --new-airport-country ... --allow-database-write` succeeds today for the real Anoka candidate, with zero relevance check anywhere in `_validate_config()`, `_run_review_write()`, or `_run_execute()`. **A human reviewer could, today, once separately authorized, create "Anoka County-Blaine Airport" as a real canonical `Airport` row from ordinary pavement-reconstruction evidence with no EMAS content whatsoever.** This is the exact gap this design closes.

## 3. EMAS relevance definition and evidence classes (Sections 4)

Not reduced to "contains the word EMAS." Evidence classes, each independently checkable and source-neutral (concepts, not English strings — see §12/Section 17):

| Class | Description | Explicit EMAS term required? |
|---|---|---|
| A | Explicit EMAS (EMAS / Engineered Materials Arresting System / arresting bed, replacement/maintenance/procurement/design) | Yes |
| B | Explicit runway safety area / arrestor need (RSA deficiency, RSA improvement, runway-end safety issue, declared-distance reduction from constrained runway end, arresting-system feasibility, overrun mitigation) | No |
| C | Planning/feasibility signal (EMAS feasibility study, alternatives analysis, preliminary engineering/environmental/design study considering EMAS, CIP/master-plan entry explicitly naming arrestor/EMAS work) | Sometimes (naming EMAS = A+C together; naming only "arrestor feasibility" = B+C) |
| D | Funding/procurement signal (grant/bid/design-construction-supervision explicitly tied to EMAS) | Yes |
| E | Existing installation signal (currently installed EMAS, replacement/maintenance of a known bed) | Yes |
| F | Incident-driven signal (overrun involving an existing EMAS, repair/replacement after deployment) | Yes (names the EMAS that arrested/was involved) |
| G | Generic runway work only (pavement reconstruction, lighting, PAPI, resurfacing, electrical vault) | No — and never sufficient alone (below) |

**G alone is never sufficient, derived (not assumed):** every RWI airport with any runway at all periodically resurfaces, relights, or rebuilds pavement — this is universal airport maintenance, uncorrelated with EMAS need or opportunity. Treating G as relevant would make RWI's watch queue converge toward "every airport with a runway," defeating the entire discovery gate's purpose and directly reproducing the exact failure this mission exists to prevent (the real Anoka document *is* class G only, and is the mission's own worked non-example). G evidence is retained (it is the identity-discovery substrate) but contributes zero weight to relevance classification.

## 4. Relevance-state vocabulary (Section 5)

Deterministic, no scores/probabilities — matching RWI's own pre-existing, deliberate invariant (`rwi-post-d4d8-strategic-orientation.md` capability #22: "candidate scoring/ranking: NOT_PRESENT (deliberately)"), and matching `PromotionPolicyOutcome`'s own three-lane, allowlist-not-blocklist discipline.

- `EMAS_CONFIRMED` — class E or F present (an existing/known installation is referenced).
- `EMAS_STRONG_SIGNAL` — class A, C-naming-EMAS, or D present, with no confirmed installation yet (early opportunity, explicit EMAS language).
- `EMAS_PLAUSIBLE_SIGNAL` — class B or C-without-naming-EMAS present, no class A/D/E/F (RSA/arrestor-feasibility language without the product name — the genuinely early, pre-announcement signal this gate must not suppress).
- `RUNWAY_ONLY_NOT_EMAS_RELEVANT` — class G present, none of A–F.
- `INSUFFICIENT_EVIDENCE` — no runway-safety-shaped evidence of any class (should not normally arise once identity discovery has already fired, since identity discovery itself requires runway/airport evidence, but included for completeness and for candidates seeded from non-runway-work documents).

Five members — the minimum needed to keep §6's admission rule and §7's watch-queue boundary each expressible as a single, deterministic set membership test, matching the mission's own suggested shape as the correct minimum after derivation (not adopted uncritically — G was independently confirmed insufficient in §3 above, not merely assumed per the mission's own instruction).

## 5. Canonical admission rule (Section 6)

**Adopted: Option B, human-gated.** Explicit EMAS evidence (`EMAS_CONFIRMED`/`EMAS_STRONG_SIGNAL`) OR governed plausible runway-safety/EMAS-opportunity evidence (`EMAS_PLAUSIBLE_SIGNAL`) MAY become eligible for canonical admission — but only after an explicit, separate HUMAN relevance-approval action, never from the automatic classification alone. `RUNWAY_ONLY_NOT_EMAS_RELEVANT` and `INSUFFICIENT_EVIDENCE` can never be approved — the approval action itself refuses for these two states, fail-closed (mirrors `create_airport_from_approved_candidate()`'s own "gate-check stored columns, fail closed" shape).

Rejected: **Option A** (explicit EMAS only) — would satisfy Section 7's "don't wait for public announcement" instruction only for classes A/D/E/F, silently excluding the genuinely early class-B/C signals RWI most wants to catch first; too strict. **Option C** (candidate exists non-canonically forever, relevance review fully separate/unbounded) — under-specifies when CREATE_NEW_AIRPORT ever becomes reachable at all, leaving the exact gap this mission must close half-open. Option B is the only one of the three that (a) keeps the gate meaningfully strict (G is categorically excluded, no exceptions), (b) still reaches early opportunities (plausible-signal candidates are eligible, not blocked), and (c) keeps a human as final authority on the judgment call between "worth admitting now" and "wait for stronger evidence" for the ambiguous middle tier.

## 6. Early discovery / watch semantics (Section 7)

Two genuinely different thresholds, not one:

- **DISCOVERY WORTH WATCHING** — automatic, no human step: any `UnknownAirportCandidate` whose latest automatic relevance assessment is `EMAS_CONFIRMED`/`EMAS_STRONG_SIGNAL`/`EMAS_PLAUSIBLE_SIGNAL`. Surfaced in an operator watch queue. This is where early, pre-announcement discovery lives — a class-B "RSA alternatives analysis" candidate enters the watch queue the moment it's discovered, with no human review required to get there.
- **CANONICAL ADMISSION JUSTIFIED** — human-gated, per Section 5: requires an explicit relevance-approval action on top of the automatic classification.

`RUNWAY_ONLY_NOT_EMAS_RELEVANT` and `INSUFFICIENT_EVIDENCE` candidates are excluded from the watch queue (visible on direct lookup, never surfaced as "needs attention") but are never deleted (Section 18/19).

## 7. Anoka case classification (Section 8)

Real evidence: explicit identity (Anoka County-Blaine Airport), Runway 18-36 pavement reconstruction, electrical vault improvements, no EMAS claim, no RSA/overrun/arrestor signal anywhere in the currently-extracted evidence or the full real PDF body (confirmed by direct reading during the 5F mission). This is class G only.

| # | Question | Answer |
|---|---|---|
| A | Should `UnknownAirportCandidate` be created? | **YES** — unaffected by this gate; identity discovery (UAC3 Option 3) already correctly fires on the uncorroborated explicit name, a separate governance question. |
| B | Should it remain stored? | **YES** — append-only, kept indefinitely (Section 18/19). |
| C | Should it enter an EMAS watch/review queue? | **NO** — `RUNWAY_ONLY_NOT_EMAS_RELEVANT` is explicitly outside the watch-queue boundary (Section 6). |
| D | Should `CREATE_NEW_AIRPORT` be eligible? | **NO** — blocked by the new admission gate; a relevance-approval attempt against this classification is refused. |
| E | Should it become canonical now? | **NO.** |
| F | Should any Signal be created? | **NO** — Signal creation requires canonical Airport identity first (Section 16), unreachable here regardless. |

## 8. Future Anoka scenario matrix (Section 9)

| # | Scenario | Class(es) | Relevance state | Watch queue? | Canonical-admission eligible? | Signal eligible? |
|---|---|---|---|---|---|---|
| 1 | "Runway 18-36 EMAS Feasibility Study" | A + C | `EMAS_STRONG_SIGNAL` | Yes | Yes (human approval required) | No (pre-canonical) |
| 2 | "Runway 18-36 Runway Safety Area Alternatives Analysis" | B + C | `EMAS_PLAUSIBLE_SIGNAL` | Yes | Yes (human approval required, higher evidentiary bar since no explicit EMAS term) | No (pre-canonical) |
| 3 | "EMAS Procurement and Installation" | D (+E if installation is complete) | `EMAS_STRONG_SIGNAL` or `EMAS_CONFIRMED` | Yes | Yes | No (pre-canonical) |
| 4 | "Runway 18-36 Pavement Rehabilitation" | G only | `RUNWAY_ONLY_NOT_EMAS_RELEVANT` | No | No | No |
| 5 | Overrun incident, no EMAS mention | B (an overrun demonstrates exactly the runway-end-safety exposure class B describes, even without naming EMAS or "RSA" literally) — **temporal qualifier matters**: a *fresh* incident is `EMAS_PLAUSIBLE_SIGNAL`; a *historical* (years-old) incident with no current follow-on evidence is `INSUFFICIENT_EVIDENCE` (Section 14's false-negative discussion, item M) | See note | Yes if fresh / No if historical | Yes if fresh (human approval) / No if historical | No (pre-canonical) |

No scenario ever reaches Signal eligibility directly — Signal creation requires canonical admission first in every case (Section 16).

## 9. Existing-airport interaction (Section 10)

This gate governs **only** the path from `UnknownAirportCandidate` to a brand-new canonical `Airport` row, i.e. it is wired solely into `create_airport_from_approved_candidate()` (UAC4) and its CLI caller. It is never wired into: `resolve_candidate_to_existing_airport()` (MATCH_EXISTING_AIRPORT — an existing canonical Airport's identity is not re-litigated by this gate), any already-canonical `Airport` row, or any of `identity_guard`/`intelligence_review`/`promotion_policy`/Signal creation for evidence already attached to an existing Airport. Existing airports never have to "re-prove" their existence or re-earn EMAS relevance — the canonical identity lifecycle (once admitted) and the ongoing evidence-relevance lifecycle (materiality per claim, already governed by `evaluate_signal_candidate()`/`evaluate_promotion_policy()`) remain the two separate, already-correct systems they are today.

## 10. Persistence model (Sections 11–12)

Five options evaluated:

1. **Derive ephemerally from SourceAssertions only, no persistence.** Auditability: none (nothing durable to point to). Traceability: perfect but recomputed every time, no record of *when* something changed state. Human review: cannot durably record an approval — the approval itself is the whole point of Section 5's gate and must be a persisted fact. Rejected as sole mechanism, but its computation core (pure evaluator) is exactly right as *part* of the design (see below).
2. **Append-only candidate relevance review/history table.** Mirrors `UnknownAirportCandidateReview`'s own proven shape exactly (immutable, `before_update`/`before_delete` listeners raise, recency-determines-current, free-text reviewer, narrow CHECK-constrained vocabulary). Auditability: full. Traceability: full (references the candidate; candidate's linked `SourceAssertion`s are already queryable via `unknown_airport_candidate_id`). Human review: first-class. History: full, and rediscovery (Section 15) falls out for free — a new assessment row is just another append. Complexity: low — a near-exact structural copy of an already-proven, already-tested pattern. Schema impact: one new table, zero changes to existing tables. Duplication: of *pattern*, not of code (mirrors this codebase's own repeated precedent of one narrow table per governed question — `UnknownAirportCandidateReview`, `ReviewerAction`, `InstallationAssertionLink`). International: vocabulary is evidence-class based, unaffected. UI/query: `get_latest_*_by_recency()` pattern already proven reusable. **Selected.**
3. **Extend `UnknownAirportCandidateReview` vocabulary** (e.g. add `CONFIRM_EMAS_RELEVANT`/`REJECT_EMAS_RELEVANCE` actions). Rejected per Section 12 below — would blur a structurally different governed question ("is this identity real") with a genuinely separate one ("does this identity matter to RWI's mission"), which this codebase has *repeatedly and deliberately* refused to do (`ReviewerAction` vs. `identity_guard_decision` vs. `intelligence_review_decision` vs. `promotion_policy_decision` are already four separate vocabularies for four separate questions — this would be a fifth, forced into an existing column instead of given its own).
4. **Reuse Intelligence/promotion-policy concepts** (`intelligence_review_decision`/`promotion_policy_decision` columns). Wrong grain: those live per-`SourceAssertion`, answer "does this one row's evidence merit a Signal" for an *already-canonical* airport's evidence. EMAS relevance is a property of the **candidate** (an aggregate judgment across all its linked assertions, made *before* canonical identity exists at all) — forcing it onto a per-assertion column would require inventing an aggregation rule these two existing columns were never designed to carry, and would apply "Signal-worthiness" semantics to a question that isn't about Signals yet. Rejected.
5. *(no stronger fifth option identified — 2 combined with a pure-core evaluator strictly dominates)*

**Recommended: Option 2, composed with a pure-core evaluator exactly as Option 1 would be** — this directly reuses the proven `promotion_policy_evaluation.py` + `promotion_policy_persistence.py` two-layer shape read fresh this mission: a pure, DB-free `evaluate_emas_relevance()` function (Option 1's computation, without its persistence gap) feeding a thin persistence bridge that writes AUTOMATIC assessment rows into the new append-only table; a human relevance-approval action writes its own HUMAN-sourced row into the *same* table (distinguished by a `source` column), never conflated with `UnknownAirportCandidateReview`.

**Section 12 answer: yes, EMAS relevance needs its own review concept, entirely separate from `UnknownAirportCandidateReview`.** `MATCH_EXISTING_AIRPORT`/`CREATE_NEW_AIRPORT`/`REJECT_CANDIDATE`/`DEFER` remain pure identity/canonical-resolution actions, untouched. A new, narrow vocabulary — e.g. `CONFIRM_EMAS_RELEVANT` / `MARK_NOT_EMAS_RELEVANT` / `DEFER_RELEVANCE_REVIEW` — governs the business-relevance question, in its own table, with its own CHECK constraints.

## 11. Evidence traceability (Section 13)

Required chain, fully satisfiable by the recommended model: `UnknownAirportCandidate` → its linked `SourceAssertion`s (already queryable via the existing `unknown_airport_candidate_id` FK, unchanged) → each assertion's `EvidenceBag`/`CandidateFragment`-derived claims (already governed, unchanged) → an automatic `UnknownAirportCandidateRelevanceAssessment` row whose `evidence_classes_matched`/`reason` names exactly which claims produced which class (deterministic template string, never LLM-generated — mirrors `PromotionPolicyDecision.reason`'s own discipline) → an optional human-sourced row in the same table, referencing the automatic row it was made against (a `basis_assessment_id` field, mirroring `supersedes_review_id`) → eventual canonical admission (UAC4), which itself already logs which `review_id`/candidate produced the new Airport. No free-text-only, untraceable approval is possible: the human action is validated against the *current* automatic classification at write time, exactly as `_require_current_review()` validates staleness today.

## 12. Automatic/human boundary (Section 14)

| Step | Classification |
|---|---|
| Identity discovery → `UnknownAirportCandidate` created | AUTOMATIC (existing, UAC1/UAC3 Option 3, unchanged) |
| Relevance evidence-class extraction (source-specific) | AUTOMATIC — a source adapter's job (mirrors `mac_granicus_extractor.py`), not this gate's |
| Relevance classification (evidence classes → `RelevanceOutcome`) | AUTOMATIC, deterministic, pure (`evaluate_emas_relevance()`) |
| Persisting the automatic classification | AUTOMATIC (persistence bridge, no human step) |
| Entering the watch queue | AUTOMATIC (a derived query over the persisted state, Section 6) |
| Relevance **approval** (unlocking canonical-admission eligibility) | HUMAN_GATED — new, explicit action, separate from identity review |
| `MATCH_EXISTING_AIRPORT`/`CREATE_NEW_AIRPORT`/`REJECT_CANDIDATE`/`DEFER` | HUMAN_GATED (existing, unchanged) |
| Canonical Airport creation | HUMAN_GATED, execution step (existing UAC4, extended with one new precondition) |
| Signal creation | HUMAN_GATED, MANUAL_OPERATION (existing, unaffected, unreachable pre-canonical per Section 16) |
| Relevance re-assessment on new evidence (rediscovery) | AUTOMATIC (Section 15) |

## 13. False positives (Section 15)

Attack cases and their defense, all defeated by the same structural rule — **only classes A–F, never G, ever contribute to a non-`RUNWAY_ONLY_NOT_EMAS_RELEVANT` outcome, and evidence-class matching is scoped per-candidate to that candidate's own linked assertions only:**

- Runway pavement reconstruction, PAPI replacement, runway lighting, taxiway construction, terminal work — all class G (or entirely outside the runway-safety vocabulary), never contribute. Defeated structurally.
- Generic RSA wording unrelated to EMAS (e.g. a boilerplate FAA-compliance checklist paragraph present in nearly every capital-project memo) — the *evidence-class matcher itself*, not this gate, is responsible for this precision; this mirrors the exact false-positive discipline already proven necessary in `mac_granicus.py`'s own relevance-filter work (5D's `_has_proximate_runway_work_mention`, adjacency-based, not bag-of-words) — class-B extraction must use the same proximate/structural matching discipline, not naive keyword search, or it inherits 5D's own pre-fix defect one layer up. Documented as a hard extraction requirement, not solved by this design's evaluator layer alone.
- "Arresting" used in an unrelated context (e.g. "arresting the decline in ridership") — an extraction-layer defeat again, same reasoning: class-A matching must require EMAS/arrestor-system-shaped phrasing, not the bare word "arrest*".
- Historical EMAS mention referring to *another* airport — defeated by the same discipline already locked in 5F/Option 3: evidence classes are extracted per-candidate from that candidate's own linked `SourceAssertion`s only (which are themselves already identity-scoped by the guard); a document mentioning MSP or FCM in passing (the real Anoka PDF's own body text) never attaches its EMAS-irrelevant-here content to the Anoka candidate, because 5F's title-only airport-name-extraction discipline already prevents cross-airport evidence bleed at the identity layer, one level below this gate.
- Funding-source document listing many airports (e.g. a CIP appropriations bill covering 20 airports, one of which has EMAS) — same defense: evidence classes must be extracted from the *fragment/assertion already scoped to one candidate*, never from a shared document-level bag that could leak an unrelated airport's EMAS content onto this one.

## 14. False negatives (Section 16)

Genuinely meaningful RWI signals, none requiring the literal word "EMAS":

- Runway safety area alternatives study — class B/C, `EMAS_PLAUSIBLE_SIGNAL`. Meaningful (Scenario 2, Section 8).
- Insufficient RSA length / constrained runway-end geometry — class B, `EMAS_PLAUSIBLE_SIGNAL`. Meaningful — this is precisely the pre-condition an EMAS installation exists to remediate.
- Overrun mitigation study — class B/C. Meaningful.
- Declared-distance reduction due to runway-end constraints — class B (a declared-distance reduction is a direct, quantified symptom of exactly the runway-end-safety-margin problem EMAS solves). Meaningful.
- Arrestor feasibility language without the EMAS product name — class B/C. Meaningful, and the whole reason `EMAS_PLAUSIBLE_SIGNAL` exists as a distinct tier from `EMAS_STRONG_SIGNAL`.
- FAA (or equivalent international authority's) safety-area compliance work, *generic* — genuinely ambiguous: a bare "FAA Part 139 compliance inspection" line item is **not** by itself class B (too generic — it covers wildlife hazard management, lighting, marking, and dozens of unrelated compliance areas having nothing to do with runway-end safety margins). Only compliance language specifically about runway-end/RSA/declared-distance/arrestor topics qualifies as class B — a deliberately drawn line, matching Section 3's own "G is universal, uncorrelated with EMAS" reasoning applied one level up.

## 15. International / source-neutral verdict (Section 17)

**Verdict: source-neutral by construction, provided the discipline below is followed.** The evidence classes (A–G) and the evaluator (`evaluate_emas_relevance()`) are defined over a structured, already-classified evidence-class tag set (e.g. `frozenset[EvidenceClass]`), analogous to how `evaluate_promotion_policy()` (read fresh this mission) never re-reads raw document text — it consumes already-typed `Claim` objects. All FAA/MAC-specific vocabulary (RSA, Part 139, AIP/BIL funding-program names) belongs entirely to the **extraction layer** (a future MAC-specific, and eventually Brazil/Europe/Asia-specific, adapter — mirroring `mac_granicus_extractor.py`'s own existing role), never to the evaluator. A Brazilian or European source adapter, extracting native-language text, must simply produce the same structured tags (e.g. mapping "área de segurança de pista" concepts to class B) — zero change to `evaluate_emas_relevance()` itself required. This is the same layering discipline that already let RWI's identity-discovery system (UAC1-5) remain provider-neutral while `mac_granicus_extractor.py` alone carries MAC-specific parsing.

## 16. Signal interaction and governance order (Section 18)

Locked sequence: candidate discovered → relevance classified (automatic) → relevance approved (human) → canonical Airport created (UAC4, gated) → linked `SourceAssertion`s move to the new Airport (existing UAC4 behavior, unchanged) → **only then** does that evidence become eligible to flow through the ordinary, entirely unmodified known-airport pipeline (`identity_guard` → `intelligence_review` → `promotion_policy` → human-only Signal creation). No auto-Signal, ever, at any point — this design adds no new Signal-creation path and touches `governed_signal_creation.py` not at all.

**Newly discovered dependency, surfaced by this mission's own fresh reading of `scripts/review_unknown_airport_candidate.py`'s docstring (its own "DOWNSTREAM CONTINUATION" section, UAC5B):** moved `SourceAssertion`s retain `identity_guard_decision = INSUFFICIENT_IDENTITY` after `MATCH_EXISTING_AIRPORT`/`CREATE_NEW_AIRPORT` execution — **a faithful guard replay is not currently possible** (the structured `EvidenceBag` fields needed to re-run the guard are lossily discarded before persistence; the codebase's own prior finding explicitly declined to invent a "lossy/partial substitute" because it "could produce an actively MISLEADING ATTACH_CONFIRMED"). This means: **even after this gate's relevance approval and even after canonical admission, candidate-origin evidence cannot reach Signal creation today** — a pre-existing gap, unrelated to and not fixed by this mission, but directly load-bearing for Section 18's own sequencing and worth flagging honestly rather than silently assumed away (see STOP findings, §29 below).

## 17. Retention and dormancy policy (Section 19)

Identity-valid, EMAS-irrelevant candidates (`RUNWAY_ONLY_NOT_EMAS_RELEVANT`/`INSUFFICIENT_EVIDENCE`) are **kept forever, never deleted**, consistent with RWI's append-only philosophy throughout (SourceAssertions, reviews, evaluations are never deleted anywhere in this codebase). They are **excluded from the active watch queue** — a derived filter over the append-only assessment table, not a deletion, expiry, or separate archival state. No new "deleted"/"expired" lifecycle state is introduced — the existing recency-determines-current pattern already makes "not currently relevant" a simple, correct read, with no risk of an ever-growing *active* queue (only an ever-growing *total* history, which is the intended, auditable behavior).

## 18. Rediscovery lifecycle (Section 20)

When a new document later links a new `SourceAssertion` to the *same* `UnknownAirportCandidate` (dedup already guaranteed for free by UAC1's own `candidate_fingerprint` uniqueness — no duplicate candidate is ever created for the same identity), the evaluator is simply re-run against the candidate's now-larger evidence set, and a new AUTOMATIC assessment row is appended with a later timestamp. "Current" relevance state is always the latest row by recency — the exact `get_latest_unknown_airport_candidate_review()` pattern, reused for relevance. A candidate previously `RUNWAY_ONLY_NOT_EMAS_RELEVANT` that later gains class-A evidence cleanly and automatically re-enters the watch queue the moment the new assessment is persisted — no duplicate creation, no special "reopen" action needed at the identity layer. (A human-sourced `MARK_NOT_EMAS_RELEVANT` row, if one exists, is likewise simply superseded by recency once fresh automatic evidence and/or a fresh human review produces a newer row — never mutated, never deleted.)

## 19. Operator vocabulary (Section 21)

Minimum fields Commander needs to make the identity/relevance/status distinction legible, without designing full UI:

- **UNKNOWN AIRPORT** — the candidate's raw claimed identity (`raw_name`, `raw_city`, etc.) — already exists.
- **IDENTITY STATUS** — `UNRESOLVED` / `RESOLVED → Airport #N` — already exists (UAC5 CLI's own "RESOLUTION STATE" section).
- **EMAS RELEVANCE** — the latest relevance outcome (`EMAS_CONFIRMED`/`EMAS_STRONG_SIGNAL`/`EMAS_PLAUSIBLE_SIGNAL`/`RUNWAY_ONLY_NOT_EMAS_RELEVANT`/`INSUFFICIENT_EVIDENCE`), with its deterministic reason string — new.
- **RELEVANCE REVIEW STATUS** — `NOT_REVIEWED` / `CONFIRMED_RELEVANT (human, date, reviewer)` / `MARKED_NOT_RELEVANT (human, date, reviewer)` / `DEFERRED` — new, entirely separate from IDENTITY STATUS.
- **CANONICAL-ADMISSION ELIGIBILITY** — a derived boolean/explanation ("eligible: relevance confirmed 2026-08-19 by human:X" / "blocked: relevance is RUNWAY_ONLY_NOT_EMAS_RELEVANT") — new, surfaced identically in both the CLI's dry-run output and any future UI.

## 20. Minimum implementation slices (Section 22)

Derived, not adopted blindly from the mission's own example shape — confirmed correct after derivation, since it matches this codebase's own repeatedly-proven "pure core → persistence bridge → human CLI → gated execution → pilot" sequencing used for every prior slice family (UAC1-5, EB1-5, promotion-policy):

- **ERG1** — `app/services/emas_relevance_evaluation.py`: `EvidenceClass` enum, `RelevanceOutcome` enum, `EmasRelevanceContext`/`EmasRelevanceDecision` dataclasses, pure `evaluate_emas_relevance()`. No DB, no migration, fully unit-testable in isolation — mirrors `promotion_policy_evaluation.py` exactly.
- **ERG2** — new table `unknown_airport_candidate_relevance_assessments` (+ migration) + `app/services/emas_relevance_persistence.py` (mirrors `promotion_policy_persistence.py`): persists AUTOMATIC assessments only.
- **ERG3** — human relevance-review recording: new narrow vocabulary (`CONFIRM_EMAS_RELEVANT`/`MARK_NOT_EMAS_RELEVANT`/`DEFER_RELEVANCE_REVIEW`), written into the same table with `source=HUMAN`, via either a new sibling CLI or an extension to `review_unknown_airport_candidate.py`'s own mode set — mirrors `record_unknown_airport_candidate_review()`'s exact shape.
- **ERG4** — the canonical-admission gate itself: a new `_require_emas_relevance_approved()` precondition wired into `create_airport_from_approved_candidate()`, a new fail-closed `EmasRelevanceNotApprovedError`, and the UAC5 CLI's `--execute` (CREATE_NEW_AIRPORT) path surfacing the refusal exactly like `StaleReviewError` is surfaced today.
- **ERG5** — read-only watch-queue query/report script (which candidates are `EMAS_STRONG_SIGNAL`/`EMAS_PLAUSIBLE_SIGNAL`/`EMAS_CONFIRMED` and awaiting human relevance review) — no new writes.
- **ERG6** — real Anoka pilot: run ERG1's evaluator against Anoka's real, already-known evidence (read-only, no new network), confirm `RUNWAY_ONLY_NOT_EMAS_RELEVANT`, confirm ERG4's gate refuses a dry-run `CREATE_NEW_AIRPORT` execute for it.

**Recommended first slice: ERG1 only** — zero schema/DB impact, fully self-contained, directly testable against every scenario in Section 8's matrix and Section 13/14's attack lists before any persistence or gate-wiring risk is taken on, matching this codebase's own "prove the pure core first" precedent for every prior slice family.

## 21. Schema and migration impact (Section 23)

One new table (working name `unknown_airport_candidate_relevance_assessments`): `id`, `candidate_id` (FK → `unknown_airport_candidates.id`), `assessed_at`, `source` (`AUTOMATIC`|`HUMAN`, CHECK-constrained), `outcome` (the five-member vocabulary, CHECK-constrained), `evidence_classes_matched` (comma-joined, mirrors the existing lossy-but-adequate convention already used for `identifiers`/`names`/`runway_pairs` elsewhere), `reason` (deterministic for AUTOMATIC rows, free text for HUMAN rows), `action` (nullable, only for HUMAN rows: `CONFIRM_EMAS_RELEVANT`/`MARK_NOT_EMAS_RELEVANT`/`DEFER_RELEVANCE_REVIEW`), `reviewer` (nullable, required when `source=HUMAN`), `basis_assessment_id` (nullable self-reference, the automatic row a human decision was made against). Immutable via the same `before_update`/`before_delete`-raises event-listener pattern as `UnknownAirportCandidateReview`. One migration. **Zero backfill** — no EMAS relevance data exists anywhere today to migrate; every existing candidate (the single Controlled Rehearsal #1 row) simply has no assessment rows until the evaluator is first run against it. **Zero impact** on existing canonical `Airport` rows or their `SourceAssertion`s (the gate is wired only into new-candidate admission, Section 9). **Zero impact** on `SourceAssertion`'s own schema (no new column there) — relevance stays entirely candidate-scoped, in its own table, never blurred into per-assertion promotion-policy columns (Section 12).

## 22. Threat model (Section 24)

| # | Attack | Defense |
|---|---|---|
| A | Explicit identity + zero EMAS relevance | Real Anoka case — classified `RUNWAY_ONLY_NOT_EMAS_RELEVANT`, gate refuses admission (Section 7). |
| B | Generic runway work falsely treated as EMAS | Class G structurally excluded from every non-`RUNWAY_ONLY_NOT_EMAS_RELEVANT` outcome (Section 3). |
| C | Explicit EMAS text about another airport | Per-candidate evidence scoping, inherited from 5F's title-only identity extraction + the guard's own per-candidate `SourceAssertion` linkage (Section 13). |
| D | Contradictory source evidence | Not this gate's concern — identity contradiction is already the guard's job (unchanged); relevance evaluation only ever runs on evidence already attached to one resolved candidate. |
| E | Previously irrelevant candidate later becomes relevant | Rediscovery lifecycle, Section 18 — new assessment row, automatic re-entry to watch queue. |
| F | Duplicate candidate discovery | Unaffected, already solved by UAC1's `candidate_fingerprint` uniqueness — this gate assumes, never re-solves, that guarantee. |
| G | Human relevance approval then later contradictory evidence | New evidence produces a new AUTOMATIC assessment row; a human can record a new `MARK_NOT_EMAS_RELEVANT`/`DEFER` row superseding-by-recency the old approval — but note: this gate does **not** retroactively revoke an already-created canonical Airport (Section 9 — canonical identity lifecycle is separate once admitted); a contradicted post-admission approval is a data-quality signal for human attention, not an automatic un-admission (RWI has no "delete a canonical Airport" operation anywhere, by design). |
| H | Candidate matches an existing canonical Airport | Out of scope for this gate entirely — that's `MATCH_EXISTING_AIRPORT`/`resolve_candidate_to_existing_airport()`, never touched by this design (Section 9). |
| I | Relevance approval without sufficient evidence | The approval action itself is gate-checked against the *current* automatic classification at write time (mirrors `_require_current_review()`'s staleness check) — an approval attempt against `RUNWAY_ONLY_NOT_EMAS_RELEVANT`/`INSUFFICIENT_EVIDENCE` is refused, fail-closed, not merely advisory (Section 5). |
| J | Canonical admission without relevance approval | The precise gap this mission closes — `_require_emas_relevance_approved()` (ERG4) refuses `create_airport_from_approved_candidate()` otherwise. |
| K | Signal creation without canonical identity | Already structurally impossible — Signal creation requires a `SourceAssertion` with a real `airport_id`, which does not exist pre-admission (Section 16). |
| L | Multilingual evidence | Section 15 — evaluator consumes structured tags only; extraction-layer concern, source-neutral by construction. |
| M | Historical article mistaken for current opportunity | Section 8, Scenario 5 — temporal qualifier (fresh vs. historical, reusing the `TemporalQualifier` concept already proven in `promotion_policy_evaluation.py`) distinguishes `EMAS_PLAUSIBLE_SIGNAL` from `INSUFFICIENT_EVIDENCE` for incident-driven evidence with no current follow-on. |

Fail-closed confirmed throughout: every new gate (relevance approval, canonical admission) defaults to refusal absent an explicit, current, positively-matching record — no ambiguous or missing state is ever treated as permissive.

## 23. Options considered and recommendation (Section 25)

Three complete architectures:

**Option I — Ephemeral-only (Persistence Option 1 alone).** No new table; relevance recomputed on demand from `SourceAssertion`s each time, with human approval recorded as a free-text note somewhere ad hoc. Rejected: no durable, traceable approval record — fails Section 13's own traceability requirement outright, and fails Section 5's requirement that approval be a checkable gate precondition rather than a recomputation that could silently change between approval-time and admission-time.

**Option II — Blur into existing vocabulary (Persistence Options 3/4).** Extend `UnknownAirportCandidateReview` or the per-assertion promotion-policy columns. Rejected per Section 10/12: wrong grain (per-assertion vs. per-candidate-aggregate), and violates this codebase's own repeated, deliberate refusal to merge distinct governed questions into one vocabulary.

**Option III — New pure evaluator + new append-only persistence table + new gate precondition on UAC4 (Persistence Option 2, composed with Option 1's computation).** **Recommended.** Directly answers all seven required questions:
- *Where does relevance state live?* A new table, `unknown_airport_candidate_relevance_assessments`, keyed to `candidate_id`, entirely separate from `UnknownAirportCandidateReview`.
- *Who/what computes it?* A pure, deterministic, source-neutral evaluator (`evaluate_emas_relevance()`) — automatic, no human step, mirrors `evaluate_promotion_policy()`.
- *What do humans approve?* A separate, narrow relevance-approval action (`CONFIRM_EMAS_RELEVANT`/etc.), gate-checked against the current automatic classification.
- *What unlocks `CREATE_NEW_AIRPORT`?* A human-sourced `CONFIRM_EMAS_RELEVANT` row that is current (latest by recency) and whose basis classification is not `RUNWAY_ONLY_NOT_EMAS_RELEVANT`/`INSUFFICIENT_EVIDENCE`.
- *How do dormant candidates behave?* Kept forever, excluded from the watch queue by a derived filter, never deleted (Section 17).
- *How does rediscovery work?* A new append-only row on new evidence; current state always recency-derived (Section 18).
- *How does evidence stay traceable?* The full chain in Section 11, entirely reusing already-proven linkage (`unknown_airport_candidate_id` on `SourceAssertion`) plus one new, narrow addition.

## 24. Final summary (31 items, Section 26)

1. Starting HEAD `f9ba7f0d87120df7e6736d82e860d4036be114dd` (== origin/main); DB checkpoint SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` — verified, unchanged throughout.
2. Current canonical-admission gap: **confirmed real, from code** — `create_airport_from_approved_candidate()` and the UAC5 CLI have zero EMAS-relevance check anywhere (§2).
3. EMAS relevance definition: evidence classes A–G (§3), G structurally never sufficient, derived not assumed.
4. Evidence classes: A–G, table in §3.
5. Relevance-state vocabulary: `EMAS_CONFIRMED`/`EMAS_STRONG_SIGNAL`/`EMAS_PLAUSIBLE_SIGNAL`/`RUNWAY_ONLY_NOT_EMAS_RELEVANT`/`INSUFFICIENT_EVIDENCE` (§4), deterministic, no scores.
6. Early-discovery/watch semantics: watch-queue entry is automatic (any of the three positive classes); canonical admission requires an additional human step (§6).
7. Canonical-admission rule: **Option B** — explicit EMAS OR governed plausible-signal evidence, human-approved; G/insufficient categorically excluded (§5).
8. Anoka classification: `RUNWAY_ONLY_NOT_EMAS_RELEVANT` — candidate created (yes), stored (yes), watch queue (no), CREATE_NEW_AIRPORT eligible (no), canonical now (no), Signal (no) (§7).
9. Future Anoka scenario matrix: 5 scenarios classified, table in §8.
10. Existing-airport interaction: gate wired only into new-candidate admission; existing canonical Airports and their evidence pipelines entirely unaffected (§9).
11. Persistence options: 5 evaluated (§10).
12. Recommended persistence model: new pure evaluator + new append-only `unknown_airport_candidate_relevance_assessments` table, entirely separate from `UnknownAirportCandidateReview` (§10/§12).
13. Human-review model: new, narrow, CHECK-constrained vocabulary (`CONFIRM_EMAS_RELEVANT`/`MARK_NOT_EMAS_RELEVANT`/`DEFER_RELEVANCE_REVIEW`), append-only, gate-checked against current automatic classification at write time (§10/§13).
14. Evidence-traceability model: candidate → linked SourceAssertions → EvidenceBag/claims → automatic assessment → human approval (referencing its basis) → canonical admission; no free-text-only approval possible (§11).
15. Automatic/human boundary: table in §12 — everything through watch-queue entry is automatic; approval and admission are human-gated.
16. False-positive analysis: defeated structurally (class-G exclusion, per-candidate evidence scoping) or flagged as an extraction-layer discipline requirement inherited from 5D/5F precedent (§13).
17. False-negative analysis: class-B/C signals (RSA deficiency, declared-distance reduction, arrestor feasibility) are genuine and captured as `EMAS_PLAUSIBLE_SIGNAL`; generic FAA-compliance language is deliberately excluded as too broad (§14).
18. International/source-neutral verdict: **source-neutral by construction** — evaluator consumes structured tags only; all source/language-specific vocabulary lives in the extraction layer, never the evaluator (§15).
19. Signal interaction: candidate → relevance approved → canonical Airport → ordinary known-airport pipeline → Signal (human-only, unchanged); no auto-Signal at any point. **New finding:** even post-admission, candidate-origin evidence cannot currently reach Signal creation due to a pre-existing, separately-tracked guard-replay gap (UAC5B) — flagged, not fixed, by this mission (§16).
20. Retention/dormancy policy: kept forever, excluded from the active watch queue by a derived filter, never deleted (§17).
21. Rediscovery lifecycle: new automatic assessment row on new evidence; current state always recency-derived; no duplicate candidate creation (§18).
22. Operator semantics: UNKNOWN AIRPORT / IDENTITY STATUS / EMAS RELEVANCE / RELEVANCE REVIEW STATUS / CANONICAL-ADMISSION ELIGIBILITY (§19).
23. Threat-model verdict: 13 attacks (A–M) addressed, fail-closed throughout (§22).
24. Schema impact: one new table, zero changes to `UnknownAirportCandidate`, `UnknownAirportCandidateReview`, or `SourceAssertion` (§21).
25. Migration impact: one migration, zero backfill required, zero impact to the existing single rehearsal candidate row or any canonical Airport (§21).
26. Minimum implementation slices: ERG1–ERG6, derived and confirmed against this codebase's own proven sequencing (§20).
27. Options considered: three complete architectures (§23).
28. Recommended architecture: **Option III** — pure evaluator + new append-only table + new UAC4 gate precondition (§23).
29. **STOP findings:** (a) the confirmed real gap in §2 — CREATE_NEW_AIRPORT is exploitable today for zero-EMAS-relevance evidence, no code change made, authorization for implementation must be separately sought; (b) the newly-surfaced UAC5B guard-replay gap (§16/§19 item 19) means candidate-origin evidence cannot reach Signal creation even after this gate's own future implementation and even after canonical admission — a distinct, pre-existing, not-yet-scoped follow-on slice.
30. **READY_FOR_EMAS_RELEVANCE_GATE_IMPLEMENTATION: YES** — architecture is fully specified, composes only already-proven patterns (`promotion_policy_evaluation.py`/`promotion_policy_persistence.py`/`UnknownAirportCandidateReview`), and ERG1 has zero schema/DB risk.
31. **Exact recommended first implementation slice: ERG1** — `app/services/emas_relevance_evaluation.py`, a pure, DB-free, fully unit-testable `evaluate_emas_relevance()` core (evidence classes, five-member outcome vocabulary, deterministic reasoning), tested directly against Section 8's scenario matrix and Section 13/14's attack lists before any persistence or gate-wiring work begins.

RWI_EMAS_RELEVANCE_GATE_DESIGN_COMPLETE
