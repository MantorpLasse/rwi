# Reconciliation and physical-installation identity design

## 1. Executive summary

RWI should add reconciliation as a small, evidence-backed layer between
`SourceAssertion` and a true physical `Installation`. It must not reinterpret
the present 149 legacy Installation rows automatically. The smallest durable
model is: retain `Installation` as the physical-system entity; add an
assertion-to-installation link carrying the reviewed relationship decision; and
add a compact immutable/auditable decision record only when a decision is made.
Aggregate assertions may stay unlinked forever. This remains compatible with
the simplified Airport/Installation/Incident/Signal/Source architecture.

## 2. Reconciliation semantics

A decision answers one narrow question: does this assertion support the same
physical system as a candidate Installation? Values are stable internal keys:
`SAME_PHYSICAL_INSTALLATION`, `DIFFERENT_PHYSICAL_INSTALLATION`, and
`UNRESOLVED`. No additional state is required. “Proposed” belongs to review
state, not identity outcome.

Every decision records evidence/reason, actor (`human` or named automated
proposer), timestamp, review state, and reversal/supersession reference. It is
non-destructive: change means append/supersede, never erase raw assertion or
prior decision.

## 3. Assertion ↔ Installation relationship

Use a new future `InstallationAssertionLink` rather than `Installation.source_id`:
one Installation has many links; an assertion can have zero, one, or many links.
The latter is essential for “four systems at FLL”: an aggregate assertion can
support an airport inventory fact but must not be split into four system links
unless another decision explicitly says what it supports. Normally a discrete
assertion links once; exceptional multi-system support remains possible.

Minimum link fields: assertion ID, nullable Installation ID, outcome,
evidence-quality, review-state, reason/notes, actor, decided-at, supersedes ID.
The nullable target permits recorded unresolved/candidate review without
pretending identity. Do not place this meaning in `SourceAssertion` or
`Installation.source_id`: both lose many-to-many evidence and audit history.

## 4. Aggregate evidence handling

FAA’s 70 Tableau assertions are airport/type inventory evidence. They prove
only that the stated product exists somewhere at that airport at the captured
source granularity. They create no system and do not establish a count,
runway/end, current lifecycle, or correspondence to an existing Installation.

When later evidence says two or three systems, retain all claims: the aggregate
stays unlinked (or may be linked only to an airport-level inventory statement,
not systems); discrete assertions can support systems individually. A stated
count without locations remains an aggregate/count assertion, not arbitrary
new systems. Staleness is represented by source retrieval/date and later
assertions, never by overwriting the old claim.

## 5. Physical identity rules

Strong evidence: explicit system ID; exact airport/runway/end plus explicit
single-system language; official predecessor/successor statement; direct
official project/engineering identity tied to one system. Only a human-reviewed
strong assertion (or reviewed non-conflicting medium combination) may establish
`SAME`.

Medium evidence: compatible dimensions, date, product, vendor wording,
coordinates supported by text, project title, or independent corroboration.
It proposes review only. Weak evidence—same airport/product/year/vendor,
centroid, count, or text similarity—never establishes identity. Automation may
store/extract and propose; it may not make a reviewed `SAME`, `DIFFERENT`,
lifecycle, vendor, or replacement decision. Even deterministic direct evidence
should require one lightweight human approval because a parser can misclassify
granularity.

## 6. Runway/end rules

Unknown remains null. A supported runway with unknown end links to `Runway`
and leaves end null. A supported end uses the raw value plus canonical runway/end
only when direct evidence supports it. Current free-text `runway_end` is
sufficient short-term because it is optional and permits multiple systems at
one end; retain raw runway/end wording in assertions. Do not redesign Runway.

Renamed/historical designations remain raw assertion text until an explicit
airport runway alias/history capability is needed. A source giving only a
number/direction ambiguously is not canonicalized. Conflicting candidates are
separate assertions and `UNRESOLVED`, not a chosen end. Opposite ends are
strong evidence of different systems only when the source identifies discrete
systems; multiple systems at one end remain possible.

## 7. Replacement and history

A replacement is a new physical Installation when evidence establishes a new
successor. Keep predecessor and successor, then add a future nullable
`predecessor_installation_id` only if one-to-one replacement is sufficient;
prefer a small `InstallationRelationship` table (`replaces`, evidence link,
review/audit fields) because one project can replace several systems and a
system can have several refurbishment events. Refurbishment is an assertion/
event, not replacement unless source language says so. Uncertain history stays
as historical assertion plus unresolved decision.

## 8. Signal/project handoff

For BOS Runway 27, grant/construction assertions and Signal describe a project,
not an operational system. `Signal.category` says what project it is;
`Signal.status` says its project phase. On direct completion evidence, create a
new physical Installation only after review, retain the project assertion and
link it as supporting evidence; do not “graduate” merely from Signal completed.
Installation lifecycle is separately evidence-backed (`operational`, `under_replacement`,
`replaced`, `retired_removed`, `historical_unknown`).

## 9. AI/n8n boundary

