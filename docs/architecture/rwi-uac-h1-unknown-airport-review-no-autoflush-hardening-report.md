# RWI UAC-H1 — Unknown Airport Review Read-Path no_autoflush Hardening

Status: IMPLEMENTED, ADVERSARIALLY RE-CHECKED, COMMITTED, PUSHED.

## 1. Starting HEAD

`5e1f4080faffbfb61b03224ea3dfd9f8f3898311` — confirmed `== origin/main` at mission start.

## 2. Files read fresh

`app/services/unknown_airport_candidate_persistence.py` (UAC1, full), `app/services/unknown_airport_candidate_resolution.py` (UAC4, full), `scripts/review_unknown_airport_candidate.py` (UAC5 CLI, `_read_candidate_state()` and its callers), all real callers of `get_latest_unknown_airport_candidate_review()` (found via grep, not assumed: `_require_current_review()` in UAC4 and `_read_candidate_state()` in the UAC5 CLI — every other match was a docstring mention or a differently-named ERG3 function), ERG2/ERG3/ERG4's own already-reviewed `no_autoflush` implementations (for the established idiom), the existing UAC1/UAC4/UAC5 test suites.

## 3. Original bug reproduction

Independently reproduced (not trusted from the ERG4 report) via three isolated in-memory SQLite probes:

- **Attack A** (plain, already-resolved `candidate_id: int` + an unrelated invalid pending `UnknownAirportCandidate` row added to the same session): calling `get_latest_unknown_airport_candidate_review(session, candidate_id)` directly raised a raw `sqlite3.IntegrityError` via SQLAlchemy's autoflush, originating from the function's own bare `session.query(...)`.
- **Attack B** (an expired `candidate.id` attribute read *as the argument expression* at the call site, after an intervening `session.commit()`, plus the same unrelated pending row): also leaked — but from a *different* location: the attribute-refresh SELECT SQLAlchemy performs when accessing an expired attribute, which happens in the **caller's** scope, before the callee's own body ever executes.
- **Attack C** (the untouched `resolve_candidate_to_existing_airport()` / MATCH_EXISTING_AIRPORT path, called with a plain resolved `candidate_id`/`review_id` and the same unrelated pending row): also leaked, from `session.get(UnknownAirportCandidate, candidate_id)` — the very first line of that function, before `_require_current_review()` is even reached.

## 4. Root cause

Two distinct but related gaps, both genuinely pre-existing (confirmed unmodified by ERG1–ERG4 via `git diff --stat` on this file before any change in this mission):

