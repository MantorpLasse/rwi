# FAA EMAS import contract

Status: reconnaissance contract only; no production acquisition or parsing is implemented.

Accessed: 2026-07-20.

This contract applies the frozen flow:

```text
FAA source
-> AcquisitionSource
-> AcquisitionRun
-> immutable Snapshot
-> Document
-> Observation
-> Verification
-> Fact
-> Intelligence rule
-> Intelligence
```

No row may enter an airport, runway, runway-end, EMAS, Observation, Verification,
Fact, or Intelligence table directly from FAA material.

## 1. Authoritative source

The selected starting source is the FAA's **Engineered Material Arresting System
(EMAS)** fact sheet and its embedded installation map:

- Publisher: Federal Aviation Administration (FAA), U.S. Department of
  Transportation.
- FAA page: <https://www.faa.gov/newsroom/engineered-material-arresting-system-emas-0>
- Page title: `Engineered Material Arresting System (EMAS)`.
- Page publication date displayed during reconnaissance: 2026-04-24.
- Installation-map target embedded by FAA:
  <https://explore.dot.gov/t/FAA/views/EMASIncidentsandInstallations/Main?%3Aembed=yes&%3Atoolbar=no#2>
- Tableau workbook/view: `EMASIncidentsandInstallations` / `Main`.
- Reference number: none displayed for the fact sheet or map.
- Access date: 2026-07-20.

The FAA Airports engineering page independently describes the fact sheet as the
resource providing the installation map:
<https://www.faa.gov/airports/engineering/incursions_excursions/emas>.
The separately published certified-products PDF is authoritative for certified
manufacturers and models, but is not an installation inventory:
<https://www.faa.gov/sites/faa.gov/files/2022-08/emas_certified_equipment_list_2022_08_17.pdf>.

No linked CSV, spreadsheet, API, or formally versioned installation-list PDF was
found. The canonical installation presentation is therefore mixed: a Drupal HTML
fact sheet (`text/html`) embedding a DOT Tableau visualization (`text/html` plus
stateful Tableau responses). JavaScript is required to operate the map.

The fact sheet is authoritative but is not itself the row dataset. The embedded
workbook is the selected installation source. A production fetch is **not yet
deterministic**: direct CSV/export and view requests were denied by the DOT edge
during this inspection, and Tableau VizQL is stateful and undocumented. There was
no authentication prompt or published API/rate limit. Public availability does not
establish permission for automated extraction or redistribution. Robots policy,
export permission, attribution, cadence, and acceptable request rate must be
confirmed before automation. Until then acquisition is semi-automatic.

Expected cadence is event-driven and unannounced. The page changed recently (the
displayed date is 2026-04-24); no source update schedule is published. A monthly
manual check is a conservative pilot policy, not a claim about FAA cadence.

## 2. Acquisition identity

Register one future AcquisitionSource under the existing FAA PublishingSource:

| Attribute | Contract value |
|---|---|
| key | `faa.emas.installations.tableau` |
| type | `tableau` |
| canonical target | the exact workbook/view URL above, without transient session parameters |
| publisher | FAA |
| workbook | `EMASIncidentsandInstallations` |
| view | `Main` |
| policy | semi-automatic, approved export/capture only |

An AcquisitionRun identifies one attempt, not one airport. It records requested and
final URL, UTC start/end, outcome, HTTP status when available, response headers
excluding secrets, workbook/view, chosen export format, adapter version, operator,
and failure detail. Transient Tableau session IDs must not define source identity.

The acquisition entities do not yet exist in production code or migrations; only
`docs/architecture/acquisition-domain-design.md` defines them. Implementing the
frozen AcquisitionSource, AcquisitionRun, and Snapshot slices is therefore a hard
prerequisite to any real import.

## 3. Snapshot identity and immutability

The first acceptable raw payload is one approved, complete Tableau crosstab/data
export or a documented response capture containing all installation marks. Do not
snapshot a screenshot as row data. Do not treat the FAA Drupal wrapper as the
installation snapshot; it may be acquired separately as its own source/document.

Snapshot identity is `(acquisition_source_id, SHA-256 of exact bytes, byte_size)`.
Preserve exact bytes before parsing, plus reported media type, detected media type,
original filename when supplied, retrieval time, storage identifier, requested and
final URL, and first AcquisitionRun. Bytes and metadata establishing identity are
write-once. Identical bytes for this AcquisitionSource reuse the Snapshot; any byte
change creates another Snapshot. Parsing, normalization, and re-fetch never alter a
Snapshot.

The expected snapshot media type is the media type of the approved export (prefer
`text/csv` with its exact encoding). If the only approved capture is a Tableau
response, retain its server-reported media type and capture method; never relabel it
as CSV. HTML error/login/denial responses fail admission and are not installation
Snapshots.

