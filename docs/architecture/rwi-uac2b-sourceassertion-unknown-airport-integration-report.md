# RWI UAC2B — SourceAssertion ↔ Unknown Airport Candidate Integration

Implementation report. Slice 2 of
`docs/architecture/rwi-governed-new-airport-discovery-design.md`. Starting
checkpoint: HEAD `322827bc57ad04bac2e5bb1cd30d39cc4865f47c` (== origin/main),
real DB SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`
(1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
FK check `[]`, integrity `ok`, `unknown_airport_candidates`/
`unknown_airport_candidate_reviews` absent, `source_assertions` has no
`unknown_airport_candidate_id` column) — all verified fresh at the start
of this mission and confirmed unchanged at the end. **No migration was
run against the real database.**

## Files read fresh

`docs/architecture/rwi-governed-new-airport-discovery-design.md`,
`docs/architecture/rwi-uac1-unknown-airport-candidate-persistence-report.md`,
`docs/architecture/rwi-uac2a-unknown-airport-candidate-migration-report.md`,
`app/models/source_assertion.py`, `app/models/unknown_airport_candidate.py`,
`app/services/unknown_airport_candidate_persistence.py`,
`app/services/discovery_evidence_persistence.py`,
`app/services/evidence_attachment_guard.py`,
`scripts/migrate_evidence_identity_slice1.py` (the "safe table rebuild"
precedent for `source_assertions` specifically),
`scripts/migrate_signal_disposition_d4d2.py`,
`scripts/migrate_unknown_airport_candidates_uac2a.py`.

## Files modified

- `app/models/source_assertion.py` — additive `unknown_airport_candidate_id`
  column, mutual-exclusivity `CheckConstraint`, one-directional
  relationship (no back-populates, no change to
  `app/models/unknown_airport_candidate.py`).
- `app/services/discovery_evidence_persistence.py` — new function
  `persist_candidate_linked_source_assertion()`; additive
  `attached_unknown_airport_candidate_id` field (default `None`) on
  `DiscoveryPersistenceResult`; `persist_discovery_fragment()` itself is
  **entirely unmodified** — same signature, same logic, same behavior.
- `tests/test_model_contract.py` — extended (not weakened) for the new
  column/FK/index/relationship.
- `tests/test_discovery_evidence_persistence.py` — 14 new tests for the
  new function; all 16 pre-existing tests unmodified and still passing.
- `tests/test_unknown_airport_candidate_migration.py` (UAC2A's own test
  file — not in this mission's own "expected likely modified" list, but
  a genuine, necessary fix; see "Defects found"/"Corrections made"
  below) — `_pre_uac1_db()`'s fixture-building helper and one downstream
  test's own seed/read logic updated to account for `source_assertions`'
  new forward FK; no test's own assertions/behavior were weakened, only
  the fixture-construction mechanics changed.
- `tests/test_capture_mac_discovery.py` (the MAC Granicus capture-runner
  suite — likewise not in the "expected likely modified" list, likewise
  a genuine, necessary fix) — one test's own real-migration-script chain
  extended with the two new UAC2A/UAC2B migrations, following the exact
  precedent that test's own docstring already established for every
  prior additive `source_assertions` migration.

## Files created

- `scripts/migrate_source_assertion_unknown_airport_uac2b.py`
- `tests/test_source_assertion_unknown_airport_migration.py` (54 tests)
- This report.

## SourceAssertion model change

One nullable FK column, `unknown_airport_candidate_id -> unknown_airport_candidates.id`,
indexed, no `ON DELETE` override (defaults to SQLite's implicit
`NO ACTION`, matching every other FK on this model and every FK
elsewhere in the repository). One named `CheckConstraint`
(`ck_source_assertions_airport_candidate_mutually_exclusive`):
`NOT (airport_id IS NOT NULL AND unknown_airport_candidate_id IS NOT NULL)`.
One one-directional relationship
(`unknown_airport_candidate: Mapped[Optional["UnknownAirportCandidate"]] = relationship()`,
no `back_populates`), modeled directly on the existing
`ReviewerAction.duplicate_of_signal` precedent — `app/models/unknown_airport_candidate.py`
was **not** touched; "many SourceAssertions may point at the same
candidate" is proven by a plain query, not an ORM collection. No status
duplication, candidate-name snapshot, resolution state, or confidence
field was added — the mission's own explicit exclusion list.

## Mutual-exclusivity invariant

**Enforced at the DATABASE level**, not merely by application discipline
— proven by direct ORM construction bypassing both persistence functions
entirely (`test_dual_identity_rejected_at_db_layer_direct_orm_construction`
and its migration-level counterpart
`test_dual_identity_rejected_post_migration`). The truth table is
explicitly tested, all three valid states plus the one forbidden state:

| `airport_id` | `unknown_airport_candidate_id` | Result |
|---|---|---|
| set | NULL | allowed (known airport — unchanged pre-existing behavior) |
| NULL | set | allowed (new — governed candidate link) |
| NULL | NULL | allowed (unresolved — pre-existing behavior, unchanged) |
| set | set | **rejected** by CHECK, both at the ORM layer and via raw SQL bypassing the ORM |

Not an XOR: both-NULL is explicitly proven still valid
(`test_unresolved_state_both_null_still_allowed`) — the design's own
locked contract, restated verbatim from the mission brief.

## FK/delete behavior

No `ondelete=` override anywhere — SQLite's default (no `ON DELETE`
clause at all) applies, meaning a referenced `UnknownAirportCandidate`
cannot be deleted while any `SourceAssertion` still points at it with FK
enforcement on. Proven directly:
`test_deleting_referenced_unknown_airport_candidate_is_blocked_by_fk`
seeds a candidate-linked assertion, attempts to delete the candidate, and
confirms `IntegrityError`. This matches every other FK in this model
(`airport_id`, `runway_id`, `signal_id`) and the mission's own explicit
preference for `NO ACTION`/`RESTRICT`-style behavior over `CASCADE`.

## Discovery persistence API change

Smallest safe addition: `persist_candidate_linked_source_assertion(session,
source_metadata, fragment, *, unknown_airport_candidate_id: int) ->
DiscoveryPersistenceResult`, added to `discovery_evidence_persistence.py`
(reusing its existing `_get_or_create_source()`/`_get_existing_assertion()`
private helpers unchanged — no duplication). Validates only that
`unknown_airport_candidate_id` refers to an existing, already-persisted
`UnknownAirportCandidate` (`session.get()` check, `ValueError` otherwise)
— performs **no** lookup by raw name or fingerprint, **no** candidate
creation, and **no** automatic canonical match of any kind. The target
candidate must already exist, found-or-created separately by
`find_or_create_unknown_airport_candidate()` (UAC1), before this function
is ever called. Sets `airport_id=NULL`,
`unknown_airport_candidate_id=<given id>`, and reuses the existing
`AttachmentOutcome.INSUFFICIENT_IDENTITY` value for
`identity_guard_decision` — deliberately not a new sixth guard outcome
(see the function's own module-level comment for the reasoning: the
candidate link is an orthogonal fact, recorded via the separate column,
never folded into guard vocabulary). If the identical fragment identity
was already resolved to a known Airport by an earlier
`persist_discovery_fragment()` call, this function returns that existing
row **exactly as-is** — never rewrites an already-resolved identity,
proven by `test_already_airport_linked_assertion_is_never_rewritten_to_candidate_linked`.

## Known-airport backward compatibility

**`persist_discovery_fragment()` is byte-for-byte unmodified** — same
signature, same body, same tests. All 16 pre-existing
`test_discovery_evidence_persistence.py` tests pass unchanged. No caller
using only `airport_id` requires any change; the new
`attached_unknown_airport_candidate_id` field on `DiscoveryPersistenceResult`
has a default of `None`, so every existing construction/comparison of
that dataclass remains valid.

## Candidate-linked evidence result

Core UAC2B capability, proven end to end in
`test_candidate_linked_evidence_persists_with_airport_id_null` and its
migrated-schema counterpart
`test_candidate_linked_evidence_persists_post_migration`: candidate
created (UAC1) → `SourceAssertion` persisted via the new function →
`airport_id` NULL, `unknown_airport_candidate_id` set → original evidence
text preserved verbatim → zero canonical `Airport` rows created →
candidate itself remains non-canonical (`resolved_airport_id` still
`None`) → the row is fully queryable/auditable after commit.

## Multiple-evidence convergence result

Proven: three independent `SourceAssertion` rows, each with its own
distinct evidence text, all link to the same `UnknownAirportCandidate`
(`test_multiple_source_assertions_can_link_to_the_same_candidate` and its
migrated-schema counterpart
`test_multiple_assertions_share_one_candidate_post_migration`). No
overwrite, no one-to-one restriction — exactly the many-`SourceAssertion`-
to-one-`UnknownAirportCandidate` cardinality the design requires. No join
table is needed for this direction: `unknown_airport_candidate_id` lives
on the "many" side (`SourceAssertion`), the same forward-FK-on-the-many-
side pattern already established by `SourceAssertion.signal_id` (many
assertions may point at one `Signal`) and `Signal.installation_id` — a
`SourceAssertion` can structurally only ever hold one FK value in this
column, so "at most one candidate per assertion" is the schema's default
shape, not something requiring extra enforcement.

## Candidate immutability result

Confirmed unaffected: linking new `SourceAssertion` rows never touches
the candidate's own claim fields
(`test_candidate_linking_does_not_mutate_candidate_claim_fields` — before/
after comparison of `raw_name`/`raw_city`/`raw_country`/
`candidate_fingerprint`). This is structurally guaranteed, not merely
observed: `persist_candidate_linked_source_assertion()` never constructs
an `UnknownAirportCandidate` object or assigns to any of its attributes
anywhere in its own source (confirmed by the same AST-based
no-canonical-construction proof pattern already established for
`persist_discovery_fragment()`), and UAC1's own field-level immutability
`before_update` guard remains the backstop even if it somehow did.

## Read-path audit

Full repository search (`app/`, `scripts/`, tests excluded) for every
read of `SourceAssertion.airport_id`. Key structural fact that resolves
most sites: candidate-linked rows always get
`identity_guard_decision = "INSUFFICIENT_IDENTITY"`, and the entire
governed downstream pipeline (`intelligence_review_persistence.py` →
`promotion_policy_persistence.py` → `human_review_queue.py` →
`governed_signal_creation.py` → `existing_signal_reconciliation_candidates.py`)
fails closed unless `identity_guard_decision == "ATTACH_CONFIRMED"` —
checked before any `airport_id` read in that chain — so candidate-linked
rows are structurally excluded from ever reaching those `airport_id`
reads.

**UNCHANGED_SAFE** (12 sites — `airport_id IS NULL` already meant
"unresolved," and the new possible cause doesn't change what these sites
do with that fact):
- `fleet_health_check.py:289` (FH-D2 input) — scoped to `signal_id IS NOT NULL`, which a candidate-linked row can never have.
- `fleet_health_rules.py:504-520` (`evaluate_fh_d2`) — already explicitly skips `airport_id IS None`.
- `existing_signal_reconciliation_candidates.py:213,292-293` — only reached after the `ATTACH_CONFIRMED` gate.
- `discovery_evidence_persistence.py:282,385` — this module's own idempotent-reuse `attached_airport_id` field; it also correctly returns `attached_unknown_airport_candidate_id` alongside it.
- `governed_signal_creation.py:391-392,450,520` — unreachable for candidate-linked rows (gated at line 375).
- `human_review_queue.py:294,299-301,342` — scoped to `promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"`, itself only ever computed for `ATTACH_CONFIRMED` rows.
- `scripts/apply_mdw_current_presence_pilot.py:31`, `scripts/apply_cgf_physical_installation_pilot.py:82`, `scripts/analyze_nasr_emas_runway_end_resolution.py:183,189,257-259`, `scripts/reconcile_bos_orh_emas_identities.py:171,173` — all scoped to `assertion_type == "runway_end"` or hardcoded legacy ids; candidate-linked rows are always `assertion_type == "project_construction"`.
- `app/static_export/build.py:368,825` — keyed off a specific `Airport.id`'s own reverse relationship; a candidate-linked row (`airport_id NULL`) structurally cannot appear in it.

