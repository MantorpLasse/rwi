# Canonical Runway Foundation — Merge-Readiness Review

**Read-only architecture review.** No code, documentation, or database was
modified in this task other than creating this single report. All
verification below is either a direct git/diff inspection, a read-only
query against the real database, or an isolated/temporary-database test —
the real database was never written to.

## Scope

- Reviewed range: `394fa01..071ab2d` (5 commits, `main` → feature branch tip)
- 51 files changed, 10,515 insertions, 15 deletions

## 1. Architectural inventory

| Group | Main files | Responsibility | Kind |
|---|---|---|---|
| **A. Canonical Runway/RunwayEnd domain** | `app/models/runway_end.py`, `app/models/airport.py`, `app/models/physical_installation_identity.py`, `app/models/__init__.py` | New `RunwayEnd` entity; `Runway.runway_ends` and `PhysicalInstallationIdentity.runway_end_id`/`canonical_runway_end` additions | Runtime domain model |
| **B. Identity linkage** | `app/services/physical_installation_identity_linking.py` | Explicit, human-gated linking of a reviewed `PhysicalInstallationIdentity` to a canonical `RunwayEnd` — never automatic | Runtime service |
| **C. Migration** | `scripts/migrate_canonical_runway_runway_end_slice1.py` | Additive schema change (new table + new nullable column) for an already-populated database | One-off maintenance script (this repo has no Alembic; see §3) |
| **D. FAA NASR acquisition/preservation** | `app/acquisition/nasr_apt_csv.py`, `app/acquisition/faa_runway_ends.py` (refactored, not rewritten), `scripts/acquire_nasr_apt_csv.py` | Discover → download → validate → hash → preserve → sidecar, with no DB dependency | Runtime acquisition module + CLI |
| **E. NASR normalization/classification** | `app/services/runway_identity.py`, `app/services/runway_inventory.py` | `normalize_pair()`/`normalize_end()`; `is_two_ended_pair_shape()`/`is_canonical_runway_candidate()` structural classification; `classify_airport_batch()`/`resolve_us_clean_batch()` | Runtime domain service |
| **F. Canonical runway planning** | `app/services/runway_inventory.py::plan_airport_inventory()`, `apply_plan()` | Deterministic plan generation and the single write path | Runtime domain service |
| **G. Real-DB application tooling** | `scripts/apply_canonical_runway_inventory_mdw_cgf_pilot.py`, `..._us_clean_batch.py`, `..._us_newly_clean_batch.py`, `scripts/correct_allegheny_airport_identity.py`, `scripts/dry_run_canonical_runway_inventory.py` | Narrowly-scoped, dry-run-default, backup-first apply scripts, each targeting a specific approved batch | Maintenance scripts |
| **H. MDW/CGF pilot & identity linking** | `scripts/link_physical_installation_identities_mdw_cgf_pilot.py` | The original 6-identity pilot linking, already applied before this review | Maintenance script (already executed) |
| **I. U.S.-wide batch work** | `..._us_clean_batch.py` (63 airports), `..._us_newly_clean_batch.py` (12, then 1 airport) | Progressive, classification-derived (never hardcoded) apply batches | Maintenance scripts |
| **J. Allegheny final resolution** | `scripts/correct_allegheny_airport_identity.py` | One-off, evidence-backed identity correction for exactly airport id 75 | Maintenance script |
| **K. Tests** | 19 new/modified test files | See §11 | Test suite |
| **L. Documentation** | `docs/domain/*.md` (14 new reports), `docs/data-sources/*.md` (2 new) | Investigation, design, dry-run, and completion reports per slice | Documentation |
| **M. Public/UI boundary** | `tests/test_static_export.py` (test-only change) | No production static-export code was modified; one new regression test proves `RunwayEnd`/`runway_end_id` never leak | Test-only |

