# Evidence Identity Slice 6B: Reconciliation Persistence Design Stress Test

## Verdict

This is a read-only design decision. No models, migrations, database records,
source assertions, UI, export, or network resources are changed.

CGF is a straightforward control case. MDW proves that existing `Installation`
rows mix airport inventory, end-specific research, and historical count claims.
Design A is unsafe as the general contract. Use Design B: future reviewed
`PhysicalInstallationIdentity` records, supported by SourceAssertion links and
kept distinct from legacy `Installation`. This preserves the simplified Claude
architecture and does not revive Observation -> Verification -> Fact ->
Intelligence.

## 1. CGF control case

| Material | Granularity | Safe reading |
|---|---|---|
| Legacy Installation 54 / FAA Tableau assertion 54 | Airport inventory | EMASMAX somewhere at CGF, not a physical identity. |
| Assertions 101 and 198 | Discrete end `06` | Independent source claims for one end. |
| Assertions 102 and 199 | Discrete end `24` | Independent source claims for the other end. |
| Legacy Installations 144 and 145 | Curated legacy rows | Context only until expressly reviewed/mapped. |

A reviewer can establish two physical identities: CGF end `06`, supported by
101/198, and CGF end `24`, supported by 102/199. Only after review may their
canonical placement cite the current runway/end. The airport/type aggregate
stays unlinked; it cannot be divided between systems. No automatic mapping to
legacy rows 144/145 is justified even where values look compatible.

## 2. MDW stress test

| Material | What it supports | What it does not support |
|---|---|---|
| Legacy 26 / assertion 26 | FAA Tableau `greenEMAS` airport inventory | A bed, runway, end, count, or identity. |
| Legacy 74 / assertion 103 | Direct 2014 `22L` claim | Current NASR correspondence, vendor, or legacy match. |
| Legacy 109 / assertion 106 | Historical two-system count, raw `2006/2007` | Locatable systems or replacement history. |
| NASR assertions 145--148 | 2026 EMAS at `04R`, `22L`, `13L`, `31R` | Product, year, vendor, or a legacy link. |
| Signals 12 and 68 | Watch/project evidence | Physical identity or lifecycle event. |

The 2025 historical runway-designation change makes retained raw wording
essential. A reviewer may consider the source-supported `22L` claims together,
but must decide the relationship explicitly. Fact-sheet count and airport
inventory assertions stay unlinked. Other NASR ends are distinct runway-end
evidence, not automatic identities or replacements.

## 3. Design comparison

### A. SourceAssertion -> InstallationAssertionLink -> existing Installation

This works only if every legacy Installation is a single physical system. It is
not: current rows include aggregates and historical counts. Making those rows
canonical would silently promote incompatible meanings or require unsafe legacy
conversion. Their source/year/product fields would appear stronger than the
recoverable upstream evidence permits.

### B. SourceAssertion -> InstallationAssertionLink -> PhysicalInstallationIdentity -> optional legacy Installation

This separates source evidence from reviewed identity without changing legacy
semantics. A physical identity is created only after review. Aggregates can
remain unlinked forever. Later, an explicit legacy mapping can say a row is
represented by an identity, represents no identity, or is unresolved.

**Decision:** Design B is necessary and small enough.

## 4. Minimum future model

| Future record/field | Rationale |
|---|---|
| `PhysicalInstallationIdentity.id` | Stable key; never airport/type/year-derived. |
| `airport_id` required | One airport can have many identities. |
| `runway_id` nullable | Reviewed canonical runway only when evidence supports it. |
| `runway_end` nullable | Reviewed end; no uniqueness constraint, allowing multiple systems at an end. |
| `InstallationAssertionLink.assertion_id`, `physical_installation_id` | Many-to-many evidence support with provenance retained. |
| Link outcome/review state/reason/actor/time/supersedes | Reversible, auditable human decision. |
| Later `LegacyInstallationMapping` | Explicit bridge to untouched legacy rows. |
| Later `PhysicalInstallationRelationship` | Auditable `replaces` graph. |

Stable outcomes are `SAME_PHYSICAL_INSTALLATION`,
`DIFFERENT_PHYSICAL_INSTALLATION`, and `UNRESOLVED`. A null target is allowed
only for an explicitly persisted unresolved review. Vendor, manufacturer,
product, year, and lifecycle stay source claims until separately reviewed.

For CGF, two reviewed identities would carry the two end-specific evidence
sets. For MDW, Slice 6C creates no identity from existing data automatically.

## 5. Failure modes and guards

| Failure mode | Guard |
|---|---|
| Airport/type/year treated as identity | Never a match key; aggregate/count evidence remains unlinked. |
| Multiple beds on runway/end | No airport/runway/end uniqueness constraint. |
| Renamed designation | Preserve raw wording; canonical placement is review-only. |
| NASR expanded into vendor/year/product | NASR means only EMAS at the reported end/cycle. |
| Legacy field mistaken for evidence | Require retained SourceAssertion evidence for reviewed links. |
| AI/n8n merges systems | Automation proposes only; human approves every outcome. |
| Decision proves permanent truth | Append/supersede, never overwrite evidence or prior audit. |
| Replacement inferred from maintenance | Require explicit successor/replacement evidence. |

## 6. Lifecycle and replacement

Identity asks whether claims concern the same physical system; lifecycle asks
what happened to it. They must remain separate. Whole-bed replacement normally
creates a new identity with a reviewed `replaces` relationship. Repair,
maintenance, funding, or refurbishment is not replacement unless explicitly
said. Signals remain project/watch evidence and cannot create, retire, replace,
or reconcile an identity.

AI/n8n may rank candidates and draft reasoning, but may not make a reviewed
identity, runway/end, vendor, lifecycle, or replacement decision. Every initial
link, including CGF, requires human confirmation.

## 7. Incremental migration strategy

1. Add the tables and constraints only; do not convert the 149 legacy rows.
2. Validate reviewed decisions, audit/supersession, explicit targets, and the
   prohibition on airport/type/year identity keys.
3. Test isolated CGF/MDW fixtures. Verify aggregate assertions stay unlinked
   and legacy domain tables are unchanged.
4. After separate approval, run a small human-reviewed CGF pilot. Any legacy
   mapping requires its own explicit decision.
5. Add replacement/lifecycle presentation only when direct evidence and real
   review cases justify it.

## 8. Explicit non-changes

Do not rewrite legacy notes or records; infer runway/end/vendor/year; alter
public export/UI; fetch evidence; auto-link FAA Tableau, fact-sheet,
USAspending/project, or NASR assertions; or restore the former architecture.

## Recommended Slice 6C

Implement only the minimal `PhysicalInstallationIdentity` and
`InstallationAssertionLink` persistence layer, migration, validation,
audit/supersession behavior, and isolated tests. Do not create data
automatically, map legacy rows, add lifecycle/replacement data, or change
public output. Separate approval is required before a real-database migration
or CGF pilot write.

Open decisions: whether an unresolved attempt needs a null-target record,
whether legacy mapping belongs in 6C or later, and governed human actor values.
None blocks schema-only work.

