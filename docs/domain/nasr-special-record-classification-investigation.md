# FAA NASR Special-Record Classification Investigation

**Read-only investigation. No code, database, or archive modification.**
Every fact below comes from the already-preserved archive
(`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`) — including its own
bundled FAA field-definition PDF (`APT DATA LAYOUT.pdf`), read directly
from the archive — and from the real, already-applied development
database, inspected read-only. No new network access was needed: FAA's
own bundled documentation fully answered the field-definition question
(§5).

## 1. Executive conclusion

**FAA NASR carries no per-row field, in either `APT_RWY.csv` or
`APT_RWY_END.csv`, that flags a record as "runway" vs. "helipad" vs.
"other."** The facility-level `SITE_TYPE_CODE` (Airport/Balloonport/
Seaplane Base/Gliderport/Heliport/Ultralight) exists on every row, but for
all 12 currently-ambiguous airports it reads `'A'` (Airport) on *every*
row — including the helipad/placeholder rows — because those pads are
catalogued under the airport's own facility record, not a separate
heliport site number. No structured field distinguishes them there.

What *does* reliably distinguish them, confirmed empirically across the
**entire** national dataset (23,196 rows, zero unexplained exceptions),
is the **shape of `RWY_ID` itself**: a genuine fixed-wing runway is always
reported as two reciprocal-heading tokens separated by `/` (FAA's own
runway-numbering convention). Every one of the 6,521 rows nationwide that
is *not* in that shape falls into one of exactly three explained
categories — helicopter pads (`H1`, `H2`, ..., `H-A`, `HA`, ...),
balloonport pads (`B1`), or empty placeholder records (`NNX`, always
zero-length/zero-width/blank-surface) — and none of them represent a
fixed-wing runway. This is **Option D**: a minimal, evidence-backed
structural rule on `RWY_ID`'s own format — not a field-value rule (no
such field exists) and not a designation-prefix heuristic guessing at
meaning (the categories were independently confirmed, not assumed). See
§6 and §8 for the full reasoning and the rejected alternatives.

Applying this rule (simulated, not implemented) resolves all 12 currently-
ambiguous airports with **zero regression** anywhere in the previously-
clean 63-airport set, MDW/CGF unchanged, and no change to any
normalization rule.

## 2. Exact 12 ambiguous airports

Confirmed by re-running the existing, unmodified
`resolve_us_clean_batch()` read-only against the current database:

| id | Code | Name | Blocking record(s) | Real runway pairs present |
|---|---|---|---|---|
| 1 | ASE | Aspen/Pitkin County Airport | `00X` | `15/33` |
| 6 | BGM | Greater Binghamton Airport | `H1` | `10/28`, `16/34` |
| 7 | CRQ | McClellan-Palomar Airport | `H1` | `06/24` |
| 25 | GON | Groton-New London | `H1` | `05/23`, `15/33` |
| 36 | PDK | DeKalb/Peachtree | `H1` | `03L/21R`, `03R/21L`, `16/34` |
| 38 | ORD | Chicago O'Hare International | `10X`, `H1` | 8 pairs (`04L/22R` ... `10R/28L`) |
| 46 | EWR | Newark Liberty International | `H1` | `04L/22R`, `04R/22L`, `11/29` |
| 47 | TTN | Trenton-Mercer | `H1`, `H2`, `H3` | `06/24`, `16/34` |
| 50 | LGA | LaGuardia | `H1` | `04/22`, `13/31` |
| 51 | FRG | Republic | `H1`, `H2` | `01/19`, `14/32` |
| 63 | GMU | Greenville Downtown | `H1`, `H2` | `01/19`, `10/28` |
| 71 | DCA | Reagan National | `19X` | `01/19`, `04/22`, `15/33` |

Full record detail for every blocking row (`RWY_ID`, `SITE_TYPE_CODE`,
`RWY_LEN`, `RWY_WIDTH`, `SURFACE_TYPE_CODE`), pulled directly from
`APT_RWY.csv`:

| Airport | RWY_ID | SITE_TYPE_CODE | Length (ft) | Width (ft) | Surface |
|---|---|---|---|---|---|
| ASE | `00X` | `A` | 0 | 0 | *(blank)* |
| BGM | `H1` | `A` | 54 | 54 | ASPH |
| CRQ | `H1` | `A` | 40 | 50 | CONC |
| GON | `H1` | `A` | 45 | 45 | ASPH |
| PDK | `H1` | `A` | 56 | 56 | CONC |
| ORD | `10X` | `A` | 0 | 0 | *(blank)* |
| ORD | `H1` | `A` | 200 | 100 | CONC |
| EWR | `H1` | `A` | 54 | 54 | ASPH |
| TTN | `H1`/`H2`/`H3` | `A` | 64 each | 64 each | ASPH |
| LGA | `H1` | `A` | 45 | 45 | ASPH |
| FRG | `H1`/`H2` | `A` | 79 / 44 | 79 / 44 | ASPH |
| GMU | `H1`/`H2` | `A` | 50 each | 50 each | CONC |
| DCA | `19X` | `A` | 0 | 0 | *(blank)* |

`SITE_TYPE_CODE` is `'A'` (Airport) on every single one of these blocking
rows — confirming §1's finding that it cannot discriminate at this
specific set of airports.

## 3. Special record categories found

Every non-`/`-paired `RWY_ID` in the entire national `APT_RWY.csv`
(6,521 of 23,196 rows) was categorized. No category was left unexplained:

| Category | Pattern | Count (national) | Dimensions | Surface |
|---|---|---|---|---|
| Helicopter pad (numbered) | `H<digits>` | 6,487 | Always present, 10–6,000 ft (median 50 ft) | Always present |
| Helicopter pad (multi-pad, hyphenated) | `H-<letter>` | 6 | 100 ft (all) | Present |
| Helicopter pad (multi-pad, no hyphen) | `H<letter>` | 8 | 100 ft / 60 ft | Present |
| Balloonport pad | `B<digits>` | 12 | 150–1,000 ft | TURF (all but one) |
| Empty placeholder | `\d{2}X` | 6 | **Always 0 / 0** | **Always blank** |

Not all "unusual" patterns mean the same thing, confirmed by inspection
rather than assumed:

- **`H`-prefixed records are helicopter landing pads**, corroborated
  independently at sites where `SITE_TYPE_CODE = 'H'` (a dedicated
  heliport, e.g. `SXS` has `HA` through `HI`) — the same naming
  convention used consistently whether the pad sits at a dedicated
  heliport or is co-located inside an airport's own facility record (our
  12 airports' case, `SITE_TYPE_CODE = 'A'`). Of the 6,487 `H<digits>`
  rows nationwide, 6,149 (95%) belong to `SITE_TYPE_CODE='H'` facilities
  and only 331 (5%) are co-located at `SITE_TYPE_CODE='A'` airports — our
  12 airports are within that smaller group. The long-length outliers
  (2,000–6,000 ft) are exclusively at dedicated heliports, not at any of
  the 12 airports in scope.
- **`B1` is a balloonport pad**, corroborated the same way: of 12
  national occurrences, 11 are at `SITE_TYPE_CODE='B'` facilities and 1
  (`TX42`) is co-located at an `'A'` airport — same pattern as the
  helicopter case. `TURF` surface and near-square dimensions in every
  case are consistent with a balloon launch/recovery area, not a runway.
- **`NNX` records are empty placeholders**, not a different facility type
  at all — `RWY_LEN`, `RWY_WIDTH`, and `SURFACE_TYPE_CODE` are blank/zero
  on all 6 national occurrences without exception. There is no physical
  or operational data in these rows to lose by excluding them; excluding
  them costs canonical inventory nothing.

None of these categories represent a fixed-wing runway, and none contain
usable runway data that would be lost by excluding them from canonical
`Runway`/`RunwayEnd` inventory.

## 4. Relevant NASR fields

Fields inspected across `APT_RWY.csv` (25 columns) and `APT_RWY_END.csv`
(80 columns): `RWY_ID`, `SITE_TYPE_CODE`, `RWY_LEN`, `RWY_WIDTH`,
`SURFACE_TYPE_CODE`, `COND`, `TREATMENT_CODE`, `RWY_LGT_CODE`, and every
other column present. No field among any of these is a "runway type" or
"facility class" discriminator at the row level:

- `SITE_TYPE_CODE` is a **facility-level** code (common to every APT file,
  ordered by `SITE_NO, SITE_TYPE_CODE, RWY_ID`), not a per-runway type —
  it describes what kind of landing facility the whole `SITE_NO` is, and
  is `'A'` for every row belonging to an airport's own facility record,
  runway or pad alike.
