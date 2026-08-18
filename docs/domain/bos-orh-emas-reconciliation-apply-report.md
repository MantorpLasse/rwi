# BOS / ORH EMAS Reconciliation — Real Apply Report

Documents the **executed, real** apply of the governed 4-row BOS/ORH
EMAS reconciliation batch against `data/runway_safe.db`, following the
completed investigation/dry-run lineage below. This is the terminal
report for that workstream — it does not rewrite or supersede any prior
report.

## 1. Evidence basis

Per `docs/domain/bos-orh-emas-reconciliation-investigation.md`: NASR
reports current physical EMAS presence at BOS `04L`/`15R` and ORH
`11`/`29`. Massport's own newsroom (BOS) and three official MPA
procurement-contract notices (ORH) — all Tier 1 — confirm the reciprocal
protected-operational-direction naming for each bed, matching RWI's own
canonical `Runway`↔`RunwayEnd` topology exactly. Verdicts:
`BOS_RECONCILABLE`, `ORH_RECONCILABLE`, `NO_SCHEMA_CHANGE_NEEDED`.

## 2. Approved write set

| Assertion | Airport | Physical | Protected | Canonical `RunwayEnd` id |
|---|---|---|---|---|
| 161 | BOS | `04L` | `22R` | 15 |
| 162 | BOS | `15R` | `33L` | 25 |
| 164 | ORH | `11` | `29` | 179 |
| 165 | ORH | `29` | `11` | 180 |

Exactly 4 `PhysicalInstallationIdentity` + 4
`InstallationAssertionLink(outcome="SAME_PHYSICAL_INSTALLATION")` rows.
`SourceAssertion.runway_end` never written (matches the unchanged MDW/CGF
precedent). BGM/LEX/ELM never touched.

## 3. Real DB target

