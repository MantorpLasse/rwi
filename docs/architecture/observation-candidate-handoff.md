# Observation Candidate Handoff Contract

Status

- Document type: Implementation-boundary contract
- Domain status: Design only; no persistent Candidate entity approved
- Applies to: Future extractors, parsers, and controlled importer execution

## 1. Purpose and boundary

An **Observation Candidate** is temporary input produced while extracting possible claims from one existing Document. It carries only enough information to validate and create an immutable Observation. It is not preserved evidence, verified truth, or part of the authoritative knowledge chain.

```text
Document
  → extractor/parser
  → temporary Observation Candidate
  → generic validation and conversion
  → immutable Observation
```

The authoritative flow remains unchanged:

```text
PublishingSource → Document → Observation → Verification → Fact → Intelligence
```

A Candidate is an in-memory value or a serialized transfer structure scoped to an importer/parser execution. It has no database table, identity, lifecycle, CRUD interface, or relationships from Verification or Fact. It does not duplicate AcquisitionRun or Snapshot responsibilities.

## 2. Minimum candidate contract

| Field | Required | Contract |
|---|---:|---|
| `document_id` | Yes at the handoff boundary | Identifies exactly one existing Document. An extractor operating in a pre-resolved Document context may omit it from each emitted item internally, but the converter must receive the resolved context and must reject conflicting item-level identity. |
| `observation_type_key` | Yes | Stable, unique governed vocabulary key. Database IDs must not be embedded in source-specific extractors. |
| `raw_value` | Yes | Exact source expression, including meaningful whitespace, punctuation, capitalization, and line breaks. |
| `normalized_value` | No | Machine- or human-produced interpretation candidate; never a verified or accepted value. |
| `extraction_confidence` | No | Confidence that extraction correctly captured what the Document expressed, in the inclusive range `0.0–1.0`. It is not truth confidence. |
| `evidence_locator` | No | Generic location within the Document, such as a PDF page, section, table row, JSON path, HTML selector, Tableau mark, or source-record identifier. |
| `extraction_method` | No | Short description of how extraction occurred, supporting troubleshooting and reproducibility. |
| `extractor_version` | No | Version of the extracting implementation or configuration. It does not imply an Extractor database entity. |
| `source_record_key` | No | Transient, source-supplied identity hint for batch diagnostics and retry analysis. It is not mapped to Observation and must not be assumed globally unique. |

The contract contains no airport, runway, installation, manufacturer, grant, or project fields. Source-specific concepts are represented as values governed by an ObservationType.

Candidate values are untrusted source-derived input. Implementations must use a fixed allowlist matching this contract; source input cannot select model attributes, construct SQL, or introduce additional persistence fields.

## 3. Ownership and resolution

### Document context

The importer or extractor operates for a known Document. Document resolution occurs before persistence and must produce exactly one existing Document. Candidate input cannot create a Document, PublishingSource, Snapshot, or Acquisition record and cannot reassign the converter to another Document. A missing Document, or a candidate identifier conflicting with the execution context, rejects that candidate before any Observation is created.

### ObservationType

`observation_type_key` resolves through the governed, unique ObservationType key. Unknown and inactive types are rejected. Importers cannot create or reactivate ObservationTypes. Vocabulary changes follow their own governance process; database IDs remain an application-level persistence detail.

### Layer responsibilities

| Layer | Responsibility |
|---|---|
| Extractor/parser | Reads material associated with the known Document and emits Candidates. It does not determine truth or access Verification or Fact. |
| Candidate validator/converter | Validates the generic contract, resolves Document and ObservationType, evaluates batch duplicates, and constructs allowlisted Observation creation input. |
| ObservationRepository | Persists immutable Observations. It contains no FAA-, PDF-, CSV-, Tableau-, HTML-, or other source-specific parsing logic. |
| Future human review | May confirm temporary Candidates before conversion, but uses the same contract and conversion rules. It does not turn Candidates into truth. |

## 4. Validation and conversion

Validation proceeds in this order:

1. Validate the Candidate shape and reject unknown fields where the transport supports strict shape validation.
2. Resolve the execution's Document and reject missing or conflicting Document context.
3. Resolve the active ObservationType by key.
4. Require a `raw_value` containing at least one non-whitespace character while preserving the submitted value unchanged.
5. Validate optional values and metadata.
6. Evaluate exact duplicates within the current batch and report potential persisted matches.
7. Construct an Observation using only the approved mapping.
8. Persist the approved set and commit its transaction.
9. Return structured per-candidate results plus a batch commit outcome.

Transport-level newline normalization may occur before handoff when unavoidable. No later layer may silently trim meaningful whitespace, change capitalization, reinterpret raw text, or replace `raw_value` with `normalized_value`. Empty optional strings become null according to the existing Observation conventions.

`extraction_confidence` may be null. A supplied value must be a finite decimal from `0.0` through `1.0`, inclusive. Malformed, non-finite, below-range, and above-range values reject the Candidate before persistence.

### Candidate-to-Observation mapping