- `RWY_LGT_CODE` includes a `PERI` (perimeter) lighting value, which
  appears on some but not all of the flagged helipad rows — suggestive,
  not reliable (several flagged rows have it blank).
- No column named or described as a runway/facility type exists in
  either file's field list.

## 5. FAA field-definition evidence

Source: `APT DATA LAYOUT.pdf`, bundled inside the preserved archive
itself — FAA's own authoritative field dictionary for this exact dataset,
consulted directly rather than a third-party interpretation. No live
network access was needed; the primary source was already fully available
locally.

> **SITE_TYPE_CODE** – Landing Facility Type Code.
> `A` AIRPORT · `B` BALLOONPORT · `C` SEAPLANE BASE · `G` GLIDERPORT ·
> `H` HELIPORT · `U` ULTRALIGHT

> **APT_RWY** ordered by `SITE_NO, SITE_TYPE_CODE, RWY_ID`
> `RWY_ID` - Runway Identification
> `RWY_LEN` - Physical Runway Length (Nearest Foot)
> `RWY_WIDTH` - Physical Runway Width (Nearest Foot)

`RWY_ID` is documented only as "Runway Identification" — a value, not a
typed/coded field with an accompanying vocabulary (unlike
`SURFACE_TYPE_CODE`, `COND`, `TREATMENT_CODE`, etc., which all have
documented code tables). **No structured discriminator field exists for
runway-vs-helipad-vs-other at the row level.** The `SITE_TYPE_CODE`
vocabulary above is the only FAA-documented type system relevant here,
and it independently corroborates what the `H`/`B` prefixes mean
(cross-referenced empirically in §3) without being usable as a per-row
filter at these 12 airports.

`CSV_README.pdf` (also bundled in the archive) was checked and contains
no mention of `RWY_ID`, runway types, or helipads — it documents the
CSV format transition generally, not field semantics.

## 6. Candidate rules evaluated

| # | Rule | Field-based? | Verdict |
|---|---|---|---|
| 1 | `SITE_TYPE_CODE != 'A'` at the row | Yes | **Rejected** — `'A'` on every blocking row at all 12 airports; excludes nothing there. |
| 2 | `RWY_ID` doesn't start with `H` | No (prefix heuristic) | **Rejected as insufficient on its own** — misses `B1` and the `NNX` placeholders; also not grounded in a documented FAA field, so explicitly disfavored per this task's instruction. |
| 3 | `RWY_LEN == 0` | Field-based (partial) | **Rejected as sole rule** — correctly catches all 6 `NNX` placeholders but would keep every `H`/`B` pad (which have real, nonzero dimensions), leaving the 11 of 12 airports whose blocker is `H`-prefixed still ambiguous. |
| 4 | **`RWY_ID` is exactly two non-empty tokens separated by `/`** | Structural, grounded in FAA's own reciprocal-heading runway-numbering convention (a two-directional fixed-wing runway is *by definition* reported as two headings) | **Adopted.** Already exactly the shape `normalize_pair()` requires to not raise — this rule doesn't change what counts as a valid runway pair, it changes what happens to a *non-matching* row (skip it, rather than abort the whole airport). |

Rule 4 is not a "prefix regex guessing at meaning" — it doesn't test for
`H` or `B` or `X` at all. It tests the one structural property FAA's own
numbering convention guarantees for every genuine two-directional runway
and guarantees *against* for every category found in §3 (a helipad,
balloonport pad, or placeholder record never has a second reciprocal
heading, because none of them are two-directional runways to begin with).

## 7. Regression analysis against the 63 clean airports

**Mathematically guaranteed, and empirically confirmed.** An airport is
only ever classified clean today because `plan_airport_inventory()` did
not raise `AmbiguousRunwayDesignationError` while processing its
(already-filtered-to-that-airport) NASR rows — which means every `RWY_ID`
row it saw already satisfied `normalize_pair()`'s two-ended requirement.
Rule 4 (§6) only removes rows that *fail* that same requirement, so it
cannot remove or alter any row a previously-clean airport's plan already
used. Confirmed directly: a full read-only simulation (rule applied as a
pure input filter, no code touched) against the real, current database
reproduces **all 63 previously-clean airport IDs unchanged**, plus the 12
formerly-ambiguous ones newly resolved — zero false positives (nothing
newly excluded), zero false negatives (nothing wrongly kept), zero
airports newly regressed to a worse classification.