No file in group M is production public-export/template code — the
branch does not touch `app/static_export/` at all. This is a backend
domain foundation branch by construction, not a public-facing change.

**Overlap/duplication check:** none found. Each apply script is scoped to
a distinct, non-overlapping approved batch (pilot → 63 → 12 → 1), and each
later script explicitly does not modify or broaden the earlier ones — verified
directly (`git diff --stat` on `..._us_clean_batch.py` across the commits
that introduced the 12- and 1-airport batches shows zero changes to that
file after its initial commit).

## 2. Domain model review

**Verdict: sound, with one non-blocking observation.**

- `Airport 1—N Runway`: unchanged, pre-existing, correct.
- `Runway 1—N RunwayEnd`: `RunwayEnd.runway_id` is `NOT NULL` (every end
  belongs to exactly one runway); `Runway.runway_ends` relationship uses
  `cascade="all, delete-orphan"` — correct, since a `RunwayEnd` has no
  independent existence without its parent `Runway`.
- `UniqueConstraint("runway_id", "designation")` on `RunwayEnd` prevents
  duplicate ends per runway at the database level — good, not just
  planner-level.
- `PhysicalInstallationIdentity.runway_end_id` is **nullable** — correctly
  models "legitimately unresolved" as a first-class, permanent state, not
  an error condition.
- `PhysicalInstallationIdentity.runway_end` (free-text evidence field) is
  **never written by any code introduced in this branch** — confirmed by
  reading `app/services/runway_inventory.py` in full: `apply_plan()` never
  imports or references `PhysicalInstallationIdentity` at all;
  `evaluate_identity_links()` only ever `SELECT`s it. Canonical identity
  cannot silently overwrite evidence identity — the two are structurally
  separate columns, and the only code path that ever writes
  `runway_end_id` is `physical_installation_identity_linking.py`'s
  `apply_identity_links()`, itself gated by the fail-closed
  `plan_identity_links()`/`SAFE_STATUSES` re-check discipline (pre-dates
  this specific review but reused, not modified, by it) — and no script
  in group G/I/J of §1 imports it.
- **Observation (non-blocking, §15):** `Runway` itself has no
  `UniqueConstraint` on `(airport_id, designation)` at the database level
  — deduplication is enforced entirely by `plan_airport_inventory()`'s
  normalized-designation matching, not by a DB constraint. This is a
  pre-existing property of the `Runway` model (not introduced by this
  branch) and is well covered by tests
  (`test_apply_plan_then_replan_is_idempotent`,
  `test_apply_plan_never_identifies_runways_by_airport_alone`), but it is
  a defense-in-depth gap worth closing eventually.
- Indexes: `RunwayEnd.runway_id` and
  `PhysicalInstallationIdentity.runway_end_id` are both indexed — correct
  for FK lookup performance. `tests/test_model_contract.py` was updated to
  pin the exact new columns/FKs/indexes/relationships/cascades — this is a
  strong, already-existing schema-drift safety net that was correctly
  extended, not bypassed.

## 3. Migration review

**Verdict: upgrade path sound and already proven; downgrade path has a
reproducible bug (non-blocking — see reasoning below).**

This repository has no Alembic or formal migration-chain system. Its
established convention (predating this branch — see
`scripts/import_usaspending_grants.py::ensure_source_external_id_column()`)
is: a brand-new database is created via `Base.metadata.create_all()`
directly from the current SQLAlchemy models (which already include
`RunwayEnd` and the new column on this branch — proven correct by all 530
tests, every one of which builds a fresh schema this way); an
**already-populated** database is upgraded via a small, idempotent,
additive, checked-guard script. `scripts/migrate_canonical_runway_runway_end_slice1.py`
follows this convention correctly: it checks `if not table exists` /
`if column not in columns` before acting, wraps the change in an explicit
transaction, requires `--allow-database-write`, and backs up by default.

**Reproducible finding:** `downgrade()` fails when run against a database
containing the full application schema. Verified directly, isolated,
never against the real database:

