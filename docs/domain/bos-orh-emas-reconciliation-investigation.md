# BOS / ORH EMAS Evidence-Backed Reconciliation Investigation

**Investigation / reconciliation design only. No database write, no
`PhysicalInstallationIdentity`/`InstallationAssertionLink` creation, no
`SourceAssertion` promotion, no production UI/export code change, no
commit, no push, no deployment.** Scope: BOS (assertions 161, 162) and
ORH (assertions 164, 165) only — BGM, LEX, and ELM are explicitly out of
scope and were not touched or re-examined.

## 1. Branch / HEAD

Branch `main`, HEAD `e62ca7b07f56aec209c3fb046ec12857693b14fb` (the
already-completed, already-pushed NASR EMAS AUTO_RESOLVABLE promotion
commit). Working tree carries the same pre-existing, still-uncommitted
`PUBLIC_EMAS_PROTECTED_DIRECTION_PRESENTATION` slice changes as before
this task started (`app/static_export/build.py`,
`app/static_export/presentation.py`,
`app/static_export/templates/airport_detail.html`,
`tests/test_static_export.py`, plus the new
`docs/product/public-emas-protected-direction-presentation.md`) —
**unmodified by this task** (see §21/§23).

## 2. Real DB path and proof unchanged

```
c:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

| | Before this task | After this task |
|---|---|---|
| Size | 667648 bytes | 667648 bytes |
| mtime | 1787004353.2183805 | 1787004353.2183805 |
| SHA-256 | `23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559` | (identical) |

Confirmed identical immediately before this investigation began and again
after every risk-bearing step (the web research, the read-only DB
inspection, and the isolated-copy simulation in §19). All simulation
writes in this task were made **only** to a disposable scratch-directory
copy (`...\scratchpad\sim_bos_orh_runway_safe.db`, outside the repository,
never `data/runway_safe.db`), with the real file's hash re-verified
identical immediately after the simulation script ran (assertion built
into the script itself — see §19).

## 3. BOS current canonical topology

Airport id **3** (`Boston Logan International Airport`, IATA/FAA `BOS`,
ICAO `KBOS`, Boston, Massachusetts).

| Runway id | Designation | RunwayEnd ids / designations |
|---|---|---|
| 3 | `9/27` | 19 (`9`), 20 (`27`) |
| 66 | `4L/22R` | 15 (`4L`), 16 (`22R`) |
| 67 | `4R/22L` | 17 (`4R`), 18 (`22L`) |
| 68 | `14/32` | 21 (`14`), 22 (`32`) |
| 69 | `15L/33R` | 23 (`15L`), 24 (`33R`) |
| 70 | `15R/33L` | 25 (`15R`), 26 (`33L`) |

6 Runways, 12 RunwayEnds, every Runway has exactly 2 ends — consistent
with the nationwide zero-exception topology guarantee already established
in `docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md` §4.

## 4. ORH current canonical topology

Airport id **44** (`Worcester Regional`, IATA/FAA `ORH`, ICAO `KORH`,
Worcester, Massachusetts).

| Runway id | Designation | RunwayEnd ids / designations |
|---|---|---|
| 54 | `11/29` | 179 (`11`), 180 (`29`) |
| 120 | `15/33` | 181 (`15`), 182 (`33`) |

2 Runways, 4 RunwayEnds, both with exactly 2 ends.

## 5. Exact relevant assertions (read-only, re-confirmed this task)

| Assertion | Airport | `raw_runway_end_value` | Pair | `runway_end` (normalized) | `review_state` | `evidence_quality` | Source |
|---|---|---|---|---|---|---|---|
| 161 | BOS | `04L` | `04L/22R` | `NULL` | `unreviewed` | `direct_strong` | FAA NASR APT_ARS.csv, 2026-08-06 cycle (source id 69) |
| 162 | BOS | `15R` | `15R/33L` | `NULL` | `unreviewed` | `direct_strong` | FAA NASR APT_ARS.csv, 2026-08-06 cycle (source id 69) |
| 164 | ORH | `11` | `11/29` | `NULL` | `unreviewed` | `direct_strong` | FAA NASR APT_ARS.csv, 2026-08-06 cycle (source id 69) |
| 165 | ORH | `29` | `11/29` | `NULL` | `unreviewed` | `direct_strong` | FAA NASR APT_ARS.csv, 2026-08-06 cycle (source id 69) |

No `PhysicalInstallationIdentity` exists for BOS or ORH (confirmed by
direct query — both return zero rows). Neither airport is touched by the
already-applied 97-row promotion (`e62ca7b`) — both remain exactly as
`REVIEW_REQUIRED` left them.

**A. Current physical EMAS presence** (NASR-reported, this cycle only):
BOS at `04L` and `15R`; ORH at `11` and `29`. This is the entirety of what
the 4 assertions above establish.

**B. Protected runway direction** (derived, not stored anywhere yet):
BOS `04L`→`22R`, `15R`→`33L`; ORH `11`→`29`, `29`→`11` — see §8/§12.

**C. Historical installation/replacement information** (separate rows,
untouched by anything proposed here): BOS Installation 107 (`install_year
2005`, notes describing systems A/2005 and B/2006, replaced 2012 and 2014
per the 2016 FAA Fact Sheet); ORH Installation 116 (`install_year 2008`,
notes describing the 2008/2009 original system and RWI's own prior
research finding a full 2024/2025 replacement) plus 5 ORH USAspending
`replacement`-category Signals.

**D. Future/current project information** (separate, untouched): BOS
Signal 3, `Runway 9/27 RSA and EMAS phase 2`, `status="under
construction"`, `runway_id=3` (the `9/27` pair — the *third*, not-yet-
current BOS EMAS system, physically distinct from the two beds this
investigation concerns).

