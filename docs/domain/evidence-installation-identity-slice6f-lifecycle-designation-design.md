# Evidence Identity Slice 6F: Minimal Lifecycle and Runway-Designation Evidence Rules

## Decision

Physical identity, lifecycle evidence, and runway-designation evidence are
three different claims. RWI must not convert one into another. The current
SourceAssertion -> InstallationAssertionLink -> PhysicalInstallationIdentity
model remains sufficient for the next practical step because it can preserve
unresolved evidence and leave it unlinked. No schema change is required now.

## 1. Core distinctions

| Concept | Meaning | Does not establish |
|---|---|---|
| PhysicalInstallationIdentity | One reviewed canonical physical EMAS installation. | Year, product/vendor, lifecycle, or replacement. |
| Lifecycle evidence | A source-backed claim that something happened to an installation. | Identity unless the source explicitly identifies the system. |
| Runway designation evidence | A claim about runway naming, closure, replacement, or aliasing. | A new or continuing EMAS bed. |

A current EMAS record at end 22L does not establish installation date. Repair
does not establish replacement. A redesignation does not establish either a
new physical identity or continuity of an old one.

## 2. Minimum lifecycle semantics

RWI needs only these evidence classifications when a source actually supports
them. They are classifications of an assertion/event candidate, not new fields
on PhysicalInstallationIdentity.

| Classification | Sufficient evidence | Insufficient evidence | Physical event or project state | Automation |
|---|---|---|---|---|
| `installed_or_commissioned` | Explicit installed, commissioned, opened, or completed EMAS at a named system/location. | Funding, planning, generic completion, current inventory. | Physical event. | Auto-classify explicit language; human review before identity/continuity link. |
| `repaired` | Explicit EMAS repair/repair work at a system or airport. | “Improvement,” maintenance budget, or a generic construction project. | Physical event only if the source says work occurred; otherwise project evidence. | Auto-ingest/classify; never infer replacement. |
| `replaced` | Explicit replacement/removal-and-new-installation language, preferably predecessor/successor or location. | Repair, retrofit, material work, incident, or a changed vendor/product. | Physical event affecting at least two identities when system scope is explicit. | Human review. |
| `removed` | Explicit removal/decommissioning of identified EMAS. | End absent from a later incomplete inventory. | Physical event. | Propose; human review. |
| `activated` | Direct incident/arresting-system activation evidence tied to EMAS. | An incident at an airport without EMAS engagement evidence. | Physical event; may create a Signal. | Auto-ingest; review lifecycle consequence. |
| `construction_started` / `construction_completed` | Explicit project milestone and named scope. | Funded, planned, design, or procurement. | Project state; completion becomes installation evidence only when the EMAS system/location is explicit. | Auto-classify source wording. |

`planned`, `funded`, `design`, `procurement`, `construction`, and `completed`
are Signal/project states. They are not physical-installation states by default.
A completed project can establish an installation event only with explicit EMAS
and location/system evidence.

## 3. Continuity and replacement rules

Two claims across time may support `SAME_PHYSICAL_INSTALLATION` only with
positive continuity evidence: an explicit persistent system ID; an authoritative
predecessor/successor statement identifying the same system; or a reviewed
combination of direct, non-conflicting records that identifies one physical bed
by a stable engineering/location marker and contains no replacement evidence.

These never establish continuity by themselves: same airport, runway, end,
product, vendor, approximate year, matching text, or current presence. Without
positive continuity evidence, retain independent assertions and use
`UNRESOLVED` rather than a SAME link.

Replacement is established by direct language such as “replaced [identified
bed] with [identified new bed]”, or an equivalent authoritative statement that
unambiguously identifies predecessor and successor. A repair, retrofit,
activation, or different date is not a replacement rule.

## 4. Runway designation evidence

Formatting normalization is a technical comparison aid, not historical runway
evidence. For example, `06/24` and `6/24` may be normalized for candidate
lookup only; the raw source value remains preserved and normalization never
authorizes a canonical identity link.

| Classification | Required evidence | Rule |
|---|---|---|
| Formatting variant | Same source/system context and reversible syntactic formatting only. | Retain raw wording; use normalized form only as a comparison candidate. |
| Historical alias/redesignation | Authoritative runway history explicitly identifies old and new designations and their relation. | Record relationship conceptually; never rewrite historical source evidence. |
| Closure | Explicit closure of a runway/designation. | Does not remove an EMAS identity or prove where it moved. |
| New/replacement runway | Explicit construction/replacement statement plus location/continuity evidence. | Does not automatically transfer a bed or create one. |