- A fresh `Base.metadata.create_all()` database (full schema, all ~20
  tables), followed immediately by `downgrade()`, raises:
  `sqlite3.OperationalError: error in table physical_installation_identities
  after drop column: unknown column "runway_end_id" in foreign key definition`.
- The identical `upgrade()` → `downgrade()` sequence against a **minimal**
  two-table schema (just `physical_installation_identities` and
  `runway_ends`) succeeds without error.

The most likely cause is `installation_assertion_links` holding a foreign
key into `physical_installation_identities` — SQLite's native
`ALTER TABLE ... DROP COLUMN` (available since 3.35, confirmed present:
this environment runs 3.50.4) has documented edge cases when other
tables hold foreign keys into the table being altered. This was not
narrowed further, since the **upgrade** path — the one actually used, the
one already run and verified against the real development database
(across every apply task in this branch's history) — works correctly and
was proven idempotent in this same isolated test (re-running `upgrade()`
against an already-current schema is a byte-for-byte no-op). `downgrade()`
is a rollback safety net that has never been exercised against a real,
full-schema database, and this project's actual recovery mechanism
throughout every apply task in this branch has consistently been
timestamped `shutil.copy2` backups plus manual restore, not DDL-level
downgrade — so this finding does not threaten the branch's actual,
already-verified forward path. Classified **non-blocking** — see §15.

## 4. Planner/classification review

**Verdict: sound, no competing heuristics found.**

- `is_two_ended_pair_shape()` (`app/services/runway_identity.py`) is
  purely structural: `len(parts) == 2 and all(part.strip() for part in parts)`
  after splitting on `/`. No character/prefix/suffix test anywhere.
  `is_canonical_runway_candidate()` (`app/services/runway_inventory.py`)
  is a one-line delegate to it, applied inside `classify_airport_batch()`
  as an input-eligibility filter **before** `plan_airport_inventory()` is
  called — the classification boundary is exactly where the design
  intended it, not inside the core planner.
- `plan_airport_inventory()`/`normalize_pair()`/`normalize_end()`
  themselves are unmodified in semantics (the shape check was extracted,
  not changed — verified by the full pre-existing normalization test
  suite passing unchanged) and remain fail-closed: any row that reaches
  them with a malformed shape or a genuine pair/end mismatch still raises
  `AmbiguousRunwayDesignationError`, proven by
  `test_classify_ambiguous_still_fires_for_a_genuine_non_numeric_heading`.
- Branch-wide search for `startswith("H"`, `startswith("B"`,
  `endswith("X"`, or any airport-ID-equality check (`== "AGC"`,
  `== "MDW"`, `== "CGF"`, hardcoded id `75`) inside production matching
  code found **zero** occurrences — every match found was either a test
  fixture, a docstring/comment explaining the *absence* of such a
  heuristic, or the intentionally one-off `correct_allegheny_airport_identity.py`
  (which is explicitly scoped to id 75 by design, not part of the generic
  planner — reviewed separately in §7/§9).
- AGC's `H1` record is excluded because `"H1"` is a single token (no `/`),
  not because of any `H`-prefix rule — directly confirmed by
  `test_is_two_ended_pair_shape_does_not_depend_on_special_prefix_characters`,
  which asserts `"H1/H2"` (contains `H`, but *is* pair-shaped) classifies
  as a **valid candidate**.
- Identifier resolution precedence (`{faa_code, iata_code, icao_code} - {None}`
  matched against NASR `ARPT_ID`) is unchanged from the original MDW/CGF
  pilot design, generic, and has no ambiguity-guessing fallback — a
  `NULL`-everywhere airport is `UNRESOLVED`, never guessed from name/city
  (this is exactly what made Allegheny's original resolution correctly
  fail closed, and what makes the *correction* script's approach
  deliberately out-of-band rather than a planner change).
- Planning and writing remain separated: `plan_airport_inventory()`/
  `classify_airport_batch()`/`resolve_us_clean_batch()` only ever
  `SELECT`; `apply_plan()` is the sole write function, always called
  explicitly and always at the end of an apply script's own precondition
  chain — no implicit writes during planning anywhere.
- Idempotency: proven repeatedly and empirically at every batch size (MDW/CGF
  pilot, 63-airport, 12→1-airport, Allegheny) — re-running any dry run after
  a real apply reports `0/0/0` everywhere, confirmed again in this review's
  own real-DB check (§12).

## 5. NASR acquisition/provenance review

**Verdict: sound, reproducible, correctly separated from ingestion.**

- Cycle discovery (`discover_apt_csv_url()`/`discover_nasr_apt_archive()`)
  computes the effective cycle dynamically from the live FAA index on
  every call — no hardcoded cycle date anywhere in production code
  (confirmed by branch-wide search: the only `"2026-08-06"` occurrences
  outside `DEFAULT_ZIP`/`DEFAULT_DATABASE` default-parameter constants and
  test/docstring text are in documentation, and default parameters are
  overridable, not load-bearing).
- The full download→hash→validate→preserve→sidecar chain was independently
  verified live against the real FAA/NFDC hosts in an earlier task in this
  branch's history (not repeated here, since no new network access is
  authorized for this review) and is exercised entirely by mocked-network
  tests in `tests/test_nasr_apt_csv_acquisition.py` (31 tests) covering
  temp-download-before-preserve, host allowlisting, redirect validation,
  ZIP/member validation, same-hash idempotency, different-hash collision
  abort, sidecar/archive mismatch abort, and CLI dry-run-by-default
  behavior.