```
C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

Used, without exception, by the fresh pre-apply dry-run, the backup, the
apply itself, and all post-write verification — one engine/session built
directly from this resolved path inside `main()`; `app.database.SessionLocal`
was never referenced anywhere in this apply.

## 4. Pre-apply SHA-256/size/mtime

| | |
|---|---|
| Size | 667648 bytes |
| mtime | 1787004353.2183805 |
| SHA-256 | `23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559` |

Governed state confirmed pre-apply: `Runway`=180, `RunwayEnd`=360,
`PhysicalInstallationIdentity`=6, `InstallationAssertionLink`=8. All 9
`REVIEW_REQUIRED` assertions (BOS 161/162, ORH 164/165, BGM 181/182, LEX
153/154, ELM 183) confirmed `runway_end IS NULL`.

## 5. Fresh dry-run result

Re-run immediately before the apply, against the real database:
`writable_count=4`, `already_reconciled_count=0`, plan identical to the
approved design (same 4 assertions, same canonical `RunwayEnd` ids, same
physical/protected mappings). `session.new/dirty/deleted == 0`. Real DB
SHA/size/mtime confirmed unchanged immediately after.

## 6. Fingerprint

```
9c599fc89958292de694a60bb1d492013c639ce31ba49cf0f3b6f2968e5bc923
```

Matched exactly, stable across the fresh dry-run and the apply's own
internal re-plan.

## 7. Backup path/hash/verification

```
data/backups/runway_safe-pre-bos-orh-emas-reconciliation-20260818-110953.db
```

Size 667648 bytes, SHA-256
`23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559` —
byte-identical to the pre-apply real DB. Independently opened and
verified: `PRAGMA integrity_check` → `ok`; `PhysicalInstallationIdentity`=6,
`InstallationAssertionLink`=8; all 4 target assertions confirmed
`runway_end IS NULL`.

(A second, manually-created verification backup,
`runway_safe-pre-bos-orh-emas-reconciliation-20260818-110928.db`, was
also taken and independently verified immediately before the apply
command was run, per this task's own §3 step — both backups are
byte-identical to each other and to the pre-apply real DB.)

## 8. Exact apply command

```
.venv\Scripts\python.exe -m scripts.reconcile_bos_orh_emas_identities --apply --allow-database-write --expected-fingerprint 9c599fc89958292de694a60bb1d492013c639ce31ba49cf0f3b6f2968e5bc923
```

Executed exactly once. Exit code 0.

## 9. Identities created

**4.** Independently re-queried after the apply:

| Assertion | Airport | `runway_end` | `runway_end_id` |
|---|---|---|---|
| 161 | BOS | `04L` | 15 |
| 162 | BOS | `15R` | 25 |
| 164 | ORH | `11` | 179 |
| 165 | ORH | `29` | 180 |

## 10. Links created

**4**, all `outcome="SAME_PHYSICAL_INSTALLATION"`, `actor="human:rwi-owner"`,
one per target assertion (161, 162, 164, 165) — confirmed by direct
query.

## 11. `SourceAssertion.runway_end` unchanged proof

Confirmed `NULL` for all four target assertions (161, 162, 164, 165)
immediately after the apply, and confirmed unchanged as **whole rows**
(not just that one column) in the backup-vs-live table diff (§12).

## 12. Backup-vs-live diff

Full 14-table comparison between the immediate pre-apply backup (§7) and
the post-apply live database:

| Table | Change |
|---|---|
| `physical_installation_identities` | +4, -0 |
| `installation_assertion_links` | +4, -0 |
| every other table (`airports`, `runways`, `runway_ends`, `source_assertions`, `sources`, `installations`, `signals`, `incidents`, `acquisition_runs`, `acquisition_sources`, `publishing_sources`, `snapshots`) | unchanged |

Exactly the allowed diff — nothing else changed anywhere.

## 13. MDW/CGF unchanged

All 6 pre-existing `PhysicalInstallationIdentity` rows (ids 1–6) and all
8 pre-existing `InstallationAssertionLink` rows (ids 1–8) confirmed
byte-identical to their pre-apply values (same `runway_end`/`runway_end_id`,
same `outcome`) by direct post-apply query.

## 14. BGM/LEX/ELM unchanged

All 5 assertions (181, 182, 153, 154, 183) confirmed `runway_end IS NULL`
post-apply, with no `InstallationAssertionLink` referencing any of them —
completely untouched.

## 15. FK/integrity result

`PRAGMA foreign_key_check` → `[]`. `PRAGMA integrity_check` → `ok`.

## 16. Idempotency

A fresh dry-run immediately after the apply reports `writable_count: 0`,
`already_reconciled_count: 4` — all four rows correctly recognized as
`ALREADY_RECONCILED` (existing identity ids 7, 8, 9, 10). No second real
write was performed or needed.

## 17. BOS public result

Static site regenerated locally from the now-updated real database (not
deployed). BOS's "EMAS idag" shows exactly 2 items: `Bana 22R` (physical
`04L`, "Granskad identitet") and `Bana 33L` (physical `15R`, "Granskad
identitet"). "Runway 9/27 RSA and EMAS phase 2" remains solely under
"Projekt och bevakning" — confirmed absent from `current_emas`. Canonical
"Banor" unaffected (all 6 BOS runways still listed).

## 18. ORH public result

ORH's "EMAS idag" shows exactly 2 items: `Bana 11` (physical `29`,
"Granskad identitet") and `Bana 29` (physical `11`, "Granskad identitet").
The 5 replacement-lifecycle USAspending signals remain solely under
"Finansiering och bidrag". Canonical "Banor" unaffected (both ORH runways
still listed).

## 19. Project/history separation

Confirmed for both airports: no current/project conflation (Runway 27
Phase 2 stays out of `current_emas`), no current/history conflation
(ORH's 2024/2025 replacement story stays out of `current_emas`), 0
duplicate items, 0 raw internal ids visible in `data.json` or the
rendered HTML. Spot-checked MDW, CGF, and ADS (NASR-only pathway) for
regression: all three render exactly as before this apply — MDW 4 items,
CGF 2 items, ADS 1 item, all correctly labeled.

## 20. Tests

Focused: `tests/test_reconcile_bos_orh_emas_identities.py` — 31 passed
(unchanged from the dry-run report; this task executed the
already-reviewed writer, adding no new tests). Full suite: **642 passed**
(611 baseline + 31 from the prior dry-run task) — unchanged from the
pre-apply baseline. `py_compile`: clean. `git diff --check`: exit 0.

## 21. Final state

| | Value |
|---|---|
| `Runway` | 180 (unchanged) |
| `RunwayEnd` | 360 (unchanged) |
| `PhysicalInstallationIdentity` | 10 (6 pre-existing + 4 new) |
| `InstallationAssertionLink` | 12 (8 pre-existing + 4 new) |
| `SourceAssertion` (161/162/164/165) `runway_end` | still `NULL` (by design) |
| Remaining `REVIEW_REQUIRED` assertions (BGM 181/182, LEX 153/154, ELM 183) | untouched, 5 assertions across 3 airports |
| Airports publishing current EMAS via `current_emas` | 60 (58 pre-existing + BOS + ORH, both newly reviewed) |

## 22. Deployment status

**NOT DEPLOYED.** The static site was regenerated only into the local,
gitignored `site/` directory for validation — no publish/deploy step was
run in this task.
