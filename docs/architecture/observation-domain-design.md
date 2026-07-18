# RWI Observation Domain Design

Status

- Document type: Domain Design
- Sprint: Sprint 3 – Observation Domain
- Implementation status: Design only
- Decision status: Proposed

## 1. Purpose and scope

This document defines the Observation domain for RWI. It governs how claims are captured from preserved evidence before any claim is accepted as true.

The knowledge flow is:

```text
PublishingSource
    ↓ publishes
Document
    ↓ supports extraction of
Observation
    ↓ is assessed by
Verification
    ↓ may establish
Fact
    ↓ may support
Intelligence
```

The design supports manual extraction, deterministic importers, OCR, AI-assisted extraction, ambiguous and conflicting claims, incomplete records, historical claims, and future source types. It defines concepts and boundaries only. It does not define database tables, persistence mappings, routes, or user-interface implementation.

## 2. Formal definition

An **Observation** is an immutable, provenance-bound record that a particular claim was extracted from a particular preserved Document at a particular time. It contains the original extracted expression, an optional normalized candidate interpretation, the subject the claim purports to describe, a governed ObservationType, precise evidence location, and extraction metadata.

An Observation asserts:

> “This evidence contains or implies this claim.”

It does not assert:

> “This claim is true.”

### 2.1 What an Observation is

- A claim extracted from exactly one Document.
- A durable record of what the evidence said or appeared to say.
- A carrier of ambiguity, incompleteness, and extraction warnings.
- A candidate input to later Verification.
- Independently retainable even when another Observation duplicates or contradicts it.
- Reproducible: a reviewer can locate the evidence and understand how the claim was extracted.

### 2.2 What an Observation is not

- Not a publisher or information origin.
- Not a publication, file, web page, or snapshot.
- Not a decision that a claim is true or false.
- Not the canonical current state of an Airport, Runway, Project, or installation.
- Not a Fact.
- Not an intelligence conclusion, score, recommendation, forecast, or risk assessment.
- Not a place for untraceable analyst opinion.

### 2.3 Boundaries with adjacent concepts

| Concept | Responsibility | Difference from Observation |
|---|---|---|
| PublishingSource | Identifies the organization or origin that publishes Documents. | It describes provenance at publisher level, not an extracted claim. |
| Document | Preserves one publication or captured content version. | It is evidence from which zero or many Observations may be extracted. |
| Observation | Preserves one extracted claim and its precise provenance. | It records evidence without deciding truth. |
| Verification | Assesses one or more Observations using rules, reviewers, and corroboration. | It owns truth assessment, conflict resolution, and truth confidence. |
| Fact | Represents an accepted, temporally scoped conclusion. | It is an outcome supported through Verification, not raw extraction. |
| Intelligence | Produces explainable decisions, scores, risks, opportunities, and forecasts from Facts. | It interprets accepted knowledge rather than source evidence directly. |

## 3. Core invariants

1. Every Observation traces to exactly one Document.
2. Every Observation uses one governed, versioned ObservationType.
3. Every Observation has one subject reference, resolved or explicitly unresolved.
4. The original extracted value is always preserved and is never overwritten by normalization.
5. A normalized value is a candidate interpretation, not truth.
6. Extraction confidence and truth confidence are separate.
7. Observations do not create, update, or resolve Facts.
8. Conflicting and duplicate Observations may coexist.
9. Corrections create new Observations; they do not rewrite claim history.
10. Superseded, rejected, and source-removed Observations remain traceable.
11. An Observation must be locatable within its Document to the best precision the medium permits.
12. Import retries must not create accidental duplicate Observations.

## 4. Lifecycle and immutability

### 4.1 Recommended lifecycle

The Observation lifecycle should describe capture readiness, not truth:

```text
Captured
   ├──→ Incomplete ──→ Ready for Verification
   └──→ Ready for Verification

Captured / Incomplete / Ready for Verification
   └──→ Superseded
```

Verification later records outcomes such as accepted, rejected, or inconclusive. “Verified” and “Rejected” should not be Observation states because they are conclusions owned by Verification. Interfaces may show an Observation’s latest verification outcome, but that projection must not blur the domain boundary.

### 4.2 State meanings

| State | Meaning |
|---|---|
| Captured | The claim and minimum provenance have been stored, but readiness checks have not completed. |
| Incomplete | The claim is retained but lacks required interpretation, subject resolution, or evidence precision. Warnings explain what is missing. |
| Ready for Verification | Required structural validation has passed. No truth judgment is implied. |
| Superseded | A newer Observation corrects or replaces this extraction record. The earlier record remains visible and traceable. |

An importer may create an Observation directly as Incomplete or Ready for Verification when deterministic validation permits. Captured is useful for manual drafts and multi-step extraction pipelines but should not become a permanent dumping ground.

### 4.3 Immutable content, append-only correction

The claim-bearing payload should become immutable once captured:

- Document association
- ObservationType and its version
- subject as captured
- raw value
- normalized candidate value
- evidence references
- extraction origin

