# RWI Acquisition Domain Design

Status

- Document type: Domain Design
- Sprint: Sprint 4 – Acquisition Domain
- Implementation status: Design only
- Decision status: Approved and frozen

## 1. Purpose

This document defines how external source material enters RWI and becomes preserved, traceable Documents. It closes the architectural gap before the governed knowledge flow:

```text
External material
→ AcquisitionSource
→ AcquisitionRun
→ Snapshot
→ Document
→ Observation
→ Verification
→ Fact
→ Intelligence
```

Acquisition answers where material came from, how it was retrieved, what exact bytes were preserved, and whether retrieval succeeded. It does not interpret claims or decide truth.

The design has one governing provenance rule:

> Every Document has exactly one immutable origin Snapshot.

Manual and automated material use the same acquisition path. This avoids a second provenance mechanism for uploads and ensures every accepted Fact can ultimately be reconstructed from preserved evidence.

## 2. Scope

This design covers:

- registered external and manual acquisition targets;
- historical retrieval attempts;
- immutable raw snapshots;
- content hashing and duplicate handling;
- creation of normalized Documents from snapshots;
- minimal adapter responsibilities;
- the boundary between acquisition and Observation extraction;
- change detection, failure history, and retries;
- security and operational constraints at architecture level;
- validation against FAA Tableau and FAA AIP grant examples.

The design reuses the existing PublishingSource and Document concepts. It does not alter the frozen Observation design.

## 3. Non-goals

This design does not define or implement:

- a generic workflow engine;
- job scheduling or orchestration infrastructure;
- a message bus;
- cloud or local blob-storage implementation;
- source-specific business fields in generic acquisition entities;
- Observation, Verification, Fact, or Intelligence internals;
- truth assessment or automatic Fact creation;
- automatic runway or runway-end assignment;
- production scraping code;
- credentials management infrastructure;
- a generic web crawler;
- Document editing or publication management;
- retention-policy automation.

## 4. Domain terminology

### PublishingSource

The organization or publishing origin responsible for information, such as the Federal Aviation Administration. PublishingSource answers **who published it**.

### AcquisitionSource

A registered target or manual channel through which RWI retrieves material. It answers **where and how RWI obtains it**.

The FAA may be one PublishingSource with several AcquisitionSources: the FAA EMAS Tableau dashboard, the FAA AIP grants index, and a manual FAA PDF upload channel.

### AcquisitionRun

One immutable historical record of an attempt to acquire material from one AcquisitionSource. A run exists whether it succeeds, produces unchanged content, fails, or is blocked.

### Snapshot

The immutable exact bytes successfully preserved from an AcquisitionSource. A Snapshot is evidence, not a parsed or normalized representation.

### Document

The existing normalized representation of one preserved publication or captured source version. A Document has exactly one origin Snapshot and belongs to the same PublishingSource as its AcquisitionSource.

### Adapter

A technology-specific acquisition boundary that retrieves bytes and transport metadata. Examples include HTTP, direct PDF, API, CSV, Tableau, and manual upload adapters.

### Parser or extractor

A component outside acquisition that reads a preserved Snapshot through its Document and submits Observation candidates. It never creates Facts.

## 5. Domain model

```text
PublishingSource 1 ─────── 0..* AcquisitionSource
                                │
                                │ 1
                                ▼
                         0..* AcquisitionRun
                                │
                                │ 0..1 resolved content
                                ▼
                         0..* Snapshot
                                │
                                │ 0..1 normalized publication
                                ▼
                         0..1 Document
                                │
                                ▼
                         0..* Observation
```

The diagram expresses these rules:

- each AcquisitionSource belongs to exactly one PublishingSource;
- each AcquisitionRun belongs to exactly one AcquisitionSource;
- a run may resolve to no Snapshot on failure or one Snapshot on preserved success/no-change;
- the same Snapshot may be referenced by multiple runs from the same AcquisitionSource when content is unchanged;
- each Snapshot belongs to exactly one AcquisitionSource and records the first run that captured it;
- a Snapshot may have no Document or exactly one Document;
- each Document has exactly one Snapshot origin;
- one Document may support many Observations under the frozen Observation design.

## 6. Entity responsibilities

### 6.1 AcquisitionSource

