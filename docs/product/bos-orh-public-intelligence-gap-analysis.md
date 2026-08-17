# BOS / ORH Public Intelligence Gap Analysis

**Read-only investigation. No code, database, schema, or public-export
modification.** Every fact below comes from the real, current development
database (inspected read-only) and the current repository code
(`app/static_export/build.py`, `app/static_export/templates/`,
`app/services/physical_installation_reconciliation.py`, and the relevant
ingestion scripts).

Framework used throughout:

- **RWI KNOWS** — structured/canonical data exists
- **RWI CAN PROVE** — preserved evidence supports it
- **RWI PUBLISHES** — the current static/public export actually exposes it
- **RWI IS MISSING** — RWI genuinely lacks the information/evidence/linkage

## 0. Baseline

Branch `main`, HEAD `fbb0ed94ccacdb45e9923cb489a58773f39c3b92` (=
origin/main). DB `data/runway_safe.db`, `Runway`=180, `RunwayEnd`=360, U.S.
planner: 76 `ALREADY_COMPLETE` / 0 unresolved / 0 ambiguous / 0 conflict.

- **BOS** = Airport id **3** (`Boston Logan International Airport`, FAA/IATA `BOS`, ICAO `KBOS`)
- **ORH** = Airport id **44** (`Worcester Regional`, FAA/IATA `ORH`, ICAO `KORH`)

## 1. How the public pages are actually built

`app/static_export/build.py::_airport_view()` is the single function that
decides what an airport detail page (and `data.json`) contains. Two
findings apply to *every* airport, not just BOS/ORH:

### 1a. Canonical runway inventory is deliberately excluded — but the reason is now stale

```python
# `runways` (and the "Banor" template section that read it) is
# intentionally omitted from the public airport view - see
# docs/ui/mdw-runway-diagnosis.md. The `runways` table is a
# non-exhaustive, one-row-per-airport placeholder, not a governed
# canonical runway/runway-end inventory; publishing it here or in
# data.json could misleadingly imply completeness next to the
# governed `reviewed_identities` list below. Restore once RWI has
# governed canonical runway/runway-end coverage.
```

`docs/ui/mdw-runway-diagnosis.md` (the doc this comment cites) was written
**before the canonical `RunwayEnd` model existed** — it says verbatim
*"There is no `RunwayEnd` table or model in this codebase"* and that MDW
had exactly **1** `Runway` row. Both statements are now false: `RunwayEnd`
exists and is complete for all 76 U.S. airports (this task's own baseline
check, §0), and MDW alone now has **4** `Runway` rows. **The condition the
comment says to wait for has already been met.** This is answer **B** to
the "why is runway information absent publicly" question in the task
brief: present in canonical DB, intentionally excluded from public export,
for a reason that no longer applies.

### 1b. "EMAS idag" (current EMAS) publication policy

`airport_detail.html`:

```jinja
{% if airport.reviewed_identities or airport.nasr_presence %}
  ...renders pills...
{% else %}
  <div class="empty-state">Ingen aktuell EMAS-förekomst är publicerad från
  granskad eller FAA-cykelbaserad evidens.</div>
{% endif %}
```

Two independent publication routes feed this section, both built in
`_airport_view()`:

