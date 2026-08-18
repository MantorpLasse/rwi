# BOS / ORH EMAS Reconciliation — Guarded Writer Dry-Run Report

Implements, tests, and rehearses (but does **not** execute against the
real database) a narrowly-scoped, guarded reconciliation writer for the
4 BOS/ORH `REVIEW_REQUIRED` NASR current-EMAS-presence `SourceAssertion`
rows proven reconcilable by
`docs/domain/bos-orh-emas-reconciliation-investigation.md`.

**No real database write, no `SourceAssertion.runway_end` write, no
UI/export code change, no schema/model change, no other airport touched,
no commit, no push, no deployment.**

## 1. Evidence basis inherited from the investigation

Per `docs/domain/bos-orh-emas-reconciliation-investigation.md` (read in
full before this task; not rewritten here): NASR reports current physical
EMAS presence at BOS `04L`/`15R` and ORH `11`/`29`. Massport's own
newsroom (BOS) and three official MPA procurement-contract notices (ORH),
both Tier 1, confirm the reciprocal protected-operational-direction naming
for each bed, matching RWI's own canonical `Runway`↔`RunwayEnd` topology
exactly, with zero ambiguity. `NO_SCHEMA_CHANGE_NEEDED` — the existing
MDW/CGF `PhysicalInstallationIdentity` +
`InstallationAssertionLink(outcome="SAME_PHYSICAL_INSTALLATION")`
mechanism applies directly, and a direct query in that report established
that MDW/CGF's own linked `SourceAssertion.runway_end` values are still
`NULL` today — publication has only ever depended on the reviewed
identity, never on that column. This writer therefore never touches
`SourceAssertion.runway_end`.

## 2. Exact 4-row reconciliation set

| Assertion | Airport | Physical (NASR) | Canonical `RunwayEnd` id | Protected direction | Canonical `Runway` id |
|---|---|---|---|---|---|
| 161 | BOS | `04L` | 15 | `22R` | 66 (`4L/22R`) |
| 162 | BOS | `15R` | 25 | `33L` | 70 (`15R/33L`) |
| 164 | ORH | `11` | 179 | `29` | 54 (`11/29`) |
| 165 | ORH | `29` | 180 | `11` | 54 (`11/29`) |

Hardcoded as `TARGET_ROWS` in the writer — the only population this
module ever considers; BGM (181, 182), LEX (153, 154), and ELM (183) are
structurally unreachable, not merely excluded by a runtime check.

## 3. Writer architecture

`scripts/reconcile_bos_orh_emas_identities.py`. Modeled directly on
`scripts/promote_nasr_emas_runway_end_assertions.py`'s already-proven
dry-run/snapshot/fingerprint/backup-ordering pattern, but creates
`PhysicalInstallationIdentity` + `InstallationAssertionLink` rows (via the
unmodified `app/services/physical_installation_reconciliation.py`
functions) instead of writing a `SourceAssertion` column. Key functions:
`_plan_row()` (per-row precondition resolution), `_check_snapshot()`,
`_fingerprint()`, `plan()`/`dry_run()`/`apply()`, `main()`.

## 4. DB-target isolation model

`main()` builds its own `sessionmaker`/`engine` directly from the
resolved `--database` path for every operation, dry-run and apply alike.
`app.database.SessionLocal` is never imported anywhere in this module —
the exact incident class documented in
`docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md` is
structurally impossible here, not merely avoided by convention. Proven by
8 dedicated tests (§17) using two real, on-disk temp databases
(`protected.db`/`target.db`), including one that binds a rogue
`app.database.SessionLocal` to `protected.db` and confirms `main()` still
only ever writes `target.db`.

## 5. Preconditions

Checked per-row in `_plan_row()`, in order, any failure raises
`ReconciliationGuardError` and aborts the entire batch before any write:

1. assertion id ∈ `{161, 162, 164, 165}` (structural — `TARGET_ROWS` is the only iteration source)
2. resolved `Airport.faa_code` matches the expected code, and `assertion.airport_id` equals that airport's id
3. `assertion.assertion_type == "runway_end"`
4. `assertion.evidence_quality == "direct_strong"`
5. `assertion.raw_runway_end_value` equals the expected physical value
6. `assertion.runway_end is None`
7. the canonical `Airport` exists (implicit in #2's lookup)
8. the expected canonical `Runway` exists (implicit in #9's resolution)
9. the expected physical `RunwayEnd` resolves via `normalize_end()` to exactly one candidate at this airport
10. the topology-derived reciprocal (`_protected_direction()`) matches the investigation-approved value
11. no existing `PhysicalInstallationIdentity` at this `(airport_id, runway_end_id)` unless it exactly matches an already-reconciled shape (→ `ALREADY_RECONCILED`, not an error)
12. no existing `InstallationAssertionLink` for this assertion unless it exactly matches that same already-reconciled shape
13. any other existing identity/link shape (partial, mismatched outcome, wrong end) → hard `ReconciliationGuardError`, never silently overwritten
14. MDW/CGF rows are never queried or touched by any code path here — proven by test, not merely absent from `TARGET_ROWS`
15. BGM/LEX/ELM are structurally unreachable (§2)

## 6. Snapshot guard

`EXPECTED_SNAPSHOT` is a frozen tuple derived directly from `TARGET_ROWS`
itself: `((161, "BOS", "04L", "22R"), (162, "BOS", "15R", "33L"), (164,
"ORH", "11", "29"), (165, "ORH", "29", "11"))`. `_check_snapshot()`
compares the freshly-resolved plan's `(assertion_id, airport_code,
physical, protected_direction)` tuples against this exactly — not a bare
count — and aborts on any mismatch before the fingerprint is even
computed.

## 7. Fingerprint algorithm

SHA-256 of the sorted JSON-encoded `(assertion_id, airport_id, physical,
canonical_runway_end_id, protected_direction, "SAME_PHYSICAL_INSTALLATION")`
tuples for every `WRITABLE`-state row. Computed fresh on every `plan()`
call; `apply()` requires an `--expected-fingerprint` argument and aborts
on any mismatch — including a same-count-but-different-content mismatch
(tested explicitly).

## 8. Exact fingerprint

Computed against the real database, twice, in dry-run mode only:

```
9c599fc89958292de694a60bb1d492013c639ce31ba49cf0f3b6f2968e5bc923
```

Identical on both runs — stable.

## 9. Planned `PhysicalInstallationIdentity` fields

For each of the 4 rows: `airport_id` (3 or 44), `runway_id` (the parent
canonical `Runway`), `runway_end` (the raw physical designation, e.g.
`"04L"` — text, matching the existing MDW/CGF convention exactly),
`runway_end_id` (the resolved canonical `RunwayEnd` FK, set directly at
creation — a one-step simplification of the original two-step MDW-era
pilot pattern, since BOS/ORH's topology is already fully resolved).
**Represents PHYSICAL bed location only** — the protected direction is
never stored on the identity, only derived at presentation time via
existing topology (`app/static_export/build.py::_protected_direction()`,
unmodified). No manufacturer, install year, or replacement field is set —
none of those facts are supported by this reconciliation's own evidence.

## 10. Planned `InstallationAssertionLink` fields

One per assertion: `assertion_id`, `physical_installation_id` (the newly
created identity), `outcome="SAME_PHYSICAL_INSTALLATION"`,
`actor="human:rwi-owner"` (matching the existing MDW/CGF convention
exactly), and a concise `reason` string, e.g. for assertion 161: *"FAA
NASR 2026-08-06 explicitly reports EMAS at BOS runway end 04L;
current-presence only, no historical continuity claim. Protected
direction (22R) confirmed by Massport/MPA primary-source evidence - see
docs/domain/bos-orh-emas-reconciliation-investigation.md."* No long
source passages copied — the full evidence lives in the investigation
report, referenced by path.

## 11. Dry-run result (real database)

| | |
|---|---|
| DB path | `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db` |
| Planned reconciliations | 4 |
| Planned identities | 4 |
| Planned links | 4 |
| Real writes | 0 |
| `SourceAssertion.runway_end` (161/162/164/165) | all `NULL`, before and after |
| Fingerprint | `9c599fc89958292de694a60bb1d492013c639ce31ba49cf0f3b6f2968e5bc923` (stable across 2 runs) |
| Session mutation proof | `len(session.new) == len(session.dirty) == len(session.deleted) == 0`, asserted directly and re-verified independently in this task |
| DB size before/after | 667648 / 667648 |
| DB mtime before/after | 1787004353.2183805 / 1787004353.2183805 |
| DB SHA-256 before/after | `23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559` / (identical) |

## 12. Disposable-copy apply result

Full apply executed only against a disposable file-copy in the session
scratch directory (never the real database). Pre-copy SHA-256 matched the
real database's exactly
(`23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559`).
Result: `rows_written: 4`, `identities_created: 4`, `links_created: 4`,
matching the approved fingerprint exactly.

## 13. Exact table/row changes

Full 14-table comparison between the disposable copy's own pre-apply
backup and its post-apply state:

| Table | Change |
|---|---|
| `physical_installation_identities` | +4, -0 |
| `installation_assertion_links` | +4, -0 |
| every other table (`airports`, `runways`, `runway_ends`, `source_assertions`, `sources`, `installations`, `signals`, `incidents`, `acquisition_runs`, `acquisition_sources`, `publishing_sources`, `snapshots`) | unchanged |

`source_assertions` unchanged as a **whole-row** comparison — not just
the `runway_end` column — confirming no field on any of the 9
`REVIEW_REQUIRED` assertions (or any other assertion) changed at all.

Post-apply disposable-copy state: `PhysicalInstallationIdentity` = 10 (6
pre-existing + 4 new), `InstallationAssertionLink` = 12 (8 + 4), `Runway`
= 180 (unchanged), `RunwayEnd` = 360 (unchanged), all 9
`REVIEW_REQUIRED` assertions (BOS 161/162, ORH 164/165, BGM 181/182, LEX
153/154, ELM 183) still `runway_end IS NULL`.

## 14. `SourceAssertion.runway_end` unchanged proof

Confirmed three independent ways: (1) the writer's own per-row
precondition #6 requires it `NULL` before writing and never sets it —
structurally, no code path in this module assigns to that column at all;
(2) direct query of all 9 `REVIEW_REQUIRED` assertions on the disposable
copy post-apply, all `NULL`; (3) the full table diff (§13) shows
`source_assertions` completely unchanged as whole rows, not merely that
one column.

## 15. FK/integrity checks

Disposable copy, post-apply: `PRAGMA foreign_key_check` → `[]`. `PRAGMA
integrity_check` → `ok`.

## 16. Static-export simulation

Built from the reconciled disposable copy only (`app.static_export.build.build_site`,
unmodified — no production export code touched in this task).

**BOS**: exactly 2 `current_emas` items — `{primary_label: "Bana 22R",
physical_runway_end: "04L", ...}` and `{primary_label: "Bana 33L",
physical_runway_end: "15R", ...}`. "Runway 9/27 RSA and EMAS phase 2"
appears only in the signals list (`Projekt och bevakning`), never in
`current_emas` — correctly separated. `runways` unaffected: all 6 BOS
pills present.

**ORH**: exactly 2 `current_emas` items — `{primary_label: "Bana 11",
physical_runway_end: "29", ...}` and `{primary_label: "Bana 29",
physical_runway_end: "11", ...}`. The 5 replacement-lifecycle USAspending
signals appear only in `funding_signals`, never in `current_emas`.
`runways` unaffected: both ORH pills present.

Checked and confirmed: 0 duplicate items at either airport; 0 raw
internal ids (`PhysicalInstallationIdentity.id`,
`InstallationAssertionLink.id`, assertion id) anywhere in the output; 0
current/project conflation (Runway 27 stays out of `current_emas`); 0
current/history conflation (ORH's replacement story stays out of
`current_emas`).

## 17. Focused test result

`tests/test_reconcile_bos_orh_emas_identities.py` — **31 passed**,
covering: dry-run zero-mutation, exact 4-row plan, exact 4
identity/link creates, `SourceAssertion.runway_end` untouched, all 4
individual physical→protected mappings, topology-not-arithmetic
derivation (2 tests, including an asymmetric-suffix pair), MDW/CGF-shaped
rows untouched, BGM/LEX/ELM-shaped assertions untouched, duplicate-
identity fail-closed, already-reconciled no-op, mismatched-link
fail-closed, evidence-quality drift, topology drift, snapshot drift,
fingerprint drift, CLI flag gating (all 3 combinations), failed
post-write verification never commits, idempotent rerun, no unrelated
table changes, and the 8-test wrong-database isolation suite (A–H,
including the `SessionLocal`-override test).

## 18. Full pytest result

**642 passed** (611 pre-existing baseline + 31 new). `py_compile`: clean
on both the writer and its test file. `git diff --check`: exit 0.

## 19. BOS result

`BOS_RECONCILABLE` (inherited verdict, unchanged). Both assertions
(161→`04L`/`22R`, 162→`15R`/`33L`) plan cleanly as `WRITABLE`, rehearsed
successfully end-to-end on a disposable copy, simulated public output
correct, Runway 27 Phase 2 correctly excluded throughout.

## 20. ORH result

`ORH_RECONCILABLE` (inherited verdict, unchanged). Both assertions
(164→`11`/`29`, 165→`29`/`11`) plan cleanly as `WRITABLE`, rehearsed
successfully end-to-end, simulated public output correct, 2024/2025
replacement-lifecycle evidence correctly excluded throughout.

## Exact future real-apply command

```
.venv\Scripts\python.exe -m scripts.reconcile_bos_orh_emas_identities ^
  --apply --allow-database-write ^
  --expected-fingerprint 9c599fc89958292de694a60bb1d492013c639ce31ba49cf0f3b6f2968e5bc923
```

(`--database` omitted → defaults to `data/runway_safe.db`; the fingerprint
must be re-confirmed unchanged by a fresh dry-run immediately before any
real apply is authorized, since this document itself will age.)

## Recommendation: is real apply safe?

**Yes — this batch is ready for real apply, pending explicit human
authorization** (not granted in this task, which was dry-run/rehearsal
only per instruction). Basis: 4 writes, 2 rows each, exhaustively tested
(31 dedicated tests including full wrong-database isolation coverage),
rehearsed successfully end-to-end on a disposable copy with a full
14-table diff showing exactly the intended +4/+4 change and nothing else,
zero effect on any protected table or airport, `SourceAssertion.runway_end`
proven untouched by construction, and a simulated public-site result that
exactly matches the investigation's approved design with no duplication,
leakage, or conflation. No incident occurred during this task's own
development (unlike the NASR promotion writer's history) — the
architecture was built directly on that writer's already-battle-tested,
already-fixed pattern (own session/engine bound to `--database`,
validate-before-backup ordering, `no_autoflush` write loop) from the
start.

BOS_ORH_EMAS_RECONCILIATION_DRY_RUN_COMPLETE
