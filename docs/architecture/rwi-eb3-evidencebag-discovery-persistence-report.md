# RWI EB3 — Discovery EvidenceBag Write Integration (Implementation Report)

Slice 3 of `docs/architecture/rwi-full-evidencebag-persistence-design.md`. Implementation-only mission; commit/push reserved for a separate adversarial EB3 review checkpoint.

## 1. Starting HEAD

`a52108c44ba06ffa5e11099ae2caea581ce7d8be` (== `origin/main`), matching EB2's own committed HEAD exactly.

## 2. Real DB checkpoint (before and after — unchanged)

SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size 1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25, `PRAGMA foreign_key_check`=`[]`, `PRAGMA integrity_check`=`ok`, no UAC or EB schema tables present. Re-verified identical after all implementation work.

## 3. Modern discovery write-path inventory

Exactly two production functions create a `SourceAssertion` from a `CandidateFragment`/`EvidenceBag`, both in `app/services/discovery_evidence_persistence.py`:

| Function | Classification |
|---|---|
| `persist_discovery_fragment()` | MUST_WRITE_EVIDENCEBAG |
| `persist_candidate_linked_source_assertion()` | MUST_WRITE_EVIDENCEBAG |

`app/services/unknown_airport_discovery_integration.py`'s `resolve_or_persist_discovery_identity()` only orchestrates — it calls exactly one of the two functions above per fragment and creates no `SourceAssertion` itself. Legacy importers (NASR, USAspending, FAA EMAS) construct `SourceAssertion` directly from structured government data with no `CandidateFragment`/`EvidenceBag` in the pipeline at all — LEGACY_OTHER_PATH / OUT_OF_SCOPE, untouched.

## 4. Files read (fresh, this mission)

`docs/architecture/rwi-full-evidencebag-persistence-design.md`, `app/models/identity_guard_evaluation.py`, `app/services/discovery_evidence_persistence.py`, `app/services/evidence_bag_serialization.py`, `app/models/source_assertion_evidence_bag.py`, `app/services/discovery_candidate_fragment.py`, `app/services/evidence_attachment_guard.py`, `app/services/unknown_airport_discovery_integration.py`, `tests/test_evidence_bag_persistence.py` (grep-scanned), `tests/test_unknown_airport_discovery_integration.py`, `tests/test_discovery_evidence_persistence.py` (grep-scanned for fixture convention).

## 5. Files modified

- `app/services/discovery_evidence_persistence.py`
- `app/services/unknown_airport_discovery_integration.py`

## 6. Files created

- `tests/test_evidence_bag_discovery_persistence.py`
- `docs/architecture/rwi-eb3-evidencebag-discovery-persistence-report.md` (this file)

EB1 models and the EB2 migration script were **not** modified — no genuine defect was found in either during this mission.

## 7. Snapshot-persistence architecture

A single private helper, `_attach_evidence_bag_snapshot(session, *, source_assertion_id, evidence_bag)`, is the only code in the module permitted to construct a `SourceAssertionEvidenceBag` row. It owns `serialize_evidence_bag()`, `hash_serialized_evidence_bag()`, and `EVIDENCE_BAG_SCHEMA_VERSION` internally — no caller can supply payload/hash/schema_version independently. Both `persist_discovery_fragment()` and `persist_candidate_linked_source_assertion()` call this same helper from their "new assertion" branch, immediately after the assertion's own `session.flush()`. A second helper, `_reconcile_replay_snapshot(session, *, existing_assertion, evidence_bag)`, handles the "existing assertion found via fragment-identity dedup" branch for both functions.

## 8. Write-time payload/hash/schema consistency verdict

**HOLDS.** `_attach_evidence_bag_snapshot()` is the sole writer; `evidence_bag_hash` is computed from the exact `evidence_bag_json` string that gets persisted (never an independently-normalized copy), and `schema_version` is always the serializer's own constant. Verified by `TestWriteTimeConsistency` (round-trips the stored payload back through `deserialize_evidence_bag()` and confirms equality with the original `EvidenceBag`) and by an API-shape test confirming the helper's signature accepts only an `EvidenceBag`, never a payload/hash/schema_version parameter.

## 9. Exact-EvidenceBag-used-by-guard verdict

**HOLDS for both paths, by different but equally sound mechanisms.**

