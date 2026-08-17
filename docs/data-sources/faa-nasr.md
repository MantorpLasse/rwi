# FAA NASR (National Airspace System Resources) — 28-Day Subscription

## 1. Source overview

The FAA's National Airspace System Resources (NASR) 28-Day Subscription is
a periodically-republished set of CSV/data products describing U.S.
airports, runways, navigation aids, airspace, and related aeronautical
facilities. RWI uses one product from this subscription — the Airport
(APT) CSV package — as its authoritative source for U.S. canonical runway
and runway-end inventory.

## 2. Publisher

Federal Aviation Administration (FAA), via its National Flight Data
Center (nfdc.faa.gov) for archive hosting.

## 3. Dataset/product

NASR 28-Day Subscription, Airport (APT) CSV package — one ZIP archive per
cycle, named `<DD>_<Mon>_<YYYY>_APT_CSV.zip` (e.g.
`06_Aug_2026_APT_CSV.zip`). The archive contains multiple CSV files; RWI
currently uses three:

- **`APT_RWY.csv`** — canonical runway (pair) inventory: one row per
  physical runway.
- **`APT_RWY_END.csv`** — canonical runway-end inventory: one row per
  directional threshold of a runway.
- **`APT_ARS.csv`** — arresting-system (EMAS) presence evidence: one row
  per runway end with a reported arresting system.

**`APT_RWY.csv`/`APT_RWY_END.csv` are the canonical runway/runway-end
inventory.** `APT_ARS.csv` is a separate concern — EMAS/arresting-system
presence evidence — and is never treated as an inventory source. A
runway/end physically existing and a runway/end currently reporting an
arresting system are different claims, even though both come from the
same NASR cycle. See
[`docs/domain/canonical-runway-runway-end-design.md`](../domain/canonical-runway-runway-end-design.md) §4.

## 4. Why RWI uses it

FAA NASR is the authoritative published source for U.S. airport runway
geometry and designation. RWI uses it for two distinct purposes:

- **Canonical runway/runway-end inventory** (`APT_RWY.csv`/`APT_RWY_END.csv`) —
  populating the `Runway`/`RunwayEnd` domain model deterministically.
- **EMAS/arresting-system presence evidence** (`APT_ARS.csv`) — an
  independent evidence source for `SourceAssertion` rows, unrelated to
  inventory population.

NASR does not cover non-U.S. airports; those require separate,
not-yet-documented authoritative sources (see the handbook's future-sources
list).

## 5. Discovery endpoint

- **Index**: `https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/` — lists the current and next (preview) cycle, each as its own dated sub-page.
- **Cycle page pattern**: `https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/<YYYY-MM-DD>/` — one cycle's own page lists that cycle's per-product download links, including the APT CSV package.
- **Archive host**: `nfdc.faa.gov`, under `/webContent/28DaySub/extra/`.

This chain — index → cycle page → archive link — was independently
verified live (not merely inferred from local metadata) as part of
[`docs/domain/nasr-acquisition-preserve-design.md`](../domain/nasr-acquisition-preserve-design.md).
For the `2026-08-06` cycle specifically: the index still listed it as the
"Current" cycle, the cycle page's own APT CSV link was byte-for-byte
identical to the locally recorded `final_archive_url`
(`https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip`),
and the archive host returned the expected byte count live.

**Operational note**: FAA's edge may reject requests carrying a generic
tooling/library User-Agent. RWI's acquisition code sends an explicit,
identifiable, browser-compatible User-Agent rather than a default one —
see §9.

**FAA does not publish a checksum for the archive.** No SHA-256 or other
hash is available from FAA/NFDC to compare against; the hash RWI records
is computed locally from the downloaded bytes, not confirmed against any
FAA-published value.

## 6. Acquisition method

Discover the current cycle and archive URL, download to a temporary file,
validate it, hash it, and preserve it only after validation succeeds — no
step writes to final storage until every earlier step has succeeded. See
§9 for the implementation and §10 for the exact preservation rules.

## 7. Raw storage location

