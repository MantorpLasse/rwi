# Evidence Identity Slice 6E: MDW Reconciliation Stress Test

## Verdict

MDW cannot yet be reconciled into historical physical-bed continuity. The
preserved FAA NASR cycle establishes **four current EMAS-equipped runway ends**:
`04R`, `22L`, `13L`, and `31R`. It does not establish four historical
installations, installation year, vendor/product, or a replacement sequence.

The current Slice 6C model remains sufficient because it can represent reviewed
current identities and leave all unresolved history outside canonical identity.
No schema change is required before a narrow current-evidence pilot. However,
the recommended next slice is **C: the smallest lifecycle/designation evidence
design**, before reconciling MDW, because same-end continuity across time and
the renamed runway are not established by the retained evidence.

## 1. Read-only MDW inventory

| Area | Preserved state |
|---|---|
| Airport | ID 12, Chicago Midway International Airport, FAA/IATA `MDW`. |
| Current Runway row | ID 12, current designation `13L/31R`; notes preserve the 2025 redesignation history. |
| Legacy Installations | 26: FAA aggregate greenEMAS; 74: 2014 greenEMAS at 22L; 109: historical EMASMAX/count claim. |
| Incidents | None. |
| Signals | 12: speculative internal lifecycle watch; 68: official CIP design-phase “EMAS Bed Repairs.” |
| Physical identities / links | None for MDW. CGF identities/links are unrelated. |

Signal 12 is an internal watch item and its private assessment is not public
evidence. This report uses only its public source-note classification. Signal
68 is preserved project evidence; it has no runway link.

## 2. Assertion matrix

| ID | Source and date | Type/granularity | Preserved claim | Reading |
|---|---|---|---|---|
| 26 | FAA Tableau source 12; retrieved 2026-07-22 | `airport_inventory` / aggregate | `greenEMAS` at MDW | Airport/type inventory only. |
| 103 | PRWeb source 52; published 2015-06-17 | `runway_end` / discrete | First greenEMAS bed completed Nov 2014 on `22L` | Historical location/event claim. |
| 106 | FAA fact sheet source 56; published 2011-03-07, retrieved 2026-07-26 | `historical` / count | `4`, raw `2006/2007` | Historical count/year wording only; no ends. |
| 145 | FAA NASR source 69; effective 2026-08-06, retrieved 2026-08-16 | `runway_end` / discrete | `04R` on raw runway `04R/22L`, EMAS | Current end presence. |
| 146 | FAA NASR source 69; effective 2026-08-06 | `runway_end` / discrete | `22L` on `04R/22L`, EMAS | Current end presence. |
| 147 | FAA NASR source 69; effective 2026-08-06 | `runway_end` / discrete | `13L` on `13L/31R`, EMAS | Current end presence. |
| 148 | FAA NASR source 69; effective 2026-08-06 | `runway_end` / discrete | `31R` on `13L/31R`, EMAS | Current end presence. |

All NASR rows preserve APT_ARS locators: lines 168, 169, 170, and 171
respectively, within the preserved 2026-08-06 archive. Assertions 26 and 106
remain visibly aggregate/historical and are not candidates for physical links.

## 3. Current NASR evidence

FAA NASR directly reports `ARREST_DEVICE_CODE = EMAS` at MDW end `04R` and
`22L` on raw runway `04R/22L`, and at ends `13L` and `31R` on raw runway
`13L/31R`. This is authoritative current runway-end presence at the stated
cycle. It says nothing about whether beds are the same physical systems that
existed in 2014 or 2006/2007.

## 4. Preserved runway designation history

| Historical designation | Later/current designation | Preserved support | Confidence |
|---|---|---|---|
| `13C/31C` | `13L/31R` on 2025-06-12 | Current Runway 12 notes and `rename_mdw_runway_13c31c_to_13l31r.py` preserve the City of Chicago press-release summary. | Medium: official publisher asserted, but exact document URL/artifact is not retained. |
| Earlier `13L/31R` | Closed permanently 2025-06-12 | Same preserved summary. | Medium. |

The summary supports a designation change/closure event, not a physical EMAS
bed mapping. It must not create a new identity or prove that any historical
`13C/31C` claim is the same as current `13L/31R`.

## 5. Evidence timeline

| Time | Kind | Preserved fact and limit |
|---|---|---|
| 2006/2007 (claimed) | Historical installation/count wording | Assertion 106 says count 4 and raw years `2006/2007`; it does not identify ends or continuity. |
| 2011-03-07 | FAA publication | Source publication date for assertion 106, not an installation date. |
| Nov 2014 (claimed) | Installation event | Assertion 103 says first greenEMAS bed completed at 22L. |
| 2015-06-17 | PRWeb publication | Publication date of assertion 103 source, distinct from the claimed 2014 completion. |
| 2025 | Project/design | CIP 46109 “EMAS Bed Repairs,” design phase, $880,000; no runway or construction/replacement claim. |
| 2025-06-12 | Designation event | Existing 13C/31C renamed 13L/31R while prior 13L/31R closed, per retained summary. |
| 2026-08-06 | NASR effective cycle | Current EMAS at four ends. |
| 2026-08-16 | RWI NASR retrieval | Source ingestion date, not a physical event. |

## 6. Physical-installation candidates

