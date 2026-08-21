# FH-D4 Signal Disposition — D4D3: Read / Resolution Service — Report

D4D3 of [fh-d4-signal-disposition-design.md](fh-d4-signal-disposition-design.md)
(§13 "Read API", §19 "Recommended implementation slices"). This slice
implements ONLY the read-only resolution layer: given an exact set of
Signal ids, what disposition status (if any) currently applies. No Fleet
Health integration, no CLI, no writes, no migration changes, no real
database access.

## 1. Files read fresh

`fh-d4-signal-disposition-design.md`, `app/models/signal_disposition.py`,
`app/services/signal_disposition_persistence.py`, the D4D1 report, the
D4D2 report, `scripts/migrate_signal_disposition_d4d2.py`,
`tests/test_signal_disposition_persistence.py`,
`tests/test_signal_disposition_migration.py`,
`app/services/reviewer_action_persistence.py` (`get_latest_reviewer_action()`
precedent), `app/services/fleet_health_check.py` (`session.no_autoflush`
precedent).

## 2. Exact files created

- `app/services/signal_disposition_resolution.py`
- `tests/test_signal_disposition_resolution.py` (48 tests at
  implementation time; 60 after the critical-review checkpoint below)
- `docs/architecture/fh-d4-signal-disposition-d4d3-resolution-report.md`
  (this file)

No existing file was modified.

**Note**: §14's "exactly three queries" claim below was found false during
the critical-review checkpoint (see "Critical Review Checkpoint" section
at the end of this report) — retained here unedited as the original,
as-implemented claim; the corrected, re-verified behavior is documented in
the checkpoint section.

## 3. Read API

```python
UNREVIEWED = "UNREVIEWED"
CONFIRMED_DISTINCT = "CONFIRMED_DISTINCT"
CONFIRMED_SAME_REAL_WORLD_EFFORT = "CONFIRMED_SAME_REAL_WORLD_EFFORT"

def resolve_fh_d4_group_status(session, signal_ids) -> SignalDispositionStatus
def resolve_fh_d4_group_statuses(session, groups) -> dict[tuple[int, ...], SignalDispositionStatus]
def get_latest_disposition_for_member_set(session, signal_ids) -> Optional[SignalDisposition]
def find_related_historical_dispositions(session, signal_ids) -> tuple[RelatedHistoricalDisposition, ...]
```

Named `app/services/signal_disposition_resolution.py` - deliberately
**not** the design doc's own §14 sketch name
(`fh_d4_disposition_resolution.py`), which names a *future*, separate,
Fleet-Health-specific adapter (D4D4, not this slice) that will eventually
call into this module. This module itself is generic and domain-level -
it knows nothing about FH-D4 or Fleet Health, mirroring how
`app.services.reviewer_action_persistence` knows nothing about the
specific caller of `get_latest_reviewer_action()`.

## 4. Result / status contract

```python
@dataclass(frozen=True)
class SignalDispositionStatus:
    status: str  # UNREVIEWED | CONFIRMED_DISTINCT | CONFIRMED_SAME_REAL_WORLD_EFFORT
    signal_ids: tuple[int, ...]
    latest_disposition_id: Optional[int] = None
    decision: Optional[str] = None
    reviewer: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None

@dataclass(frozen=True)
class RelatedHistoricalDisposition:
    disposition_id: int
    member_signal_ids: tuple[int, ...]
    decision: str
    reviewer: str
    reason: str
    created_at: datetime
    relation: str  # "SUBSET" | "SUPERSET"
```

No ORM object is exposed by `resolve_fh_d4_group_status()`/
`resolve_fh_d4_group_statuses()`/`find_related_historical_dispositions()` -
per this mission's own §12 instruction ("D4D4 integration should not need
lazy ORM traversal"). `get_latest_disposition_for_member_set()` is the one
exception, returning the real `SignalDisposition` ORM object - it exists
specifically to mirror `get_latest_reviewer_action()`'s own established
precedent for a caller that genuinely needs the ORM row itself (e.g. to
read `.members` or use `.id` as a future `supersedes_id`); every other
function returns only the narrow dataclasses.

