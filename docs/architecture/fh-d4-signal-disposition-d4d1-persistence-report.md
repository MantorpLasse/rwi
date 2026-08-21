# FH-D4 Signal Disposition — D4D1: Persistence Foundation — Report

D4D1 of [fh-d4-signal-disposition-design.md](fh-d4-signal-disposition-design.md)
(§19 "Recommended implementation slices"). This slice implements ONLY the
persistence foundation the design document describes: models, the
persistence service, and fixture-only tests. No migration, no Fleet Health
integration, no CLI, no real database access of any kind.

## 1. Scope discipline

Per this slice's own explicit boundary, this implementation does **not**:
create a migration; touch `data/runway_safe.db` (read or write); integrate
with `app/services/fleet_health_review_rules.py` or
`app/services/fleet_health_check.py`; add a human-facing CLI; change any
`Signal.published` value; merge or delete any Signal; move any provenance
FK; or select a canonical Signal. Verified explicitly — see §9.

## 2. Files read fresh

`docs/architecture/fh-d4-signal-disposition-design.md` (authoritative over
this mission's own prose per its own instruction), `app/models/reviewer_action.py`,
`app/models/signal.py`, `app/models/source_assertion.py`,
`app/services/reviewer_action_persistence.py`,
`app/services/governed_signal_creation.py`, `app/models/__init__.py`,
`tests/test_reviewer_action_persistence.py`.

## 3. Files created

- `app/models/signal_disposition.py` — `SignalDisposition`,
  `SignalDispositionMember` models.
- `app/services/signal_disposition_persistence.py` —
  `record_signal_group_disposition()`.
- `tests/test_signal_disposition_persistence.py` — 55 tests, fixture-only.
- `docs/architecture/fh-d4-signal-disposition-d4d1-persistence-report.md`
  (this file).

## 4. Files modified

- `app/models/__init__.py` — two new imports/`__all__` entries
  (`SignalDisposition`, `SignalDispositionMember`), alphabetically placed;
  no other line changed.
- `tests/test_model_contract.py` — extended with entries for the two new
  tables (`EXPECTED_COLUMNS`, `EXPECTED_FOREIGN_KEYS`, `EXPECTED_INDEXES`,
  `EXPECTED_RELATIONSHIPS`) and the two new model names added to the
  existing `test_all_current_models_are_exported_from_app_models` literal
  lists — this pre-existing snapshot test would otherwise permanently fail
  the moment a legitimate new table is registered; this is the exact same
  kind of update the R4B slice made to this same file when it added one new
  column (see `existing-signal-reconciliation-r4b-reviewer-action-report.md`
  §12: "one column added to the `reviewer_actions` snapshot"). Every other
  table's snapshot entry is byte-for-byte unchanged (verified: 284 focused
  tests including this file's own remaining assertions all pass unmodified).

No other existing file was touched.

## 5. SignalDisposition model shape

```python
class SignalDisposition(Base):
    __tablename__ = "signal_dispositions"
    id: int (PK)
    decision: str  # VARCHAR(30), CHECK IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')
    reason: str  # TEXT, NOT NULL
    reviewer: str  # VARCHAR(100), NOT NULL
    created_at: datetime  # DATETIME, NOT NULL, default now(UTC)
    supersedes_id: Optional[int]  # FK -> signal_dispositions.id, nullable, indexed
```

Relationships: `supersedes` (self, `remote_side`), `members` (one-to-many to
`SignalDispositionMember`, `passive_deletes=True`).

## 6. SignalDispositionMember model shape

```python
class SignalDispositionMember(Base):
    __tablename__ = "signal_disposition_members"
    id: int (PK)
    disposition_id: int  # FK -> signal_dispositions.id, indexed
    signal_id: int  # FK -> signals.id, indexed
    __table_args__ = (UniqueConstraint("disposition_id", "signal_id"),)
```

Relationships: `disposition` (back-populates `members`), `signal` (plain
read reference, no back-populate on `Signal` — mirrors
`ReviewerAction.duplicate_of_signal` exactly, which also has no
`Signal`-side back-reference).

Neither model has `canonical_signal_id`, `confidence`, `score`, `ranking`,
`publication`/`published`, `resolution_state`, `fingerprint`, raw evidence,
or any provider/vendor field — verified directly
(`TestModelShape::test_no_forbidden_columns_anywhere`).

## 7. DB constraints (implemented exactly as designed, §9 of the design doc)

| Constraint | Mechanism | Verified by |
|---|---|---|
| `decision IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')` | DB `CHECK` | `test_invalid_decision_rejected_at_db_level_bypassing_service` (direct ORM construction, bypassing the service) |
| `signal_id` references a real Signal | DB `FOREIGN KEY`, no `ON DELETE` clause | `test_member_signal_fk_enforced_at_db_level` (with `PRAGMA foreign_keys=ON`) |
| `supersedes_id` references a real disposition | DB `FOREIGN KEY` | `test_supersedes_fk_enforced_at_db_level` |
| No duplicate member within one disposition | DB `UNIQUE(disposition_id, signal_id)` | `test_duplicate_member_row_rejected_at_db_level` |
| Exact-set supersession match | **Service-level only** (no cross-table set-equality CHECK is expressible in SQLite) | full `TestSupersession` class, §13 |
| Minimum cardinality ≥ 2 | **Service-level only** | `TestValidation`, §10 |
| `reviewer`/`reason` non-blank | **Service-level only**, `.strip()` | `TestValidation` |
| Signal deletion blocked once it is a disposition member | DB `FOREIGN KEY`, `passive_deletes=True` on the relationship, no cascade | `test_deleting_a_member_signal_fails_safely_with_fk_enforced` |

No SQLite trigger was introduced for the two service-level-only invariants,
matching the design doc's own explicit instruction (§6) — this project has
never used custom SQL triggers, only CHECK constraints and ORM
`before_update`/`before_delete` event listeners.

## 8. Immutability implementation

Four event listeners, exact structural mirror of `ReviewerAction`'s own two
(`before_update`/`before_delete`, each raising `ValueError`):
`_prevent_signal_disposition_update`, `_prevent_signal_disposition_delete`,
`_prevent_signal_disposition_member_update`,
`_prevent_signal_disposition_member_delete`. Member rows are exactly as
immutable as their header — a mutable child under an immutable header would
be a hole in the guarantee. Verified behaviorally for both models, both
operations (`TestImmutability`, 4 tests).

## 9. Persistence API

```python
def record_signal_group_disposition(
    session: Session, *, signal_ids: Sequence[int], decision: str,
    reviewer: str, reason: str, supersedes_id: Optional[int] = None,
) -> SignalDisposition
```

Implemented exactly per the design doc's §12 contract — no deviation. Order
of validation: decision vocabulary → reviewer/reason non-blank → dedup/sort
→ minimum cardinality → per-id existence → supersession existence +
exact-set match → insert header (flush for id) → insert members (flush).

## 10. Validation behavior

- `decision` not in `("DISTINCT", "SAME_REAL_WORLD_EFFORT")` → `ValueError`.
- Blank or whitespace-only `reviewer`/`reason` → `ValueError` (`.strip()`
  check, matching `record_reviewer_action()`'s own identical convention —
  confirmed this project's established convention rejects whitespace-only
  values, per the mission's own "unless established project convention
  clearly says otherwise" instruction).
- Fewer than 2 distinct signal ids (including an empty list) → `ValueError`.
- Any signal id that does not resolve via `session.get(Signal, id)` →
  `ValueError` naming the missing id.
- Both `reviewer` and `reason` are stored `.strip()`ped, not verbatim (a
  caller-supplied `"  human:reviewer  "` is stored as `"human:reviewer"`).

## 11. Duplicate-id behavior

Caller-supplied `signal_ids` is deduplicated and sorted
(`tuple(sorted(set(signal_ids)))`) before any validation or persistence.
`[5, 2, 5, 3, 2]` persists exactly 3 member rows for the set `{2, 3, 5}` —
verified directly (`test_duplicate_caller_input_normalized_to_exact_member_set`).
Input order never affects the stored member set
(`test_input_order_does_not_affect_stored_member_set`).

## 12. Pair/triple/quintuple results

| Group size | Real synthetic example | Header rows created | Member rows created |
|---|---|---|---|
| 2 | `TestGoldenPath::test_pair` | 1 | 2 |
| 3 | `TestGoldenPath::test_triple` | 1 | 3 |
| 5 | `TestGoldenPath::test_quintuple` | 1 | 5 |

All three verified by direct row-count and member-id-set assertions,
matching the design doc's own §17 simulation exactly.

## 13. Supersession success behavior

`old (41,67) -> new (41,67)`: **allowed**
(`test_same_exact_set_supersession_succeeds`,
`test_same_set_different_order_still_supersedes` — set comparison is
order-independent). `d2.supersedes_id == d1.id`; both rows remain queryable;
`d1` is verified byte/field-unchanged after `d2` is recorded
(`test_supersession_does_not_mutate_previous_disposition`); `d1`'s member
rows are verified unchanged in id/disposition_id/signal_id
(`test_previous_members_remain_untouched_after_supersession`).

## 14. Supersession rejection matrix

Using synthetic ids (never the real 41/67/80), exactly the four shapes the
mission specified:

| Old set | New set | Result |
|---|---|---|
| `{41, 67}` | `{41, 67, 80}` | **Rejected** — `test_grown_group_rejected_as_supersession` |
| `{41, 67, 80}` | `{41, 67}` | **Rejected** — `test_shrunk_group_rejected_as_supersession` |
| `{41, 67}` | `{41, 80}` | **Rejected** — `test_different_same_size_group_rejected_as_supersession` |
| `{41, 67}` | `{41, 67}` | **Allowed** (already shown above) |

Every rejection raises `ValueError` matching `"exact same member Signal-id
set"`, and leaves zero pending state
(`test_supersession_mismatch_leaves_no_pending_state`).
`supersedes_id` pointing to a nonexistent disposition is also rejected
(`test_supersedes_id_must_reference_existing_disposition`).

## 15. Append-only history proof

`TestAppendOnlyHistory::test_full_history_chain_both_entries_remain_queryable`
constructs `D1 DISTINCT` then `D2 SAME_REAL_WORLD_EFFORT` (superseding `D1`,
same member set), then re-fetches both by id after both commits:
`D1.decision == "DISTINCT"` and `D2.decision == "SAME_REAL_WORLD_EFFORT"`
still hold, `D2.supersedes_id == D1.id`, and `session.query(SignalDisposition).count()
== 2`. No "latest disposition" resolution logic exists anywhere in this
slice (deferred to D4D3 per the design doc and this mission's own explicit
instruction) — `record_signal_group_disposition()` has no read/query
helper beyond the one internal `_member_signal_ids()` used for its own
supersession-set comparison.

## 16. Transaction/non-commit verdict

**Confirmed.** `session.rollback()` after a successful call removes every
pending row (`test_record_signal_group_disposition_never_commits`). AST
scan proves zero `.commit()` calls anywhere in the service module
(`test_service_module_never_calls_session_commit_ast`). AST scan also
proves no `create_engine`/`SessionLocal` identifier is referenced anywhere
in the module's actual code (`test_service_does_not_construct_its_own_engine_or_session`
— deliberately AST-based, not a raw substring scan, since the module's own
docstring legitimately names `SessionLocal` in prose to explain what it
does *not* do). The service never backs up or migrates anything — no such
code exists in the module at all.

## 17. Failure atomicity verdict

Two classes of failure, precisely distinguished (no overclaiming):

1. **Validation failures** (invalid member Signal, decision, blank
   reviewer/reason, supersession mismatch) happen entirely before any
   `session.add()` call — `session.new` is empty afterward, and the
   database has zero new rows, pending or otherwise
   (`test_invalid_member_signal_leaves_no_pending_state`,
   `test_supersession_mismatch_leaves_no_pending_state`).
2. **A failure between the header flush and the member flush** (the two
   separate `session.flush()` calls this two-step header+children design
   requires — the child rows need the header's real, DB-assigned id first)
   is a genuinely different case: the header row IS pending, flushed, and
   visible within the same uncommitted transaction at the moment of
   failure (`test_forced_failure_between_header_and_member_flush_is_recoverable_by_caller_rollback`,
   which forces this exact window via a monkeypatched first member
   construction). This is **not** silently durable — nothing is committed
   unless the caller calls `session.commit()`. A caller that instead rolls
   back (the documented, expected recovery path, matching
   `governed_signal_creation.py`'s own identical two-step
   Signal-then-`signal_id`-link precedent, whose own docstring states "if
   anything fails, a caller rollback leaves neither a Signal nor a link")
   ends up with zero rows of either kind, verified directly. The service's
   own guarantee ends at "flush only, never commit"; full atomicity across
   the header/member boundary is the **caller's** responsibility via
   rollback, not something this non-committing service can unilaterally
   provide on its own — this distinction is proven precisely, not assumed.

## 18. Signal immutability proof

`TestSignalSafety::test_signals_unchanged_after_disposition` snapshots
every relevant column (`airport_id`, `runway_id`, `source_id`, `title`,
`category`, `confidence`, `status`, `published`, `installation_id`) on two
Signals before and after recording a `SAME_REAL_WORLD_EFFORT` disposition
between them — identical before/after, for both Signals.

## 19. Provenance-preservation proof

`test_source_id_and_publication_unchanged` confirms `Signal.source_id` and
`Signal.published` are untouched on both members after a disposition.
`test_existing_source_assertion_links_unchanged` constructs a real
`SourceAssertion` linked to one member Signal (`signal_id` set), records a
disposition naming both Signals, and re-fetches the `SourceAssertion` by id
afterward — `signal_id` still points at the original Signal; no provenance
was moved to the other member.

## 20. Signal-delete FK behavior

`test_deleting_a_member_signal_fails_safely_with_fk_enforced` (with
`PRAGMA foreign_keys=ON`, mirroring `app/database.py`'s own real
connect-event listener): deleting a Signal that is a disposition member
raises `FOREIGN KEY constraint failed`; after rollback, both the Signal and
its `SignalDispositionMember` row are confirmed still present. A
non-member Signal deletes cleanly with no interference
(`test_deleting_a_non_member_signal_is_unaffected`). Identical mechanism to
`SourceAssertion.signal_id`'s own already-verified precedent — no new
delete-protection idea was invented.

## 21. Information-firewall verdict

**Confirmed, both structurally and behaviorally.** AST scan
(`test_service_module_never_references_forbidden_signal_attributes_ast`)
confirms zero `ast.Attribute` nodes named `title`, `notes`, `source_notes`,
`estimated_total_value_usd`, `estimated_emas_value_usd`, `supplier`,
`likely_supplier`, `supplier_reason`, `confirmed_vendor`, `category`,
`confidence`, `status`, or `manual_year_estimate` appear anywhere in the
service module. A second AST test
(`test_service_module_only_imports_expected_names`) asserts the exact,
complete set of names imported from other modules — nothing beyond
`Optional`, `Sequence`, `Session`, `Signal`,
`SIGNAL_DISPOSITION_DECISIONS`, `SignalDisposition`,
`SignalDispositionMember`. Behaviorally,
`test_behavioral_no_financial_or_title_leak_into_disposition_fields`
constructs Signals with a deliberately identifiable title and a large
financial figure, records a disposition, and confirms neither value
appears in the disposition's own `reason` field or on any member row
object.

## 22. International/Unicode verdict

`test_unicode_reviewer_and_reason_round_trip` (Swedish reviewer name and
reason text) and `test_no_us_specific_assumption_in_generic_international_fixture`
(Japanese airport/Signal/reviewer/reason text) both pass, with exact
round-trip equality re-fetched from the session. Nothing in the model or
service assumes US airports, FAA, USD, a specific vendor, USAspending, or
English text — confirmed by direct inspection (the module's own five-value
information firewall in §21 structurally cannot reference any such field).

## 23. Design deviations

**None.** Every constraint, validation rule, API signature, and semantic
choice in this implementation matches
`fh-d4-signal-disposition-design.md` §5–§13 exactly. No fresh
implementation constraint was found that contradicted the design.

## 24. Defects/corrections found

**None in the design.** One test-authoring correction made during this
slice's own development (not a defect in production code): the first draft
of `TestNoRealDatabaseAccess`'s two tests used a raw substring scan
(`"runway_safe.db" not in source`), which is self-defeating for any test
file that must legitimately *name* the real database in its own prose to
assert it is never touched. Corrected to an AST-based scan that excludes
docstrings and compares against a runtime-constructed (non-literal) target
string, so the check verifies real code references without tripping over
its own necessary self-description. No production code was affected by
this correction.

## 25. Regression/unit tests added

`tests/test_signal_disposition_persistence.py`: **55 tests** across
`TestModelShape` (4), `TestConstraints` (4), `TestGoldenPath` (3),
`TestValidation` (9), `TestDuplicateIdNormalization` (2),
`TestSupersession` (9), `TestAppendOnlyHistory` (1), `TestImmutability` (4),
`TestTransactionOwnership` (3), `TestFailureAtomicity` (3),
`TestSignalSafety` (5), `TestInformationFirewall` (3),
`TestInternational` (2), `TestDeterminism` (1),
`TestNoRealDatabaseAccess` (2). `tests/test_model_contract.py`: extended
in place (4 dict additions + 1 literal-list update), zero other assertions
changed.

## 26. Focused test result

`tests/test_signal_disposition_persistence.py` +
`tests/test_reviewer_action_persistence.py` +
`tests/test_reviewer_action_migration.py` +
`tests/test_reviewer_action_confirm_distinct_signal.py` +
`tests/test_model_contract.py` + `tests/test_governed_signal_creation.py` +
`tests/test_governed_signal_creation_reconciliation.py` +
`tests/test_governed_signal_creation_distinct_confirmation.py`: **284
passed**, 0 failed.

## 27. Full pytest result

See validation run recorded at implementation time — expected 2044
(prior checkpoint baseline) + 55 new D4D1 tests = **2099**, 0 regressions
in any pre-existing test (`test_model_contract.py`'s own snapshot update
is the only pre-existing-file change, and every one of its other
assertions passed unmodified).

## 28. py_compile result

Clean on `app/models/signal_disposition.py`,
`app/services/signal_disposition_persistence.py`,
`tests/test_signal_disposition_persistence.py`, `app/models/__init__.py`,
`tests/test_model_contract.py`.

## 29. git diff --check result

Clean (no whitespace errors) — see validation run.

## 30. Explicit real-DB no-access proof

- No test in `tests/test_signal_disposition_persistence.py` opens
  `data/runway_safe.db` — every fixture uses
  `create_engine("sqlite:///:memory:")` exclusively (`make_session()`/
  `make_session_with_foreign_keys_enforced()`), verified both by direct
  code inspection and by two dedicated tests
  (`TestNoRealDatabaseAccess`) that AST-scan this test file and both
  production modules for any string constant naming the real database
  file, outside of legitimate docstring prose explaining that it is never
  touched.
- `data/runway_safe.db`'s SHA-256, size, and mtime were captured
  immediately before running the full pytest suite for this slice and
  matched the checkpoint established at the start of this session
  (`4aa8c25f...`, 1,794,048 bytes, `1787237717.1444063`) — unchanged,
  because nothing in this implementation or its tests ever opens that
  path.
- No migration script exists anywhere in `scripts/` referencing
  "disposition" or "d4" — confirmed directly (`ls scripts/`).
- No Fleet Health integration module or CLI exists — confirmed directly
  (`ls app/services/`, no `fh_d4_disposition_resolution.py` or similar;
  no new file under `scripts/`).

## 31. Critical review checkpoint

A fresh adversarial review of D4D1 (before commit/push) explicitly
distrusted every claim above and independently re-verified them. **One
genuine, serious production defect was found and fixed.** Everything else
re-checked out sound.

### 31.1 Genuine defect found and fixed

**Member-set immutability after persistence was not actually enforced.**
The original implementation's immutability story rested entirely on
`before_update`/`before_delete` event listeners (§8 above) - but inserting
a brand-new `SignalDispositionMember` row is neither an UPDATE nor a DELETE
of any existing row, so neither listener ever fired for it. Reproduced
directly, unmocked: after a disposition was created and committed via
`record_signal_group_disposition()`, a plain
`session.add(SignalDispositionMember(disposition_id=<already-committed
id>, signal_id=<a different Signal>)); session.commit()` **silently
succeeded**, extending an already-reviewed group's own historical
membership - exactly the "exact-group historical identity is broken"
failure mode the design doc's own §8 (group identity/staleness semantics)
depends on never happening. A second, related attack -
`disposition.members.append(new_member)` via the relationship collection -
was also tested and found to be **already blocked**: appending to the
collection marks the parent `SignalDisposition` dirty, which trips its own
pre-existing `before_update` listener. Only the direct-INSERT path was
actually open.

**Fix**: a new `before_insert` listener on `SignalDispositionMember`
(`_prevent_member_insert_into_an_already_sealed_disposition`), gated by a
transient, never-mapped, never-persisted Python instance attribute
(`ACCEPTING_INITIAL_MEMBERS_ATTR`) that `record_signal_group_disposition()`
sets `True` on a freshly-constructed `SignalDisposition` only for the
duration of inserting that disposition's own initial member batch, cleared
in a `finally` block regardless of success or failure. A disposition
loaded from the database - by definition, anything not freshly constructed
in the current operation - never carries this attribute at all, so the
listener's `getattr(..., False)` default correctly fails closed. No new
persisted column, no SQL trigger, no schema change - the smallest fix that
actually closes the gap, consistent with this slice's own explicit
"do not over-engineer" instruction.

Verified after the fix: the original attack is blocked
(`ValueError`, not a silent success); a fresh-session variant (the
disposition never even constructed in the attacking session) is also
blocked, proving the guard works from cold, not merely via a leftover
marker on a live Python object; two dispositions created in the same
session do not interfere with each other's sealing; legitimate creation,
including legitimate supersession (which also constructs a brand-new
header + member batch), is completely unaffected. Nine new permanent
regression tests added (`TestMemberSetImmutabilityAfterPersistence`).

**A genuine subtlety surfaced while writing the regression test for
"marker cleared even on failure"**: when `record_signal_group_disposition()`
raises before returning (e.g. a simulated mid-batch construction failure),
the caller never receives a reference to the `disposition` object the
service constructed locally - once the function's stack frame unwinds with
no other strong reference held, SQLAlchemy's identity map (which holds
objects by weak reference) can no longer return that exact instance, and a
later query legitimately constructs a fresh one from the database row
instead. The first draft of that test asserted identity-preserving
behavior that isn't actually guaranteed - and isn't required to be: a
freshly-loaded instance was never marked `True` in the first place, so the
fail-closed default protects it exactly as strongly as it would the
original object. The test was corrected to assert the real security
property (a rogue member insert against that disposition_id is still
blocked) rather than an incidental object-identity detail that depends on
garbage-collection timing - documented here per this review's own "no
overclaiming" discipline, not silently smoothed over.

### 31.2 Re-verified sound (no change needed)

- **Model shape**: `SignalDisposition`/`SignalDispositionMember` still have
  exactly the fields the design specifies; no `canonical_signal_id`, score,
  confidence, ranking, publication, resolution-workflow, fingerprint, raw
  evidence, or provider field exists anywhere - re-confirmed by direct
  column-set inspection.
- **Decision vocabulary**: attacked with lowercase (`"distinct"`),
  whitespace-padded (`" DISTINCT "`), `None`, and `bytes` - every case
  correctly rejected by the plain `in` check with no coercion or silent
  normalization; the DB `CHECK` independently rejects `"MAYBE"` via direct
  ORM-bypass construction.
- **`None` reviewer/reason**: raises `AttributeError` (from `None.strip()`),
  not `ValueError` - verified this is not a defect unique to this service
  by reproducing the *identical* behavior against
  `app.services.reviewer_action_persistence.record_reviewer_action()` (the
  established precedent this service is deliberately modeled on) with
  `reason=None`/`reviewer=None`: same `AttributeError`, same root cause
  (trusting the `str` type hint rather than defending against a wrong
  type). Matched, established, pipeline-wide behavior - not weakened,
  not silently left unverified; two permanent regression tests added
  documenting it explicitly. Tab/newline-only reviewer/reason are
  correctly rejected via `ValueError` as expected (`.strip()` reduces them
  to empty).
- **FK/delete safety**: real `FOREIGN KEY constraint failed` reproduced
  with `PRAGMA foreign_keys=ON` for both a missing member Signal and a
  missing `supersedes_id` target; deleting a member Signal is blocked the
  same way; a non-member Signal deletes cleanly.
- **UNIQUE member constraint**: attacked directly at the DB level
  (bypassing service dedup entirely) - `UNIQUE constraint failed` on a
  duplicate `(disposition_id, signal_id)` pair; the identical
  original-draft test previously used a bare `pytest.raises(Exception)`
  without matching the specific error text, meaning it would have
  "passed" even for the wrong reason after the member-set-immutability fix
  landed (the new `before_insert` listener now also fires here) - tightened
  to match `"UNIQUE constraint failed"` specifically during this review, so
  it genuinely proves the UNIQUE constraint rather than merely proving
  *some* exception occurred.
- **Minimum cardinality / duplicate normalization / missing-Signal
  atomicity**: re-attacked with `[]`, `[1]`, `[1,1]`, `[1,1,1]`, and
  `[5,2,5,3,2]` - all behave exactly as originally reported; `session.new`
  confirmed empty after every validation failure.
- **Supersession exact-set semantics**: re-attacked with all four required
  shapes (grown, shrunk, different-same-size, nonexistent target) plus two
  new attacks this review added: (a) an old disposition with **zero**
  persisted member rows (constructed directly, bypassing the service -
  "malformed" relative to what the service itself would ever produce) is
  correctly rejected, since the comparison is derived from the *actual*
  persisted member rows via a fresh query, never from caller assumptions,
  so an empty real set can never equal a non-empty requested set with no
  special-case code needed; (b) a genuine supersession chain (D1 → D2 → D3,
  three decisions across two supersessions) preserves every row unchanged
  and links correctly.
- **Self-supersession / cycles**: true self-supersession is structurally
  impossible through the service's own API (a disposition has no `id`
  until after it is already validated and flushed, and `supersedes_id` is
  only ever accepted as a reference to something already existing). A
  cycle attempt (making an already-superseded `D1` retroactively
  "supersede" the `D2` that already supersedes it) was attacked directly
  at the model level and blocked by the same pre-existing header
  immutability listener that blocks any other post-creation field
  mutation - not a new mechanism, and not a gap.
- **Historical overlap vs. supersession**: confirmed the service correctly
  distinguishes the two - the same Signal appearing in two entirely
  separate, non-superseding dispositions is allowed (a real, legitimate
  historical-overlap case per this review's own §28), while an actual
  supersession claim against a differently-shaped set is still rejected.
  A superseded quintuple (5-member group, two full decisions) round-trips
  correctly.
- **Transaction ownership / failure atomicity**: re-verified byte-for-byte
  as originally reported - `session.rollback()` after a successful call
  removes every pending row; AST confirms zero `.commit()` calls and zero
  `create_engine`/`SessionLocal` references; the two-flush window (header
  flushed before members) genuinely leaves a pending-but-uncommitted
  header at the moment of a forced mid-batch failure, fully recoverable by
  the caller's own rollback - not overclaimed.
- **Signal immutability / provenance safety**: re-verified with full
  before/after column snapshots (including `published`, `source_id`,
  `runway_id`, `installation_id`) and a real linked `SourceAssertion` -
  nothing on any Signal or existing provenance link changes.
- **Information firewall**: re-verified via the same two AST tests (zero
  forbidden-attribute references, exact import allowlist - now extended by
  exactly one name, `ACCEPTING_INITIAL_MEMBERS_ATTR`, for the fix above)
  plus the behavioral leak test.
- **Model registration / model-contract update**: fresh
  `configure_mappers()` + `Base.metadata.create_all()` smoke test against
  an isolated in-memory engine succeeds cleanly, discovering both new
  tables. `git diff --stat` on both modified files confirms purely additive
  changes (`app/models/__init__.py`: 1 line added, 0 removed;
  `tests/test_model_contract.py`: 39 lines added, 0 removed) - every
  pre-existing snapshot entry and assertion is untouched, confirmed by
  direct diff inspection, not merely by the test suite passing.
- **Design-doc alignment**: line-by-line comparison against
  `fh-d4-signal-disposition-design.md` §5-§13 found no omission and no
  accidental semantic drift in the *design* itself - the one gap found
  (§31.1) was an implementation gap in realizing the design's own stated
  "member set is immutable" intent, not a flaw in the design document.
- **International/Unicode**: re-verified (Swedish, Japanese fixtures) -
  exact round-trip, no encoding issue.

### 31.3 Final test totals

`tests/test_signal_disposition_persistence.py`: **69 tests** (55 original +
9 member-set-immutability-after-persistence tests +
2 malformed-supersession-target/cycle tests + 3 `None`/tab-newline
reviewer-reason tests, net of 1 test rewritten in place for the identity/
garbage-collection correction in §31.1). Focused regression suite
(this file + `test_reviewer_action_persistence.py` +
`test_reviewer_action_migration.py` +
`test_reviewer_action_confirm_distinct_signal.py` + `test_model_contract.py`
+ the three `governed_signal_creation` family files): **298 passed**. Full
pytest: see the validation run recorded at commit time - expected 2099
(prior checkpoint) + 14 net new tests = **2113**, zero regressions.
`py_compile`: clean. `git diff --check`: clean.

## 32. Conclusion

D4D1, after this critical review, is sound: two new, purely additive
tables with real FK integrity, immutability that now genuinely covers both
mutation *and* post-creation member insertion, a single validated
persistence entry point that never commits, and 69 fixture-only tests
covering every item both this mission's own Phase 16 checklist and this
review's own attack list named. One genuine, serious defect (member-set
mutability via direct INSERT) was found and fixed with the smallest change
that closes it; every other claim in the original report held under
adversarial re-verification. Ready to commit and push.