1. `get_latest_unknown_airport_candidate_review()` itself performs a bare `session.query(...)` with no `session.no_autoflush` guard — unlike every "latest" helper ERG2/ERG3 added, which already learned this exact lesson.
2. Its two real callers — `_require_current_review()` (UAC4) and `_read_candidate_state()` (UAC5 CLI) — read `candidate.id` as an argument expression, and (in UAC4's two top-level entry points) perform additional unguarded reads (`session.get(UnknownAirportCandidate, candidate_id)`, the duplicate-code `session.query(Airport)...` loop) *before* ever reaching the UAC1 helper. Fixing only the UAC1 helper closes Attack A but leaves Attacks B and C open, since those leaks occur in the caller's own scope, before the fixed function's body is ever entered.

## 5. Exact fix

Four narrow, mechanical `session.no_autoflush` wraps, each following the identical, already-established ERG2/ERG3/ERG4 idiom (wrap the entire read-only precondition phase, leave the intentional write section outside):

1. `get_latest_unknown_airport_candidate_review()` (`app/services/unknown_airport_candidate_persistence.py`) — its own `session.query(...)` wrapped.
2. `_require_current_review()` (`app/services/unknown_airport_candidate_resolution.py`) — its entire body (pure read-only, no writes) wrapped.
3. `resolve_candidate_to_existing_airport()` and `create_airport_from_approved_candidate()` (same file) — their entire precondition-check phases (from the first `session.get()` through the last `_require_*` call, including `create_airport_from_approved_candidate()`'s own duplicate-code query loop, a second independently-discovered unwrapped `session.query()`) wrapped; the intentional write section (`candidate.resolved_airport_id = ...` / `airport = Airport(...); session.add(...)` through `session.flush()`) deliberately left **outside** the block.
4. `_read_candidate_state()` (`scripts/review_unknown_airport_candidate.py`) — its entire body (pure read-only, inspect/preview only) wrapped.

No blanket `Session`-wide autoflush disabling anywhere; no change to the query itself beyond the wrap.

## 6. Expired-attribute verdict

HIGH PRIORITY, attacked explicitly and iteratively: an initial, narrower attempt (fixing only the UAC1 helper) was empirically proven insufficient — Attacks B and C both still leaked after that first fix, from `_require_current_review()`'s own `candidate.id` read and from `resolve_candidate_to_existing_airport()`'s own opening `session.get()`, respectively. The fix was widened (item 2 and 3 above) until every reproducible attack shape closed. The full read phase — from the very first attribute read/query in each of the four hardened functions through their own return — is now proven safe, not merely the UAC1 helper in isolation.

## 7. Latest-semantics non-regression

Unchanged: `created_at DESC, id DESC`, identical tiebreak. Re-proven directly (`test_same_timestamp_latest_semantics_unchanged_by_hardening`, plus the pre-existing `test_get_latest_tiebreaks_by_id_on_identical_timestamps`, both still passing) — the fix changes only *when* a flush may occur, never the query's own `ORDER BY`/`LIMIT` semantics, candidate scoping, or None-handling.

## 8. MATCH_EXISTING_AIRPORT (`resolve_candidate_to_existing_airport()`) verdict

Precondition-check phase confirmed no longer leaks on an unrelated invalid pending object (`test_match_existing_airport_precondition_phase_does_not_leak_but_real_write_still_validates`) — but the SAME test also proves the function's own intentional write flush (`session.flush()`, deliberately left outside the `no_autoflush` block) still correctly raises `IntegrityError` once a genuine write happens, because the unrelated pending row really is invalid. No swallowing of real write errors — exactly the mission's own required boundary. Happy-path (no unrelated pending state) re-confirmed unaffected.

## 9. CREATE_NEW_AIRPORT / UAC4 verdict

Same pattern, additionally covering ERG4's own gate (`_require_admission_eligible()`, itself already `no_autoflush`-wrapped internally, nested harmlessly inside this wider wrap) and the duplicate-code query loop. `test_create_new_airport_precondition_phase_does_not_leak` and `test_create_new_airport_happy_path_unaffected_by_hardening` both pass. Full ERG4 canonical-admission test suite (36 tests, `tests/test_unknown_airport_candidate_admission_eligibility.py` + the `TestErg4*` classes in `tests/test_unknown_airport_candidate_resolution.py`) re-run and confirmed unaffected.

## 10. Intentional-write-boundary verdict

Every wrap follows the identical, already-reviewed SCOPE discipline documented inline at each site: the `no_autoflush` block covers only the read/precondition phase; the caller's own eventual intentional write (`session.flush()`/`session.commit()`) still flushes the whole session's pending state, by design, exactly like every other persistence function in this codebase. No function in this mission gained a new commit or a suppressed write.

## 11. Transaction semantics

Unchanged. No function in this module or the CLI ever called `session.commit()` before this mission, and none does now. `session.no_autoflush` is a context manager that only suppresses *automatic* pre-query flushing for its own duration; it has no effect on explicit `session.flush()`/`session.commit()` calls, rollback behavior, or exception propagation (confirmed directly: `raise` statements inside every wrapped block propagate normally, the context manager's `__exit__` still runs to restore the session's autoflush setting).

## 12. Information-firewall verdict

Zero changes to ERG1, ERG2, ERG3, ERG4's own eligibility semantics (`app/services/unknown_airport_candidate_admission_eligibility.py` untouched), UAC3, identity guard, Signal, Installation, promotion, publishing, or the DB schema — confirmed via `git diff --stat` showing exactly six touched files, all named in this report. No new governance vocabulary, no new action type, no new table.

## 13. Regression tests added

9 new tests: 3 in `tests/test_unknown_airport_candidate_persistence.py` (`TestGetLatestReviewNoAutoflushHardening` — unrelated pending object, isolated expired-attribute read, same-timestamp semantics re-proof), 5 in `tests/test_unknown_airport_candidate_resolution.py` (`TestUacH1NoAutoflushHardening` — `_require_current_review()` isolated, MATCH precondition-phase-safe-but-real-write-still-validates, MATCH happy path, CREATE precondition-phase-safe, CREATE happy path), 1 in `tests/test_review_unknown_airport_candidate.py` (`_read_candidate_state()` direct call with unrelated pending object). Every "no leak" assertion proves the pending invalid object is genuinely still pending (`bad in session.new`) rather than merely asserting the absence of an exception.

## 14. Focused tests

`test_emas_relevance_evaluation.py` + `test_unknown_airport_candidate_relevance_persistence.py` + `test_unknown_airport_candidate_relevance_review_persistence.py` + `test_unknown_airport_candidate_relevance_review_migration.py` + `test_unknown_airport_candidate_resolution.py` + `test_review_unknown_airport_candidate.py` + `test_unknown_airport_candidate_admission_eligibility.py` + `test_unknown_airport_candidate_persistence.py` + `test_unknown_airport_candidate_migration.py` + `test_model_contract.py` + `test_effective_identity_guard_decision.py` + `test_resolved_candidate_evidence_reevaluation.py`: **636 passed, 0 failed**.

## 15. Full pytest

See final report below — one complete run, reported exactly.

## 16. py_compile

Clean across all six touched files.

## 17. git diff --check

Clean, exit code 0.

## 18. Real DB before/after

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. This mission never opened `data/runway_safe.db`.

## 19. Files modified

`app/services/unknown_airport_candidate_persistence.py`, `app/services/unknown_airport_candidate_resolution.py`, `scripts/review_unknown_airport_candidate.py`, `tests/test_unknown_airport_candidate_persistence.py`, `tests/test_unknown_airport_candidate_resolution.py`, `tests/test_review_unknown_airport_candidate.py`, this report. No model, migration, or schema changes.

## 20. READY_FOR_ERG5

**YES** — the pre-existing no_autoflush gap ERG4's own review found and deliberately left unfixed is now closed at every point it was empirically shown to be reachable (the UAC1 helper itself, both UAC4 entry points' full precondition phases, and the UAC5 CLI's own inspect path), with zero semantic drift, zero schema change, and a full regression suite proving both the fix and its precise boundary (real write errors still surface).

RWI_UAC_H1_UNKNOWN_AIRPORT_REVIEW_NO_AUTOFLUSH_HARDENED