Exactly the three designed status values - **no persisted `STALE` state**,
matching this mission's own explicit §4 instruction. "Stale/related"
context is a *separate*, advisory-only function
(`find_related_historical_dispositions()`), never a value the status
vocabulary itself carries.

## 5. Exact-set identity verdict

Plain `frozenset(int)` equality throughout - no fingerprint, matching
design doc §8. `{41, 67}` and `{67, 41}` (reversed) and `{41, 41, 67, 67}`
(duplicated) all normalize to the identical canonical form
(`tuple(sorted(set(signal_ids)))`) and resolve identically. `{41, 67}`,
`{41, 67, 80}`, and `{41, 80}` are three completely distinct identities -
no subset/superset relationship is ever treated as a match for the
*current* status question (that relationship is reserved exclusively for
the separate `find_related_historical_dispositions()` advisory function).

## 6. Input validation

`_normalize_and_validate()` (called by every public entry point):

1. Deduplicates and sorts - matching `record_signal_group_disposition()`'s
   own identical normalization exactly, so a read query and the write that
   created a matching row always agree on canonical form.
2. Requires at least 2 distinct ids (`ValueError` otherwise) - the same
   minimum cardinality the write side enforces; a disposition can never
   legitimately have fewer than 2 members, so a 0- or 1-element query is a
   caller error, not a legitimate "no disposition" case.
