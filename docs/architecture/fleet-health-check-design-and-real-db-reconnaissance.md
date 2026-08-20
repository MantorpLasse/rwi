# RWI Fleet Health Check — Design and Real-DB Reconnaissance

Status: design + reconnaissance only. No repair, no migration, no write of any kind
was performed against the real database. No commit/push authorized for this task.

## 1. Mission and Non-Goals

**Mission**: systematically inspect the real RWI airport/runway/runway-end/Signal/
evidence graph for structural, semantic, provenance, identity, lifecycle, and
presentation anomalies; determine which anomalies actually exist, how widespread
they are, which checks can be made deterministic, which require human judgment,
and how a future reusable health-check system should be designed.

**Non-goals for this task** (explicitly out of scope, per the mission's own hard
safety rules): no repair of any anomaly found; no mutation of the real database;
no Signal create/update/delete; no Airport/Runway/RunwayEnd/Installation change;
no ReviewerAction; no SourceAssertion modification; no publish/unpublish; no
migration; no automatic repair system; no commit/push.

## 2. Current Architecture Relevant to Health Checking

Two graphs coexist in the real database:

- **Physical/geographic graph** (mostly legacy, pre-dates the governed pipeline):
  `Airport -> Runway -> RunwayEnd`, and `Airport/Runway -> Installation` (the
  EMAS system model — replaces the old separate `EmasInstallation`/`EmasBed`
  models). `RunwayEnd` (`app/models/runway_end.py`) is deliberately identity-only
  (`runway_id` + normalized designation) — no lifecycle, alias, or geometry
  fields, by design (`docs/domain/canonical-runway-runway-end-design.md`).
- **Governed evidence graph** (new, narrow real coverage — see §3):
  `Source -> SourceAssertion -> {identity_guard_decision, intelligence_review_decision,
  promotion_policy_decision} -> ReviewerAction -> Signal` (via
  `governed_signal_creation.create_signal_from_approved_review()`), plus a
  parallel `SourceAssertion -> InstallationAssertionLink -> PhysicalInstallationIdentity`
  reconciliation graph scoped to *physical EMAS identity*, not Signal identity.

Key facts confirmed by reading the current models and services fresh (not from
memory):