- `persist_discovery_fragment()`: `evidence = candidate_fragment_to_evidence_bag(fragment)` is computed once at the top of the function and is the SAME Python object passed both to `evaluate_attachment_for_candidates(evidence, ...)` and to `_attach_evidence_bag_snapshot(..., evidence_bag=evidence)` — literal object identity, not merely value equality.
- `persist_candidate_linked_source_assertion()`: this function never itself runs the guard (its caller, `resolve_or_persist_discovery_identity()`, already did, before deciding to route here). EB3 adds `evidence = candidate_fragment_to_evidence_bag(fragment)` as this function's own first step, computed from the identical `fragment` object the caller used. `candidate_fragment_to_evidence_bag()` is proven pure (no I/O, no randomness) by its own docstring and existing test suite, so this recomputation is guaranteed to produce a value equal to whatever the caller's own guard evaluation consumed — "equivalent immutable value semantics," the mission's own explicitly-permitted alternative to literal object identity.

## 10. Known-airport path verdict

**HOLDS.** `airport_id` populated when matched, `unknown_airport_candidate_id` NULL, exactly one snapshot created and matching the exact `EvidenceBag`, `identity_guard_decision`/`identity_guard_reason` unchanged from pre-EB3 behavior, no `UnknownAirportCandidate` created. (`TestKnownCanonicalSnapshot`)

## 11. Unknown-airport path verdict

**HOLDS.** `airport_id` NULL, `unknown_airport_candidate_id` populated, exactly one snapshot equal to the exact `EvidenceBag`, no canonical `Airport` created. (`TestUnknownCandidateSnapshot`)

## 12. Unresolved/ambiguous path verdict

**Both create a SourceAssertion and both now get a snapshot, with no separate wiring required.** `UNRESOLVED_IDENTITY` (no formable candidate seed) and `AMBIGUOUS_KNOWN_IDENTITY`/`REJECT_CROSS_AIRPORT` all route through `persist_discovery_fragment()`, which unconditionally snapshots on the "new assertion" branch regardless of `outcome` value. Verified explicitly by `TestUnresolvedAndAmbiguousSnapshot` (three tests: unresolved, reject-cross-airport, ambiguous-known).

## 13. Find-or-reuse policy