`data/raw/nasr/<YYYY-MM-DD>/<original-filename>`, preserving the FAA's own
filename unchanged. Currently preserved:

```
data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip
```

## 8. Provenance metadata

A sidecar JSON, `<archive-filename>.metadata.json`, sits next to the
archive. The currently preserved sidecar for `2026-08-06` records:

| Field | Value |
|---|---|
| `source_index_url` | `https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/` |
| `cycle_page_url` | `https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-08-06/` |
| `final_archive_url` | `https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip` |
| `nasr_cycle` | `2026-08-06` |
| `retrieved_at` | `2026-08-16T06:51:07.9581445Z` |
| `sha256` | `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1` |
| `byte_size` | `8034151` |

Verified directly against the local file: the archive is exactly
`8,034,151` bytes with SHA-256 `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1`,
matching the sidecar exactly. All three required members
(`APT_ARS.csv`, `APT_RWY.csv`, `APT_RWY_END.csv`) are present in the
archive.

The current sidecar predates the versioned schema in §9's implementation
(no `schema_version`/`publisher`/`dataset`/`acquisition_method` fields);
a future acquisition via the new module would add those, and both forms
remain readable by the same verifier — see
[`docs/domain/nasr-acquisition-preserve-slice-report.md`](../domain/nasr-acquisition-preserve-slice-report.md) §12.

**`ORIGINAL_ACQUISITION_ACTOR: unknown`.** Repository evidence (a
committed report narrating a controlled, approval-gated retrieval,
timestamped shortly before the commit that used the artifact) establishes
*how* this specific archive was acquired, but not *who or what process*
ran the retrieval. This is not treated as a defect: actor identity is not
a required provenance field (see the handbook's provenance-requirements
principle), and no attempt is made here to guess between a human, an
agent, or another process. Full investigation:
[`docs/domain/nasr-provenance-acquisition-audit.md`](../domain/nasr-provenance-acquisition-audit.md).

## 9. Validation rules

Applied, in order, before anything is written to final storage:

1. Every URL used (index, cycle page, archive, and any redirect hop) must
   be `https://` on an approved host (`www.faa.gov` or `nfdc.faa.gov`
   only) — anything else is rejected before a request is made.
2. The archive is downloaded to a temporary file first, never streamed
   directly into final storage.
3. Byte size and SHA-256 are computed from the downloaded file on disk,
   not from the in-flight response.
4. The file must be a structurally valid ZIP (not corrupt/truncated).
5. `APT_ARS.csv`, `APT_RWY.csv`, and `APT_RWY_END.csv` must all be
   present as archive members.

Only after all five pass is the file moved into `data/raw/nasr/<cycle>/`
and the sidecar written.

## 10. Parsing/ingestion use

Reading the preserved archive is separate from acquiring it:

- `app/evidence/nasr_apt_rwy.py` reads `APT_RWY.csv`/`APT_RWY_END.csv`
  for canonical inventory planning.
- `app/evidence/nasr_apt_ars.py` reads `APT_ARS.csv` for EMAS presence
  evidence.
- `app/services/runway_inventory.py` turns preserved rows into a
  deterministic plan (`plan_airport_inventory()`), classifies airports as
  clean/blocked (`resolve_us_clean_batch()`), and — only when explicitly
  applied — writes `Runway`/`RunwayEnd` rows (`apply_plan()`).

Both readers independently re-verify the archive's SHA-256 against its
sidecar before trusting any row from it.

## 11. Refresh/update behavior

NASR republishes on a 28-day cycle. The discovery logic
(`discover_apt_csv_url()` / `discover_nasr_apt_archive()`) always resolves
"the latest cycle effective as of today," not a hardcoded date, so it
needs no code change to pick up a new cycle. A future scheduled check
could run on that same ~28-day cadence; no such scheduler exists yet —
this document does not specify one.

## 12. Human-review boundary

**Acquisition may**: discover, download, validate, hash, preserve the raw
archive, and write its provenance sidecar.

