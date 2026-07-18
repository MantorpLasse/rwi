# ADR-0003

## Governed Immutable Observation Model

Status

Accepted

Date

2026-07-18

---

## Context

RWI transforms preserved aviation evidence into verified and explainable knowledge through a one-way flow:

```text
PublishingSource
→ Document
→ Observation
→ Verification
→ Fact
→ Intelligence
```

Documents may contain incomplete, ambiguous, conflicting, historical, erroneous, or repeated claims. Independent Documents may make equivalent claims or contradict one another. Manual, deterministic, OCR, and AI-assisted extraction methods may also differ in how reliably they capture those claims.

RWI therefore requires a governed layer between Document and Fact that preserves exactly what evidence claimed without treating the claim as accepted truth. The full contract and implementation freeze are defined in the [Observation Domain Design](../architecture/observation-domain-design.md).

## Decision

RWI will use a governed, hybrid, immutable Observation model.

### Observation boundary

An Observation is a traceable claim extracted from exactly one preserved Document. It records what evidence states or appears to state; it does not decide whether the claim is true.

Verification exclusively owns:

- acceptance;
- rejection as a truth decision;
- truth confidence;
- conflict resolution;
- promotion into accepted Facts.

The Observation lifecycle may identify structural incompleteness or extraction errors. “Verified” and “Rejected” are not Observation truth states. They are Verification outcomes.

### Governed hybrid structure

Every Observation uses a governed, versioned ObservationType. Unrestricted free-text predicates are not allowed.

The model combines:

- one shared Observation concept;
- governed ObservationType definitions;
- an explicit resolved or unresolved subject representation;
- a mandatory preserved raw value;
- an optional typed normalized candidate value;
- precise evidence references where the Document medium permits them;
- extraction metadata, including extraction confidence and warnings.

This structure provides shared provenance and lifecycle behavior without permitting unrestricted EAV semantics or requiring a separate Observation model for every claim category.

### Values and confidence

The raw extracted value is mandatory and immutable. A normalized value is an interpretation candidate and must never replace or overwrite the raw value.

Extraction confidence describes confidence that evidence was captured and represented correctly. Truth confidence describes confidence that a claim reflects reality. Extraction confidence belongs to Observation extraction metadata; truth confidence belongs exclusively to Verification.

### Provenance and evidence

Every Observation retains provenance to exactly one Document and, where available, a precise location inside that Document. Evidence location may identify a page, section, table position, source record, selector, timestamp, or other medium-appropriate locator defined by the domain design.

Claims assembled from multiple Documents become multiple Observations. Verification may assess them together later.

### Immutability, correction, and conflict

Observation claim payloads are immutable after capture. Corrections create new Observations linked through correction or supersession lineage. Earlier Observations remain visible and traceable.

Conflicting Observations may coexist. Observation does not choose a winner or rewrite conflicting evidence. Verification later determines whether claims conflict and how they contribute to an accepted conclusion.

Later knowledge layers never rewrite earlier evidence layers. Verification does not mutate Observation evidence; Facts do not mutate Verifications or Observations; Intelligence does not mutate Facts or their provenance.

### Importers and reconstruction

Importers may submit governed Observation candidates. They require deterministic idempotency and fingerprinting so retries do not create accidental duplicates. Importers may never create Facts directly.

Every accepted Fact and future Intelligence conclusion must remain reconstructable through an unbroken lineage:

```text
Fact
→ Verification
→ Observation
→ Document
→ PublishingSource
```

### Initial implementation boundary

The first implementation remains deliberately small. This ADR records the frozen architecture but does not require every deferred extraction, value, matching, Verification, or Intelligence capability to be implemented in the first Observation slices.

## Alternatives Considered

### Generic triple or EAV-style model

A generic subject-predicate-value model offers flexibility and a simple submission shape for importers.

It is rejected as the primary RWI model because it encourages unrestricted predicates, weak validation, inconsistent semantics, difficult querying, and poor reviewer interfaces. Adding governance and typed validation sufficient for RWI would recreate much of the selected hybrid model without making those constraints explicit.

### Domain-specific Observation tables

Separate tables or concepts for installation, runway-length, project-status, EMAS, and other Observation families offer strong claim-specific typing.

They are rejected as the primary model because they create table proliferation, repetitive provenance and lifecycle infrastructure, migration overhead, fragmented importer interfaces, and excessive implementation work whenever a new claim category is introduced.

### Governed hybrid model

The governed hybrid model is selected because it combines shared provenance and lifecycle infrastructure, controlled ObservationType semantics, typed normalization where useful, preserved raw evidence, and extensibility without unrestricted EAV behavior or domain-table proliferation.

## Consequences

### Positive consequences

- Source claims and raw extracted values are preserved.
- Every Observation remains traceable to one exact Document and its PublishingSource.
- Conflicting and repeated evidence can coexist without premature resolution.
- Manual and automatic extraction use the same governed claim contract.
- Deterministic importer reruns can be idempotent.
- Extraction quality remains separate from truth assessment.
- Accepted knowledge can be reconstructed from preserved evidence.
- ObservationType governance provides controlled extensibility without a table per claim category.
- Append-only correction and supersession preserve history.

### Costs and trade-offs

- The model requires more entities and relationships than a simple claim table.
- ObservationType definitions and versions require active governance.
- Importers require carefully defined fingerprints and source-record identities.
- Corrections append records rather than updating claim payloads in place.
- Complete provenance queries require additional joins.
- Subject and typed-value invariants require explicit validation.
- Verification logic must exist before any Observation can support an accepted Fact.
- Storage and review interfaces must account for superseded, duplicate, incomplete, and conflicting Observations.

## Deferred Concerns

The following are explicitly outside this slice:

- advanced OCR workflows;
- AI extraction tooling;
- compound Observation types beyond the first governed vocabulary;
- broad generic entity matching;
- production FAA or other external importers;
- premature PostgreSQL-specific optimization;
- Intelligence modelling;
- advanced Verification policy;
- broad redesign or expansion of the frozen Observation domain.

These deferrals do not relax the decisions in this ADR. Further design expansion remains out of scope until the frozen Observation, Verification, and Fact implementation path is complete and reviewed end to end.
