# Canonical Runway Inventory — MDW + CGF Pilot Apply (dry run only)

**No write has been made to the real development database.** Everything
below comes from a fresh read-only dry run against the real database plus
isolated in-memory tests. The real database's last-write timestamp
(`2026-08-16 17:11:20`, from the prior Slice 1 schema migration) is
unchanged throughout this task.

## 1. Source artifact

```
data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip
```

Inputs used: `APT_RWY.csv` (runway pairs) and `APT_RWY_END.csv` (runway
ends) — the canonical runway **inventory** files. `APT_ARS.csv` (EMAS
arresting-system presence) was **not** used as an inventory source and was
not read by the apply script at all — it stays conceptually and technically
separate, per `docs/domain/canonical-runway-runway-end-design.md` §4. No
network access occurred; the archive was read from local disk only, with
its existing SHA-256 verification (`app/evidence/nasr_apt_rwy.py`).

New script (dry-run-first, mirrors `scripts/apply_mdw_current_presence_pilot.py`'s
discipline): `scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py`,
scoped to exactly `TARGET_CODES = ("MDW", "CGF")`.

## 2. MDW dry-run plan

Resolved: Airport id **12**, "Chicago Midway International Airport".
Source rows: 4 runway rows, 8 runway-end rows.

| | Count |
|---|---|
| Runways would create | **3** |
| Runways would enrich | **0** |
| Runways unchanged | **1** |
| RunwayEnds would create | **8** |
| RunwayEnds already existing | 0 |
| Unresolved identities | **0** |
| Ambiguous identities | **0** |
| Duplicate identity targets | **0** |

## 3. CGF dry-run plan

Resolved: Airport id **57**, "Cuyahoga". Source rows: 1 runway row, 2
runway-end rows.

| | Count |
|---|---|
| Runways would create | **0** |
| Runways would enrich | **1** |
| Runways unchanged | 0 |
| RunwayEnds would create | **2** |
| RunwayEnds already existing | 0 |
| Unresolved identities | **0** |
| Ambiguous identities | **0** |
| Duplicate identity targets | **0** |

No special-casing exists anywhere in the planner for CGF's simpler,
single-runway shape (same code path as MDW).

## 4. Exact Runway rows to create

**MDW (3):**

| Designation | Raw (NASR) | Length | Width | Surface |
|---|---|---|---|---|
| `4L/22R` | `04L/22R` | 1679 m | 46 m | ASPH |
| `4R/22L` | `04R/22L` | 1964 m | 46 m | ASPH-CONC |
| `13R/31L` | `13R/31L` | 1176 m | 18 m | ASPH |

**CGF: none.**

## 5. Exact Runway rows to enrich

**MDW: none** — the existing `13L/31R` row (id 12) already has
length_m/width_m/surface populated; reported as **unchanged**, not enriched.

**CGF (1):** existing row id **58**, designation `6/24` → would receive
`length_m=1677, width_m=30, surface="ASPH"` (all three fields are currently
`NULL` on this row — enrichment fills them, nothing is overwritten).

## 6. Exact RunwayEnd rows to create

**MDW (8):** under `4L/22R` → `4L`, `22R`; under `4R/22L` → `4R`, `22L`;
under `13L/31R` (existing runway id 12) → `13L`, `31R`; under `13R/31L` →
`13R`, `31L`.

**CGF (2):** under `6/24` (existing runway id 58) → `6`, `24`.

## 7. Identity mapping proposals — report only, NOT applied

| ID | Airport | Reviewed end | → Canonical runway | → Canonical runway end |
|---|---|---|---|---|
| 3 | MDW | `04R` | `4R/22L` | `4R` |
| 4 | MDW | `22L` | `4R/22L` | `22L` |
| 5 | MDW | `13L` | `13L/31R` | `13L` |
| 6 | MDW | `31R` | `13L/31R` | `31R` |
| 1 | CGF | `06` | `6/24` | `6` |
| 2 | CGF | `24` | `6/24` | `24` |

`runway_end_id` was **not** set on any of these — the script's proposal
output carries an explicit `"REPORT ONLY - runway_end_id is not set by this
script"` note on every entry.

## 8. Protected-table guarantees