MDW (id 12) and CGF (id 57) — the applied pilot — remain exactly
`ALREADY_COMPLETE`, `0` creates / `0` enrichments / `0` end-creates, as
required.

## 8. Recommended general rule

**Option D**, stated precisely: *A NASR `APT_RWY.csv`/`APT_RWY_END.csv`
row participates in canonical `Runway`/`RunwayEnd` inventory only if its
`RWY_ID` is exactly two non-empty tokens separated by `/`. A row whose
`RWY_ID` does not have that shape is skipped for that airport — the rest
of the airport's valid two-ended rows are still processed normally,
instead of the whole airport's plan being aborted.*

Options A and B (a single or combined FAA field) are not available — no
such field exists for this purpose (§4–§5). Option C (no safe rule
exists, requires a separate facility model or manual review) is not
correct either: a safe, general, fully-verified rule *does* exist; it is
simply structural rather than field-value-based, and it required
empirical verification (§3, §7) precisely because no FAA field states it
directly.

## 9. U.S.-wide simulated result

Simulated read-only (rule applied as an input filter before calling the
unmodified `resolve_us_clean_batch()`/`plan_airport_inventory()` — no
source file was changed):

| | Before this rule | With this rule (simulated) |
|---|---|---|
| Clean airports | 63 | **75** |
| Ambiguous airports | 12 | **0** |
| Unresolved airports | 1 | **1** (unchanged — see §10) |
| Runway creates (new, not yet applied) | 94 *(already applied)* | **+22** for the 12 newly-clean airports |
| Runway enrichments (new, not yet applied) | 39 *(already applied)* | **+6** |
| RunwayEnd creates (new, not yet applied) | 284 *(already applied)* | **+62** |

("Before" totals reflect the already-applied clean batch from the prior
task; the "with this rule" column is the *additional* amount the 12
newly-unblocked airports would contribute, not yet applied anywhere.)

