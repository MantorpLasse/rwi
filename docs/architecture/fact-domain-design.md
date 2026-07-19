# RWI Fact Domain Design

Status

- Document type: Domain design
- Sprint: Sprint 4 – Fact Foundation
- Scope: Fact domain only
- Implementation status: Design only
- Architecture status: Frozen

## 1. Purpose

The Fact domain represents atomic knowledge that RWI currently accepts as true after review of preserved evidence.

```text
PublishingSource → Document → Observation → Verification → Fact → Intelligence
```

Each layer has one responsibility:

- Document preserves source material and publication provenance.
- Observation preserves what one Document expressed.
- Verification records a reviewer's judgement about an Observation.
- Fact records an accepted knowledge statement supported through Verification.
- Intelligence derives explainable conclusions from Facts.

A Fact is not evidence, a source quotation, a review, or an analytical conclusion. It does not make an Observation true retroactively. It states only what RWI accepts as knowledge under a defined semantic and temporal scope.

## 2. Fact definition

A **Fact** is an immutable, versioned, atomic accepted statement with governed meaning, a defined subject and temporal scope, and explicit support from one or more accepted Verifications.

A Fact must answer:

- What proposition does RWI accept?
- What entity or subject does it concern?
- For what time or validity scope is it accepted?
- Which accepted reviews support it?
- Which earlier Fact version, if any, does it replace?

The accepted value is a knowledge-layer value. It may agree with a normalized Observation candidate, but it is not copied merely because extraction succeeded. Promotion must make the accepted meaning explicit.

### 2.1 Core invariants

1. A Fact can originate only through Verification.
2. Every supporting Verification identifies exactly one preserved Observation.
3. Every Fact has at least one supporting Verification whose outcome is `ACCEPTED` at creation time.
4. Fact creation never modifies an Observation or Verification.
5. A Fact statement is atomic: one governed proposition, one subject, one accepted value, and one temporal scope.
6. Accepted Fact content is immutable. Changed knowledge creates another Fact version.
7. Earlier Fact versions and their support remain queryable.
8. Current status is derived from append-only lineage, not by overwriting historical content.
9. Every Fact remains reconstructable through Verification, Observation, Document, and PublishingSource.
10. Fact contains accepted knowledge only; reasoning, recommendations, forecasts, scores, and risk assessments belong to Intelligence.

### 2.2 What Fact may contain

Conceptually, a Fact may contain:

- its own version identity;
- a governed semantic type or proposition key;
- the subject the accepted statement concerns;
- the accepted value in the representation allowed by that semantic type;
- applicable or effective time, where the proposition is time-sensitive;
- creation/acceptance time;
- supersession lineage to an earlier Fact version;
- support links to the accepted Verifications used for promotion;
- a concise promotion note only when needed to explain selection or scope.

The physical value and subject representation should reuse the governed semantics already established by the Observation design where applicable. This document does not redesign ObservationType, subject resolution, or candidate values, and it does not require their current persistence shape to change.

### 2.3 What Fact must never contain

Fact must not contain:

- source files, snapshots, HTML, PDFs, or quoted evidence;
- parsing, OCR, importer, or acquisition state;
- raw values as a replacement for Observation evidence;
- mutable review outcomes or reviewer assignments;
- extraction confidence;
- truth-review history that belongs to Verification;
- conflict-resolution algorithms;
- analytical reasoning, recommendations, risk, opportunity, forecast, or priority scores;
- denormalized provenance that can silently diverge from the supporting chain.

Fact does not parse Documents, review evidence, import files, replace Observation or Verification, or perform analytics.

## 3. Eligibility and creation boundary

A proposed accepted statement is eligible to become a Fact only when:

1. its subject, semantic meaning, accepted value, and temporal scope are explicit;
2. at least one supporting Verification has status `ACCEPTED`;
3. every cited Verification belongs to an Observation relevant to that same proposition and scope;
4. each supporting Observation retains its required Document provenance;
5. contradictory or later review records have been considered by the promoting reviewer or policy without being erased;
6. the proposed Fact identifies whether it starts a new lineage or supersedes a current Fact version;
7. no later layer is being used to bypass Verification.

