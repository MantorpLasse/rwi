# NASR EMAS AUTO_RESOLVABLE Promotion — Dry-Run Report

Implements and rehearses (but does **not** execute against the real
database) the guarded promotion writer for the 97 NASR current-EMAS-
presence `SourceAssertion` rows classified `AUTO_RESOLVABLE` by
`scripts/analyze_nasr_emas_runway_end_resolution.py`.

## ⚠️ Incident during this task, fully resolved

**During development of this task, a real, unauthorized write occurred
against `data/runway_safe.db`, then was fully diagnosed, reverted, and
its root cause fixed.** Full transparency, in order:

1. While rehearsing the writer against a disposable copy
   (`--database <scratch>/disposable_runway_safe.db --apply
   --allow-database-write`), the script's `main()` built its backup from
   the correct `--database` path, but then executed the actual read/write
   session via the imported `app.database.SessionLocal` — a
   process-global session factory bound once, at import time, to the
   application's own configured database (`data/runway_safe.db`),
   **completely independent of the script's own `--database` argument.**
   As a result, the intended disposable-copy write was instead applied to
   the real database: all 97 `AUTO_RESOLVABLE` rows had `runway_end`
   written.
2. This was caught immediately (the very next command in this task
   re-checks the real DB's size/mtime/hash after every risk-bearing
   step). The real DB's mtime had changed and 97 rows showed
   `runway_end IS NOT NULL`.