**Acquisition must not**: update `Airport`, `Runway`, `RunwayEnd`,
`Installation`, or `Signal`; create evidence assertions; reconcile
identities; or change public export output. `app/acquisition/nasr_apt_csv.py`
has no database dependency at all — no session or model import anywhere
in that module.

Turning a preserved cycle into database changes is a separate, explicitly
approved workflow: deterministic planning
(`plan_airport_inventory()`/`resolve_us_clean_batch()`) proposes changes,
a human reviews the proposal, and only then is `apply_plan()` invoked —
backed up, re-verified immediately before write, and all-or-nothing.

**Current ingestion status** (detail in the linked reports, not
duplicated here): the canonical `Runway`/`RunwayEnd` model is implemented;
the MDW/CGF pilot and a clean, deterministic 63-airport U.S. batch have
both been applied and verified against the real database. 13 U.S.
airports remain outside that batch — 12 blocked by non-runway NASR
records (helipads/special-use strips) mixed into `APT_RWY.csv`, and 1
lacking a usable FAA/IATA/ICAO identifier. See
[`docs/domain/canonical-runway-us-wide-dry-run-report.md`](../domain/canonical-runway-us-wide-dry-run-report.md)
and
[`docs/domain/canonical-runway-us-clean-batch-report.md`](../domain/canonical-runway-us-clean-batch-report.md)
for the full breakdown. Public runway-inventory presentation ("Banor")
remains suppressed until canonical coverage and public presentation are
separately reviewed and approved.

## 13. Known limitations

- The original acquisition actor for the currently preserved `2026-08-06`
  archive is unknown (§8) — not a blocking issue, but worth stating
  plainly rather than guessing.
- FAA does not publish a checksum for this archive; RWI's hash is a
  locally-computed value only, not independently confirmed by FAA.
- 12 U.S. airports currently require a reviewed rule for handling
  non-runway (helipad/special-use) rows before they can join the clean
  batch — deliberately not auto-handled yet.
- NASR does not cover non-U.S. airports; those need separate authoritative
  sources not yet identified/documented.
- Public runway-inventory presentation remains suppressed pending a
  separate review, independent of how complete canonical ingestion is.

## 14. Relevant code

- `app/acquisition/faa_runway_ends.py` — original FAA NASR
  index/cycle/archive discovery logic (`discover_apt_csv_url()`), plus
  EMAS-specific in-memory fetch/parse.
- `app/acquisition/nasr_apt_csv.py` — reusable discover → download →
  validate → hash → preserve → sidecar module (no database dependency).
- `scripts/acquire_nasr_apt_csv.py` — CLI wrapper; discovery-only dry run
  by default, `--acquire` required for a real download/preserve.
- `app/evidence/nasr_apt_rwy.py` / `app/evidence/nasr_apt_ars.py` — SHA-256-verifying readers of the preserved archive.
- `app/services/runway_inventory.py` — deterministic inventory
  planning/classification/apply.
- `scripts/apply_canonical_runway_inventory_us_clean_batch.py` — the
  approved, applied clean-batch apply script.

## 15. Relevant reports/history

- [`docs/domain/canonical-runway-runway-end-design.md`](../domain/canonical-runway-runway-end-design.md) — canonical model design, including the `APT_RWY`/`APT_ARS` boundary.
- [`docs/domain/nasr-provenance-acquisition-audit.md`](../domain/nasr-provenance-acquisition-audit.md) — provenance investigation for the preserved `2026-08-06` artifact.
- [`docs/domain/nasr-acquisition-preserve-design.md`](../domain/nasr-acquisition-preserve-design.md) — live source verification and acquisition-module design.
- [`docs/domain/nasr-acquisition-preserve-slice-report.md`](../domain/nasr-acquisition-preserve-slice-report.md) — acquisition module implementation report.
- [`docs/domain/canonical-runway-us-wide-dry-run-report.md`](../domain/canonical-runway-us-wide-dry-run-report.md) — full U.S.-wide dry-run classification.
- [`docs/domain/canonical-runway-us-clean-batch-report.md`](../domain/canonical-runway-us-clean-batch-report.md) — applied clean-batch report.
