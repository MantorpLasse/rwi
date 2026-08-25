# RWI ERG1.5 — Inventory vs. Active Opportunity Semantics

Status: DESIGN RESOLUTION ONLY. NO CODE CHANGE. NO DB WRITE. NO MIGRATION. NO ERG2 IMPLEMENTATION. NO COMMIT. NO PUSH.

Starting HEAD: `e56eab51c3d9018a0873ad31756ae55fe1964450` (confirmed `== origin/main`). DB checkpoint: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok` — confirmed read-only, unchanged throughout.

## Files read (fresh, this mission)

`docs/architecture/rwi-emas-relevance-gate-design.md`, `docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md`, `app/services/emas_relevance_evaluation.py`, `tests/test_emas_relevance_evaluation.py` (all four already deeply known from this same session's own prior turns, re-confirmed unchanged since the last commit — no edits occurred between then and this mission). Newly read fresh this mission: `app/models/installation.py`, `app/models/airport.py`, `app/models/signal.py` (full), plus targeted greps of `scripts/graduate_signal_to_installation.py` and every real `Installation.status`/`Signal.status` value actually in production use across `scripts/*.py`.

**The single most important finding of this mission came from those model reads, not from re-deriving anything from first principles**: RWI's OWN EXISTING, ALREADY-CANONICAL architecture already draws exactly the distinction this mission was asked to resolve — `Installation` ("what's installed today," `status` ∈ `{"active", "design", "under construction"}` in real use, no "removed"/"decommissioned" value exists anywhere in production data) is the **inventory** concept; `Signal` ("something that could become a future EMAS order," `status` ∈ `{"identified", "completed", ...}`) is the **opportunity/watch** concept; `scripts/graduate_signal_to_installation.py` is a real, working, already-shipped pipeline that turns a completed Signal into an Installation (`Signal.status="completed"` + `Signal.installation_id` set, a new `Installation(status="active", ...)` row created). This is not a new idea this mission invents — it is the SAME separation RWI already relies on for canonical airports, one layer downstream of where ERG1 currently sits.

## 1. Three-dimension verdict

**Two independent primitive dimensions, not three.** Re-deriving from first principles and cross-checking against the Installation/Signal precedent above:

- **A. INVENTORY RELEVANCE** — mirrors `Installation`: does the evidence establish a confirmed (current or historical) EMAS installation exists/existed at this airport? Driven by `EvidenceClass.E_EXISTING_INSTALLATION` / `F_INCIDENT_DRIVEN` alone.
- **C. ACTIVE WATCH / OPPORTUNITY** — mirrors `Signal`: is there current-or-future-shaped evidence of an EMAS lifecycle event worth an operator's attention right now? Driven by `A_EXPLICIT_EMAS`/`B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED`/`C_PLANNING_OR_FEASIBILITY`/`D_FUNDING_OR_PROCUREMENT`, and — see §5 — E/F when an installation's own lifecycle event (repair, replacement) is itself explicitly current/future.

**B. CANONICAL ADMISSION RELEVANCE is not a third primitive — it is a derived boolean: `inventory_relevant OR active_watch`.** This is directly confirmed by the design doc's own already-locked Section 6 rule (Option B: explicit EMAS OR governed plausible-signal evidence, either one, unconditionally unlocks eligibility for human-considered admission) — Option B never required BOTH dimensions simultaneously, only that AT LEAST ONE hold. Re-checking this independently against the Installation/Signal precedent confirms it: a canonical Airport can legitimately exist in RWI today purely because it has an `Installation` row with no open `Signal` (pure inventory), or purely because it has an open `Signal` with no `Installation` yet (pure prospect) — RWI's real schema already permits and expects both shapes independently. So admission relevance is correctly modeled as "either dimension alone is sufficient," not as its own independent judgment.

A and C are genuinely orthogonal — proven by the case matrix (§3 below): Case A is inventory-only, Case F/G/H are watch-only, Case B/C are both, Case K is neither. No case collapses the two into one axis.

## 2. Dimension definitions

**INVENTORY_RELEVANT** = `True` iff at least one `POSITIVE`-polarity `E_EXISTING_INSTALLATION` or `F_INCIDENT_DRIVEN` observation exists, **regardless of temporality** — unchanged from ERG1's own existing E-exemption reasoning (an installation's historical existence remains a present-tense structural fact about RWI's world model; "history is preserved, never deleted" per the mission's own domain principle). Contradiction never removes this (§10).

**ACTIVE_WATCH** = `True` iff at least one of:
- (a) any `A`/`B`/`C`/`D` observation contributes to the outcome under ERG1's EXISTING (unchanged) temporal-discount rule (i.e., is already in today's `contributing`/`matched_classes` set) **AND its temporality is not `COMPLETED`** (new — a closed-out pipeline event is not something to keep watching; see Case J), OR
- (b) any `E`/`F` observation carries an EXPLICIT temporality in `{CURRENT_STATE_AS_OF_DOCUMENT_DATE, PLANNED_FUTURE_ACTION, REQUESTED_PENDING_APPROVAL}` (a repair/replacement genuinely in progress or planned — not merely "an installation exists").

**Why the asymmetry between (a) and (b) is justified, not arbitrary:** classes A/B/C/D are inherently forward-looking by their own definition (a feasibility study, a funding action, a safety-area deficiency needing mitigation are, by definition, unresolved situations unless explicitly closed out) — so `UNKNOWN` temporality reasonably defaults to "still open" for them, exactly mirroring the reasoning ERG1's own review already locked for outcome computation. Class E is inherently backward-looking by default (an installation's existence is, by default, an already-completed fact) — so treating E as "actively watched" requires an explicit signal that something is happening NOW, not merely the absence of information. This is the precise, narrow fix for the ERG1 review's own flagged finding: `EMAS_CONFIRMED` no longer unconditionally implies `active_watch=True`.

**CANONICAL_ADMISSION_RELEVANT** = `INVENTORY_RELEVANT OR ACTIVE_WATCH` — exactly `outcome not in {RUNWAY_ONLY_NOT_EMAS_RELEVANT, INSUFFICIENT_EVIDENCE}`, i.e. unchanged from ERG1's current computation. Confirmed sufficient as a plain boolean (§7) — no richer state is needed, since a human reviewer's admission decision is already informed by seeing `inventory_relevant`/`active_watch` separately, not merely their disjunction.

## 3. Case matrix (A–L)

| Case | Evidence | Inventory | Admission | Watch | Why |
|---|---|---|---|---|---|
| A. Installed 2011, still present, no current project | E, `HISTORICAL_FACT` | **True** | **True** | **False** | Confirmed existence (inventory); nothing currently happening (E's own temporality is historical, uncorroborated). |
| B. Installed 2011, replacement planned 2027 | E(`HISTORICAL_FACT`) + D(`PLANNED_FUTURE_ACTION`) | **True** | True | **True** | Inventory from E; watch from D (planned, undiscounted). Outcome stays `EMAS_CONFIRMED` (E still takes headline precedence), but `active_watch` now correctly separates "there's also something to watch." |
| C. Existing bed currently under repair | E(`CURRENT_STATE_AS_OF_DOCUMENT_DATE`) | **True** | True | **True** | Rule (b): E's own temporality is explicitly current — a repair-in-progress is itself lifecycle activity. |
| D. Confirmed existing EMAS, no date known | E(`UNKNOWN`) | **True** | True | **False** | `UNKNOWN` never affirmatively grants active_watch for E (rule b requires an explicit current/future tag) — matches the mission's own "UNKNOWN must not magically mean current" instruction directly. |
| E. Historical source: installed 2004, current status unknown | E(`HISTORICAL_FACT`) | **True** | True | **False** | Same computation as Case A. "Is it still physically present" is a separate, human-verification question (§11) outside relevance classification's scope — not a gap this mission needs to close. |
| F. EMAS feasibility study, no installation yet | A+C | **False** | True | **True** | No E/F evidence at all — not yet inventory. Live opportunity — watch. |
| G. RSA deficiency/alternatives study, EMAS not named | B (or C alone) | **False** | True | **True** | Early, pre-announcement signal — exactly what the design doc's own Section 7 exists to preserve. |
| H. Explicit EMAS procurement | D | **False** | True | **True** | Live funding/procurement action. |
| I. EMAS project cancelled | D (POSITIVE) + D (CONTRADICTING) | **False** | True | **True** (surfaced as contradicted) | Contradiction never suppresses (§10) — the cancelled status is visible in `contradicting_evidence_classes`/`reason`, not hidden; a human still needs to see and confirm it, which is MORE served by keeping it visible in the watch view, not less. |
| J. Old EMAS removed, full standard RSA constructed instead | E(`HISTORICAL_FACT`, POSITIVE) + E(CONTRADICTING, "removed") + B(`COMPLETED`, "RSA built") | **True** | True | **False** | Inventory preserved (it existed — history is not deleted). Nothing to watch: the contradicting removal claim never grants watch either way, and the RSA-construction evidence, correctly tagged `COMPLETED`, is excluded from active_watch by the new (a)-rule — the story is closed. |
| K. Generic runway work only (Anoka, locked) | G only | **False** | **False** | **False** | Unaffected by every change in this mission — G never touches the E/F/A/B/C/D machinery at all. |
| L. Confirmed historical EMAS, airport not currently canonical | E(`HISTORICAL_FACT`) | **True** | True | **False** | The mission's own critical product question, worked through concretely — see §4. |

## 4. Critical product question — answered

**Should a confirmed existing EMAS installation be sufficient reason for an airport to exist canonically in RWI even with no active opportunity? YES — confirmed against the real architecture, not merely assumed.** The `Installation`/`Signal` split already proven in production (§ intro) demonstrates RWI already treats "has EMAS" and "has an open opportunity" as independently sufficient reasons for a canonical Airport row to matter: a canonical Airport with only `Installation` rows and zero open `Signal`s is a completely ordinary, already-supported shape in the real schema today (nothing in `Airport`/`Installation` requires a linked `Signal` to exist). RWI is not solely an opportunity detector — it is also, by its own already-built data model, a system of record for confirmed EMAS deployments. The user's expected direction is correct, and now has direct architectural grounding rather than resting on intuition alone.

## 5. Watch semantics — the candidate definition, attacked and refined

The mission's own candidate definition ("current/future evidence worth surfacing... historical inventory alone should normally not imply WATCH") is **correct in spirit and is exactly what §2's `ACTIVE_WATCH` definition implements** — with the one necessary refinement already built into §2: `COMPLETED`-tagged A/B/C/D evidence must ALSO be excluded (a closed-out pipeline event, e.g. "RSA subsequently constructed, deficiency resolved," is not a current/future signal either, even though it isn't `HISTORICAL_FACT` — Case J is the concrete counter-example that forced this refinement). Without this refinement, a resolved, closed EMAS-adjacent story would incorrectly keep surfacing in an active watch queue forever.

## 6. Inventory semantics

Inventory relevance includes: current confirmed EMAS (Case A/C/D), historical EMAS still believed current (Case A/E — no distinction is drawn between these two at the relevance-classification layer, correctly — see §11), and historical/removed EMAS (Case J — "history is preserved, never deleted" means the fact that an EMAS *existed* remains inventory-relevant even after a contradicting removal claim; the removal is surfaced, not erased). Historical-only, uncertain-installation evidence (Case E) is **inventory-relevant but not, by itself, sufficient to assert current physical presence** — that is a genuine, correctly-drawn distinction: *"belongs in historical RWI knowledge"* is what `inventory_relevant=True` means; *"should create a currently canonical airport"* is the SEPARATE, human-gated admission decision (§7) that a human makes AFTER seeing both `inventory_relevant` and its own temporal basis in `reason` — the evaluator does not conflate "this evidence exists" with "you should act on it as current."

## 7. Canonical admission — boolean confirmed sufficient

Re-derived independently against all twelve mission-listed evidence shapes (confirmed E, historical E, planned D, feasibility A+C, early B, generic G, cancelled D, removed E): every one of them is correctly classified by the existing 5-member `RelevanceOutcome` plus the two new booleans — **no richer state is required.** The temptation to add a richer "admission readiness" enum (e.g. distinguishing "admission-eligible via inventory" from "admission-eligible via opportunity") was considered and rejected: a human reviewer approving `CREATE_NEW_AIRPORT` already sees `inventory_relevant`/`active_watch`/`outcome`/`reason` all separately in the decision object — collapsing that into a single richer enum would only re-hide information the two-boolean model already exposes plainly, with no decision-making benefit. Boolean remains correct, per the mission's own "prefer minimal vocabulary unless boolean is demonstrably insufficient" instruction — it was not demonstrated insufficient anywhere in the case matrix.

## 8. Opportunity vs. Signal — firewall re-confirmed, strengthened by the architecture read

`active_watch=True` NEVER means "Signal should be created." This is now grounded in the real schema, not just doctrine: `Signal` is a canonical-Airport-scoped row (`Signal.airport_id` is a non-nullable FK to `airports.id`) — it **cannot exist at all** for a pre-canonical `UnknownAirportCandidate`, structurally, regardless of anything ERG1 computes. `active_watch` is advisory information about a CANDIDATE; Signal creation remains entirely downstream of canonical admission (UAC4) and remains human-only, entirely untouched by this mission or by ERG1 itself (still zero import of `app.models.Signal`/`governed_signal_creation` anywhere in the evaluator, unchanged from the prior review).

## 9. Temporality — resolved per dimension, not globally

No single global rule for `UNKNOWN` — resolved independently per dimension, exactly as the mission requests:
- **Preserve inventory relevance**: YES, unconditionally — `E`/`F` are temporal-discount-exempt for `inventory_relevant` regardless of tag (§2).
- **Permit canonical admission**: YES, as a consequence of inventory relevance alone already being admission-sufficient (§7) — an `UNKNOWN`-dated confirmed installation (Case D) is still admission-eligible.
- **Enable watch**: NO, not for `E`/`F` specifically (§2's asymmetric rule) — `UNKNOWN` never affirmatively grants `active_watch` for existing-installation evidence, directly answering the mission's own explicit warning that "UNKNOWN must not magically mean current." For `A`/`B`/`C`/`D`, `UNKNOWN` DOES still contribute to `active_watch` (unchanged from the existing, already-reviewed-sound outcome-level rule) — because those classes are inherently forward-looking by definition, this is not the same risk the mission is warning against for E/F.

## 10. Contradictions — resolved, deterministic, explainable

**Contradictions never change `inventory_relevant`, `active_watch`, or `canonical_admission_relevant` — they only ever add surfaced information (`contradicting_evidence_classes` + a `reason` note), for all three dimensions uniformly, extending the identical non-suppression principle the prior ERG1 review already locked for the outcome computation.** Re-derived independently rather than merely re-applied: suppressing a dimension because of a contradiction would hide the underlying positive evidence's existence from the human reviewer who is the actual, final decision-maker — a "watch" queue entry reading "EMAS project (CONTRADICTED: reported cancelled) — human review recommended" is objectively more useful and more honest than silence, and a dimension flipped to `False` by an unverified contradicting claim could just as easily be wrong as the original positive claim. Fail-closed here means "never let the system quietly decide the dispute for the human," not "assume the contradiction is true." Worked through all three mission-listed contradiction cases (EMAS planned + cancelled → Case I; existing + later "removed" → Case J; RSA deficiency + later full-standard RSA completed → also folds into Case J's own B-observation) plus a re-check of the two ERG1-review contradiction tests already in the suite — all consistent with this rule.

## 11. ERG1 output-contract sufficiency — verdict C, narrow change required

**C: a narrow output-contract change is required before ERG2, but NOT a vocabulary/enum change.** Specifically:

1. **Add** `is_inventory_relevant: bool` to `EmasRelevanceDecision` — currently entirely unrepresented; `EMAS_CONFIRMED` alone cannot answer "is this about existence or about activity" today.
2. **Redefine the computation of** `is_watch_worthy` (name and meaning unchanged — it always meant "worth an operator's watch-queue attention"; only its computation was too permissive) to the asymmetric §2 rule, so a dormant `EMAS_CONFIRMED` candidate (Case A/D/E) no longer reads identically to an active opportunity (Case B/C/F/G/H).
3. **Leave `is_canonical_admission_relevant` computed exactly as today** (`outcome not in {RUNWAY_ONLY, INSUFFICIENT}`) — already correct as the OR of the two dimensions (§7), no change needed.
4. **No change to `RelevanceOutcome`, `EvidenceClass`, or `ObservationPolarity`** — the five-member outcome vocabulary and seven evidence classes remain exactly as locked by the parent design doc and the ERG1 implementation; this is a two-boolean refinement layered on top, not a re-architecture.

This is explicitly NOT implemented in this mission (design-resolution only) — it is the exact, narrow, fully-specified change the next implementation mission should make.

## 12. ERG2 persistence impact

Re-derived against the refined model: ERG2 must persist `outcome`, `reason`, `evidence_classes_matched`, `contradicting_evidence_classes`, `evaluator_version` (all as already recommended in the ERG1 report) **plus now `is_inventory_relevant` and `is_watch_worthy` as their own persisted columns** — challenged and confirmed necessary (not redundant denormalization, unlike the ERG1 report's own original recommendation to skip persisting the two booleans): under the OLD model both booleans were pure functions of `outcome` alone and therefore safely re-derivable at read time; under the NEW model, `is_watch_worthy`/`is_inventory_relevant` depend on PER-OBSERVATION temporality, which the current Section 21 schema sketch does not plan to persist in full (only the aggregate `evidence_classes_matched` string) — so unless a future slice also persists the full observation set (a heavier design not recommended here), the two booleans MUST be persisted directly at write time, or the information is lost. `is_canonical_admission_relevant` remains correctly NOT persisted (still a pure function of `outcome` alone, unchanged). No new table, no schema redesign beyond two additional boolean columns on the same already-planned `unknown_airport_candidate_relevance_assessments` table — still a narrow, additive change, not new schema architecture.

## 13. Website / shareholder sanity check

The two-dimension model translates cleanly to exactly the shareholder-facing vocabulary the mission sketches, now with a concrete mapping:

- **KNOWN EMAS** ("EMAS installed — no current activity") = `inventory_relevant=True, active_watch=False` (Case A/D/E).
- **WATCH** ("EMAS replacement planned") = `active_watch=True` (Case B/C, also inventory-relevant).
- **EARLY WATCH** ("Runway safety alternatives under study") = `active_watch=True, inventory_relevant=False` (Case F/G/H).
- **HISTORICAL** ("Historical EMAS installation — current status uncertain") = `inventory_relevant=True`, `outcome=EMAS_CONFIRMED` with `HISTORICAL_FACT` basis visible in `reason` (Case E) — the "uncertain" framing is exactly what `reason`'s deterministic text already carries (it names the temporal basis), so no new field is needed for this nuance.
- **NOT EMAS RELEVANT** ("Runway reconstruction only") = `inventory_relevant=False, active_watch=False, canonical_admission_relevant=False` (Case K/Anoka).

Every shareholder-facing sentence the mission sketches is expressible from the refined two-boolean-plus-outcome contract with no additional field. Sanity check passed.

## 14. Anoka regression — reconfirmed unaffected

Anoka County-Blaine Airport (Runway 18-36 pavement reconstruction + electrical vault improvements, class G only): `inventory_relevant=False`, `active_watch=False` (renamed computation of `is_watch_worthy`, same result), `canonical_admission_relevant=False` — **identical to today's already-locked regression**, since G never touches any of the E/F/A/B/C/D logic this mission refines. `UnknownAirportCandidate` identity may still exist (UAC3 Option 3, entirely untouched) — identity discovery remains structurally separate from, and independent of, every dimension this mission resolves.

## 15. Recommendation

**Adopt the two-independent-primitive-dimension model** (`inventory_relevant`, `active_watch`) **with `canonical_admission_relevant` as their derived disjunction**, exactly as specified in §2/§11 — the smallest model that correctly distinguishes all five of the mission's own required distinctions (known EMAS inventory, historical knowledge, active/future opportunity, early warning, irrelevant runway work), grounded directly in RWI's own already-proven `Installation`/`Signal` architectural split rather than invented fresh. **ERG1 DOES need revision before ERG2** — a narrow, two-part change (§11: add one field, refine one existing field's computation), zero enum/vocabulary changes, zero schema changes beyond two additional boolean columns at the ERG2 layer. This is not a "STOP, redesign everything" finding — it is exactly the kind of narrow, well-scoped correction the ERG1 review's own correction policy anticipated when it flagged this as an open question rather than either ignoring it or over-fixing it prematurely.

## 16. Final report (22 items)

1. Starting HEAD `e56eab51c3d9018a0873ad31756ae55fe1964450` (== origin/main); DB checkpoint confirmed unchanged throughout — see intro.
2. Files read: listed above (intro) — including, newly this mission, `Installation`/`Airport`/`Signal` models and real `status` value usage across `scripts/*.py`.
3. Three-dimension verdict: **two independent primitives** (inventory, active_watch) **plus one derived boolean** (canonical_admission_relevant = OR of the two) — not three independent dimensions (§1).
4. Inventory definition: confirmed E/F evidence, any temporality, temporal-discount-exempt, unaffected by contradiction (§2/§6).
5. Canonical-admission definition: `inventory_relevant OR active_watch`, boolean, no richer state needed (§2/§7).
6. Active-watch definition: A/B/C/D contributing-and-not-`COMPLETED`, OR E/F with explicit current/future temporality — asymmetric by design, justified (§2/§5).
7. Case A–L matrix: table in §3.
8. Existing dormant EMAS verdict: inventory-relevant, admission-eligible, NOT watch-worthy (Case A/D/E) — this is the exact conflation the ERG1 review flagged, now resolved.
9. Future/replacement verdict: both inventory- and watch-relevant when a repair/replacement carries explicit current/future temporality (Case B/C).
10. Early-signal verdict: watch-relevant, NOT inventory-relevant, still admission-eligible — preserves the design doc's own early-discovery principle unchanged (Case F/G/H).
11. Historical-only verdict: inventory-relevant, admission-eligible, not watch-worthy; "still physically present" remains a separate human-verification question outside relevance scope (§6, Case E).
12. Cancelled/removed verdict: contradictions never suppress any dimension, only surface (§10, Case I/J).
13. `UNKNOWN` temporality verdict: preserves inventory and admission relevance unconditionally; does NOT grant watch for E/F; DOES still contribute to watch for A/B/C/D (unchanged, already-reviewed-sound reasoning) (§9).
14. Contradiction verdict: never changes any of the three dimensions, always surfaced, deterministic and explainable (§10).
15. Signal firewall verdict: reconfirmed and strengthened — `Signal.airport_id` is structurally non-nullable, so a pre-canonical candidate cannot have a Signal regardless of `active_watch` (§8).
16. Anoka verdict: unaffected, identical to the existing locked regression (§14).
17. Current ERG1 contract sufficiency verdict: **insufficient as-is** — verdict C (§11).
18. Exact ERG1 change required: add `is_inventory_relevant: bool`; refine `is_watch_worthy`'s computation to the asymmetric §2 rule; leave `is_canonical_admission_relevant`, `RelevanceOutcome`, `EvidenceClass`, `ObservationPolarity` unchanged (§11) — NOT implemented in this mission.
19. ERG2 persistence implications: persist both booleans directly (no longer safely re-derivable from `outcome` alone under the new model) alongside the previously-recommended fields; no new table, two additional columns (§12).
20. Website/shareholder sanity verdict: passes — every sketched sentence maps cleanly to the two-boolean-plus-outcome contract (§13).
21. **READY_FOR_ERG2: NO** — implement the §11/§18 ERG1 revision first (a narrow, well-specified, low-risk change), under its own adversarial review, before designing ERG2's persistence schema around it.
22. Exact recommended next mission: **"RWI ERG1.6 — Inventory/Watch Boolean Refinement"** (implementation + adversarial review + commit/push, mirroring the ERG1/ERG1-review two-mission pattern): implement exactly the §11/§18 change (one new field, one refined computation, zero new vocabulary), extend the test suite with the full Case A–L matrix from §3 as permanent regressions, re-verify the Anoka regression and every existing test remains green, then proceed to ERG2 only after that slice is committed.

RWI_ERG1_5_INVENTORY_VS_OPPORTUNITY_DESIGN_RESOLVED
