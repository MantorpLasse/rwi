# MSP Gated Discovery Capture Runner — Dry Run

The first explicit, gated command that orchestrates the full, already-
committed MSP discovery pipeline end to end:

```
live MAC archive (app.acquisition.mac_granicus)
    -> AcquisitionService / Snapshot
    -> MAC extractor (app.acquisition.mac_granicus_extractor)
    -> CandidateFragment
    -> alternate-airport topology enrichment
    -> EvidenceBag
    -> Evidence Attachment Guard
    -> governed persistence (app.services.discovery_evidence_persistence)
```

**Implementation + dry-run + disposable-DB rehearsal only.** No real
database migration, no real database write, no Signal creation, no
scheduler, no n8n, no public UI change, no deployment, no commit, no
push. Baseline: branch `main`, HEAD
`d89273160a61c6e0f24d603ad0fce18be6fb6b17`.

## 1. Runner architecture

`scripts/capture_mac_discovery.py` — one file, no new framework. It adds
no new pipeline logic: every step (acquisition, extraction, guard
evaluation, enrichment, persistence) calls the already-committed,
unmodified component directly. The genuinely new code is narrow:
candidate-airport selection, two-pass enrichment orchestration, read-only
persistence planning, fingerprinting, and the CLI safety gates.

Invoke as a module, not a bare script (`scripts/capture_mac_discovery.py`
has no `scripts/__init__.py`, and the file itself imports
`scripts.migrate_discovery_governed_evidence_slice1` by package path):

```
python -m scripts.capture_mac_discovery [flags]
```

Public entry points: `run_capture(config: CaptureConfig, *, client=None)
-> dict` (used directly by tests and by `main()`), `CaptureConfig`,
`FixtureDocument`.

## 2. CLI safety model

Default-closed on every axis:

| Flag | Effect |
|---|---|
| (none) | No live network, no write. Requires `fixture_documents` (or fails with `NO_LIVE_NETWORK_AND_NO_FIXTURE_PROVIDED`). |
| `--allow-live-network` | Enables real HTTP fetches against `metroairports.granicus.com`. Still no database write on its own. |
| `--apply` | Requires `--allow-database-write` too, or refuses (`"--apply requires --allow-database-write."`). |
| `--allow-database-write` | Requires `--apply` too, or refuses (`"--allow-database-write requires --apply."`). |
| `--expected-fingerprint HASH` | Required for a real write to proceed - see §9. |
| `--skip-backup` | Isolated/temp DBs only, mirrors the migration script's own flag. |

**No write ever occurs merely because live network is enabled** - proven
directly: `test_dry_run_live_network_creates_zero_database_rows` fetches
real (mocked) content over "live network" with no apply flags and
asserts the target database file is byte-identical before and after.

## 3. Explicit database binding