- `data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip` remains the archive
  actually used by every canonical-runway apply script in this branch —
  confirmed present, unchanged (size/mtime identical to every prior check
  in this branch's history) throughout this review.
- No source code in this branch relies on chat/manual knowledge to
  reproduce a future acquisition — `app/acquisition/nasr_apt_csv.py` has
  zero references to `SessionLocal`/`app.database`/`sqlalchemy` (confirmed
  by AST-level test `test_module_has_no_database_imports`), and its own
  CLI (`scripts/acquire_nasr_apt_csv.py`) is dry-run by default, requiring
  `--acquire` for a real download.
- Raw preservation and canonical ingestion remain conceptually and
  structurally separate: `app/evidence/nasr_apt_rwy.py` (reader) and
  `app/acquisition/nasr_apt_csv.py` (acquirer) are different modules with
  no import relationship in either direction.

## 6. DB-safety tooling review

**Verdict: consistent, no unsafe scripts found.**

Every real-write script introduced by this branch (group G/I/J in §1)
follows the same 16-point workflow the task describes: dry-run by
default; explicit `--apply` **and** `--allow-database-write` both
required; exact pre-write precondition/collision checks; a second,
immediate pre-write re-resolution that aborts the entire batch on any
drift (membership or aggregate-count); a timestamped backup created
automatically before the first write; post-write backup comparison;
`PRAGMA foreign_key_check`; idempotency proof; focused and full test
runs; public-boundary verification. This was independently confirmed for
every one of the four apply scripts across this branch's own history of
tasks, and re-confirmed by direct reading of all four scripts' source in
this review — none of them writes by default, none lacks a precondition
gate, and none has a write scope broader than its own stated target
(`apply_plan()` itself structurally cannot write outside `runways`/
`runway_ends`; `correct_allegheny_airport_identity.py` structurally
cannot write outside the single `airports` row it fetches by hardcoded
id). No script conflates planning and application — every one calls a
read-only planning function first and only writes after an explicit
`apply=True`/`--apply` branch.

## 7. MDW/CGF protected identity-linkage review

**Verdict: confirmed protected, both architecturally and empirically.**

All six links confirmed present and correctly targeted, read-only,
against the real database in this review:

```
CGF: (1, 57, '06', 9)   (2, 57, '24', 10)
MDW: (3, 12, '04R', 3)  (4, 12, '22L', 4)  (5, 12, '13L', 5)  (6, 12, '31R', 6)
```

Architecturally: `apply_plan()` never imports `PhysicalInstallationIdentity`
(confirmed by grep, §2); no script in group G/I/J of §1 imports anything
from `physical_installation_identity_linking.py` (confirmed by grep across
all five apply scripts). Generic batch logic **cannot** relink these rows
because it has no code path that touches the table at all.
`runway_end_id` is nullable, correctly modeling "unresolved but
legitimate" as a permanent, non-error state. `RunwayEnd`'s own cascade
(`delete-orphan` from `Runway`) combined with the *absence* of a cascade
on `PhysicalInstallationIdentity.canonical_runway_end` means: if a
`RunwayEnd` with an active identity link were ever deleted via its parent
`Runway`, SQLite's FK enforcement (`PRAGMA foreign_keys=ON`, set at every
connection in `app/database.py`) would **raise**, not silently orphan the
link — a fail-safe default, not a defect, though currently untested
directly (see §15).

