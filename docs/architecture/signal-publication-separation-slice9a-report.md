# Signal Publication Separation — Slice 9A Report

Implements Slice 9A of
`docs/architecture/reviewer-action-human-signal-promotion-slice9-design.md`
§8/§14: separate `CREATE_SIGNAL` from `PUBLISH_SIGNAL` by replacing the
hardcoded publication exclusion with an explicit `Signal.published` column.
No `ReviewerAction`, no `SourceAssertion.signal_id`, no governed Signal
creation, no real DB write. Design only became code for this one, narrow,
additive boundary.

## 1. Starting HEAD

`main` at `f0da2aed08a9079d6dd2d55f8c211d4881b45b20`, matching `origin/main`.
Baseline: 1060 passed.

## 2. Old publication mechanism

`app/static_export/build.py::_is_public_signal()`, re-verified before any
change:

```python
def _is_public_signal(signal: Signal) -> bool:
    """Quarantine the two baseline-identified unnormalized airport Signals."""
    return signal.id not in (52, 54)
```

Used at exactly two call sites: `_airport_view()` (airport-detail signal
list) and `build()`'s main `all_signals`/`signal_views` construction. No
field on `Signal` represented publication; it was a hardcoded two-id
exclusion tuple, the entire mechanism.

## 3. Live risk being closed

Every one of the six existing Signal-creation sites
(`app/services/signal_rules.py`, and the five `scripts/*.py` importers)
produces a Signal that is public immediately, by omission — nothing marks a
new row as excluded unless a developer remembers to hand-edit the exclusion
tuple. A future human-approved governed Signal (Slice 9C, not built here)
would have had no way to stay internal-only without this change.

## 4. Signal schema change

One new column: `signals.published BOOLEAN NOT NULL DEFAULT 1` (SQL-level
default fills every existing row to `1`/true as part of the same `ALTER
TABLE` statement). Model: `app/models/signal.py`, `published: Mapped[bool] =
mapped_column(Boolean, nullable=False, default=True)`. No other column,
table, index, or foreign key changed. `test_model_contract.py` updated to
expect exactly this one addition; `test_model_table_contract_is_unchanged`,
`test_model_relationship_contract_is_unchanged`, and
`test_current_metadata_creates_cleanly_in_isolated_sqlite` all still pass.

## 5. Default semantics

Two goals were in real tension, exactly as the design doc anticipated: (a)
every existing writer must keep working unmodified, which wants a default of
`True`; (b) a future governed write should be fail-closed, which wants a
default of `False`. A single column default cannot satisfy both. Resolution:
the **model/DB-level default is `True`** (serves the six existing writers,
the more urgent and immediately-real need), and **fail-closed behavior for
governed creation is a service-layer responsibility**, not a column default —
Slice 9C's `create_signal_from_approved_review()` must explicitly pass
`published=False` itself. This is documented directly on the model as a code
comment so a future implementer of Slice 9C cannot miss it. Chosen model:
column is `NOT NULL` (not nullable) — every row has a real, unambiguous
answer to "is this public," and the migration can compute a real backfill
value for every existing row (no NULL/fail-closed-on-NULL semantics were
needed).

## 6. Migration / backfill strategy

`scripts/migrate_signal_publication_slice9a.py`, modeled on the proven
pattern in `scripts/migrate_promotion_policy_persistence_slice7.py`
(explicit `--database`, `--allow-database-write` gate, timestamped backup
unless `--skip-backup`, idempotent `upgrade()`, `downgrade()` via the
existing `_drop_column_via_rebuild()` table-rebuild technique reused
verbatim). Differs from every prior Slice 1/4/7 migration in one respect:
this column cannot be added nullable-and-left-NULL, because publication must
have a real answer for every row immediately. `upgrade()` therefore: (1)
`ALTER TABLE signals ADD COLUMN published BOOLEAN NOT NULL DEFAULT 1` — SQLite
backfills every existing row to `1` as part of this one statement; (2)
`UPDATE signals SET published=0 WHERE id IN (52, 54)` — the exact legacy
exclusion set, re-asserted idempotently on every run regardless of whether
the column already existed.

## 7. Legacy public-set preservation

`LEGACY_EXCLUDED_SIGNAL_IDS = (52, 54)` in the migration script is the sole
source of truth for the backfill — not re-derived from any other heuristic,
matching the task's own instruction not to invent a new rule. Verified two
ways: (a) `tests/test_signal_publication_migration.py`'s
`test_public_signal_id_set_identical_before_and_after_migration` computes the
old rule (`id not in (52, 54)`) independently of the updated
`_is_public_signal()` and asserts the resulting id set is unchanged after
migration; (b) the disposable real-DB rehearsal (§12) computed the same
comparison against all 68 real rows and found an exact match.

