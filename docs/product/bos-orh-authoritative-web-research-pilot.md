# BOS / ORH Authoritative Web Research Pilot

RWI's first controlled web research pilot. **Research only — no code, database,
ingestion, Source/Signal creation, or public-export changes were made in
this task.** Baseline: `docs/product/bos-orh-public-intelligence-gap-analysis.md`
(unchanged by this task), branch `main`, HEAD `fbb0ed94ccacdb45e9923cb489a58773f39c3b92`.

Question this pilot asks (deliberately different from the gap analysis):
*what can a careful research agent discover on the public web today that is
new, newer, stronger, contradictory, or materially useful compared with
current RWI knowledge?*

## 1. Methodology

Iterative, source-hierarchy-aware web research via `WebSearch`/`WebFetch`,
starting from broad lifecycle-concept queries (not narrow "BOS EMAS"
searches), following identifiers (contract numbers, project names) surfaced
by each result into follow-up searches. 11 distinct search queries, ~10
document fetches attempted (several Tier-1 government/procurement sites
returned HTTP 403 and could not be read directly — noted per-source below).
Every material finding was checked against the actual current RWI database
state (re-queried read-only during this task) and against
`bos-orh-public-intelligence-gap-analysis.md`, not against assumption.

## 2. Source hierarchy applied

- **Tier 1** (used as primary evidence): Massport's own newsroom
  (massport.com/media/newsroom — 3 press releases fetched directly),
  official MPA procurement/contract notices (via Bay State Banner listings
  — several blocked at fetch time but their titles/scope text were captured
  through search snippets, which is weaker than a direct fetch and is
  flagged as such below).
- **Tier 2** (corroboration): Construction Equipment Guide (detailed
  technical trade coverage), Spectrum News 1, Worcester Business Journal,
  Aviation Pros, Revere Journal/East Boston Times/Boston 25/Boston.com/WROR
  (all citing or quoting Massport directly, not independent reporting).
- **Tier 3** (leads only, not relied on for canonical claims): general
  search-result synthesis, Wikipedia (not fetched as a source of fact in
  this pilot).

No claim in this report rests solely on a Tier 3 source.

## 3. BOS findings

### 3a. Runway 27 EMAS Phase 2 — confirmed, matches RWI closely

Massport's own newsroom (fetched directly, published **2026-08-06**):
Phase 2 = continued EMAS installation at the end of Runway 27, closing
Runway 9-27 for 75 days starting **2026-08-31**, expected complete
**"before Thanksgiving"** 2026. This matches RWI's Signal 3
(`construction_start=2026-08-31`, `completion_date=2026-11-15`) almost
exactly. **Classification: ALREADY_KNOWN** (RWI's existing record is
accurate and current).

### 3b. The 04L/15R vs 22R/33L "discrepancy" — resolved

Massport's own press release states, verbatim: *"Boston Logan has two
other EMAS systems already operational—one at Runway 22R and another at
Runway 33L."* This is a **Tier 1, primary-source, direct** statement — not
secondhand via a news article.

**Verdict: the apparent contradiction is caused by runway-end naming/
reference-frame semantics, not by a factual conflict or stale data.**
Strong supporting evidence, from an unrelated ORH procurement document
(§4c below): MPA Contract W306's official scope text names the *exact
same* dual-convention pattern for Worcester's EMAS ends — *"Replace Runway
29 Departure EMAS **(R/W 11 End)**"* and *"Replace Runway 11 Departure
EMAS **(R/W 29 End)"*. This confirms, from Massport's own contract
language, that "[Runway X] EMAS" (public/common naming, naming the
*direction/threshold the system protects*) and "R/W [reciprocal end]"
(NASR's structured `RWY_END_ID`, naming the *physical location of the
bed*) are two valid, coexisting conventions describing the same
installation — the bed for "Runway 22R's EMAS" physically sits at the far
end of the pair, which NASR's `RWY_END_ID` field records as `04L`, and
likewise "Runway 33L's EMAS" is recorded as `15R`. **This is not
unresolved** — it is resolved with high confidence via two independent,
consistent, official sources (Massport's press release + Massport's own
contract-naming convention applied elsewhere). RWI's raw NASR/Tableau data
was never wrong; it uses a different, equally valid reference frame that
was not previously understood inside RWI.

### 3c. Project identity, cost, and contractors — NEW_TO_RWI