If any of these is wrong, create a correcting Observation and link it to the earlier Observation with a correction or supersession relationship. This preserves auditability and makes importer behavior reproducible.

Administrative workflow state, review assignments, and append-only warnings may evolve, but their history must be recorded. A transition to Superseded identifies a relationship between records; it must not remove the older record.

### 4.4 Deletion policy

Normal deletion should not exist. A source row disappearing, a parser defect, or a rejected claim is not a reason to delete evidence history.

Exceptional removal may be required for legal, security, privacy, or accidental-secret reasons. In that case, quarantine or redact access while retaining a minimal audit tombstone containing identity, reason, actor, and timestamp. This is an operational exception, not the ordinary lifecycle.

### 4.5 Trade-offs

Immutability increases record volume and requires correction-aware queries. In exchange, it provides reliable provenance, repeatable imports, historical reconstruction, and safe conflict analysis. For an intelligence system, those advantages outweigh the storage and query complexity.

## 5. Modelling strategy

### 5.1 Option A: generic triple

Structure: subject, predicate, value.

Advantages:

- Very extensible.
- Easy for importers and AI systems to emit.
- New predicates may not require structural migrations.
- Naturally represents sparse claims.

Disadvantages:

- Weak validation without substantial metadata infrastructure.
- Poor unit and type safety.
- Difficult indexing and analytical queries across heterogeneous values.
- Easy vocabulary fragmentation.
- Generic forms are hard for reviewers to understand.
- Entity references, ranges, measurements, and temporal meaning become ad hoc.

### 5.2 Option B: domain-specific observations

Structure: separate AirportObservation, RunwayObservation, ProjectObservation, InstallationObservation, and similar concepts.

Advantages:

- Strong domain-specific validation.
- Clear persistence and query shapes.
- Straightforward domain-specific user interfaces.
- Good static typing and discoverability.

Disadvantages:

- Every new subject or claim family creates structural work and migrations.
- Shared provenance and extraction behavior is duplicated.
- Importers need many target-specific interfaces.
- Cross-domain review and conflict tooling becomes fragmented.
- AI extraction output must know application-specific structures too early.

### 5.3 Option C: hybrid governed observation

Structure:

```text
Observation
  ├── ObservationType
  ├── ObservationSubject
  ├── typed candidate value
  ├── raw value
  ├── EvidenceReference(s)
  └── ExtractionMetadata
```

Advantages:

- One provenance and lifecycle model.
- A governed vocabulary prevents uncontrolled predicates.
- Typed values support validation and useful indexes.
- New ObservationTypes often require metadata and validators rather than schema changes.
- Importers and AI tools have one submission contract.
- Forms can be generated from ObservationType definitions.
- Domain-specific read models can optimize important queries later.

Disadvantages:

- More infrastructure than a simple triple.
- Cross-type queries still require careful indexing.
- Subject and value polymorphism need strict invariants.
- Governance tooling is required before the vocabulary grows.

### 5.4 Recommendation

Adopt **Option C, the hybrid governed model**.

Use a stable Observation aggregate with a governed ObservationType registry, a subject abstraction, preserved raw text, typed normalized candidates, separate evidence references, and extraction metadata. Add domain-specific projections or indexes only when measured query needs justify them. Do not create a table or class hierarchy for every claim family.

## 6. Observation subjects

### 6.1 Supported subject categories

The subject vocabulary should initially permit:

- Airport
- Runway
- Runway End
- Project
- Document
- PublishingSource
- EMAS Bed or future installation entity when its canonical model is ready
- External unresolved entity

Future subject categories must be registered and admitted by specific ObservationTypes. An ObservationType must never silently accept every subject category.

### 6.2 Resolved subjects

A resolved subject points to one canonical RWI entity and records its subject category. The reference means “this claim purports to describe this entity”; it does not mean the claim is true.

Subject resolution is distinct from verification. Matching `STP` to a canonical Airport can be deterministic and high confidence while the claim “STP has two EMAS systems” remains unverified.

### 6.3 Unresolved external subjects

Unresolved imported entities must be allowed. Rejecting them would either lose claims or force importers to create premature canonical entities.

An unresolved subject should preserve:

- subject category claimed by the source, if known;
- external identifier type and exact raw identifier;
- source namespace, such as FAA airport identifier;
- raw name or label;
- optional contextual attributes such as city, state, or country;
- optional candidate canonical entity;
- resolution state and resolution warnings.

For example, an FAA import may create an unresolved Airport subject with namespace `FAA airport identifier`, raw identifier `STP`, and label `St. Paul Downtown`. A later identity-resolution process may link it to an RWI Airport without rewriting the Observation’s original external identity.

### 6.4 Subject rules

- Every Observation has exactly one primary subject.
- A subject is either resolved or unresolved, never ambiguously both.
- Resolution changes are audited.
- The original external identifier remains preserved after resolution.
- Relationships between two entities are represented through a relationship-capable ObservationType and an entity-reference value, not by assigning two primary subjects.