## 4. Document identity and normalization

Every admitted installation Snapshot creates exactly one Document, and every such
Document references exactly that one Snapshot. Reprocessing returns the same
Document. No fabricated origin is permitted.

Normalize the Document as follows:

| Document field | Value |
|---|---|
| source | FAA PublishingSource inherited from AcquisitionSource |
| title | `FAA EMAS Incidents and Installations — Main` |
| document_type | `Tableau installation export` |
| url | exact final export/capture URL; dashboard URL remains in acquisition metadata |
| published_date | null unless the captured artifact explicitly supplies one |
| accessed_date | date of the first successful capture |
| document_reference | `EMASIncidentsandInstallations/Main` |
| revision | explicit source revision only; never synthesize from access date |
| content_hash | Snapshot SHA-256 if retained for compatibility |
| status | `active`, or `incomplete` when export identity/metadata is ambiguous |

Different bytes produce a new Snapshot and Document. They do not overwrite or
silently revise an earlier Document.

The current Document model has no Snapshot foreign key. The frozen acquisition
transition must be implemented before this invariant can be enforced.

## 5. Source fields

Reconnaissance establishes two distinct source surfaces. Their claims must not be
merged merely because they share a page.

### FAA fact-sheet HTML

Verified fields/content are page title and displayed date; EMAS explanatory text;
manufacturer/product prose (`Runway Safe`, `EMASMAX`, `greenEMAS`); and an
arrestments table with incident month/year, crew/passenger count, and narrative
containing airport name/code/location. Those incident rows are not installation
rows.

### Tableau installation marks

The existing frozen architecture records the visible installation contract as:

- airport name and/or displayed airport identifier;
- geographic map position;
- product text (`EMASMAX` or `greenEMAS`) when displayed;
- system count when displayed;
- installation-year display text, including forms such as
  `1999 (2008) (2025)`;
- Tableau mark/record location sufficient to find the displayed evidence.

The exact underlying Tableau column names, types, row count, stable mark IDs, and
export schema could not be verified because the DOT host denied direct view/export
requests during reconnaissance. They are discovery requirements for the controlled
pilot, not facts to guess. Airport coordinates may position a mark but are not
stable identifiers. The inspected material does not establish a runway designation,
runway-end designation, installation dimensions, installation status vocabulary,
or a per-bed stable identifier.

## 6. RWI target fields

Source fields map only through evidence layers:

| Source value | Immediate target | Later normalized target |
|---|---|---|
| captured payload | Snapshot bytes | none |
| publication identity | Document metadata | none |
| airport code/name | candidate subject locator | matched Airport after review |
| product display | Observation raw/normalized value | Fact `airport.emas.product` after accepted Verification |
| displayed system count | Observation raw/normalized value | Fact `airport.emas.system_count` after accepted Verification |
| displayed year string | Observation raw value | Fact only after semantics are independently verified |
| mark/row identity | Observation `evidence_locator` | provenance only |

`Airport`, `Runway`, `RunwayEnd`, legacy `EmasInstallation`, and `EmasBed` are not
import targets in this slice. Accepted Facts can later drive governed normalization;
the source adapter cannot write those entities.

## 7. Airport matching

Use a staged, reviewable match:

1. Exact FAA location identifier (`faa_code`) when the export explicitly provides it.
2. Exact ICAO code, then exact IATA code, only when explicitly labelled.
3. Normalized airport name plus state/city as a candidate, never automatic identity.
4. Coordinates as corroboration only, within a documented tolerance.

Never reinterpret an unlabelled three-character token as FAA or IATA by assumption.
Zero matches or multiple matches produce an unresolved candidate and no subject-bound
Fact. Preserve every source identifier and the chosen/matched RWI Airport ID in
review metadata. Current airport code columns are indexed but not unique, so the
importer must detect duplicate matches instead of selecting the first row.

## 8. Runway and runway-end matching

The selected Tableau source has not been shown to expose runway or runway-end
identifiers. Geographic airport marks and system counts do not establish which end
is protected. Therefore the first import is airport-scoped only.

If a controlled export later exposes an explicit runway/end, preserve its raw text
and normalize designations (leading zero, `L/C/R`, reciprocal pair) only as a
candidate. Match Runway within the already resolved Airport, then RunwayEnd within
that Runway. Never derive an end from coordinates, count, installation order, or a
runway pair. Ambiguous, obsolete, renamed, or unmatched designations require manual
verification and remain unlinked.

## 9. Observation candidates

One source mark may emit only the currently governed types:

| ObservationType | Raw value | Normalized value | Subject |
|---|---|---|---|
| `airport.emas.product` | exact displayed product | exact governed spelling when unambiguous | Airport |
| `airport.emas.system_count` | exact displayed count | canonical base-10 integer string | Airport |
| `airport.emas.installation_year_display` | exact complete display string | null initially | Airport |