3. **Requires every id to reference a real, existing Signal** -
   `ValueError` naming the missing id(s), never a silent `UNREVIEWED`.
   This mission's own explicitly stated preference (§6): "missing Signal
   IDs should fail clearly rather than silently produce UNREVIEWED if the
   caller claims a real current group." Chosen deliberately to mirror the
   *write* side's own identical existence-check precondition
   (`record_signal_group_disposition()`'s own `session.get(Signal, id) is
   None: raise ValueError`) - a caller invoking group resolution for
   nonexistent ids is almost certainly a bug, and `UNREVIEWED` would
   misleadingly imply "this real group has not been reviewed yet" rather
   than "this group does not even reference real Signals."
   `get_latest_reviewer_action()` itself does *not* verify its
   `source_assertion_id` argument exists - noted as a considered but
   rejected precedent, since that function's own contract is narrower (one
   fixed id, not a caller-assembled group whose very membership is the
   thing under question) and the mission's own explicit preference here
   points the other way.

Malformed/non-integer ids are not specially type-checked - they fail
naturally and clearly through the same existence check (`session.get(Signal,
"not-an-id")` resolves to `None` for any value that isn't a real primary
key, landing in the same `ValueError` path with the offending value named
in the message).

## 7. Latest-order verdict

`created_at` DESC, `id` DESC - the exact tie-break
`get_latest_reviewer_action()` already uses, reused verbatim (design doc
§13's own explicit instruction). Implemented as `max(..., key=lambda h:
(h.created_at, h.id))` over the candidates whose member set exactly
matches the queried set.

## 8. Supersession semantics verdict

**Recency alone, never a `supersedes_id` chain walk** - matching
`get_latest_reviewer_action()`'s own already-reviewed philosophy exactly
("recency alone already identifies current state... no chain-walking is
needed"). A full three-row supersession chain (`D1 DISTINCT` → `D2 SAME`
supersedes `D1` → `D3 DISTINCT` supersedes `D2`) resolves to `D3` correctly
without ever reading `.supersedes_id` for resolution purposes.

**A genuinely investigated design question, resolved by precedent, not
guessed**: D4D1's own `record_signal_group_disposition()` does *not*
require a later disposition for the same exact member set to supply
`supersedes_id` at all - only a genuine *supersession claim* (when
`supersedes_id` is actually given) must target an exact-set match; a
brand-new, unlinked disposition for an already-dispositioned exact set is
fully legal at the persistence layer. This raises the mission's own §19
question directly: should a later, *unsuperseded* disposition for the same
set still become "latest"? **Yes - by design, matching precedent exactly.**
Verified directly (`test_competing_unsuperseded_disposition_still_becomes_latest`):
a third disposition recorded with `supersedes_id=None` for a set that
already has two linked, chained dispositions still becomes the resolved
"latest" purely by recency. This is not a silently-invented governance
rule - it is the *same* rule `get_latest_reviewer_action()` already
applies to `ReviewerAction`, where the identical situation (a new action
recorded without linking to the prior one via `supersedes_action_id`) has
always resolved by recency alone, already reviewed and committed in this
project. No STOP was warranted; the ambiguity was resolved by consistency
with an existing, already-reviewed precedent rather than an assumption.

## 9. No-history verdict

No disposition at all for a given exact set → `SignalDispositionStatus(status=UNREVIEWED,
signal_ids=<canonical>, latest_disposition_id=None, decision=None,
reviewer=None, reason=None, created_at=None)`.

## 10. DISTINCT verdict

Latest matching disposition has `decision == "DISTINCT"` →
`status == CONFIRMED_DISTINCT`, all audit fields populated from that row.

## 11. SAME verdict

Identically for `decision == "SAME_REAL_WORLD_EFFORT"` →
`status == CONFIRMED_SAME_REAL_WORLD_EFFORT`. No canonical-Signal field
exists anywhere on the result - confirmed directly
(`assert not hasattr(status, "canonical_signal_id")`).

## 12. Stale/subset/superset verdict

A subset query against a dispositioned superset, and a superset query
against a dispositioned subset, both resolve to `UNREVIEWED` for the
*current* exact set - `resolve_fh_d4_group_status()` never treats a
subset/superset relationship as a match. The pre-existing, narrower
disposition remains permanently valid and independently inspectable
(`find_related_historical_dispositions()`), but never resolves the new
query.

## 13. Related-history policy

**Narrow: strict subset or superset only - never bare intersection.**
This mission's own §11 provided the deciding example directly: `old {A,B}`
vs. `current {A,C}` share only Signal A, and the mission itself frames
this as "may be a completely different historical case" - arguing *against*
treating mere overlap as related. Given that reasoning, intersection-based
"related" was rejected as too noisy (it would flood a future presentation
layer with pairs that merely happen to share one Signal, most of which are
likely unrelated historical cases per the mission's own framing).
Subset/superset was chosen as the narrow, defensible rule directly
motivated by the mission's own worked examples (§10's "group grew by one"/
"group shrank by one" cases) - these are the cases where a disposition
genuinely answers a narrower or broader version of the *same* underlying
question, unlike a merely-overlapping, structurally unrelated pair.
Verified directly: `old {A,B}` is NOT surfaced as related to `current
{A,C}`, but IS surfaced (as `SUBSET`) for `current {A,B,C}`.

Kept as a clearly separate function (never called internally by
`resolve_fh_d4_group_status()`) rather than folded into the main status
result - the design doc's own §21 left "whether this second query belongs
in the same module or is a D4D4 concern" open; this mission's own explicit
§3 responsibility #5 ("expose related/stale historical disposition context
without treating it as current") resolves that specific question in favor
of D4D3 owning it, but the two questions ("what is current" vs. "what
related history exists") remain structurally independent - calling
`find_related_historical_dispositions()` can never change what
`resolve_fh_d4_group_status()` returns, and vice versa.

## 14. Query-count/batching verdict

`resolve_fh_d4_group_statuses()` (batch) uses exactly **three queries
total**, regardless of how many groups or Signals are being resolved in
one call: (1) find every `disposition_id` with at least one member among
the UNION of all requested Signal ids; (2) fetch the *complete* member
list for exactly those candidate dispositions; (3) fetch the header rows
for those same candidates. Never one query per group, never one query per
Signal. Existence-checking (`_normalize_and_validate()`) still runs one
`session.get()` per distinct Signal id across all groups - a genuine,
documented, bounded cost (equal to the total distinct Signal count, not
the group count), not eliminated further since SQLAlchemy's own identity-
map-aware `session.get()` is already the established precedent's own
existence-check idiom throughout this pipeline.
`resolve_fh_d4_group_status()` (single-group) is implemented as a trivial
wrapper calling the batch function with one group - it is not a separate
code path, so its query count is identical to a one-group batch call.
`find_related_historical_dispositions()` deliberately uses a full,
unfiltered fetch of every `SignalDispositionMember` row (one query) rather
than a filtered/complex query - documented as a deliberate tradeoff given
the small expected scale of this table (a few dozen rows even with all 12
real FH-D4 groups fully dispositioned, per the D4D1 report's own §17
simulation).

## 15. Batch-equivalence verdict

Guaranteed **by construction**, not merely tested:
`resolve_fh_d4_group_status()`'s own implementation *is*
`resolve_fh_d4_group_statuses(session, [signal_ids])[canonical_key]` - the
two can never disagree. Verified anyway (`test_batch_equivalence_to_single_group_resolution`)
across three groups of mixed status (`CONFIRMED_DISTINCT`,
`CONFIRMED_SAME_REAL_WORLD_EFFORT`, `UNREVIEWED`) for defense-in-depth, and
cross-group contamination is explicitly ruled out
(`test_no_cross_group_contamination`).

## 16. Determinism verdict

Repeated calls, reversed caller input, duplicate caller ids, and reversed
DB insertion order all produce byte/field-identical results. Same-timestamp
disposition rows tie-break correctly by `id` (§18 below).

## 17. Same-timestamp tiebreak verdict

Two dispositions for the identical exact set, constructed with an
identical `created_at` value (set at construction time, before any flush -
mutating `created_at` on an already-flushed row would itself be blocked by
the header's own pre-existing immutability listener, a genuine constraint
this test had to work around correctly rather than accidentally prove the
wrong thing). The higher-`id` row is confirmed to win, matching the
documented `created_at` DESC, `id` DESC contract exactly
(`test_same_timestamp_id_tiebreak`).

## 18. Competing-unsuperseded-history verdict

See §8 above - resolved by precedent (recency wins regardless of
`supersedes_id` linkage), verified directly, documented as a deliberate
consistency decision rather than a silently-invented rule.

## 19. Malformed-history verdict

A disposition header with **zero** members (constructed directly,
bypassing `record_signal_group_disposition()`'s own `>=2` enforcement) and
one with **exactly one** member both structurally can never equal any
real, valid ≥2-element queried group - `resolve_fh_d4_group_status()`
correctly reports `UNREVIEWED` for both, with **no special-case code**
required (an empty or single-element frozenset simply never equals a
genuine group's frozenset). A zero-member disposition also never appears
in `find_related_historical_dispositions()`'s own output despite an empty
set being a trivial mathematical subset of everything - excluded
deliberately (`members_frozen == query_set` short-circuit only excludes
exact matches, but an empty set can never equal a ≥2-element query set
either, so it correctly falls through to the subset branch... verified
directly that it is NOT surfaced, since a 0-member "disposition" carrying
no real audit meaning would be pure noise, not genuine related history).
Duplicate members (impossible via the `UNIQUE` constraint) and a missing
member Signal (impossible via the FK) were not separately re-attacked here
- both are already exhaustively covered by D4D1's/D4D2's own committed,
passing test suites, and this service does not weaken either guarantee in
any way.

## 20. No-autoflush/read-only verdict

Every public function's body is wrapped in `session.no_autoflush` -
including the existence-check/normalization step, not merely the batch
queries - the exact review-checkpoint fix
`app.services.fleet_health_check`'s own `build_fleet_hard_invariant_snapshot()`/
`build_fleet_review_snapshot()` already established. Verified directly: a
caller's own pending, dirty `Airport.name` change survives untouched
across every read function call (`test_never_autoflushes_callers_pending_mutation`);
SQL-instrumentation confirms zero `INSERT`/`UPDATE`/`DELETE` statements are
ever emitted (`test_no_write_sql_statements_emitted_instrumented`); the
session's own `.new`/`.dirty`/`.deleted` collections remain empty after
every function call.

## 21. Information-firewall verdict

AST-verified: zero references anywhere in the module to `title`, `notes`,
`source_notes`, any financial field, `supplier`/`likely_supplier`/
`confirmed_vendor`, `category`, `confidence`, `status`,
`manual_year_estimate`, `published`, `airport_id`, `runway_id`, or
`installation_id` - the module reads only `SignalDisposition.id`/
`.decision`/`.reviewer`/`.reason`/`.created_at`,
`SignalDispositionMember.signal_id`/`.disposition_id`, and `Signal` only
via `session.get(Signal, id)` (existence-check only, touching no other
Signal column). Behaviorally confirmed: a Signal with a deliberately
identifiable title and a large financial figure never leaks into the
resolved status's own `repr()`.

## 22. Pair/triple/quintuple verdict

All three group sizes verified for both `DISTINCT` and
`SAME_REAL_WORLD_EFFORT`, plus explicit subset/superset-of-a-dispositioned-
group negative cases for each direction.

## 23. 12-group synthetic simulation

Reproduced the real cardinality mixture (6 pairs, 4 triples, 2 quintuples
= 34 total Signals across 12 groups), dispositioned 4 of the 12 groups
(2 `DISTINCT`, 2 `SAME_REAL_WORLD_EFFORT`) via a single batch call, and
confirmed exactly 8 groups resolve `UNREVIEWED` and the other 4 resolve
their exact recorded decision - all via one `resolve_fh_d4_group_statuses()`
call, not 12 separate ones.

## 24. Unicode/international verdict

Swedish (`Åsa Lindqvist`) and Japanese fixtures both round-trip through
the resolved status's own `reviewer`/`reason` fields exactly. Nothing in
the module assumes US airports, FAA, USD, or English text - structurally
impossible given the information firewall (§21).

## 25. Migration-schema parity verdict

`test_resolution_works_against_migration_created_db` builds a pre-D4D2
schema, runs the real `scripts/migrate_signal_disposition_d4d2.upgrade()`
(never `Base.metadata.create_all()` for the two disposition tables), then
exercises both `resolve_fh_d4_group_status()` and
`find_related_historical_dispositions()` against that genuinely migrated
database end-to-end - confirms this service works on the actual migration
schema, not merely an ORM-generated one.

## 26. Failure-loud verdict

A database missing the `signal_dispositions`/`signal_disposition_members`
tables entirely raises a real, uncaught exception
(`sqlalchemy.exc.OperationalError` in practice) when queried - never
silently converted to `UNREVIEWED` or an empty tuple. Verified directly
(`test_missing_schema_raises_not_unreviewed`). AST-confirmed: **zero**
`try`/`except` blocks exist anywhere in this module's own source
(`test_no_try_except_anywhere_in_module_ast`) - there is no code path by
which any exception could be caught and silently reinterpreted.

## 27. Defects/design ambiguities found

**No defect found.** One genuine design question was investigated
carefully rather than guessed at - see §8 above (competing unsuperseded
dispositions for the same exact set) - and resolved by direct consistency
with `get_latest_reviewer_action()`'s own already-reviewed precedent, not
by invention. This did not rise to a STOP-and-report case: the resolution
follows directly from an existing, already-committed architectural
decision this project already made once, applied consistently a second
time.

## 28. Corrections made

None - no defect required correcting. One test-authoring correction was
made during this slice's own development (not affecting production code):
the first draft of the same-timestamp tie-break test attempted to set
`created_at` on an already-flushed `SignalDisposition` row, which
correctly triggered the header's own pre-existing immutability listener
(an `UPDATE`, not an `INSERT`) - corrected to set `created_at` at
construction time instead, before the first flush.

## 29. Focused tests

`tests/test_signal_disposition_resolution.py` +
`tests/test_signal_disposition_persistence.py` +
`tests/test_signal_disposition_migration.py` +
`tests/test_reviewer_action_persistence.py` +
`tests/test_model_contract.py`: **232 passed**, 0 failed.

## 30. Full pytest

See the validation run recorded at implementation time - expected 2178
(D4D2 checkpoint baseline) + 48 new D4D3 tests = **2226**, 0 regressions.

## 31. py_compile

Clean on `app/services/signal_disposition_resolution.py` and
`tests/test_signal_disposition_resolution.py`.

## 32. `git diff --check`

Clean (exit 0, no whitespace errors).

## 33. Explicit real-DB no-access proof

- Every test uses an isolated in-memory or `tmp_path`-scoped SQLite
  database - never `data/runway_safe.db`. Verified both by direct code
  inspection and by a dedicated AST-based test
  (`TestNoRealDatabaseAccess`) confirming no literal reference to the real
  database filename appears anywhere in the module's own compiled source
  (the module has no `DEFAULT_DATABASE`-style constant at all - unlike the
  migration scripts, this is a pure library module with no CLI entry point
  and no database-path argument of its own).
- `data/runway_safe.db`'s SHA-256/size/mtime were captured immediately
  before running the full pytest suite for this slice and matched the
  checkpoint established at the start of this session.
- No real disposition was created anywhere in this task - every test
  fixture is synthetic, using generic Signal ids never hardcoded to the
  real MSP #41/#67 shape (per this mission's own explicit §25 instruction).

## 34. Conclusion (implementation-time)

D4D3 is implemented exactly per the committed design's own §13 read
contract, extended by this mission's own explicit scope (batch resolution,
related-history advisory context) with each extension's own design
decision investigated against established precedent rather than guessed.
No defect was found; the one genuine design question (competing
unsuperseded dispositions) was resolved by direct consistency with
`get_latest_reviewer_action()`'s own already-reviewed precedent. Ready for
its own adversarial D4D3 review checkpoint before D4D4 (Fleet Health
integration) begins.

---

## Critical Review Checkpoint (D4D3, post-implementation)

Per this project's own standing two-phase discipline, the implementation
report above was **not trusted**. A fresh adversarial review was performed
against the actual committed code and actual instrumented database
behavior, per a 41-section review mission. Two genuine defects were found
and fixed; every other reviewed section held up.

### Defect 1 (CONFIRMED): §29's "exactly three queries" claim was false

**Original claim** (§14 above, and the original module docstring):
`resolve_fh_d4_group_statuses()` costs "exactly three queries total,
regardless of scale," with existence-checking treated as a separately
acknowledged, "bounded but per-Signal-id" cost.

**Reality, empirically measured via direct SQL instrumentation** (not
merely re-read from the code): the batch resolver's existence check
(`_normalize_and_validate()`, called once *per group* inside the batch's
own list comprehension) issued one `session.get(Signal, id)` per
individual Signal id, not merely "per distinct Signal id across the whole
call" as the report implied. Measured directly:

| Groups | Signals | SELECT statements (original) |
|---|---|---|
| 1 | 2 | 5 |
| 12 | 22 | 25 |
| 100 | 200 | 179 |

Clearly `O(distinct Signal ids)`, not the constant few the report claimed.
The original test (`test_batch_query_count_bounded_not_n_plus_one`) never
caught this because it asserted only a "generous" `len(select_statements)
< 30` bound at `n=12` — loose enough to pass even with the real defect
present. This is exactly the weakness this mission's own §35 warned about
by name ("batch-count test instrumenting only part of call").

**Fix**: split the old `_normalize_and_validate()` into a pure
`_normalize()` (dedup/sort/cardinality check, no DB access) and a new
`_validate_all_exist()` that checks the *entire batch's* union of Signal
ids in exactly one `SELECT id FROM signals WHERE id IN (...)` query.
`resolve_fh_d4_group_statuses()` now computes `all_signal_ids` once across
every group before calling it. Result: **exactly 4 SELECT statements
total, regardless of group count or Signal count** (1 existence check + 3
candidate/member/header batch queries) — re-verified at `n=1`, `n=12`, and
`n=100` groups. `resolve_fh_d4_group_status()` (single-group) and
`find_related_historical_dispositions()` both now call `_normalize()` +
`_validate_all_exist()` directly (the latter needed a second follow-up fix
— see "Bug introduced by the fix itself," below).

**Regression tests added** (`TestBatchResolutionQueryCost`, 5 tests):
exact-count assertions at `n=1/12/100` groups (parametrized), an isolated
test of `_validate_all_exist()` alone against 100 ids (exactly 1 query),
and a zero-match short-circuit case (exactly 2 queries — no member/header
query needed when no candidate dispositions exist). The original
`test_batch_query_count_bounded_not_n_plus_one` was itself tightened from
`< 30` to an exact `== 4`.

### Bug introduced by the fix itself, caught before any test run

Splitting `_normalize_and_validate()` left a dangling call to the
now-deleted function name inside `find_related_historical_dispositions()`
— a `NameError` waiting to fire on first real call. Caught by re-reading
the diff before running tests (not by a failing test — the existing suite
never called that function in a way that would have caught it before a
fresh reviewer read the file top-to-bottom). Fixed by replacing the
dangling call with `_normalize()` + `_validate_all_exist()`, matching the
pattern used everywhere else in the module. Confirmed via the full
`tests/test_signal_disposition_resolution.py` run afterward (all 48
original tests, including every `find_related_historical_dispositions()`
test, passed against the corrected code).

### Defect 2 (CONFIRMED — genuine visibility gap, not an algorithm bug): §9's "competing unsuperseded decisions" question

Re-investigated this mission's own §9 (marked CRITICAL, explicit "do not
guess") from first principles rather than accepting the implementation
report's §8/§18 conclusion at face value. Confirmed independently that the
report's core claim is correct: D4D1's `record_signal_group_disposition()`
genuinely does not require `supersedes_id` at all, so two independent
reviewers *can* legitimately create unlinked, competing dispositions for
the same exact Signal set via the already-committed write API — and
"recency wins" (this module's resolution algorithm) is correctly justified
by direct precedent-matching with `get_latest_reviewer_action()`, which
has the identical policy for `ReviewerAction`. **The resolution algorithm
itself was not changed** — changing it would have meant second-guessing
D4D1's already-committed write-side semantics, explicitly out of this
mission's scope.

However, further investigation surfaced a real, narrow gap the original
report did not address: the read layer gave **zero visibility** into
whether such a competing-roots situation had actually occurred for a given
exact set — it silently picked a "latest wins" answer with no signal that
a genuine disagreement exists between two dispositions that never
reference each other. Since D4D3 is read-only and cannot alter D4D1's
write-time validation, the fix chosen is a narrow, read-only *addition*:
a new `independent_root_count: int` field on `SignalDispositionStatus`,
computed as a plain count of already-fetched, exact-set-matching
dispositions whose own `supersedes_id IS NULL` (normally 0 or 1; a value
`>1` is direct proof of an independently-recorded competing root,
regardless of which one "won" by recency). **No chain-walking is
performed** — the field is computed purely from headers already fetched
for the existing resolution query, adding no additional database queries
(confirmed by the unchanged `== 4` query-count assertions above, which
were measured *after* this field was added).

This is documented as a deliberate, narrow correction, not a new
governance rule: it changes visibility only, never `status`,
`decision`, or `latest_disposition_id`.

**Regression tests added** (`TestIndependentRootCount`, 5 tests): zero
roots for `UNREVIEWED`, one root for an ordinary single disposition, one
root for a properly-chained 3-row supersession (proving chain length never
inflates the count), two roots for the competing-unsuperseded scenario
(with an explicit assertion that `status`/`latest_disposition_id` are
unaffected), and parity between the single-group and batch resolvers. The
pre-existing `test_competing_unsuperseded_disposition_still_becomes_latest`
was also strengthened with an `independent_root_count == 2` assertion.

### §21 (malformed historical disposition) — strengthened, no defect found

The original zero-member/one-member coverage was sound but did not attack
the two scenarios this mission's own §21 explicitly named: a CHECK-bypassed
impossible `decision` value, and an FK-bypassed orphaned member row.
Two new regression tests were added:

- **CHECK bypass**: a hand-built minimal schema (no `decision` CHECK
  constraint) with a header row carrying `decision='BOGUS_IMPOSSIBLE_
  DECISION'` — `resolve_fh_d4_group_status()` correctly raises `KeyError`
  from the `_DECISION_TO_STATUS` lookup rather than silently resolving to
  any valid status. Fail-loud confirmed.
- **FK bypass / orphaned member**: attempting to create an orphaned
  `SignalDispositionMember` via the ORM is *already* blocked one layer
  earlier by D4D1's own `before_insert` seal guard (a second, independent
  fail-loud mechanism, confirmed as a side effect of writing this test).
  Bypassing the ORM entirely with a raw SQL insert (the only way such a
  row can exist) and then calling `resolve_fh_d4_group_status()` correctly
  raises `KeyError` from the `headers_by_id[...]` lookup in
  `resolve_fh_d4_group_statuses()` — never a fabricated status.