3. The same `main()` call had, moments earlier and correctly, created a
   pre-write backup of the file passed as `--database` — which, at that
   exact moment, was still a byte-identical, unmutated copy of the real
   database (it had just been `cp`'d seconds before). That backup
   (`data/backups/runway_safe-pre-nasr-emas-runway-end-promotion-20260817-213632.db`)
   was verified (`PRAGMA integrity_check` → `ok`, correct
   `Runway`/`RunwayEnd`/`PhysicalInstallationIdentity` counts, correct
   Morristown/Allegheny corrections present, 0 promoted rows) and used to
   restore the real database.
4. Restoration was verified **byte-for-byte** via SHA-256 hash
   (`731e51dc...`) matching between the backup and the restored file, plus
   `PRAGMA integrity_check`/`foreign_key_check`, all governed counts, and
   all 6 MDW/CGF `PhysicalInstallationIdentity` links — all confirmed
   identical to the pre-incident state.
5. **Root cause fixed**: `main()` now builds its own SQLAlchemy engine/
   session directly from the resolved `--database` path for every
   operation (dry-run and apply alike), and never references
   `app.database.SessionLocal` at all. A second, related bug found during
   the same rehearsal (`apply()`'s own dirty-row verification silently
   only caught the *last* row in a multi-row batch, because the default
   `autoflush=True` session behavior flushes earlier pending changes as
   soon as a later `session.get()` call for a different row runs) was
   fixed with an explicit `session.no_autoflush` block around the write
   loop, and covered by a new dedicated regression test using a plain,
   default-autoflush session and 3 writable rows across 3 airports —
   exactly the condition that exposed it (no single-row test, in this
   suite or the prior classifier's, could have caught it).
6. **This same latent `--database`-is-decorative flaw exists in every
   prior correction/apply script in this repository** (e.g.
   `scripts/correct_morristown_airport_identity.py`,
   `scripts/correct_allegheny_airport_identity.py`) — it was never
   exposed before because no earlier task ever tried to redirect an
   `--apply` write away from the real database. This is flagged here as a
   cross-cutting follow-up (§ below), not fixed in those other scripts in
   this task (out of scope).

**Net effect on the real database: none.** Verified via SHA-256 hash
match, `PRAGMA integrity_check`/`foreign_key_check`, and every governed
count, both immediately after restoration and again at the end of this
entire task.

## 1. Semantic contract (inherited, unchanged)

Per `docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md`:
`SourceAssertion.raw_runway_end_value` = source value verbatim;
`SourceAssertion.runway_end` = normalized canonical **physical** designation;
`PhysicalInstallationIdentity.runway_end_id` = canonical physical bed
location; protected operational direction is derived via reciprocal
`RunwayEnd` topology, never stored by this slice. NASR current-presence
evidence establishes only "present as of this NASR cycle" — never
install year, replacement lifecycle, manufacturer, or dimensions.

## 2. Exact writer scope

`scripts/promote_nasr_emas_runway_end_assertions.py` reuses
`scripts/analyze_nasr_emas_runway_end_resolution.py`'s classification
logic entirely (imports `classify_all`/`summarize`, never reimplements
it). Bounded to `assertion_type="runway_end"` rows the classifier calls
`AUTO_RESOLVABLE`. Writes exactly one field:
`SourceAssertion.runway_end = <normalized physical designation>`. Never
writes the reciprocal/protected designation. Never touches
`raw_runway_end_value`, `raw_airport_name`, `raw_airport_identifier`,
`raw_relevant_text`, `evidence_quality`, `review_state`, `airport_id`,
`source_id`, `assertion_type`, `notes`, or any `Source`, `Signal`,
`Runway`, `RunwayEnd`, `Airport`, `Installation`,
`PhysicalInstallationIdentity`, or `InstallationAssertionLink` row.
`SourceAssertion` has no `updated_at`/`onupdate` column at all (checked
directly in the model) — confirmed nothing else changes, not even an
automatic timestamp.

## 3. Safety model

Dry-run by default. A real write requires **both** `--apply` and
`--allow-database-write`; `--apply` alone is a CLI usage error
(`parser.error`, `SystemExit`, no backup, no session). `apply()` re-plans
(re-classifies + re-checks the snapshot + fingerprint) immediately before
writing, never trusting a caller-supplied stale plan.

## 4. Preconditions (all 13 from the task brief, as implemented)

Handled by a combination of the reused classifier (population
membership, evidence quality, per-row physical mapping, conflicting/
ambiguous-identity checks) and this writer's own `_writer_row()`/`plan()`
layer: `runway_end` currently `NULL` (→ `WRITABLE`) vs. already correctly
set (→ `ALREADY_PROMOTED`, a no-op) vs. set to something else entirely
(→ `DRIFTED`, hard failure). Any `DRIFTED` row aborts the **entire**
batch — never a partial skip treated as success.

## 5. Approved snapshot (frozen guard)

```python
EXPECTED_SNAPSHOT = {
    "assertions_total": 115,
    "AUTO_RESOLVABLE": 97, "ALREADY_LINKED": 9, "REVIEW_REQUIRED": 9,
    "AMBIGUOUS": 0, "CONFLICT": 0, "INSUFFICIENT_EVIDENCE": 0,
}
```

`plan()` aborts with `PromotionGuardError` if a fresh classification
doesn't match this exactly.

## 6. Deterministic fingerprint

SHA-256 of the sorted `(assertion_id, airport_id, raw_runway_end_value,
proposed_runway_end)` tuples for every `WRITABLE`-state row. Computed
fresh on every `plan()` call; `apply()` requires an `--expected-
fingerprint` argument and aborts on any mismatch.

**Current approved fingerprint** (stable across repeated dry-runs against
the real database, confirmed twice):

```
05d76227c3fe863c30aa8adbcbaeb8a92590e5f8f687ca4a103b3b59f7d38d42
```

## 7. Dry-run behavior

`dry_run()` calls `plan()` (read-only) and asserts
`session.new == session.dirty == session.deleted == 0` before returning
— proven directly, not merely assumed from "no commit was called."

## 8. Backup strategy

Identical convention to every prior correction script:
`data/backups/runway_safe-pre-nasr-emas-runway-end-promotion-<timestamp>.db`,
created only on `--apply`, after CLI validation but before the session
opens, verified via `PRAGMA integrity_check` immediately after creation.

## 9. Transaction strategy

`apply()` re-plans, writes inside a single `session.no_autoflush` block
(preventing any implicit partial flush mid-loop), verifies the pending
change set exactly matches the intended `WRITABLE` rows, flushes, re-
reads every touched row from the database to confirm the exact expected
post-write values, and only then commits. Any mismatch at any stage
raises `PromotionGuardError` — no partial commit path exists.

## 10. Idempotency strategy

A rerun after a successful apply reclassifies every previously-written
row as `ALREADY_PROMOTED` (matching value, no-op) rather than `WRITABLE`
— `dry_run()` on an already-promoted population reports `writable_count:
0`. This is a writer-level state layered on top of the classifier's own
unchanged `AUTO_RESOLVABLE` class (the classifier itself doesn't consider
current `runway_end` state, by design — see the semantics analysis).

## 11. Focused tests

`tests/test_promote_nasr_emas_runway_end_assertions.py` — **18 tests**,
all against isolated in-memory or temp-file databases, never the real
one: dry-run zero writes, only `AUTO_RESOLVABLE` rows planned, correct
physical designation written, reciprocal designation never written,
`REVIEW_REQUIRED`/`ALREADY_LINKED` rows and their identities/links left
byte-for-byte untouched, snapshot drift fails closed, fingerprint drift
fails closed even at equal count, missing raw value fails closed,
already-correct `runway_end` treated as a no-op, drifted `runway_end`
fails closed, both CLI flags required, backup precedes mutation, rerun
after success is a no-op, no `PhysicalInstallationIdentity`/
`InstallationAssertionLink` ever created, `Runway`/`RunwayEnd` rows
unchanged, raw evidence fields unchanged, and — added directly because of
the incident above — a multi-row (3 airports) batch write under a
default-autoflush session, proving `apply()`'s own `no_autoflush` guard
makes it correct regardless of the caller's session configuration.

## 12. Real-DB dry-run result

| | |
|---|---|
| DB path | `data/runway_safe.db` |
| Classifier counts | 115 total; 97 `AUTO_RESOLVABLE`; 9 `ALREADY_LINKED`; 9 `REVIEW_REQUIRED`; 0/0/0 |
| Fingerprint | `05d76227c3fe863c30aa8adbcbaeb8a92590e5f8f687ca4a103b3b59f7d38d42` (stable across 2 independent dry-runs) |
| Affected airports (writable) | 56 |
| Unique physical ends implicated | 112 |
| Duplicate assertions, same physical end | 3 pairs (CGF `06`, CGF `24`, MDW `4R`) — all `ALREADY_LINKED`, none writable |
| `REVIEW_REQUIRED` airports | BGM, BOS, ELM, LEX, ORH |
| `ALREADY_LINKED` airports | CGF, MDW |
| Session mutation proof | `session.new/dirty/deleted == 0` asserted directly inside `dry_run()` |
| Real DB size/mtime before vs. after this dry-run | unchanged (667648 bytes; verified by hash) |

Proposed writes = 97 exactly, on every run. No STOP condition triggered.

## 13. Disposable-copy apply result

Full apply executed only against a disposable file-copy in the session
scratch directory (never the real database — see the incident note for
the one exception that occurred, caught, and reverted before any other
step in this task continued). Result: `rows_written: 97`, matching the
approved fingerprint exactly.

## 14. Exact simulated changed-row/table analysis

Full 14-table diff between the disposable copy's own pre-apply backup and
its post-apply state: **exactly 97 rows changed, all in
`source_assertions`, and the only field touched on any of them was
`runway_end`.** No other table, row, or column differs anywhere.

## 15. Protected-table verification

On the disposable copy, post-apply: `Runway`=180, `RunwayEnd`=360,
`PhysicalInstallationIdentity`=6, `InstallationAssertionLink`=8 — all
unchanged. All 6 MDW/CGF identity rows (`runway_end`/`runway_end_id`
values) unchanged. All 9 `REVIEW_REQUIRED` assertions (including BOS
161/162, ORH 164/165) confirmed still `runway_end IS NULL`.

## 16. FK/integrity result

`PRAGMA foreign_key_check` → `[]`. `PRAGMA integrity_check` → `ok`.

## 17. Public-product impact

Static export generated **from the disposable copy only**, into a
scratch-only location, never affecting the real committed `site/`
directory or any deployed output.

- **56 airport pages gain a populated "EMAS idag" section** (via the
  `nasr_presence` pathway) — matches the writer's own `affected_airport_count`
  exactly.
- **0 duplicate pills** within any single airport's section.
- **28 airports remain with zero current-EMAS publication** after this
  batch — airports whose only NASR assertions are `REVIEW_REQUIRED` (BOS,
  ORH, BGM, LEX, ELM) plus airports with no NASR EMAS assertion at all.
  MDW/CGF are unaffected by (not counted as gaining from) this batch —
  they already publish via the separate, human-reviewed
  `reviewed_identities` pathway.

## 18. Presentation consequence

**Classification: `TECHNICALLY_CORRECT_BUT_NEEDS_PRESENTATION_FOLLOWUP`.**

Every value this batch publishes is the literal, sourced, physical NASR
`RWY_END_ID` — factually accurate and exactly the same convention already
live today for all 6 MDW/CGF pills (this is not a new pattern). However,
the BOS/ORH/BGM cases prove that for *some* airports, the operator's own
public language names the reciprocal end instead — and because that
signal was detected only from RWI's own free-text evidence (not from an
inherent property of the data), it is plausible that a few of the 97
`AUTO_RESOLVABLE` airports have the same real-world naming difference
without it happening to be documented anywhere in RWI's notes yet. This
is not classified `BLOCKING_PRESENTATION_PROBLEM`: the published values
are never factually wrong (they are exactly what the FAA reports, exactly
as qualified by the existing site copy — *"Uppgiften beskriver förekomst
vid banände, inte projektstatus eller fysisk historik"*), and the known,
confirmed conflicting cases are already correctly excluded from this
batch. It is not `SAFE_AS_IS` either, because a presentation
improvement (the reciprocal-derived label design already proposed in the
semantics analysis, §15/17 there) would make the page markedly less
likely to visually contradict what an airport's own press materials say.
**No UI change was made in this task** — this is evidence for the next
slice, per instruction.

## 19. Remaining REVIEW_REQUIRED set (untouched by this writer)

BOS (assertions 161, 162), ORH (164, 165), BGM (181, 182), LEX (153,
154), ELM (183) — 9 assertions across 5 airports, byte-for-byte
unchanged by both the dry-run and the disposable-copy apply. These
belong to a later, separate, evidence-backed human-reconciliation slice
(the semantics analysis's §21 recommendation), not this one.

## 20. Exact future real-apply command

```
.venv\Scripts\python.exe -m scripts.promote_nasr_emas_runway_end_assertions ^
  --apply --allow-database-write ^
  --expected-fingerprint 05d76227c3fe863c30aa8adbcbaeb8a92590e5f8f687ca4a103b3b59f7d38d42
```

(`--database` omitted → defaults to `data/runway_safe.db`; the fingerprint
above is the one approved by this dry-run and must be re-confirmed
unchanged by a fresh dry-run immediately before any real apply is
authorized, since this document itself will age.)

## 21. Recommendation: is real apply approved?

**Not approved in this task** (explicitly out of scope — dry-run and
disposable-copy rehearsal only, per instruction). Based on the evidence
gathered, this batch is a strong, well-guarded, low-risk candidate for
future approval: 97 writes, one field each, exhaustively tested,
rehearsed successfully end-to-end on a disposable copy with full
verification, zero effect on any protected table, and a
`TECHNICALLY_CORRECT_BUT_NEEDS_PRESENTATION_FOLLOWUP` (not blocking)
presentation consequence. The incident in this task's own development
(§ above) was caused by a bug in the writer's CLI plumbing, not by any
flaw in the guard design itself — every guard (snapshot, fingerprint,
per-row precondition, post-write verification) worked exactly as
designed the moment the underlying session-wiring bug was fixed, and the
real database was protected end-to-end by its own independent safety net
(the automatic pre-write backup) even while the bug was active.

## Cross-cutting follow-up (not fixed in this task)

Every prior correction/apply script in this repository
(`scripts/correct_morristown_airport_identity.py`,
`scripts/correct_allegheny_airport_identity.py`, and likely others)
shares the same latent flaw this task's incident exposed: their
`--database` CLI argument is threaded through to `backup_database()` but
never to the actual read/write session, which always uses the shared
`app.database.SessionLocal` regardless. This was never previously
observable because no earlier task tried to redirect an `--apply` write
away from the real database — but it means none of those scripts can
currently be safely rehearsed against a disposable copy the way this
task's writer now can. Worth a small, dedicated follow-up to audit and
fix consistently across the family, before any of them is next rehearsed
this way.