**DEFER_TO_UAC3** (1 site — a real, pre-existing gap, not introduced by
UAC2B, that becomes actionable only once a future slice starts creating
real candidate-linked rows in production):
- `fleet_health_check.py:571` (`_build_source_assertion_review_states`) and its consumers `fleet_health_review_rules.py:178-187,607-654` (`evaluate_fh_f2`/`evaluate_fh_f3`) — the only place in the repository that queries `airport_id IS NULL` across **all** `SourceAssertion` rows with no companion check on `identity_guard_decision` or the new `unknown_airport_candidate_id` column. `evaluate_fh_f3` would flag a candidate-linked row whose `review_state` later becomes `"reviewed"` as `REVIEW_REQUIRED` with wording implying raw, un-triaged evidence ("no attributed airport ... candidate for human review") — misleading once such a row is genuinely already routed into UAC governance, though not factually false (it does still lack a canonical `Airport`). **Not fixed in this mission**: no real candidate-linked row can exist yet (nothing in production calls the new function outside tests, per this mission's own explicit stop boundary), Fleet Health files are outside this mission's file scope, and "do not fix every caller speculatively" (mission §23) governs. Recommended as an explicit, named UAC3-or-later follow-up: teach `evaluate_fh_f2`/`evaluate_fh_f3` to also check `unknown_airport_candidate_id IS NULL` before classifying a row as raw/unresolved.

**UAC2B_UPDATED**: none beyond the two files listed in "Files modified"
above — no other caller needed updating, since every other airport_id
reader is structurally shielded from candidate-linked rows by an
existing, unrelated gate (the `ATTACH_CONFIRMED` chain or an
`assertion_type`/hardcoded-id filter).

## Information-firewall verdict

**Sound.** No code anywhere converts `unknown_airport_candidate_id` into
`airport_id`, automatically or otherwise — grep-confirmed across the new
migration, the new service function, and the modified model. No Airport,
Runway, RunwayEnd, or Signal is ever created by anything touched in this
mission (proven by the same `TestNoCanonicalSideEffects`-style tests
already established for UAC1, re-run against candidate-linked evidence
specifically:
`test_candidate_linked_evidence_creates_no_canonical_rows`/
`test_no_canonical_side_effects_post_migration`). No promotion-policy
code accepts candidate identity as canonical — candidate-linked rows'
`identity_guard_decision` is always `INSUFFICIENT_IDENTITY`, which
`promotion_policy_persistence.py`/`intelligence_review_persistence.py`
already treat as "does not qualify" (unchanged, unmodified code, verified
by the `ATTACH_CONFIRMED`-gate read-path audit above).

## Migration shape

`scripts/migrate_source_assertion_unknown_airport_uac2b.py`: same
`inspect()`/`backup_database()`/`upgrade()`/`downgrade()`/`main()`/typed-
exception API shape as `migrate_unknown_airport_candidates_uac2a.py`,
adapted for a **table rebuild** (SQLite has no `ALTER TABLE ADD CONSTRAINT`
— a table-level CHECK constraint cannot be added to an existing table any
other way). The rebuild pattern (create replacement table, copy rows,
drop original, rename) is the same one
`scripts/migrate_evidence_identity_slice1.py` already established for
this exact table, refined with the strict schema-parity/partial-schema/
incompatible-schema/atomicity discipline from
`migrate_signal_disposition_d4d2.py` and
`migrate_unknown_airport_candidates_uac2a.py`. The upgrade-direction
replacement table is compiled **fresh** from
`Base.metadata.tables["source_assertions"]` (never hand-typed) — every
pre-existing column/CHECK/UNIQUE constraint is carried over automatically
alongside the new column/CHECK. The downgrade direction necessarily uses
a **hand-written, frozen snapshot** of the exact pre-UAC2B column list
(`_PRE_UAC2B_COLUMNS`) — the live ORM metadata now includes the new
column, so it cannot be used to reconstruct the pre-UAC2B shape for the
reverse rebuild; this snapshot is verified correct by dedicated tests
(`TestPreUac2bSnapshotSanity`) rather than merely trusted.

## UAC2A dependency verdict

**Enforced, checked before any write connection to the target database is
even opened.** `upgrade()` calls `uac2a_migration.inspect(database)["ready"]`
first (reusing UAC2A's own migration module directly, never
reimplementing its schema-comparison logic) and raises
`Uac2aNotAppliedError` if UAC2A is missing or schema-incompatible —
proven for both "UAC2A tables entirely absent" and "UAC2A partially/
incompatibly applied." Never auto-creates or repairs the UAC2A schema.

## Upgrade preservation proof

A representative populated `source_assertions` table (seeded via raw SQL
against the genuinely pre-UAC2B schema, since the ORM model already
declares the new column unconditionally and cannot be used to construct
pre-migration rows) is snapshotted field-for-field before `upgrade()` and
compared after — identical. The new column is confirmed `NULL` for every
pre-existing row (zero backfill). Every pre-existing named constraint and
index is confirmed present after the rebuild
(`test_upgrade_preserves_every_pre_existing_named_constraint`/
`test_upgrade_preserves_every_pre_existing_index`).

## Downgrade policy

Refuses outright, atomically, if **any** row carries a non-NULL
`unknown_airport_candidate_id` (proven, plus refusal-atomicity proof via
full `inspect()` equality before/after the refused call). A safe no-op
if the column is already absent. When every link is confirmed NULL,
rebuilds cleanly back to the exact pre-UAC2B column set, preserving every
row and every pre-existing constraint. A full upgrade→downgrade→upgrade
round trip ends `ready: True`.

## Partial/incompatible schema behavior

Fails closed for: column added without the CHECK constraint (via raw
`ALTER TABLE ADD COLUMN`, both with and without an inline FK reference),
UAC2A missing entirely, UAC2A present but schema-incompatible, and
`source_assertions` itself missing. No case silently repairs or rebuilds
an ambiguous existing shape — a human must resolve any collision by hand,
matching the D4D2/UAC2A precedent's own discipline.

## Raw SQL constraint result

Against a genuinely migrated schema, `PRAGMA foreign_keys=ON`: `airport_id`
only accepted; `unknown_airport_candidate_id` only accepted; both NULL
accepted; both non-NULL rejected by the CHECK (specific
`sqlite3.IntegrityError` with `"CHECK constraint failed"` match, never a
broad `Exception`); nonexistent `unknown_airport_candidate_id` rejected
by its FK; nonexistent `airport_id` still rejected by its own
pre-existing FK; and — the explicit case the mission's own §21 named —
**both IDs individually valid but both non-NULL still rejected by the
CHECK**, proving the constraint fires independent of whether either
target actually exists.

## Model/migration parity

Every `TestModelMigrationParity` test builds its database via
`uac2b_migration.upgrade()` (itself built on `uac2a_migration.upgrade()`)
only — never `Base.metadata.create_all()` for either new schema piece.
Confirmed against the genuinely migrated schema: known-airport evidence
persists unchanged, candidate-linked evidence persists correctly, dual
identity is rejected, multiple assertions share one candidate, and no
canonical side effects occur anywhere.

## Defects found

**None in the production code.** One genuine, real, cross-file test
regression was found and fixed (see below) — not a defect in the model
change or either migration, but an unavoidable consequence of the model
change on a fixture helper in an already-committed, already-reviewed
sibling test file that this mission does not otherwise touch.

## Corrections made

1. Two test-authoring fixes localized to this mission's own new test
   file: `test_representative_populated_source_assertions_preserved_field_for_field`
   in `tests/test_source_assertion_unknown_airport_migration.py`
   originally tried to seed pre-migration data via the ORM
   `SourceAssertion` constructor, which fails against a database still in
   the pre-UAC2B schema because the ORM model already declares
   `unknown_airport_candidate_id` unconditionally. Corrected to seed via
   raw SQL against the genuine pre-UAC2B column set instead — the
   realistic "real production data, not yet migrated" scenario the test
   is meant to represent.

2. **A genuine, real regression in `tests/test_unknown_airport_candidate_migration.py`
   (UAC2A's own, already-reviewed-and-committed test file), caused by
   this mission's own model change, found and fixed.** That file's
   `_pre_uac1_db()` fixture helper builds "every table except UAC1's own
   two" by copying each other table via `Table.to_metadata()` into a
   separate `MetaData`. Once `source_assertions` gained a forward FK to
   `unknown_airport_candidates` (this mission's own change), that copy
   started producing a `MetaData` where `source_assertions`' own FK
   target was never copied — `create_all()` then failed with
   `NoReferencedTableError` from SQLAlchemy's own DDL table-sorting logic,
   breaking 61 of that file's 69 tests (reproduced directly, not merely
   theorized). Fixed by rebuilding `_pre_uac1_db()` to build the full
   current schema first, then use
   `migrate_source_assertion_unknown_airport_uac2b`'s own frozen
   pre-UAC2B `source_assertions` snapshot to rebuild that one table back
   to its pre-UAC2B shape — the same technique this mission's own new
   migration test file already uses for its own "neither UAC2A nor
   UAC2B applied" starting state. One downstream test in that same file
   (`test_representative_domain_rows_unchanged_after_upgrade`) had the
   identical ORM-vs-pre-UAC2B-schema mismatch for its own `SourceAssertion`
   seed/read and was fixed the same way (raw SQL). `test_signal_disposition_migration.py`'s
   own analogous fixture was checked and confirmed **unaffected** — its
   exclusion set (`signal_dispositions`/`signal_disposition_members`)
   has no relationship to `unknown_airport_candidates`, so
   `source_assertions` copies into that fixture's `MetaData` cleanly.

3. **A second genuine, real regression, same root cause, in
   `tests/test_capture_mac_discovery.py`** — the MAC Granicus discovery
   capture-runner test suite. Its own
   `test_apply_succeeds_after_running_the_real_migration_script` runs a
   real chain of migration scripts (Slice 1/4/7/9C) against a synthetic
   "unmigrated" database, then calls the real capture runner (which
   itself calls `persist_discovery_fragment()`). That test's own
   docstring already documented the exact precedent for this failure
   mode before UAC2B existed ("the ORM model ... now also declares
   [prior slices'] columns unconditionally, so any SELECT against
   SourceAssertion ... requires the physical table to carry all
   [of them] too") — UAC2B's new column is the identical case one slice
   further. Fixed by adding `migrate_unknown_airport_candidates_uac2a.upgrade()`
   and `migrate_source_assertion_unknown_airport_uac2b.upgrade()` to that
   test's own existing migration chain (UAC2A before UAC2B, since UAC2B
   depends on it) and updating its docstring from "ALL FOUR" to "ALL SIX"
   additive migrations — following the exact pattern that test's own
   authors already established for this exact class of change.

## Focused tests

`tests/test_source_assertion_unknown_airport_migration.py`: **54 passed**,
0 failed. `tests/test_discovery_evidence_persistence.py`: **30 passed**
(16 pre-existing + 14 new), 0 failed. `tests/test_model_contract.py`:
**5 passed**, 0 failed. See the final chat report for the combined
broader-suite and full-pytest counts.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both run clean; see the final chat report.

## Real DB before/after proof

Unchanged throughout this mission: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25 — verified
fresh both before and after. `unknown_airport_candidates`/
`unknown_airport_candidate_reviews` confirmed **absent**;
`source_assertions` confirmed to still have **no**
`unknown_airport_candidate_id` column — neither UAC2A nor UAC2B was ever
applied to the real database. `TestNoRealDatabaseAccess` additionally
proves, via AST, that the only string literal in the migration module
naming the real database file is the single, legitimate `DEFAULT_DATABASE`
constant.

---

# Critical review addendum

Adversarial review performed against fresh reads of the actual diff and
both migrations, plus direct empirical attacks (not merely re-reading the
report's own claims). Zero production defects were found; three genuine
test-coverage gaps (all explicitly named in this mission's own attack
list) were closed.

## Domain-invariant verdict

**Sound, and now attacked through every named vector.** The truth table
was already covered by INSERT-path tests; this review added the missing
**UPDATE-path** attacks the mission's own §3/§10 specifically demanded:
a raw-SQL `UPDATE` (via SQLAlchemy Core `text()`, bypassing the ORM's own
attribute path) moving a valid, committed known-airport row to also carry
a candidate link is rejected with `CHECK constraint failed`
(`test_dual_identity_rejected_on_update_known_to_both_raw_sql`); the
identical attack via plain ORM attribute assignment after commit, in both
directions (known→both, candidate→both), is likewise rejected
(`test_dual_identity_rejected_on_update_known_to_both_via_orm`/
`test_dual_identity_rejected_on_update_candidate_to_both_via_orm`). No
code path "prefers one silently" — every attempted dual state raises
before any row is persisted.

## SourceAssertion model verdict

**Confirmed exactly as claimed by direct schema inspection**, not
re-trusting the report: exactly one new nullable FK column, correct
target (`unknown_airport_candidates.id`), no `ON DELETE` override,
correctly indexed (`PRAGMA index_list`/`index_info` confirm a plain
single-column `ix_source_assertions_unknown_airport_candidate_id`, no
extra unrequested indexes). No status duplication, name snapshot,
resolution state, or confidence field exists on `SourceAssertion` for
this addition.

## Relationship/FK/delete verdict

**Sound.** The new `unknown_airport_candidate` relationship carries no
`back_populates` and creates no reciprocal collection on
`UnknownAirportCandidate` — confirmed by re-reading both model files
fresh; setting it does not implicitly mutate anything on the candidate
side. Deleting a referenced candidate while a `SourceAssertion` still
points at it is blocked (`IntegrityError`, FK enforcement) — re-verified
directly. Deleting a `SourceAssertion` itself has no cascade to either
`Airport` or `UnknownAirportCandidate` (no such cascade exists on either
FK). Existing `airport`/`runway`/`signal` relationships are byte-for-byte
unchanged in the diff.

## Persistence-API verdict

**Sound.** Fresh re-read of `persist_candidate_linked_source_assertion()`
confirms: validates only that the given `unknown_airport_candidate_id`
exists (`ValueError` otherwise); never queries by raw name or
fingerprint; never constructs an `UnknownAirportCandidate`; never
commits (`session.flush()` only, caller owns the transaction, matching
every sibling service); reuses `_get_or_create_source()`/
`_get_existing_assertion()` unchanged, so idempotency and Source-reuse
semantics are identical to the known-airport path.

## Known-airport compatibility verdict

**Confirmed unchanged by direct diff inspection**:
`persist_discovery_fragment()`'s own body has zero lines changed in this
mission — the only change touching it at all is the new, default-`None`
`attached_unknown_airport_candidate_id` field on the dataclass it
returns. All 16 pre-existing tests for it pass unmodified.

## Candidate-linked evidence verdict

**Sound**, re-verified end to end: `airport_id` NULL, candidate id set,
evidence text preserved verbatim, zero `Airport`/`Runway`/`RunwayEnd`/
`Signal` rows created, no `ReviewerAction` created (this function never
touches that table), row independently queryable post-commit.

## Multi-evidence convergence verdict

**Sound**, re-verified: 3 independent assertions to 1 candidate, no
overwrite; UAC1's own exact-fingerprint convergence (re-discovering the
identical claim) reuses the same candidate row without disturbing
already-linked evidence, since `find_or_create_unknown_airport_candidate()`
never touches `SourceAssertion` at all.

## Dual-identity bypass verdict

**Sound — every named vector fails closed, INSERT and UPDATE alike**,
per the domain-invariant verdict above. Three new regression tests close
the UPDATE-path gap this review's own attack found untested (the
underlying behavior was already correct — SQLite `CHECK` constraints
apply to `UPDATE` by default, no separate enforcement was needed — but
it was unproven before this review).

## Migration schema-parity verdict

**Confirmed via a genuinely independent path**, not merely
`uac2b_migration.inspect()`: a fresh synthetic DB was migrated
baseline→UAC2A→UAC2B and its real `sqlite_master`-stored `CREATE TABLE`
text and `PRAGMA table_info`/`foreign_key_list`/`index_list` were read
directly. The delta is exactly: `+unknown_airport_candidate_id` column,
`+` its FK, `+` its index, `+` the named mutual-exclusivity `CHECK` —
every one of the six pre-existing named constraints
(`ck_source_assertions_type`, `ck_source_assertions_evidence_quality`,
`ck_source_assertions_review_state`, `ck_source_assertions_record_identity`,
`uq_source_assertions_source_record`, `uq_source_assertions_locator_fragment`)
and all five pre-existing indexes are present unchanged.

## Migration preservation verdict

**Sound**, re-confirmed field-for-field (not row-count-only) via the
existing `TestExistingDataPreservation` test, itself re-read fresh: two
real rows seeded via raw SQL (since the ORM cannot construct rows
against the genuinely pre-UAC2B schema), snapshotted, migrated, and
compared identical; the new column confirmed `NULL` on both.

## UAC2A dependency verdict

**Sound, and deliberately requires BOTH UAC2A tables, not just
`unknown_airport_candidates` — confirmed intentional, not
over-requiring.** UAC2B's own FK target is only `unknown_airport_candidates.id`,
but `uac2b_migration.upgrade()` delegates its readiness check entirely to
`uac2a_migration.inspect(database)["ready"]`, which is UAC2A's own,
already-reviewed, atomic readiness contract (both of its own tables
correct together, per that migration's own committed design — a single
slice, not independently completable halves). Re-using that contract
whole, rather than hand-checking only `unknown_airport_candidates` here,
is the correct choice: it avoids UAC2B silently accepting a state UAC2A
itself would never call "ready" (e.g. `unknown_airport_candidates`
correct but `unknown_airport_candidate_reviews` missing/incompatible),
and avoids duplicating UAC2A's own schema-comparison logic a second
time. Verified for cases A (both absent) and B (candidate table present,
incompatible) via existing tests, both correctly refused before UAC2A's
own `inspect()` is even asked about column-level detail.

## inspect() trustworthiness verdict

**Sound**, re-verified against the review's own attack list: new column
present with no FK, FK wrong target, index missing (this review's own
prior addition), `CHECK` absent, extra column, wrong type/nullability —
all produce `matches_expected_schema: False`/`ready: False` and are
independently confirmed to make `upgrade()` raise
`IncompatibleExistingSchemaError` for the identical case, via the shared
`_schema_mismatch_reasons()` function both call.

## Rebuild-atomicity verdict — genuinely proven, not merely re-asserted

**The two pre-existing atomicity tests only proved "failure before any
DDL statement runs" (the whole rebuild function replaced with an
immediate raise) — this review found that gap and closed it.** Two new
tests perform the REAL `CREATE`/`INSERT ... SELECT`/`DROP TABLE`
statements (the actual compiled DDL, not a mock), then crash
deliberately **after the real `DROP TABLE` but before the `RENAME`** —
the single riskiest point in the whole sequence, since at that instant
the original table has genuinely been dropped and no replacement yet
bears its name. Both prove, via a completely fresh, unpatched
`sqlite3.connect()` inspection afterward, that SQLite's own transactional
DDL rollback undoes the DROP (the original table, and the seeded row
inside it, are both back exactly as they were) and leaves no orphaned
`__uac2b_new`/`__uac2b_downgrade` replacement table behind. This is now
real, empirical proof of the guarantee the whole rebuild design depends
on, for both the upgrade and downgrade directions.

## Downgrade verdict

**Sound**, re-confirmed: refuses atomically for one linked row, many
linked rows, and mixed known/candidate/unresolved row sets (the
`WHERE unknown_airport_candidate_id IS NOT NULL` count is nonzero in
every mixed case regardless of how many other rows are in other states);
succeeds and fully restores the pre-UAC2B column set when every link is
NULL.

## Raw-SQL constraint verdict

**Sound**, re-confirmed for every named case including the explicit
"both valid ids still rejected by CHECK" case, now supplemented by the
UPDATE-path attacks above (the mission's own §17 explicitly asked for
UPDATE paths "not only INSERT" — closed).

## Index/query verdict

**Sound**, confirmed via direct `PRAGMA index_list`/`index_info`: exactly
one new index, on exactly the new column, no extras added without
evidence of need.

## FH-F2/FH-F3 verdict — SAFE_TO_DEFER_TO_UAC3, precision improved over the original report

Fresh, direct inspection of `fleet_health_check.py:563-578`
(`_build_source_assertion_review_states`) and
`fleet_health_review_rules.py:178-188,607-655` (`SourceAssertionReviewStateFact`,
`evaluate_fh_f2`, `evaluate_fh_f3`), independent of the original report's
own characterization:

- `_build_source_assertion_review_states()` queries **every**
  `airport_id IS NULL` row with no companion check on
  `unknown_airport_candidate_id` or any governance-decision column —
  confirmed by direct source read.
- **FH-F2 fires for `review_state == "unreviewed"`** — and
  `persist_candidate_linked_source_assertion()` always sets
  `review_state = "unreviewed"` on creation (confirmed from source). This
  means a real candidate-linked row, the moment one exists, would
  immediately trigger FH-F2's `INFORMATIONAL` finding with the summary
  text "pending identity-guard processing, not a defect" — **factually
  imprecise** for such a row: the identity guard did not skip it: it ran,
  correctly found no known-airport match, and the row was deliberately
  linked to a governed candidate as the designed outcome, not left
  pending. This is a more immediate exposure than the original report's
  own framing (which focused only on FH-F3's later `"reviewed"` case) —
  corrected here.
- **FH-F3 fires for `review_state == "reviewed"`** with classification
  `REVIEW_REQUIRED` (an elevated, actionable severity, not merely
  informational) and summary text implying raw, un-triaged evidence
  ("candidate for human review") — misleading for a row already under a
  separate, already-governed UAC candidate-review workflow, though not
  factually false (it genuinely still lacks a canonical `Airport`).
- Neither rule uses `ERROR`-level classification; F2 is `INFORMATIONAL`,
  F3 is `REVIEW_REQUIRED` — misleading, never a false `ERROR`.
- **No current production code path creates a real candidate-linked row
  today** — confirmed structurally: `persist_candidate_linked_source_assertion()`
  is called nowhere outside test files in this entire repository (grep-
  confirmed), no candidate-selection integration exists yet (explicitly
  out of scope through UAC3), and the real database has neither UAC2A
  nor UAC2B applied. The gap is therefore **100% latent** in the actual
  running system today.

**Verdict: `SAFE_TO_DEFER_TO_UAC3`.** Not a blocking UAC2B defect — it
cannot fire against any real row that exists today, it never causes data
loss, corruption, or a false `ERROR`, and fixing it now would require
touching Fleet Health files that are outside this mission's own file
scope, which is exactly the "speculative mass refactor" §20 forbids.

**Exact expected UAC3 correction** (recorded here so it isn't lost):
extend `SourceAssertionReviewStateFact` with an
`unknown_airport_candidate_id: Optional[int]` field; have
`_build_source_assertion_review_states()` select it alongside
`review_state`; have `evaluate_fh_f2`/`evaluate_fh_f3` skip (or route to
a distinct, UAC-aware rule/summary) any fact where it is non-`None` —
distinguishing "raw, truly unresolved evidence" from "governed evidence
already linked to an `UnknownAirportCandidate`, awaiting its own separate
MATCH_EXISTING_AIRPORT/CREATE_NEW_AIRPORT/REJECT_CANDIDATE/DEFER
resolution." Expected new test: a `SourceAssertionReviewStateFact` with
`unknown_airport_candidate_id` set must not trigger FH-F2 or FH-F3 (or
must trigger a differently-worded, UAC-aware finding), while an otherwise
identical fact with it `None` continues to trigger exactly as today.

## Other read-path audit verdict

**The original report's count is confirmed correct upon fresh
re-inspection**, with the one refinement above (FH-F2's own exposure is
now stated more precisely, not merely FH-F3's): 12 sites `UNCHANGED_SAFE`,
0 sites requiring a `UAC2B_UPDATED` change beyond the two files already
modified, 1 site `DEFER_TO_UAC3`. No speculative mass refactor performed.

## Information-firewall verdict

**Sound.** Fresh repository-wide grep for any
`if ... unknown_airport_candidate_id ... :` conditional found exactly
one production match — the existence-validation check in
`persist_candidate_linked_source_assertion()` itself
(`if session.get(UnknownAirportCandidate, unknown_airport_candidate_id) is None`)
— which raises `ValueError`, never converts anything to canonical
identity. No `Airport`/`Runway`/`Signal` auto-creation exists anywhere
touched by this mission.

## Candidate-immutability verdict

**Sound**, re-confirmed by direct before/after field comparison after
attaching evidence, and independently re-confirmed that
`resolved_airport_id` is untouched by anything in UAC2B — grep-confirmed
zero writes to it in `discovery_evidence_persistence.py` or the new
migration.

## Model-contract verdict

**Sound.** Diff re-read fresh: purely additive (one new column entry, one
new FK entry, one new index entry, one new relationship entry); every
pre-existing `source_assertions` assertion in the file is untouched, not
weakened. `configure_mappers()`/fresh `create_all()` smoke re-run clean
(all 5 tests in `test_model_contract.py` pass).

## Cross-file fixture-fix verdict

**Both fixes are genuinely required, correctly scoped, and preserve each
test's original property** — re-confirmed by tracing exactly why each
broke (SQLAlchemy's `NoReferencedTableError` during DDL table-sorting for
`test_unknown_airport_candidate_migration.py`'s `_pre_uac1_db()`, and a
plain `OperationalError` from the ORM declaring a column the physical
pre-UAC2B/pre-migration-chain table doesn't yet have in
`test_capture_mac_discovery.py`). Neither fix silently applies UAC2B
where a test meant to represent a pre-UAC2B state — both rebuild
`source_assertions` back to its genuine pre-UAC2B shape (the migration
test) or add the real UAC2A+UAC2B migration calls to an already-real
migration chain (the capture-runner test), matching that test's own
pre-existing, self-documented precedent for every prior additive
`source_assertions` slice. Neither masks a migration-dependency bug —
both still exercise the real migration scripts, just now including
UAC2A/UAC2B where the ORM's own unconditional schema declaration
requires it. Fixture layering across this growing `source_assertions`
migration chain is genuinely becoming more elaborate (six additive
migrations now touch its shape); this is reported as a legitimate
observation for future migration authors, not redesigned here — no
correctness issue exists today, and premature redesign would exceed this
mission's own scope.

## Transaction/failure-atomicity verdict

**Sound**, re-confirmed: `persist_candidate_linked_source_assertion()`
validates before any `session.add()`, never commits, and an uncommitted
call is fully discarded on `session.rollback()` (implicitly proven by
every test in this file, none of which would show data if this were
false, since none call `session.commit()` before their own assertions in
the negative-path tests).

## Source-neutral/international verdict

**Sound.** Fresh grep of all three new/changed production files for
source-specific terms (MAC, Granicus, USAspending, n8n, any LLM/model
name) found only pre-existing comments describing *other, unrelated*
importers (NASR/USAspending/FAA ingestion) that explicitly never
populate these columns — no new dependency introduced. UAC1's own
pre-existing Unicode fixture coverage (re-used by this mission's
candidate-linked tests) already proves non-English claims work correctly
end to end.

## Test-quality verdict

Read all 56 UAC2B migration tests and all 33 discovery-persistence tests
against the review's own checklist. Found and closed: no UPDATE-path
dual-identity attack (closed, 3 new tests), rebuild atomicity was
mock-only at the whole-function level (closed, 2 new tests using real
mid-sequence DDL injection). Confirmed already present and sound: no
broad `except Exception`/`pytest.raises(Exception)` anywhere (every
raw-SQL test names the specific `IntegrityError` substring); migration
parity tests build exclusively via `uac2b_migration.upgrade()`, never
`create_all()` (grep-confirmed); multi-evidence convergence is tested;
direct ORM dual-identity attack was already present (pre-review); FH-F2/
FH-F3 gap is now explicitly documented (not silently untested — it was
never in scope to test, since no code path can produce the state today).
No test fixture was found representing a schema state that cannot
genuinely occur.

## Defects found

**None in production code.** Three genuine test-coverage gaps, all
explicitly named in this mission's own attack list, found and closed:
missing UPDATE-path dual-identity attacks; rebuild-atomicity tests proving
only "no DDL ran" rather than genuine mid-sequence rollback; FH-F2/FH-F3's
exact exposure was under-specified in the original report (F2's own,
more immediate exposure via `review_state="unreviewed"` was not
previously called out).

## Corrections made

1. Three new tests closing the UPDATE-path dual-identity gap in
   `tests/test_discovery_evidence_persistence.py`.
2. Two new tests closing the rebuild-atomicity mock-only gap in
   `tests/test_source_assertion_unknown_airport_migration.py`, using real
   mid-sequence DDL injection (crash after the real `DROP TABLE`, before
   `RENAME`) for both the upgrade and downgrade directions.
3. This report's own FH-F2/FH-F3 section corrected/sharpened — no code
   change (confirmed `SAFE_TO_DEFER_TO_UAC3`, not a UAC2B blocker), but
   the documented exact expected UAC3 correction is more precise than the
   original report's.

## Regression tests added

5 new tests (61 total across the two touched test files' own new-test
counts: 56 in the migration file, up from 54; 33 in the discovery-
persistence file, up from 30).

## Focused tests

`tests/test_source_assertion_unknown_airport_migration.py`: **56
passed**, 0 failed. `tests/test_discovery_evidence_persistence.py`: **33
passed**, 0 failed. Combined broader suite (UAC2B + UAC2A + UAC1 +
discovery persistence + model-contract + MAC capture-runner + Fleet
Health + adjacent governance): **539 passed**, 0 failed.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the corrections; see the final chat report.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25,
`unknown_airport_candidates`/`unknown_airport_candidate_reviews`
confirmed **absent**, `source_assertions` confirmed to still have **no**
`unknown_airport_candidate_id` column — verified fresh both before and
after this review.

RWI_UAC2B_SOURCEASSERTION_UNKNOWN_AIRPORT_INTEGRATION_IMPLEMENTATION_COMPLETE