Purpose: define one stable, governed acquisition target or manual channel without containing retrieved data.

Essential semantics:

| Field or property | Responsibility |
|---|---|
| Stable key | Immutable machine identifier, never reused for another target. |
| Display name | Human-readable target name. |
| Acquisition type | Governed adapter family such as HTTP page, PDF, CSV, API, Tableau, or manual upload. |
| Canonical URL | Stable configured target where applicable; absent only for a manual channel without a remote target. |
| PublishingSource | Required publisher relationship. |
| Active flag | Controls whether new runs are permitted; never removes history. |
| Expected media type | Optional allowlisted response expectation. |
| Adapter configuration | Versioned, non-secret technical configuration only. |
| Acquisition policy | Governed limits such as permitted redirects, size ceiling, retry class, and minimum interval. |
| Manual flag | Distinguishes actor-supplied material from remote retrieval. |
| Created/updated timestamps | Administrative history; key and publisher identity are not silently repurposed. |

Configuration must not include source-specific business values such as runway, grant amount, airport code, or EMAS product. Those belong in preserved content and later Observations.

Secrets are never stored in configuration metadata. Configuration may hold a secret reference understood by deployment infrastructure.

### 6.2 AcquisitionRun

Purpose: preserve one attempt and its transport outcome.

Essential semantics:

| Field or property | Responsibility |
|---|---|
| AcquisitionSource | Required source configuration used by the run. |
| Started/completed timestamps | Exact attempt interval. |
| Status | Final acquisition outcome; running is temporary until finalized. |
| Requested URL | Exact URL requested for this attempt. |
| Final URL | Redirect-resolved URL when available. |
| HTTP status | Transport result when HTTP applies. |
| Response headers | Sanitized provenance-relevant headers; secrets and cookies excluded. |
| Reported media type | Media type returned by the remote system. |
| Adapter identity/version | Reproducibility metadata. |
| Error category/details | Safe diagnostic summary for failed or partial runs. |
| Snapshot reference | Optional content resolved by the run. |
| New-snapshot flag | Whether this run first created that Snapshot or matched existing content. |
| Initiating actor | Human or service identity. |
| Retry lineage | Optional earlier run that this run retries. |

Runs are append-only historical records. Final run results are never overwritten by later attempts. Corrections to administrative annotations are audited rather than replacing transport history.

Runs are ordered deterministically by start timestamp and stable run identity. Discovered child links preserve source order where meaningful and use normalized URL plus discovery identity as a stable tie-breaker.

### 6.3 Snapshot

Purpose: preserve immutable raw evidence.

Essential semantics:

| Field or property | Responsibility |
|---|---|
| AcquisitionSource | Required source namespace for content identity. |
| First AcquisitionRun | Run that first preserved this Snapshot. |
| SHA-256 | Hash of exact raw bytes. |
| Byte size | Exact raw byte count. |
| Media type | Validated media type used for preservation. |
| Original filename | Sanitized supplied/downloaded filename when present. |
| Storage reference | Opaque reference to immutable content storage. |
| Retrieved timestamp | Time the bytes were first captured. |
| Storage/integrity metadata | Information needed to verify the stored object. |

The content hash does not replace byte preservation. Hash, size, and stored bytes must agree when integrity is checked.

Snapshot does not contain parsed airport, grant, project, runway, or EMAS fields. Adapter-specific acquisition metadata belongs to the run. Publication metadata belongs to Document. Extracted claims belong to Observation.

## 7. Relationships and cardinalities

1. One PublishingSource may have zero or many AcquisitionSources.
2. One AcquisitionSource belongs to exactly one PublishingSource.
3. One AcquisitionSource has zero or many AcquisitionRuns.
4. One AcquisitionRun belongs to exactly one AcquisitionSource.
5. One AcquisitionRun resolves to zero or one Snapshot.
6. One Snapshot belongs to exactly one AcquisitionSource.
7. One Snapshot records exactly one first-capture AcquisitionRun.
8. One Snapshot may be referenced by many later no-change AcquisitionRuns from that same AcquisitionSource.
9. One Snapshot creates zero or one Document.
10. One Document originates from exactly one Snapshot.
11. A Document’s PublishingSource must equal its origin Snapshot’s AcquisitionSource PublishingSource.
12. One Document may support zero or many Observations.