## 7. Governed ObservationTypes

ObservationType is a versioned semantic contract, not an unrestricted user-entered string.

### 7.1 Essential definition

Each ObservationType contains:

| Property | Purpose |
|---|---|
| Key | Stable machine key, for example `airport.emas.product`. Keys are never reused with a different meaning. |
| Version | Identifies the semantic and validation contract used when the Observation was captured. |
| Display name | Reviewer-friendly label. |
| Description | Precise definition of what the claim means and does not mean. |
| Expected value type | String, integer, decimal, boolean, date, year, enumeration, identifier, URL, measurement, range, unknown, raw text, or entity reference. |
| Allowed subject types | Explicit subject categories to which the claim may apply. |
| Unit definition | Required unit, permitted units, or dimension for measurements. |
| Enumeration definition | Stable allowed codes and labels when the type is an enumeration. |
| Validation rules | Required form, ranges, patterns, precision, and cross-field constraints. |
| Normalization guidance | Deterministic interpretation rules and ambiguity warnings. |
| Lifecycle status | Proposed, active, deprecated, or retired. |
| Replacement key/version | Migration guidance for deprecated semantics. |

### 7.2 Initial vocabulary examples

| Key | Value type | Allowed subject | Meaning |
|---|---|---|---|
| `airport.emas.product` | Enumeration | Airport | Product family reported as present at an airport, such as EMASMAX or greenEMAS. |
| `airport.emas.system_count` | Integer | Airport | Number of EMAS systems or protected runway ends reported for the airport, according to the type definition. |
| `airport.emas.installation_year_display` | Raw text | Airport | Source display string containing one or more installation/history years whose semantics may be unresolved. |
| `runway_end.emas.installation_year` | Year | Runway End | Candidate initial installation year for one runway-end installation. |
| `runway.length` | Measurement | Runway | Reported physical runway length with explicit unit. |
| `runway.width` | Measurement | Runway | Reported physical runway width with explicit unit. |
| `project.status` | Enumeration | Project | Source-reported project status using a governed mapping. |

The key should encode the semantic subject family, not the source. FAA and an airport master plan should use the same ObservationType when they make semantically equivalent claims.

### 7.3 Versioning

Minor label or documentation corrections do not require a semantic version. Changes to meaning, expected type, unit, subject applicability, or normalization rules require a new ObservationType version. Existing Observations retain the version under which they were validated.

Deprecated types remain readable. A replacement mapping may support new extraction, but historical Observations are not rewritten automatically.

### 7.4 Governance

Only approved ObservationTypes may reach Ready for Verification. Unknown importer keys fail validation or remain in a quarantined import item; they must not create vocabulary dynamically. Vocabulary additions require a definition, examples, validation cases, ownership, and review.

## 8. Observation values

### 8.1 Dual representation

Every Observation preserves two distinct representations:

1. **Raw value:** the exact extracted expression, including punctuation, units, whitespace where meaningful, and source formatting.
2. **Normalized candidate:** an optional typed interpretation produced by a human or extractor.

The raw value is mandatory except when the evidence itself encodes an explicit absence and the ObservationType permits that case. A normalized candidate never overwrites or substitutes for the raw value.

Example:

```text
Raw value:              (2008)
Candidate value type:   Year
Candidate value:        2008
Candidate meaning:      Unknown
Normalization warning:  Parentheses may indicate replacement, not initial installation
```

### 8.2 Required value families

- String
- Integer
- Decimal
- Boolean
- Date
- Year
- Enumeration
- Identifier with namespace
- URL
- Measurement with magnitude and unit
- Range with inclusive/exclusive bounds and unit where relevant
- Unknown with a governed reason
- Raw text
- Future entity reference

“Unknown” is an explicit semantic value, not a null used to hide missing data. Null means no normalized candidate was supplied. Unknown means the source or extractor explicitly indicates that the value is unknown.

### 8.3 Storage strategy comparison

#### JSON only

Flexible and importer-friendly, but weak for constraints, indexing, precision, referential integrity, and consistent queries. Schema validation moves entirely into application code.

#### Typed columns only

Strong constraints and query performance for scalar values, but sparse, awkward for measurements and ranges, and costly when new compound value families appear.

#### Child table per value type

Strong typing and extensibility, but introduces many joins, tables, and lifecycle rules. It is disproportionate for early RWI and complicates generic review workflows.

#### Hybrid typed value

Use a value-type discriminator and typed scalar representation for common queryable values, with a governed structured representation for compound values such as measurements and ranges. Entity references retain real reference semantics. Raw value remains a separate immutable field.

### 8.4 Recommendation

Use the **hybrid typed-value approach**. Optimize common scalar types for validation and querying; use schema-validated structured values only for genuinely compound types. Do not use arbitrary JSON as an escape hatch. ObservationType determines which representation is legal and ensures exactly one normalized representation is populated.

## 9. Provenance and evidence

### 9.1 Document provenance

