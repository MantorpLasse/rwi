# Canonical Runway Inventory — Database-Wide U.S. Dry Run

**Read-only. No database write occurred.** Everything below comes from a
fresh read-only ORM session (`SELECT` only — no `session.add()` /
`session.commit()` anywhere in the analysis) plus the existing, unmodified
`app.services.runway_inventory.plan_airport_inventory` planner, run against
every deterministically-matched U.S. airport in the development database.
The real database's file size and last-write timestamp are unchanged
before and after this task (`643072` bytes, confirmed identical). No
network access occurred anywhere — the NASR reader
(`app/evidence/nasr_apt_rwy.py`) only ever opens the local, already
SHA-256-verified archive.

## 1. Executive summary

Of the database's 76 U.S. airports, **63 (83%)** resolve deterministically
against the preserved FAA NASR 2026-08-06 extract with **zero ambiguity**:
2 are already fully populated (the MDW/CGF pilot), 48 would be safely
enriched with new runways/ends alongside their existing seed row, and 13
would be created from scratch. **12 airports (16%)** are currently blocked
— not because of any runway-designation ambiguity, but because each one
has exactly one *non-runway* NASR record (a helipad `H1`/`H2`/`H3`, or an
unpaired special-use strip like `00X`/`10X`/`19X`) mixed into its
`APT_RWY.csv` rows. The existing planner correctly fails closed on the
whole airport rather than guessing, per its "never guess" design. One
airport (`Allegheny County Airport Authority`) has no FAA/IATA/ICAO
identifier at all and cannot be matched by any means. No cross-airport
NASR ARPT_ID collisions, no duplicate-row hazards, and no legacy
Runway-designation mismatches were found anywhere in the U.S. scope. All 6
existing `PhysicalInstallationIdentity` rows are already linked (by the
MDW/CGF pilot); there is currently no unlinked-identity backlog to assess
for future readiness. The 10 non-U.S. airports are out of scope and
untouched, as instructed.

**Recommendation:** split into a clean deterministic batch (63 airports)
and a review/exception queue (12 airports + 1 no-identifier airport) —
see §12.

## 2. U.S. airport scope

| | Count |
|---|---|
| Total airports in DB | **86** |
| U.S. airports (`country = "USA"`) | **76** |
| Non-U.S. airports | **10** |
| U.S. airports with no FAA/IATA/ICAO identifier | **1** |
| U.S. airports with an identifier but no matching NASR `ARPT_ID` row | **0** |
| U.S. airports deterministically matched to NASR | **75** |

Matching reused the existing, already-proven logic exactly as written in
`scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py` /
`scripts/dry_run_canonical_runway_inventory.py`: an airport's candidate
code set is `{faa_code, iata_code, icao_code} - {None}`, and any NASR row
whose `ARPT_ID` is in that set belongs to that airport. No new matching
rule was introduced.

Not every U.S.-country row is automatically eligible, per the task's
caution: **Allegheny County Airport Authority** (id 75) has `faa_code`,
`iata_code`, and `icao_code` all `NULL` — it is a parent authority record
with no NASR-matchable identifier, and is excluded, not silently skipped.
Zero airports fell into "has an identifier, but that identifier doesn't
appear anywhere in NASR" — every identifier present in the DB does resolve
to at least one real NASR record.

No cross-airport `ARPT_ID` collisions were found (no two DB airports
share a candidate code that both resolve into NASR).

## 3. Current DB runway coverage (U.S. scope)

| | Count |
|---|---|
| Existing `Runway` rows (U.S. airports) | **62** |
| Existing `RunwayEnd` rows (U.S. airports) | **10** |
| U.S. airports with zero `Runway` rows | **13** |
| U.S. airports with exactly one `Runway` row | **61** |
| U.S. airports with multiple `Runway` rows | **1** (MDW, id 12, already fully populated by the pilot: 4 runways) |
| Airports already fully populated by the MDW/CGF pilot | **2** (MDW: 4 runways / 8 ends; CGF: 1 runway / 2 ends) |

All 10 existing `RunwayEnd` rows belong to MDW (8) and CGF (2) — no other
airport has any canonical `RunwayEnd` data yet, consistent with the pilot
being the only apply so far. The single legacy `Runway` row seeded per
airport (61 airports) is a placeholder/summary row, not an exhaustive
inventory — confirmed below, since NASR reports more physical runways
than exist as DB rows at the great majority of these airports.

## 4. NASR matching coverage