- **`reviewed_identities`** — `PhysicalInstallationIdentity` rows that have
  an `InstallationAssertionLink` with `outcome="SAME_PHYSICAL_INSTALLATION"`.
  Created only by `app/services/physical_installation_reconciliation.py`,
  which is **deliberately manual and human-gated** (its own docstring:
  *"This module intentionally contains no matching or automatic
  reconciliation"* — `actor`/`reason` are required arguments). This is
  correct, conservative, by-design behavior — not a bug.
- **`nasr_presence`** — `SourceAssertion` rows where `assertion_type ==
  "runway_end"`, the source is a NASR APT_ARS.csv cycle
  (`external_id` starts `faa_nasr:airport_csv:`), **and `assertion.runway_end`
  (the normalized column) is set.**

**Critical finding**: `scripts/dry_run_nasr_apt_ars.py` — the nationwide
NASR current-EMAS-presence ingestion script — populates
`raw_runway_end_value` (e.g. `"04L"`, `"11"`) on every row it creates, but
**never writes the normalized `runway_end` column**. Read-only DB check:

```
Total runway_end SourceAssertions:            115
Unpromoted (raw only, runway_end still NULL): 115
Promoted (runway_end set):                      0
Distinct airports with unpromoted evidence:     63
```

**Zero rows, at any airport in the entire database, have ever been
promoted through this pathway.** The only reason `EMAS idag` is non-empty
anywhere today is the *other* route: the 6 existing
`PhysicalInstallationIdentity` rows, which belong to exactly **2**
airports — MDW (4) and CGF (2), the project's known pilot pair. Every
other one of the 76 canonically-complete airports, including BOS and ORH,
currently shows an empty `EMAS idag` section **not because the evidence
doesn't exist**, but because neither publication route has been run for
them. This is a nationwide **UI/PUBLIC EXPORT GAP**, not a BOS/ORH-specific
issue and not an evidence gap — see §5–§6.

## 2. BOS — everything RWI knows

### Canonical runway inventory (already governed, part of the 76/76 milestone)

| Runway | RunwayEnds |
|---|---|
| 9/27 | 9, 27 |
| 4L/22R | 4L, 22R |
| 4R/22L | 4R, 22L |
| 14/32 | 14, 32 |
| 15L/33R | 15L, 33R |
| 15R/33L | 15R, 33L |

6 Runways, 12 RunwayEnds. `RWI KNOWS` this completely; `RWI PUBLISHES`
none of it (§1a).

### EMAS-related evidence

| Row | Type | Runway/end link | Status | Source | Evidence quality |
|---|---|---|---|---|---|
| Installation 33 | EMASMAX | `runway_id=NULL`, `runway_end=NULL` | active | FAA EMAS Incidents/Installations map (Tableau, `faa_tableau`) | partial |
| Installation 107 | EMASMAX, install_year 2005 | `runway_id=NULL`, `runway_end=NULL` | active | FAA EMAS Fact Sheet 2016 (`faa_fact_sheet`) | partial |
| SourceAssertion 161 | `runway_end` (raw) | `raw_runway_end_value="04L"` (pair `04L/22R`) | unreviewed | NASR APT_ARS.csv 2026-08-06 | direct_strong |
| SourceAssertion 162 | `runway_end` (raw) | `raw_runway_end_value="15R"` (pair `15R/33L`) | unreviewed | NASR APT_ARS.csv 2026-08-06 | direct_strong |

**Notable finding — a discrepancy already visible inside RWI's own free
text, never reconciled**: both FAA structured sources (the Tableau map,
via Installation 33's own note, and NASR APT_ARS.csv, via SourceAssertions
161/162) record the currently-equipped ends as **04L** and **15R** — the
*reciprocal* ends of the pairs commonly associated with Logan's EMAS beds.
But Signal 3's own `source_notes` (added 2026-08-14, citing a Massport
statement reported by the Revere Journal) states explicitly: *"Boston
Logan already has two other EMAS installations in operation: Runway 22R
and Runway 33L (registered in this database as Installation 33 and
Installation 107)."* **RWI already possesses, in free text, a
human-sourced correction/clarification that the equipped ends are 22R and
33L — not 04L/15R as the raw structured NASR/Tableau fields say — and this
has never been reconciled into any structured field.** This is exactly the
kind of discrepancy the reconciliation layer (`PhysicalInstallationIdentity`
+ `InstallationAssertionLink`) exists to resolve and record — it has
simply never been run for BOS.

### Runway 27 Phase 2 project