`PENDING`, `REJECTED`, and `UNDECIDED` Verifications cannot independently establish a Fact. They remain visible in the evidence history and may provide context to a later decision, but support links that establish accepted knowledge point to the specific accepted Verifications relied upon.

Eligibility is necessary, not automatic. An accepted Verification means a reviewer accepted one Observation's claim; promotion additionally establishes the precise atomic Fact value and scope. This design defines no scoring, voting, consensus, or automatic-promotion algorithm.

A conceptual **Fact candidate** is the proposed promotion input before acceptance. It is not an accepted Fact and does not belong in the knowledge chain. The first implementation need not persist Fact candidates or add a candidate lifecycle.

## 4. Relationships and provenance

The authoritative support relationship is:

```text
Fact version
  └── supported by one or more specific accepted Verifications
        └── each reviews exactly one Observation
              └── belongs to exactly one Document
                    └── belongs to one PublishingSource
```

A Fact may have many supporting Verifications and therefore many supporting Observations and Documents. A Verification may support more than one Fact when one reviewed Observation legitimately supports several separately scoped atomic statements. An Observation may consequently support multiple Facts through its Verifications.

The Fact-to-Verification support link is explicit and version-specific. Observation and Document provenance are derived through that link rather than copied onto Fact. This avoids redundant relationships that could disagree. A future read model may project the full chain for efficient display, but the projection is not authoritative.

Each support link should preserve its role in promotion only if a minimal distinction is genuinely required, such as primary support versus corroborating support. The first foundation should avoid weights, votes, and generic evidence graphs.

### 4.1 Traceability questions

For every Fact version, RWI must be able to answer:

- Which Verification records supported this accepted statement?
- What status, reviewer confidence, comment, and review time did each record contain?
- Which immutable Observation did each Verification review?
- What raw and normalized candidate values did those Observations preserve?
- Which Document and PublishingSource produced each Observation?
- Which previous Fact version did this version supersede, and which later version replaced it?

Later reviews do not rewrite the support set of an existing Fact version. If later evidence changes accepted knowledge, it supports a new Fact version.

## 5. Lifecycle

The lifecycle is deliberately small:

1. **Candidate** – a proposed statement and support set under consideration. It is not yet a Fact and need not be persisted initially.
2. **Accepted Fact** – an immutable Fact version created through eligible accepted Verification support.
3. **Superseded Fact** – an earlier accepted version for which a later accepted version records an explicit supersession link.
4. **Retired Fact** – an earlier accepted statement explicitly withdrawn without a replacement accepted value.

“Superseded” is derived from an incoming supersession link; the earlier row is not rewritten. Retirement, if required, should likewise be an append-only lineage event or terminal record that names the retired Fact and reason. A mutable `is_current` flag is not authoritative.

Retirement is appropriate only when RWI no longer accepts the proposition and has no replacement value—for example, the statement was discovered to be inapplicable or its semantic slot ceased to exist. Ordinary temporal change should create a newly scoped Fact version rather than erase or retire historically valid knowledge.

Rejected or undecided promotion proposals are not Fact lifecycle states. Their review belongs outside accepted Fact records.

## 6. Versioning and identity

Each accepted version receives a new Fact identifier. Versions are connected by an explicit nullable `supersedes_fact` relationship.

This is preferred over a stable mutable Fact row because:

- accepted content never changes in place;
- every historical statement can be cited independently;
- support remains attached to the exact version it established;
- corrections and changed temporal scope remain reconstructable;
- SQLite can enforce the simple self-reference now, and PostgreSQL can retain the same semantics later.

The logical continuity of a proposition is its supersession chain, not reuse of one database identifier. A governed semantic slot—subject, proposition type, and applicable temporal dimension—determines whether a new accepted statement belongs to an existing chain. The exact physical key and constraints are implementation decisions for the Fact foundation; they must not weaken the one-current-version invariant.

Supersession means “this accepted version replaces that accepted version for the same semantic slot.” It must not be used merely because two Facts are related or because one has a later creation date. A version cannot supersede itself, and cycles are prohibited.

## 7. Current and historical truth

For a given governed semantic slot, the **current Fact** is the terminal accepted Fact version that:

- has not been superseded by another accepted version;
- has not been retired;
- is applicable at the requested time under its temporal scope.

Current truth is therefore a query over accepted lineage and validity, not a mutable boolean. A later creation timestamp alone does not establish current status. There should be at most one current accepted Fact for the same subject, proposition, and overlapping temporal scope.

Historical versions remain first-class query results. They are required to explain:

- what RWI accepted at an earlier time;
- which evidence and reviews justified that understanding;
- when and why accepted knowledge changed;
- which Intelligence conclusions used the knowledge available at that time;
- whether a correction changed value, meaning, subject resolution, or temporal scope.

Historical queryability is essential even when the source evidence itself never changed. Evidence is immutable; the accepted interpretation of that evidence may evolve as new reviews or Documents arrive.

## 8. Conflicting evidence

Conflicting Observations and Verifications continue to coexist. Fact must not delete, merge, or rewrite them.

Conflict handling follows these principles:

- A Fact is created only when a promotion decision identifies an accepted atomic conclusion and its support.
- Conflicting accepted reviews do not automatically select a winner.
- If no accepted conclusion can be justified, no current Fact is created or superseded merely to force certainty.
- Evidence may be apparently conflicting because of different times, subjects, definitions, or scopes; appropriately distinct Facts may coexist when their semantic or temporal slots do not overlap.
- Two contradictory current Facts for the same semantic slot and overlapping time are not an acceptable steady state.
- The promotion decision may record a concise explanation, while reusable reasoning policy and conflict algorithms remain outside the Fact payload.

This design does not define source rankings, quorum, confidence aggregation, consensus, or automatic conflict resolution.

## 9. Boundary with Intelligence

Fact is atomic accepted knowledge. Intelligence consumes one or more Facts to produce interpretations or decisions.

Examples of Fact content include an accepted installation year, runway dimension, project status at a date, or identified product. Examples of Intelligence content include predicted procurement likelihood, risk assessment, opportunity ranking, recommended action, trend explanation, or confidence-weighted forecast.

Fact must not contain:

- reasoning chains;
- recommendations;
- opportunity or risk assessments;
- forecasts;
- prioritization;
- derived scores;
- narrative conclusions assembled from several Facts.

Every Intelligence conclusion must later identify the exact Fact versions it used. Intelligence never rewrites a Fact or its evidence lineage.

## 10. Recommended implementation sequence

Implementation should proceed in these finite slices:

1. **Fact model and migration.** Add the smallest immutable accepted-version model, governed semantic/value scope, supersession lineage, and explicit support links to accepted Verifications. No promotion automation or UI.
2. **Fact repository.** Add create, get, deterministic lineage/history, current-version, and support reads. No generic query framework, update, or delete methods.
3. **Read-only Fact views.** Display current and historical versions with complete Verification-to-Document traceability and no mutation controls.
4. **Manual Fact creation from accepted Verification.** Add a governed promotion form that requires eligible support, explicit accepted value and scope, and optional supersession of the current version. It creates no Intelligence.
5. **Automatic promotion, future.** Consider only after manual promotion and lineage are proven. Any policy must remain explainable and may not bypass Verification.
6. **Intelligence foundation.** Design and implement separately after accepted Fact lineage works end to end.

No slice may add mutable Fact overwrite, direct Observation-to-Fact creation, persistent Fact candidates, scoring, consensus, or Intelligence behavior merely for convenience.

## 11. Architecture decision

The Fact domain is frozen around these rules:

- Fact is immutable, atomic, versioned accepted knowledge.
- Fact exists only through explicit support from accepted Verification records.
- Each version has its own identifier and preserves its exact support set.
- Supersession and retirement preserve append-only history.
- Current truth is derived from terminal lineage and temporal applicability.
- Observation and Verification remain unchanged and retain their existing ownership.
- Complete provenance is reconstructed through Fact → Verification → Observation → Document → PublishingSource.
- Fact contains neither review behavior nor Intelligence reasoning.

Physical persistence details may be clarified during the Fact foundation, but they must not change these ownership, eligibility, provenance, history, and one-way knowledge-flow rules without reopening this design explicitly.