Both failure modes were already structurally impossible to silently
mishandle (the code has no `try`/`except` anywhere, verified by the
pre-existing AST test), but had no direct regression test before this
review. Now covered.

### Sections re-verified with no defect found

§1 (starting HEAD `d71e9e2a3b8df68819964dcb6e6c69c2e4f73c81`, confirmed via
`git rev-parse HEAD`) · §5 (exact-set identity, unchanged) · §6 (input
validation, unchanged behavior, now split across two functions) · §7
(batched, no N+1 — see Defect 1) · §8 (latest-order tie-break, unchanged)
· §16–§21 (related-history policy: strict subset/superset only, exact
match excluded, multiple related histories returned unsorted-by-relevance
but deterministically ordered by `(created_at, disposition_id)`, malformed
history — see above) · §22–§23 (`session.no_autoflush` wraps every public
function's entire body including the new `_validate_all_exist()` call;
session `.new`/`.dirty`/`.deleted` unchanged before/after) · §24–§26
(query failure/missing schema propagate uncaught; migration-created-DB
parity re-run and passing) · §27–§28 (AST-verified information firewall
unaffected by the new code; no Fleet Health import anywhere in the module)
· §29–§34 (pair/triple/quintuple, 12-group synthetic mix, overlapping
cases, determinism, immutability, Unicode — all pre-existing coverage
re-run and passing, no changes needed).

### Final validation

- `tests/test_signal_disposition_resolution.py`: **60 passed** (48
  original + 12 new: 5 query-cost, 5 independent-root-count, 2 malformed-
  history).
- Focused: `test_signal_disposition_persistence.py` +
  `test_signal_disposition_migration.py` +
  `test_reviewer_action_persistence.py`: **179 passed**.
- Full `pytest`: see final commit-time validation run recorded separately;
  expected baseline 2226 (pre-review) + 12 new tests = **2238**.
- `py_compile` clean on both changed Python files.
- `git diff --check` clean (exit 0).
- `data/runway_safe.db` SHA-256/size/mtime confirmed unchanged
  (`4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
  1,794,048 bytes) before and after this entire review.

### Conclusion (post-review)

Two genuine defects were found and fixed by actually attacking the
implementation rather than trusting its own report: a real, reproduced
query-scaling defect (Defect 1) and a real, narrow visibility gap
(Defect 2) — plus one self-introduced bug from the Defect-1 fix, caught
before any test run. No other reviewed section required correction. D4D3
is sound for commit.