Every Observation belongs to exactly one Document. Claims assembled from multiple Documents must become multiple Observations. Verification may assess them together later.

The Document supplies publication-level provenance:

- publisher through PublishingSource;
- title and reference;
- revision;
- publication and access dates;
- exact URL where available;
- content hash or snapshot identity where available.

### 9.2 EvidenceReference

Evidence location should be a separate one-to-many concept because one claim may rely on several locations within the same Document, such as a table row plus a footnote. All EvidenceReferences for an Observation must target that Observation’s Document.

EvidenceReference supports:

- page or page range;
- paragraph;
- named section or heading path;
- table identity;
- table row and column;
- image or figure;
- rectangular bounding box with page/canvas dimensions and coordinate system;
- URL fragment;
- HTML selector plus optional DOM/text anchor;
- media timestamp or time range;
- OCR region;
- source record identifier;
- quoted evidence text;
- evidence-location warnings.

### 9.3 What belongs directly on Observation

- Document identity
- ObservationType and version
- subject
- raw claim value
- optional normalized candidate value
- lifecycle state
- capture time
- correction/supersession relationship

### 9.4 What belongs in EvidenceReference

- medium-specific location coordinates;
- quoted supporting excerpt;
- page, table, selector, image, timestamp, or source-row anchors;
- bounding-box coordinate metadata;
- locator precision and warnings.

Raw claim value and evidence excerpt are related but not identical. The raw value is the exact value being asserted; the evidence excerpt provides enough surrounding content to review it.

### 9.5 Locator durability

Evidence locators should combine the strongest available anchors. An HTML selector alone is fragile; preserve selector, URL fragment, surrounding text, and snapshot hash when possible. A PDF bounding box requires page number, coordinate system, page dimensions, and quoted OCR/text content. A source row identifier should include its source namespace and the snapshot in which it appeared.

The review standard is:

> A reviewer can identify the exact preserved Document version, navigate to the smallest practical evidence region, see the original expression in context, and reproduce the candidate normalization.

## 10. Extraction metadata

Extraction metadata describes how reliably RWI captured the evidence. It does not estimate whether the claim is true.

### 10.1 ExtractionRun

A separate ExtractionRun groups work performed by one manual session, parser run, OCR job, AI invocation, or importer batch. It should preserve:

- extraction method: manual, deterministic parser, OCR, AI-assisted, or hybrid;
- extractor/importer identity and version;
- parser and OCR engine versions;
- AI provider, model identifier, and prompt/workflow version where applicable;
- actor, whether human or service identity;
- start and completion timestamps;
- batch identifier;
- configuration fingerprint;
- input Document identity and hash;
- run-level warnings and outcome counts.

### 10.2 Observation-level extraction metadata

Each Observation may additionally record:

- extraction confidence;
- source row identifier;
- field mapping used;
- normalization rule version;
- observation-specific warnings;
- manual edits made before capture;
- link to the ExtractionRun.

### 10.3 Confidence separation

**Extraction confidence** answers:

> “How confident are we that the evidence was read and represented correctly?”

Examples include OCR character ambiguity, uncertain table boundaries, and AI parsing confidence.

**Truth confidence** answers:

> “How confident are we that the claim accurately describes reality?”

Truth confidence belongs exclusively to Verification. An exact deterministic extraction can have extraction confidence 1.0 while the source claim is obsolete or false. Conversely, a credible source may contain text that OCR extracted poorly.

## 11. Duplicates and conflicts

### 11.1 Duplicate observations

Duplicates are semantically equivalent claims about the same subject, using the same ObservationType and normalized candidate, with compatible temporal scope. They may arise from:

- importer retry against the same source record;
- repeated statements within one Document;
- the same claim appearing in multiple Documents.

An accidental retry of the same extraction should be prevented through idempotency. Independent occurrences in different evidence locations or Documents should remain separate Observations because they carry distinct provenance and may provide corroboration.

### 11.2 Conflicting observations

Conflicts are incompatible claims that cannot all describe the same subject under the same semantic and temporal scope.

Example:

- FAA Document: installation year `1999`.
- Airport master plan: installation year `2000`.

Both Observations remain. The Observation domain records neither as the winner. A later Verification compares source authority, publication and effective dates, definition differences, evidence precision, and possible explanations such as construction year versus commissioning year.

### 11.3 Temporal and semantic caution

Values that differ are not automatically conflicts. They may describe different valid times, revisions, units, runway ends, or meanings. Conflict detection may identify candidates, but Verification determines whether a true contradiction exists.

### 11.4 Verification consumption

Verification receives:

- one or more Observations;
- subject-resolution state;
- ObservationType semantics and versions;
- evidence and extraction metadata;
- candidate duplicate/conflict groupings;
- source and Document context.

It may accept, reject, qualify, or leave claims inconclusive and may establish a Fact. It never deletes or rewrites the input Observations.

## 12. First manual workflow

The first manual workflow should remain intentionally narrow:

```text
Document Detail
    ↓
Add Observation
    ↓
Choose governed ObservationType
    ↓
Choose existing subject or create unresolved subject
    ↓
Enter exact raw value
    ↓
Optionally enter normalized candidate
    ↓
Add evidence location and excerpt
    ↓
Review extraction warnings
    ↓
Save as Incomplete or Ready for Verification
```

The form should derive allowed subjects, value editor, units, enumerations, and validation from ObservationType. It should show the selected Document throughout and must not offer Fact creation.

The first version needs one evidence location, with additional EvidenceReferences added later. It should support manual text/page/table locators before advanced PDF selection, OCR bounding boxes, or browser DOM capture.

## 13. Importer interface

### 13.1 Submission envelope

An importer submits an Observation candidate containing:

- Document identity and content/snapshot fingerprint;
- resolved subject or unresolved external subject descriptor;
- ObservationType key and version;
- exact raw value;
- optional typed normalized candidate;
- one or more evidence locators;
- extraction metadata and ExtractionRun identity;
- warnings;
- external record identifier where available;
- importer idempotency key;
- optional source-row state such as present, changed, or removed.

The ingestion boundary validates and records candidates. It does not permit importers to create Facts or truth confidence.

### 13.2 Idempotency and fingerprinting

Each deterministic candidate should have a stable fingerprint derived from semantic inputs such as:

- importer namespace and versioned mapping;
- Document snapshot identity;
- source record identifier;
- ObservationType version;
- subject identity as captured;
- canonical raw value representation;
- evidence locator identity.

The fingerprint prevents retry duplicates within the same evidence occurrence. It must not collapse equivalent claims from different Documents or distinct evidence locations.

### 13.3 Batch imports and retries

An import batch records:

- source snapshot;
- importer version and configuration;
- start/end timestamps;
- candidate, accepted, duplicate, warning, and failure counts;
- item-level results;
- retry lineage.

Retries use the same idempotency keys. A batch may partially succeed, but every item outcome must be recorded. Corrected importer logic creates new Observations that supersede erroneous earlier output; it does not mutate it silently.

### 13.4 Changed and deleted source rows

A changed source row produces a new Observation when claim-bearing content changes and links it to the earlier Observation as a source revision or correction. Cosmetic changes that do not alter the fingerprint may remain idempotent.

A missing source row does not delete its earlier Observation. The new snapshot records the absence, and the system may create a source-removal signal for later review. Absence alone does not prove the earlier real-world claim is false.

### 13.5 Partial failures

One malformed record must not invalidate a complete batch unless atomicity is explicitly required for that importer. Failed candidates remain quarantined with raw input, error details, and retry eligibility. Successful candidates retain their batch lineage.

## 14. Conceptual model

```text
PublishingSource
    └── publishes Document
                     ├── contains zero or many Observations
                     └── is the sole Document for each Observation

Observation
    ├── uses one versioned ObservationType
    ├── describes one ObservationSubject
    ├── preserves one raw value
    ├── may contain one typed normalized candidate
    ├── has one or more EvidenceReferences when ready
    ├── belongs to one ExtractionRun or manual capture context
    ├── may supersede or correct another Observation
    └── may later participate in zero or many Verifications
```

### 14.1 Components

| Component | Purpose and responsibilities | Essential fields | Timing |
|---|---|---|---|
| Observation | Immutable extracted claim and lifecycle anchor. | Identity, Document, ObservationType version, subject, raw value, normalized candidate, state, captured time, supersession link. | First implementation slice, in minimal form. |
| ObservationType | Governs semantics, validation, subject applicability, value type, and units. | Key, version, display name, definition, value schema, allowed subjects, validation, lifecycle. | Registry definition before Observation persistence; minimal initial vocabulary first. |
| ObservationSubject | Represents resolved RWI identity or preserved unresolved external identity. | Subject category, resolved reference or external namespace/id/label, resolution state, contextual identifiers. | Resolved and unresolved minimum in early slices. Advanced matching later. |
| ObservationValue | Preserves raw value and optional typed candidate without claiming truth. | Raw value, type discriminator, typed value, unit/namespace, interpretation warnings. | Common scalar, enumeration, year, identifier, and raw text first; complex types later. |
| EvidenceReference | Locates evidence precisely inside the sole Document. | Locator type, page/section/table/selector/row/timestamp/bounding box as applicable, excerpt, coordinate metadata, warnings. | Simple page/section/table/source-row locators first; OCR and geometry later. |
| ExtractionRun | Groups manual or automated extraction provenance and reproducibility metadata. | Method, actor, tool/model/parser versions, timestamps, batch/config fingerprints, warnings. | Minimal manual context first; full importer batches later. |
| Verification | Assesses truth using one or more Observations. | Out of scope for Observation implementation. | Later domain. |
| Fact | Stores accepted conclusion supported by Verification. | Out of scope for Observation implementation. | Later domain. |

## 15. FAA EMAS example

The FAA Tableau map illustrates why Observation must be separate from Fact.

### 15.1 Product claim