These four concepts are kept strictly separate throughout this report —
no historical/current or project/current conflation anywhere below.

## 6. Primary sources reviewed (this task's own re-verification)

All fetches performed fresh in this task via `WebSearch`/`WebFetch`, not
copied from the prior pilot's findings. Source hierarchy identical to
`docs/product/bos-orh-authoritative-web-research-pilot.md` §2 (Tier 1 =
primary/official; Tier 2 = corroboration quoting Tier 1 directly; Tier 3 =
not used for any material claim).

| # | Source | Tier | Fetch method | Publisher | Title/identifier | Date |
|---|---|---|---|---|---|---|
| 1 | `massport.com/media/newsroom/massport-begin-runway-safety-work-boston-logan` | 1 | Direct fetch (this task) | Massport | "Massport to Begin Runway Safety Work at Boston Logan" | (undated on the fetched page; content matches the Phase 1/2025 announcement) |
| 2 | `reverejournal.com/2026/08/12/massport-to-begin-phase-2-of-runway-safety-work-at-boston-logan/` | 2 (quotes Massport directly) | Direct fetch (this task) | Revere Journal | "Massport to begin Phase 2 of runway safety work at Boston Logan" | 2026-08-12 |
| 3 | `baystatebanner.com/public-notice/mpa-contract-no-w269-c1-...` | 1 | Search-snippet only — direct fetch returned HTTP 403 (this task) | Massachusetts Port Authority (public procurement notice) | "MPA Contract No. W269-C1: Runway 11 Departure End EMAS Replacement (Runway 29 End), Worcester Regional Airport" | bid window referenced: through 2024-03-20 |
| 4 | `baystatebanner.com/public-notice/mpa-contract-no-w269-c2-...` | 1 | Search-snippet only — direct fetch returned HTTP 403 (this task) | Massachusetts Port Authority | "MPA Contract No. W269-C2: Replacement of Runway 29 Departure End Engineered Material Arresting System (EMAS), Worcester Regional Airport" | not captured |
| 5 | `baystatebanner.com/public-notice/mpa-contract-no-w306-...` | 1 | Search-snippet only — direct fetch returned HTTP 403 (this task, same block as the prior pilot) | Massachusetts Port Authority | "MPA Contract No. W306: Federally Funded Airfield Capital Improvement Projects" (bundles both EMAS ends + Taxiway F + terminal roadway) | not captured |
| 6 | `spectrumnews1.com/ma/worcester/news/2024/09/18/worcester-airport-emas-system` | 2 (quotes airport/Massport officials directly) | Direct fetch (this task) | Spectrum News 1 | "Worcester Regional Airport replaces 'EMAS' runway system" | 2024-09-18 |

No claim below rests on a Tier 3 source. Sources 3–5 remain fetch-blocked
(HTTP 403) exactly as in the prior pilot — flagged as weaker evidence
(title/snippet only, not full document text) per this task's own tiering
discipline, not silently treated as equivalent to a direct fetch.

## 7. BOS evidence findings

**Massport's own newsroom, fetched directly twice in this task (source 1,
and its 2026-08-12 republish as source 2), states, verbatim (source 2):**