- **Existing assertion + existing snapshot**: content-equality checked (current incoming `EvidenceBag`'s serialization compared against the stored payload). Identical → safe no-op, reused silently. Different → `ConflictingEvidenceBagReplayError`, fails loud, original snapshot never touched.
- **Existing assertion + no snapshot (legacy/pre-EB3 row)**: **never backfilled.** Rationale: the fragment-identity dedup key (`source_id`, `artifact_identity`, `source_locator`, `raw_fragment_hash`) is keyed on `raw_fragment_hash`, which is only a hash of `raw_text` — it does not cover the fully-structured `EvidenceBag` (identifiers/names/runway tokens/issuers/locations/contradictions/alternate-airport topology), so a byte-identical fragment-identity match is not, by itself, sufficient proof that today's derived `EvidenceBag` equals whatever originally produced that legacy row's decision. A missing snapshot on a legacy row remains a valid, permanent, and predictable state rather than one that depends on whether a later replay happens to occur.

Implemented in `_reconcile_replay_snapshot()`; tested by `TestReplaySemantics` and `TestLegacyMissingSnapshotNeverBackfilled`.

## 14. Exactly-one-snapshot verdict

**HOLDS**, enforced at both layers: DB-level `UniqueConstraint`/`unique=True` FK from EB1 (proven via direct `IntegrityError` on a forced second insert), and service-level logic (a snapshot is only ever attempted on the "new assertion created" branch; replay reuses or conflicts, never re-attempts). (`TestExactlyOneSnapshot`)

## 15. Conflicting-replay verdict

**Fails loud.** `ConflictingEvidenceBagReplayError` (subclass of `ValueError`) is raised, the original snapshot is left untouched. Proven directly against `_reconcile_replay_snapshot()` with a deliberately different `EvidenceBag` for an existing assertion.

## 16. Transaction-atomicity verdict

**HOLDS for both new-assertion and unknown-candidate paths**, proven by real failure injection (monkeypatched `_attach_evidence_bag_snapshot` / `session.flush` to raise `RuntimeError` at the relevant point, never mocked away) followed by `session.rollback()` and re-querying: no orphan `SourceAssertion`, no orphan snapshot, ever survives a failed transaction. (`TestNewAssertionAtomicity`)

## 17. Candidate rollback verdict

**HOLDS.** A newly-created `UnknownAirportCandidate` is removed by rollback when snapshot creation fails downstream in the same call. A pre-existing candidate (from an earlier, already-committed call) survives untouched when a later call linking a second assertion to it fails and rolls back. (`TestUnknownCandidateAtomicity`)

## 18. Historical guard firewall verdict

**HOLDS.** `SourceAssertion.identity_guard_decision`/`identity_guard_reason` are set exactly as before EB3 (from the pre-existing guard evaluation), never touched by snapshot creation. Zero `IdentityGuardEvaluation` rows are ever created by any EB3 code path. (`TestHistoricalGuardFirewall`)

## 19. SourceAssertion backward-compatibility verdict

**HOLDS.** All 80 pre-existing tests in `tests/test_discovery_evidence_persistence.py` and `tests/test_unknown_airport_discovery_integration.py` pass unmodified against the new code, proving field-level output is unchanged for both persistence functions aside from the new sibling snapshot row's existence.

## 20. Migration-chain parity

Two new tests build a database through real migrations only (`Base.metadata.create_all()` then surgically reduced to the pre-UAC2B shape, then `uac2a_migration.upgrade()` → `uac2b_migration.upgrade()` → `eb2_migration.upgrade()`, never `create_all()` as the final proof) and exercise both the known-canonical and unknown-candidate paths end-to-end, confirming snapshot creation against a genuinely migrated schema. (`TestMigrationChainParity`)

## 21. Missing/incompatible schema behavior

**Missing EB schema: fails loud**, via a new `EvidenceBagSchemaRequiredError`, raised by `_verify_evidence_bag_schema_ready()` at the top of both public functions before any write is attempted. No auto-migration, no silent SourceAssertion-only fallback. **Incompatible-but-present schema** is deliberately left to fail naturally at INSERT time with a real SQLAlchemy error — the check here is a lightweight existence check (via `session.execute(text(...))` against `sqlite_master` through the session's own connection), not a duplicate of EB2 migration's own deep structural comparison, per the mission's explicit "do not duplicate schema inspection" instruction.

**Design correction found and fixed during this same implementation phase** (see §24): the first version of this check used `sqlalchemy.inspect(session.get_bind())`, which opens a second, independent `Connection` against the engine. For an in-memory `sqlite:///:memory:` test database (`SingletonThreadPool`, one shared physical connection per thread), that second connection's transaction bookkeeping collided with the Session's own open transaction and silently corrupted session state (observed as duplicate primary-key reuse across unrelated rows, with a `SAWarning: Identity map already had an identity ... replacing it with newly flushed object`). Replaced with a query through the Session's own connection (`session.execute(text(...))`), which resolved it completely — all previously-passing tests pass again.

## 22. UAC dependency verdict

**Asymmetric, as expected.** `persist_discovery_fragment()` (known-airport path) requires only the EB2 schema, never UAC schema — proven by a dedicated test building a DB with EB tables but without any UAC tables. `persist_candidate_linked_source_assertion()` (unknown-airport path) already required UAC schema before EB3 (via its `UnknownAirportCandidate` FK validation) — EB3 adds only the EB-schema requirement on top, unchanged UAC dependency.

## 23. Unicode/lossless verdict

**HOLDS.** A dedicated test persists an `EvidenceBag` containing Swedish/Portuguese/Japanese/Arabic characters, an emoji, NFC/NFD variants, commas, quotes, newlines, tabs, and backslashes, commits to a real on-disk `tmp_path` SQLite file, closes and reopens the database in a fresh engine/session, and deserializes the stored snapshot — exact equality holds against the original `EvidenceBag`. No comma-join fallback is used anywhere in the snapshot path (only in the pre-existing, unmodified `raw_*` audit columns, which EB3 does not touch).

## 24. Source-neutrality verdict

**HOLDS.** AST-based import inspection of `discovery_evidence_persistence.py` confirms no vendor/geography/language-specific dependency (MAC/Granicus/FAA/USAspending/n8n/OpenAI/network libraries all absent).

## 25. MAC fixture compatibility

**HOLDS.** `scripts/capture_mac_discovery.py` is confirmed (by grep) to call `persist_discovery_fragment()`/`persist_candidate_linked_source_assertion()`. `tests/test_capture_mac_discovery.py` (116 tests) passes unmodified against the new code — its fixtures already use `Base.metadata.create_all()`, which registers the EB1 tables automatically, so the new schema-readiness check is satisfied without any fixture change.

## 26. Information-firewall verdict

**HOLDS.** No identity reevaluation, no `IdentityGuardEvaluation` creation, no unknown-candidate resolution, no canonical `Airport` creation, no `Signal` creation, no promotion-policy change. Verified explicitly by `TestNoCanonicalOrSignalSideEffects` and `TestHistoricalGuardFirewall::test_no_identity_guard_evaluation_rows_ever_created`.

## 27. Defects found

One defect, found and fixed during this same implementation phase (not a pre-existing committed defect — introduced and caught within this mission, before any test suite run against the full pre-existing corpus): the original `_verify_evidence_bag_schema_ready()` implementation used `sqlalchemy.inspect(session.get_bind())`, which corrupts in-memory SQLite session state as described in §21. No genuine defect was found in already-committed EB1/EB2 code; neither was modified.

## 28. Corrections made

Replaced `sqlalchemy.inspect(engine).has_table()` with a `session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name IN (...)"), params)` query, reusing the Session's own connection instead of opening a second one.

## 29. Focused tests

497 passed (0 failed) across: `test_discovery_evidence_persistence.py`, `test_unknown_airport_discovery_integration.py`, `test_evidence_bag_discovery_persistence.py` (new, 29 tests, A–Y matrix), `test_evidence_bag_persistence.py`, `test_evidence_bag_migration.py`, `test_capture_mac_discovery.py`, `test_unknown_airport_candidate_persistence.py`, `test_unknown_airport_candidate_migration.py`, `test_source_assertion_unknown_airport_migration.py`.

## 30. Full pytest

Ran once at final readiness: **3021 passed, 0 failed**, 770.97s (0:12:50). (Baseline before this mission's changes: 2992 passed — the +29 delta is exactly the new `tests/test_evidence_bag_discovery_persistence.py` file; every pre-existing test still passes unmodified.)

## 31. py_compile

`app/services/discovery_evidence_persistence.py`, `app/services/unknown_airport_discovery_integration.py`, `tests/test_evidence_bag_discovery_persistence.py` — all compile cleanly.

## 32. git diff --check

Clean — no whitespace errors.

## 33. Real DB before/after proof

Identical before and after (§2) — SHA-256, size, disposition counts, FK/integrity checks, and UAC/EB schema absence all unchanged.

## 34. git status

Exactly two modified files (`app/services/discovery_evidence_persistence.py`, `app/services/unknown_airport_discovery_integration.py`) and two new files (`tests/test_evidence_bag_discovery_persistence.py`, this report) beyond the pre-existing, unrelated untracked files already present at mission start (various design docs, UI screenshots, research directory — all untouched). No commit made, per mission policy.

## 35. Recommended EB4 scope

EB4 ("re-evaluation service") is the natural next slice per the design doc's own EB1→EB6 sequence: a governed service that, given a `SourceAssertion` with an existing `SourceAssertionEvidenceBag` snapshot and a (possibly newly-resolved) target `Airport`, deserializes the snapshot, re-runs `evaluate_attachment()`/`evaluate_attachment_for_candidates()` against it, and persists the result as a new, append-only `IdentityGuardEvaluation` row — never mutating the original `SourceAssertion` or snapshot. The most natural first trigger is UAC5's own already-identified use case: after a human resolves an `UnknownAirportCandidate` to a real `Airport` (via `scripts/review_unknown_airport_candidate.py`), EB4 would let that resolution re-evaluate every `SourceAssertion` linked to that candidate against the now-known airport's real topology — closing the exact "resolved-evidence continuation" gap UAC5B's own STOP identified, now that EB3 guarantees every modern-discovery `SourceAssertion` has the lossless evidence needed to re-evaluate against.

---

RWI_EB3_EVIDENCEBAG_DISCOVERY_PERSISTENCE_IMPLEMENTATION_COMPLETE