MDW: preserved notes/scripts say former `13C/31C` was renamed `13L/31R` on
2025-06-12 and the earlier `13L/31R` closed. This is adequate to preserve a
designation-history candidate, but the retained exact official artifact/locator
is incomplete. It cannot map any EMAS bed across names. Human review is required
before a historical alias affects canonical placement.

## 5. International and language rules

The rules use source meaning, not FAA identifiers, NASR formatting, US grant
terminology, or English. Every evidence capture should retain original wording,
source language, locator, and raw values. A normalized interpretation and any
English/Swedish translation are separate derived fields or presentation values;
they never replace the original. This permits later Brazil, Europe, Asia, or
New Zealand research without treating translated terms as original proof.

## 6. Automation posture

| Evidence | Automation boundary |
|---|---|
| Official runway-end EMAS inventory | AUTO-INGEST and AUTO-CLASSIFY as current end presence. |
| Explicit official “EMAS installed/completed at runway end X” | AUTO-INGEST; PROPOSE physical identity/event. Human review selects canonical identity. |
| Aggregate inventory/count | AUTO-INGEST; never auto-link or split into identities. |
| “EMAS Bed Repairs” | AUTO-INGEST and classify repair/project evidence; never auto-replacement. |
| Funding/planning/procurement | AUTO-INGEST as project evidence; never installation event. |
| Conflicting historical designations or across-time same end | PROPOSE and HUMAN-REVIEW. |
| Explicit predecessor/successor replacement statement | AUTO-CLASSIFY replacement candidate; HUMAN-REVIEW relationship. |

This is human-in-the-loop by exception: sources and routine classifications
flow automatically; only identity, continuity, replacement, and designation
consequences require review.

## 7. Apply the rules to MDW

| MDW evidence | Safe conclusion |
|---|---|
| NASR 145–148, 2026-08-06 | Current EMAS presence at 04R, 22L, 13L, and 31R; candidates only. |
| Assertion 103, Nov 2014 22L | Historical greenEMAS completion claim at 22L. |
| Assertions 103 + 146 | Same named end, but lifecycle continuity is UNRESOLVED. |
| Assertion 106, raw 2006/2007 count 4 | Historical aggregate/count; no physical identities. |
| CIP 46109, 2025 “EMAS Bed Repairs” | Repair/project-design evidence; no replacement or completion conclusion. |
| MDW designation summary | Runway history candidate; no automatic EMAS mapping. |

## 8. Persistence assessment

For now, retain lifecycle and designation claims as SourceAssertions plus
Signals/project evidence, with reviewed identity links only where justified.
Do not add lifecycle fields to PhysicalInstallationIdentity and do not alter
legacy Installation.

If a future reviewed case needs persistence, add only two narrow records:

- `PhysicalInstallationLifecycleEvent`: identity (nullable when unresolved),
  supporting assertion, classification, review/audit fields. It must not be a
  generic event framework.
- `RunwayDesignationRelationship`: airport, predecessor/successor Runway or
  retained raw designations, relationship classification, supporting assertion,
  review/audit fields. It must preserve historical values rather than rewrite
  them.

Neither table is justified for implementation now: MDW has no proven event or
designation relationship that needs a canonical record.

## 9. Research Engine output

A future native-language research engine should emit a compact proposal:

```text
SOURCE CLAIM -> preserved original fragment/locator/language
             -> evidence classification (inventory, project, lifecycle, designation)
             -> confidence and raw/translated interpretation
             -> optional proposed identity/replacement/designation relationship
             -> review requirement and reason
```

It must never directly rewrite canonical identity, legacy rows, or public data.

## Recommended next slice

**D: both persistence changes can wait until stronger evidence exists.**
Implement a narrow, human-reviewed MDW *current-presence* pilot only if the
product decision accepts identities that mean “current NASR-supported EMAS at
this end” and explicitly make no historical-continuity claim. Otherwise continue
evidence acquisition first. Do not implement lifecycle or designation schema
preemptively.

## Explicit answers

1. A physical installation is a reviewed canonical physical EMAS system, not
   an airport/type/year record or a project.
2. Lifecycle evidence is an explicit source claim of an event affecting a
   system; project status alone is not lifecycle.
3. SAME across time needs explicit or reviewed positive continuity evidence,
   not matching location/product/date.
4. Replacement needs direct predecessor/successor or equivalent explicit
   replacement evidence.
5. Redesignation is a separate runway-history relationship preserving raw old
   and new names.
6. AI stops for identity, continuity, replacement, and ambiguous designation
   consequences; it may ingest/classify routine evidence.
7. RWI needs no schema change now.
8. Next: wait for stronger evidence or, separately approve a narrow current
   MDW pilot with no historical-continuity claim.