> "Boston Logan currently has two other EMAS installations in service, one
> at the end of Runway 22R and another at the end of Runway 33L."

Source 1 (the earlier/original announcement) states the equivalent
sentence in near-identical wording. Both are Massport's own words, not a
third party's paraphrase.

**Runway 27 Phase 2 status, same sources:** source 2 states the current
(2026-08-12) status explicitly — "Runway 9-27 will be closed, with only
very limited availability, for 75 days starting August 31" and "work
expected to be completed before Thanksgiving," describing this as
continuing work that "build[s] on work completed last fall" (Phase 1).
This matches RWI's own Signal 3 (`construction_start=2026-08-31`,
`completion_date=2026-11-15`, `status="under construction"`) closely, and
critically, **the same sentence that reports Runway 27 as still under
construction is the same sentence that reports the 22R/33L systems as
already "in service."** Massport's own language draws this exact
distinction — it is not RWI inferring it.

Confirmed independently: **no `SourceAssertion` exists anywhere in RWI's
DB for a `runway_end` of `9` or `27` at BOS** (the only two `runway_end`
assertions for BOS are 161/162, at `04L`/`15R`) — the NASR 2026-08-06
cycle itself does not yet report EMAS at Runway 9/27, consistent with it
not yet being operational. There is no data source in RWI, real or
proposed, that could accidentally cause Runway 27 to appear in a
current-EMAS-presence reconciliation for BOS.

## 8. BOS physical → protected mapping

| Physical (NASR `RWY_END_ID`) | Canonical `RunwayEnd` | Canonical `Runway` | Protected direction (topology) | Confirmed publicly as |
|---|---|---|---|---|
| `04L` | id 15 | `4L/22R` (id 66) | `22R` (id 16) | "Runway 22R" (Massport, Tier 1, direct) |
| `15R` | id 25 | `15R/33L` (id 70) | `33L` (id 26) | "Runway 33L" (Massport, Tier 1, direct) |

Verified through **both** required channels independently:

1. **Canonical Runway topology** (RWI's own data): each pair has exactly
   2 `RunwayEnd` rows; "the other end" is a deterministic lookup with zero
   ambiguity for either pair.
2. **Authoritative external evidence** (Massport, §7): names the
   reciprocal end of each pair, matching RWI's topology-derived value
   exactly, both times.

## 9. BOS reconciliation verdict

- **Does NASR establish current physical EMAS presence?** Yes — `direct_strong`, current (2026-08-06) cycle, at `04L` and `15R`.
- **Does Massport establish the corresponding public/protected direction?** Yes — Tier 1, fetched directly twice in this task, names `22R` and `33L`.
- **Are `04L` vs `22R` and `15R` vs `33L` actual contradictions?** No.
- **Is each pair the same physical installation viewed from opposite reference frames?** Yes — confirmed by topology (§8) with zero ambiguity, and by two independently fetched Massport statements that agree with each other and with RWI's topology.
- **Is there enough evidence to remove `REVIEW_REQUIRED`?** Yes.
- **Confidence/review outcome justified:** `SAME_PHYSICAL_INSTALLATION`, high confidence — a governed, human-reviewable identity per assertion (161→`04L`/`22R`, 162→`15R`/`33L`), matching the exact reconciliation pattern already used and approved for all 6 MDW/CGF rows (§15).

### Verdict: `BOS_RECONCILABLE`

## 10. Runway 27 separation verdict

Runway 27 Phase 2 is explicitly, textually distinguished from the two
current EMAS systems by Massport's own language (§7), has no NASR
`runway_end` assertion of any kind in RWI today, and is represented solely
by Signal 3 (`status="under construction"`), untouched by anything
proposed in §16–§18. The simulation in §19 independently confirms Runway
27 does not appear in the simulated `current_emas` list for BOS and
remains solely under "Projekt och bevakning"/"Tidslinje."

### Verdict: `BOS_RUNWAY_27_CORRECTLY_SEPARATED` — not conflated with current presence, and the proposed write set (§16) cannot cause it to be, since it only ever touches assertions 161/162.

## 11. ORH evidence findings

Three separate, official Massachusetts Port Authority procurement notices
were found (sources 3–5, §6), all following the identical naming
convention independently confirmed at BOS by a completely different
document type (a press release, not a procurement notice) — meaningful
cross-validation that this is a general Massport convention, not a
one-off. Direct fetch of all three individual notice pages returned HTTP
403 in this task, identically to the prior pilot's own attempt — so these
are relied on as Tier 1 **title/scope-snippet** evidence, explicitly
weaker than a direct fetch, per this task's own tiering discipline (§6).

- **W269-C1**: *"Runway 11 Departure End EMAS Replacement (**Runway 29
  End**)"* — the system protecting Runway 11 departures is physically at
  the `29` end.
