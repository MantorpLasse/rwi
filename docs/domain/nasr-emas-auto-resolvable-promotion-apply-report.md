# NASR EMAS AUTO_RESOLVABLE Promotion — Real Apply Report

Documents the **executed, real** apply of the governed 97-row NASR
current-EMAS-presence promotion batch against `data/runway_safe.db`,
following the completed design/classification/dry-run/safety-review
lineage below. This is the terminal report for that workstream — it does
not rewrite or supersede any prior report.

## 1. Approved semantic contract (unchanged, inherited)

Per `docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md`:
`PhysicalInstallationIdentity.runway_end_id`/`SourceAssertion.runway_end`
mean the canonical **physical** RunwayEnd location, exactly as an
authoritative source (NASR `RWY_END_ID`) reports it — never the
reciprocal/protected-direction end. No schema change; no semantics were
modified by this apply.

## 2. Incident/safety-review lineage (brief)

1. `docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md`
   — design + nationwide read-only classification (97 AUTO_RESOLVABLE / 9
   ALREADY_LINKED / 9 REVIEW_REQUIRED / 0 / 0 / 0).
2. `docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md` — writer
   implementation, dry-run, and a real incident (an accidental write to
   the real DB during disposable-copy rehearsal, caused by a session-
   wiring bug) that was caught immediately, fully reverted from a
   verified backup, and root-caused.
3. A subsequent independent final safety review fixed a related backup-
   ordering gap, removed a dead import that was a residual confusion
   risk, added 8 dedicated wrong-database regression tests, and reached
   verdict `NASR_EMAS_PROMOTION_READY_FOR_REAL_APPLY`.
4. **This report** documents the actual authorized real apply that
   followed.

## 3. Exact real DB target

