# Evidence Identity Slice 3A — discrete evidence recovery

## 1. Evidence inventory and provenance findings

The repository retains three materially different evidence forms:

1. **Original structured records:** the checked-in FAA CSV (already backfilled
   as 70 airport inventory assertions), USAspending/IIJA/construction records
   (30 project assertions), and NASR `APT_ARS` acquisition input when obtained.
2. **Curated source transcriptions in scripts:** explicit source URL, publisher,
   summary and sometimes runway/end/dimensions, notably CGF and Gadelius.
3. **Legacy interpreted state/notes:** FAA fact-sheet and enrichment rows. These
   are useful research leads but are not uniformly original source records.

No legacy row is itself promoted to physical identity. The present 100
SourceAssertions remain 70 FAA aggregate inventory and 30 project/construction
assertions; none is a discrete physical-system assertion.

## 2. FAA fact-sheet findings

The 2011/2016 fact-sheet scripts use hard-coded mappings and place composite
source wording in notes. They recover airport-level counts/history but not a
faithful per-system source row or table locator. JFK (`2`,
`1996(1999)/2007(2014)`), BOS (`2`, `2005/2006(2012)(2014)`), MDW (`2`,
`2006/2007****`), ORD (`2`, `2008`), LGA (`4`, `2005(2014)/2015`) and FLL
(`4`, `2004, 2014`) must therefore remain raw aggregate/historical evidence.
Parentheses/footnotes are not canonical lifecycle fields. No fact-sheet
backfill is safe until PDF table entries and locators are recovered directly.

## 3. NASR/APT_ARS findings

`import_faa_runway_ends.py` consumes official NASR arresting-system rows and
filters for EMAS. It can provide authoritative airport/runway/end assertions,
but it historically enriches existing Installation rows and preserves its
result only in links/notes. The raw NASR artifact is not checked in as a
recoverable source-record archive. Future capture must make one assertion per
EMAS APT_ARS record with cycle/artifact, `ARPT_ID`, `RWY_ID`, `RWY_END_ID` and
device code. Non-EMAS arresting devices remain excluded.

## 4. Recoverable discrete candidates already preserved

| Candidate | Preserved source claim | Classification |
|---|---|---|
| CGF end 06 | Cuyahoga County completion release; 2018, 322 ft, one bed at end 06. | Direct, discrete runway-end evidence; future physical identity support. |
| CGF end 24 | Same release; 2018, 435 ft, one bed at end 24. | Direct, discrete runway-end evidence; different end supports different systems. |
| MDW 22L | PRWeb text retained in Gadelius script: first greenEMAS bed November 2014 at 22L. | Direct discrete source claim; vendor role still needs source-role wording. |
| Gadelius list: ZRH, RUN, DZA, SCN, HND, NHT, CGH | One official partner list gives airport/year entries. | Discrete airport/year installation claims, but no runway/end/system ID; likely physical-system candidate only after source fragment recovery. |
| ORD greenEMAS | PRWeb says O'Hare received greenEMAS “shortly after” Midway. | Specific product/existence but ambiguous date/location; not discrete identity. |
| CGH | Preserved Brazilian/Gadelius source summaries say greenEMAS installed 2022. | Airport/product/year assertion; no runway/end. |
| WLG / ZQN | Official airport source URLs and curated notes retain completion evidence. | Potentially discrete only after exact page fragment is preserved; WLG says both ends and must not be split without individual-system wording. |

## 5. Aggregate-only and unresolved evidence

FAA Tableau airport/type claims remain aggregate only. JFK, BOS, MDW, ORD,
LGA and FLL fact-sheet counts prove neither a per-end allocation nor a mapping
to legacy rows. Research documents such as `utreding_status_flygplatser.md`
contain useful URLs and conclusions, but are RWI research synthesis rather than
new upstream source fragments. They must not become evidence records by
themselves. SDU is project evidence, not completion; FLL flood/replacement and
BOS Runway 27 remain project/history leads until original records are captured.

## 6. International evidence

Gadelius is the best preserved international source family: ZRH 2016, RUN
2017, DZA 2018, SCN 2019, HND September 2019, NHT October 2019 and CGH August
2022 appear in the script’s retained source summary. They can become
airport-level historical/physical-system-candidate assertions only with a
deterministic locator/raw-fragment hash; none has supported runway/end data.
Santos Dumont is planned, not installed. Wellington/Queenstown official-source
records are promising but require preserved exact source text; no new claim is
created from notes alone.

## 7. Proposed backfill and dry-run result

No Slice 3A backfill was implemented or applied. Although several scripts
contain transcribed claims, a generic parser would convert curated
interpretation into purported upstream records. The safe next implementation is
an explicit, source-family-specific manifest containing only verbatim retained
source fragments/locators (starting CGF and PRWeb/Gadelius), with hashes and
review state. It must be additive, SourceAssertion-only, dry-run first and
incapable of changing any other table.

Consequently there is no real-database apply command, no backup request, and
no new dry-run count in this slice. The existing Slice 2 count remains 100;
all are aggregate/project assertions.

## 8. Priority examples

- **JFK:** aggregate FAA assertion plus historical fact-sheet composite count;
  unresolved pending original table and end-level records.
- **BOS:** aggregate FAA assertion, historical count, and project grants;
  Runway 27 remains project evidence.
- **CGF:** two recoverable discrete end claims, 06 and 24; no Runway FK should
  be inferred.
- **MDW:** 22L is a recoverable direct candidate; FAA greenEMAS and fact-sheet
  EMASMAX remain distinct aggregate/historical claims.
- **ORD:** product coexistence is unresolved; no link by product/year.
- **LGA/FLL:** count/history only; do not manufacture four installations.

## 9. Database safety and recommended next step

No real development database write occurred. No Installation, Signal, Incident,
Airport, Runway, Source, or SourceAssertion row changed. No UI/export change
occurred.

Recommended next step: create a reviewable, source-specific recovery manifest
from preserved verbatim source fragments (not legacy Installation rows), first
for CGF and the Gadelius/PRWeb entries, then dry-run it. Only after its exact
candidate count and raw locators are reviewable should a database apply be
requested.