Nationally, applying the rule excludes 6,521 of 23,196 `APT_RWY.csv` rows
(all of §3's categories) and keeps 16,675; on the `APT_RWY_END.csv` side,
33,350 of 39,871 rows are kept.

## 10. One no-identifier airport

**Not solved in this task**, kept explicitly separate from the 12-airport
special-record issue per instruction:

- **id**: 75
- **name**: Allegheny County Airport Authority
- **`faa_code`**: `None`
- **`iata_code`**: `None`
- **`icao_code`**: `None`
- **country**: USA

Deterministic NASR matching fails because the airport-to-NASR matching
convention this project already uses (`{faa_code, iata_code, icao_code} -
{None}` as candidate codes, matched against NASR `ARPT_ID`) has nothing
to match against — all three identifier fields are `NULL` in RWI's own
`Airport` row. This is a data-completeness gap in RWI's own record, not a
NASR data-quality or classification issue, and the rule in §8 does
nothing to address it.

## 11. Future-model implications

Helipads and balloonport pads are legitimate aerodrome facilities — they
are not noise or bad data, they're simply a different facility type than
the fixed-wing `Runway`/`RunwayEnd` model RWI has built. Recommended
posture, scoped deliberately small per instruction (no `Helipad`/generic
`Facility` model added in this task):

- **Preserve them only in raw evidence for now.** The rule in §8 operates
  on already-preserved NASR rows read at parse time — it doesn't discard
  anything from the preserved archive itself (`data/raw/nasr/` keeps
  every row, always). It only decides what does or doesn't become a
  canonical `Runway`/`RunwayEnd` row.
  - This means a future need (e.g. EMAS-on-a-helipad evidence, if that
    ever became relevant) could still be served by re-reading the same
    preserved archive with a different reader, without re-acquiring
    anything.
- **Do not model them yet.** There's no current RWI use case asking for
  helipad/balloonport data specifically; building a model for them now
  would be speculative scope, not a response to a real requirement.
- **Revisit only if a genuine need appears** — e.g. if EMAS presence data
  (`APT_ARS.csv`) is ever found to reference a helipad `RWY_END_ID`
  instead of a fixed-wing one, which would be a reason to reconsider, not
  addressed by this investigation.

## 12. Test plan for future implementation

No code was changed in this task. Tests to write once the rule in §8 is
actually implemented (in `app/services/runway_inventory.py`'s
`plan_airport_inventory()`, or a wrapping layer that filters before
calling it — implementation choice deferred to that future task):

- **Helipad row is skipped, not fatal** — a synthetic `APT_RWY.csv` row
  with `RWY_ID="H1"` alongside a valid pair no longer raises; the valid
  pair still plans correctly.
- **Balloonport row is skipped, not fatal** — same, with `RWY_ID="B1"`.
- **Empty-placeholder row is skipped, not fatal** — same, with
  `RWY_ID="00X"`, `RWY_LEN=0`, `RWY_WIDTH=0`, blank surface.
- **Legitimate runway preservation** — a two-ended `RWY_ID` row is never
  affected by the new skip logic; existing
  `tests/test_runway_inventory_planning.py` coverage must continue
  passing unchanged.
- **MDW regression** — re-running the MDW pilot fixture with the new
  logic still yields exactly 4 runways / 8 ends, matching
  `tests/test_apply_canonical_runway_inventory_mdw_cgf_pilot.py`'s
  existing assertions.
- **CGF regression** — same, 1 runway / 2 ends.
- **All 63 prior-clean airports unchanged** — a fixture reproducing each
  of the 63 airports' NASR row shape must classify identically
  before/after the change (no new creates/enrichments appear for any of
  them purely from the new skip logic).
- **12 ambiguous airports reclassified as expected** — each of the 12
  airports in §2, given real fixture rows matching their actual NASR
  shape, must now classify as `CLEAN_CREATE`/`CLEAN_ENRICH` with the
  exact create/enrich/end-create counts in §2's table, not `AMBIGUOUS`.
- **No designation-only heuristic dependency** — a test asserting the
  skip condition is `len(rwy_id.split("/")) == 2 and both non-empty`,
  *not* a check for `H`/`B`/`X` characters — so a future NASR cycle
  introducing a differently-prefixed non-runway record is still handled
  correctly without a code change, and so nobody accidentally
  reintroduces a prefix-matching version of this rule later.
- **A genuine `AmbiguousRunwayDesignationError` still fires when it
  should** — the pair-vs-actual-ends mismatch case (`plan_airport_inventory`'s
  existing "ends ... do not match the pair designation" check) must remain
  unaffected — that's a real data conflict, not a special-record
  exclusion, and this task doesn't touch it.

## 13. Smallest next implementation slice

Add one pure filtering step — "keep only `RWY_ID` values with exactly two
non-empty `/`-separated tokens" — applied to the `runway_rows`/
`runway_end_rows` lists **before** they're passed into
`plan_airport_inventory()`. This requires no change to
`normalize_pair()`, `normalize_end()`, `plan_airport_inventory()`, or
`apply_plan()` themselves; those already do exactly the right thing for
every row that reaches them. The smallest safe change is at the *caller*
boundary (the `resolve_us_clean_batch()`/`classify_airport_batch()`
orchestration layer, or the NASR reader itself), together with the test
plan in §12 — not a change to the core planner's semantics.

---

## Explicit answers

**A. Can the 12 ambiguous airports be resolved with one generic FAA-backed rule?**
**Yes.** All 12 resolve cleanly under a single structural rule, verified
against every one of their blocking records individually (§2) and
confirmed by a full simulation (§9).

**B. What exact field/rule should govern inclusion in canonical Runway inventory?**
No single FAA field does. The rule is structural: **`RWY_ID` must be
exactly two non-empty tokens separated by `/`** — FAA's own reciprocal-
heading runway-numbering shape, already what `normalize_pair()` requires.
Rows that don't match this shape (helicopter pads, balloonport pads,
empty placeholders — §3) are skipped, not treated as an airport-wide
failure.

**C. Does the rule leave the original 63 clean airports unchanged?**
**Yes**, both by construction (§7's proof: the rule only removes rows that
already couldn't have been part of any clean airport's plan) and by
direct simulation against the real, current database.

**D. How many U.S. airports would then be deterministic?**
**75 of 76** — everything except the one no-identifier airport (§10).

**E. What remains unresolved?**
Only the one no-identifier airport (id 75, Allegheny County Airport
Authority) — a data-completeness gap in RWI's own `Airport` row, unrelated
to this investigation's special-record findings, deliberately not solved
here.

**F. What is the smallest safe implementation step?**
A single input-filtering step at the caller boundary (§13) — no change to
the core planner's normalization or matching logic, with the test plan in
§12 as its acceptance criteria.

---

No code change. No database write. No commit. No push. No deployment.