`build_engine(database: Path)` is the **only** place a SQLAlchemy engine
is constructed in this module - it binds to exactly the resolved
`--database` path and is used for schema inspection, the persistence
session, post-write verification, and (indirectly, via the reused
migration script's own `backup_database()`) the backup source. The
module never imports `app.database.SessionLocal` or `app.database.engine`
- checked structurally by `test_module_never_imports_process_global_session_local`
(parses the module's actual `import`/`from...import` AST nodes, not its
prose, since the module's own docstring legitimately explains what it
does *not* import).

Given the prior wrong-DB incident this task references, isolation is
proven with a `protected.db`/`target.db` pair
(`test_wrong_db_isolation_protected_untouched_target_written`): apply
runs against `target.db` only; `protected.db` remains byte-for-byte
identical, and even monkeypatching `app.config.settings.database_url` to
point at a third, nonexistent path changes nothing - the runner never
consults it (`test_default_app_database_cannot_override_explicit_target`).

## 4. Provider / acquisition flow

Two distinct document-fetch paths, matching the write-safety split
exactly:

- **Dry-run / any mode's discovery step**: `discover_relevant_fragments()`
  calls `MACGranicusAcquisitionProvider.retrieve()` **directly** - pure
  HTTP, no `Session`, no database at all. `AcquisitionService.acquire()`
  is deliberately never called here, because it **always commits**
  internally (confirmed by reading `app/services/acquisition.py` before
  writing this runner) - calling it in dry-run mode would create real,
  durable `AcquisitionSource`/`AcquisitionRun`/`Snapshot` rows even
  without `--apply`. `snapshot_change_status_dry_run()` reports
  `NEW_DOCUMENT`/`UNCHANGED_DOCUMENT`/`CHANGED_DOCUMENT` via a read-only
  `SELECT` against `Snapshot`, never a write.
- **Apply**: `acquire_document_for_apply()` uses the real, unmodified
  `AcquisitionService`, bound to the caller's explicit session, exactly
  as `app/scripts/capture_faa_emas.py` already does for FAA.

Both paths reuse `discover_recent_meetings()`/`discover_agenda_items()`
unchanged - archive addressing (`view_id`/`clip_id`/`meta_id`), never a
search query, never a hardcoded document URL.

## 5. Candidate selection

`select_candidate_airports()` — deliberately not a blind scan of every
airport:

1. **Topology-overlap query** (primary, evidence-driven): a single,
   targeted `WHERE RunwayEnd.designation IN (...)` /
   `WHERE Runway.designation IN (...)` query against the fragment's own
   extracted, normalized runway tokens - genuinely bounded, not a full
   table scan in application code.
2. **`PILOT_SAFETY_CASE_SUPPLEMENTAL_CODES = ("SFO",)`** - a tiny,
   explicit, human-reviewed supplemental list, documented in the module
   itself as NOT evidence-derived and NOT provider identity. Included
   only so this pilot's own cross-airport safety property keeps being
   exercised even for a fragment whose real topology never overlaps with
   SFO's (which is exactly this fragment's real shape). **This list is
   pilot-scoped, not a generic selection mechanism** - it exists only to
   keep exercising this one, already-proven MSP/SFO safety case. It must
   not be generalized into a reusable "supplemental candidates" pattern
   or grown as new providers are added; a future provider covering a
   different authority/region needs its own explicit, separately-
   reviewed decision about whether it needs anything like this at all.

`KNOWN_ISSUER_REFERENCE = {"MSP": frozenset({"Metropolitan Airports
Commission"})}` supplies the guard's own `known_issuers` input when
building each `CandidateAirport` - the guard still requires the
fragment's own extracted issuer text to match before recording any
positive evidence; this reference never bypasses that.

**Final attachment always passes through the unmodified guard** -
candidate selection only decides who is asked, never who wins.

## 6. Enrichment

`evaluate_with_enrichment()` - two passes, never a hardcoded outcome:

1. Evaluate the raw fragment against all selected candidates. If
   **exactly one** reaches `ATTACH_CONFIRMED` (the guard's own
   determination, not an assumption), that candidate is the "home"
   airport.
2. Load the home airport's real canonical topology (read-only, from the
   target DB) and enrich the fragment via the already-committed
   `enrich_with_alternate_airport_topology()`, then re-evaluate.

If pass 1 does not produce exactly one confirmed candidate, nothing is
enriched - pass 1's result is final.

## 7. Dry-run planning

`plan_governed_persistence()` performs **only `SELECT`** queries against
`Source`/`SourceAssertion`, using their pre-existing columns
(`external_id`, `artifact_identity`, `source_locator`,
`raw_fragment_hash`) - it never references `identity_guard_decision`/
`identity_guard_reason`, so it works correctly whether or not the
discovery migration has been applied. It never calls
`session.add()`/`flush()`/`commit()`.

**Zero-mutation proof**: `test_dry_run_creates_zero_database_rows` and
`test_dry_run_live_network_creates_zero_database_rows` compare the full
target database file's SHA-256 before and after a dry run (fixture and
live-network respectively) and assert byte-identical - a stronger,
end-to-end equivalent of "session.new == 0, session.dirty == 0,
session.deleted == 0" that also catches any mutation path an
internal-session-state check might miss.

## 8. Fingerprint

`compute_plan_fingerprint()` hashes (SHA-256, over a sorted, JSON-encoded
tuple list) only the **upstream-content-derived** fields - document/
fragment identity, guard outcome, attached airport code, `Source`
external id - deliberately excluding target-database-state fields
(`would_be_created`, existing row ids). This makes the fingerprint:

- **Stable across repeated dry runs** while upstream content is
  unchanged (`test_fingerprint_stable_across_repeated_dry_runs`).
- **Independent of which database it is planned against**
  (`test_fingerprint_independent_of_target_database_state`).
- **Confirmed live**: the real live-network dry run (§16) against the
  actual real MAC document produced fingerprint
  `234b8667b537accf6ad4f8732c31895d15c82e7ab8698e6a4b6dab0b20d06f84` -
  byte-for-byte identical to every fixture-based dry run of the same
  real PDF content run during this task's own development, confirming
  the property live, not just in unit tests.

## 9. Schema migration gate

Before any real write is attempted, `run_capture()` checks
`schema_ready` (both `identity_guard_decision`/`identity_guard_reason`
present), computed via the already-committed, reused
`scripts.migrate_discovery_governed_evidence_slice1.inspect()` - never
reimplemented. If absent: `report["blockers"] =
["DISCOVERY_SCHEMA_MIGRATION_REQUIRED"]`, `applied = False`, session
rolled back, **no auto-upgrade**. Confirmed both against a hand-built,
genuinely pre-migration schema fixture and, live, against the real
production database (§16 - still unmigrated as of this task).

The fingerprint gate runs immediately after: `--expected-fingerprint`
must match the freshly recomputed plan fingerprint, or apply refuses
(`FINGERPRINT_MISMATCH`) - proven for both a deliberately wrong
fingerprint and an omitted one.

**Both gates are checked before the persistence loop ever runs** - an
earlier draft of this runner called `persist_discovery_fragment()`
inside the same loop that built the plan, before either gate; this was
caught and fixed during this task's own development, before any test was
written against it, so no version of the shipped code or its tests ever
exercised the unsafe ordering.

## 10. Disposable migration/apply rehearsal

Performed against **three separate disposable databases** in this task,
none of them the real one:

1. A synthetic disposable DB (`smoke_disposable.db`), migrated, seeded
   with MSP/SFO - full dry-run -> apply -> idempotent-second-apply cycle
   proven manually before the formal test suite was written.
2. A `protected.db`/`target.db` pair - wrong-DB isolation (§3).
3. **A disposable byte-for-byte copy of the real production database**
   (`real_disposable_rehearsal.db`), migrated via the real, unmodified
   `scripts/migrate_discovery_governed_evidence_slice1.py --allow-database-write`,
   then captured **live** (`--allow-live-network`) against the real MAC
   archive and applied (`--apply --allow-database-write
   --expected-fingerprint 234b8667...`).

## 11. Disposable rehearsal — exact persisted rows

Against `real_disposable_rehearsal.db` (a real copy of production data,
`source_assertions` pre-count 221):

