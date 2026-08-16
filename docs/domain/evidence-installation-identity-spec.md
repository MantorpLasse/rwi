# Evidence and Installation Identity Specification

**Status:** Design-only semantic contract. It changes no model, data, schema, importer, UI, or route.

## Purpose and factual basis

The current 149 `Installation` rows mix different meanings: 69 FAA Tableau airport/type assertions, 61 FAA fact-sheet dated assertions, and 19 curated news/manual assertions. Only Cuyahoga’s two 2018 records are proven at row level to be distinct physical systems (ends 06 and 24). No existing row is proven a duplicate.

The checked-in FAA CSV has one row per airport/type and contains no runway, runway end, system count, installation year, vendor, stable system identifier, or source-row identifier. It must not be treated as a physical-system baseline.

## 1. Physical Installation

An **Installation** means one distinct physical EMAS system/bed at an Airport. It is not an airport-wide inventory claim and is not merely an upstream source row.

- An Airport may have many Installations.
- A Runway may have many Installations.
- Both ends of a Runway may have distinct Installations.
- Location is optional evidence: runway/end are never invented merely to complete a record.
- More than one system at the same end remains possible when evidence supports it; location is not a universal identity key.

Two assertions are the same Installation only when evidence identifies the same physical system. Matching airport, product/type, year, or source type alone never establishes identity.

When evidence only proves “EMAS exists at this airport,” retain an airport-level assertion. Do not create arbitrary systems or split an aggregate count into systems. A human-reviewed physical Installation may be recorded with unknown runway/end only if the evidence establishes one physical system but not its location.

### History and replacement

A replacement never silently overwrites its predecessor. A successor is a new physical system when evidence establishes it; the predecessor remains historical and is linked only with evidence. A retrofit/refurbishment is not automatically a replacement. Preserve raw source language where its physical meaning is uncertain.

## 2. Source Assertion

A **Source Assertion** preserves what one source record actually claims at its own granularity.

| Assertion type | Meaning | Creates a Physical Installation by itself? |
|---|---|---|
| Airport-level inventory | EMAS/product exists somewhere at airport | No |
| Runway-level | Associated with a runway; end unknown | Normally no |
| Runway-end-level | Distinct installed system associated with an end | Yes, if direct and non-aggregate |
| Physical-system | Source expressly identifies a discrete system/bed | Yes |
| Historical | Installation/replacement/removal/retrofit history | Only if it identifies a discrete system |
| Project/construction | Plan, funding, procurement, construction, or completion | No until completion and system identity are evidenced |

An assertion may be explicitly unresolved. It must never be silently promoted to a stronger granularity.

## 3. Source record identity and provenance

Every upstream record should retain:

1. Source/publisher identity.
2. Exact URL or immutable source artifact.
3. Retrieval timestamp and artifact/version/hash where practical.
4. Upstream record identity: row/item/mark ID, or deterministic locator plus raw-fragment hash.
5. Source locator: workbook/view/sheet/worksheet/mark for Tableau; page/table/line for PDF; section/anchor for HTML.
6. Raw values used for matching: airport name/code, type, runway/end, years, dimensions, vendor wording, count, and relevant text.
7. Parser/import version and extraction time where automated.

An idempotent re-import key is **namespaced source identity + stable upstream record ID**. If none exists, use locator plus immutable artifact/version/hash and raw-fragment hash. Airport + type, airport + year, title, or similarity are never universal keys.

## 4. Reconciliation

Reconciliation decides whether assertions are **same**, **different**, or **unresolved** with respect to Physical Installations.

### Strong evidence

- Same explicit upstream physical-system ID.
- Same airport, explicitly named runway and end, and source language identifying one system.
- Official source explicitly identifies predecessor/successor relationship.
- Direct official cross-reference, or a unique engineering/project identity plus explicit location/system language.

### Medium evidence

Useful for human review but normally needing corroboration:

- Same airport/type, named runway, compatible date.
- Matching dimensions, vendor wording, project title, and time window.
- Source coordinates clearly locating the same end, supported by text.
- Independent sources agreeing on a specific system/end.

### Weak evidence — never sufficient alone

- Same airport, type/product, or year.
- Airport-centroid coordinates or similar text.
- System count, generic source title/type, or source agreement at airport level.
- Likelihood that Runway Safe supplied the system.

Only strong evidence, or a reviewed non-conflicting combination of medium evidence, may establish “same.” Explicit different ends/runways/system IDs, or a stated predecessor/successor relationship, establish “different.” Every other relationship is unresolved.

## 5. Runway/end semantics

| Evidence state | Permitted conclusion |
|---|---|
| Airport known; runway unknown | Airport assertion, or reviewed physical system with unknown location; never guess. |
| Runway known; end unknown | Store runway association; do not choose end. |
| Runway and end known | Store the supported end association. |
| Multiple candidate runways | Preserve raw candidates; leave canonical runway unresolved. |
| Multiple candidate ends | Preserve runway; leave end unresolved unless distinct assertions identify each end. |

Coordinates, naming conventions, proximity, and airport-layout assumptions are not authoritative runway/end evidence.

## 6. Lifecycle and dates

Installation lifecycle is independent of Signal project lifecycle. Evidence-backed Installation states are: `operational/current`, `under replacement`, `replaced`, `retired/removed`, and `historical/unknown`. Do not assign a state unsupported by evidence.