All candidates retain Document ID, stable evidence locator (export row/mark key plus
field), extraction method `tableau_export`, and parser version. Raw strings are never
trimmed away or overwritten. Candidate conversion uses the existing candidate
handoff and creates Observations only. Unsupported fields are recorded in a parser
report and preserved in the Snapshot, not forced into unrelated types.

## 10. Verification expectations

Human review is mandatory for the pilot. The reviewer confirms source payload and
locator, airport identity, literal value, normalization, scope, and whether the mark
is an installation rather than an incident or narrative statement. System counts
must be checked for whether they mean beds, systems, airports, or protected ends.
Year strings remain undecided unless the source supplies definitions. Conflicting
Observations coexist. A changed opinion creates another append-only Verification.

## 11. Fact candidates

Only accepted Verifications may be promoted through `FactPromotionService`.
Eligible airport-scoped candidates are:

- `airport.emas.product` with the accepted displayed product;
- `airport.emas.system_count` only after count semantics are confirmed.

Do not promote `airport.emas.installation_year_display` as an installation or
replacement year. The parentheses in `1999 (2008) (2025)` are not defined by the
source contract. Do not create runway-, runway-end-, manufacturer-, dimension-, or
status Facts without explicit scoped evidence and governed vocabulary. Every Fact
keeps its supporting Verification links and therefore reconstructs the Observation,
Document, and Snapshot chain.

There is currently no governed FactType model/repository: `Fact.fact_type_key` is a
string. This does not block promotion of the three established Observation keys,
but it means callers must use the exact reviewed key and must not invent new ones.

## 12. Intelligence rules that can consume the Facts

The existing `CURRENT_EMAS` rule consumes current accepted
`airport.emas.product` Facts and can derive `CURRENT_EMAS` Intelligence for the
airport subject. It must run only after promotion. No existing rule consumes system
count or the raw year display. No rule may infer manufacturer, runway end,
replacement history, or missing-data conclusions from absence in this map.

## 13. Missing-data handling

Missing values emit no candidate for that type. Empty strings, Tableau nulls,
placeholder dashes, hidden fields, and absent tooltip values are distinguishable in
the extraction report. They never become zero, `unknown`, `false`, a current date,
or a guessed identifier. A row without a resolvable airport is preserved and
reported but does not create a subject-bound Observation. Absence from a later
snapshot is a change for review, not proof that an installation was removed.

## 14. Ambiguity handling

Preserve ambiguous text verbatim and attach a warning. In particular:

- parenthesized years have no assigned initial/replacement/removal semantics;
- a count does not identify individual runway ends;
- map position does not identify a runway end;
- product text does not prove current operational status;
- page-level prose does not automatically apply to every installation mark;
- duplicate airport marks are not merged until their row identity and scope are
  understood.

Ambiguity lowers extraction confidence or yields no normalized value; it is not
resolved by manufacturing a Fact. Manual Verification accepts, rejects, or leaves
the claim undecided.

## 15. Manufacturer handling

Preserve a manufacturer Observation candidate only when the same installation
record explicitly names the manufacturer. The fact sheet's general statement that
Runway Safe is currently the sole FAA-standard manufacturer is separate evidence
and must not be copied onto every map row. Product-to-manufacturer knowledge is not
source attribution. If a mark says only that EMAS exists, no manufacturer candidate
or Fact is created.

The current ObservationType vocabulary has no manufacturer type. If an export
explicitly supplies a row-level manufacturer, the smallest future addition is
`airport.emas.manufacturer` (raw text, Airport scope). A runway-end-specific type
should be added only when explicit runway-end evidence exists. Neither is added by
this slice.

## 16. Idempotency

Acquisition is idempotent by source-scoped byte hash. Document creation is
idempotent by Snapshot's one-to-zero-or-one relationship. Candidate conversion uses
a deterministic fingerprint over Document, ObservationType, subject, exact raw
value, normalized value, evidence locator, extraction method, and parser version in
accordance with the existing candidate handoff. Re-running the same parser against
the same Document returns existing Observations. A parser correction or changed
source creates new/superseding evidence; it never updates an Observation.

## 17. Re-fetch and change detection

Each scheduled or manual attempt creates an AcquisitionRun. Conditional HTTP may be
used only when the host supports validators reliably. A `304` links to the last
Snapshot; it does not create empty content. A `200` response is validated, hashed,
and compared with prior Snapshots for this AcquisitionSource. Byte-identical content
is `no_change`; different bytes create a Snapshot and Document.

After successful parsing, compare deterministic source record keys and field values
between Documents. Classify added, changed, missing, and structurally unparseable
records for review. Do not mutate earlier records and do not equate a missing mark
with retirement. Because Tableau session responses may vary without business-data
change, prefer a stable approved export; otherwise the adapter must document and
test canonical capture boundaries before automation.

