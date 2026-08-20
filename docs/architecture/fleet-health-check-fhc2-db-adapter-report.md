# Fleet Health Check — FHC2 Read-Only DB Adapter

Status: implementation complete, not committed/pushed (per this task's own
stop boundary). No database write of any kind occurred in this task; a real
DB read-only smoke test was performed and left the database byte-identical.

## 1. Starting HEAD

`c7eec79531959470f324d316f5428802968613e3`, local main == origin/main,
confirmed before any work began.

## 2. Adapter API

```python
build_fleet_hard_invariant_snapshot(session: Session) -> FleetHardInvariantSnapshot
run_fleet_hard_invariant_check(session: Session) -> tuple[HealthFinding, ...]
```

`run_fleet_hard_invariant_check()` is `build_fleet_hard_invariant_snapshot()`
followed by an unmodified call to FHC1's own `evaluate_hard_invariants()` —
no rule logic is duplicated or reimplemented anywhere in this module.

## 3. Snapshot Shape

Unmodified from FHC1: `FleetHardInvariantSnapshot`, a bundle of the same ten
narrow fact-tuple fields FHC1 already defined and reviewed. FHC2 populates
every field but adds no new fields, no ORM objects, no raw text, no
financial fields, no presentation fields — the snapshot remains exactly the
adapter boundary FHC1's own review established, not a second domain model.

## 4. Query/Cardinality Strategy

Ten queries total, always — never one query per entity, regardless of fleet
size (measured directly: **10 SQL statements** for fleets of 3, 30, and 100
entities alike):

| Fact | Query shape | Join type |
|---|---|---|
| `airport_codes` | `SELECT * FROM airports` | none |
| `runway_end_counts` | `Runway LEFT JOIN RunwayEnd ... GROUP BY Runway.id` | aggregate (one-to-many, deliberately collapsed by GROUP BY) |
| `runway_designations` | `SELECT * FROM runways` | none |
| `installation_years` | `SELECT * FROM installations` | none |
| `installation_runway_airports` | `Installation LEFT JOIN Runway` | to-one FK |
| `physical_installation_identity_airports` | `PhysicalInstallationIdentity LEFT JOIN Runway, LEFT JOIN RunwayEnd LEFT JOIN Runway (aliased)` | two independent to-one FKs |
| `signal_runway_airports` | `Signal LEFT JOIN Runway` | to-one FK |
| `source_assertion_signal_airports` | `SourceAssertion INNER JOIN Signal WHERE signal_id IS NOT NULL` | to-one FK, pre-filtered |
| `signal_construction_dates` | `SELECT * FROM signals` | none |
| `source_assertion_governance` | (1) `SELECT * FROM reviewer_actions ORDER BY ...` (2) `SELECT id, signal_id FROM source_assertions WHERE id IN (...)` — only executes the second query if any ReviewerAction exists at all | none / bounded IN |