- **W269-C2**: *"Replacement of Runway 29 Departure End Engineered
  Material Arresting System (EMAS)"* — names the reciprocal convention
  ("Runway 29 Departure End") for the other bed; its own physical-end
  parenthetical was not captured via snippet in this task (fetch blocked).
- **W306** (bundled contract, re-confirmed from the prior pilot's own
  captured scope text, same 403 block on re-fetch): *"(1) Replace Runway
  29 Departure EMAS (**R/W 11 End**), (2) Replace Runway 11 Departure EMAS
  (**R/W 29 End**)..."* — states **both** directions of the convention in
  one document, for one program.

RWI's own already-ingested evidence corroborates this independently:
Signal 46's `source_notes` (already in the DB, unchanged) reads *"...THIS
GRANT FUNDS A PORTION OF PHASE 2, WHICH CONSISTS OF REPLACING THE
ENGINEERED MATERIAL ARRESTING SYSTEM FOR RUNWAY 29 DEPARTURE END"* — the
same "departure end" framing, describing the same replacement program.

**Lifecycle/replacement story, kept separate from current-presence
evidence (§13):** Spectrum News 1, fetched directly in this task,
confirms: original system installed 2009, 15–20 year design life,
$5,000,000 replacement project sized for Boeing 737-800-class aircraft,
with the airport's own description of a phased sequence ("this is the
airport's active e-mas system on the other side of the runway. This will
be replaced next year"). This matches RWI's own Installation 116 note and
the 5 `replacement`-category USAspending Signals (§13) — none of this was
used to establish current physical presence or the protected-direction
mapping, which rest entirely on NASR (current presence) + topology +
Massport contract-naming evidence (protected direction).

## 12. ORH physical → protected mapping

| Physical (NASR `RWY_END_ID`) | Canonical `RunwayEnd` | Canonical `Runway` | Protected direction (topology) | Confirmed by |
|---|---|---|---|---|
| `11` | id 179 | `11/29` (id 54) | `29` (id 180) | W269-C1 + W306: "Runway 29 Departure EMAS (R/W 11 End)" |
| `29` | id 180 | `11/29` (id 54) | `11` (id 179) | W269-C1 + W306: "Runway 11 Departure End EMAS Replacement (Runway 29 End)" |

Verified through both required channels: canonical topology (one
`Runway`, exactly 2 ends, zero ambiguity) and the official MPA contract
naming convention, which states the mapping explicitly and symmetrically.

## 13. ORH reconciliation verdict

- **Both current systems reconcilable deterministically?** Yes — same topology-and-authoritative-naming pattern as BOS, arguably with stronger textual proof since the contract language states both frames in the same sentence.
- **Current physical presence vs. 2024/2025 replacement lifecycle — kept separate?** Yes. The proposed write (§16) establishes only "NASR reports current presence at physical end X, which protects direction Y" — it does not write, infer, or imply an install year, replacement year, manufacturer, or contractor. `Installation.install_year=2008` (Installation 116) remains an untouched, pre-existing, separately-governed field.
- **Confidence/review outcome justified:** `SAME_PHYSICAL_INSTALLATION`, high confidence, for both assertions (164→`11`/`29`, 165→`29`/`11`).

### Verdict: `ORH_RECONCILABLE`

## 14. Replacement-history separation verdict

The proposed write set (§16) touches only `PhysicalInstallationIdentity`
and `InstallationAssertionLink` rows tied to assertions 164/165 (current
NASR presence). It does not touch `Installation` 35 or 116, does not set
or infer `install_year`/`replacement_year` on any row, and does not touch
any of ORH's 5 USAspending `Signal` rows. The $12,323,296 RWI grant total
vs. the ~$15,000,000 web-reported total (§ unresolved, per the prior
pilot) remains unreconciled and is explicitly out of scope for this
identity-reconciliation task — a funding-total question, not a
runway-end-identity question.

### Verdict: `ORH_REPLACEMENT_HISTORY_CORRECTLY_SEPARATED`

## 15. Comparison with MDW/CGF precedent

All 6 existing `PhysicalInstallationIdentity` rows (4 MDW, 2 CGF) and all
8 `InstallationAssertionLink` rows were re-inspected directly in this
task. Every one uses:

- `create_physical_installation_identity()` +
  `record_reconciliation_decision()`
  (`app/services/physical_installation_reconciliation.py`) — the same two
  functions, unmodified, that this proposal would reuse.
- `outcome="SAME_PHYSICAL_INSTALLATION"`, `actor="human:rwi-owner"`, a
  `reason` string quoting the source and framing ("current-presence only,
  no historical continuity claim").

**One structural difference found, not a blocker:** the *original* MDW
pilot (`scripts/apply_mdw_current_presence_pilot.py`) created identities
with `runway_end` (free-text string) set but `runway_end_id` (canonical FK)
left `NULL`, requiring a *second*, later script
(`scripts/link_physical_installation_identities_mdw_cgf_pilot.py`) to
backfill `runway_end_id` via topology resolution — necessary at the time
because canonical `RunwayEnd` linkage for MDW/CGF wasn't yet fully
resolved when the original pilot ran. For BOS/ORH, canonical topology is
already fully resolved today (§3–4), so a future writer can set both
`runway_end` and `runway_end_id` in the same creation step — a
simplification of the historical two-step pattern, not a new mechanism.

**Critically important precedent finding, direct query result (this
task):** all 8 of MDW/CGF's linked `SourceAssertion` rows (101, 198, 102,
199, 145, 146, 147, 148) still have `runway_end IS NULL` and
`review_state='unreviewed'` **today**, even though they are the most
thoroughly reviewed rows in the entire database. **Publication has never
depended on `SourceAssertion.runway_end` being set for a reviewed
identity — only on the `PhysicalInstallationIdentity` +
`InstallationAssertionLink(outcome="SAME_PHYSICAL_INSTALLATION")` pair
existing** (confirmed directly in `app/static_export/build.py::_current_emas_views()`,
§19). This directly answers the open question below.

Explicit answers:

- **Is a schema change needed?** No.
- **Is a new reconciliation concept needed?** No — `SAME_PHYSICAL_INSTALLATION` is exactly correct for both airports.
- **Can existing identity/link semantics represent BOS/ORH correctly?** Yes, without modification.
- **Should `SourceAssertion.runway_end` also be promoted, or should reviewed identity alone drive publication?** **Reviewed identity alone** — matching the untouched, already-approved MDW/CGF precedent exactly (§ above). Also populating `SourceAssertion.runway_end` for 161/162/164/165 would be redundant with the reviewed identity, would blur the deliberate separation between the mechanical 97-row NASR promotion track and the human-reviewed reconciliation track (`emas-runway-end-semantics-and-nasr-promotion-analysis.md` §17–18), and is not required by the classifier design (`REVIEW_REQUIRED` rows are explicitly excluded from that other writer's scope, by design, permanently — reconciliation, not promotion, is the correct path for these).

### Verdict: `NO_SCHEMA_CHANGE_NEEDED`

## 16. Exact proposed future write set (NOT executed)

| Assertion | Airport | Raw physical | Normalized physical | Canonical `RunwayEnd` id | Derived protected direction | Proposed review outcome | Proposed `PhysicalInstallationIdentity` action | Proposed `InstallationAssertionLink` action | `SourceAssertion.runway_end` populated? |
|---|---|---|---|---|---|---|---|---|---|
| 161 | BOS | `04L` | `4L` | 15 | `22R` | `SAME_PHYSICAL_INSTALLATION` | **CREATE**: `airport_id=3, runway_id=66, runway_end="04L", runway_end_id=15` | **CREATE**: `assertion_id=161, outcome=SAME_PHYSICAL_INSTALLATION, actor="human:rwi-owner", reason=<cites Massport source 2, §6>` | **No** |
| 162 | BOS | `15R` | `15R` | 25 | `33L` | `SAME_PHYSICAL_INSTALLATION` | **CREATE**: `airport_id=3, runway_id=70, runway_end="15R", runway_end_id=25` | **CREATE**: `assertion_id=162, outcome=SAME_PHYSICAL_INSTALLATION, actor="human:rwi-owner", reason=<cites Massport source 2, §6>` | **No** |
| 164 | ORH | `11` | `11` | 179 | `29` | `SAME_PHYSICAL_INSTALLATION` | **CREATE**: `airport_id=44, runway_id=54, runway_end="11", runway_end_id=179` | **CREATE**: `assertion_id=164, outcome=SAME_PHYSICAL_INSTALLATION, actor="human:rwi-owner", reason=<cites W269-C1/W306, §6>` | **No** |
| 165 | ORH | `29` | `29` | 180 | `11` | `SAME_PHYSICAL_INSTALLATION` | **CREATE**: `airport_id=44, runway_id=54, runway_end="29", runway_end_id=180` | **CREATE**: `assertion_id=165, outcome=SAME_PHYSICAL_INSTALLATION, actor="human:rwi-owner", reason=<cites W269-C1/W306, §6>` | **No** |

Recommended improvement over the MDW-era `reason` text convention: embed
the exact source URL and this task's fetch date directly in the `reason`
string (the MDW-era reasons cite "FAA NASR 2026-08-06" but no external
URL) — strengthens future auditability without changing the schema.

## 17. Exact preconditions for the future write

1. **Scope allowlist**: the writer must accept only the 4 assertion ids
   `{161, 162, 164, 165}`, hardcoded, and refuse (fail closed, no partial
   apply) any request touching any other assertion — matching this task's
   explicit BOS+ORH-only scope, and the existing `TARGET_CODES` pattern
   already used by `link_physical_installation_identities_mdw_cgf_pilot.py`.
2. **Idempotency/pre-existence check**: re-verify immediately before
   writing that no `PhysicalInstallationIdentity`/`InstallationAssertionLink`
   already covers any of these 4 assertions (true today; must still be
   true at write time) — a rerun after a successful apply must be a no-op,
   not an error or a duplicate.
3. **Fresh classification re-check**: re-run
   `scripts/analyze_nasr_emas_runway_end_resolution.py` immediately before
   writing; abort if any of the 4 assertions has drifted away from
   `REVIEW_REQUIRED` (e.g., if a later NASR cycle changed the raw value).
4. **Fingerprint guard**: a deterministic SHA-256 fingerprint (same
   pattern as `promote_nasr_emas_runway_end_assertions.py` §6) over the
   sorted `(assertion_id, airport_id, physical_designation,
   canonical_runway_end_id, protected_direction)` tuples for exactly these
   4 rows; required as an explicit `--expected-fingerprint` CLI argument;
   any mismatch aborts before any write or backup.
5. Dry-run by default; a real write requires **both** `--apply` and
   `--allow-database-write`.
6. The writer must bind its session to the explicitly resolved
   `--database` path via its own engine/session factory — **never**
   `app.database.SessionLocal`, regardless of what the `--database`
   argument is (the exact incident documented in
   `docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md` must not
   recur here).
7. Validate the snapshot/fingerprint **before** creating the backup (the
   ordering fix already applied to the NASR promotion writer during its
   own final safety review).
8. Create and independently verify (`PRAGMA integrity_check`, byte
   comparison) a timestamped backup before any write.
9. Fail closed on any data/evidence drift at any stage — no partial
   commit path.
10. Perform the entire 4-row write (2 identities + 2 links per airport)
    as one bounded transaction.
11. Verify the exact changed rows/tables afterward via a full table diff
    against the pre-write backup — expect precisely 4 new
    `PhysicalInstallationIdentity` rows and 4 new
    `InstallationAssertionLink` rows, zero changes anywhere else
    (`InstallationAssertionLink` is DB-enforced append-only via its own
    `before_update`/`before_delete` triggers, §15 — this is not solely the
    writer's own discipline but a structural guarantee).
12. Preserve all 6 existing MDW/CGF `PhysicalInstallationIdentity` rows
    and all 8 existing links byte-for-byte (verified by the same table
    diff).
13. Leave every BGM/LEX/ELM assertion (153, 154, 181, 182, 183)
    completely untouched (verified by the same table diff — these ids are
    outside the §1 allowlist by construction).
14. Idempotent on rerun.

## 18. Simulated public-site result

Simulation methodology (§19 has the exact mechanism and proof of
isolation): a **disposable scratch-directory copy** of the real database
had the exact §16 write set applied via the real, unmodified
`create_physical_installation_identity()`/`record_reconciliation_decision()`
functions, then `app.static_export.build.build_site()` was run directly
against that copy into a scratch output directory. The real database's
SHA-256 hash was re-verified identical immediately after, both by the
simulation script's own built-in assertion and independently in this
report (§2).

**BOS simulated `current_emas`:**

```json
[
  {"primary_label": "Bana 22R", "physical_runway_end": "04L", "protected_runway_direction": "22R",
   "evidence_basis": "reviewed", "evidence_basis_label": "Granskad identitet"},
  {"primary_label": "Bana 33L", "physical_runway_end": "15R", "protected_runway_direction": "33L",
   "evidence_basis": "reviewed", "evidence_basis_label": "Granskad identitet"}
]
```

**ORH simulated `current_emas`:**

```json
[
  {"primary_label": "Bana 11", "physical_runway_end": "29", "protected_runway_direction": "11",
   "evidence_basis": "reviewed", "evidence_basis_label": "Granskad identitet"},
  {"primary_label": "Bana 29", "physical_runway_end": "11", "protected_runway_direction": "29",
   "evidence_basis": "reviewed", "evidence_basis_label": "Granskad identitet"}
]
```

Both match the expected conceptual result exactly (task brief §7):

- **BOS**: both current EMAS systems appear under "EMAS idag" with the
  correct, evidence-matching protected-direction labels (`22R`, `33L`).
  Runway 27 Phase 2 remains solely under "Projekt och bevakning"
  (Signal 3, `status="under construction"`) — confirmed directly in the
  rendered HTML (§19) — not duplicated into "EMAS idag."
- **ORH**: both current EMAS systems appear under "EMAS idag" (`Bana 11`,
  `Bana 29`). The 2024/2025 replacement history remains solely in
  "Projekt och bevakning"/"Tidslinje" (the 5 USAspending grant Signals,
  Installation 116's notes) — not duplicated or reinterpreted as current-
  install-year information.

Checked and confirmed absent in both cases:

- **Duplicate public EMAS items**: none — exactly 2 items per airport, no repeats.
- **Incorrect protected-direction labels**: none — every label matches §9/§13's verdict exactly.
- **Current/project conflation**: none — Runway 27 stays out of "EMAS idag."
- **Historical/current conflation**: none — ORH's replacement story stays out of "EMAS idag."
- **Internal-field leakage**: none — no `PhysicalInstallationIdentity.id`, `InstallationAssertionLink.id`, or assertion id appears in the simulated `data.json`/HTML (spot-checked directly).
- **Canonical runway inventory ("Banor")**: unaffected at both airports — BOS still shows all 6 runway pills, ORH both, in the simulated output.

No UI/export code was changed to produce this simulation — it exercises
the exact, already-existing, already-implemented (but not yet committed)
`current_emas` presentation logic from the prior, separate presentation
slice, fed by simulated reconciliation data on a disposable copy only.

## 19. Simulation mechanism and isolation proof

Script (session scratchpad, not part of the repository):
`...\scratchpad\simulate_bos_orh_reconciliation.py`. Sequence:

1. Computed `real_hash_before` from `data/runway_safe.db` directly.
2. Asserted the simulation database path is textually different from the
   real path.
3. Opened a session against **only** the scratch copy
   (`sim_bos_orh_runway_safe.db`, itself `cp`'d fresh from the real file
   immediately beforehand).
4. Applied the exact §16 write set via the real
   `create_physical_installation_identity()`/`record_reconciliation_decision()`
   functions, resolving each canonical `RunwayEnd` via
   `app.services.runway_identity.normalize_end()` (the same normalization
   `_find_canonical_runway_end()` uses in production) — not hand-typed
   ids.
5. Committed **only** to the scratch copy.
6. Called `app.static_export.build.build_site(scratch_output_dir,
   session=<scratch session>)` — the real, unmodified export function.
7. Closed the session, then recomputed `real_hash_after` from
   `data/runway_safe.db` and asserted it equals `real_hash_before` —
   the script itself would raise `AssertionError` and halt before
   printing success if the real file had changed in any way.

Actual run output confirmed:

```
Simulation writes committed to DISPOSABLE COPY ONLY: ...\sim_bos_orh_runway_safe.db
Simulated static site built at: ...\sim_site
Real DB hash unchanged: 23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559
```

matching §2's hash exactly, confirmed a second time by this report's own
independent check (§2).

## 20. Remaining unresolved questions

- Sources 3–5 (§6, the three MPA contract notices) remain blocked at
  HTTP 403 for direct fetch, both in the prior pilot and in this
  re-verification attempt — relied on as title/scope-snippet evidence
  only. A future task with an alternate retrieval path (e.g., a cached
  version, a FOIA-style direct document request, or a different fetch
  tool) could upgrade this to a full direct-fetch citation.
- BOS's total Runway 27 Phase 2 project cost (~$110–115M, per the prior
  pilot) remains unreconciled to one authoritative figure, and whether
  BOS's 3 separate USAspending grants fund that same project remains
  unresolved — both are out of scope for this identity reconciliation.
- ORH's ~$2.7M gap between RWI's federal-grant total ($12,323,296) and
  the web-reported ~$15,000,000 total replacement cost remains
  unreconciled — out of scope here (§14).
- ORH's exact 2025 completion date and installing contractor for the
  Runway 11-end replacement were not found by either this task or the
  prior pilot.
- Whether MPA Contract W269-C2's own physical-end parenthetical (not
  captured via snippet in this task) exactly matches the topology-derived
  value is not independently confirmed by that specific document — the
  mapping stands on W269-C1 + W306 (which *does* state both directions
  explicitly) plus topology, not on W269-C2 alone.

## 21. Files created/changed

**Created**: `docs/domain/bos-orh-emas-reconciliation-investigation.md`
(this report) only.

**Changed**: none. No production code, model, migration, template, or
test file was modified in this task. The pre-existing uncommitted
presentation-slice changes (`app/static_export/build.py`,
`app/static_export/presentation.py`,
`app/static_export/templates/airport_detail.html`,
`tests/test_static_export.py`,
`docs/product/public-emas-protected-direction-presentation.md`) were left
exactly as found — read from, never edited, in this task.

**Outside the repository (session scratchpad only, not committed, not
part of git status)**: `inspect_bos_orh.py` (read-only DB inspection),
`simulate_bos_orh_reconciliation.py` (isolated-copy simulation),
`sim_bos_orh_runway_safe.db` (disposable database copy), `sim_site/`
(simulated static-export output).

## 22. Tests/checks run

- Full existing test suite: **611 passed**, 0 failed — confirms the
  codebase's baseline state is unchanged (no code was modified by this
  task, so this is a sanity re-confirmation, not a report of new test
  coverage).