## 8. Static-export change

`_is_public_signal()` now reads `signal.published` directly — the hardcoded
exclusion tuple is removed entirely, not kept alongside the new field. Both
call sites (`_airport_view()`, `build()`) are unchanged; they still call
`_is_public_signal(signal)`. `test_build_site_excludes_unpublished_signal_from_public_output`
(new, in `tests/test_static_export.py`) proves an unpublished Signal is
absent from `index.html`, `signals/index.html`, `data.json`, and has no
per-signal detail page, while an ordinary published Signal is unaffected.

## 9. Existing Signal-writer audit

All six sites re-inspected; none modified, none needed to be:

| Site | Verdict |
|---|---|
| `app/services/signal_rules.py::add_source_and_flag_keywords` | Unchanged. Relies on the model default (`published=True`). |
| `scripts/import_usaspending_grants.py` | Unchanged. Same. |
| `scripts/import_faa_construction_report.py` | Unchanged. Same. |
| `scripts/add_mdw_emas_bed_repairs_signal.py` | Unchanged. Same. |
| `scripts/add_rw_shareholder_letter_signals.py` | Unchanged. Same. |
| `scripts/add_brazil_expansion.py` | Unchanged. Same. |

`test_signal_created_without_published_kwarg_matches_legacy_writer_shape`
constructs a `Signal(...)` the same way `signal_rules.py` does (no
`published` argument) and asserts it defaults to `published=True` and passes
`_is_public_signal()` — proving the model default alone preserves every
current writer's behavior without touching their code, confirming the task's
"minimal behavior-preserving changes" preference.

## 10. Internal (unpublished) Signal semantics

Verified by `test_unpublished_signal_stays_internally_queryable` (a Signal
with `published=False` round-trips through `session.get()` after
`session.expire_all()` with all its data intact) and
`test_unpublished_signal_excluded_from_is_public_signal_filter`. No deletion,
no invalid state, no special-casing anywhere else in the ORM — it is an
ordinary row that one function's filter excludes.

## 11. Future governed-write safety

`test_creating_a_signal_does_not_imply_public_export` and
`test_signal_model_accepts_explicit_published_false` together prove the
future intended shape — `Signal(..., published=False)` persists normally and
is excluded from `_is_public_signal()` — without connecting to
`SourceAssertion` or creating anything resembling Signal #69. `Slice 9C` is
not implemented.

## 12. Real-data disposable rehearsal

Performed against a disposable copy only, at
`<scratchpad>/disposable_slice9a_rehearsal.db` (deleted after verification;
never inside the repository or `data/`):

- Pre-migration real DB sha256: `1eb956b3a17a866af94d9e5f7b1a0f388eb68a19e6642d551616a93d5b8de736`, size 1,769,472 bytes.
- `upgrade()` run against the disposable copy (once accidentally without
  `--skip-backup`, which wrote a backup of the *disposable* file — not the
  real database — into `data/backups/`; that stray file was identified and
  deleted immediately, and the real database was independently confirmed
  byte-unchanged before and after). All subsequent rehearsal runs used
  `--skip-backup` as intended for a temp DB.
- Result: `signals_count=68` (unchanged), `published_true_count=66`,
  `published_false_count=2`, `legacy_excluded_ids_published_false=True`,
  `foreign_key_check=[]`, `integrity_check=('ok',)`.
- Public-set equivalence: the id set with `published=1` after migration
  equals the id set the old `id not in (52, 54)` rule would have produced —
  exact match, all 68 rows accounted for.
- Every non-`published` column, for all 68 rows, compared column-for-column
  between the real database and the disposable post-migration copy: **byte
  identical**.
- On a second, fresh disposable copy: `upgrade()` → `downgrade()` →
  re-`upgrade()` all succeeded; `published_column_exists` correctly toggled
  `True → False → True`; `signals_count` stayed 68 throughout;
  `foreign_key_check` stayed empty at every step.

## 13. Public before/after equivalence

Confirmed identical by both the unit-level test (§7) and the real-data
rehearsal (§12): the set of publicly visible Signal ids is unchanged, and no
existing public Signal's rendered content changed, since no column other
than the new `published` was touched.

## 14. FK/integrity

Clean at every step of every rehearsal and every migration test:
`PRAGMA foreign_key_check` empty, `PRAGMA integrity_check` returns `ok`,
both after `upgrade()` and after `downgrade()`.

## 15. Focused tests