The model intentionally does not support many-to-many Snapshot–Document provenance. A compound API response, CSV, or Tableau export remains one preserved Document; its individual rows or marks become evidence locations for multiple Observations. If a source exposes separately retrievable publications, each publication is acquired as its own Snapshot and Document.

## 8. Immutability rules

- AcquisitionSource keys are immutable and never reused.
- Completed AcquisitionRuns are immutable historical attempts.
- Snapshot bytes, hash, size, source association, first-run association, retrieval time, and storage identity are immutable.
- Snapshot content is never replaced when an external source changes.
- A changed source creates a new Snapshot.
- A no-change run links to the existing Snapshot and does not alter it.
- A Document never changes its origin Snapshot.
- Manual uploads are preserved byte-for-byte before normalization.
- Parsers and later knowledge layers never modify Snapshots.
- Observation, Verification, Fact, and Intelligence layers never rewrite acquisition history or evidence.

Operational quarantine may block access to malicious, legally restricted, or accidentally sensitive content. Quarantine does not silently delete the Snapshot record or provenance. Any exceptional physical removal requires a durable audit tombstone and is outside ordinary acquisition behavior.

## 9. Provenance rules

Every Document traces through exactly one path:

```text
Document
→ Snapshot
→ AcquisitionSource
→ PublishingSource
```

The Snapshot also traces to the first AcquisitionRun, while every later run that encountered identical content remains independently visible.

The provenance chain must answer:

- who published the material;
- which configured target or manual channel supplied it;
- who or what initiated acquisition;
- when acquisition occurred;
- which URL was requested and which final URL responded;
- which adapter/version performed acquisition;
- what media type and transport metadata were observed;
- which exact bytes were preserved;
- whether those bytes were new or matched earlier content;
- which Document normalized those bytes.

Manual upload follows the same chain. A manual AcquisitionSource is registered for one PublishingSource; upload creates an AcquisitionRun and Snapshot before Document creation. There is no separate “manual origin” entity.

Existing Documents created before acquisition provenance is implemented require an explicit transition plan. They must not be assigned fabricated Snapshots. Before the exactly-one-origin invariant is enforced for legacy rows, each Document must be matched to preserved material or explicitly captured through a governed legacy/manual acquisition run. Unresolved legacy origin remains visible as incomplete provenance until resolved.

## 10. Acquisition lifecycle

```text
Registered AcquisitionSource
        ↓
Start AcquisitionRun
        ↓
Validate policy and target
        ↓
Adapter retrieves or accepts bytes
        ↓
Validate status, redirects, size, media type, and content
        ↓
Hash exact bytes
        ↓
Match existing Snapshot for this AcquisitionSource?
   ├── yes → link run to existing Snapshot → no_change
   └── no  → preserve immutable Snapshot → success
        ↓
Optional explicit Document creation
        ↓
Parser/extractor may submit Observation candidates later
```

A run is recorded before external I/O or upload acceptance begins. It always reaches a terminal status, including after process recovery. Successful preservation does not imply successful parsing, Document creation, or truth verification.

Discovery is acquisition metadata, not automatic evidence interpretation. For example, the AIP index run may record discovered PDF URLs in deterministic order. The PDFs use one registered FAA AIP PDF AcquisitionSource, while each exact PDF URL is recorded by its own AcquisitionRun and preserved as its own Snapshot.

## 11. Snapshot and deduplication behavior

### Content identity

Snapshot content identity is scoped to AcquisitionSource and determined by exact SHA-256 plus byte size, with stored-byte verification available to guard against corruption or collision concerns.

Scoping prevents identical bytes retrieved through different AcquisitionSources from losing distinct origin. Physical storage may deduplicate identical blobs internally, but domain-level Snapshots remain separate when AcquisitionSource differs.

### Unchanged content

When a run retrieves byte-identical content from the same AcquisitionSource:

- no new Snapshot is created;
- the run links to the existing Snapshot;
- the run status is `no_change`;
- current response metadata remains on the new run;
- the existing Snapshot and Document remain unchanged.

