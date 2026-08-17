# EMAS ↔ Canonical RunwayEnd Semantics and NASR Promotion Analysis

**Design + read-only analysis only. No database write, no assertion
promotion, no `PhysicalInstallationIdentity` creation, no schema change,
no ingestion, no public/UI change.** Baseline: branch `main`, HEAD
`93682ad63082efe2723595ede685fe78851785ec`, DB `data/runway_safe.db`
(667648 bytes, unchanged before/after this task), `Runway`=180,
`RunwayEnd`=360, U.S. planner 76 `ALREADY_COMPLETE`/0 unresolved/0
ambiguous/0 conflict — all re-confirmed identical at the end of this task.

## 1. Problem statement

Recent BOS/ORH research
([`bos-orh-authoritative-web-research-pilot.md`](../product/bos-orh-authoritative-web-research-pilot.md))
found that FAA NASR's `RWY_END_ID` field and airport-operator public
language can name the *same physical EMAS bed* differently: NASR reports
BOS's beds as physically at `04L`/`15R`, while Massport's own newsroom
names them `Runway 22R`/`Runway 33L`. Before RWI promotes any of the 115
already-ingested `direct_strong` NASR current-EMAS-presence
`SourceAssertion` rows (63 airports) toward publication, this reference-
frame distinction must be defined precisely, and each assertion must be
classified for whether it is safely resolvable under that definition.

## 2. Current model semantics (as built, not as named)