- NASR `APT_RWY.csv` rows read (whole archive, all countries): **23,196**
- NASR `APT_RWY_END.csv` rows read (whole archive, all countries): **39,871**
- Rows actually used (filtered to the 75 matched U.S. airports' candidate codes): a small subset of the above — every other row in the archive belongs to an airport not in RWI's DB and was never touched by any classification.

## 5. Database-wide dry-run totals

Aggregated across all 75 matched U.S. airports (read-only `plan_airport_inventory` calls only — `apply_plan()` was never imported or called):

| | Count |
|---|---|
| Airports processed (matched) | **75** |
| Airports matched cleanly (`ALREADY_COMPLETE` + `CLEAN_ENRICH` + `CLEAN_CREATE`) | **63** |
| Airports unresolved (no NASR match at all) | **0** |
| Airports ambiguous/conflicting (blocked entirely) | **12** (all `AMBIGUOUS`; **0** `CONFLICT`) |
| `Runway` rows that would be **created** | **94** |
| `Runway` rows that would be **enriched** (existing row, filling `NULL` length/width/surface) | **39** |
| `Runway` rows already matched, no change needed | **14** |
| `RunwayEnd` rows that would be **created** | **284** |
| Existing `Runway` rows reused (matched, not duplicated) | **53** |
| Existing `RunwayEnd` rows reused (matched, not duplicated) | **10** (all MDW/CGF) |

## 6. Airport classification breakdown

Classification is a **report-only heuristic** for this dry run — it is
not encoded in the domain model or the planner. Definitions used:

- **ALREADY_COMPLETE** — 0 creates, 0 enrichments, 0 end-creates; airport already fully populated.
- **CLEAN_ENRICH** — airport has ≥1 existing `Runway` row, plan resolves with no ambiguity, and every existing row is accounted for in the plan (no orphaned legacy rows).
- **CLEAN_CREATE** — airport currently has zero `Runway` rows; plan resolves with no ambiguity.
- **PARTIAL_MATCH** — plan resolves, but ≥1 existing `Runway` row doesn't correspond to any NASR-planned pair for that airport (legacy/decommissioned data). *(0 airports hit this — see §8.)*
- **AMBIGUOUS** — `plan_airport_inventory` raised `AmbiguousRunwayDesignationError` from a malformed/non-pair source row.
- **CONFLICT** — either a genuine RWY/RWY_END disagreement (`AmbiguousRunwayDesignationError` with "do not match the pair designation"), a duplicate normalized pair within one airport's own NASR rows, or a cross-airport `ARPT_ID` collision. *(0 airports hit this.)*
- **UNRESOLVED** — no candidate identifier, or no NASR row matches any candidate identifier.

| Classification | Airports | Runway creates | Runway enrich | RunwayEnd creates |
|---|---|---|---|---|
| `ALREADY_COMPLETE` | 2 | 0 | 0 | 0 |
| `CLEAN_ENRICH` | 48 | 63 | 39 | 222 |
| `CLEAN_CREATE` | 13 | 31 | 0 | 62 |
| `AMBIGUOUS` | 12 | 0 (blocked) | 0 (blocked) | 0 (blocked) |
| `PARTIAL_MATCH` / `CONFLICT` / `UNRESOLVED` | 0 | — | — | — |
| **Total (matches §5)** | **75** | **94** | **39** | **284** |

The 48 `CLEAN_ENRICH` airports are overwhelmingly the same shape: one
seeded placeholder `Runway` row already in the DB, matched by normalized
designation to one of NASR's real runway pairs at that airport, enriched
with real length/width/surface, plus the airport's *other* physical
runways (which have no DB row yet) created alongside it.

## 7. Top exceptions / blockers

All 12 `AMBIGUOUS` airports are blocked by the **same root cause**: FAA
NASR's `APT_RWY.csv` includes non-runway physical facilities at the same
`ARPT_ID` — helipads (`RWY_ID` values like `H1`, `H2`, `H3`) and unpaired
special-use strips (`00X`, `10X`, `19X`) — which have no `/` in their
`RWY_ID` and therefore cannot satisfy `normalize_pair()`'s "exactly two
ends" requirement. `plan_airport_inventory()` raises on the *first* bad
row it encounters for an airport and aborts that airport's entire plan —
correct, fail-closed behavior per its own docstring, but it means one
unrelated helipad record currently blocks otherwise-clean runway data at
major airports:

| Airport | Blocking NASR row(s) | Real runway pairs otherwise present |
|---|---|---|
| Aspen/Pitkin County (ASE) | `00X` | 1 |
| Greater Binghamton (BGM) | `H1` | 2 |
| McClellan-Palomar (CRQ) | `H1` | 1 |
| Groton-New London (GON) | `H1` | 2 |
| DeKalb/Peachtree (PDK) | `H1` | 3 |
| Chicago O'Hare (ORD) | `10X`, `H1` | 8 |
| Newark Liberty (EWR) | `H1` | 3 |
| Trenton-Mercer (TTN) | `H1`, `H2`, `H3` | 2 |
| LaGuardia (LGA) | `H1` | 2 |
| Republic (FRG) | `H1`, `H2` | 2 |
| Greenville Downtown (GMU) | `H1`, `H2` | 2 |
| Reagan National (DCA) | `19X`, or `RWY_ID` without `/` | 3 |

**Diagnostic-only check** (no code changed, no rule added): re-running the
existing, unmodified planner for these 12 airports with only the
non-`/` NASR rows filtered out beforehand confirms every one of them
resolves **cleanly** — combined, that would be **+22 Runway creates, +6
Runway enrich, +62 RunwayEnd creates** across the 12. This is reported as
context for sizing a future fix, not as part of this dry run's clean
batch, and **no exclusion rule was implemented**, per instruction.

This non-paired-`RWY_ID` pattern is not unique to RWI's 12 airports — the
full NASR archive contains **6,521** such rows nationwide (28% of all
`APT_RWY.csv` records, across 5,927 distinct `ARPT_ID`s worldwide),
confirming it's a systematic, well-known FAA data shape (helipads and
non-standard strips coexisting with paved runways in the same file), not
a one-off data-quality defect.