Every join FHC2 performs is on a **to-one** foreign key (an FK column
directly on the "left" table pointing at the "right" table's primary key).
A to-one join can never multiply rows on its own left side — this is why
none of these nine non-aggregate queries can ever fan out, by construction,
regardless of how many *other*, unrelated one-to-many relationships an
entity happens to participate in. The one truly one-to-many relationship
this module reads (RunwayEnd per Runway) is handled with `GROUP BY`, which
collapses it back to exactly one row per Runway — the count FH-B1 needs, not
a source of duplication.

## 5. Dedup Strategy

FHC2's own queries are dedup-safe by construction (§4), so no explicit
Python-side deduplication was needed in the adapter itself. This is **not**
treated as a substitute for FHC1's own defensive `_dedupe_preserve_order()`
(added at the FHC1 review checkpoint) — that remains a second, independent
line of defense should a future maintenance change to this adapter ever
introduce an accidental fan-out. Both layers were verified independently
(§14).

## 6. Rule-to-Query Mapping (all 11 rules)

| Rule | Fact | Adapter function |
|---|---|---|
| FH-A2 | `AirportCodeFact` | `_build_airport_codes` |
| FH-B1 | `RunwayEndCountFact` | `_build_runway_end_counts` |
| FH-B2 | `RunwayDesignationFact` | `_build_runway_designations` |
| FH-C1 | `InstallationYearFact` | `_build_installation_years` |
| FH-C2 | `InstallationRunwayAirportFact` | `_build_installation_runway_airports` |
| FH-C5 | `PhysicalInstallationIdentityAirportFact` | `_build_physical_installation_identity_airports` |
| FH-D1 | `SignalRunwayAirportFact` | `_build_signal_runway_airports` |
| FH-D2 | `SourceAssertionSignalAirportFact` | `_build_source_assertion_signal_airports` |
| FH-E3 | `SignalConstructionDateFact` | `_build_signal_construction_dates` |
| FH-G2 / FH-G3 | `SourceAssertionGovernanceFact` (shared) | `_build_source_assertion_governance` |

## 7. NULL/Legacy Handling

- An airport with zero `Runway` rows contributes nothing to
  `runway_end_counts`/`runway_designations` — never fabricated.
- An `Installation`/`PhysicalInstallationIdentity`/`Signal` with no
  `runway_id`/`runway_end_id` link produces a fact with `runway_id=None`,
  `runway_airport_id=None` (or the RunwayEnd equivalent) — FHC1's own rules
  already treat this as absence, not contradiction; FHC2 never substitutes a
  guessed value.
- `source_assertion_signal_airports` is built **only** for SourceAssertions
  with a non-NULL `signal_id` — the ~221 of 222 real rows with no governed
  link produce no fact at all, not a vacuously-passing one, matching the
  mission's explicit "do not fabricate a comparison" instruction.
- `source_assertion_governance` is built **only** for SourceAssertions with
  at least one recorded `ReviewerAction` — the ~220 real rows that have
  never been reviewed produce no fact at all (FH-G2/FH-G3 could never fire
  for `latest_action=None` regardless, so this is a pure efficiency/scope
  narrowing, not a behavior change).
- NULL vs. empty-string airport codes are preserved exactly as stored — a
  dedicated test (`test_null_vs_empty_string_airport_codes_preserved`)
  confirms `icao_code=""` is never coerced to `None` or vice versa.

## 8. Latest ReviewerAction Strategy

`_latest_reviewer_action_by_assertion_id()` fetches every `ReviewerAction`
row in one query, ordered by `(source_assertion_id, created_at DESC, id
DESC)` — the exact tiebreak
`app.services.reviewer_action_persistence.get_latest_reviewer_action()` and
`app.services.human_review_queue._latest_actions_by_assertion()` already
use — then keeps the first (= latest) row per `source_assertion_id` in
Python. Never chain-walks `supersedes_action_id`. Verified directly against
`get_latest_reviewer_action()` for every fixture assertion
(`TestLatestReviewerActionEquivalence`), including the case where an
assertion has **three** ReviewerActions recorded (a join/history fan-out
attack) — the batched result matches the per-assertion helper's own result
exactly.

## 9. Query-Count Behavior

Measured directly with a `before_cursor_execute` SQLAlchemy event counter:
**exactly 10 SQL statements**, identical for fleets of 3, 30, and 100
entities. No N+1 behavior anywhere in this adapter.

## 10. Determinism

Every fact-building query orders by its own primary entity id ascending
(`Airport.id.asc()`, `Runway.id.asc()`, etc.); `source_assertion_governance`
additionally sorts its bounded `assertion_ids` list before building facts.
Verified: two `build_fleet_hard_invariant_snapshot()` calls on the same
session produce `==`-equal snapshots; two independently-built databases
containing the same rows inserted in **opposite order** (airport 2 then 1,
vs. 1 then 2) produce identical `run_fleet_hard_invariant_check()` output.

## 11. Read-Only Guarantees

Verified both structurally and behaviorally:

- AST-checked: no `add`/`flush`/`commit`/`delete`/`add_all`/`merge`/
  `bulk_save_objects` identifier anywhere in the module.
- Behavioral: running the full check against a populated database leaves
  every table's row count unchanged, and leaves `session.dirty`/`session.new`/
  `session.deleted` all empty (no pending ORM-tracked changes of any kind).
- Real-DB smoke test (§18): SHA-256/size/mtime identical before and after.

Schema readiness is explicitly **not** this module's concern (§18 below) —
consistent with every other read-only service in this pipeline
(`reviewer_action_persistence.py`, `human_review_queue.py`,
`existing_signal_reconciliation_candidates.py`, none of which self-check
schema readiness either); a future FHC3 CLI owns that gate, mirroring
R4D/R4E's own established pattern.

## 12. Healthy DB Fixture

A synthetic multi-country healthy fleet (Sweden, Japan, plus a deliberate
zero-runway reference-only airport) was built via real SQLAlchemy ORM
inserts into an isolated, disposable SQLite database and run through the
real adapter + unmodified FHC1 evaluator: **zero findings**, stable across
repeated calls.

## 13. Per-Rule DB-Backed Violations

All 11 rules were independently exercised through real DB rows and the real
adapter (not hand-built fact dataclasses) — each producing exactly the
intended `rule_id`, `entity_ids`, and `structured_evidence`, verified by
direct assertion on all three (not merely a finding count). A combined
fixture violating all 11 simultaneously was also run, confirming
`{finding.rule_id for finding in findings} == set(RULE_IDS)` exactly.

## 14. JOIN Fan-Out Attacks

Five dedicated adversarial fixtures, each constructing a genuine one-to-many
relational shape and confirming the adapter still produces the logically
correct fact count:

- A Runway with **3** RunwayEnd rows (a real FH-B1 violation) collapses to
  **one** `RunwayEndCountFact` with `runway_end_count=3` — not three facts.
- **3** SourceAssertions linked to the same Signal correctly produce
  **3** independent `SourceAssertionSignalAirportFact` rows (genuinely 3
  real facts, not a bug) while the Signal's own `SignalRunwayAirportFact`
  stays at exactly **1** — proving D2's fan-out (correct, expected) never
  leaks into D1 (which never joins through SourceAssertion at all).
- **3** ReviewerActions on one SourceAssertion (APPROVE_SIGNAL, DEFER,
  APPROVE_SIGNAL again) reduce to exactly **1** governance fact carrying the
  truly latest action (highest id).
- **3** Installation rows at one airport, none runway-linked, produce 3
  independent facts and **zero** findings (not a fabricated duplicate
  error — that's FHC3/FH-C3 territory).
- A `PhysicalInstallationIdentity` with both links absent produces exactly
  **1** fact and zero findings (the real, deliberate-by-design state for
  every one of the 10 real rows).

## 15. Legacy Realistic Fixture

Built a fixture combining every legacy characteristic named in the mission:
zero-runway airport, multiple airport-only Installation rows, a legacy
Signal with `source_id` set but no supporting SourceAssertion and free-text
`category`/`confidence` values, and an unreviewed SourceAssertion with both
`airport_id` and `signal_id` NULL. Result: **zero findings**, confirming
none of these real, common legacy shapes is ever mistaken for a hard
invariant violation.

## 16. Governed Fixture

Built a synthetic #222/#67-shaped fixture (no provider-specific constants
anywhere: no "FAA", "MSP", "MAC", "Runway Safe", or "USAspending" string
appears in the adapter module — verified by direct test) — a governed
SourceAssertion, its linked Signal, an `APPROVE_SIGNAL` then a
`MARK_DUPLICATE` action whose `duplicate_of_signal_id` matches the
assertion's own `signal_id`: **zero findings**. Breaking the target/link
match (`duplicate_of_signal_id` pointing at a different Signal) produces
exactly one **FH-G2** finding; breaking a terminal-action/link consistency
(`REJECT_SIGNAL` recorded while `signal_id` stays set) produces exactly one
**FH-G3** finding.

## 17. International Fixture

A synthetic Sweden/Japan/UK fleet (Arlanda/Narita/Heathrow, ARN/NRT/LHR,
ESSA/RJAA/EGLL) runs through the adapter identically to the US-shaped
fixtures — zero findings for a healthy shape, and the module source itself
contains no FAA/USAspending/USD/vendor-specific string (verified by test).

## 18. Real DB Smoke Result

Performed, read-only, after all synthetic tests passed:

- Pre-check hash: SHA-256 `4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`, size 1,794,048 bytes, mtime `1787237717.1444063` — matched the expected checkpoint exactly.
- Opened via the established `mode=ro` SQLite URI pattern (same as
  `scripts/list_human_review_queue.py`/`scripts/review_reconciliation_item.py`).
- `run_fleet_hard_invariant_check()` against the real database: **0
  DETERMINISTIC_ERROR findings** — matches the reconnaissance task's own
  prediction exactly.
- Snapshot fact counts, confirmed against known real values: 86
  `airport_codes`, 180 `runway_end_counts`/`runway_designations`, 149
  `installation_years`/`installation_runway_airports`, 10
  `physical_installation_identity_airports`, 68
  `signal_runway_airports`/`signal_construction_dates`, **1**
  `source_assertion_signal_airports` (the single real governed link,
  SourceAssertion #222 → Signal #67), **1** `source_assertion_governance`
  fact (the single assertion with any ReviewerAction history).
- Post-check hash: SHA-256/size/mtime **identical** to pre-check — proven
  byte-for-byte.

## 19. Focused Tests

`tests/test_fleet_health_check.py`: **36 passed**, 0 failed.
`tests/test_fleet_health_rules.py` (FHC1, re-run unmodified): **116
passed**, 0 failed. Combined: **152 passed**.

## 20. Full Pytest

**1818 passed**, 0 failed (1782 pre-existing baseline + 36 new FHC2 tests —
matches exactly, no regressions, no change to FHC1).

## 21. py_compile

Clean on both new files (`app/services/fleet_health_check.py`,
`tests/test_fleet_health_check.py`).

## 22. git diff --check

Exit 0, clean.

## 23. Exact Files Changed

New only:
- `app/services/fleet_health_check.py`
- `tests/test_fleet_health_check.py`
- `docs/architecture/fleet-health-check-fhc2-db-adapter-report.md` (this file)

No existing production file was modified. `app/services/fleet_health_rules.py`
(FHC1) was imported, never edited.

## 24. Defects/Corrections

None found in FHC1 during this task (re-run unmodified, all 116 tests still
pass). Test-fixture-only issues were found and fixed in this task's **own**
new test file before being reported as passing: five of the initial
per-rule DB-backed violation tests created a `Runway` row without its two
`RunwayEnd` rows, which correctly and independently triggered a genuine
FH-B1 finding alongside the intended rule — not an adapter defect, but an
incomplete test fixture. Fixed by adding the two `RunwayEnd` rows every
`Runway` fixture needs to stay FH-B1-clean, isolating each test to its
single intended violation as designed.

## 25. READY_FOR_FHC2_REVIEW_CHECKPOINT

**Yes.**

## 26. Exact Next Recommendation

A fresh adversarial review checkpoint of this FHC2 adapter (per the
project's established two-phase pattern), covering: (1) whether every join's
"cannot fan out" claim in §4 actually holds under further adversarial
construction; (2) whether the NULL/legacy-scoping decisions in §7 exactly
match FHC1's own documented input contracts; (3) whether the query-count
bound in §9 holds for a fixture that specifically stresses the governance
IN-query path; (4) re-verification of the real-DB smoke result. Only after
that checkpoint should a CLI (`scripts/run_data_health_check.py`, explicitly
out of scope for FHC2 per this mission) be authorized as a new, separate
slice.

---

## Critical Review Checkpoint (RWI_FLEET_HEALTH_CHECK_FHC2_CRITICAL_REVIEW_COMMIT_PUSH)

A fresh, adversarial review of this implementation was performed
independently of the report above — every claim re-verified against the
actual code and against fresh, direct probes, not trusted from the
report's own prose.

### Architecture verification

Confirmed sound: FHC2 contains zero health-rule logic (every finding still
originates exclusively from FHC1's unmodified `evaluate_hard_invariants()`);
the API stays narrow (`build_fleet_hard_invariant_snapshot(session)`,
`run_fleet_hard_invariant_check(session)`), the caller owns the `Session`
and its transaction, no DB path is baked in, no query knobs exist beyond
what the 11 rules require. Re-verified every one of the 10 queries'
cardinality claims individually (root entity, join type, fan-out
possibility, dedup key) — all confirmed correct: nine of the ten queries
join exclusively on to-one foreign keys or a joined table's own primary key
(both structurally incapable of multiplying rows on the query's driving
side), and the tenth (`runway_end_counts`) deliberately uses `GROUP BY` to
collapse the one genuinely one-to-many relationship in this rule set
(RunwayEnd per Runway) back to one row per Runway.

### Cardinality/fan-out findings

All six mission-named attacks (A–F) were freshly constructed and verified,
including one the original test suite had **not** actually covered:
`PhysicalInstallationIdentity` targeted by **three** independent, append-only
`InstallationAssertionLink` rows — confirmed the FH-C5 query never touches
that table at all and still produces exactly one fact. Added as a permanent
regression test (`test_multiple_installation_assertion_links_do_not_fanout_c5`).
Every other named attack (multiple SourceAssertions on one Signal, multiple
ReviewerActions on one assertion, two RunwayEnds on one Runway, multiple
Installations at one airport, insertion-order permutations) was already
covered by the implementation's own test suite and re-verified fresh —
no fan-out found anywhere.

### Transaction/autoflush result — **genuine defect found and fixed**

This was the most important finding of this review. SQLAlchemy's default
`autoflush=True` meant calling `run_fleet_hard_invariant_check(session)` (or
`build_fleet_hard_invariant_snapshot(session)`) would silently flush ANY of
the caller's own pending, uncommitted ORM changes to the database the
instant this module's first `SELECT` ran — a real, demonstrated hidden-write
side effect of a function whose entire contract is "read-only." Verified
directly before any fix: a caller's dirty, unflushed `Airport.name` edit was
pushed to the database via a genuine `UPDATE` statement, visible to a raw
SQL read within the same uncommitted transaction, merely by calling the
"read-only" health check. This is exactly the class of defect this
project's entire safety discipline (R1–R4E, FHC1's own purity mandate)
exists to prevent, and it directly contradicts this module's own explicit
"No `session.add()`... no attribute assignment... anywhere" claim — the
attribute assignment wasn't made by this module, but this module's read
caused someone else's pending assignment to be persisted regardless, which
is the same practical harm.

**Fix**: `build_fleet_hard_invariant_snapshot()` now wraps its entire body in
`with session.no_autoflush:`, suppressing autoflush for the duration of this
one call only — it never changes the session's own configured autoflush
setting, and never itself flushes, commits, or discards the caller's pending
changes. Verified the fix: the caller's pending edit remains exactly pending
(still in `session.dirty`) after the call, the raw on-disk value is
untouched during the call, and — importantly — the health check still
correctly *reads* the caller's current in-session state (including their
pending edit, via the normal Session identity-map/unit-of-work semantic) —
it only stops the surprise *write*, it does not make the read stale or
blind to the caller's own pending work.

### Query-count correction (not a defect, but the prior claim was wrong)

The implementation report claimed "exactly 10 SQL statements, always."
Re-instrumented directly against the real database: **11** statements were
observed, not 10. Root cause: `_build_source_assertion_governance()` issues
a second, bounded query (the signal_id lookup) only when at least one
`ReviewerAction` exists anywhere in the database — the original query-count
test's synthetic fixture happened to contain zero `ReviewerAction` rows, so
it only ever exercised the 10-statement short-circuit path. The real
database has 2 `ReviewerAction` rows, so it takes the 11-statement path.
Both counts are correct, intentional, and bounded (neither is proportional
to entity count) — this was a **test-coverage gap in the original report's
own verification**, not a functional defect. Corrected: the query-count test
now covers both paths explicitly, at two different entity-count scales
each, proving `10` with zero `ReviewerAction` rows and `11` with at least
one, in both cases independent of fleet size.

### Query-failure verdict

Confirmed sound, both by source inspection (zero `try`/`except` blocks
anywhere in the module — a query failure can only ever propagate as a raw,
unmodified exception) and by direct construction: dropping a required table
before calling `run_fleet_hard_invariant_check()` raises a clear
`sqlite3.OperationalError` / SQLAlchemy `OperationalError`, never a silently
swallowed "zero findings" result. Added as a permanent regression test.

### Schema-mismatch verdict

Confirmed sound and unchanged from the report: this module correctly
assumes an ORM-compatible schema and performs no auto-migration of any
kind; a missing table/column fails loud via the same mechanism as
query-failure above. Schema-readiness gating remains, as documented,
explicitly out of scope for FHC2 and deferred to a future FHC3 CLI.

### Per-rule / combined / legacy / governed / international fixtures

All re-read and re-verified fresh against the actual test file (not
trusted from the report): every per-rule DB-backed test asserts `rule_id`,
`entity_ids`, and `structured_evidence`, not merely a finding count; the
combined 11-violation fixture produces exactly the 11 intended rule IDs
with no duplicates; the legacy and governed fixtures both behave exactly as
documented; the international fixture and a direct source-text check
confirm no FAA/USAspending/USD/vendor-specific string exists anywhere in
the module.

### Real DB smoke result (re-run)

Performed again, read-only, after all fixes: pre-check hash
`4aa8c25fe8ce299463a9b5bd707590d91520c14f76b05291695d52603ee71965`
(1,794,048 bytes) — unchanged from the original checkpoint. **0
DETERMINISTIC_ERROR findings.** Snapshot counts independently re-confirmed
and cross-checked against known real values: 86 airports, 180
runway/runway-designation facts, 149 installation-year/installation-runway
facts, 10 physical-installation-identity facts, 68 signal-runway/signal-date
facts, 1 source-assertion-signal fact (the real #222→#67 governed link), 1
source-assertion-governance fact (the real assertion with any ReviewerAction
history). **Real DB query count: 11** (not 10 — see the query-count
correction above; the real database has 2 ReviewerActions, so it correctly
takes the 11-statement path). Post-check hash identical to pre-check —
proven byte-for-byte, both before and after this review's own additional
real-DB probing.

### No-CLI/FHC3-leakage verdict

Confirmed: no `argparse`, no `click`/`typer`, no database-path handling, no
print/report logic, no warning/review-tier rule anywhere in
`fleet_health_check.py`.

### Test-quality findings

Three real gaps found in the original 36-test suite, all fixed with
permanent regression tests (bringing the suite to 42 tests):

1. **No autoflush test existed at all** — this is exactly how the real
   defect above went undetected during implementation. Added two tests:
   one proving a caller's pending edit is never flushed, one proving the
   read still correctly reflects the caller's current in-session state.
2. **The mission-named InstallationAssertionLink fan-out attack was never
   actually constructed** — the original suite attacked every other named
   relationship but not this one. Added.
3. **Latest-action equivalence was only ever tested with distinct, implicit
   timestamps** — the `(created_at DESC, id DESC)` tiebreak's second clause
   was never actually stressed. Added a test with two ReviewerActions
   sharing an identical, explicit `created_at`, confirming the batched
   result matches `get_latest_reviewer_action()` exactly (id DESC breaks
   the tie in both).

Additionally added a direct query-failure regression test (see above) that
the original suite also lacked.

### Defects found

One genuine production defect (autoflush hidden-write side effect, above).
One documentation/test-coverage error (the "always 10" query-count claim,
above) — not a functional defect, but a real correction to an inaccurate
claim.

### Corrections made

`build_fleet_hard_invariant_snapshot()` wrapped in `session.no_autoflush`.
Query-count test split into two explicit scenarios (with/without any
`ReviewerAction`), each verified at two entity-count scales. Both corrected
in the production module and the test suite respectively; this report
updated to state the true, re-verified behavior rather than repeat the
original claim.

### Regression tests added

7 new tests: `test_run_never_autoflushes_callers_pending_mutation`,
`test_run_reflects_callers_own_pending_in_session_state`,
`test_query_failure_raises_clear_exception_not_empty_result`,
`test_multiple_installation_assertion_links_do_not_fanout_c5`,
`test_identical_created_at_timestamps_break_tie_by_id_matching_helper_exactly`,
`test_bounded_query_count_without_any_reviewer_action`,
`test_bounded_query_count_with_reviewer_action_present` (the last replacing
the original single, incomplete query-count test).

### Final totals

`tests/test_fleet_health_check.py`: **42 passed** (36 original + 7 new − 1
replaced), 0 failed. `tests/test_fleet_health_rules.py` (FHC1, re-run
unmodified): **116 passed**. Combined with `test_reviewer_action_persistence.py`
and `test_human_review_queue.py` (batching-adjacent, re-run since the
latest-action logic was scrutinized): **277 passed** together. Full suite:
**1824 passed**, 0 failed (1818 pre-review total + 6 net new tests: 7 added
− 1 replaced), 0 regressions anywhere, including in FHC1.

### READY_FOR_FHC3_OR_CLI

**Yes**, with the autoflush defect now fixed and verified — this was the
one finding that would have made FHC2 unsafe to build a CLI or any other
caller on top of without discovering it independently later, likely inside
a real caller's own transaction.

RWI_FLEET_HEALTH_CHECK_FHC2_READ_ONLY_DB_ADAPTER_COMPLETE