Conditional HTTP responses such as `304 Not Modified` may link to the last known Snapshot only when validators and source policy establish that relationship. A 304 does not create empty snapshot content.

### Changed content

Any byte change creates a new Snapshot, even if the parsed business values appear unchanged. Normalization, whitespace cleanup, OCR, or decompression must not modify the preserved raw bytes used for identity.

### Duplicate Documents

A Snapshot can create at most one Document. Repeating Document creation returns the existing Document. A no-change run therefore reuses both Snapshot and Document.

Different Snapshots may represent different revisions or captures of what humans regard as the same publication. They produce distinct Documents because Documents are versioned preserved evidence. Publication-level reconciliation may link related revisions later without merging their origins.

### Not every Snapshot becomes a Document

A Snapshot may remain without a Document when:

- the response is safely preserved for diagnosis but invalid or unsupported;
- publication metadata is insufficient;
- a reviewer has not approved a manual capture;
- the material is an acquisition artifact that is not admitted as normalized evidence.

The absence of a Document is explicit and auditable. It does not permit parsing into Facts or bypassing the Document layer.

## 12. Document creation rules

1. Document creation requires one validated Snapshot.
2. The Snapshot must not already have another Document.
3. The Document PublishingSource is inherited from the AcquisitionSource and cannot disagree with it.
4. The Document origin Snapshot is immutable.
5. The Document title, reference, revision, publication date, and document type are normalized metadata; original bytes remain in Snapshot.
6. The canonical source target remains on AcquisitionSource. The exact requested and final URLs remain on AcquisitionRun. Document URL uses the exact publication URL when known, not merely a publisher homepage.
7. Document accessed date reflects the first acquisition that produced its origin Snapshot.
8. `active` is used when the exact publication and required metadata are sufficiently identified.
9. `incomplete` is used when evidence is preserved but publication identity or metadata remains incomplete, such as a homepage-only link or ambiguous Tableau capture.
10. Later source unavailability creates a failed/unavailable AcquisitionRun; it does not automatically withdraw, delete, or rewrite the preserved Document.
11. Other existing Document statuses are assigned only according to their established Document semantics, not inferred from acquisition failure.

### Manual uploads

Manual material uses a manual AcquisitionSource associated with the declared PublishingSource. The upload action creates an AcquisitionRun containing actor, supplied filename, declared media type, and validation outcome. Accepted bytes create a Snapshot; the Snapshot may then create a Document under the same rules as remote material.

The uploader’s declaration is acquisition metadata, not truth. Publisher selection and publication metadata must remain reviewable.

### Indexes and linked publications

An acquired HTML index page is one Snapshot and may become one Document. Linked PDFs are not folded into that Document. Each retrieved PDF becomes its own Snapshot and Document. Discovery linkage records which index run found each target without changing the independent origin of each PDF Document.

## 13. Adapter boundary

An acquisition adapter accepts a governed AcquisitionSource configuration and run context, then returns one acquisition result.

Minimum adapter result responsibilities:

- requested and final location;
- transport outcome and safe headers;
- reported media type and filename where available;
- exact byte stream, subject to limits;
- adapter identity and version;
- structured acquisition warnings or failure category;
- optionally, deterministically ordered discovered resource locations.

Supported adapter families may include:

- HTTP web page;
- direct PDF download;
- CSV download;
- API response;
- Tableau export or approved response capture;
- manual upload.

Adapters may implement authentication, redirects, conditional requests, and transport-specific validation according to policy. They do not:

- create Documents directly;
- parse aviation claims;
- create Observations, Verifications, Facts, or Intelligence;
- assign airports, runways, or runway ends as truth;
- interpret ambiguous dates or source-specific semantics;
- decide that a missing record represents deletion or retirement.

Tableau remains a specialized adapter because its VizQL mechanism is stateful and undocumented. Its adapter may preserve an approved export or captured response plus workbook/view identifiers, but those identifiers remain acquisition metadata rather than generic model fields.

## 14. Parsing and Observation boundary

Acquisition ends when raw bytes and acquisition metadata are preserved and optionally normalized into a Document.

Extraction begins from the preserved Document and its Snapshot:

```text
Adapter
→ AcquisitionRun
→ Snapshot
→ Document

Parser/extractor
→ reads Document and Snapshot
→ submits Observation candidates
```