```text
Document:             Preserved FAA EMAS map snapshot
Subject:              Airport STP, resolved or unresolved FAA airport identifier
ObservationType:      airport.emas.product
Raw value:            EMASMAX
Normalized candidate: EMASMAX enumeration value
Evidence:             Tableau mark/source record for STP
```

This remains an Observation because the displayed marker may represent current status at snapshot time, may be airport-centroid data, and does not identify a specific runway end.

### 15.2 System-count claim

```text
Document:             Preserved FAA EMAS map snapshot
Subject:              Airport STP
ObservationType:      airport.emas.system_count
Raw value:            2
Normalized candidate: Integer 2
Evidence:             Tableau tooltip or source record
```

This remains an Observation because the definition of “system” must be confirmed, the associated runway ends are not identified, and another FAA or airport Document may report a different count or effective date.

### 15.3 Historical display claim

```text
Document:             Preserved FAA EMAS map snapshot
Subject:              Airport STP
ObservationType:      airport.emas.installation_year_display
Raw value:            1999 (2008) (2025)
Normalized candidate: None initially
Warning:              Parenthesized-year semantics are undocumented
Evidence:             Exact tooltip/source record and snapshot
```

Candidate year tokens may be extracted as `1999`, `2008`, and `2025`, but the Observation must not label them as initial installation or replacement years without evidence. Later Verification may combine FAA guidance, airport documents, and installation records to establish appropriately scoped Facts.

## 16. Recommended implementation slices

Each slice should be independently reviewable and suitable for one focused commit. These are future implementation recommendations, not work performed by this design sprint.

### Slice 1: vocabulary contract and design fixtures

**Goal:** Establish the governed ObservationType contract and a small approved vocabulary before storing claims.

**Scope:** Domain definitions, validators, initial FAA-oriented types, unit and enumeration rules, and contract tests using in-memory examples.

**Likely files affected:** A future observation domain module, vocabulary configuration or registry, focused domain tests, glossary/architecture documentation.

**Acceptance criteria:** Keys are unique and versioned; value type and subject rules validate; unknown keys fail; initial types have definitions and examples; no persistence or routes exist.

**Risks:** Premature vocabulary breadth and semantics that silently encode source-specific assumptions.

**Out of scope:** Database persistence, manual UI, importers, Verification, and Facts.

### Slice 2: minimal immutable Observation persistence

**Goal:** Persist a claim tied to exactly one Document with one governed type, one resolved subject, raw value, and a small typed candidate set.

**Scope:** Observation identity, Document relationship, lifecycle state, immutable claim rules, scalar/year/enumeration candidates, and migration/contract tests.

**Likely files affected:** Future Observation model/domain files, model exports, one Alembic migration, model contracts, migration tests.

**Acceptance criteria:** Document provenance is mandatory; raw value is preserved; illegal subject/value combinations fail; correction does not mutate claim content; rollback is tested on disposable databases.

**Risks:** Polymorphic subject integrity and choosing persistence details before query requirements are measured.

**Out of scope:** Unresolved subjects, advanced evidence, importer batches, Verification, and Fact creation.

### Slice 3: evidence references

**Goal:** Make each ready Observation reviewable at an exact location in its Document.

**Scope:** Page, page range, section, paragraph, table row/column, URL fragment, HTML selector, source row id, and evidence excerpt.

**Likely files affected:** Evidence domain/model files, migration, validation services, focused provenance tests, read templates.

**Acceptance criteria:** Evidence cannot point outside the Observation’s Document; locator-specific validation works; a reviewer can see the excerpt and locator; no truth decision is present.

**Risks:** Over-general locator structures and fragile web selectors.

**Out of scope:** OCR bounding-box editor, PDF annotation UI, media players, and Verification.

### Slice 4: unresolved external subjects and resolution

**Goal:** Allow import candidates such as FAA `STP` before a canonical entity match exists.

**Scope:** External namespace/id/label preservation, subject-category validation, candidate match, audited resolution, and ambiguity warnings.

**Likely files affected:** Subject domain/model files, migration, resolution service/repository, focused matching tests, review UI.

**Acceptance criteria:** Import does not create canonical Airports implicitly; original external identity survives resolution; one subject cannot be simultaneously resolved and unresolved; resolution is reversible and audited.

**Risks:** False identity matches and generic polymorphic references without referential guarantees.

**Out of scope:** Broad entity deduplication and truth verification.

### Slice 5: read-only Observation display

**Goal:** Expose provenance-bound claims on Document Detail without adding editing.

**Scope:** Document-to-Observation read query, type/value/evidence display, lifecycle and warning presentation, unresolved subject state.

**Likely files affected:** Read route/query, Document template or Observation detail template, minimal Bootstrap styling, isolated route/template tests.

**Acceptance criteria:** All claim and provenance fields render safely; superseded records remain visible; no write controls or Fact language appears; legacy source data is not used.

**Risks:** UI accidentally implying that normalized candidates are verified facts.

**Out of scope:** Creation, editing, Verification, and search.