| Field | Value |
|---|---|
| `source_id` | 70 (new) |
| `source_assertion_id` | 222 (new) |
| `airport_id` | 45 (MSP - the real production MSP row id) |
| `identity_guard_decision` | `ATTACH_CONFIRMED` |
| `identity_guard_reason` | *"2 independent positive evidence categories (issuer, runway_topology) agree on 'Minneapolis St. Paul International', none contradicted."* |
| `raw_relevant_text` (first line) | `PD&E 09/03/2024` (the real memo, verbatim) |
| `signals` count | 68 (unchanged from the real DB's own baseline) |

## 12. Idempotency

Re-running the exact same live-network apply command against the same
`real_disposable_rehearsal.db` a second time: `source_created: false`,
`source_assertion_created: false`, same ids (70/222), `source_assertions`
count unchanged at 222. Proven both live (this rehearsal) and via the
formal fixture-based test suite (`test_idempotent_second_apply_no_duplicates`).

## 13. No-Signal proof

Structural (`test_module_never_imports_signal_model` - the string
`"Signal"` never appears in the runner's source at all) and behavioral
(`test_no_signal_created`, and directly confirmed on the disposable
rehearsal DB: `signals` count 68 before and after). No matter how
investor-relevant the MSP memo's content is, nothing is ever persisted
as a `Signal` by this runner.

## 14. Wrong-DB isolation

See §3. All four required proofs hold:
`protected.db` byte-identical after a `target.db` apply; the backup file
(when taken) is a byte-for-byte pre-write copy of the **target**, not
any other database (`test_backup_corresponds_to_target_database`); every
post-write verification query in this document and its tests reads the
explicitly resolved target path; `app.config.settings.database_url`
cannot redirect a write.

## 15. Live MSP dry run

`python -m scripts.capture_mac_discovery --allow-live-network
--max-recent-meetings 3 --historical-meeting-clip-id 2349` against the
**real** `data/runway_safe.db` (no write flags):

| | |
|---|---|
| Source family | `metroairports.granicus.com` |
| Meetings inspected | 4 (3 real recent 2026 meetings + 1 explicitly-addressed historical PD&E meeting) |
| Agenda items inspected | 94 |
| Relevant | 1 |
| Ignored | 93 (zero false positives) |
| Document fetched | 1, HTTP 200, `application/pdf`, 1,085,250 bytes, `NEW_DOCUMENT` |
| MSP result | `ATTACH_CONFIRMED` (airport id 45, the real production id) |
| SFO result | `REJECT_CROSS_AIRPORT` (airport id 4, the real production id), reason grounded in `runway_topology='30L'`/`'12R'`/`'12R/30L'` |
| Plan fingerprint | `234b8667b537accf6ad4f8732c31895d15c82e7ab8698e6a4b6dab0b20d06f84` |
| Schema readiness | both discovery columns absent (unmigrated, as expected) |
| Applied | `false` |

## 16. Real DB unchanged proof

| | Before | After (this task's every real-DB-touching step) |
|---|---|---|
| Path | `data/runway_safe.db` | same |
| Size | 667648 | 667648 |
| mtime | `1787051393.6995218` | `1787051393.6995218` |
| `source_assertions` count | 221 | 221 |
| Discovery columns present | No | No |

Confirmed identical before the live dry run, after the live dry run, and
after the separate disposable-copy rehearsal (which only ever wrote to
`real_disposable_rehearsal.db`, a copy in the session scratchpad, never
`data/runway_safe.db` itself).

## 17. Exact future real migration requirement

```
python scripts/migrate_discovery_governed_evidence_slice1.py --database data/runway_safe.db --allow-database-write
```

(omit `--skip-backup` for the real run - a timestamped backup under
`data/backups/` is taken automatically first, exactly as this script
already requires). This is a **separate, explicitly-approved** future
step - not performed in this task.

## 18. Exact future real capture command

```
python -m scripts.capture_mac_discovery --database data/runway_safe.db \
  --allow-live-network --max-recent-meetings 3 --historical-meeting-clip-id 2349 \
  --apply --allow-database-write \
  --expected-fingerprint <fingerprint from a fresh dry run performed at approval time>
```

**The fingerprint must be recomputed via a fresh dry run immediately
before the real apply, not copied from this report** - if the MAC
archive's content changes between this task and the real capture (a new
meeting, a corrected memo), the fingerprint will legitimately differ,
and the stale one from this document will correctly be refused
(`FINGERPRINT_MISMATCH`) by design.

## 19. Recommendation

Both the real migration and the first real capture **appear ready to be
approved as separate, explicit next steps**:

- The migration script has been re-proven (again) via a real,
  production-data disposable copy in this task, not just synthetic
  fixtures.
- The capture runner's every safety gate (live-network, write, schema,
  fingerprint, wrong-DB isolation) has been proven against real
  production data in a disposable rehearsal, not only synthetic tests.
- The live dry run against the real, current MAC archive produces
  exactly the expected single relevant document, with zero false
  positives across 94 real items.
- No code path in this runner can write to the real database without
  all of: `--allow-live-network` (or a fixture), `--apply`,
  `--allow-database-write`, a migrated schema, and a matching, freshly-
  computed fingerprint - all explicit, all human-supplied.

Recommended order: (1) approve and run the real migration (§17); (2)
immediately re-run the live dry run against the now-migrated real
database to obtain a fresh fingerprint; (3) approve and run the real
capture (§18) with that fresh fingerprint. Both remain deliberately
outside this task's own scope.