A parser must record its own identity, version, evidence locator, source record identifier, raw extracted value, normalized candidate, warnings, and extraction confidence according to the frozen Observation design.

Parser output never alters the Snapshot or Document. A new parser version may produce new or corrected Observation candidates with idempotency and supersession lineage. Parser failure belongs to extraction history, not to the completed AcquisitionRun, unless acquisition itself failed to preserve usable bytes.

Neither adapter nor parser may create Facts. Verification exclusively owns truth decisions and Fact promotion.

## 15. Change-detection rules

### Byte-level change

- Exact hash and byte-size match within one AcquisitionSource means unchanged content.
- Any byte difference means a new Snapshot.
- HTTP validators may avoid a transfer but do not replace content hashing for newly received bytes.

### Record-level change

New, changed, and missing source records are detected by a versioned parser comparing deterministic source record identities across Snapshots. They are extraction results, not Snapshot mutations.

- New row: submit new Observation candidates.
- Changed row: submit new candidates and preserve correction/supersession lineage where the claim changed.
- Missing row: record absence/change candidate for review; do not delete earlier Observations or infer real-world retirement.
- Reordered row: do not treat as changed when stable record identity and content are unchanged.

### Structure change

A parser that cannot find expected fields or violates its input contract records a structure-change warning or parser failure against its extraction run. The raw Snapshot remains valid evidence even when parsing fails.

### Source unavailability

Unavailable, blocked, permission-denied, or rate-limited responses create failed AcquisitionRuns without a new Snapshot. They do not alter the most recent preserved Snapshot or Document.

## 16. Failure and retry semantics

### AcquisitionRun terminal statuses

| Status | Meaning |
|---|---|
| `success` | New valid bytes were preserved as a new Snapshot. |
| `no_change` | The run established that content matches an existing Snapshot. |
| `partial_success` | Primary bytes were safely preserved but an ancillary acquisition responsibility, such as discovery metadata, was incomplete. |
| `unavailable` | Target could not be reached or supplied no retrievable material. |
| `blocked` | Policy, robots/usage restrictions, or access controls prohibited acquisition. |
| `invalid_response` | Transport succeeded but response failed validation. |
| `unsupported_format` | Bytes were preserved or rejected according to policy, but the format is not supported for Document creation. |
| `permission_failure` | Authentication or authorization failed. |
| `rate_limited` | Remote service requested reduced request frequency. |
| `failed` | Adapter or unexpected acquisition failure not represented above. |

`running` may exist as a non-terminal operational state. Recovery must finalize abandoned runs visibly rather than deleting them.

Parser failure is an extraction outcome and does not rewrite a successful AcquisitionRun. If an adapter cannot determine or preserve the response format at all, the acquisition outcome is `invalid_response`, `unsupported_format`, or `failed` as appropriate.

### Retry policy

- Retries create new AcquisitionRuns linked to the earlier run.
- A retry never overwrites the earlier error.
- Only transient classes such as unavailability, rate limiting, and selected server failures are automatically retryable when policy permits.
- Permission failures, policy blocks, invalid responses, and unsupported formats require correction or review before retry.
- Remote `Retry-After` and configured minimum intervals are respected.
- Retry counts and backoff are bounded.
- A successful retry may create a Snapshot or resolve to an existing one.

## 17. Security and operational constraints

### Credentials and secrets

- Store only secret references in AcquisitionSource configuration.
- Never persist authorization headers, cookies, tokens, or passwords in run response headers or errors.
- Logs and diagnostics must redact sensitive query parameters and headers.
- Acquisition adapters receive only credentials required for that source.

### Network safety

- Validate configured schemes and destinations.
- Defend against SSRF by blocking loopback, link-local, private, metadata-service, and otherwise prohibited destinations unless explicitly governed for deployment.
- Revalidate redirect targets and limit redirect depth.
- Resolve DNS safely and account for rebinding.
- Manual URL input must not bypass registered AcquisitionSource policy.

### Usage policy and rate limiting

- Review robots directives, terms of use, licensing, attribution, and agency policies before automation.
- Respect configured request intervals, server rate limits, and retry instructions.
- Tableau or other undocumented mechanisms default to semi-automatic acquisition until use and export expectations are approved.
- Acquisition history records policy blocks rather than circumventing them.