| Field | Code meaning today | Doc meaning | Test-enforced? | Explicit or implied? |
|---|---|---|---|---|
| `PhysicalInstallationIdentity.runway_end_id` | FK to `RunwayEnd`, set only via `record_reconciliation_decision()`/`create_physical_installation_identity()` (`app/services/physical_installation_reconciliation.py`) | `canonical-runway-runway-end-design.md`: "physical runway/end" (used throughout) | Yes — `_validate_identity()` requires the `RunwayEnd` to belong to the same airport | **Implied by usage, not written as one sentence anywhere** — but unambiguous in practice (see §8) |
| `SourceAssertion.runway_end` | Normalized string, written **only** by `_nasr_presence_view()`'s read path expecting it, never actually populated by any ingestion script (§8/§10) | Same design doc: "governs `SourceAssertion`s and, after review, `PhysicalInstallationIdentity` — never the runway/end rows themselves" | No direct test of its semantic meaning (only that it stays `NULL` pre-promotion, via the gap-analysis findings) | Implied |
| `SourceAssertion.raw_runway_end_value` | Free-text, exactly the NASR `RWY_END_ID` string, unmodified | Not separately documented | No | Implicit (matches NASR's own field name/values 1:1) |
| `Installation.runway_end` | Free-text, no FK, populated inconsistently (often `NULL` even when notes describe a specific end) | `docs/domain/canonical-runway-runway-end-design.md` doesn't cover `Installation` at all — it predates the canonical model and was never migrated | No | N/A — a separate, older, unmigrated concept |

`Installation` was never given a `runway_end_id` FK and is out of scope
for this analysis — it remains a free-text, non-canonical record of
*what's installed*, distinct from `PhysicalInstallationIdentity`'s
*where, canonically*.

## 3. Physical vs. protected/source-named reference frames

Four concepts, per this task's brief:

- **A. Physical bed location** — the canonical `RunwayEnd` at/beyond which
  the bed physically sits. This is what NASR's `RWY_END_ID` records.
- **B. Protected operational direction** — the reciprocal `RunwayEnd`:
  the direction whose overrunning aircraft the bed catches. This is what
  airport-operator public language (Massport's press releases, USAspending
  grant text) typically names.
- **C. Source-named runway/end** — whatever a specific document literally
  says, which may be either A or B depending on the source.
- **D. Canonical runway pair** — the `Runway` row containing both
  reciprocal `RunwayEnd`s.

**Verdict: B is deterministically derivable from A via canonical Runway
topology and must not be stored separately.** Every governed `Runway` has
exactly two `RunwayEnd` children (§5) — "the reciprocal of end X" is
always exactly "the other `RunwayEnd` on the same `Runway`," a pure
relationship lookup, never designation arithmetic. Storing B as its own
field would duplicate information already fully recoverable from A + the
existing topology, and would introduce a second field that could drift
out of sync with the first.

## 4. Reciprocal-end derivation

Verified read-only against the real database: **every one of the 180
governed `Runway` rows has exactly 2 `RunwayEnd` children, with zero
exceptions** (`RunwayEnd` count = `Runway` count × 2 exactly, and a direct
`GROUP BY runway_id HAVING count(*) != 2` query returns no rows). This is
structurally guaranteed, not incidental: `plan_airport_inventory()` only
ever accepts NASR rows shaped as an exact two-token pair
(`is_two_ended_pair_shape()`), so no canonical `Runway` can exist with any
other number of ends. Deriving the reciprocal by topology ("the other
`RunwayEnd` row sharing this `runway_id`") therefore requires **zero**
designation-parsing heuristics and has **zero** known exceptions anywhere
in the governed 76-airport dataset. This is preferred over, and used
instead of, any heading-arithmetic approach (e.g. ±180°), which would be
fragile for non-numeric-only designations and unnecessary given the
topology guarantee.

## 5. BOS case study

| EMAS | Source-named end/direction | NASR raw value | Normalized `SourceAssertion.runway_end` | Canonical `Runway` | Physical `RunwayEnd` | Reciprocal `RunwayEnd` | Operator/public naming | Reconciliation state |
|---|---|---|---|---|---|---|---|---|
| Bed 1 | assertion 161: `04L` | `RWY_ID=04L/22R, RWY_END_ID=04L` | `NULL` (unpromoted) | `4L/22R` | `4L` | `22R` | Massport (2026-08-06 press release, fetched directly): *"...one at Runway 22R..."* | No `PhysicalInstallationIdentity`; `SourceAssertion` 161 unreviewed |
| Bed 2 | assertion 162: `15R` | `RWY_ID=15R/33L, RWY_END_ID=15R` | `NULL` (unpromoted) | `15R/33L` | `15R` | `33L` | Massport: *"...and another at Runway 33L."* | No `PhysicalInstallationIdentity`; `SourceAssertion` 162 unreviewed |

**Not contradictory — confirmed with strong, direct evidence.** Massport's
own newsroom (Tier 1, fetched directly, published 2026-08-06) is the
authority for both the physical-bed-count claim ("two other EMAS
systems") *and* the public naming ("Runway 22R"/"Runway 33L"). NASR's
`04L`/`15R` and Massport's `22R`/`33L` are the two reciprocal ends of the
*same* two runway pairs — RWI's own topology confirms `22R` is exactly
the reciprocal of `4L` (both on `Runway` `4L/22R`), and `33L` is exactly
the reciprocal of `15R` (both on `Runway` `15R/33L`). No repository
evidence contradicts this interpretation, and independent corroboration
exists (§6): ORH's official contract language explicitly uses *both*
conventions side by side for the same physical asset, which would be an
unlikely coincidence if the BOS interpretation were wrong.

## 6. ORH case study

| EMAS | NASR raw value | Official contract language (MPA Contract W306, per the web research pilot) | Physical `RunwayEnd` | Protected/operational direction |
|---|---|---|---|---|
| Bed 1 | `RWY_ID=11/29, RWY_END_ID=11` | *"Replace Runway 29 Departure EMAS **(R/W 11 End)**"* | `11` | `29` |
| Bed 2 | `RWY_ID=11/29, RWY_END_ID=29` | *"Replace Runway 11 Departure EMAS **(R/W 29 End)**"* | `29` | `11` |

**ORH is the strongest available proof case for the nationwide model.**
Unlike BOS (where the two naming conventions appear in two different
documents), ORH's own official Massport procurement contract states
**both conventions in the same sentence**, explicitly parenthetical:
`"Runway 29 Departure EMAS (R/W 11 End)"` unambiguously means "the EMAS
that protects Runway 29's departure operations, physically located at the
R/W 11 end." This is direct, unambiguous, primary-source proof that the
dual-naming pattern is a real, established, official Massport convention
— not a RWI misreading, not a data error, and not specific to BOS.

This is corroborated within RWI's own already-ingested evidence:
ORH's USAspending Signal 46 (`source_notes`, already in the database)
reads *"...REPLACING THE ENGINEERED MATERIAL ARRESTING SYSTEM FOR RUNWAY
29 DEPARTURE END"* — grant-text language using the "departure end"
framing for the bed physically at end `11`.

## 7. MDW/CGF existing-link compatibility

All 6 real `PhysicalInstallationIdentity` rows (4 MDW, 2 CGF) and all 8
`InstallationAssertionLink` rows were inspected directly.

| Airport | `runway_end_id` designation | `InstallationAssertionLink.reason` (verbatim) |
|---|---|---|
| CGF | `06` | *"Cuyahoga County completion evidence and FAA NASR each explicitly identify CGF runway end 06 as EMAS evidence."* |
| CGF | `24` | *"...CGF runway end 24 as EMAS evidence."* |
| MDW | `04R` | *"FAA NASR 2026-08-06 explicitly reports EMAS at MDW runway end 04R; current-presence only, no historical continuity claim."* |
| MDW | `22L` | *"...MDW runway end 22L..."* |
| MDW | `13L` | *"...MDW runway end 13L..."* |
| MDW | `31R` | *"...MDW runway end 31R..."* |

**Verdict: fully consistent, no blocker.** Every single one of the 8
`reason` texts quotes the NASR `RWY_END_ID` value **unchanged, at face
value** — none apply a reciprocal reinterpretation. This is the *existing,
already-human-approved* precedent this analysis's semantic contract
(§9) formalizes, not a new invention: `PhysicalInstallationIdentity.runway_end_id`
has always meant physical location in every real row that exists today.
The BOS/ORH dual-naming finding does not conflict with this — it is a
*presentation* concern (§17), not a canonical-identity concern. Also
notable: MDW's reason texts already contain the exact "current-presence
only, no historical continuity claim" discipline this analysis's §13
independently arrives at — the existing human reviewer already applied
this caution manually.

## 8. Proposed canonical semantic contract

**Adopted (matches existing, already-approved practice exactly):**

> `PhysicalInstallationIdentity.runway_end_id` means ONLY the canonical
> PHYSICAL runway end at which the installation exists — exactly what an
> authoritative source's own runway-end field (e.g. NASR `RWY_END_ID`)
> reports, taken at face value, never reinterpreted toward a reciprocal/
> protected-direction reading.

**Protected operational direction: derive, do not store (choice A).**
Reciprocal derivation is 100% deterministic via topology (§4) with zero
known exceptions — there is no evidence anywhere in the governed dataset
that the relationship is non-deterministic, so storing it explicitly
would be redundant and a drift risk, not a safety improvement.

**`SourceAssertion.runway_end` should mean: the normalized PHYSICAL end**
— i.e., `normalize_end(raw_runway_end_value)`, the exact same
transformation already used to build every canonical `RunwayEnd.designation`
(`app/services/runway_identity.py::normalize_end`). This is a pure,
source-agnostic string normalization (not FK-linked, not requiring a
canonical-Runway match) and is what `_nasr_presence_view()` already
expects when it reads this field.

## 9. Assertion normalization finding

**Why `runway_end` was never written: deliberately deferred, matching an
architectural separation already documented before this task — not an
importer bug, not a missing normalization step in the sense of an
oversight, and not a safety policy invented here.**
`canonical-runway-runway-end-design.md` §4 already states the intended
split explicitly: *"EMAS presence evidence = `APT_ARS.csv`. Answers 'what
does the FAA currently report as equipped.' Governs `SourceAssertion`s
and, after review, `PhysicalInstallationIdentity` — never the runway/end
rows themselves."* `scripts/dry_run_nasr_apt_ars.py` (the nationwide
ingestion path, traced directly in this task) writes
`raw_runway_end_value` and stops there by design — normalization into
`runway_end` was reserved for a promotion step that was never built as a
repeatable mechanism (only the MDW-specific pilot script,
`apply_mdw_current_presence_pilot.py`, ever performed it, one airport at a
time).

**Is `NASR RWY_END_ID → canonical physical RunwayEnd designation`
semantically valid for these assertions?** Yes, for every assertion this
task classified `AUTO_RESOLVABLE`/`REVIEW_REQUIRED`/`ALREADY_LINKED` (112
of 115) — confirmed deterministic and topology-backed, per §4 and the
classifier's own logic (§10-11).

## 10. Classifier design and rules

Implemented as `scripts/analyze_nasr_emas_runway_end_resolution.py` —
read-only, no `--apply`, no `session.add()`/`commit()` anywhere, proven by
test (`len(session.new) == 0 and len(session.dirty) == 0` after
classification). Per-assertion classification, exact criteria:

- **`AUTO_RESOLVABLE`** — `(raw_runway_value, raw_runway_end_value)`
  normalizes and matches exactly one canonical `RunwayEnd` at this
  airport; no existing `PhysicalInstallationIdentity` at that end; no
  free-text evidence at this airport names the reciprocal end in an EMAS
  context.
- **`ALREADY_LINKED`** — a `PhysicalInstallationIdentity` already exists
  at the resolved physical `RunwayEnd` with a reviewed
  `SAME_PHYSICAL_INSTALLATION` link (regardless of which specific
  assertion id was originally linked — a later NASR cycle reporting the
  same physical end correctly reuses this class, not `AUTO_RESOLVABLE`).
- **`REVIEW_REQUIRED`** — mapping is deterministic, but another
  free-text source at this airport (`Installation.notes` or
  `Signal.source_notes`) contains an explicit **standalone** `"Runway
  {reciprocal designation}"` / `"bana {reciprocal designation}"` phrase in
  an EMAS/arresting-system context — i.e. real dual-naming/attribution
  evidence a human should see before promotion.
- **`AMBIGUOUS`** — the normalized end designation matches more than one
  candidate `RunwayEnd` at the airport.
- **`CONFLICT`** — an existing `PhysicalInstallationIdentity` at the
  resolved end has a recorded `DIFFERENT_PHYSICAL_INSTALLATION` decision
  and no superseding `SAME_PHYSICAL_INSTALLATION` decision.
- **`INSUFFICIENT_EVIDENCE`** — no `airport_id` at all, or no canonical
  `Runway`/`RunwayEnd` at this airport matches the raw values (fails
  closed rather than guessing).

No airport-specific rule exists anywhere in the classifier — every branch
is a generic, evidence-based check applied uniformly.

### A false-positive lesson worth recording

The `REVIEW_REQUIRED` heuristic went through two corrections during this
task, both caught by manually inspecting real output rather than trusting
the first pass:

1. **First draft**: a bare substring check on the reciprocal designation
   matched **83/115** assertions — almost all false positives, caused by
   a templated `Installation.notes` sentence ("...lists multiple
   EMAS-equipped ends here: `04L/22R/04L, 15R/33L/15R`.") that
   incidentally contains both ends' text as substrings of an unrelated
   pair-listing sentence.
2. **Second draft**: requiring an explicit `"Runway {end}"` phrase (word
   boundary) dropped this to 30/115, but a second templated pattern
   ("...lists EMAS at multiple ends of runway `10R/28L` (10R, 28L); exact
   end not recorded.") still matched, because `"runway 10R"` is a literal
   substring of `"runway 10R/28L"`.
3. **Final**: adding a negative lookahead excluding any match immediately
   followed by `/` (i.e. still part of an `"X/Y"` pair token, not a
   genuine standalone reference) brought this to a manually-verified
   **9/115**, each one spot-checked against its actual source text (§12).

## 11. Nationwide dry-run classification counts

| Classification | Count |
|---|---|
| `AUTO_RESOLVABLE` | **97** |
| `ALREADY_LINKED` | **9** |
| `REVIEW_REQUIRED` | **9** |
| `AMBIGUOUS` | **0** |
| `CONFLICT` | **0** |
| `INSUFFICIENT_EVIDENCE` | **0** |
| **Total** | **115** |

- Assertions total: 115. Airports total: 63.
- Unique physical `RunwayEnd`s implicated: **112**.
- Duplicate assertions for the same physical end: **3 pairs** — CGF end
  `06` (2 assertions), CGF end `24` (2 assertions), MDW end `4R` (2
  assertions). All 6 of these are already `ALREADY_LINKED`, consistent
  (6 distinct already-reviewed ends + 3 duplicate extra assertions for 3
  of them = 9 `ALREADY_LINKED`, exactly matching the table above).
- Airports with mixed classifications (i.e. some assertions in one class,
  others in a different class, at the *same* airport): **0** — every
  airport's assertions currently land in a single, consistent class.

## 12. Manually reviewed edge cases

All 9 `REVIEW_REQUIRED` assertions were read in full (not sampled):

| Airport | Evidence | Verdict |
|---|---|---|
| BOS (×2) | Massport press release naming `22R`/`33L` | Genuine — the motivating case |
| ORH (×2) | Own USAspending grant text: *"...FOR RUNWAY 29 DEPARTURE END"* | Genuine — matches the W306 contract pattern independently |
| BGM (×2) | Binghamton's own 2021 Airport Master Plan Update names *"Runway 16 EMAS"* for work whose grant text says *"...AT THE...RUNWAY 34 END"* | Genuine — a fourth independent real-world confirmation of the same naming pattern |
| LEX (×2) | Grant/installation notes for a runway with EMAS at **both** ends (4 and 22) each mention the other end by name | Plausible/cautious, not necessarily a naming conflict — LEX may simply have two separate, correctly-attributed beds; flagging for a human to confirm which grant funds which bed is the right conservative call |
| ELM (×1) | Note mentions a *runway extension* (unrelated construction) at the reciprocal end in the same sentence as the EMAS purchase | Borderline; kept `REVIEW_REQUIRED` rather than silently auto-resolved, per this task's explicit instruction not to weaken criteria to inflate the `AUTO_RESOLVABLE` count |

No `CONFLICT` or `AMBIGUOUS` cases exist in the real dataset (both counts
are 0); their code paths are covered only by synthetic test fixtures
(§ tests). MDW/CGF's 9 `ALREADY_LINKED` assertions were spot-checked and
confirmed to resolve to exactly the already-approved physical ends.

## 13. Assertion ≠ installation — what promotion may and may not establish

A NASR current-EMAS-presence assertion legitimately establishes **only**:
*as of this NASR cycle's effective date, the FAA reports an arresting
system present at this specific physical `RunwayEnd`.*

**Promotion of an `AUTO_RESOLVABLE` assertion may legitimately create/update:**
- `SourceAssertion.runway_end` (normalized physical end string) — pure
  mechanical normalization, no new claim beyond what the raw value already
  said.

**It must NOT, on its own, establish or imply:**
- Installation *year* (NASR presence evidence carries no install date).
- *Original vs. replacement* lifecycle state (a bed reported present in
  cycle N could be original or a replacement — NASR presence alone cannot
  distinguish these; see ORH's real 2024/2025 replacement, which current-
  presence evidence alone would not reveal happened).
- Manufacturer/vendor.
- Physical dimensions/bed size.
- Any claim beyond "present as of NASR cycle X" — this is exactly the
  discipline MDW's own existing `InstallationAssertionLink.reason` text
  already applies ("current-presence only, no historical continuity
  claim"), carried forward here as an explicit rule rather than an
  informal convention.

## 14. NASR temporal/cycle semantics recommendation

- **Cycle N → Cycle N+1, same physical end reported present**: should
  strengthen/continue the same current-presence evidence, not create a
  second physical installation record. Implemented in this classifier as
  `ALREADY_LINKED` detection keyed by physical `RunwayEnd`, not by
  assertion id — proven by test
  (`test_duplicate_cycle_assertions_for_the_same_end_are_each_classified_consistently`
  and the real MDW/CGF duplicate-cycle counts in §11).
- **Cycle N → Cycle N+1, presence disappears**: must NOT be silently
  treated as "installation removed" by any future automated writer. It
  may represent genuine removal, a replacement/reconstruction interval
  (exactly ORH's real 2024/2025 pattern — briefly absent from operational
  service during replacement, though NASR's own update cadence may or may
  not capture that gap), or a source/extraction issue. **Recommendation
  for the future promotion writer**: a disappearance should generate a
  flagged review item, never an automatic "removed" state change. Not
  implemented here — analysis only.

## 15. Public presentation implication (design only, not implemented)

Recommended: lead with the **operator/public-facing framing** (matches
how Massport/the airport itself communicates, avoids RWI appearing to
contradict operator terminology) with the **physical location** available
as expandable provenance:

```
Runway 22R — EMAS
  ⌄ Fysisk placering: banände 04L (FAA NASR-cykel 2026-08-06)
```

This keeps `PhysicalInstallationIdentity.runway_end_id`/`SourceAssertion.runway_end`
storing only the physical value (§9), while presentation derives the
protected-direction label via the reciprocal-topology lookup (§4) purely
at render time — no schema change, no dual storage. **Not implemented in
this task** — design recommendation only.

## 16. Schema-change verdict

### NO_SCHEMA_CHANGE_NEEDED

Physical location remains the sole canonical field
(`PhysicalInstallationIdentity.runway_end_id`, unchanged meaning);
protected direction is 100%-deterministically derived via existing
`Runway`↔`RunwayEnd` topology (§4); `SourceAssertion.runway_end` needs
only its already-designed normalized-physical-string meaning, not a new
column. No new "role" or "reference-frame" field is needed anywhere.

## 17. Future promotion write contract (design only, not executed)

For an `AUTO_RESOLVABLE` assertion, the **narrowest correct write set**:

- **Write**: `SourceAssertion.runway_end = normalize_end(raw_runway_end_value)`.
  Nothing else.
- **Do NOT write**: `PhysicalInstallationIdentity` (stays human-gated, per
  `physical_installation_reconciliation.py`'s own stated design — "no
  matching or automatic reconciliation"), `InstallationAssertionLink`, or
  any `Installation` field.

**Preconditions (fail closed on drift, matching every existing correction
script's pattern in this repository)**:
1. Re-run this classifier immediately before writing; abort the entire
   batch if the assertion's classification has changed from
   `AUTO_RESOLVABLE` since the plan was reviewed.
2. `SourceAssertion.runway_end` must still be `NULL` (idempotency — a
   second run against an already-promoted row is a no-op, not an error,
   matching the batch-apply scripts' established convention).
3. The resolved canonical `RunwayEnd` must still belong to the same
   airport and the same normalized pair.
4. Never touch `ALREADY_LINKED`, `REVIEW_REQUIRED`, `AMBIGUOUS`,
   `CONFLICT`, or `INSUFFICIENT_EVIDENCE` rows in this same writer — each
   of those needs a distinct, separately-approved handling path (or none
   at all, for `REVIEW_REQUIRED`/`CONFLICT`/`AMBIGUOUS`, which stay
   human-gated).
5. Timestamped backup before any write; post-write `PRAGMA
   foreign_key_check`; idempotent on rerun (§ established convention from
   every prior correction script in this repository).

## 18. Publication consequence

**Yes — promoting `SourceAssertion.runway_end` alone is sufficient for
`EMAS idag` to publish current presence, for the `nasr_presence` pathway
specifically.** Read directly from `app/static_export/build.py::_airport_view()`:
`nasr_presence` is built from `SourceAssertion` rows where
`assertion_type == "runway_end"` **and `assertion.runway_end` is
truthy** — nothing else is required. It does not check for a
`PhysicalInstallationIdentity`, a `RunwayEnd` FK, or any reviewed link.
This means the narrow write contract in §17 — a single string field,
zero human reconciliation — is *exactly* the missing piece keeping this
pathway empty everywhere except never (currently 0 airports use it; all 6
existing pills come from the separate `reviewed_identities` pathway,
which remains fully human-gated and untouched by this proposal).

## 19. Product impact estimate

- **97 assertions could safely move forward** under the narrow §17
  contract without any human reconciliation step.
- Unique physical ends implicated by `AUTO_RESOLVABLE` assertions: up to
  ~91 (112 total implicated minus the 9 `ALREADY_LINKED`/9
  `REVIEW_REQUIRED`, allowing for the 3 duplicate pairs already counted
  within `ALREADY_LINKED`).
- **Airports that could gain a populated "EMAS idag" section**: every
  airport contributing at least one `AUTO_RESOLVABLE` assertion — the
  large majority of the 63 airports carrying NASR presence evidence (all
  except the 4 airports — BOS, ORH, BGM, LEX — whose *only* current
  assertions this cycle happen to be exactly the ones flagged
  `REVIEW_REQUIRED`, plus ELM's single flagged assertion; every other
  airport among the 63 has at least one clean `AUTO_RESOLVABLE`
  assertion). Exact per-airport gain was not separately tabulated in this
  task (out of scope: this is analysis, not the promotion writer itself).
- **9 assertions require manual research/review** before promotion
  (`REVIEW_REQUIRED`) — a small, well-characterized set (§12), each with
  a documented reason.
- **0 are blocked by real ambiguity/conflict** in the current real
  dataset (`AMBIGUOUS`=0, `CONFLICT`=0) — both code paths exist and are
  tested, but no real assertion currently falls into either.

Promotion of `AUTO_RESOLVABLE` assertions closes the public-page gap
directly for the `nasr_presence` half of "EMAS idag" (§18) — it does
**not** advance the separate, still fully human-gated
`reviewed_identities` pathway, which remains scoped to MDW/CGF only,
unchanged by anything in this analysis.

## 20. Blockers

**None found for the `AUTO_RESOLVABLE` majority.** The only real
constraint identified is definitional, not technical: RWI needed an
explicit semantic contract for what `runway_end`/`runway_end_id` mean
before promoting anything — that gap is what this task closes. No schema
migration, no MDW/CGF incompatibility, and no nationwide data-quality
blocker was found.

## 21. Recommended next slice

1. **Implement the §17 promotion writer** (`SourceAssertion.runway_end`
   normalization only, for `AUTO_RESOLVABLE` assertions), following the
   existing fail-closed, backup-first, idempotent, dry-run-by-default
   pattern used by every prior correction/apply script in this
   repository. Expected direct result: "EMAS idag" becomes populated for
   the majority of the 63 airports carrying NASR presence evidence,
   including a `04L`/`15R` pill for BOS's physical location today (with
   the presentation-layer reciprocal-label design in §15 as a natural,
   separate follow-up).
2. Separately, evidence-backed human reconciliation (the existing
   MDW/CGF pilot pattern) for the 9 `REVIEW_REQUIRED` assertions,
   starting with BOS (the best-evidenced case, per §5) and ORH (the
   strongest proof case, per §6).
3. The §15 public-presentation design (protected-direction label with
   expandable physical-location provenance) as a UI follow-up once §17 is
   live.

## Tests

`tests/test_analyze_nasr_emas_runway_end_resolution.py` — 13 tests,
covering: deterministic physical-end mapping, reciprocal derivation via
topology (not designation arithmetic), zero DB mutation (both
`classify_all()` and `run()`), `ALREADY_LINKED` detection, duplicate-cycle
classification consistency, `AMBIGUOUS` (synthetic fixture — no real case
exists), `CONFLICT` (synthetic fixture — no real case exists),
`INSUFFICIENT_EVIDENCE` (both no-airport and no-canonical-runway shapes),
a BOS-shaped dual-naming fixture, an ORH-shaped symmetric-no-flag
baseline, and an MDW/CGF-shaped existing-link compatibility fixture.

Full suite: **567 passed** (554 baseline + 13 new). `git diff --check`:
exit 0. `python -m py_compile`: clean.
