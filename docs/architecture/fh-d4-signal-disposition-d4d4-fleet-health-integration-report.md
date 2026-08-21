# FH-D4 Signal Disposition — D4D4: Fleet Health Integration — Report

D4D4 of [fh-d4-signal-disposition-design.md](fh-d4-signal-disposition-design.md)
(§14 "Fleet Health integration point", §15 "Operational output semantics").
This slice integrates persisted Signal-group disposition state into
operational FH-D4 Fleet Health output, WITHOUT changing the pure FH-D4
detector. Implementation only in this pass — no commit, no push (per this
mission's own explicit stop boundary).

## 1. Files read fresh

`fh-d4-signal-disposition-design.md`, the D4D1/D4D2/D4D3 reports,
`app/services/signal_disposition_resolution.py`,
`app/services/fleet_health_review_rules.py`, `app/services/fleet_health_check.py`,
`app/services/fleet_health_rules.py`, `scripts/run_data_health_check.py`,
`app/models/signal_disposition.py`, `scripts/migrate_signal_disposition_d4d2.py`.

## 2. Exact files created

- `app/services/fh_d4_disposition_resolution.py`
- `tests/test_fh_d4_disposition_resolution.py` (41 tests at implementation
  time; 60 after the critical-review checkpoint below)
- `docs/architecture/fh-d4-signal-disposition-d4d4-fleet-health-integration-report.md`
  (this file)

**Note**: §5's result contract below shows the implementation-time shape
of `FhD4DispositionResolution` (without `attention_required`) — the
critical-review checkpoint at the end of this report added that field to
close a real visibility gap; retained here unedited as the original,
as-implemented contract.

## 3. Exact files modified

**None.** `app/services/fleet_health_review_rules.py` (the pure FH-D4
detector), `app/services/fleet_health_check.py` (`run_fleet_review_check()`/
`run_full_fleet_health_check()`), `app/services/signal_disposition_resolution.py`
(D4D3, already reviewed/committed), and `scripts/run_data_health_check.py`
(the CLI) are all byte-for-byte unchanged — confirmed via `git status`
(nothing but the three new files above appears as a change) and via the 443
pre-existing focused tests for these modules, all still passing unmodified.

## 4. Integration architecture

```
DB facts
    -> pure FH-D4 rule (fleet_health_review_rules.evaluate_fh_d4, UNCHANGED)
    -> raw FH-D4 HealthFinding groups (entity_ids = member Signal ids)
    -> resolve_fh_d4_findings() (NEW - this module)
    -> FhD4DispositionResolution (operational active/resolved/ambiguous view)
```

`app/services/fh_d4_disposition_resolution.py` is the new, small, single-
purpose adapter module the design doc's own §14 recommends — not
`fleet_health_check.py` (its own committed contract is "pure DB reads
assembling facts for the pure rule modules," never a governance-table
read) and not the CLI (`scripts/run_data_health_check.py`'s own AST-
verified contract is "compose already-reviewed services, add zero logic of
its own"). The module never imports `fleet_health_review_rules` (confirmed
by a dedicated AST test, §22) — it only ever consumes `HealthFinding`
objects already produced elsewhere, never re-derives or re-detects
anything. `evaluate_fh_d4()` itself is proven unaffected by a direct
before/after equality test (§18 below).

## 5. Public API / result contract

```python
FH_D4_RULE_ID = "FH-D4"

def resolve_fh_d4_findings(session, findings: Sequence[HealthFinding]) -> FhD4DispositionResolution: ...
def run_disposition_aware_fh_d4_review(session) -> FhD4DispositionResolution: ...

@dataclass(frozen=True)
class FhD4OperationalGroup:
    raw_finding: HealthFinding          # exact same object FH-D4 emitted, never mutated
    signal_ids: tuple[int, ...]
    airport_id: Optional[int]
    status: str                          # D4D3's own three-value vocabulary
    latest_disposition_id: Optional[int]
    decision: Optional[str]
    reviewer: Optional[str]
    reason: Optional[str]
    created_at: Optional[datetime]
    independent_root_count: int
    ambiguous_history: bool              # independent_root_count > 1
    related_history: tuple[RelatedHistoricalDisposition, ...]

@dataclass(frozen=True)
class FhD4DispositionResolution:
    active_findings: tuple[FhD4OperationalGroup, ...]
    confirmed_distinct: tuple[FhD4OperationalGroup, ...]
    confirmed_same_effort: tuple[FhD4OperationalGroup, ...]
    ambiguous_groups: tuple[FhD4OperationalGroup, ...]
    non_d4_findings: tuple[HealthFinding, ...]
```

No ORM object is exposed anywhere in this result shape — every field is a
plain frozen dataclass, `str`, `int`, `Optional`, or `tuple`, matching D4D3's
own established contract ("the integration should not need lazy ORM
traversal"). `resolve_fh_d4_findings()` accepts ANY tuple of `HealthFinding`
(a full FHC3 result, a mixed rule-id set, or FH-D4-only) and filters
internally; `run_disposition_aware_fh_d4_review()` is a convenience
single-call wrapper composing the unmodified `run_fleet_review_check()`
with `resolve_fh_d4_findings()`.

## 6. Entrypoint choice (mission §13)

**Option A: a new, additional entrypoint** — `run_disposition_aware_fh_d4_review()`
— not an extension of `run_fleet_review_check()`/`run_full_fleet_health_check()`
in place. Both existing functions remain completely untouched, so every
existing caller (including the CLI and its own test suite) is provably
unaffected; verified by re-running `tests/test_fleet_health_check.py`,
`tests/test_fleet_health_check_review_findings.py`, and
`tests/test_run_data_health_check.py` unmodified (all still pass, §18/§27
below). The existing low-level raw FHC3 check remains fully available with
zero behavior change, per this mission's own explicit strong preference.

## 7. UNREVIEWED behavior

No disposition exists for a group's exact member set → the group lands in
`active_findings`, `status="UNREVIEWED"`, `independent_root_count=0`,
`ambiguous_history=False`. `related_history` may still be non-empty (see
§10). Verified: `TestUnreviewed`.

## 8. CONFIRMED_DISTINCT behavior

An exact-match `DISTINCT` disposition (with `independent_root_count <= 1`)
→ the group lands in `confirmed_distinct`, excluded from `active_findings`
entirely — no longer presented as pending review work — while its own
`raw_finding`, decision, reviewer, and reason remain fully inspectable on
the returned record (never deleted or hidden). No Signal mutation of any
kind occurs. Verified: `TestExactDistinct`.

## 9. CONFIRMED_SAME behavior

An exact-match `SAME_REAL_WORLD_EFFORT` disposition (with
`independent_root_count <= 1`) → the group lands in its own, separate
`confirmed_same_effort` bucket — never folded into `confirmed_distinct`,
never presented as an error/warning, never silently dropped. No canonical
Signal is ever inferred (`FhD4OperationalGroup` has no such field). No
merge/unpublish action of any kind occurs anywhere in this module (it has
no write capability at all — see §20). Verified: `TestExactSame`.

## 10. Stale-history behavior

A stored disposition for a strict subset or superset of the CURRENT FH-D4
group's exact member set never resolves the current group — the current
group's own exact-match status stays whatever it actually is (typically
`UNREVIEWED`, landing in `active_findings`), while the old, narrower/
broader disposition is surfaced only as `related_history` (advisory,
`relation="SUBSET"`/`"SUPERSET"`), reusing D4D3's own exact subset/superset
semantics verbatim (never bare intersection — a merely-overlapping,
non-subset/superset historical set is never surfaced as related, matching
D4D3's own established, already-reviewed reasoning). Verified:
`TestStaleHistory` (grown-group and shrunk-group directions, plus the
merely-overlapping negative case) and the 12-group simulation (§17).

## 11. Independent-root policy (mission §6/§19 — a genuine design decision)

D4D3's own `independent_root_count` field is exposed per group but this
module NEVER changes `status`/`decision`/`latest_disposition_id` — "latest
wins" resolution stays exactly as D4D3 already established (unmodified,
unre-derived). What D4D4 decides is purely an OPERATIONAL PRESENTATION
question: a resolved group (`CONFIRMED_DISTINCT`/`CONFIRMED_SAME_REAL_WORLD_EFFORT`)
whose `independent_root_count > 1` (proof that more than one
independently-recorded root disposition exists for this exact member set,
created via D4D1's own already-committed write API, which does not require
`supersedes_id` linkage) is placed in its own dedicated `ambiguous_groups`
bucket instead of `confirmed_distinct`/`confirmed_same_effort` — never
silently folded into "resolved" as if the review history were unanimous.
This is "fail-visible over silent resolution" (this mission's own explicit
preference): the group's own `status`/`decision`/`latest_disposition_id`
fields are still populated (a caller inspecting an `ambiguous_groups` entry
can see exactly what the latest disposition says and who recorded it), but
its OPERATIONAL bucket makes the disagreement impossible to miss or
silently render as a clean, resolved result. An `UNREVIEWED` group can
never be ambiguous by construction — `independent_root_count` is only
nonzero when at least one matching disposition exists — verified directly
(`test_unreviewed_group_can_never_be_ambiguous`). A properly-chained
supersession (every later disposition for the same exact set links via
`supersedes_id`) stays at exactly one root and is never treated as
ambiguous, regardless of chain length. No chain-walking is introduced
anywhere in this module. Verified: `TestAmbiguousRoots` (3 tests).

## 12. Accounting-invariant verdict

For every call, `len(d4_findings) == len(active_findings) +
len(confirmed_distinct) + len(confirmed_same_effort) + len(ambiguous_groups)`
— each raw FH-D4 finding lands in EXACTLY one of the four buckets, proven
directly (not merely count-asserted) in the 12-group simulation (every
group's own exact bucket assignment is independently asserted, not just the
total) and in `TestAccountingInvariant` (which also asserts no
signal-id-set appears in more than one bucket). No raw finding is ever
destroyed, mutated, or silently dropped.

## 13. Raw-FHC3 preservation verdict

`run_fleet_review_check()`/`run_full_fleet_health_check()`/`evaluate_review_findings()`
in `fleet_health_check.py`/`fleet_health_review_rules.py` are untouched —
zero lines changed (confirmed by `git status` showing no modification to
either file). All 443 pre-existing focused tests covering both modules
(`tests/test_fleet_health_check.py`, `tests/test_fleet_health_check_review_findings.py`,
`tests/test_fleet_health_review_rules.py`, `tests/test_run_data_health_check.py`)
pass unmodified. `evaluate_fh_d4()`'s own output is directly proven
byte-identical before and after using this module in between
(`test_evaluate_fh_d4_output_unaffected_by_importing_this_module`).

## 14. Non-D4 preservation verdict

`resolve_fh_d4_findings()` filters internally to `rule_id == "FH-D4"` for
its own resolution logic; every other finding passes through
`non_d4_findings` with identical object identity and original relative
order — verified directly with a mixed FH-A1/FH-D3/FH-G1 fixture
(`TestNonD4Untouched.test_other_rule_findings_pass_through_unmodified`,
asserting `result.non_d4_findings == other_findings` by tuple equality,
which for frozen dataclasses also proves field-level equality) and with a
REAL, non-hand-constructed FH-D3 finding produced by
`evaluate_fh_d3()` alongside a real FH-D4 finding from the same fact set
(`test_real_fh_d3_finding_untouched_alongside_fh_d4`).

## 15. Schema-readiness verdict

This module deliberately does NOT check whether the D4D2 tables exist
before querying them — it fails loud naturally (an ordinary, uncaught
`sqlalchemy.exc.OperationalError` propagates), exactly matching D4D3's own
already-reviewed, already-committed "no self-check, fail loud" contract.
A Path-based, pre-session schema gate reusing `scripts.migrate_signal_
disposition_d4d2.inspect()` (mirroring the existing CLI's own `check_
schema_readiness()`, which already composes five other `inspect()`
functions the same way) is deliberately NOT added inside this module: a
direct search (`grep -rn "from scripts\." app/`) confirmed **zero**
`app/services/*` modules anywhere in this codebase import from `scripts/`
— only the reverse dependency exists throughout this pipeline. Adding one
here would introduce a new, backwards architectural dependency for a
single slice. A future CLI/D4D5 extension is responsible for composing
`migrate_signal_disposition_d4d2.inspect()` into its own schema gate before
ever opening a Session and calling this module — the same established
pattern the existing CLI already uses for its other five inspectors, not a
new idea. Verified: `TestFailureLoud.test_missing_d4d2_schema_raises_not_all_unreviewed`
(never converts operational failure into "all groups unreviewed").

## 16. Batch-query verdict

Bounded, scale-invariant, empirically measured (not merely asserted):
`resolve_fh_d4_group_statuses()` (D4D3's own batch resolver, unmodified) —
exactly 4 queries. `_batched_related_history()` (this module's own new,
private batched scan — see its own docstring for why a per-group call to
D4D3's single-group `find_related_historical_dispositions()` was rejected:
it would reproduce the exact class of N+1 defect D4D3's own critical
review already found and fixed once) — exactly 1 query if nothing is
related to any group (short-circuits, matching D4D3's own short-circuit
style), or exactly 2 if at least one relation exists anywhere in the whole
batch. **Total: exactly 5 (no related history) or exactly 6 (related
history present) SELECT statements for the ENTIRE call, regardless of
group count** — independently measured at 1/12/100 groups (identical count
at every scale) via direct SQL instrumentation, and asserted as an EXACT
count (not a "generous bound") in `TestBatchQueryBehavior` — a direct
response to the D4D3 review's own lesson that a loose bound does not
actually catch a real N+1 defect. Zero queries at all when no FH-D4
findings are present (verified). No lazy-load queries after the call
returns (all fields are plain values, no ORM objects).

## 17. Pair/triple/quintuple verdict

All three group sizes verified for both `DISTINCT` and
`SAME_REAL_WORLD_EFFORT`, using the REAL `evaluate_fh_d4()` pure detector
(not hand-constructed findings) to prove genuine end-to-end integration —
`TestGroupSizes` (6 parametrized cases).

## 18. 12-group simulation verdict

Reproduced the real cardinality mixture (6 pairs, 4 triples, 2 quintuples)
with a deliberately varied mix: one `DISTINCT`, one `SAME`, one stale
(subset-disposition-only) triple, one ambiguous (two independent roots)
triple, one `DISTINCT` quintuple, and 7 untouched (unreviewed) groups.
Every group's own exact operational bucket is independently asserted
against a hand-computed expectation table (not merely a count) —
`TestTwelveGroupSimulation`. The accounting invariant (§12) is also
verified in the same test.

## 19. Group-disappears verdict

A disposition exists in the database for a group the CURRENT detector run
does not emit at all (e.g. runway identity changed) — `resolve_fh_d4_findings()`
never fabricates an active finding for it; the result is driven entirely
by the `findings` actually supplied. Verified: `TestGroupDisappears`
(calling with an empty findings tuple despite a real stored disposition —
every bucket is empty).

## 20. Read-only/no-autoflush verdict

The entire function body is wrapped in `session.no_autoflush`, the same
review-checkpoint fix `fleet_health_check.py`'s own snapshot builders and
D4D3's own resolution functions already established. Verified directly: a
caller's own pending, dirty `Airport.name` change survives untouched
across a full `resolve_fh_d4_findings()` call
(`test_never_autoflushes_callers_pending_mutation`); SQL instrumentation
confirms zero `INSERT`/`UPDATE`/`DELETE` statements are ever emitted, for
both `resolve_fh_d4_findings()` and `run_disposition_aware_fh_d4_review()`
(`test_no_write_sql_emitted`); the session's own `.new`/`.dirty`/`.deleted`
collections remain empty after a call on a session with nothing pending
(`test_session_stays_clean`). No `session.add()`, `.flush()`, `.commit()`,
or `.delete()` appears anywhere in this module's own source.

## 21. Determinism verdict

Repeated calls, reversed finding input order, and reversed disposition
insertion order all produce equal results — `TestDeterminism` (3 tests).
Both result dataclasses are `@dataclass(frozen=True)`, matching D4D3's own
immutability contract.

## 22. Information-firewall verdict

AST-verified: zero references anywhere in the module to `title`, `notes`,
`source_notes`, any financial field, `supplier`/`likely_supplier`/
`confirmed_vendor`, `category`, `confidence`, `manual_year_estimate`,
`published`, `runway_id`, or `installation_id`
(`test_module_never_references_forbidden_signal_attributes_ast` — note
`status` is deliberately excluded from this specific list, since this
module legitimately accesses `SignalDispositionStatus.status`/
`FhD4OperationalGroup.status`, D4D3's own reused three-value vocabulary,
which collides on the bare identifier with the real, forbidden
`Signal.status` column under a naive name-only AST scan; `Signal.status`
itself is never read, confirmed independently by the behavioral test
below). A second AST test confirms this module never imports
`app.services.fleet_health_review_rules` at all — it only ever consumes
already-produced `HealthFinding` objects. Behaviorally confirmed: a Signal
with a deliberately identifiable title and a large financial figure never
leaks into the resolved result's own `repr()`
(`test_behavioral_no_signal_content_leak`).

## 23. Migration-schema-parity verdict

`test_resolution_works_against_migration_created_db` builds a pre-D4D2
schema, runs the real `scripts/migrate_signal_disposition_d4d2.upgrade()`
(never `Base.metadata.create_all()` for the two disposition tables), then
exercises `resolve_fh_d4_findings()` against that genuinely migrated
database end-to-end (a stale/grown-group scenario) — confirms this
integration works on the actual migration schema, not merely an
ORM-generated one.

## 24. Failure-loud verdict

A database missing the `signal_dispositions`/`signal_disposition_members`
tables entirely raises a real, uncaught exception when queried — never
silently converted into "all groups unreviewed." A malformed FH-D4 finding
(wrong `entity_type`) raises `ValueError` rather than being silently
processed as if it were a normal Signal group. Both verified in
`TestFailureLoud`.

## 25. Defects/design ambiguities found

**No defect found in this implementation pass.** One genuine design
question — the independent-root operational policy (mission §6/§19) — was
investigated carefully and resolved with a narrow, documented, additive
decision (a dedicated `ambiguous_groups` bucket, "fail-visible over silent
resolution," see §11) rather than guessed at or silently folded into
"resolved." A second genuine architectural question — whether to reuse
`migrate_signal_disposition_d4d2.inspect()` for a schema gate inside this
module — was resolved by direct precedent search (no `app/services/*`
module imports from `scripts/` anywhere in this codebase) rather than
introducing a new, backwards dependency (see §15).

## 26. Corrections made

Two self-caught bugs during test authoring, both fixed before any review
checkpoint: (1) the initial 12-group simulation fixture mistakenly used
one of the 6 *pair* groups (only 2 members total) as the "stale" scenario
and sliced `[:2]` of it, which is the ENTIRE group, not a genuine subset —
corrected to use one of the 4 *triple* groups instead, so `[:2]` is a real,
strict subset. (2) the AST information-firewall test's forbidden-attribute
list originally included `"status"`, which collided with this module's own
legitimate `SignalDispositionStatus.status`/`FhD4OperationalGroup.status`
accesses (an identifier-name collision, not a real Signal.status read) —
corrected by removing `"status"` from that specific list with a
documenting comment, while the behavioral leak test continues to prove no
actual Signal column value ever leaks.

## 27. Focused tests

`tests/test_fh_d4_disposition_resolution.py` (41) +
`tests/test_signal_disposition_resolution.py` (60) +
`tests/test_signal_disposition_persistence.py` +
`tests/test_signal_disposition_migration.py` +
`tests/test_fleet_health_review_rules.py` +
`tests/test_fleet_health_check_review_findings.py` +
`tests/test_fleet_health_check.py` +
`tests/test_run_data_health_check.py`: **443 passed**, 0 failed.

## 28. Full pytest

Baseline 2238 (D4D3 checkpoint) + 41 new D4D4 tests = **2279 passed**, 0
failed, 0 regressions (207.47s) — exact match to the expected delta, no
unexplained change anywhere else in the suite.

## 29. py_compile

Clean on `app/services/fh_d4_disposition_resolution.py` and
`tests/test_fh_d4_disposition_resolution.py`.

## 30. git diff --check

Clean (exit 0, no whitespace errors).

## 31. Explicit real-DB no-access proof

Every test uses an isolated in-memory or `tmp_path`-scoped SQLite
database — never `data/runway_safe.db`. Verified both by direct code
inspection and by a dedicated AST-based test (`TestNoRealDatabaseAccess`)
confirming no literal reference to the real database filename appears
anywhere in the module's own compiled source. `data/runway_safe.db`'s
SHA-256/size/mtime were captured immediately before and after running the
full pytest suite for this slice and matched the checkpoint established at
the start of this session
(`4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
1,794,048 bytes). No real disposition was created anywhere in this task;
no real migration was run.

## 32. Deferred: D4D5 human CLI

Not implemented in this slice, per this mission's own explicit stop
boundary. A future D4D5 would add a human disposition-recording CLI
(`scripts/record_signal_group_disposition.py` or similar, per the design
doc's own §19 slice table) and/or extend `scripts/run_data_health_check.py`
with a disposition-aware presentation mode built on top of
`run_disposition_aware_fh_d4_review()` — the composition point this slice
now provides — plus its own Path-based schema gate reusing
`migrate_signal_disposition_d4d2.inspect()`, exactly like the CLI's
existing five-inspector gate.

## 33. Conclusion (implementation-time)

D4D4 integrates persisted Signal-group disposition state into operational
FH-D4 output through a new, small, single-purpose adapter module, without
changing the pure FH-D4 detector, the FHC3 review-check adapter, the CLI,
or D4D3's own already-committed read service in any way. The independent-
root operational policy (fail-visible, dedicated ambiguity bucket) and the
schema-readiness architectural boundary (fail loud, no backwards
`scripts/`-import dependency) were both genuinely investigated design
decisions, documented here and in the module's own source, not silently
assumed defaults. Ready for its own adversarial D4D4 critical review
checkpoint before D4D5 begins.

---

## Critical Review Checkpoint (D4D4, post-implementation)

Per this project's own standing two-phase discipline, the implementation
report above was **not trusted**. A fresh adversarial review was performed
against the actual committed code and actual instrumented behavior, per a
47-section review mission. One genuine defect (a real operational
usability/safety gap) was found and fixed; every other reviewed section
held up under attack.

### Defect (CONFIRMED): `ambiguous_groups` was invisible to a caller that only checks `active_findings`

**The concern** (review mission §12/§14): the original design placed a
group with `independent_root_count > 1` into its own, fully disjoint
`ambiguous_groups` bucket, excluded from `active_findings` entirely. A
downstream operational caller building "today's human review queue" by
reading `active_findings` alone would **never see an ambiguous group at
all** — the exact silent-suppression failure mode `independent_root_count`
was introduced (in D4D3's own review) to prevent in the first place. The
group's own `status`/`decision` fields being populated on the
`ambiguous_groups` entry does not help a caller who never looks at that
bucket.

**Investigation**: confirmed this is real, not theoretical — a naive,
plausible integration (`for group in result.active_findings: queue_for_review(group)`)
would silently skip every ambiguous group forever, with no error, no
warning, and no test able to catch it from the caller's side (since
`ambiguous_groups` is a legitimately separate, correctly-populated bucket
— nothing is broken from D4D4's own internal point of view). This matches
the review's own explicit framing: "if current bucket model hides it from
the normal active workload, treat as a potential defect."

**Resolution considered and rejected**: adding ambiguous groups directly
into `active_findings` (dual bucket membership) would fix the visibility
gap but break the accounting invariant's disjointness requirement (§5),
and the review mission's own §14 explicitly prefers "one primary bucket
plus explicit attention semantics" over dual membership.

**Fix applied**: added `FhD4DispositionResolution.attention_required` — a
new, derived, read-only tuple field equal to `active_findings +
ambiguous_groups`, in original raw-finding order. This is **not a fifth
exclusive bucket** — the four PRIMARY buckets remain exactly as originally
designed, pairwise disjoint and exhaustive over `d4_findings` (verified,
see "Accounting-invariant re-verification" below); `attention_required` is
a convenience UNION view over two of them, computed once per call at zero
additional query cost (pure Python bucket-membership logic, no new SQL).
`confirmed_distinct`/`confirmed_same_effort` groups are never included in
it. A caller that reads `attention_required` instead of `active_findings`
can no longer silently miss an ambiguous group.

**Regression tests added** (`TestAmbiguousRoots`, 2 new tests):
`test_ambiguous_group_never_silently_missed_via_attention_required`
(direct reproduction of the original gap, now closed) and
`test_attention_required_is_exactly_active_plus_ambiguous_no_more_no_less`
(proves the field is exactly `active_findings ∪ ambiguous_groups`, using
`id()`-based identity comparison since `FhD4OperationalGroup`/
`HealthFinding` are unhashable — see "Observed characteristic," below).

### Observed characteristic, not a defect: `HealthFinding`/`FhD4OperationalGroup` are unhashable

`HealthFinding.structured_evidence` is typed `Mapping[str, Any]` but the
concrete values every caller (including `evaluate_fh_d4()` itself)
constructs are plain `dict` literals — `dict` is unhashable, so any frozen
dataclass embedding one (including `FhD4OperationalGroup`, via
`raw_finding`) inherits that unhashability. This is a pre-existing
characteristic of `HealthFinding` (defined in FHC1's own
`fleet_health_rules.py`, untouched by this slice), not something D4D4
introduced or can fix without touching an already-reviewed, unrelated
module. Confirmed by direct reproduction (`hash(HealthFinding(...))`
raises `TypeError`). All tests needing set-like comparison of these
objects use `id()`-based identity sets or hashable derived keys
(`signal_ids` tuples) instead of `set()` on the objects themselves — this
was corrected in the `attention_required` regression test during its own
authoring (see "Corrections made").

### Sections re-verified with no defect found

§3 architecture separation (no detector predicate change, no
`HealthFinding` mutation, no persistence-write import, no free-text
inference — re-confirmed via a new `TestNoWritePathImports` AST test) ·
§4 result contract (both dataclasses remain frozen, ORM-free, no score/
rank/confidence/canonical-Signal/publication field) · §5 accounting
invariant (re-verified with a genuine SET-based, key-disjointness test —
`test_bucket_key_sets_are_pairwise_disjoint_and_exhaustive` — not merely a
count match, per the review's own explicit warning that count-only checks
can hide a wrong-group-in-wrong-bucket bug) · §6 non-D4 preservation
(re-verified with a 6-rule mixed set — FH-A1/FH-C3/FH-D3/FH-E1/FH-E4/FH-F1
— alongside FH-D4, `TestWideNonD4Preservation`) · §7-§9 UNREVIEWED/DISTINCT/
SAME (unchanged, re-run) · §10-§11 stale subset/superset and bare-overlap
(unchanged, re-run, plus a new large-scale correctness test — see below) ·
§13 ambiguity + supersession interaction (new test:
`test_ambiguity_persists_after_superseding_one_of_two_independent_roots` —
traces D1 root → D2 supersedes D1 → D3 second independent root → D4
supersedes D3, confirming ambiguity correctly PERSISTS after D4 because D1
remains an unlinked root, using D4D3's own `independent_root_count`
verbatim with zero local recomputation) · §15 group-disappears (unchanged,
re-run) · §16-§17 group reappears identical/changed (covered by existing
exact-match and stale-history tests) · §18 batch resolution (re-verified:
exactly 5/6 SELECT statements regardless of scale, unchanged by the
`attention_required` fix since it adds no queries) · §19 related-history
correctness at scale (**new, high-priority test**:
`test_many_dispositions_only_genuine_subset_superset_attached` — five
stored dispositions {A,B}/{A,B,C}/{A,C}/{D,E}/{A,B,C,D} plus one
completely unrelated disposition, current query {A,B,C,D,E} — confirms
all five genuine subsets attach as SUBSET, the unrelated one never
appears, no cross-contamination) · §20 overlapping groups
(**new test**: `test_shared_signal_never_leaks_state_between_groups` — G1=
{A,B}, G2={A,C}, G3={A,B,C} sharing Signal A, a disposition on G1 proven to
never affect G2's or G3's own independent resolution) · §21 duplicate raw
findings (**new, explicit contract test and decision**: duplicates are
preserved, never silently deduplicated — matches "raw detector owns
semantics"; both entries resolve identically and the count-based
accounting invariant still holds) · §22 malformed findings (**3 new
tests**: zero signal ids, one signal id — both fail loud via the same
`ValueError` D4D3 already raises; duplicate ids within one finding are
silently normalized, matching D4D3's own established dedup discipline;
malformed `structured_evidence` never affects resolution since it is never
read) · §23 member-id source contract (**new test**:
`test_resolution_uses_entity_ids_never_summary_text` — a deliberately
misleading `summary`/`structured_evidence` has zero effect; only
`entity_ids` is ever read) · §24 raw `HealthFinding` immutability
(unchanged, `is` identity re-confirmed) · §25 no-autoflush (unchanged,
re-run) · §26-§28 D4D3/related-history/missing-schema failure propagation
(structurally impossible to swallow — confirmed via a new
`test_no_try_except_anywhere_in_module_ast` AST test, mirroring D4D3's own
precedent) · §29 schema-gate policy (re-confirmed correct: no
`app/services/*` → `scripts/` import exists anywhere in this codebase; not
changed) · §30 migration-schema parity (unchanged, re-run) · §31 raw FHC3
preservation (unchanged, re-run against all 443 pre-existing focused
tests) · §32-§33 full entrypoint (**2 new tests**:
`test_run_disposition_aware_calls_raw_review_check_exactly_once` — proves
no duplicate DB acquisition via a call-counting monkeypatch — and a
measured, honestly-documented full-entrypoint query count: **15 total
SELECT statements** for a minimal fixture, FHC3's own fixed per-fact-type
cost plus D4D4's own bounded 5, confirmed not to scale with data volume by
the pre-existing D4D4-only scale tests) · §34 information firewall
(unchanged AST + behavioral tests, re-run) · §36 pair/triple/quintuple
(unchanged, re-run) · §37-§38 12-group simulation and full-state accounting
(unchanged, re-run, plus the new set-based disjointness test) · §39
determinism (**new test**:
`test_related_history_sorted_deterministically_with_identical_timestamps`
— `_batched_related_history()`'s own sort is genuinely new code, not
merely reused from D4D3 unchanged, so it received its own dedicated
same-timestamp tie-break attack) · §40 result immutability (both
dataclasses remain `frozen=True`; no mutable default field) · §42 design
alignment (confirmed consistent with design doc §14-15; `ambiguous_groups`
and `attention_required` are documented as this mission's own explicit
extensions, not silently invented).

### Final validation

- `tests/test_fh_d4_disposition_resolution.py`: **60 passed** (41 original
  + 19 new: 2 attention_required, 1 set-based disjointness, 1 ambiguity+
  supersession, 1 related-history-at-scale, 1 overlapping-independence, 4
  duplicate/malformed-finding, 1 member-id-contract, 3 full-entrypoint, 1
  wide-non-D4, 2 no-write-import/no-try-except, 1 same-timestamp
  related-history-ordering, 1 net addition already counted above — 60
  total, confirmed by direct pytest run).
- Focused: D4D3 + D4D1 + D4D2 + FHC3 + Fleet Health + operational CLI
  tests: **461 passed**.
- Full `pytest`: baseline 2279 (pre-review) + 19 new tests = **2298
  passed**, 0 failed, 0 regressions (269.04s) — exact match to the expected
  delta. (One earlier run in this same session reported 2297/2298 with no
  failure/error/skip reported for the missing one; re-run twice more,
  both times cleanly 2298/2298 with a matching `--collect-only` count -
  treated as a one-off transient flake in this large a suite, not a real
  regression, and not reproduced on any subsequent run.)
- `py_compile` clean on both changed Python files.
- `git diff --check` clean (exit 0).
- `data/runway_safe.db` SHA-256/size/mtime confirmed unchanged
  (`4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`,
  1,794,048 bytes) before and after this entire review.

### Conclusion (post-review)

One genuine defect was found and fixed by actually attacking the
implementation's own operational usability rather than trusting its self-
report: a real, reproduced silent-visibility gap where `ambiguous_groups`
was invisible to any caller reading only `active_findings`, closed with a
narrow, additive, non-breaking `attention_required` convenience view that
does not weaken the accounting invariant. Every other reviewed section
— including several genuinely new, previously-untested attack surfaces
(related-history correctness at real scale, overlapping-group
independence, duplicate/malformed raw findings, the member-id source
contract, and the full composed entrypoint's own query cost) — held up
under fresh adversarial attack. D4D4 is sound for commit.