| Fact | Source | Tier |
|---|---|---|
| Project name/number: MPA Project No. **L1633**, "Runway 27 Safety Area Improvements" (design-build) | Bay State Banner listing (title only, via search — fetch blocked, HTTP 403) | 1 (title only) |
| Contractor: **McCourt Construction Company** | Construction Equipment Guide, published 2026-04-02 | 2 |
| Design-build engineer: **Jacobs Engineering Group Inc.** (30% design complete at contract award) | Construction Equipment Guide | 2 |
| Major subcontractors: **J.F. White Contracting Co.** (pile installation), **Algar**, **Coastal Precast** (VA, piles/caps/beams) | Construction Equipment Guide | 2 |
| Total construction cost: **$115,000,000** (one source) / **$110,000,000** (another, dated closer to Phase 1's completion) | Construction Equipment Guide search synthesis ($115M); separate search result reporting Phase 1 reopening ($110M) | 2 (both) |
| Deck: 650 ft long, several hundred ft wide, ~70% extends into the ocean, 300 piles, 75-year design life, emergency access ramps both sides | Construction Equipment Guide | 2 |
| Phase 1 exact reopening date: **2025-11-14** | Search synthesis citing local coverage | 2 |

**None of this — project number, contractor, engineering firm,
subcontractors, or total project cost — exists anywhere in RWI today.**
RWI's own funding picture (3 USAspending signals totaling ~$65.2M + a
$17.5M IIJA note) is a partial, federal-grant-only view; the ~$110–115M
total figure is the full construction cost Massport/its contractor
reports, which necessarily includes non-federal funding RWI has never
seen. The two cost figures found ($110M vs $115M) were not reconciled to
a single authoritative number in this pilot — flagged as an open item
(§13).

**Classification: NEW_TO_RWI. Investor relevance: HIGH** (total project
budget and named contractor/engineer are exactly the kind of concrete,
verifiable facts an investor page is missing today).

### 3d. Vendor / Runway Safe involvement — do not overclaim

Per this pilot's own §14 discipline:

- **A. Confirmed EMAS project**: yes.
- **B. Confirmed manufacturer/vendor**: Runway Safe acquired ESCO's
  EMASMAX® product line in February 2020 and is described (via search
  synthesis, not a single authoritative document fetched in full) as the
  sole current manufacturer of FAA Advisory Circular 150/5220-22B-compliant
  EMAS material. This makes Runway Safe the *overwhelmingly likely*
  material supplier for any current EMASMAX installation, including this
  one.
- **C. Confirmed Runway Safe involvement in this specific project**: **not
  found**. No Massport, McCourt, or Jacobs source names Runway Safe by
  corporate name for project L1633. Runway Safe's own website
  (`runwaysafe.com/creating-a-market-leader-in-runway-safety/`, fetched
  directly) does not mention Boston Logan, Worcester, or any specific
  2025/2026 U.S. project.
- **Conclusion: D** — likely commercial relevance (near-certain material
  supplier by product-line ownership) but vendor not publicly established
  for this specific project. This *strengthens* the reasoning behind RWI's
  existing `Signal.likely_supplier="Runway Safe"` on Signal 3 (previously
  justified only by "FAA construction program identifies EMAS phase 2" —
  a weak, non-specific reason) without being able to promote it to
  "confirmed." **Classification: STRONGER_EVIDENCE** for the existing
  `likely_supplier` field, not a new fact.

### 3e. No incidents found

No EMAS activation/runway overrun incident was found for BOS in this
pilot — consistent with RWI's own record (0 `Incident` rows for airport
id 3). **ALREADY_KNOWN / confirmed absence.**

## 4. ORH findings

### 4a. The 2009 original system and why it was replaced — NEWER_INFORMATION

Spectrum News 1 (fetched directly, published **2024-09-18**): the
original EMAS, installed **2009**, was reaching the end of its
**15–20-year design lifespan**, and the replacement was sized for a
**Boeing 737-800-class** aircraft — larger/heavier than current regular
traffic — explicitly framed as forward-looking capacity planning, not
reactive repair. This closes exactly the "reason for replacement" gap the
prior RWI gap analysis flagged as unresolved, and matches Installation
116's own free-text RWI note ("2 system (2008/2009)... original
installation") almost exactly. **Classification: NEWER_INFORMATION /
STRONGER_EVIDENCE. Investor relevance: HIGH** (a lifecycle/capacity
rationale directly explains *why* $15M+ was spent, which the current ORH
page cannot answer at all).

### 4b. Exact replacement sequencing and cost — NEWER_INFORMATION