| Candidate | Classification | Support and limits |
|---|---|---|
| Current 04R | PROBABLE current physical installation | NASR 145 directly identifies EMAS at the end. It is enough for a future reviewed current identity, but not historical continuity. |
| Current 22L | PROBABLE current physical installation | NASR 146 and historical assertion 103 identify the same end at different times. Location is clear; continuity is not. |
| Current 13L | PROBABLE current physical installation | NASR 147 directly identifies current EMAS. Redesignation history prevents historical continuity inference. |
| Current 31R | PROBABLE current physical installation | NASR 148 directly identifies current EMAS. Same redesignation limitation. |
| 2006/2007 systems | INSUFFICIENT as identities | FAA historical count 4 has no runway/end/system locations. |
| Airport greenEMAS inventory | AMBIGUOUS aggregate | Tableau assertion 26 cannot be divided into systems. |

“Probable” deliberately means source-supported current end presence that could
become a human-reviewed current identity. It is not a claim of complete
lifecycle identity or count across time.

## 7. MDW 22L continuity check

Assertions 103 and 146 clearly describe the same named runway end, `22L`.
They establish a 2014 greenEMAS completion claim and NASR current EMAS presence
in 2026. They do **not** establish that the same physical bed persisted:
there may have been repair, retrofit, or replacement, and preserved evidence
does not distinguish those possibilities. Same runway end is not sufficient for
a SAME_PHYSICAL_INSTALLATION decision across the interval.

## 8. Repair, replacement, and lifecycle

The official City of Chicago 2025–2029 CIP Report (source 66, page 34) supports
only an MDW project named “EMAS Bed Repairs,” design phase 2025, $880,000, with
no named runway and no construction phase. Classification: **repair/planning**;
not partial reconstruction, full replacement, or completion. There are no MDW
incidents in RWI to imply a replacement event.

Current persistence can retain this safely as a Signal and Source without a
lifecycle model. Future lifecycle work needs at least evidence-backed event
semantics that distinguish repair from replacement and keep designation events
separate from identity; MDW does not justify adding a broad vocabulary now.

## 9. Legacy Installation comparison

| Legacy row | Classification | Why direct identity link is unsafe |
|---|---|---|
| 26, greenEMAS aggregate | Aggregate-only | It summarizes FAA inventory and four ends in notes; no discrete upstream record or system correspondence. |
| 74, 22L greenEMAS, 2014 | Partially supported historical candidate | Assertion 103 supports its historical 22L/year claim, but not continuity to NASR 146 or the vendor detail present only in legacy material. |
| 109, EMASMAX, 2006 | Historical candidate / unresolved | Its notes discuss older fact-sheet material, while assertion 106 preserves only count 4/raw 2006/2007; it cannot be one physical system. |

## 10. Model and automation stress test

The Slice 6C model can represent reviewed current identities with null runway
FKs where designation history is unresolved, and can leave aggregate/historical
claims unlinked. No model field is needed before MDW work. Replacement needs a
later separate relationship/event layer; it must not be encoded by changing an
identity.

| Evidence type | Automation posture |
|---|---|
| NASR explicit EMAS runway-end row | AUTO-INGEST; PROPOSE-LINK only until reviewed current-identity rules are explicitly approved. |
| FAA Tableau aggregate | AUTO-INGEST; never auto-link to a physical identity. |
| Historical 22L completion and current 22L NASR | PROPOSE-LINK / HUMAN-REVIEW; no automatic continuity. |
| Designation history | AUTO-INGEST preserved evidence; HUMAN-REVIEW for alias/continuity effects. |
| CIP “EMAS Bed Repairs” | AUTO-INGEST project evidence; HUMAN-REVIEW for any lifecycle interpretation. |

This applies human-in-the-loop by exception: routine evidence can flow in
automatically, while the small ambiguous set is surfaced.

## 11. CGF compared with MDW

| CGF | MDW |
|---|---|
| Two discrete ends with corroborating source evidence and no historical conflict. | Four current ends plus aggregate, count/history, redesignation, and repair evidence. |
| Two established reviewed identities. | Four current candidates, but no proven cross-time continuity. |
| No lifecycle question needed for the pilot. | Lifecycle/designation ambiguity is central. |

MDW demonstrates that end-level presence is not the same as durable physical
identity through time, and that aliases, repair projects, and legacy rows must
not be compressed into a convenient match.

## Unresolved questions and next slice

- Is an authentic City of Chicago redesignation source artifact/locator available?
- Is there direct completion/replacement evidence for the 2025 repair project?
- Can source-supported historical locations be recovered for the 2006/2007
  count claim?

**Recommended next slice:** design the smallest evidence-backed lifecycle and
runway-designation handling rules, without schema changes unless a concrete
review case requires them. Do not run an MDW reconciliation pilot first.

## Concise answers

1. **Established physical MDW installations:** zero across time; four current
   end-specific EMAS presences are established by NASR and are future identity
   candidates, not yet reviewed identities.
2. **Ends/designations:** current 04R/22L ends 04R and 22L; current 13L/31R
   ends 13L and 31R; historical 13C/31C-to-13L/31R relationship is only
   partially preserved.
3. **Current vs historical:** NASR four ends are current as of 2026-08-06;
   2014 22L and 2006/2007 count claims are historical.
4. **Same physical bed across history/current:** none can safely be treated as
   the same bed, including 22L.
5. **Repair/replacement:** repair-design evidence exists; complete replacement
   evidence does not.
6. **Redesignation:** it affects canonical runway interpretation, not identity
   automatically.
7. **Slice 6C model:** yes; it truthfully holds current reviewed identities and
   defers unresolved history.
8. **Next action:** lifecycle/designation evidence rules first; do not reconcile
   MDW yet.