### Slice 6: first manual capture workflow

**Goal:** Let a reviewer capture one Observation from an existing Document.

**Scope:** Governed type selection, resolved subject selection, exact raw value, optional candidate value, one evidence reference, warnings, and save as Incomplete or Ready for Verification.

**Likely files affected:** Dedicated forms/schemas, command service, route, templates, validation and integration tests.

**Acceptance criteria:** Form behavior derives from ObservationType; invalid combinations fail; successful capture is immutable and traceable; no Facts can be created.

**Risks:** Treating form edits as mutation after capture and building a complex evidence editor too early.

**Out of scope:** OCR, AI, bulk capture, and Verification decisions.

### Slice 7: importer boundary and batch ledger

**Goal:** Accept deterministic Observation candidates safely and idempotently.

**Scope:** Submission envelope, extraction runs, batch/item outcomes, stable fingerprints, retry, partial failures, source-row changes and removals.

**Likely files affected:** Import contracts, command service, batch/extraction models and migration, repositories, importer test doubles, operational documentation.

**Acceptance criteria:** Identical retry creates no duplicate; changed claims create linked new Observations; failures are quarantined per item; source deletion does not delete Observations; importer cannot create Facts.

**Risks:** Incorrect fingerprint boundaries and coupling to Tableau or another first source.

**Out of scope:** A production FAA scraper, scheduling, Verification, and automated Fact promotion.

### Slice 8: advanced extraction and provenance

**Goal:** Extend the stable core to OCR, AI-assisted extraction, geometry, and compound values.

**Scope:** OCR regions, bounding boxes, model/prompt provenance, measurements, ranges, entity-reference values, and reviewer tooling.

**Likely files affected:** Extraction adapters, evidence/value extensions, migrations where necessary, review interfaces, evaluation fixtures.

**Acceptance criteria:** Raw evidence remains preserved; model and prompt versions are reproducible; confidence remains extraction-only; human review can correct through supersession.

**Risks:** Model nondeterminism, sensitive prompt content, coordinate incompatibility, and excessive structured-value flexibility.

**Out of scope:** Autonomous truth decisions.

## 17. Architecture Decision Record recommendation

This design deserves a dedicated ADR because the choice affects every future importer, reviewer workflow, evidence model, Verification process, and Fact lineage.

Suggested title:

> ADR-0003: Governed, Immutable Observation Model

Suggested decision:

> RWI represents extracted claims as immutable Observations tied to exactly one Document. Observations use a governed, versioned ObservationType, one resolved or explicitly unresolved subject, a preserved raw value, an optional typed normalized candidate, separate evidence references, and extraction metadata. Corrections create linked new Observations. Truth assessment belongs exclusively to Verification.

Alternatives to record:

- unrestricted subject-predicate-value triples;
- domain-specific Observation subclasses;
- mutable current-value records;
- direct importer-to-Fact pipelines;
- JSON-only values and evidence;
- disallowing unresolved external subjects.

Consequences to record:

- strong provenance and historical auditability;
- safe preservation of ambiguity and conflict;
- governed vocabulary and validation overhead;
- more records because corrections and repetitions are retained;
- additional complexity for polymorphic subjects and typed values;
- requirement for later resolution, verification, and projection services;
- importers remain simpler and cannot bypass truth assessment.

The ADR should be written and accepted before the first persistence migration, but it is not created by this design task.

## 18. Final recommendation

RWI should adopt a **hybrid, governed, immutable Observation architecture**.

- **Architecture:** one Observation aggregate shared across domains, with governed type semantics rather than unrestricted triples or per-domain subclasses.
- **Subject model:** exactly one resolved canonical subject or one preserved unresolved external subject; resolution is audited and separate from truth verification.
- **Value model:** mandatory immutable raw value plus an optional schema-validated typed candidate; use typed scalar representations and governed structured compound values.
- **Provenance model:** exactly one Document per Observation and one or more separate EvidenceReferences locating the claim precisely within that Document.
- **Lifecycle:** Captured, Incomplete, Ready for Verification, and Superseded; accepted/rejected outcomes belong to Verification. Corrections create new linked Observations. Normal deletion is prohibited.
- **Importer interface:** idempotent candidate submissions containing Document, subject, ObservationType, raw value, candidate value, evidence, extraction metadata, warnings, external identity, and fingerprint. Importers never create Facts.
- **First implementation slice:** approve and test the ObservationType contract and a deliberately small vocabulary before persistence.
- **Future work:** minimal immutable persistence, evidence locators, unresolved-subject resolution, read-only display, manual capture, importer batches, then OCR/AI-assisted extraction. Verification and Fact design remain separate later domains.

This architecture preserves RWI’s central rule: evidence creates claims, Verification evaluates claims, accepted conclusions become Facts, and only explainable Facts support Intelligence.

## Design Freeze Decision

### Decision

The Observation Domain Design is **approved for implementation**.

