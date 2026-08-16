# Evidence Identity Slice 5 report — NASR EMAS acquisition

## Evidence-only dry run continuation

The new NASR reader verifies captured SHA-256 and authentic schema, preserves
all ten raw columns, raw row hash, cycle and `APT_ARS.csv:line=N` locator. It
finds 112 candidates, with zero malformed or duplicate identities. It reports
all 112 as skipped for creation because no NASR `Source` row exists and this
slice forbids Source writes; `SourceAssertion.source_id` is required. No domain
table can be mutated. A future apply needs an approved NASR Source first.

## NASR Source provenance continuation

The existing `Source` model is sufficient: its unique `external_id` supplies
governed idempotency without schema change. Read-only dry run finds no existing
NASR Source and proposes exactly one: external ID
`faa_nasr:airport_csv:2026-08-06:06_Aug_2026_APT_CSV.zip`, publisher Federal
Aviation Administration, source type `faa_nasr_apt_ars`, official published
archive URL, document reference archive filename, retrieved date 2026-08-16,
and official dataset summary. It represents the actual FAA artifact, not RWI
interpretation.

With this proposed Source resolved logically, dry run reports 112 candidates,
112 would create, 0 already present, 0 skipped, 0 malformed and 0 duplicate
identities. JFK/BOS/MDW/ORD/LGA/FLL retain the runway-end sets reported above;
these remain informationally compatible with aggregate/historical assertions,
not reconciled identity.

Focused provenance tests pass (15); `git diff --check` passes. No source or
assertion was written. A future apply must create one `sources` row and 112
`source_assertions` rows only; Airports, Runways, Installations, Incidents and
Signals are guaranteed unchanged.

## Existing implementation

The repository already has the correct FAA-specific discovery/parser boundary
in `app/acquisition/faa_runway_ends.py`. It discovers the official FAA NASR
subscription index, chooses the latest effective 28-day cycle, reads that
cycle page, and extracts the FAA/NFDC date-stamped `*_APT_CSV.zip` link. The
archive is ZIP; the expected member is `APT_ARS.csv`. Existing parsing uses
the authentic column names `ARPT_ID`, `RWY_ID`, `RWY_END_ID`, and
`ARREST_DEVICE_CODE`, and deliberately admits only code exactly `EMAS` (not
BAK-12, MA-1A, or other arresting devices). `import_faa_runway_ends.py` then
uses these rows only to enrich legacy Installation rows; that behavior must
not be used for evidence recovery.

## Official source and cycle semantics

Official discovery starts at:

`https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/`

The existing code obtains a dated cycle page at
`https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/{cycle}/`
and extracts its authoritative NFDC `..._APT_CSV.zip` link. Cycle dates are
effective 28-day NASR publications; the actual archive URL must be discovered,
not fabricated.

## Acquired authentic artifact

Approved controlled acquisition retrieved cycle **2026-08-06** from the FAA
discovery flow. The unchanged archive is
`data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip` (8,034,151 bytes;
SHA-256 `dd9768780197ba3e14d447be0be9cf95e1e55e7c56c8ec4dfecf5dc4f4a10ef1`).
Its sidecar metadata records index, cycle page, final URL, cycle, retrieval
time, hash and size. `APT_ARS.csv` exists at the archive root.

Authentic columns are `EFF_DATE`, `SITE_NO`, `SITE_TYPE_CODE`, `STATE_CODE`,
`ARPT_ID`, `CITY`, `COUNTRY_CODE`, `RWY_ID`, `RWY_END_ID`, and
`ARREST_DEVICE_CODE`. It contains 465 rows, 112 explicit EMAS rows, 63 EMAS
airports and 112 distinct `(airport, runway, end)` records. No database write
or SourceAssertion creation occurred.

Priority evidence: JFK 04R/22L ends 04R and 22L; BOS 04L/22R end 04L and
15R/33L end 15R; MDW four ends (04R,22L,13L,31R); ORD 04R/22L both ends; LGA
four ends (04,22,13,31); FLL four ends (10L,28R,10R,28L). These are
runway-end source claims only, not physical reconciliation.

Airports with multiple EMAS ends are: MRY, SFO, SBP, TEX, GON, DCA, BCT, FLL,
EYW, SUA, MDW, ORD, PWK, LEX, LFT, AUG, BOS, ORH, STP, MKC, EWR, TEB, TTN,
BGM, FRG, JFK, LGA, CLE, CGF, ABE, AVP, PVD, HXD and ADQ.

## Artifact status and blocker

The acquisition is now sufficient for a deterministic evidence-only dry run:
112 `runway_end` candidates, each using cycle/archive SHA-256 plus
`APT_ARS.csv:line=N` and raw-row hash. It must preserve all raw columns and
filter only exact `EMAS`. This dry-run/backfill implementation remains the next
step; no real database apply is authorized.

## Controlled acquisition required before continuation

The next live request would first fetch the exact index URL above, then only
the cycle page and the archive URL it explicitly publishes. A controlled
future command should write the unchanged ZIP to
`data/raw/nasr/<discovered-cycle>/<published-filename>`, calculate SHA-256 and
byte size, and retain retrieval time, source URL and cycle in sidecar metadata.
It must not write the development database. The existing in-memory fetcher
does not yet preserve artifact bytes/metadata, so it cannot satisfy Slice 5
without this small acquisition wrapper.

After an authentic artifact is available, one SourceAssertion candidate per
exact EMAS row should use `runway_end`, raw four-column record representation,
artifact SHA-256, deterministic `APT_ARS.csv:line=N` locator, and parser
version. No airport/runway/installations are created or modified. Idempotency
is artifact identity + locator + raw-row hash.

## Public and database boundaries

Raw NASR ZIP/CSV and metadata must stay under non-public `data/raw`; no static
view or `data.json` path reads SourceAssertion or artifacts. A later assertion
apply would write only `source_assertions`; Airports, Runways, Installations,
Incidents, Signals and Sources remain guaranteed unchanged.

## Tests and next step

Existing `test_faa_runway_ends.py` covers discovery errors, EMAS-only filtering
and conservative runway/end enrichment. New authentic-schema, row identity,
raw preservation and multiple-end tests require a checked-in small fixture
derived from an approved authentic artifact. The next action is not a database
apply: request approval for the controlled live artifact acquisition described
above, then inspect its real schema and implement the evidence-only dry run.