## 8. Allegheny/USAspending ingestion root-cause assessment

`scripts/import_usaspending_grants.py` is **completely untouched** by
this branch (confirmed: empty diff between `394fa01` and `071ab2d` for
this file). The root cause — `resolve_airport()`'s city/state fallback
path names a newly-created `Airport` after `grant.recipient_name` when no
FAA Loc ID is embedded and no existing airport matches the beneficiary
city/state — **still exists in current code** and **could reproduce** for
any future USAspending grant whose recipient is an authority/organization
operating multiple airports under one name, exactly as it did for
Allegheny. This is isolated to `import_usaspending_grants.py`'s own
fallback branch — no equivalent pattern was found elsewhere in the
ingestion codebase during this review.

**Classification: NON-BLOCKING FOLLOW-UP.**

Reasoning: this canonical-runway branch does not introduce, worsen, or
depend on this ingestion behavior — it *discovered and corrected* one
instance of its downstream effect (airport id 75) through a narrowly-scoped,
evidence-backed, one-off script that intentionally does not touch
`import_usaspending_grants.py` at all, exactly as every prior task in
this branch's history was instructed to do. Blocking this branch's merge
on fixing an unrelated, pre-existing ingestion script would violate the
review's own instruction not to broaden scope for adjacent debt. It
belongs in the follow-up register (§15), not as a merge gate.

## 9. Public-boundary review

**Verdict: no exposure found; no production public code touched.**

`app/static_export/` (templates, `build.py`, presentation logic) has zero
changes in this branch's diff — confirmed via the file list in §1. The
only related change is one new regression test in
`tests/test_static_export.py`
(`test_build_site_does_not_expose_runway_end_or_runway_end_id_anywhere`),
which seeds a linked `RunwayEnd` and asserts neither `"runway_end_id"`
nor `"RunwayEnd"` appear in any generated HTML page or `data.json` — and
the pre-existing `"Banor" not in detail_html` assertion (public runway
inventory suppression) is retained unchanged in the same test file. Every
apply task across this branch's history additionally ran a real
`build_site()` against the actual post-apply database (not repeated here,
since it was already proven and no source changed since) and found zero
leaks each time. "Recent update"/signal behavior is untouched by this
branch — no code in groups A–J references `Signal`/`updated_at`-driven
presentation logic at all.

## 10. Test architecture review

**Verdict: strong coverage of the architectural risks that matter; two
minor gaps, both non-blocking.**