The final architecture review found no serious contradiction, data-loss risk, traceability failure, subject-resolution blocker, cross-database value-model blocker, or violation of the one-way knowledge flow. The hybrid governed model is sufficiently constrained to begin implementation on SQLite without preventing a later PostgreSQL transition.

### Confirmed principles

The following principles are confirmed and frozen:

1. An Observation is a traceable claim extracted from exactly one preserved Document.
2. Observation does not determine truth.
3. Raw extracted values are always preserved.
4. Normalized values are interpretations and never overwrite raw values.
5. Extraction confidence is separate from truth confidence.
6. Conflicting Observations may coexist.
7. Corrections and supersession preserve history.
8. Importers may create Observation candidates but never Facts.
9. Verification owns acceptance, rejection, and promotion decisions.
10. Knowledge flows in one direction only: PublishingSource → Document → Observation → Verification → Fact → Intelligence.
11. Later layers never rewrite earlier evidence layers. Verification outcomes do not mutate Observations; Facts do not mutate Verifications or Observations; Intelligence does not mutate Facts or their provenance.
12. Every accepted Fact and every Intelligence conclusion must remain reconstructable through an unbroken lineage to preserved Observations, EvidenceReferences, Documents, and PublishingSources.

### Mandatory corrections made

No domain-model correction was required.

One documentation-level correction is frozen here: the implementation discussions in Section 16 describe capability boundaries, but they are not the authoritative delivery order. The finite implementation path below supersedes any earlier slice numbering or ordering. In particular, the ADR is completed before the ObservationType foundation or persistence work begins.

Acceptance, rejection, and promotion are confirmed as Verification responsibilities. “Verified” and “Rejected” remain Verification outcomes and must not be implemented as mutable Observation truth states.

### Explicitly deferred concerns

The following concerns are deliberately deferred and are not blockers for the frozen path:

- advanced OCR bounding-box capture;
- AI-specific review tooling;
- compound value types beyond those required by the first governed vocabulary;
- broad automated entity matching beyond the minimum resolved/unresolved subject contract;
- source-specific production importers and scheduling;
- performance projections or PostgreSQL-specific optimizations before measured need.

These deferrals do not relax the frozen invariants. Raw evidence, one-Document provenance, immutable correction history, subject identity as captured, typed candidate validation, and end-to-end lineage are mandatory from the first slice in which each capability appears.

### Frozen first implementation slice

The first implementation slice is the ADR for the governed immutable Observation model.

Its scope is limited to recording the accepted decision, alternatives, consequences, invariants, ownership boundaries, and one-way lineage rule already defined by this document. It introduces no model, migration, route, template, importer, Verification, or Fact implementation.

It is complete when the ADR is accepted and contains no unresolved decision that would change the ObservationType, subject, value, provenance, lifecycle, or importer contracts frozen here.

### Frozen implementation path

Implementation proceeds in this concrete order:

1. **ADR for the governed immutable Observation model.** Record and accept the architectural decision and frozen invariants.
2. **ObservationType foundation.** Implement the governed, versioned vocabulary contract and the smallest types required by the initial manual workflow.
3. **Observation foundation and migration.** Implement immutable Observation persistence, exactly-one-Document provenance, resolved/unresolved subject integrity, raw values, required typed candidates, correction/supersession links, and minimum evidence/extraction provenance needed for a traceable saved Observation.
4. **Read-only Observations on Document Detail.** Display claims, subjects, raw and normalized representations, evidence, extraction warnings, lifecycle state, and supersession history without implying truth.
5. **Manual Observation creation.** Add the intentionally small capture workflow defined in this document. Creation may produce Incomplete or Ready for Verification Observations but cannot produce Facts.
6. **Importer candidate interface and idempotency support.** Add the governed submission boundary, fingerprints, batch/item outcomes, retry behavior, change handling, and source-row removal handling. Importers cannot create Facts.
7. **Verification foundation.** Implement acceptance, rejection, inconclusive assessment, truth confidence, and conflict evaluation without mutating input Observations.
8. **Fact promotion path.** Permit accepted Verification outcomes to establish traceable Facts while preserving the complete earlier lineage.
9. **End-to-end tests and usability review.** Prove the full path from PublishingSource and Document through Observation, Verification, and Fact, including correction, conflict, idempotent retry, raw-value preservation, and lineage reconstruction. Review the manual workflow for clear separation between extracted claims and accepted truth.

No additional implementation slices may be inserted unless one is strictly required to make this sequence functional while preserving the frozen principles. Such a requirement must be documented as a blocker, not used to expand domain scope.

### Freeze rule

Further Observation-domain design expansion is out of scope until the complete frozen implementation path above has been implemented and reviewed. New optional concepts, speculative abstractions, additional value families, advanced extraction features, and source-specific extensions must wait until the planned Observation, Verification, and Fact path works end to end.

Implementation may clarify physical persistence and interface details, but it must not change the frozen ownership boundaries, provenance rules, immutability rules, confidence separation, subject contract, value preservation, idempotency requirements, or one-way knowledge flow without reopening this design through an explicit architecture decision.