## 18. Error handling

Every attempt ends with a durable status such as success, no-change, blocked,
rate-limited, unavailable, invalid response, unsupported format, or failed. Reject
HTML denial/login/error pages, partial exports, unexpected media types, missing
required headers, duplicate source keys, and schema changes before Document or
Observation creation. Preserve sanitized diagnostics on the run; do not preserve an
error page as an installation Snapshot. Retry creates a new linked run with bounded
backoff. A failed run never changes the last good Snapshot, Document, or later
knowledge.

## 19. Provenance requirements

For every imported claim RWI must answer:

```text
Intelligence -> Fact -> Verification -> Observation
             -> Document -> Snapshot -> first/later AcquisitionRuns
             -> AcquisitionSource -> FAA PublishingSource
```

Required evidence includes exact raw bytes and SHA-256, access timestamp, exact
requested/final URLs, workbook/view, export/capture method and version, media type,
source row/mark locator, raw field value, parser version, matching decision, reviewer
decision, and all supersession relationships. Logs may supplement but never replace
database provenance. The Snapshot is the raw evidence; normalized values never
overwrite it or the Observation raw value.

### Existing-data audit

The current visible dataset is not an FAA import:

- `app/seed.py` hand-defines 13 airports, 13 runway specifications, 12 projects,
  and 12 legacy `Source` rows. `seed()` creates those legacy records.
- `scripts/init_db.py` creates schema only and imports no data.
- `alembic/versions/8edd52d34c76_initial_baseline.py` creates the legacy tables but
  inserts no airport rows. `alembic/versions/3f2a1c9d7e6b_expand_source_document.py`
  normalizes eligible legacy Sources into PublishingSources and Documents (merging
  the duplicate FAA report and excluding the internal watch item). Later migrations
  add domain schema/vocabulary, not airports.
- Tests create isolated fixture airports under `tests/`; those are not application
  data.
- The inspected `data/runway_safe.db` contains 13 airports and 13 runways, matching
  `app/seed.py`; zero runway ends, EMAS beds, legacy EMAS installations,
  Observations, Verifications, Facts, or Intelligence rows; and 10 normalized
  Documents.
- The 10 Documents and their PublishingSources are normalized from legacy seeded
  `Source` rows by migration `3f2a1c9d7e6b`. They have no Snapshot origin because
  acquisition persistence is not implemented. Most contain homepage-only URLs and
  are marked `incomplete`.

Classification:

| Rows | Classification | Reason |
|---|---|---|
| 13 Airports, 13 Runways, 12 Projects, 12 legacy Sources | demo/seed | exact hand-authored definitions in `app/seed.py` |
| 10 PublishingSources and 10 Documents | normalized legacy | generated from seeded Sources; no Snapshot lineage |
| vocabulary seed rows | governed seed, not source data | migrations and vocabulary modules |
| FAA-source-backed operational rows | none | no AcquisitionSource/Run/Snapshot exists |
| unknown-origin inspected rows | none identified | database counts and values reconcile to seed paths |

The fields may resemble official facts, but labels such as `official` and URLs do
not make the rows source-backed under the frozen provenance model.

## 20. First pilot airport or smallest representative subset

Use exactly one Tableau installation mark for **St. Paul Downtown Airport (STP)**,
provided the controlled export confirms the displayed `STP` identity, product,
system count, and year string already used in the frozen architecture example. It is
a useful smallest pilot because the FAA identifier can match the seeded Airport,
the installation/product claim is visible, the raw `1999 (2008) (2025)` form tests
lossless ambiguity handling, and one mark keeps provenance review bounded.

The pilot is airport-scoped. It deliberately does not create or match a RunwayEnd,
because the selected source has not demonstrated runway/end data and the current
database has no runway-end rows. Before executing even this pilot:

1. obtain an approved full-data export/capture and record its exact schema;
2. implement the frozen acquisition foundations and Document-to-Snapshot invariant;
3. verify that STP is a distinct installation mark rather than an incident mark;
4. resolve the airport by explicit source code and document the match;
5. create candidates only for source fields actually present;
6. review every candidate manually before any Fact promotion.

This pilot does not satisfy runway-end normalization; it tests and proves that the
source correctly refuses to invent it. A later runway-end pilot requires a separate
FAA or airport publication explicitly naming the runway end, preserved as its own
Snapshot and Document.

### Readiness decision

Recommendation: **semi-automatic acquisition only**. The source is authoritative
and the existing airport-level ObservationTypes can represent its core displayed
claims, but acquisition persistence is not implemented, the Tableau export is not
currently deterministic, the exact schema/stable record key is unverified, and
runway-end evidence is absent. Production automation is blocked until those four
conditions are resolved.
