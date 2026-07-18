# ADR-0002

## Source and Document Normalization

Status

Accepted

Date

2026-07-17

---

## Context

The legacy `Source` model is owned by one Project and combines publishing-organization data with publication data. This prevents Sources and Documents from existing independently and duplicates one FAA publication across two Projects.

## Decision

### Source

Source represents an organization or publishing origin and is independent of Project. It has:

- `id`
- required `name`
- `source_type`
- `homepage_url`
- `country_code`
- `reliability_level`
- `notes`

A Source may publish zero or many Documents. `homepage_url` identifies the organization website, and `reliability_level` is only a general assessment of the Source. Unknown publishers must use an explicit Source rather than a null foreign key.

### Document

Document represents one exact publication or captured content version and belongs to exactly one primary Source. It has:

- `id`
- `source_id`
- `title`
- `document_type`
- `url`
- `published_date`
- `accessed_date`
- `document_reference`
- `summary`
- `revision`
- `content_hash`
- `status`

URL is neither stable identity nor unique. A meaningful revision creates a new Document. Generic publisher-homepage URLs may be retained but must be marked as incomplete metadata. A Document must remain identifiable if its URL disappears.

### Project Relevance

Project and Document have a many-to-many relationship. The association expresses relevance only. One Document may relate to multiple Projects.

`page_number` does not belong to Document and is not migrated because all current values are empty.

## Identity and Legacy Data

- Legacy Source IDs are not preserved as Source or Document IDs.
- Publisher deduplication uses conservative, case-sensitive exact matching after Unicode and whitespace normalization. Alias reconciliation is excluded.
- The complete 12-row legacy set characterizes to 11 Sources, 11 Documents, and 12 Project–Document links because one FAA Document is relevant to two Projects.
- The internal watch item is not automatically migrated as a publication. It remains explicitly unresolved.
- Consequently, the automatically eligible subset contains 10 Sources, 10 Documents, and 11 links, plus one unresolved legacy row. The 11/11/12 counts describe the complete characterization, not the automatic publication output.
- Homepage-only URLs are retained on candidate Documents and flagged as incomplete.

## Excluded Scope

- Observation, Verification, Fact, Intelligence, citations, pages, and evidence semantics
- Publisher alias reconciliation
- Production models or normalization services
- Feature migrations or changes to the existing baseline
- Route, template, and seed changes
- Stamping the existing database

## Consequences and Limitations

Source can no longer be project-owned, and project relevance will require an association table. Existing templates and seed logic will eventually need a compatibility transition. Current URL quality is insufficient for stable identity; `revision`, `content_hash`, and status rules require later operational definitions. Production model and migration work remains blocked until the baseline-stamp procedure and treatment of the unresolved internal watch item are approved.