| Risk area | Covered? | Where |
|---|---|---|
| Model contract (schema/FK/index/relationship/cascade pinning) | Yes | `tests/test_model_contract.py` |
| `RunwayEnd` uniqueness/invariants | Yes | `tests/test_runway_end_model.py` |
| Migration upgrade/downgrade | Partially — see below | none in-repo; this review's own isolated test found the downgrade bug (§3) |
| NASR acquisition validation (host allowlist, ZIP/member validation, collision, idempotency) | Yes, extensively | `tests/test_nasr_apt_csv_acquisition.py` (31 tests) |
| Normalization/classification structural rule | Yes, explicitly non-heuristic | `tests/test_runway_identity_normalization.py`, `tests/test_runway_inventory_clean_batch_classification.py` |
| Planner determinism | Yes | `tests/test_runway_inventory_planning.py` |
| Unresolved/ambiguous/conflict classification | Yes | `tests/test_runway_inventory_clean_batch_classification.py` |
| Collision handling (apply scripts) | Yes | each `test_apply_canonical_runway_inventory_*.py` |
| Dry-run non-mutation | Yes, at every layer | every apply-script test file |
| Real-write precondition/drift-abort logic | Yes, including simulated mid-flight drift | `test_apply_aborts_when_membership_changes_before_write`, `..._when_aggregate_plan_changes_before_write` (both batch scripts) |
| Idempotency | Yes | every apply-script test file, plus this review's own real-DB re-checks |
| Protected MDW/CGF linkage | Yes | every apply-script test file asserts all 6 links unchanged after an isolated apply |
| Allegheny correction safeguards | Yes | `tests/test_correct_allegheny_airport_identity.py` (12 tests: old-state drift, FAA/ICAO collision, target-id isolation, exact proposed values, no-mutation dry run, non-idempotent-by-design second apply) |

**Gaps identified:**

- **Migration `downgrade()` has no test coverage against a full-schema
  database** — this review's own ad hoc isolated test is what surfaced
  §3's bug; no such test exists in the committed suite. **Classification:
  FOLLOW-UP** (write a regression test alongside fixing §3, not before).
- **No test exercises the FK-enforcement fail-safe described in §7**
  (deleting a `Runway`/`RunwayEnd` that has an active
  `PhysicalInstallationIdentity` link should raise, not silently orphan).
  **Classification: NICE TO HAVE** — the behavior is a SQLAlchemy/SQLite
  default, not custom logic this branch wrote, and no code path in the
  branch ever deletes a `Runway`/`RunwayEnd`, so the risk is currently
  theoretical.

No coverage gap found rises to **MERGE BLOCKER**.

## 11. Real DB verification (this review, read-only)

| | Value |
|---|---|
| `Runway` | 180 |
| `RunwayEnd` | 360 |
| U.S. planner | 76 `ALREADY_COMPLETE`, 0 `UNRESOLVED`, 0 ambiguous, 0 conflicts |
| Airport 75 | `Allegheny County Airport` / `AGC` / `None` (iata) / `KAGC` |
| AGC runways | `10/28`, `13/31` |
| AGC ends | `10`, `13`, `28`, `31` |
| MDW/CGF links | all 6 present, unchanged (values in §7) |
| `PRAGMA foreign_key_check` | `[]` |
| `PRAGMA integrity_check` | `ok` |

DB size/mtime unchanged by this review (`667648` bytes throughout — this
review performed zero writes).

## 12. Diff hygiene

- No `.db`, backup, raw-NASR, or generated-site file is tracked anywhere
  in the branch delta — confirmed by name-pattern search across the full
  51-file list; all such paths remain correctly excluded by `.gitignore`
  (`data/*.db`, `data/raw/*`, `data/backups/`).
- No `TODO`/`FIXME`/`XXX`/`HACK` marker in any new production code.
- No commented-out code blocks found.
- No absolute local filesystem paths embedded in runtime code (`app/`,
  `scripts/`) — only in documentation/report text, where they're
  descriptive, not operational.
- No Windows-only path assumptions — `pathlib.Path` used throughout;
  zero hardcoded backslash path literals in production code.