- Read-only DB inspection (`inspect_bos_orh.py`) — zero writes; confirmed
  by the unchanged real-DB hash before and after (§2).
- Isolated-copy simulation (§19) — real DB hash re-verified unchanged
  both by the script's own internal assertion and independently by this
  report.
- 6 primary-source web fetches/searches performed and cross-checked
  against the real, current database state (§6–§14).

## 23. git status

```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
	modified:   app/static_export/build.py
	modified:   app/static_export/presentation.py
	modified:   app/static_export/templates/airport_detail.html
	modified:   tests/test_static_export.py

Untracked files:
	docs/domain/bos-orh-emas-reconciliation-investigation.md
	docs/product/public-emas-protected-direction-presentation.md
	docs/research/
	docs/ui/public-ui-hero-integration-report.md
	docs/ui/screenshots/... (pre-existing, unrelated)

no changes added to commit (use "git add" and/or "git commit -a")
```

The 4 modified files and the `public-emas-protected-direction-
presentation.md` doc are identical to the state at the start of this
task (pre-existing, from the earlier, separate presentation slice — still
awaiting its own explicit go-ahead to commit, per that task's own
instruction). This task added exactly one new untracked file: this
report.

## 24. Recommended next action

Both BOS and ORH are evidence-backed reconcilable with high confidence,
using the existing, already-approved MDW/CGF mechanism, with no schema
change. Recommended sequence:

1. **Human review of this report** — specifically §16 (exact write set)
   and §17 (preconditions) — before anything is implemented.
2. **If approved**: implement a small, narrowly-scoped guarded writer
   restricted to exactly the 4 assertions in §16, following the §17
   preconditions and the same dry-run-first, fingerprint-guarded,
   backup-and-verify pattern already proven correct (after its own
   incident and fix) by
   `scripts/promote_nasr_emas_runway_end_assertions.py`. Rehearse against
   a disposable copy first, exactly as done in §19 of this report (which
   already proves the intended write set behaves correctly end-to-end),
   before any real `--apply`.
3. **After a real apply**: no additional presentation work is needed —
   the already-implemented (but still uncommitted) `current_emas`
   pathway in `app/static_export/build.py` will pick up the new reviewed
   identities automatically on the next static-site regeneration, exactly
   as demonstrated by this report's simulation (§18–§19).
4. **Separately, afterward**: apply the same evidence-backed,
   human-reviewed process to BGM (2 assertions), LEX (2 assertions), and
   ELM (1 assertion) — explicitly out of this task's scope, not
   investigated here.

BOS_ORH_EMAS_RECONCILIATION_INVESTIGATION_COMPLETE