| Candidate | Observation | Conversion |
|---|---|---|
| Resolved `document_id`/Document context | `document_id` / `document` | Use the server-resolved existing Document only. |
| `observation_type_key` | `observation_type_id` / `observation_type` | Resolve the active governed type by unique key. |
| `raw_value` | `raw_value` | Preserve unchanged after transport handling. |
| `normalized_value` | `normalized_value` | Preserve as an optional interpretation candidate. |
| `extraction_confidence` | `extraction_confidence` | Convert only after finite inclusive-range validation. |
| `evidence_locator` | `evidence_locator` | Preserve as optional generic text. |
| `extraction_method` | `extraction_method` | Preserve as optional extraction metadata. |
| `extractor_version` | `extractor_version` | Preserve as optional extraction metadata. |
| `source_record_key` | No Observation field | Retain only in the execution report or importer-local retry context. |

Conversion creates only Observation records. It cannot create Verification, Fact, Intelligence, Document, PublishingSource, AcquisitionSource, AcquisitionRun, Snapshot, or ObservationType records. It cannot populate `id`, `created_at`, or supersession relationships from Candidate-controlled values.

## 5. Duplicate and idempotency behavior

Duplicate handling is deliberately conservative because equivalent text may be legitimate repeated evidence.

### Within one batch

An exact transient comparison key is constructed from the resolved Document, ObservationType key, all Observation-mapped Candidate values, and `source_record_key` when supplied. Values are compared without normalizing `raw_value`. The first exact Candidate is retained and later exact occurrences are reported as `skipped_batch_duplicate`. A changed locator, extraction metadata value, or source-record key makes the Candidate distinct.

### Against persisted Observations

A match on Document, ObservationType, and raw value is only a **potential duplicate**. It must be reported; it is not sufficient grounds for silent suppression. Repeated evidence, different source locations, changed extraction methods, or deliberately repeated source rows may legitimately produce separate Observations. Observations from different Documents are never duplicates merely because their values match.

The current Observation schema has no durable importer idempotency key. Therefore this contract does not claim universal cross-run exactly-once behavior. An importer should retain its execution report and stable source-record identities where available, preview retry effects, and avoid blind replay. `source_record_key` supports diagnostics and importer-local retry comparison only; persisting it or adding a durable batch ledger requires a later explicit design decision and schema authorization.

No global deduplication engine, content-hash requirement, or new uniqueness constraint is approved here.

## 6. Batch transactions and results

The default for the initial monolith is **validate all, then atomically persist the approved set in one transaction**:

- Validation failures do not prevent other valid Candidates from reaching the approved set.
- Batch duplicates are skipped before persistence.
- All approved Observations commit together.
- A database constraint or unexpected persistence failure rolls back every Observation attempted in that approved set.
- Results are reported as created only after commit succeeds.

This provides useful partial acceptance at the validation boundary without leaving a partially committed persistence batch. It also makes a failed execution predictable to retry. Very large batches or source-specific partial-commit policies require a later explicit decision; per-candidate transactions are not the default contract.

The minimum per-candidate outcomes are:

- `created`
- `rejected_validation`
- `rejected_unknown_type` (including inactive type, with a distinct diagnostic code if useful)
- `skipped_batch_duplicate`
- `failed_persistence`

Each result identifies the candidate by its batch position and, when supplied, `source_record_key`; it includes a stable error code and safe diagnostic message. Internal exceptions and stack traces are logged for operators, not exposed to end users. If the persistence transaction rolls back, every approved item receives `failed_persistence`; no result is reported as created.

Expected validation errors include malformed shape, missing or conflicting Document context, unknown or inactive ObservationType, empty raw value, and invalid confidence. Database constraint and unexpected persistence failures are not swallowed. No error path creates Verification or Fact records.

## 7. Human review and security boundary

Controlled importers should initially support an in-memory dry-run report followed by explicit execution that converts valid Candidates into immutable Observations. No persistent Candidate queue is approved.

A future workflow may show temporary Candidates for human confirmation before execution. Both direct controlled execution and human-confirmed execution must use this contract and the same converter. If Candidates ever need to survive process termination while awaiting review, that is a new persistence design requiring explicit approval; this document does not authorize it.

Candidate and resulting Observation values remain untrusted in every presentation. Jinja or other UI rendering must escape them normally, unsafe HTML rendering is prohibited, and error messages must not reflect unescaped source content or internal exception details.

## 8. Proposed first implementation slice

The first implementation should contain only:

1. A typed `ObservationCandidate` transfer structure with the fields above.
2. Small structured validation and application-result types.
3. One candidate converter/application module implementing resolution, validation, batch comparison, atomic application, and result reporting.
4. Reuse of `ObservationRepository.create()` for persistence.
5. Focused unit tests for field mapping, raw preservation, confidence validation, type and Document resolution, batch duplicates, atomic rollback, and the prohibition on creating later-layer records.

It includes no UI, source importer, parser, database model, migration, persistent batch ledger, background job, Verification, or Fact behavior.

## Architecture Freeze

This document defines an implementation-boundary contract only. Observation Candidate is not a persistent domain entity and is not added to RWI's authoritative knowledge chain. The contract does not change the frozen RWI knowledge model, Observation schema, Acquisition lifecycle, or ownership of truth decisions.

Future implementation must remain within this contract. Persistent Candidate storage, durable importer fingerprints or batch ledgers, and any associated lifecycle require a new explicit architecture decision and are not currently approved.