Confirmed by direct comparison in every dry run and isolated test: this
apply plan writes only to `runways` and `runway_ends`, scoped only to MDW
(id 12) and CGF (id 57). No code path in
`scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py` touches
`airports`, `installations`, `signals`, `incidents`, `sources`,
`source_assertions`, `physical_installation_identities`, or
`installation_assertion_links`.

## 9. Idempotency test (isolated, never the real database)

`tests/test_apply_canonical_runway_inventory_mdw_cgf_pilot.py`, 5 tests,
all passing, against an in-memory database seeded to match the real
database's actual pre-pilot MDW/CGF shape exactly:

- First `run(apply=True)`: creates 4 MDW runways/8 ends total and enriches
  CGF's runway to `length_m=1677, width_m=30, surface="ASPH"`/2 ends —
  matching §2–§6 exactly. No `PhysicalInstallationIdentity` row is touched
  (`runway_end_id` and `runway_id` stay `NULL` on all of them, checked
  directly). `PRAGMA foreign_key_check` returns `[]`.
- Second `run(apply=True)` (fresh session, simulating a separate process):
  **zero** would-create, would-enrich, or would-create-ends on both
  airports — everything recognized as already existing. Final row counts
  (4 MDW runways, 8 MDW ends, 1 CGF runway, 2 CGF ends) are identical
  before and after the second run.
- `run(apply=False)` never writes anything, on any airport.

## 10. Test results

- Focused canonical-runway tests (7 files: normalization, NASR RWY/RWY_END
  parser incl. real-artifact checks, inventory planning, migration,
  this pilot's apply/idempotency, model contract, static export safety):
  **61 passed**.
- Full suite: **406 passed** (400 before this task's 5 new tests + 1 test
  file's worth from Slice 1 already counted — net +5 vs. the last known-good
  401).
- Python compilation: `scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py`
  compiles cleanly.
- `git diff --check`: passed (exit 0).

## 11. Public-export safety

Fresh `build_site()` run against the **real, current** database (schema
migrated, `runway_ends` still empty — pilot not yet applied):

- `grep -c "Banor"` on both generated MDW and CGF airport pages: **0** —
  still suppressed.
- `grep -rl "runway_end_id\|RunwayEnd"` across the entire generated site:
  **no matches**.
- (Also re-confirmed generically by the Slice 1 regression test, which
  seeds a *linked* `RunwayEnd` and proves it still can't leak into any
  page or `data.json`.)
- Real database last-write timestamp unchanged (`2026-08-16 17:11:20`)
  after this build.

## 12. Exact real DB write plan (not yet executed)

See the STOP section below.

---

## STOP — before real apply

**1. Resolved DB path**

```
C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
```

**2. Fresh backup — already created ahead of approval** (a backup is a
read of the target DB + a write to a new file elsewhere; it does not
modify `runway_safe.db` itself, so creating it now is safe):

```
data\backups\runway_safe-pre-canonical-runway-runway-end-pilot-apply-20260816-152006.db
```
643,072 bytes — verified byte-identical to the current `runway_safe.db`.

**3. Exact apply command**

```
.venv\Scripts\python.exe -m scripts.apply_canonical_runway_inventory_mdw_cgf_pilot --apply
```

**4. MDW**
- Runways create: **3**
- Runways enrich: **0**
- RunwayEnds create: **8**
- Unresolved/ambiguous: **0 / 0**

**5. CGF**
- Runways create: **0**
- Runways enrich: **1**
- RunwayEnds create: **2**
- Unresolved/ambiguous: **0 / 0**

**6. Exact protected tables** (writes touch only `runways` and
`runway_ends`, only rows belonging to airport id 12 (MDW) and id 57 (CGF)):

```
airports, installations, signals, incidents, sources, source_assertions,
physical_installation_identities, installation_assertion_links
```

**7. Identity links remain untouched** — the apply command above does not
set `runway_end_id` on any `PhysicalInstallationIdentity` row anywhere; the
mapping in §7 is a proposal only, and applying it is a separate,
not-yet-built, not-yet-approved step.

**Waiting for explicit approval before running the command in item 3.**

No commit. No push. No deployment.