- **Authoritative vs. legacy**: `RunwayEnd` is the canonical runway-end
  representation; `PhysicalInstallationIdentity.runway_end_id` and free-text
  `Installation.runway_end` are explicitly documented as pre-dating it and never
  auto-linked ("every link stays a separate, human-approved step... regardless
  of how 'obvious' it looks" — `canonical-runway-runway-end-design.md` §16).
  `Signal` itself has **no** canonical `RunwayEnd` FK at all — only `runway_id`
  (a whole `Runway`, both ends) and a free-text `runway_end`-shaped hint nowhere
  on the model. This is a genuine, named model gap (§18).
- **Nullable relationships and why**: `Signal.runway_id`, `Installation.runway_id`,
  `PhysicalInstallationIdentity.runway_id`/`runway_end_id` are all nullable by
  design — an airport-level fact (e.g. "MSP has EMAS somewhere") doesn't always
  have a resolvable runway. `PhysicalInstallationIdentity.runway_id` is *null on
  all pre-existing rows by deliberate design* per the same doc ("Existing runway
  history makes a canonical FK more speculative than useful").
- **Historical-vs-current semantics**: **`Installation` has no lifecycle field.**
  Every one of the 149 real rows has `status = "active"`; there is no
  `superseded_by`/`removed_year`/`is_current` concept. The only way two rows can
  represent "the same physical bed at two points in time" is convention (a
  low-confidence generic row plus a later, better-sourced row) — the database
  cannot structurally distinguish "two different beds" from "one bed described
  twice." This is the single biggest historical/current-confusion risk in the
  schema (see §7 and §9).
- **Weak/overloaded fields**: `Signal.confidence` mixes an epistemic scale
  (`high`/`medium`/`low`) with what look like project-lifecycle words
  (`confirmed`/`planned`/`programmed`/`speculative`) — 6 distinct values, no
  CHECK constraint. `Signal.status` and `Signal.category` are likewise free
  string columns with no DB CHECK constraint (unlike `ReviewerAction.action` or
  `InstallationAssertionLink.outcome`, which are both CHECK-constrained) — 11
  distinct `status` values and 6 distinct `category` values exist in real data
  today, none of it schema-enforced.
- **Areas the DB cannot currently represent**: no `Signal <-> PhysicalInstallationIdentity`
  FK exists, so Signal-level duplicate detection cannot use the physical-identity
  anchor for any Signal whose supporting evidence was never linked through
  `InstallationAssertionLink` — which, per §3, is nearly the entire real Signal
  corpus. The existing reconciliation-guard design doc itself already names this
  exact gap for two specific real Signals (see §7).

Full per-file service and doc findings (physical-installation reconciliation,
canonical runway identity, human review queue invariant checks, existing-Signal
reconciliation R1–R4D, static export) were re-read fresh for this task; the
load-bearing points are folded into §5–§10 below rather than repeated as a
separate literature survey.

## 3. Real DB Baseline

Verified read-only against `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db`
(absolute path resolved), both before and after all reconnaissance work in this
task:

| Check | Value |
|---|---|
| HEAD | `2472e543d1519a4a105de40111413667c6c850b2` (== origin/main) |
| DB SHA-256 (before and after) | `4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965` |
| DB size (before and after) | 1,794,048 bytes |
| DB mtime (before and after) | 1787237717.1444063 |
| `PRAGMA foreign_key_check` | `[]` |
| `PRAGMA integrity_check` | `ok` |

Entity counts:

| Entity | Count |
|---|---|
| Airports | 86 |
| Runways | 180 |
| RunwayEnds | 360 |
| Installations (EMAS) | 149 |
| Signals | 68 (66 published, 2 unpublished) |
| Sources | 70 |
| SourceAssertions | 222 |
| ReviewerActions | 2 |
| PhysicalInstallationIdentities | 10 |
| InstallationAssertionLinks | 12 |

**Governed-pipeline coverage is extremely narrow**: of 222 SourceAssertions, only
**1** (`#222`) has ever had `identity_guard_decision`/`intelligence_review_decision`/
`promotion_policy_decision` populated; the other 221 are pre-governed-pipeline
rows with all three NULL. Of 68 Signals, only **1** (`#67`) has a governed
`SourceAssertion.signal_id` link; the other 67 have only a legacy `Signal.source_id`
pointer with no traceable `SourceAssertion` chain. This single fact should drive
prioritization throughout: R1–R4E's reconciliation guard today protects exactly
one real Signal-creation event; the other 67 Signals were created by one of at
least 6 other, ungoverned write paths (`signal_rules.py` and five `scripts/*.py`
importers).

## 4. Health-Check Taxonomy

Nine anomaly classes were evaluated against real data (A–I as specified in the
mission). Each is scoped narrowly rather than merged into a generic "bad data"
bucket, matching the R1–R4E precedent of separating structural anchors from
advisory signals.

- **A. Airport identity** — code/name/duplicate checks.
- **B. Runway structure** — cardinality/orphan checks.
- **C. EMAS/physical installation** — lifecycle and cross-airport checks.
- **D. Signal identity/placement** — cross-entity placement checks.
- **E. Signal lifecycle/temporal** — chronology checks.
- **F. Provenance/evidence health** — traceability checks.
- **G. Governance health** — ReviewerAction/workflow-state checks.
- **H. Publication/presentation health** — static-export consistency checks.
- **I. Legacy data/model-lag** — explicitly *not* anomalies, tracked separately
  so they are never miscounted as defects.

## 5. Rule Classification Model

Every rule below is tagged with exactly one of:

- `DETERMINISTIC_ERROR` — persisted facts violate a hard invariant.
- `DETERMINISTIC_WARNING` — structurally suspicious, not necessarily wrong.
- `REVIEW_REQUIRED` — evidence suggests a semantic conflict needing human judgment.
- `INFORMATIONAL` — legacy/model-quality observation, not itself a defect.
- `NOT_CURRENTLY_DETECTABLE` — model lacks the structured information to check safely.

Default posture for every rule: **no auto-repair**. A rule may recommend a
specific human action but never performs one.

## 6. Proposed Rule Catalogue

*(Counts below reflect the post-critical-review catalogue — see "Critical Review
Corrections" at the end of this document for what changed from the original
reconnaissance draft and why. The original draft's summary line here
undercounted the table by 7 rows and mis-stated several classifications; both
are corrected in place below rather than left standing.)*

32 rules proposed: **12 DETERMINISTIC_ERROR, 7 DETERMINISTIC_WARNING, 6
REVIEW_REQUIRED, 4 INFORMATIONAL, 3 NOT_CURRENTLY_DETECTABLE.**

| ID | Name | Class | Entities | Evidence used | Why safe | False-positive risk | Human review? | Auto-repair? |
|---|---|---|---|---|---|---|---|---|
| FH-A1 | Airport with zero runways | INFORMATIONAL *(was DETERMINISTIC_WARNING — corrected, see review notes)* | Airport, Runway | LEFT JOIN, no matching Runway row | Pure structural absence, no interpretation | Confirmed real and legitimate for all 10 real hits (non-US airports added for context, none carry a Signal) — the original draft's own §7/§12 prose already called this "INFORMATIONAL in practice," contradicting the table's WARNING label; the table is now the corrected side of that contradiction. Would need to be re-escalated to REVIEW_REQUIRED only if such an airport ever also carried a Signal. | No | Never |
| FH-A2 | Duplicate IATA/ICAO/FAA code across airports | DETERMINISTIC_ERROR | Airport | `GROUP BY code HAVING count>1`, restricted to `code IS NOT NULL AND code != ''` | Codes are meant to be globally unique identifiers | Near zero for a genuine collision — but the query MUST also exclude empty string, not just NULL: verified 0 real rows store `''` today (all unset codes are true NULL), so this is currently safe, but the rule spec must encode `!= ''` explicitly or a future empty-string import would generate a mass false collision across every code-less airport | Yes | Never (which row is canonical requires human judgment) |
| FH-A3 | Airport with no IATA/ICAO/FAA code at all | DETERMINISTIC_WARNING | Airport | All three code columns NULL/empty | Structural, no interpretation | Airport becomes permanently excluded from canonical NASR matching (named in `runway_inventory.py`) — real but low-urgency | No | Never |
| FH-A4 | Airport name lacks any airport-shaped token | REVIEW_REQUIRED *(was DETERMINISTIC_WARNING — corrected, see review notes)* | Airport | Name string, absence of {Airport, Field, International, Regional, Airfield, Aerodrome, Airpark, Base, Airstrip, ...} AND presence of a valid ICAO/FAA code proving the row is a real, referenced airport | A real airport's proper name almost always contains one of these tokens; codes prove it's not a phantom row | **High if used alone** — 4 of 7 raw triage hits were legitimate (see §11 false-positive trap on the "County" keyword); detection is mechanical but *significance* is never machine-decidable, so this can never honestly be a DETERMINISTIC class regardless of triage framing | Yes, always | Never |
| FH-B1 | Runway without exactly 2 RunwayEnds | DETERMINISTIC_ERROR | Runway, RunwayEnd | COUNT(RunwayEnd) per Runway != 2 | `RunwayEnd` is defined as strictly two-ended (`runway_identity.py`) | None found in real data (0/180) | Yes | Never |
| FH-B2 | Duplicate runway designation within one airport | DETERMINISTIC_ERROR | Runway | GROUP BY airport_id, designation HAVING count>1 | Designations are meant to be unique per airport | None found (0/180) | Yes | Never |
| FH-B3 | RunwayEnd designation pair not a valid reciprocal (heading delta != 18) | DETERMINISTIC_WARNING | Runway, RunwayEnd | Numeric heading parsed from both ends | Reciprocal runway ends differ by 180°/18 (in tens of degrees) by physical definition | Parallel-runway suffixes (L/R/C) and non-numeric designations need careful parsing; none found in real data (0/180) | Yes if found | Never |
| FH-B4 | Runway/RunwayEnd designation not in canonical normalized form | DETERMINISTIC_WARNING | Runway, RunwayEnd | Compare stored designation to `runway_identity.normalize_end()`/`normalize_pair()` output | Normalization function is already the single source of truth used on every read | Low — this is exactly what breaks downstream matching (fails closed to "no match", not a crash) if it drifts | No, but blocks other detections silently if unfixed | Never |
| FH-B5 | Runway present in DB with no NASR/canonical-import counterpart | INFORMATIONAL | Runway | Cross-reference against `runway_inventory.py`'s own `PARTIAL_MATCH` classification | Already a named, non-error classification in existing code | N/A — informational by design | No | Never |
| FH-C1 | Installation.replacement_year < install_year | DETERMINISTIC_ERROR | Installation | Both year columns non-null, replacement < install | A replacement cannot predate its own installation — this is a logical, not business, invariant | **Corrected framing**: `replacement_year` is populated on **0 of 149** real rows (not "0/149 violations found by testing" — the field is currently entirely unused, so this rule is logically sound but has zero real informative coverage today, not a verified-safe track record) | Yes, if ever populated with a violation | Never |
| FH-C2 | Installation.runway_id belongs to a different airport than Installation.airport_id | DETERMINISTIC_ERROR | Installation, Runway | JOIN comparing airport_id | Referential nonsense if it occurred | None found (0/149) | Yes | Never |
| FH-C3 | Multiple Installation rows sharing airport_id with both runway_id and runway_end NULL | DETERMINISTIC_WARNING | Installation | GROUP BY airport_id WHERE runway_id IS NULL AND runway_end IS NULL HAVING count>1 | Structural, no interpretation of content | **Confirmed real and explainable** (see §7) — usually one generic FAA-map row plus one better-sourced corroborating row, not independent beds; still worth surfacing because the DB cannot distinguish the two cases structurally | Yes, always — this is exactly a "same real bed described twice" candidate | Never |
| FH-C4 | InstallationAssertionLink.outcome=SAME_PHYSICAL_INSTALLATION with contradictory later link on the same assertion_id | REVIEW_REQUIRED | InstallationAssertionLink | Latest-by-`(reviewed_at, id)` per assertion_id, compare outcome sequence | Reuses R2's own "retraction must be visible" logic (`existing_signal_reconciliation_candidates.py`) rather than reimplementing it | Low — mirrors already-reviewed production logic | Yes | Never |
| FH-C5 | PhysicalInstallationIdentity.runway_id/runway_end_id inconsistent with parent airport | DETERMINISTIC_ERROR | PhysicalInstallationIdentity, Runway, RunwayEnd | JOIN comparing airport_id | Same class as FH-C2, enforced at write time by `physical_installation_reconciliation.py`, checked here as a standing invariant in case a writer bypassed it | None found (0/10) | Yes | Never |
| FH-D1 | Signal.runway_id belongs to a different airport than Signal.airport_id | DETERMINISTIC_ERROR | Signal, Runway | JOIN comparing airport_id | Enforced at write time only by `governed_signal_creation.py`; the other 6 write paths (incl. `signal_rules.py`) do not enforce it, and the static export does not check it either — a real, unguarded gap | None found (0/68) today, but structurally unguarded for 5 of 6 non-governed write paths | Yes if found | Never |
| FH-D2 | SourceAssertion.signal_id links to a Signal whose airport_id disagrees with the assertion's own airport_id | DETERMINISTIC_ERROR | SourceAssertion, Signal | JOIN comparing airport_id | Named explicitly in R2's own docstring as "a data-integrity anomaly that must surface, never be silently hidden" | None found (0/1 governed link) | Yes if found | Never |
| FH-D3 | Two or more Signals at the same airport+runway_id | REVIEW_REQUIRED *(was DETERMINISTIC_WARNING — corrected, see review notes)* | Signal | GROUP BY airport_id, runway_id HAVING count>1 (runway_id NOT NULL) | Structural co-location only, no title/amount comparison (reuses R1's explicit prohibition on using those as proof) | Re-verified fresh: 0 real groups today. A shared `runway_id` is one of R1's own genuine structural ANCHOR fields (`existing_signal_reconciliation.py`) that *blocks* governed Signal creation — stronger than mere co-location, so it deserves the same "always route to a human" treatment as FH-D4, not a lower "warning" tier that a future operator might read as advisory-only | Yes, always | Never |
| FH-D4 | Two or more Signals at the same airport with runway_id NULL | REVIEW_REQUIRED | Signal | GROUP BY airport_id HAVING count>1 (runway_id NULL) | Deliberately weak signal — airport-level co-location alone, no title/vendor/amount comparison used to declare a match | **High if misused as duplicate proof** — 12 real groups found, and inspection shows most are legitimately distinct (different USAspending grant FYs/amounts, different incident dates); one pair (Signal #41/#67 at MSP) is a genuine, already-named candidate (§7, §16) | Yes, always | Never |
| FH-E1 | Signal.planning_year > Signal.procurement_year | DETERMINISTIC_WARNING *(was DETERMINISTIC_ERROR — corrected, see review notes)* | Signal | Both non-null, planning > procurement | Detection is mechanical; **significance is not** — `planning_year`/`procurement_year` have no documented field contract on the model (only "carried over from the old Project model," per `app/models/signal.py`) | **1 real hit, re-examined**: Signal #3's own notes describe a multi-phase Boston Logan RSA/EMAS project where Phase 1 procurement (2025) is on record and Phase 2 planning (2026) was logged later, as a continuation — a legitimate async-per-phase update pattern, not an impossible chronology. Treating this as a hard error would require exactly the "business inference"/"lifecycle guesswork" the review explicitly disallows for `DETERMINISTIC_ERROR` | Yes, always | Never |
| FH-E2 | Signal.procurement_year > Signal.target_year | DETERMINISTIC_WARNING *(was DETERMINISTIC_ERROR — corrected, see review notes)* | Signal | Both non-null | Same field-contract gap as FH-E1 — downgraded preemptively for consistency even though it has 0 real hits, since the underlying risk (asynchronous per-phase field updates on a single Signal row, demonstrated concretely by Signal #3) applies equally here | Re-verified fresh: 0/68 today, but no informative real coverage of the actual failure mode until a real multi-phase Signal populates both fields | Yes if found | Never |
| FH-E3 | Signal.construction_start > Signal.completion_date (full date, not year-only) | DETERMINISTIC_ERROR | Signal | Both non-null, full `Date` comparison | Construction cannot start after its own completion — retained as ERROR (not downgraded like FH-E1/E2) because it compares two full dates on the *same row describing the same construction effort*, not two independently-updated year fields that can legitimately drift across project phases | Re-verified fresh: 0/68 today; Signal #3 itself (`construction_start=2026-08-31`, `completion_date=2026-11-15`) is internally consistent with this rule, reinforcing that E3's narrower same-effort framing is safer than E1/E2's cross-field year comparison | Yes | Never |
| FH-E4 | Signal.status="completed" with no completion_date and no other year field consistent with "done" | REVIEW_REQUIRED | Signal | status literal, completion_date NULL | `status` is free-text/unconstrained (§2), so "completed" is a claim, not a guarantee | **1 real hit**: Signal #65 (§16) — legitimately worth a human glance, not a hard error, since `completion_date` is a documented-nullable legacy field | Yes | Never |
| FH-F1 | Signal.source_id set but zero governed SourceAssertion (`signal_id` link) exists | INFORMATIONAL | Signal, SourceAssertion | Anti-join | Not a defect — expected for every pre-governed-pipeline Signal | N/A by design — **67 of 68 real Signals hit this**, tracked as legacy-provenance-tier bucketing, not a per-row finding (§9) | No | Never |
| FH-F2 | SourceAssertion with airport_id NULL and review_state='unreviewed' | INFORMATIONAL | SourceAssertion | airport_id IS NULL AND review_state='unreviewed' | Documented as legitimate for un-attributable raw evidence rows still pending identity-guard processing | **5 real hits**, all `unreviewed`/pre-pipeline (§7); re-verified fresh, all 5 confirmed unreviewed | No | Never |
| FH-F3 | SourceAssertion with airport_id NULL and review_state='reviewed' | REVIEW_REQUIRED | SourceAssertion | airport_id IS NULL AND review_state='reviewed' | Split out from FH-F2 during critical review — a *reviewed* assertion that still has no airport is a materially different, more concerning case than one merely awaiting processing, and folding both into one rule with a conditional class blurred that distinction | **0 real hits** (all 5 airport_id-NULL rows are unreviewed) | Yes, always | Never |
| FH-G1 | `promotion_policy_decision=HUMAN_REVIEW_REQUIRED` with `identity_guard_decision != ATTACH_CONFIRMED` or `intelligence_review_decision != REVIEW_REQUIRED` | DETERMINISTIC_WARNING | SourceAssertion | Direct reuse of `human_review_queue.py`'s own existing `invariant_warnings` logic | Already-reviewed, already-shipped logic; this rule just standardizes running it as a fleet-wide check rather than only within the live queue view | Low — inherits the existing module's own low false-positive design | Yes | Never |
| FH-G2 | Latest ReviewerAction is MARK_DUPLICATE but SourceAssertion.signal_id != duplicate_of_signal_id | DETERMINISTIC_ERROR | ReviewerAction, SourceAssertion | Direct reuse of existing `human_review_queue.py` invariant | Same as FH-G1 | Low | Yes | Never |
| FH-G3 | Latest ReviewerAction is DEFER/NEEDS_MORE_EVIDENCE/REJECT_SIGNAL but SourceAssertion.signal_id is non-null | DETERMINISTIC_ERROR | ReviewerAction, SourceAssertion | Direct reuse of existing `human_review_queue.py` invariant | Same as FH-G1 | Low | Yes | Never |
| FH-H1 | Published Signal whose `_signal_view()` would dereference a null-required field and crash the export | NOT_CURRENTLY_DETECTABLE as a standing rule | Signal | Would require running the actual export function per-row in isolation | The export itself is the only accurate oracle for "would this crash a real build"; a static SQL check would either under- or over-approximate its exact null-handling branches | N/A | N/A | Never — this is inherently a "run it and see" check, folded into Phase 8/§10 practice, not a row-level rule |
| FH-H2 | Published Signal count vs. rendered signal detail-page count mismatch | DETERMINISTIC_ERROR | Signal, static export output | `COUNT(*) WHERE published=1` vs. count of rendered `signals/{id}.html` files | Direct, mechanical comparison of persisted vs. rendered state | None found — verified equal (66 == 66) in this task's own dry run (§10) | Yes if it ever mismatches | Never |
| FH-I1 (NOT_CURRENTLY_DETECTABLE) | "Is this Signal a duplicate of that Signal" | NOT_CURRENTLY_DETECTABLE | Signal | Would require a real-world-identity oracle the model does not have | No structural anchor (shared runway_id/physical_installation_id/source_id/artifact_identity, per R1) exists for the vast majority of legacy Signal pairs (§3, §9) | N/A | Always, when a human-plausible pair is surfaced by FH-D4 | Never |
| FH-I2 (NOT_CURRENTLY_DETECTABLE) | "Is this Installation row historical or still physically present" | NOT_CURRENTLY_DETECTABLE | Installation | `Installation` has no lifecycle/current field at all (§2) | The schema cannot represent the distinction, only convention (multiple rows) can hint at it | N/A | Always, if FH-C3 groups need resolving | Never |

## 7. Real Fleet-Wide Findings

Findings are grouped by rule; every finding lists stable IDs so it is reproducible
by re-running the same read-only queries.

- **FH-A1** (10 hits): Airports 76–85 — Congonhas, Santos Dumont, Queenstown,
  Wellington Intl, Zurich Intl, Roland Garros, Dzaoudzi Pamandzi Intl, Tokyo
  Haneda, Saarbrücken, RAF Northolt. All are non-US airports with valid
  IATA/ICAO codes and zero attached Signals — consistent with being
  context/reference rows rather than incomplete US canonical-inventory targets.
  Classed INFORMATIONAL in practice (Legacy §12), not an active defect.
- **FH-A4** (triage-only, 7 raw hits, **3 confirmed real**): a naive "airport
  name contains an organization-shaped word" scan hit 7 airports, all containing
  "county." Four are legitimate proper names (`Aspen/Pitkin County Airport`,
  `Dutchess County Airport`, `Fulton County Executive Airport`, both containing
  "Airport"/"Executive Airport" — real US GA airport naming convention). **Three
  are genuine anomalies**:
  - Airport **#28**, ICAO `KILG`/FAA `ILG`, `name = "New Castle County"` — the
    real airport (New Castle Airport, Wilmington DE) is missing the word
    "Airport" entirely; the name reads as the *owning county government's* name,
    not the airport's.
  - Airport **#23**, ICAO `KSBP`/FAA `SBP`, `name = "San Luis Obispo County"` —
    same pattern; the real airport is San Luis Obispo County Regional Airport.
  - Airport **#32**, ICAO `KSUA`/FAA `SUA`, `name = "Martin CountyWitham Field"`
    — a concatenation defect (missing separator/space between "Martin County"
    and "Witham Field"), and it has **already propagated into public content**:
    Signal **#20**'s title is `"Martin CountyWitham Field – EMAS-ersättning
    väntas efter incident (2021-09-01)"`, carrying the malformed name onto a
    published page.

  This directly and independently reproduces the anomaly class the human
  reviewer described ("recipient organization names accidentally becoming
  Airport names") — see §20.
- **FH-C3** (18 hits): Airport IDs 3, 4, 12, 13, 14, 27, 30, 38, 40, 41, 42, 47,
  50, 59, 62, 69, 70, 71 (Boston Logan, SFO, Chicago Midway, Teterboro, Kodiak,
  Waterbury-Oxford, Fort Lauderdale/Hollywood, Chicago O'Hare, Standiford, Baton
  Rouge Metro, Lafayette, Trenton-Mercer, LaGuardia, Philadelphia Intl, Rhode
  Island T.F. Green, McAllen Intl, Rutland, Reagan National) each carry exactly
  2 `Installation` rows with `runway_id` and `runway_end` both NULL. Inspection
  of `notes` on both rows in every sampled case (BOS, SFO, MDW, TEB, Kodiak)
  shows a consistent, self-documenting pattern: one row from a generic "FAA map
  region" aggregate import (`source_id=12`, no `install_year`), and a second,
  later, better-sourced row (`source_id` 51/57, `install_year` populated) with a
  Swedish-language note explicitly stating it is a "separate, newer entry in
  addition to the existing generic FAA map entry ... for the same airport."
  This is **not** silent duplication — the second row's own note documents the
  relationship — but the *database* has no structural link recording that
  relationship, so any consumer that just counts `Installation` rows per airport
  will overcount actual physical beds by exactly this pattern at these 18
  airports.
- **FH-E1** (1 hit, **reclassified DETERMINISTIC_WARNING during critical
  review** — see corrections section): Signal **#3** ("Runway 9/27 RSA and
  EMAS phase 2") has `planning_year=2026` but `procurement_year=2025`.
  Signal #3's own notes explain this fully: it is Phase 2 of an ongoing
  Boston Logan runway-safety-area/EMAS project, whose Phase 1 procurement
  (2025) is on record while Phase 2 planning (2026) was logged later as the
  project continued — a legitimate multi-phase, asynchronously-updated
  chronology, not an impossible one. This is exactly why the rule was
  downgraded from `DETERMINISTIC_ERROR`.
- **FH-E4** (1 hit): Signal **#65** ("Wellington EMAS-order (Runway Safe
  bekräftad leverantör)") has `status="completed"` but `completion_date=NULL`;
  the only year present is `target_year=2026`.
- **FH-D4** (12 groups, 30 Signals total): airport-level co-location groups at
  airports 3, 6, 9, 13, 19, 31, 37, 39, 44, 45. Ten of the twelve groups are
  differentiated by independent, structurally distinct evidence already present
  on the rows (distinct USAspending grant FY+amount pairs, or distinct
  incident dates embedded in `replacement_after_incident` titles for JFK,
  Teterboro, Bob Hope, Key West Intl, Chicago Executive) — genuinely separate
  real-world events, not duplicates. **One group (airport #45, Minneapolis–St.
  Paul Intl) contains the one governed-pipeline Signal in the entire real
  database** and is treated separately in §16 as the sole real P1 candidate.
- **FH-A2/A3, FH-B1–B3/B5, FH-C1/C2/C4/C5, FH-D1–D3, FH-E2/E3, FH-G1–G3**: **zero
  hits** in the real database for every one of these rules. This is itself a
  meaningful, positive finding — it means the schema's write-time guards
  (`physical_installation_reconciliation.py`, `governed_signal_creation.py`,
  the existing `human_review_queue.py` invariant checks) have held cleanly for
  every row currently in the real database, including the 221 legacy rows that
  never passed through them at write time.

## 8. Cross-Entity Findings

Joins (not single-table scans) were run specifically per the mission's Phase 6
guidance:

- `SourceAssertion -> Airport -> Runway -> Signal`: the one governed link
  (`SourceAssertion #222 -> Signal #67`) is fully consistent — both rows agree
  on `airport_id=45`, both have `runway_id=NULL`. **Zero cross-entity
  contradiction** in the only real chain currently governed end-to-end.
- `Signal -> supporting SourceAssertions -> their Airport/Runway`: only Signal
  #67 has any supporting SourceAssertion; already covered above. The other 67
  Signals have no governed SourceAssertion to cross-check against — a coverage
  gap (§9), not a contradiction.
- `RunwayEnd -> PhysicalInstallationIdentity -> InstallationAssertionLink ->
  SourceAssertion`: all 10 `PhysicalInstallationIdentity` rows and 12
  `InstallationAssertionLink` rows were enumerated. All 12 links are
  `SAME_PHYSICAL_INSTALLATION` with no `UNRESOLVED`/`DIFFERENT_PHYSICAL_INSTALLATION`
  retractions present, and no `supersedes_link_id` chains exist yet in real
  data — the retraction-visibility logic in R2 (`existing_signal_reconciliation_candidates.py`)
  is exercised by tests but has nothing to retract in the real fleet today.
- `ReviewerAction -> SourceAssertion -> signal_id/duplicate_of_signal_id`: the
  2 real rows (`#1 APPROVE_SIGNAL`, `#2 MARK_DUPLICATE -> Signal #67`, both for
  `SourceAssertion #222`) are internally consistent: `#222.signal_id == 67 ==
  reviewer_actions.duplicate_of_signal_id` for the latest action. No FH-G2/G3
  violation.

## 9. Historical Signal Health

All 68 real Signals were characterized on the structurally available fields:

| Bucket | Count | Rule membership |
|---|---|---|
| Governed provenance (has a linked SourceAssertion) | 1 | Signal #67 only |
| Legacy provenance only (source_id set, no SourceAssertion link) | 67 | FH-F1 |
| No usable provenance at all | 0 | — (every Signal has a non-null source_id) |
| Runway-linked (`runway_id` non-null) | 12 | — |
| Airport-only (`runway_id` NULL) | 56 | — |
| Graduated to a real Installation (`installation_id` set) | 2 | — |
| Physical-installation identity available for its evidence chain | 1 (Signal #67, transitively, via #222 — though #222 itself has no `InstallationAssertionLink`) | 0 confirmed end-to-end |
| Structurally suspicious (hits any DETERMINISTIC_ERROR/WARNING rule) | 4 (Signals #3, #20's airport, #41/#67 co-location, #65) | FH-E1, FH-A4 (via airport #32), FH-D4, FH-E4 |
| Published | 66 | — |
| Unpublished | 2 (Signals #52, #54) | — |

No quality *score* was invented, per the mission's explicit instruction — these
are plain counts against named rules.

The existing (already-committed, pre-dating this task) design doc
`docs/architecture/existing-signal-reconciliation-guard-design.md` §16/§17
**already names Signals #41 and #67 by ID** as a known, accepted gap: neither
has `InstallationAssertionLink` coverage, so the physical-installation anchor
path is unusable for them, and duplicate detection between them is
correspondingly weaker than for governed evidence. This reconnaissance
independently rediscovered the same pair via a plain SQL co-location query
(§7, FH-D4) with no prior knowledge of that doc section consulted first —
a genuine, independent cross-validation (§16).

## 10. Public/Static-Export Consistency Findings

`scripts/export_static_site.py` was run read-only against the real database
into a scratch output directory (not the repo, not committed). DB hash was
identical immediately before (`4aa8c25f...`) and after (`4aa8c25f...`) —
confirmed strictly read-only.

- 66 numeric `signals/{id}.html` pages were rendered (plus 1 `signals/index.html`),
  **exactly matching** `COUNT(*) WHERE published=1 = 66`. FH-H2 passes cleanly —
  the `published` flag is correctly and exclusively gating public signal pages.
  This is a positive, confirmed invariant, not a finding of a defect.
- No build crash occurred (would have been a hard `AttributeError` per the
  architecture read in §2 if any published Signal had a NULL/orphaned
  `airport_id` — none do).
- **DATA_ANOMALY vs. PRESENTATION_ANOMALY vs. INSUFFICIENT_EVIDENCE**: every
  finding in §7/§8/§9 is a `DATA_ANOMALY` (present in the persisted rows, not
  introduced by rendering). No `PRESENTATION_ANOMALY` (a case where correct
  data renders incorrectly) was found in this pass — the export layer's
  fail-closed guards (§2) mean bad data degrades to a blank field, not a
  fabricated or misleading one. This distinction could not be fully explored
  without rendering and visually diffing every page (out of scope for a
  read-only reconnaissance task); flagged as `INSUFFICIENT_EVIDENCE` for a
  presentation-specific defect class, separate from confirming the 3 known
  data-level naming anomalies do appear verbatim on their rendered pages
  (confirmed for Signal #20's title, §7).

## 11. False-Positive / Epistemic Safety Analysis

Three concrete false-positive traps were encountered and avoided in this task,
directly informing rule design:

1. **Keyword-in-name heuristics are unsafe alone.** The organization-name scan
   (FH-A4) flagged 7 airports on the word "county"; 4 of 7 were legitimate
   (`Aspen/Pitkin County Airport` etc.). The rule must always be corroborated by
   the *absence* of any airport-shaped token, not merely the *presence* of an
   organization-shaped one, and even then must be `REVIEW_REQUIRED`, never
   `DETERMINISTIC_ERROR`.
2. **Airport/title/date co-location is not duplicate proof.** FH-D4 found 12
   groups; 10 were legitimate distinct events once their own structured fields
   (grant FY+amount, incident date) were inspected. This directly reuses the
   R1 lesson ("financial amount equality, vendor equality, or title similarity"
   are explicitly `UNSAFE_FOR_RECONCILIATION`) for the legacy Signal corpus,
   confirming that lesson generalizes beyond the governed pipeline it was
   designed for.
3. **"Old" is not "wrong."** `PhysicalInstallationIdentity.runway_id = NULL` on
   all 10 real rows, `Installation.status = "active"` on all 149 real rows, and
   `SourceAssertion.identity_guard_decision = NULL` on 221 of 222 real rows are
   all *documented, deliberate* states of pre-governed-pipeline data, not
   defects — treating them as errors would generate hundreds of false alarms
   for one architectural fact (§3, §12).

## 12. Legacy-Data Treatment

Explicitly tracked as **not** anomalies, per the mission's Category I:

- 10 zero-runway international airports (FH-A1) — reference rows, not incomplete
  US inventory targets.
- 221 SourceAssertions with all three governed-decision fields NULL — predate
  the governed pipeline entirely; NULL here means "never evaluated," not "failed
  evaluation."
- 67 Signals with only legacy `source_id` provenance — created by one of 6
  pre-governed write paths, all of which were legitimate at the time they ran.
- All 149 `Installation.status = "active"` values — the field has never
  recorded anything else; this is a model gap (§18), not 149 individual defects.
- All 10 `PhysicalInstallationIdentity.runway_id = NULL` values — deliberate,
  per `canonical-runway-runway-end-design.md`.

## 13. Proposed Implementation Architecture

Recommended shape, closely matching the R1–R4E precedent already proven in this
codebase (pure core / DB adapter / persistence-or-presentation / CLI layering):

```
app/services/fleet_health_rules.py       # pure: HealthFinding dataclass + one
                                          # function per rule ID, no DB import
app/services/fleet_health_check.py       # DB adapter: runs each rule against a
                                          # read-only Session, yields HealthFinding
scripts/run_data_health_check.py         # CLI: read-only engine by default,
                                          # renders a report (text/JSON)
tests/test_fleet_health_rules.py         # pure-core rule tests (fixtures, no DB)
tests/test_fleet_health_check.py         # DB-adapter tests (in-memory SQLite)
```

`HealthFinding` fields (mirroring the structure already used by
`ReconciliationReviewItem`/`invariant_warnings`): `rule_id`, `rule_class`
(one of the 5 classification values), `entity_type`, `entity_ids` (tuple of
stable IDs), `summary`, `evidence` (structured dict of the exact fields that
triggered it — never raw free text presented as fact), `human_review_required`
(bool, derivable from `rule_class`).

Design principles carried over deliberately from R1–R4E: no scoring (counts +
rule membership only, per the mission's own instruction and §9's table); no
silent repair (every rule is read-only by construction — the module never
imports a `Session` in write mode); stable rule IDs (`FH-` prefix, never
renumbered, only ever added-to — same convention as `existing_signal_reconciliation`'s
outcome vocabulary); reuse over reimplementation where existing code already
computes the same invariant (FH-G1–G3 call into `human_review_queue.py`'s
existing logic rather than re-deriving it, exactly as R4D reused R1/R2 rather
than re-implementing reconciliation).

## 14. Proposed CLI/Report Contract

```
python -m scripts.run_data_health_check --database data/runway_safe.db
python -m scripts.run_data_health_check --database data/runway_safe.db --airport 45
python -m scripts.run_data_health_check --database data/runway_safe.db --signal 67
python -m scripts.run_data_health_check --database data/runway_safe.db --rule FH-D4
python -m scripts.run_data_health_check --database data/runway_safe.db --severity DETERMINISTIC_ERROR
python -m scripts.run_data_health_check --database data/runway_safe.db --format json
```

Read-only engine by default (same `mode=ro` URI pattern already used by
`scripts/list_human_review_queue.py` and `scripts/review_reconciliation_item.py`
in dry-run mode) — this CLI never needs a write mode at all, since no rule in
§6 performs or recommends automatic repair. A schema-readiness gate
(`check_schema_readiness()`, reusing the same pattern as R4D/R4E) should refuse
to run before checking required columns exist, exactly as those two scripts do.

## 15. Remediation-Priority Model

Separate from anomaly severity/class (§5), a **priority** axis governs
remediation ordering:

- **P0**: hard integrity/identity error that can materially misrepresent public
  intelligence (e.g. a published Signal placed at the wrong airport/runway, a
  duplicate airport code causing evidence to silently split across two rows).
- **P1**: high-confidence structural/provenance problem needing human
  correction, but not currently misrepresenting anything published.
- **P2**: legacy/model-quality weakness worth cleaning when convenient.
- **P3**: informational/model enhancement (schema gaps, not data defects).

## 16. Candidate P0/P1 Findings

**No P0 finding exists in the real database today.** Every `DETERMINISTIC_ERROR`
rule (§6) returned **zero** real hits (§7) — there is no confirmed case of a
published Signal or public page currently misrepresenting airport/runway
placement, duplicate identity, or impossible referential state.

Two **P1** candidates, both explained in full:

1. **Airports #23, #28, #32 — organization-name-leak in `Airport.name`** (FH-A4).
   *Why P1, not P0*: the ICAO/FAA codes, runway data, and (for #32) Signal
   placement are all otherwise correct — this is a display-name quality defect,
   not a misattribution of runways/signals to the wrong airport. It is P1 and
   not P2 because it has already visibly propagated onto a **published** page
   (Signal #20's title, via #32), directly matching the human-observed
   suspicion that motivated this task. *Impact*: public-facing misrepresentation
   of an airport's identity on a live page — a credibility/trust issue for
   readers, not a decision-support/intelligence-accuracy issue (the underlying
   EMAS facts for these three airports are otherwise correctly attributed).
2. **Signals #41 and #67 (Minneapolis–St. Paul Intl, airport #45) — a
   candidate for human reconciliation review, not a confirmed duplicate**
   (FH-D4, cross-referenced against the existing reconciliation-guard design
   doc's own named gap, §9). *Why P1, not P0*: both Signals are individually
   internally consistent and correctly placed; nothing here indicates
   misplacement or fabrication. The structural evidence is airport-level
   co-location plus matching `category="replacement"` — **co-location, not
   duplication** — and per §8's own lesson (and R1's), co-location alone is
   never sufficient to declare a match; only a human, via the existing
   `CONFIRM_DISTINCT_SIGNAL`/`MARK_DUPLICATE` workflow, can resolve it. *Impact
   if actually the same project*: intelligence-duplication risk — a reader
   or downstream decision-support consumer could double-count one real EMAS
   project as two separate pipeline entries, inflating apparent activity at
   MSP. This is a real decision-support risk, which is why it is P1 and not
   P2, but the health check's role stops at surfacing the candidate.

## 17. Explicit Things That MUST NOT Be Auto-Repaired

- Airport name corrections (#23, #28, #32) — renaming an airport is a factual
  claim that needs a human source check (what is the airport's actual legal/
  common name), not a string-manipulation heuristic.
- Any FH-D4/FH-I1 "possible duplicate Signal" grouping — collapsing two Signals
  requires the same `CONFIRM_DISTINCT_SIGNAL`/`MARK_DUPLICATE` governed
  decision path R1–R4E already built for exactly this purpose; a health check
  must route into that existing human workflow, never bypass it.
- Any FH-C3 "possible duplicate Installation" grouping — same reasoning, and
  the model has no lifecycle field (§18) to even express "this row is
  superseded" without a schema change.
- `Installation.status`, `Signal.status`, `Signal.category` values — these are
  unconstrained free-text; a health check may flag an unrecognized value but
  must never normalize/rewrite it.
- Any `ReviewerAction` or `InstallationAssertionLink` row — both are enforced
  immutable at the ORM level (`before_update`/`before_delete` listeners) and
  must stay that way; a health check is a read path only.

## 18. Model Gaps / NOT_CURRENTLY_DETECTABLE Checks

- **No `Installation` lifecycle field** (§2, §9, FH-I2) — cannot distinguish
  "still installed" from "replaced/removed" without a schema change.
- **No `Signal <-> RunwayEnd` or `Signal <-> PhysicalInstallationIdentity` FK**
  — Signal-level physical-identity anchoring (the strongest possible duplicate
  signal) is structurally unavailable for any Signal, governed or legacy,
  today.
- **No general-purpose Signal-duplicate oracle** (FH-I1) — by design (R1's own
  taxonomy), and correctly so; this is a permanent, not temporary, gap that a
  health check should surface candidates for, never resolve.
- **`Signal.confidence`/`status`/`category` have no CHECK constraint** — a
  health check can only compare against the *currently observed* vocabulary
  (§2), not a canonical one, since none is declared anywhere in the schema.

## 19. Recommended Implementation Slices

1. **FHC1 — pure rule core**: `app/services/fleet_health_rules.py` +
   `HealthFinding` dataclass + **11** `DETERMINISTIC_ERROR` rules only —
   **FH-A2, B1, B2, C1, C2, C5, D1, D2, E3, G2, G3** (corrected during
   critical review: FH-E1 and FH-E2 were reclassified to
   `DETERMINISTIC_WARNING` and removed from this list — see corrections
   section; FH-H2 stays excluded from FHC1 because it requires running the
   static export, not a pure function over persisted fields). No DB adapter
   yet; pure functions over plain dataclasses, tested with fixtures only.
2. **FHC2 — DB adapter + CLI (read-only)**: `app/services/fleet_health_check.py`,
   `scripts/run_data_health_check.py`, wiring FHC1's rules against a real
   `mode=ro` session, with the schema-readiness gate from §14.
3. **FHC3 — warning/review-required rules**: add FH-A1, A3, A4, B3, B4, C3,
   C4, D3, D4, E1, E2, E4, F1, F2, F3, G1 — the rules that either found real
   hits (needing careful evidence-field design so output is reproducible and
   non-alarmist), reuse existing `human_review_queue.py` logic, or (FH-E1/E2)
   were moved here specifically because critical review showed their
   significance is not machine-decidable.
4. **FHC4 — presentation cross-check**: FH-H1/H2, wiring a read-only
   static-export dry run into the same report (published-count vs.
   rendered-count parity, as validated manually in §10).
5. **FHC5 (future, separately authorized)**: a governed *disposition* workflow
   for P1 findings — NOT auto-repair, but a structured way to route a finding
   like #23/#28/#32 or #41/#67 to a human reviewer and record the outcome,
   modeled on `ReviewerAction`'s own append-only pattern. Out of scope for
   this design task; named only as the natural next architectural step once
   FHC1–FHC4 exist.

## 20. Exact Recommended Next Slice

**FHC1**: implement `app/services/fleet_health_rules.py` with the `HealthFinding`
dataclass and exactly **11** `DETERMINISTIC_ERROR` rules —
**FH-A2, FH-B1, FH-B2, FH-C1, FH-C2, FH-C5, FH-D1, FH-D2, FH-E3, FH-G2, FH-G3**
— with pure-function unit tests (fixtures, no DB). This is the lowest-risk
possible starting point: every one of these 11 rules is a genuinely hard,
interpretation-free invariant that survived adversarial review (FH-E1/E2 did
not and were moved to FHC3), and 10 of the 11 already returned zero hits
against the real database in this reconnaissance (FH-C1 currently has zero
*informative* real coverage — see corrections — but the invariant itself is
sound). Shipping them first establishes the pattern and regression-proofs the
schema's existing write-time guards without generating a single finding a
human has to triage on day one.

---

## Answering §16/Q20: was the human-observed suspicion supported?

**Yes.** Independent, read-only, structured inspection — without encoding the
prior screenshots/observations as ground truth, per the mission's own
instruction — surfaced three concrete, real, already-published data anomalies
matching the exact class the human reviewer flagged ("recipient organization
names accidentally becoming Airport names": Airports #23, #28, #32) and one
concrete co-located Signal pair spanning the legacy/governed boundary
(#41/#67) that the codebase's own prior design work had already separately
identified and named as an unresolved candidate. No fabricated or invented
anomaly was needed to reach this conclusion, and — equally importantly — the
great majority of naive heuristics tried (organization-keyword matching,
title/date co-location) were shown to be unsafe as standalone proof,
reinforcing why the rule catalogue in §6 keeps every borderline check at
`REVIEW_REQUIRED` rather than `DETERMINISTIC_ERROR`.

---

## Critical Review Corrections

This document was subjected to a fresh, adversarial review checkpoint after
its initial reconnaissance draft, per `RWI_FLEET_HEALTH_CHECK_DESIGN_REVIEW_COMMIT_PUSH`.
The review re-verified every factual claim about the model/services against
the code fresh (not from the draft's own summary), re-ran every real-data
query independently, and specifically attacked the 9-rule FHC1 recommendation
the draft had proposed. The draft's overall architecture, taxonomy, and P0/P1
findings survived review intact; the following concrete errors and
overstatements were found and corrected in place rather than left standing:

1. **Rule catalogue count was simply wrong.** The draft's §6 summary line said
   "24 rules: 9 DETERMINISTIC_ERROR / 6 DETERMINISTIC_WARNING / 5
   REVIEW_REQUIRED / 2 INFORMATIONAL / 2 NOT_CURRENTLY_DETECTABLE," but the
   table beneath it actually contained 31 rows classified 14/8/3/3/3. The
   summary line was never recomputed after the table was finalized. Corrected
   counts (after the reclassifications below, and the one new rule added)
   are now 32 rules: 12/7/6/4/3.
2. **FH-A1 was self-contradictory.** The table classified it
   `DETERMINISTIC_WARNING`; the draft's own §7/§12 prose called it "Classed
   INFORMATIONAL in practice." Resolved in favor of the evidence: reclassified
   `INFORMATIONAL`.
3. **FH-A4 carried an internally inconsistent dual classification** ("kept
   DETERMINISTIC_WARNING only as a triage flag" while stating it is "always
   REVIEW_REQUIRED in practice"). A rule cannot honestly be both. Reclassified
   `REVIEW_REQUIRED` outright — detecting the *keyword pattern* is mechanical,
   but its *significance* is never machine-decidable (4 of 7 real triage hits
   were legitimate names), which is precisely what `REVIEW_REQUIRED` means and
   `DETERMINISTIC_WARNING` does not.
4. **FH-D3 was underclassified relative to its own evidence strength.** A
   shared `runway_id` between two Signals is one of R1's own genuine
   structural ANCHOR fields (it blocks governed Signal creation in
   `existing_signal_reconciliation.py`) — stronger evidence than FH-D4's
   airport-only co-location, which the draft correctly classified
   `REVIEW_REQUIRED`. Leaving FH-D3 at the weaker `DETERMINISTIC_WARNING` tier
   was inconsistent. Reclassified `REVIEW_REQUIRED` to match.
5. **FH-E1 and FH-E2 were the most substantive finding of this review.** Both
   were originally `DETERMINISTIC_ERROR`. Fresh inspection of Signal #3's own
   notes (the one real FH-E1 hit) shows a legitimate multi-phase project
   (Boston Logan runway-safety-area/EMAS, Phase 2) whose `planning_year` and
   `procurement_year` were populated at different times for different phases
   — not an impossible chronology, but an artifact of how these two
   undocumented, free-form year fields (no field-contract docstring exists
   beyond "carried over from the old Project model") get updated
   asynchronously as a real project evolves. Treating this as a hard error
   would require exactly the "business inference"/"lifecycle guesswork" the
   review's own instructions rule out for `DETERMINISTIC_ERROR`. Both rules
   were reclassified `DETERMINISTIC_WARNING`; FH-E3 (full-date, same-row,
   same-effort comparison) was kept as `DETERMINISTIC_ERROR` since it does not
   share this risk.
6. **FH-C1's "0/149" was misleadingly framed.** The draft presented it as "0
   violations found by testing 149 rows," but `replacement_year` is populated
   on **zero** of the 149 real `Installation` rows — the rule has never been
   exercised by a real value, populated or not. The rule's logic remains
   sound (kept `DETERMINISTIC_ERROR`), but the evidentiary claim was corrected
   to state plainly that it currently has no informative real coverage.
7. **FH-A2's empty-string risk was unaddressed.** The draft's query only
   excluded `NULL` codes; if a future import wrote `''` instead of `NULL` for
   an unset code, every code-less airport would collide as a false "duplicate"
   under a naive `GROUP BY`. Verified fresh: 0 real rows currently store `''`
   (all are true `NULL`), so no real finding changes, but the rule
   specification was corrected to require `code != ''` explicitly, not just
   `IS NOT NULL`.
8. **FH-F2 conflated two different situations under one conditional
   classification** ("INFORMATIONAL... unless review_state='reviewed' (would
   then upgrade to REVIEW_REQUIRED)"). Split into two separate rule IDs:
   FH-F2 (`airport_id IS NULL AND review_state='unreviewed'`, INFORMATIONAL,
   5 real hits, all confirmed unreviewed) and the new **FH-F3**
   (`airport_id IS NULL AND review_state='reviewed'`, REVIEW_REQUIRED, 0 real
   hits, re-verified fresh).
9. **P1 finding #2's wording was tightened** from "plausible same-project
   duplication" to "a candidate for human reconciliation review, not a
   confirmed duplicate," with explicit impact framing added to both P1
   findings (§16), per the review's own instruction that rule/finding labels
   must never claim more than the structural evidence supports — the
   Signal #41/#67 evidence is co-location plus category match, not proof of
   identity.
10. **FHC1's scope changed as a direct result of #5.** The originally
    recommended FHC1 rule set (13 IDs listed against a claimed "9," itself
    another instance of the counting error in #1) is now, after removing
    FH-E1/E2, an accurate, internally consistent 11 rules: **FH-A2, FH-B1,
    FH-B2, FH-C1, FH-C2, FH-C5, FH-D1, FH-D2, FH-E3, FH-G2, FH-G3.**

**What did not change**: the overall taxonomy (A–I), the four-layer
implementation architecture (§13), the CLI contract (§14), the P0 verdict
(none), the identity of the P1 findings themselves (only their wording/impact
framing), the Installation-lifecycle model-gap conclusion (§18), the
false-positive lessons (§11), and the "auto-repair boundary" (§17, §19) all
survived adversarial review unchanged — each was independently re-derived
from a fresh reading of the code and a fresh re-run of the underlying queries
in this checkpoint, not merely re-asserted from the draft.