### Content safety

- Enforce per-source and global byte-size limits before full acceptance.
- Validate actual content against reported type and allowlists.
- Reject or quarantine executable, malformed, encrypted, or suspicious content according to policy.
- Treat PDFs, archives, office documents, HTML, and parser inputs as untrusted.
- Do not execute active content during preservation.
- Archive expansion, if later supported, requires separate size and traversal safeguards.

### Integrity and retention

- Compute SHA-256 over exact bytes during controlled ingestion.
- Verify byte size and storage checksum after write.
- Storage references must be opaque and resistant to path traversal.
- Snapshots are write-once and integrity-checkable.
- Backups must preserve content and provenance metadata together.
- Quarantine and exceptional removal are audited.

### Licensing and attribution

- Capture source URLs, publisher, access time, and available license/attribution notices.
- Preservation and use permissions are reviewed per AcquisitionSource.
- Public accessibility is not assumed to grant unrestricted automated extraction or redistribution rights.

## 18. FAA EMAS Tableau example

### Registration

- PublishingSource: Federal Aviation Administration.
- AcquisitionSource key: stable RWI key for the FAA EMAS Tableau dashboard.
- Acquisition type: Tableau.
- Canonical target: workbook `EMASIncidentsandInstallations`, view `Main` on `explore.dot.gov`.
- Policy: semi-automatic until an approved export mechanism and use expectations are confirmed.

### Run and preservation

One AcquisitionRun records requested/final URL, Tableau workbook and view identifiers in adapter metadata, adapter version, timestamps, transport outcome, and sanitized response metadata. An approved raw export or captured Tableau response is hashed and preserved unchanged as a Snapshot.

Byte-identical reacquisition links to the existing Snapshot as `no_change`. A changed response creates a new Snapshot and, when admitted, a new Document. The Document is `incomplete` when exact export identity or metadata remains ambiguous.

### Extraction

A versioned Tableau parser may later produce airport-level Observation candidates such as:

- `airport.emas.product` with raw `EMASMAX` or `greenEMAS`;
- `airport.emas.system_count` with the raw displayed count;
- `airport.emas.installation_year_display` preserving strings such as `1999 (2008) (2025)` exactly.

Airport identifiers and source record locators are preserved. Ambiguous year tokens are not promoted to installation/replacement meanings by acquisition or parsing. The adapter and parser do not assign runway or runway end when the source does not provide that evidence.

No candidate becomes a Fact without Verification. Runway-end assignment, historical-year semantics, removed markers, and conflicts with airport publications remain Verification concerns.

## 19. FAA AIP Grants example

### Grants index

The FAA AIP grants page is registered as an HTTP-page AcquisitionSource associated with FAA. A run preserves the exact HTML as a Snapshot and may create one index-page Document.

The HTTP adapter records linked PDF locations as deterministically ordered discovery results. Discovery does not treat link text or page content as accepted grant facts.

### Grant PDFs

Each grant publication or PDF is retrieved independently:

```text
Discovered PDF target
→ its own AcquisitionRun
→ its own PDF Snapshot
→ one normalized Document
```

PDF bytes, filename, final URL, media type, size, hash, and access timestamp are preserved. A replaced PDF at the same URL creates a new Snapshot and Document. An unchanged PDF reuses the existing Snapshot and Document while retaining the new no-change run.

### Extraction

A later PDF parser may submit Observation candidates for:

- airport received funding;
- project description;
- grant amount;
- runway safety work;
- possible EMAS installation or replacement.

Each raw value and page/table evidence location is preserved. Project classification, EMAS interpretation, and airport/runway identity remain candidate interpretations. No Observation becomes a Fact without Verification.

If a previously linked PDF disappears from a later index, RWI records the index change and acquisition failure. It does not delete the PDF Snapshot or infer that the grant was withdrawn.

## 20. Recommended implementation slices

Acquisition implementation is deferred until the active Sprint 3 Observation path permits it. When acquisition implementation begins, use these finite slices:

### Slice 1: AcquisitionSource foundation

Create the governed source configuration model, PublishingSource relationship, stable keys, acquisition types, active state, safe configuration, migration, repository, and contract tests. No adapters.