```
C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

Used, without exception, by the classifier, planner, backup, write
session, and post-write verification — all via one engine/session built
directly from this resolved path inside `main()`; no process-global
`SessionLocal` was used anywhere in this apply.

## 4. Pre-apply SHA-256/size/mtime

| | |
|---|---|
| Size | 667648 bytes |
| mtime | 1787002678.1205828 |
| SHA-256 | `731e51dcbd0d2337d5e31dfc5b6e631d363598a32e9b9c379f84434bf2f7ce3c` |

## 5. Classifier snapshot (fresh, immediately pre-apply)

| | |
|---|---|
| Total | 115 |
| AUTO_RESOLVABLE | 97 |
| ALREADY_LINKED | 9 |
| REVIEW_REQUIRED | 9 |
| AMBIGUOUS | 0 |
| CONFLICT | 0 |
| INSUFFICIENT_EVIDENCE | 0 |

REVIEW_REQUIRED assertion ids confirmed exactly: BOS 161,162; ORH
164,165; BGM 181,182; LEX 153,154; ELM 183 — all `runway_end IS NULL`
both before and after this apply.

Protected state confirmed pre-apply: `Runway`=180, `RunwayEnd`=360,
`PhysicalInstallationIdentity`=6, `InstallationAssertionLink`=8, all 6
MDW/CGF identities present with unchanged `runway_end`/`runway_end_id`
values, U.S. planner 76 `ALREADY_COMPLETE`/0/0/0.

## 6. Fingerprint

```
05d76227c3fe863c30aa8adbcbaeb8a92590e5f8f687ca4a103b3b59f7d38d42
```

Matched exactly, confirmed immediately pre-apply.

## 7. Backup path/hash/verification

```
data/backups/runway_safe-pre-nasr-emas-runway-end-promotion-20260817-220552.db
```

Size 667648 bytes, SHA-256 `731e51dcbd0d2337d5e31dfc5b6e631d363598a32e9b9c379f84434bf2f7ce3c`
— byte-identical to the pre-apply real DB. Independently opened and
verified: `PRAGMA integrity_check` → `ok`; all 97 writable assertions and
all 9 REVIEW_REQUIRED assertions confirmed `runway_end IS NULL`;
`Runway`=180, `RunwayEnd`=360, `PhysicalInstallationIdentity`=6,
`InstallationAssertionLink`=8.

## 8. Exact apply command

```
.venv\Scripts\python.exe -m scripts.promote_nasr_emas_runway_end_assertions --apply --allow-database-write --expected-fingerprint 05d76227c3fe863c30aa8adbcbaeb8a92590e5f8f687ca4a103b3b59f7d38d42
```

Executed exactly once. Exit code 0.

## 9. Rows written

**97.** Matches the approved plan exactly; no other write was attempted.

## 10. Backup-vs-live differences

Full 14-table comparison between the pre-apply backup (§7) and the
post-apply live database: **exactly 97 rows differ, all in
`source_assertions`, and the only field that changed on any of them is
`runway_end`.** `SourceAssertion` has no `updated_at`/`onupdate` column
(confirmed in the model), so there is no incidental timestamp change
either — the write is exactly as narrow as designed. Every one of the 97
written values was individually cross-checked against the pre-apply
plan's own proposed physical designation: zero mismatches. Every one of
the 97 `raw_runway_end_value` fields confirmed unchanged.

## 11. Protected-state verification

`Runway`=180, `RunwayEnd`=360, `PhysicalInstallationIdentity`=6,
`InstallationAssertionLink`=8 — all unchanged (proven both by direct
count and by the zero-row-difference result in §10 across all other
tables). All 6 MDW/CGF `PhysicalInstallationIdentity` rows confirmed
byte-identical (`runway_end`/`runway_end_id` values unchanged). No new
`Airport`, `Runway`, `RunwayEnd`, `PhysicalInstallationIdentity`,
`InstallationAssertionLink`, `Installation`, `Signal`, or `Source` row
was created (proven by the same full-table diff: their row counts and
content are byte-identical to the backup).

## 12. REVIEW_REQUIRED verification

All 9 REVIEW_REQUIRED assertions (BOS 161/162, ORH 164/165, BGM 181/182,
LEX 153/154, ELM 183) confirmed `runway_end IS NULL` immediately
post-apply — untouched.

## 13. FK/integrity result

`PRAGMA foreign_key_check` → `[]`. `PRAGMA integrity_check` → `ok`.

## 14. Post-apply idempotency result

A fresh dry-run immediately after the apply reports `writable_count: 0`,
`already_promoted_count: 97` — the writer-level `ALREADY_PROMOTED` state
(a refinement layered on top of the classifier's own unchanged
`AUTO_RESOLVABLE` class, which does not itself consider current
`runway_end` state by design) now correctly covers all 97 rows.
`dry_run()`'s own internal assertion (`session.new/dirty/deleted == 0`)
passed. No classifier or writer semantics were modified to achieve this
— it is the designed, tested behavior.

## 15. Static-export validation

Regenerated locally (`scripts/export_static_site`) from the now-updated
real database, for validation only — not deployed. Result: **56 airport
pages gain a populated "EMAS idag" section**; **0 duplicate same-end
pills** anywhere; no rendering errors; canonical runway inventory
("Banor") remains present and unaffected (spot-checked on BOS: all 6
runways still listed). MDW and CGF continue publishing exclusively via
the separate, human-reviewed `reviewed_identities` pathway, untouched by
this batch. BOS and ORH confirmed to show **empty** current-EMAS state
(`nasr_presence: []`, `reviewed_identities: []`) — correctly deferred,
not accidentally populated.

## 16. 56-airport product impact

| Code | Name | Published physical runway-end(s) |
|---|---|---|
| JWN | John Tune | 20 |
| ABE | Lehigh Valley | 13, 31 |
| ACV | Arcata-Eureka | 32 |
| ADQ | Kodiak | 1, 8 |
| ADS | Addison | 16 |
| AUG | Augusta State | 17, 35 |
| AVP | Wilkes-Barre / Scranton International | 22, 4 |
| BCT | Boca Raton | 23, 5 |
| BDR | Sikorsky | 6 |
| BKL | Burke Lakefront | 24R |
| BUR | Bob Hope | 8 |
| CDV | Merle K (Mudhole) Smith | 27 |
| CLE | Cleveland-Hopkins International | 10, 28 |
| CLT | Charlotte Douglas International | 36R |
| CRW | Charleston Yeager | 23 |
| DCA | Reagan National | 15, 33, 4 |
| EWN | New Bern | 22 |
| EWR | Newark Liberty International | 11, 29 |
| EYW | Key West International | 27, 9 |
| FLL | Fort Lauderdale / Hollywood International | 10L, 10R, 28L, 28R |
| FRG | Republic | 14, 32 |
| GMU | Greenville Downtown | 1 |
| GON | Groton-New London | 23, 5 |
| HXD | Hilton Head | 21, 3 |
| HYA | Cape Cod Gateway Airport | 24 |
| ILG | New Castle County | 19 |
| INT | Smith Reynolds | 15 |
| JFK | John F. Kennedy International Airport | 22L, 4R |
| LFT | Lafayette | 11, 22L, 29, 4R |
| LGA | LaGuardia | 13, 22, 31, 4 |
| LIT | Bill and Hillary Clinton National | 22R |
| LRD | Laredo International | 18R |
| MEM | Memphis | 18R |
| MHT | Manchester-Boston Regional Airport | 6 |
| MKC | Charles B. Wheeler Downtown Airport | 1, 19 |
| MRY | Monterey | 10R, 28L |
| MSP | Minneapolis St. Paul International | 12R |
| OAK | Oakland International | 28L |
| OME | Nome | 28 |
| ORD | Chicago O'Hare International | 22L, 4R |
| PBI | President Donald J. Trump International | 14 |
| PDK | DeKalb/Peachtree | 21L |
| PVD | Rhode Island T.F. Green International | 16, 23, 34 |
| PWK | Chicago Executive | 16, 34 |
| RDG | Reading Regional | 13 |
| ROA | Roanoke/Blacksburg Regional | 34 |
| ROC | Frederick Douglas / Greater Rochester International | 28 |
| SAN | San Diego International | 27 |
| SBP | San Luis Obispo County | 11, 29 |
| SFO | San Francisco International Airport | 19L, 19R, 1L, 1R |
| STP | St. Paul Downtown Airport | 14, 32 |
| SUA | Martin CountyWitham Field | 12, 30 |
| TEB | Teterboro Airport | 19, 24, 6 |
| TEX | Telluride Regional | 27, 9 |
| TTN | Trenton-Mercer | 16, 24, 34, 6 |
| VNC | Venice | 13 |

**Total: 56.**

**5 REVIEW_REQUIRED airports (not interpreted, not touched, reported for
visibility only)**:

| Code | Name |
|---|---|
| BOS | Boston Logan International Airport |
| ORH | Worcester Regional |
| BGM | Greater Binghamton Airport |
| LEX | Blue Grass |
| ELM | Elmira-Corning |

## 17. Presentation follow-up

Every published value is the literal, sourced, physical NASR designation
— the same convention already live for MDW/CGF prior to this batch.
Verdict unchanged from the dry-run report: **`TECHNICALLY_CORRECT_BUT_NEEDS_PRESENTATION_FOLLOWUP`**.
No UI change was made in this task, per instruction; a future slice
should implement the reciprocal-derived public-label design already
proposed in the semantics analysis.

## 18. Tests

Focused: classifier (13) + writer (26) + static-export (18) +
`test_source_assertions`/`test_physical_installation_reconciliation` —
**68 passed**. Full suite: **593 passed** (unchanged from the pre-task
baseline — this task added no new tests, only executed the already-
reviewed writer). `py_compile` clean. `git diff --check` exit 0.

## 19. Final governed state

| | Value |
|---|---|
| `Runway` | 180 |
| `RunwayEnd` | 360 |
| U.S. planner | 76 `ALREADY_COMPLETE`, 0 unresolved/ambiguous/conflict |
| `PhysicalInstallationIdentity` | 6 (unchanged) |
| `InstallationAssertionLink` | 8 (unchanged) |
| `SourceAssertion` (`assertion_type='runway_end'`) with `runway_end` populated | 97 |
| `SourceAssertion` (`assertion_type='runway_end'`) still `NULL` (the 9 REVIEW_REQUIRED) | 9 |
| Airport pages publishing current EMAS (`nasr_presence` or `reviewed_identities`) | 58 (56 new via this batch + MDW + CGF, pre-existing) |