`tests/test_signal_publication_migration.py` (17 test functions, one
parametrized over 7 modules = 23 cases) plus one new test in
`tests/test_static_export.py` = **24 new tests**, covering: model default and
explicit `published=False`; migration upgrade adds-and-backfills; idempotent
upgrade; exact-reversible downgrade (byte-identical schema text and row
data); FK/integrity after upgrade and after downgrade; legacy-public and
legacy-excluded backfill correctness; exact public-id-set equivalence;
row-count invariance; internal queryability of an unpublished Signal;
exclusion from `_is_public_signal()`; create-does-not-imply-publish; legacy
writer-shape default preservation; AST-verified absence of any `Signal(`
construction in seven discovery/review/promotion modules; missing
`--allow-database-write` flag safety; migration against a DB missing the
`signals` table raising cleanly; static-export regression excluding an
unpublished Signal from every generated page and `data.json`.

Command: `python -m pytest -q tests/test_signal_publication_migration.py
tests/test_static_export.py tests/test_model_contract.py` → **65 passed**.

## 16. Full pytest

**1084 passed** (1060 baseline + 24 new), 0 failed, in 155.94s.

## 17. py_compile

`python -m py_compile app/models/signal.py app/static_export/build.py
scripts/migrate_signal_publication_slice9a.py
tests/test_signal_publication_migration.py tests/test_static_export.py
tests/test_model_contract.py` → clean, no errors.

## 18. git diff --check

Clean (zero whitespace errors) across all changed/new files. Two benign
"LF will be replaced by CRLF" advisory warnings on the two new files
(platform line-ending normalization notice, not an error).

## 19. Real DB unchanged

sha256 `1eb956b3a17a866af94d9e5f7b1a0f388eb68a19e6642d551616a93d5b8de736`,
size 1,769,472 bytes, mtime `1787145621.2311418` — identical before this
task, immediately after the (accidental, then corrected) disposable-copy
backup incident, and at final verification. No migration, no write, no
schema change was ever applied to `data/runway_safe.db` itself. A stray
backup file was created in `data/backups/` from an inadvertent omission of
`--skip-backup` while migrating the *disposable* copy — its contents were a
copy of the disposable file, not the real database — and it was deleted
immediately upon discovery; no trace of it remains.

## 20. Exact files changed

- `app/models/signal.py` — modified (new `published` column + `Boolean` import)
- `app/static_export/build.py` — modified (`_is_public_signal()` body only)
- `scripts/migrate_signal_publication_slice9a.py` — new
- `tests/test_signal_publication_migration.py` — new
- `tests/test_static_export.py` — modified (one new test)
- `tests/test_model_contract.py` — modified (one new expected column)
- `docs/architecture/signal-publication-separation-slice9a-report.md` — new (this file)

## 21. git status

All six code/test changes are unstaged modifications/additions in the
working tree; no commit was made. Pre-existing untracked documentation/UI
files from prior sessions remain untouched and unrelated to this task.

## 22. Design corrections discovered

None to the Slice 9 design document's architecture. One implementation-time
clarification not spelled out in the design doc: the design doc's §8
described the additive column in general terms but didn't address that,
unlike every prior Slice 1/4/7 additive column, this one cannot be left NULL
on existing rows — it required an immediate, computed backfill for all 68
rows in the same migration. This is a data migration as well as a schema
migration, which is why `upgrade()`'s docstring calls this out explicitly as
a deliberate departure from the established nullable-additive-column
pattern.

## 23. Ready for review/commit

Yes. All required verification passed: full suite (1084/1084), focused
suite, py_compile, git diff --check, real-data disposable rehearsal
(upgrade, public-set equivalence, downgrade, re-upgrade), real DB confirmed
byte-unchanged. No code beyond the documented scope was touched. Awaiting
the separate review/commit/push authorization per this project's established
one-task-per-write-boundary discipline.

## 24. Recommended Slice 9B scope

Per the design doc's own reordered roadmap (§14 there), Slice 9B is
**reviewer-action persistence**: add the `ReviewerAction` table (modeled on
`InstallationAssertionLink`), a `persist_reviewer_action()`-shaped service,
its own migration, and tests. No Signal creation yet — 9B only lets a human
record `APPROVE_SIGNAL`/`REJECT_SIGNAL`/`DEFER`/`NEEDS_MORE_EVIDENCE`/
`MARK_DUPLICATE` against a `SourceAssertion`. Slice 9C (`SourceAssertion.signal_id`
+ `create_signal_from_approved_review()`, composing 9A's `published=False`
override with 9B's `APPROVE_SIGNAL` check) remains the earliest point at
which any Signal could actually be created from governed evidence, and stays
explicitly out of scope until separately authorized.