### Slice 2: AcquisitionRun foundation

Add append-only attempt history, terminal statuses, transport metadata, adapter version, retry lineage, and isolated migration/model tests. No external I/O.

### Slice 3: Snapshot foundation

Add immutable snapshot metadata, source-scoped content identity, hash/size/storage integrity rules, run linkage, and duplicate-content tests using temporary storage only.

### Slice 4: Manual upload origin

Implement the unified manual AcquisitionSource → AcquisitionRun → Snapshot path with validation and provenance. Do not create Documents yet.

### Slice 5: HTTP/PDF acquisition adapter

Implement one constrained HTTP adapter with redirect, media-type, size, hashing, policy, retry, and failure behavior. No crawling and no source-specific parsing.

### Slice 6: Document creation from Snapshot

Add the exactly-one Snapshot origin relationship, publisher consistency, idempotent one-to-one creation, Document status assignment, and legacy transition safeguards.

### Slice 7: Read-only acquisition history

Display AcquisitionSource, runs, failures, snapshots, hashes, and Document origin without editing or scheduling controls.

### Slice 8: FAA AIP index and PDF acquisition

Register the FAA sources, preserve the index, discover PDFs deterministically, and acquire each PDF independently. Do not extract grant claims in this slice.

### Slice 9: FAA Tableau semi-automatic adapter

Add an explicitly approved semi-automatic Tableau capture path that preserves raw material and workbook/view/adapter metadata. Do not infer runway-end facts.

### Slice 10: Observation candidate integration

Connect parsers to the already implemented governed importer candidate interface. Prove raw-value preservation, evidence locators, parser idempotency, and the prohibition on direct Fact creation.

Each slice should be one focused change with isolated migrations/tests where relevant. Do not begin a later slice while another implementation phase is active.

## 21. Explicit architectural decisions

1. PublishingSource identifies who published material; AcquisitionSource identifies where and how RWI obtains it.
2. AcquisitionSource, AcquisitionRun, and Snapshot are the only new core acquisition entities.
3. Manual and automated acquisition use the same AcquisitionRun and Snapshot path.
4. Every Document has exactly one origin Snapshot.
5. A Snapshot creates zero or one Document; one Snapshot never fans out into multiple Documents.
6. A Document never aggregates provenance from multiple Snapshots.
7. Identical bytes are deduplicated at domain level only within one AcquisitionSource.
8. Every attempt creates an immutable AcquisitionRun, including failures and no-change results.
9. Changed bytes always create a new Snapshot; previous Snapshots are never replaced.
10. Source unavailability and missing records never delete evidence or prove real-world withdrawal.
11. Adapters acquire and preserve; parsers extract Observation candidates.
12. Adapters and parsers never create Facts.
13. Snapshot bytes are the raw evidence; Document is their normalized publication identity.
14. Compound payloads remain one Document, with rows/marks addressed by Observation evidence locators.
15. Existing Documents require genuine captured origins; fabricated backfill Snapshots are prohibited.
16. Scheduling, workflow engines, message buses, and storage infrastructure are outside the domain design.

## 22. Open questions

There are no architecture-blocking open questions.

Implementation must select a concrete immutable storage mechanism and finalize the legacy Document transition procedure within the frozen rules before enforcing the origin constraint. Those are implementation decisions, not reasons to expand the domain model.

## Architecture Freeze

The Acquisition Domain Design is approved for implementation.

The architecture is frozen around three acquisition entities—AcquisitionSource, AcquisitionRun, and Snapshot—and the rule that every Document has exactly one origin Snapshot. Manual and automated material follow the same provenance path. Acquisition preserves evidence; extraction submits Observation candidates; only Verification may support Fact promotion.

Further Acquisition architecture expansion is prohibited. New ideas, optional entities, source-specific concepts, orchestration mechanisms, storage products, scheduling designs, and additional adapter families go to the backlog. Only a demonstrated implementation blocker that prevents the frozen model from preserving evidence or one-way lineage may justify reopening this design.

After this document, active work returns to **Sprint 3, Slice 2**. Acquisition implementation must not begin while the Sprint 3 implementation path is active. Only one implementation phase is permitted at a time.