Signal category/status describe project opportunity/lifecycle. A completed Signal is not proof of an operational Installation until source evidence confirms completion and identifies the system.

- **Original installation year:** source-supported initial installation/service year.
- **Replacement year:** source-supported successor replacement year; it does not erase original year.
- **Refurbishment/retrofit year:** modification date; not a replacement unless source says so.
- **Unknown:** no direct evidence.

Composite or parenthesized source years must retain raw wording; do not canonicalize ambiguity without documented evidence.

## 7. Vendor/manufacturer and evidence quality

Vendor/manufacturer is confirmed only when a source explicitly names the relevant role. Runway Safe’s market position must never infer it supplied a particular system. Likely supplier is an internal assessment, not a confirmed fact. Supplier, manufacturer, installer, licensor, contractor, and maintainer are separate roles unless source evidence connects them.

Evidence quality belongs to an assertion/reconciliation decision, separate from Source type, Signal confidence/score, and lifecycle:

| Level | Meaning |
|---|---|
| Direct/strong | Primary/authoritative source directly supports system/location/lifecycle fact. |
| Corroborated | Compatible independent support without unique system ID. |
| Partial | Aggregate/incomplete fact only. |
| Ambiguous | Composite, conflicting, or insufficient evidence. |
| Unverified candidate | Extracted/discovered but not reviewed against raw source. |

## 8. Worked examples

### JFK

FAA CSV is an aggregate EMASMAX assertion. Fact-sheet text describes multiple historical systems/replacements. Do not map current rows to named systems or split the aggregate from a count. Retain source assertions pending reconciliation.

### BOS

Existing aggregate/fact-sheet Installation assertions lack end identity. Runway 27 construction evidence is a Signal/project assertion, not a new operational system until completion and physical-system evidence are available.

### Cuyahoga

2018 assertions name ends 06 and 24. They support different physical systems; same airport/type/year does not make them duplicate. Missing Runway FKs must not be inferred.

### MDW

FAA aggregate greenEMAS, 2014 greenEMAS end/vendor evidence, and 2006 EMASMAX fact-sheet history must remain separate assertions. No mechanical merge is justified.

### ORD

FAA aggregate EMASMAX, dated fact-sheet EMASMAX, and researched greenEMAS coexist. Their physical correspondence is unresolved; retain each assertion.

## 9. AI and automation boundary

AI/agent/n8n may discover sources, preserve artifacts and raw evidence, extract claims, classify proposed granularity, normalize candidate values while keeping raw values, propose matches, and flag ambiguity.

It must not silently create a physical identity from weak evidence, infer runway/end/vendor/year/lifecycle, overwrite reviewed facts, merge records, or publish an assumption as confirmed. Automation produces source evidence and candidates; humans approve physical identity, reconciliation, lifecycle, vendor confirmation, and any merge-like decision.

## 10. Public versus internal information

Public output may contain reviewed, source-backed facts with appropriate source link/locator: airport, supported product/location/lifecycle/date, confirmed vendor, and concise evidence notes.

Internal-only pending review: “Min bedömning,” AI reasoning, candidate scores, alternative matches, raw debug/operational data, internal IDs/file paths, likely-supplier reasoning, and unverified extraction. The current static rule excluding `Signal.notes` and `manual_year_estimate` must remain. Copying private Signal notes into public Installation notes is an implementation hazard, not a permitted shortcut.

## 11. Migration safety

This specification migrates nothing. A future process must back up first; preserve every original assertion/legacy row; record explicit old-to-new mappings, actor/time/evidence reason; avoid destructive merge/delete; route ambiguity to review; preserve raw source wording/URLs/IDs; and validate public-export boundaries after every approved change.

## 12. Future FAA baseline

The current FAA CSV is an airport/type inventory assertion only. It must not be split because another source reports multiple systems. A future Tableau capture with stable mark/row identity, explicit system/location fields, or reliable locators can create stronger Source Assertions. The importer must preserve raw artifact, retrieval/version, workbook/view/sheet, source record ID, raw values, and source-record idempotency key before any reconciliation.

## 13. Decision table

| Concept | Meaning | Can create Installation? | Requires human review? | Can AI propose it? |
|---|---|---|---|---|
| Airport-level assertion | EMAS exists somewhere at airport | No | Yes | Yes |
| Runway-level assertion | Runway known, end unknown | Normally no | Yes | Yes |
| Runway-end assertion | Distinct installed system at end | Yes with direct evidence | Yes | Yes |
| Physical-system assertion | Source identifies discrete system | Yes | Yes for merge/link | Yes |
| Historical assertion | Past/replacement/retrofit fact | Only if distinct system identified | Yes | Yes |
| Project/construction | Planning through completion claim | No until completion/system evidence | Yes | Yes |
| Reconciliation | Same/different/unresolved decision | N/A | Yes | Propose only |
| Vendor confirmation | Explicit source-backed role | N/A | Yes | Extract/propose only |

## 14. Non-goals

This specification does not change schema, migrate/deduplicate/rewrite data, implement reconciliation or FAA import, implement AI/n8n, lifecycle UI, maps, routes, or vendor inference. It establishes a single discipline for future work: preserve assertions, reconcile only with evidence, and never guess.