AI may capture artifacts, create unreviewed assertions, normalize candidates
while retaining raw values, propose links/differences/replacements, and explain
evidence/conflicts. It may not establish physical identity, publish claims,
choose runway/end/vendor/year/lifecycle, merge rows, or supersede a human
decision. Human approval is required for every identity/replacement/lifecycle/
vendor confirmation; this is one review action per proposed decision, not the
former multi-stage workflow.

## 10. Existing-data pilot cases

| Airport | Legacy rows / assertions | Supported reading; unresolved evidence needed |
|---|---|---|
| JFK | FAA aggregate; 2016 fact-sheet legacy row says 2 systems, `1996(1999)/2007(2014)`. One FAA aggregate assertion. | At least historical count claim is recoverable only through legacy notes; no current discrete SourceAssertion. Do not map aggregate to systems. Need fact-sheet table locator/raw entry and system/location evidence. |
| BOS | FAA aggregate; fact-sheet count/history row; three USAspending project assertions. | Aggregate only; grants can support projects, not current systems. Fact sheet reports two historical systems and future Runway 27 is project evidence. Need direct completion/location records. |
| CGF | FAA aggregate; two 2018 end-specific curated rows (06/24). | The two cited end-specific claims are strong evidence of different systems, but are not yet SourceAssertions. Need preserved source fragments/locators before links; never infer Runway FK. |
| MDW | FAA greenEMAS aggregate; 2014 22L greenEMAS curated row; 2006/07 EMASMAX two-system fact-sheet row. | Product/history coexist; none may be mechanically merged. 22L direct claim is candidate strong after source extraction; fact-sheet count/location remains unresolved. |
| ORD | FAA EMASMAX aggregate; greenEMAS curated row; 2008 two-system fact-sheet row. | Coexistence does not establish correspondence. Need locations/system IDs or direct project records. |
| LGA | FAA aggregate; fact-sheet says 2011 count 2 then 2016 count 4 with 2005(2014)/2015 wording. | Count/history supports aggregate historical evidence, not four identities. Need source-row locations and replacement details. |
| FLL | FAA aggregate lists four equipped ends in legacy note; fact-sheet says 4 systems, 2004/2014. | Aggregate/count claims do not pair systems to ends. Need official end/system evidence and flood-replacement documents. |

## 11. Current 100 SourceAssertions

All 70 `airport_inventory` assertions are aggregate-only and cannot establish
a physical identity. All 30 `project_construction` assertions (25 USAspending,
3 IIJA, 2 construction report) may later contribute project/location/completion
evidence but cannot establish an installed physical system today. None of the
100 should link to an Installation now.

## 12. Legacy transition strategy

Phase 1 preserves raw source assertions (done only for recoverable families).
Phase 2 extracts fact-sheet/manual source records with locators and classifies
them without touching legacy rows. Phase 3 creates reviewed physical
Installations only where discrete evidence exists, with explicit legacy-row to
physical-system mapping/audit records. Aggregate legacy rows may map to zero.
Phase 4 adds reviewed reconciliation and historical relationships. Every phase
is reversible via append-only decisions and backup/validation; ambiguity stays
unmapped.

## 13. Minimal proposed persistence model

Reuse Airport, Runway, Source, SourceAssertion, Signal, and Installation.
Add only: (1) `InstallationAssertionLink` for many-to-many, outcome and audit;
(2) `InstallationRelationship` for replacement/history graph; (3) explicit
Installation lifecycle fields only when backed by linked evidence. Existing
fields cannot safely provide link outcomes/audit/many-to-many or multi-system
replacement history. Do not add a general workflow engine.

## 14. Public/map consequences

Future public output should distinguish reviewed airport-level evidence from
reviewed physical systems, projects/under-construction, operational and
historical/replaced systems, and unresolved claims. Maps must not draw an
unknown or aggregate assertion as a bed; show airport-level evidence without
false precision, and draw physical locations only when reviewed direct location
evidence exists.

## 15. Multilingual consequences

All domain keys remain stable, language-neutral machine values (for example
`project_construction`, `operational`, `replaced`, `SAME_PHYSICAL_INSTALLATION`).
Swedish/English labels live in presentation mappings, never database identity,
source evidence, or decision values.

## 16. Explicit non-goals

No implementation, migration, source import change, data reconciliation,
deduplication, physical links, UI/routes/export changes, AI automation, or
restoration of Observation→Verification→Fact→Intelligence is proposed here.

## 17. Recommended implementation slices

1. Backfill fact-sheet/manual source records with raw artifacts/locators only.
2. Add internal link/decision persistence and audit tests, with no auto-link.
3. Pilot human-reviewed CGF end-specific links and replacement relationships.
4. Add lifecycle/project handoff and only then public reviewed presentation.

## ARCHITECTURE DECISION

Adopt `Installation` as a physical-system record plus a small,
many-to-many `InstallationAssertionLink` carrying reviewed SAME/DIFFERENT/
UNRESOLVED decisions and audit metadata, and a narrow relationship record for
replacement history. This is the smallest model that preserves aggregate and
raw evidence, supports several systems per airport/end, remains reversible,
and avoids both identity guessing and a return to the former workflow
architecture.