- No secrets/credentials found in any new file.
- Unrelated pre-existing untracked files in the working tree (54 files:
  `docs/research/*`, `docs/ui/public-ui-hero-integration-report.md`,
  `docs/ui/screenshots/**`) are **not part of this branch's commits** —
  confirmed via `git diff --name-status 394fa01..071ab2d`, which does not
  list any of them. They remain untracked in the working directory only,
  outside the scope of this merge review.

## 13. Full validation (this review)

- Full pytest suite: **530 passed** — exact match to the expected
  baseline.
- Compilation: every `.py` file introduced or modified by this branch
  compiles cleanly (`py_compile`, run individually on the branch's full
  file list).
- `git diff --check`: exit 0.

No test or check was skipped; no code was changed to make any check pass.

## 14. Merge blockers

**None.**

## 15. Follow-up register

| # | Category | Issue | Why it matters | Severity | Recommended slice | Timing |
|---|---|---|---|---|---|---|
| 1 | A. Ingestion/data-quality debt | `import_usaspending_grants.py`'s city/state fallback can name an `Airport` after a grant-recipient organization instead of the facility (the Allegheny root cause) — still present, could recur | Produces future `UNRESOLVED` canonical-runway rows requiring the same manual investigation-and-correction cycle | Medium | Add an explicit check (e.g. require the fallback-created row's `notes` field, already present, to gate it out of automatic downstream use until reviewed) or a second-pass Loc-ID lookup by facility name | After merge, not urgent — no active data-quality incident today |
| 2 | D. Migration/schema debt | `migrate_canonical_runway_runway_end_slice1.py::downgrade()` fails against a full-schema database (§3) | Rollback-by-DDL is currently unreliable for this migration; actual recovery has always used timestamped-backup restore instead, so no live risk today, but the bug should not surprise a future maintainer | Medium | Rework `downgrade()` to rebuild the table (SQLite's documented workaround for FK-referenced-column drops) or explicitly document that backup-restore, not `--downgrade`, is the supported rollback path | Before the next time schema downgrade is actually needed — not before merge |
| 3 | B. Canonical-domain debt | No DB-level `UniqueConstraint` on `Runway(airport_id, designation)` | Dedup currently relies entirely on planner-level normalized matching, not a DB constraint; a future non-planner write path could create a duplicate | Low | Add the constraint in a small additive migration once a second write path to `Runway` is ever introduced | Only if/when a second write path appears |
| 4 | E. Testing debt | No regression test for `downgrade()` against a full-schema DB | Same root issue as #2; a test would have caught it earlier | Low | Add alongside the #2 fix | With #2 |
| 5 | E. Testing debt | No test for the FK-enforcement fail-safe on `Runway`/`RunwayEnd` deletion with an active identity link | Currently theoretical — no code path deletes these rows | Very low | One small isolated test asserting `IntegrityError` on such a delete attempt | Opportunistic, low priority |

## 16. Final merge-readiness verdict

**`READY_TO_MERGE_WITH_NON_BLOCKING_FOLLOWUPS`**

- **Blockers: NONE.**
- **Non-blocking follow-ups:** the 5 items in §15, none of which affects
  the correctness, safety, or completeness of the already-applied and
  already-verified 76/76 U.S. canonical runway milestone this branch
  delivers.

The domain model is sound, the planner is generic and fail-closed with no
competing heuristics, every real-write script follows the same
established, tested safety discipline, the MDW/CGF protected links are
structurally un-touchable by any batch logic in this branch, the real
database's current state matches the branch's own claims exactly, the
full test suite passes at the expected count, and diff hygiene is clean.
The one adjacent ingestion-layer debt item (USAspending's naming
fallback) and the one migration-tooling bug (`downgrade()`) are both
real, both worth fixing, and both correctly out of scope for this
specific merge per the review's own instruction not to broaden the
branch for adjacent debt.