**One airport is unmatchable by identifier, not by data:** Allegheny
County Airport Authority (id 75) has no `faa_code`/`iata_code`/`icao_code`
at all, so it cannot be resolved against NASR regardless of the archive's
contents. This is a data-completeness gap in RWI's own Airport row, not a
NASR/planner issue.

**Unrelated observation (not evaluated further, out of this task's
scope):** airport id 33 in the DB, coded to resolve cleanly against a real
NASR `ARPT_ID`, is named "President Donald J. Trump International" — not
a real FAA-registered airport name. This suggests a seed/test-data label
override on a real airport row. Flagging only because a real apply would
attach genuine NASR runway data under that name; recommend a short,
separate data-quality check before this airport is included in any real
apply, not addressed here.

## 8. Normalization findings

| Pattern | Occurrences |
|---|---|
| Leading-zero differences (e.g. `06` → `6`) | **72** end tokens, across the 63 cleanly-planned airports |
| Reciprocal runway-pair ordering (e.g. `22L/04R` vs `04R/22L`) | **0** — NASR's own `RWY_ID` ordering already matches the planner's ascending-heading canonical order in every case observed |
| Existing legacy `Runway.designation` values with no NASR counterpart | **0** — every existing `Runway` row in the 63 clean airports matched a real NASR pair; no orphaned/decommissioned legacy designations found |
| Dimensions/surface enrichment opportunities | **39** existing `Runway` rows would be enriched (`NULL` length/width/surface filled from NASR) |
| Duplicate-looking rows (two NASR rows for one airport normalizing to the same pair) | **0** — no duplicate-designation hazard found anywhere in the U.S. scope |
| Non-runway `RWY_ID` rows blocking a whole airport (helipads / unpaired strips) | **12 airports** — see §7; this is the one **unsupported pattern** found. No new normalization rule was written to handle it, per instruction; it is reported here as a blocker/exception for a future, separately-scoped decision (e.g. "skip non-`/` `RWY_ID` rows instead of aborting the airport" — a deliberate, reviewable planner change, not something to slip in during a dry run). |

## 9. MDW/CGF idempotency confirmation

Both pilot airports were re-planned against the same preserved NASR
archive and produced **exactly** the expected no-op result, confirming
the earlier applied pilot remains idempotent:

| Airport | Runways create | Runways enrich | RunwayEnds create | Existing runways matched | Existing ends matched |
|---|---|---|---|---|---|
| MDW (id 12) | **0** | **0** | **0** | 4 | 8 |
| CGF (id 57) | **0** | **0** | **0** | 1 | 2 |

Both match the task's expected values exactly (0/0/0 for both).

## 10. Physical identity link-readiness

Read-only inspection of all `PhysicalInstallationIdentity` rows (no
linking performed, no semantics broadened beyond the existing
`evaluate_identity_links_from_raw` matching rule):

| | Count |
|---|---|
| Identities already linked (`runway_end_id` set) | **6** |
| Unlinked identities | **0** |
| — with a deterministic future `RunwayEnd` match | 0 |
| — unresolved | 0 |
| — ambiguous | 0 |

All 6 existing `PhysicalInstallationIdentity` rows in the database were
already linked by the MDW/CGF pilot's separate, human-approved linking
step. There is currently no unlinked-identity backlog anywhere in the
database to assess readiness for — this section is a **confirmed zero**,
not a skipped check. This will need to be re-run once new identities
exist at newly-populated airports.

## 11. Non-U.S. airports excluded

Explicitly out of scope for FAA NASR backfill and not touched by any
planning call:

| Country | Airports |
|---|---|
| Brazil | Congonhas Airport, Santos Dumont Airport |
| New Zealand | Queenstown Airport, Wellington International Airport |
| Switzerland | Zurich International Airport |
| France | Roland Garros Airport, Dzaoudzi Pamandzi International Airport |
| Germany | Saarbrücken Airport |
| Japan | Tokyo Haneda International Airport |
| United Kingdom | RAF Northolt |

**10 airports total.** The canonical `Runway`/`RunwayEnd` model itself
remains source-agnostic (per
`docs/domain/canonical-runway-runway-end-design.md` §5/§11) — this dry
run is simply U.S.-only because FAA NASR is a U.S.-only source.

## 12. Recommended apply strategy

**Option B — split into a clean deterministic batch and a review/exception queue.**
Per instruction, B is preferred whenever any ambiguity/conflict exists,
and 12 of 75 matched airports (16%) currently do:

- **Batch 1 (clean, deterministic, no review needed):** the 63
  `ALREADY_COMPLETE` + `CLEAN_ENRICH` + `CLEAN_CREATE` airports — 94
  Runway creates, 39 Runway enrichments, 284 RunwayEnd creates, zero
  ambiguity, zero conflicts, zero duplicate hazards, zero orphaned legacy
  rows.
- **Exception queue (12 + 1 = 13 airports, not applied without a separate decision):**
  the 12 `AMBIGUOUS` airports blocked solely by non-runway NASR records
  (helipads/unpaired strips), plus Allegheny County Airport Authority
  (no identifier at all). None of these require manual per-airport
  *research* — the underlying data and the diagnostic in §7 already show
  exactly what's blocking each one and that the real runway data behind
  the block is clean. What's needed is one **narrow, reviewable planner
  decision** (whether/how to skip non-`/` `RWY_ID` rows instead of
  aborting the whole airport) — not manual airport-by-airport
  investigation.

## 13. Tests and safety verification

- Focused canonical-runway/inventory tests (9 files): **73 passed**
- Full suite: **418 passed**
- Python compilation of all milestone modules: clean
- Static-export/public-boundary regression (`tests/test_static_export.py`, incl. the "Banor" suppression and `runway_end_id`/`RunwayEnd` leak-safety tests): **14 passed**
- `git diff --check`: exit 0, no whitespace errors
- Working tree: clean aside from the same pre-existing, already-categorized untracked research/screenshot files from the prior milestone task — nothing new staged or modified by this dry run
- **Database mtime/size unchanged**: `643072` bytes, identical before and after (confirmed by direct comparison, not assumption) — no write occurred
- **No backup required** — no write occurred, so the backup discipline used by the apply scripts (`backup_database()`) was correctly never invoked
- **No network access** — `app/evidence/nasr_apt_rwy.py` opens only the local, pre-verified `data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`; no HTTP/socket code exists anywhere in the read path exercised by this dry run

## 14. Exact next implementation/apply recommendation

**Smallest next step:** apply Batch 1 only — a real, backed-up
`--apply` run of
`scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py`-style logic
widened from `TARGET_CODES = ("MDW", "CGF")` to the 63 clean airport
codes identified in §6, using the exact same dry-run-first, backup-first,
scoped-`TARGET_CODES` discipline already proven safe by the pilot. This
requires no new normalization rule, no planner change, and no identity
linking (identity linking is separately already saturated per §10). The
12-airport exception queue and the no-identifier airport are explicitly
**not** part of that step and should wait for a separate, deliberately
scoped decision on the non-`/` `RWY_ID` handling.

---

## Explicit answers

**A. How many U.S. airports can be populated deterministically?**
**63** (2 already complete + 48 clean-enrich + 13 clean-create), out of 75 matched / 76 total U.S. airports.

**B. How many require review?**
**13** — the 12 `AMBIGUOUS` airports blocked by non-runway NASR records (helipads/unpaired strips), plus 1 airport with no FAA/IATA/ICAO identifier at all.

**C. Total Runway rows that would be created?**
**94**

**D. Total Runway rows that would be enriched?**
**39**

**E. Total RunwayEnd rows that would be created?**
**284**

**F. Can the safe subset be applied in one batch?**
**Yes** — the 63-airport clean subset has zero ambiguity, zero conflicts, zero duplicate-row hazards, and zero orphaned legacy rows; it can be applied as one deterministic batch using the pilot's exact backup-first, dry-run-first discipline, scoped to those 63 codes.

**G. What is the smallest next step?**
Widen the existing, already-proven pilot apply script's `TARGET_CODES` from `("MDW", "CGF")` to the 63 clean airport codes and run it dry-run-first, backup-first, exactly as before — no new code, no new normalization rule, no identity-linking work needed.

---

No database write. No commit. No push. No deployment. Public "Banor" runway inventory remains suppressed.