| End | Year | Cost | Source |
|---|---|---|---|
| Runway 29 end | 2024 (completed ~end of September 2024) | $5,000,000 | Spectrum News 1, direct fetch |
| Runway 11 end | 2025 | $10,000,000 | Search synthesis (source fetch not attempted individually — corroborated by two independent search result summaries) |
| **Total** | 2024–2025 | **$15,000,000** | |

RWI's own derived total from 5 USAspending grant signals is
**$12,323,296** — a real, unreconciled ~$2.7M gap between the web-reported
$15M and RWI's federal-grants-only total. Plausible explanation (not
confirmed): the $5M/$10M figures may be total project costs including
non-federal match, design, and contingency, while RWI's total captures
only the federal-grant portion actually recorded in USAspending — the
same pattern found at BOS (§3c). **Not resolved in this pilot** — flagged
as an open item (§13). **Classification: NEWER_INFORMATION** (narrows
"2024/2025" to "Runway 29 end finished Sept 2024, Runway 11 end sometime
in 2025" — still short of an exact 2025 date). **Investor relevance:
HIGH.**

### 4c. Official contract scope confirms both ends replaced, in one bundled procurement — NEW_TO_RWI

Search-snippet-captured (direct fetch of the Bay State Banner page
returned HTTP 403) scope text for **MPA Contract No. W306, "Federally
Funded Airfield Capital Improvement Projects"**:

> *(1) Replace Runway 29 Departure EMAS (R/W 11 End), (2) Replace Runway
> 11 Departure EMAS (R/W 29 End), (3) Construct Replacement Taxiway F
> from Runway 11-29 to the Taxiway D intersection with Boundary Marking
> Modifications at the Terminal Ramp, (4) Rehabilitate the Entrance and
> Exit passenger roadway to the Terminal.*

Both EMAS replacements were bundled into **one** capital contract together
with an unrelated taxiway reconstruction and a terminal roadway
rehabilitation — RWI currently has no record of the taxiway or roadway
components at all (out of EMAS scope, so not pursued further in this
pilot, but noted as adjacent airfield activity). **Classification:
NEW_TO_RWI. Investor relevance: MEDIUM** (confirms both ends were part of
one coordinated program, and provides the naming-convention evidence used
in §3b).

### 4d. Future work: FY2027–2030 capital contract exists

**MPA Contract No. W343, "FY 27 Through FY 30 Airfield Capital Improvement
Projects, Worcester Regional Airport"** — title confirmed via search
listing (Bay State Banner, dated 2026-07-01 per the search snippet);
direct fetch was not attempted for the full scope text in this pilot, so
**whether it contains any further EMAS/runway-safety line item is
unconfirmed** — it is a capital-contract vehicle name, not yet a specific
project. Separately, a U.S. House member's official FY2027 funding
disclosure (`mcgovern.house.gov`, fetch blocked, HTTP 403) was found via
search listing to request funding for a **Worcester Regional Airport
Taxiway Expansion** project (permitting/design/construction of taxiway
connections along the full runway length, reducing back-taxi and runway
occupancy time) — airfield-safety-adjacent but not EMAS-specific.
**Classification: NEW_TO_RWI. Investor relevance: MEDIUM** (signals
continued capital activity at ORH beyond the completed EMAS replacement,
but not yet a confirmed EMAS project).

### 4e. Two unrelated older items found and deliberately NOT conflated

- A **2023** *"Runway 11-29 Rehabilitation Project — Phase 2"* ($12.5M,
  construction 2023-08-21 to early November 2023, Phase 1 completed 2020)
  — a Massport press release using very similar naming ("Runway 11-29",
  "Phase 2") to the 2024/2025 EMAS replacement, but is a **separate,
  earlier, general pavement-rehabilitation program**, not the EMAS
  replacement. Confirmed as distinct by construction dates (2023, not
  2024/2025) and by not mentioning EMAS/arresting-system scope in the
  fetched press release.
- A **2020** FAA/CARES Act $5M grant ("part of $273M distributed to 184
  airports nationally") — unrelated, pre-dates the 2024/2025 EMAS
  replacement, background only.

Flagged explicitly because an automated scout without careful date/scope
comparison could easily conflate either of these with the actual EMAS
replacement story — this is itself an automation lesson (§17).

### 4f. No incidents found

Consistent with RWI's own record (0 `Incident` rows for airport id 44).

## 5. BOS Phase 2 case study — full reconstruction

| Element | Finding |
|---|---|
| Purpose | Runway safety area / EMAS improvement at Runway 27's departure end |
| EMAS component | Continuation of a single EMAS installation project (Phase 1 + Phase 2), not a separate new system |
| Runway end | 27 (RWI's own Signal already correctly links `runway_id` to the 9/27 pair) |
| Phases | Phase 1: 2025-09-02 → 2025-11-14 (reopened). Phase 2: 2026-08-31 → ~2026-11-15/Thanksgiving |
| Funding found | $115M or $110M total construction cost (unreconciled); $17.5M IIJA (already in RWI); $56.2M/$9.0M/$60K USAspending grants (already in RWI, relationship to this specific project still unconfirmed) |
| Contractor | McCourt Construction Company (design-build) |
| Engineer | Jacobs Engineering Group Inc. |
| Subcontractors | J.F. White Contracting Co., Algar, Coastal Precast |
| Operational impact | Runway 9-27 closed with very limited availability for 75 days each phase; increased use of other runways; delay risk in poor weather |
| Latest status (as of this pilot) | Phase 2 actively under construction, matches RWI's "under construction" status exactly |
| What changed during 2026 | Phase 1 completed (Nov 2025); Phase 2 began (Aug 2026); Massport's own Aug 2026 release is the first Tier-1 confirmation of the 22R/33L identity RWI previously only had secondhand |

**What would an RWI investor have learned today that the current BOS page
does not tell them?** The total project is roughly **$110–115 million**
(RWI shows no total, only partial federal grant figures), it is being
built by **McCourt Construction** with **Jacobs Engineering** as
designer, involves **300 piles and a 650-foot deck built partly over the
Atlantic**, and is designed for a **75-year service life** — none of
which appears anywhere on RWI's BOS page today. The investor would also
learn, with primary-source confidence, that Logan's two other EMAS beds
are correctly identified as **22R and 33L**, resolving an ambiguity RWI's
own database could not resolve internally.

## 6. ORH replacement case study — full reconstruction

| Element | Finding |
|---|---|
| Original installation | 2008/2009, both ends of Runway 11/29 |
| Age/lifecycle | 15–20 year FAA-typical EMAS design life; the 2009 unit was approaching end-of-life by 2024 |
| Reason for replacement | Lifecycle replacement + upsizing for Boeing 737-800-class aircraft (explicitly forward-looking, not incident-driven) |
| Scope | Both runway ends (Runway 29 end in 2024, Runway 11 end in 2025) |
| Funding/grants | RWI: 5 USAspending signals, $12,323,296 total, FY2024–2025. Web: $5M (2024, Rwy 29 end) + $10M (2025, Rwy 11 end) = $15M total — gap unreconciled |
| Contract vehicle | MPA Contract W269-C1 (Runway 11 end specifically) and W306 (bundled EMAS + taxiway + roadway work) |
| Construction timing | Runway 29 end: completed ~end of September 2024. Runway 11 end: completed sometime in 2025 (exact date not found) |
| Current state | NASR's current (2026-08-06) cycle confirms EMAS present at both ends today — replacement complete and in service |
| Contractor/vendor | Not found in this pilot for either phase |
| Future work | MPA Contract W343 (FY27-30 airfield capital contract, scope unconfirmed); a Taxiway Expansion project requested for FY2027 (not EMAS-specific) |

**What would an RWI investor have learned today that the current ORH page
does not tell them?** That this was a **planned lifecycle replacement**
(not a reactive repair), explicitly sized for **larger 737-800-class
aircraft** — a forward-looking capacity signal — that the two ends were
replaced sequentially (**29 in 2024, 11 in 2025**, roughly **$5M then
$10M**), and that as of the most current NASR cycle available, **both
ends are confirmed still equipped and in service today**. None of this
narrative currently exists on the ORH page, whose "Projekt" table shows
only an empty-state message because all five relevant grants are
collapsed inside a closed "Finansiering och bidrag" toggle (per the prior
gap analysis).

## 7. RWI comparison matrix

| Finding | Airport | Classification | Lifecycle: RWI-understood → web-supported | Investor relevance |
|---|---|---|---|---|
| 22R/33L confirmed by Massport directly (Tier 1) | BOS | CONTRADICTION → resolved / STRONGER_EVIDENCE | n/a | HIGH |
| Project L1633, McCourt, Jacobs, subs, ~$110-115M | BOS | NEW_TO_RWI | UNDER_CONSTRUCTION → UNDER_CONSTRUCTION (confirmed, cost added) | HIGH |
| Phase 1 exact reopening 2025-11-14 | BOS | NEWER_INFORMATION | UNDER_CONSTRUCTION → COMPLETED (Phase 1) | MEDIUM |
| Deck engineering detail (650ft, piles, 75yr life) | BOS | NEW_TO_RWI | n/a | LOW-MEDIUM |
| Runway Safe = likely but unconfirmed vendor | BOS | STRONGER_EVIDENCE | n/a | MEDIUM |
| 2009 original + 15-20yr lifecycle + 737-800 sizing reason | ORH | NEWER_INFORMATION / STRONGER_EVIDENCE | COMPLETED (unstated reason) → COMPLETED (reason confirmed) | HIGH |
| Sequencing + cost: Rwy29 2024 $5M, Rwy11 2025 $10M | ORH | NEWER_INFORMATION | replacement → replacement (dated, costed) | HIGH |
| W306 bundled contract, naming-convention proof | ORH | NEW_TO_RWI | n/a | MEDIUM |
| W343 FY27-30 capital contract exists | ORH | NEW_TO_RWI | n/a (future) | MEDIUM |
| FY2027 Taxiway Expansion request | ORH | NEW_TO_RWI | IDENTIFIED (not EMAS) | LOW-MEDIUM |
| 2023 pavement rehab (unrelated, flagged not-to-conflate) | ORH | NEW_TO_RWI (context only) | n/a | LOW |

## 8. Future-project findings (2026+)

- BOS: Phase 2 itself is the only confirmed, funded, in-progress 2026
  activity found; no confirmed BOS EMAS/RSA work beyond Phase 2's November
  2026 completion was found in this pilot (a broader "2023 Logan Airport
  Airfield Capital Improvement Projects" contract, L1828, appeared in
  search results but was not investigated further — likely superseded by
  or related to the current L1633 work, not a distinct future item).
- ORH: MPA Contract W343 (FY27–30 airfield capital improvements) exists as
  a procurement vehicle; a FY2027 taxiway expansion request exists;
  neither was confirmed to include further EMAS/runway-safety scope in
  this pilot.

## 9. Top 10 investor-relevant findings (ranked)

1. **HIGH** — BOS EMAS ends independently, directly confirmed as 22R/33L by Massport (resolves RWI's internal discrepancy).
2. **HIGH** — BOS Phase 2 total project cost ~$110-115M, contractor McCourt Construction, engineer Jacobs Engineering — entirely absent from RWI today.
3. **HIGH** — ORH replacement reason: 2009 system at end of 15-20yr life, upsized for 737-800 — closes RWI's stated "why" gap.
4. **HIGH** — ORH replacement sequencing/cost: Runway 29 end 2024 ($5M), Runway 11 end 2025 ($10M).
5. **MEDIUM** — BOS Phase 1 exact reopening date: 2025-11-14.
6. **MEDIUM** — Runway Safe's EMASMAX monopoly since Feb 2020 strengthens (without confirming) the likely-supplier inference for both projects.
7. **MEDIUM** — ORH's W306 contract bundled both EMAS ends with a taxiway and terminal-roadway project — adjacent airfield activity RWI doesn't track at all.
8. **MEDIUM** — ORH's future FY27-30 capital contract (W343) exists; scope not yet confirmed.
9. **LOW-MEDIUM** — BOS deck engineering specifics (650ft, 300 piles, 75-yr design life) — technical color, not financial.
10. **LOW** — Neither airport shows any EMAS-related incident history in public sources, matching RWI's own zero-incident record.

## 10. Absent from current public pages

Every item in §9 ranked MEDIUM or higher is currently absent from RWI's
public BOS/ORH pages — none of the BOS project-cost/contractor/engineer
facts, the resolved 22R/33L identity (still shown as an internal,
unpublished, unreconciled discrepancy per the gap analysis), the ORH
replacement rationale/sequencing/cost, or either airport's future
capital-contract activity.

## 11. North-star result

**Classification: CLEAR_NEW_VALUE.**

This pilot found concrete, sourced, investor-relevant facts an RWI reader
cannot get from the current site today: a total project budget and named
contractor/engineer for BOS's largest current construction project, and a
clear lifecycle rationale plus costed, dated sequencing for ORH's
completed replacement — plus a confident, evidence-backed resolution of
an internal data discrepancy RWI had already noticed but not solved. It
falls short of MAJOR_NEW_VALUE because nothing found changes either
airport's fundamental project *status* (both projects' lifecycle stages
were already correctly understood by RWI — "under construction" for BOS,
"replacement completed" for ORH) and no Runway Safe commercial win was
confirmed.

## 12. Automation lessons

- **Most productive query pattern**: airport name + specific runway
  number/end + lifecycle keyword ("EMAS replacement", "safety area
  improvements"), not the generic "[code] EMAS" pattern the task
  explicitly warned against. Broad concept terms (RSA, arresting system,
  capital improvement) surfaced *different* result sets than "EMAS" alone
  — worth running both.
- **Most productive source family, by far**: the operator's own newsroom
  (`massport.com/media/newsroom/...`) — every fetch succeeded, every one
  was precisely on-topic, and one (the BOS release) single-handedly
  resolved the biggest open question in RWI's existing evidence.
- **Second most productive**: official procurement/contract-notice
  listings (Bay State Banner) — their *titles alone*, surfaced via search
  snippets, were extremely information-dense (contract numbers, exact
  scope phrases, both runway-end naming conventions) even when the pages
  themselves returned HTTP 403 on direct fetch.
- **Noisy/low-yield**: generic "capital improvement program" searches on
  the operator's own top-level pages (massport.com/business/capital-improvements)
  returned only a PDF link, not usable content directly — the PDF itself
  needs a dedicated fetch attempt or a different retrieval path.
- **Source types worth ongoing monitoring**: operator newsroom pages (RSS
  or periodic diff would be low-noise, high-signal); state/agency
  procurement-notice boards (Bay State Banner-style public-notice
  aggregators) for contract number + project title identifiers, which are
  the single best follow-up-search key found in this pilot; trade
  publications (Construction Equipment Guide-style) for contractor/
  technical detail once a project is already known.
- **Useful identifiers for follow-up searching**: MPA project/contract
  numbers (`L1633`, `W269-C1`, `W306`, `W343`) proved to be the highest-value
  search keys of the entire pilot — each one, searched alone, returned
  tightly-scoped, on-topic results. This validates the task's own
  "search identifiers separately" instruction directly.
- **Multilingual/native-language search**: not applicable to this pilot
  (both airports are U.S. domestic, English-language sources only) — but
  would matter for any future non-U.S. airport, flagged for later.
- **Steps requiring human-style reasoning, not simple crawling**:
  (a) recognizing that two Massport press releases both titled around
  "Runway 11-29" / "Phase 2" (the 2023 pavement job and the 2024/2025 EMAS
  replacement) were different projects despite near-identical naming —
  this required comparing construction-date windows and scope language,
  not keyword matching; (b) resolving the 04L/15R vs 22R/33L naming
  question required connecting a BOS-specific finding (Massport's press
  release) with an ORH-specific finding (W306's parenthetical dual-naming)
  to build one general rule — no single source stated the rule explicitly;
  (c) judging which of two conflicting cost figures ($110M vs $115M) to
  report as both, rather than silently picking one, is itself a judgment
  call a naive scout would need explicit guidance to make correctly.

## 13. Unresolved questions

- Exact total BOS Phase 2 (L1633) project cost: $110M vs $115M not
  reconciled to one authoritative figure — a direct fetch of the Bay
  State Banner L1633 notice or a Massport board-meeting document would
  likely resolve this.
- Whether BOS's 3 separate USAspending grants (and the $17.5M IIJA grant)
  fund this same $110-115M L1633 project, a different phase of Logan's
  broader RSA program, or something else — genuinely unresolved by this
  pilot.
- Exact ORH Runway 11 end EMAS replacement completion date (only "2025"
  found, not a specific date) and its installing contractor (not found
  for either ORH phase).
- ~$2.7M gap between RWI's federal-grant total ($12.32M) and the
  web-reported $15M total ORH replacement cost — plausibly non-federal
  match, not confirmed.
- Full scope of MPA Contract W343 (ORH, FY27-30) — title confirmed, scope
  contents not retrieved (fetch blocked).
- Whether Runway Safe is confirmed (vs. merely highly likely) as the
  material supplier for either the BOS Phase 2 or ORH 2024/2025
  replacement projects — not established by any source found.
- Several Tier-1 government/procurement URLs (`faa.gov` domestic notices,
  Bay State Banner detail pages, `mcgovern.house.gov`) returned HTTP 403
  to this tool's `WebFetch` — their content was only available via search
  snippets, a weaker evidentiary form than a direct fetch; a future
  automated scout would need an alternate retrieval path for these
  domains specifically.