| Field | Value |
|---|---|
| Signal | id 3, "Runway 9/27 RSA and EMAS phase 2" |
| Status | under construction |
| Confidence | confirmed |
| Runway link | `runway_id=3` (the 9/27 pair) — end-level link (9 vs 27) only in free text |
| Construction window | 2026-08-31 → 2026-11-15 |
| Likely supplier | Runway Safe (reason: "FAA construction program identifies EMAS phase 2") |
| Funding (this signal's own note) | IIJA Announcement 4, FY2026, $17,500,000 |
| Corroboration | FAA Construction Impact Report (RWY 27 RSA Phase 2); Massport via Revere Journal 2026-08-12, confirming Phase 1 completed fall 2025 |

### Separate USAspending grant signals (Massachusetts Port Authority)

| Signal | Amount | FY |
|---|---|---|
| 39 | $56,187,750 | 2025 |
| 42 | $8,983,669 | 2026 |
| 63 | $60,311.22 | 2023 |

RWI has **not** explicitly linked whether these three grants fund the same
Runway 27 Phase 2 work as Signal 3/the IIJA grant, a related-but-distinct
Logan RSA program, or something else — they exist as separate, unlinked
Signal rows. This ambiguity is itself worth naming as a finding (§7).

### BOS summary matrix

| Claim | KNOWS | CAN PROVE | PUBLISHES | MISSING |
|---|---|---|---|---|
| Airport runway inventory (6 runways, 12 ends) | ✅ | ✅ (NASR-derived, canonical) | ❌ | |
| Existing/current EMAS presence (2 systems) | ✅ | ✅ (2 independent FAA sources) | ❌ | |
| Which ends: 04L/15R (raw) vs 22R/33L (human-sourced) | partial | ✅ raw; ✅ human note — **conflicting**, unreconciled | ❌ | reconciliation needed |
| Runway 27 project (Phase 2) | ✅ | ✅ (FAA report + Massport/news) | ✅ (via Signal) | |
| Phase 2 funding (IIJA $17.5M) | ✅ | ✅ | ✅ (in signal notes text only, not structured) | |
| Phase 1 completion (fall 2025) | ✅ | ✅ (Massport statement) | partial (buried in note prose) | |
| Relationship between Phase 2 and the 3 USAspending grants | ❌ | ❌ | ❌ | ✅ genuinely unresolved |
| Total known federal funding across all BOS EMAS/RSA activity | ✅ (addable) | ✅ | ❌ (never summed/shown) | |

## 3. ORH — everything RWI knows

### Canonical runway inventory

| Runway | RunwayEnds |
|---|---|
| 11/29 | 11, 29 |
| 15/33 | 15, 33 |

2 Runways, 4 RunwayEnds. `RWI KNOWS` this completely; `RWI PUBLISHES` none
of it (§1a).

### EMAS-related evidence

| Row | Type | Runway/end link | Status | Source | Evidence quality |
|---|---|---|---|---|---|
| Installation 35 | EMASMAX | `runway_id=54` (11/29) set; `runway_end=NULL` | active | FAA Tableau map | partial |
| Installation 116 | EMASMAX, install_year 2008 | `runway_id=NULL`, `runway_end=NULL` | active | FAA Fact Sheet 2016 | partial |
| SourceAssertion 164 | `runway_end` (raw) | `raw_runway_end_value="11"` (pair `11/29`) | unreviewed | NASR APT_ARS.csv 2026-08-06 | direct_strong |
| SourceAssertion 165 | `runway_end` (raw) | `raw_runway_end_value="29"` (pair `11/29`) | unreviewed | NASR APT_ARS.csv 2026-08-06 | direct_strong |

NASR's current (2026-08-06) cycle reports EMAS present at **both** ends of
11/29 (11 and 29) — internally consistent between the two independent FAA
sources, unlike BOS.

**Installation 116's own notes are the single richest piece of evidence in
this entire analysis** — RWI's own prior human research, verbatim:

> *"2 system (2008/2009), GA-flygplats ('\*\*'), enligt både 2011- och
> 2016-Fact Sheet, ingen ändring. Detta är den ursprungliga installationen
> - vår egen tidigare research visar en senare, helt ny ersättning
> 2024/2025."*

Translation: *"2 systems (2008/2009), GA airport, per both the 2011 and
2016 Fact Sheets, no change. This is the original installation — our own
earlier research shows a later, completely new replacement in 2024/2025."*

This is corroborated by five separate USAspending "replacement"-category
signals, and by the current (2026-08-06) NASR cycle still showing EMAS
present at both ends today — i.e. RWI's own evidence already tells a
complete story: **original 2008/2009 install → full replacement
2024/2025 → still present today**, but none of the three parts of that
story ("original", "replacement", "current-post-replacement") are linked
to each other or to a canonical `RunwayEnd`.

### 2024/2025 replacement grants (Massachusetts DOT / airport authority via USAspending)

| Signal | Amount | FY |
|---|---|---|
| 46 | $6,292,342 | 2025 |
| 53 | $3,245,214 | 2024 |
| 56 | $1,387,758 | 2025 |
| 57 | $1,324,344 | 2024 |
| 62 | $73,638 | 2025 |
| **Total** | **$12,323,296** | 2024–2025 |

All five are categorized `replacement` (not `new_installation`) —
already correctly classified, consistent with Installation 116's own note.

### Why the public page says "Ingen aktuell EMAS-förekomst är publicerad..."

Exactly the mechanism in §1b: ORH has **zero** `PhysicalInstallationIdentity`
rows and its two `runway_end` SourceAssertions (164, 165) both have
`runway_end=NULL` (only `raw_runway_end_value` is set). The timeline
section still shows dated entries (Installation 116's `install_year=2008`,
the five grant Signals' `planning_year` 2024/2025) because the timeline
uses a much looser year-based inclusion rule than the strict "EMAS idag"
box — this is why the task's premise ("despite the timeline containing
EMAS/replacement evidence") is correct: two different parts of the same
page apply two very different evidence bars, and only one of the two
displays anything for ORH today.

**Separate, real product-visibility finding**: all 5 of ORH's Signals are
`usaspending_grant`-sourced, so `_airport_view()`'s
`primary_signals`/`funding_signals` split (build.py:605-606) routes every
one of them into `funding_signals` — the collapsed "Finansiering och
bidrag" `<details>` disclosure. ORH's main "Projekt"-table shows the
**empty-state** ("Inga offentliga projekt- eller bevakningsuppgifter
registrerade") as the headline, with the entire $12.3M replacement story
sitting inside a closed toggle beneath it.

### ORH summary matrix

| Claim | KNOWS | CAN PROVE | PUBLISHES | MISSING |
|---|---|---|---|---|
| Airport runway inventory (2 runways, 4 ends) | ✅ | ✅ | ❌ | |
| Runway 11/29 | ✅ | ✅ | ❌ | |
| EMAS at both ends (11 and 29), current cycle | ✅ | ✅ (2 independent FAA sources) | ❌ | |
| Original installation (2008/2009) | ✅ | ✅ (2011+2016 Fact Sheets) | ❌ (main table shows "no projects") | |
| Replacement project (2024/2025) | ✅ | ✅ (5 USAspending grants) | partial (buried in collapsed disclosure) | |
| Replacement cost/funding ($12.3M) | ✅ (addable) | ✅ | ❌ (never summed/shown, and hidden behind a toggle) | |
| Current post-replacement installation state | ✅ (inferred from NASR + notes) | ✅ | ❌ | not modeled as a distinct "current installation" record |

## 4. Authoritative source inventory already in RWI (no new research performed)

Already preserved/referenced, used above:

- **FAA NASR** (`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip`) —
  `APT_RWY.csv`/`APT_RWY_END.csv` (canonical runway inventory, already
  fully ingested) and `APT_ARS.csv` (current EMAS presence by runway end,
  ingested as raw `SourceAssertion` evidence, never promoted).
- **USAspending.gov** — grant-level funding evidence for both airports,
  correctly categorized (`new_installation` for BOS's Phase 2 grants,
  `replacement` for ORH's).
- **FAA IIJA Announcement PDFs** — the BOS Phase 2 $17.5M grant, appended
  as prose to Signal 3, not a structured Signal/Source of its own for this
  particular note (contrast with `scripts/add_iija_fy2026_known_grants.py`'s
  general convention).
- **FAA EMAS Incidents and Installations Tableau map** and **FAA EMAS Fact
  Sheets (2011, 2016)** — the source of both Installation rows at each
  airport.
- **News/Massport statement** (Revere Journal, 2026-08-12) — the only
  source that names BOS's specific equipped ends (22R, 33L) in a form a
  human can read, currently unstructured.

Distinguishing the four gap types from §8's brief:

- **A. Evidence exists but isn't modeled**: none identified for BOS/ORH —
  everything found is already a `Source`/`SourceAssertion`/`Installation`/
  `Signal` row.
- **B. Evidence exists and is modeled but isn't published**: the dominant
  pattern here — canonical runway inventory (§1a), NASR current-EMAS
  presence (§1b), BOS's 22R/33L clarification (in `Signal.source_notes`
  prose), ORH's funding total (arithmetic already possible from existing
  rows).
- **C. Evidence exists only as an unresolved assertion**: the 4 raw
  `runway_end` SourceAssertions (BOS 161/162, ORH 164/165) — `direct_strong`
  quality, genuinely unresolved (never reviewed, never promoted).
- **D. Evidence is genuinely missing**: whether BOS's 3 USAspending grants
  fund the same physical work as Signal 3/the IIJA grant; ORH's specific
  replacement completion date and current vendor (RWI knows a replacement
  happened and roughly when, but not exactly when it finished or who the
  installer was).

## 5. Public-page product assessment (investor view, today)

If an investor opens either page today:

| Gap | Ranking | Why |
|---|---|---|
| BOS: 2 EMAS systems exist and are active, invisible | **CRITICAL** | Direct, current-footprint fact; the page implies nothing is known |
| ORH: full 2024/2025 replacement story invisible in the main view | **CRITICAL** | $12.3M, recent, replacement — exactly what an investor scans for — buried in a collapsed toggle behind an empty-looking main table |
| Neither airport shows its runway inventory | **HIGH** | Basic orientation context; already fully known and governed |
| BOS's 22R/33L vs 04L/15R end discrepancy | **HIGH** | A real data-quality signal RWI itself already surfaced, currently invisible to anyone but a database reader |
| BOS Phase 2 funding total not summed/shown across sources | **MEDIUM** | The IIJA figure exists only as prose inside a note field |
| ORH replacement total not summed/shown | **MEDIUM** | Same pattern, ORH side |
| Whether BOS's 3 separate USAspending grants relate to Phase 2 | **LOW–MEDIUM** | Real ambiguity, but not misleading as currently displayed (each grant is shown individually and accurately) |

## 6. Architectural classification of each gap

| # | Gap | Classification |
|---|---|---|
| 1 | Canonical runway inventory not shown on airport pages | **PUBLIC EXPORT GAP** / **UI/RENDERING GAP** (stale exclusion, data and model already complete) |
| 2 | NASR current-EMAS presence never promoted (`runway_end` never written) nationwide | **PUBLIC EXPORT GAP** (ingestion already correct; the promotion/normalization step was never built as a repeatable, non-pilot mechanism) |
| 3 | `PhysicalInstallationIdentity` reconciliation only ever run for MDW/CGF | **RECONCILIATION GAP** (by design human-gated; simply not yet scaled past a 2-airport pilot) |
| 4 | BOS 22R/33L vs 04L/15R discrepancy | **RECONCILIATION GAP** (conflicting evidence exists; resolving it is exactly what the reconciliation layer is for) |
| 5 | ORH's funding total not computed/surfaced | **UI/RENDERING GAP** (pure aggregation of already-published Signal rows) |
| 6 | ORH's real "Projects" content hidden behind a collapsed disclosure when it's the *only* content | **UI/RENDERING GAP** |
| 7 | Whether BOS's 3 USAspending grants and Signal 3/IIJA fund the same work | **DATA QUALITY GAP** / possible **RECONCILIATION GAP** — needs a human/evidence decision, not a code fix |
| 8 | ORH's exact replacement completion date/vendor | **EVIDENCE GAP** (genuinely not in any preserved source found) |
| 9 | No mechanism watches for a *new* NASR cycle changing current-EMAS presence over time | **CHANGE-DETECTION / INTELLIGENCE GAP** (not evaluated in depth here — out of this task's two-airport scope, but structurally implied by the entirely-unpromoted nationwide state in §1b) |

No **DOMAIN MODEL GAP** or **INGESTION GAP** was found for either airport
— `RunwayEnd`, `SourceAssertion`, `PhysicalInstallationIdentity`, and the
NASR/USAspending ingestion pipelines already capture everything examined
above. Every material gap found is downstream of ingestion: reconciliation,
export, or rendering.

## 7. Recommended smallest next product slice

### A. Smallest useful slice

**Publish the canonical runway inventory on airport pages** (remove the
now-stale suppression in `_airport_view()`/`airport_detail.html`, §1a) —
*combined with* **promoting already-ingested NASR current-EMAS-presence
evidence** (write `SourceAssertion.runway_end` from the already-present
`raw_runway_end_value` for `assertion_type="runway_end"` rows sourced from
NASR APT_ARS.csv, §1b) as one coherent slice. These two are naturally
paired: showing "Banor" without any current-EMAS pills next to it is a
half-measure, and the promotion step is the direct generalization of the
MDW pilot's already-proven, already-reviewed mechanism — not a new one.

### B. What the user (investor) would visibly gain

- Every one of the 76 canonically-complete airports gains a real "Banor"
  (runways) list — immediate orientation context, today shown nowhere.
- Up to 63 airports (nationwide, not just BOS/ORH) gain a populated "EMAS
  idag" section sourced from `direct_strong`-quality FAA NASR evidence,
  with **zero new ingestion and zero new human review** — the evidence is
  already sitting in the database, unpromoted.
- BOS and ORH specifically go from a blank "no current EMAS" statement to
  showing 2 systems (BOS) / 2 ends (ORH) backed by an explicit FAA-cycle
  citation.

### C. Existing RWI layers this reuses

`RunwayEnd`/`Runway` (canonical foundation, already complete),
`SourceAssertion` (already ingested, already `direct_strong`), the
`nasr_presence` rendering path (already coded and already wired into the
template — it has simply never had data to show). No new table, no new
ingestion script, no new reconciliation tooling.

### D. What this deliberately does NOT solve

- Does **not** resolve the BOS 04L/15R vs 22R/33L discrepancy — that
  requires an evidence-backed human reconciliation decision (like the
  MDW/CGF pilot), not an automated promotion. Publishing the raw NASR
  value as-is is honest ("FAA-cykelbaserad evidens", exactly the
  qualifier the template already uses) but would surface 04L/15R, not the
  human-sourced 22R/33L, until that reconciliation happens separately.
- Does **not** scale `PhysicalInstallationIdentity` human reconciliation
  beyond MDW/CGF — that stays a deliberate, manual, evidence-reviewed
  process by design.
- Does **not** fix the ORH funding-total/disclosure-visibility gap (§6
  items 5–6) or the BOS/USAspending-grant-relationship ambiguity (§6 item
  7) — both are real but separate from the runway/NASR-presence
  publication gap and would each be their own small slice.
- Does **not** add change detection (§6 item 9).

### E. What should follow afterward

1. A second slice specifically reconciling BOS's conflicting EMAS-end
   evidence (04L/15R vs 22R/33L) — narrowly scoped, evidence-backed,
   one-airport, following the same investigate → dry-run → apply pattern
   used for Allegheny/Morristown.
2. A third slice improving the "Projects" panel's funding-signal
   visibility/aggregation (surface a computed total; reconsider showing
   the funding disclosure open-by-default when it is an airport's *only*
   project content, as at ORH) — a presentation-only change, no new data.
3. Only after those: consider whether to extend
   `PhysicalInstallationIdentity` human reconciliation beyond MDW/CGF to a
   broader batch of airports, and/or build a change-detection mechanism
   for future NASR cycles.
