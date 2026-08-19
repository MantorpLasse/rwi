# Intelligence-review persistence — Slice 4 report

Slice 4 of the roadmap in [evidence-to-signal-semantics-design.md](evidence-to-signal-semantics-design.md)
§17/§18, persisting the decision produced by the unmodified Slice 3 core
([signal-candidate-core-slice3-report.md](signal-candidate-core-slice3-report.md)) onto the governed
`SourceAssertion` rows Slice 2's MAC adapter
([mac-granicus-claim-extraction-slice2-report.md](mac-granicus-claim-extraction-slice2-report.md)) and
the Slice 1 claim core ([evidence-claim-semantics-core-report.md](evidence-claim-semantics-core-report.md))
feed into.

## 1. Starting HEAD

`24d32a2bbac2a9e7c0fc5cd76aed2fc86e620667` ("Add signal candidate evaluation core"), branch `main`,
matched `origin/main`. Baseline full pytest: 921 passed, verified before implementation began.

## 2. Exact schema changes

Two new nullable columns on the existing `source_assertions` table (`app/models/source_assertion.py`),
in the exact same additive style as Slice 1's `identity_guard_decision`/`identity_guard_reason`:

```python
intelligence_review_decision: Mapped[Optional[str]] = mapped_column(String(30))
intelligence_review_reason: Mapped[Optional[str]] = mapped_column(Text)
```

No other model changed. `Signal` (`app/models/signal.py`) was read for grounding and not modified — no
new column, no new table.

## 3. Migration design/result

`scripts/migrate_intelligence_review_persistence_slice4.py` — a near-verbatim copy of
`scripts/migrate_discovery_governed_evidence_slice1.py`'s own proven structure: `backup_database()`,
`inspect()`, `upgrade()` (idempotent `ADD COLUMN`, guarded by `PRAGMA table_info` checks),
`downgrade()` (reuses the same `_drop_column_via_rebuild()` full-table-rebuild procedure, needed
because `source_assertions` is the target of a real incoming foreign key,
`installation_assertion_links.assertion_id`), and the same CLI (`--database`, `--downgrade`,
`--allow-database-write`, `--skip-backup`). No DB-level `CHECK` constraint on
`intelligence_review_decision` — same reasoning as `identity_guard_decision`: a `CHECK` would require
the full-rebuild path even for `upgrade()`, more than a plain additive migration needs; the persistence
service is the sole writer and only ever writes a real `SignalCandidateOutcome.value`, enforced in
Python.

`tests/test_intelligence_review_persistence_migration.py` mirrors
`tests/test_discovery_governed_evidence_migration.py` exactly: a hand-built pre-Slice-4 schema (which
now correctly *includes* Slice 1's `identity_guard_decision`/`identity_guard_reason`, since those are
already part of the current baseline this slice starts from), `test_upgrade_adds_only_the_expected_columns`,
`test_upgrade_is_idempotent_when_run_twice`, `test_downgrade_is_exactly_reversible`, and the realistic
full-schema regression `test_downgrade_succeeds_against_the_full_realistic_schema_with_an_incoming_foreign_key`
(seeding a `PhysicalInstallationIdentity` + `InstallationAssertionLink` referencing the assertion, then
proving upgrade → write → downgrade → re-upgrade all succeed with zero foreign-key violations and all
surviving indexes intact). **4 passed**, 0 failed. `data/runway_safe.db` was never opened by any test or
by this report's own preparation — confirmed by an unchanged SHA-256 (`6be9c6f1...0771de0`), file size
(1,761,280 bytes), and mtime before and after this slice.

## 4. Persistence API

```python
def persist_intelligence_review(
    session: Session, source_assertion: SourceAssertion, claims: tuple[Claim, ...],
) -> IntelligenceReviewResult
```

in `app/services/intelligence_review_persistence.py`. Deliberately takes `claims` as an explicit
parameter rather than calling `extract_mac_claims()` (or any other source-specific extractor) itself —
the caller obtains the claim tuple through whichever extraction boundary is appropriate for that row's
own source family (MAC/Granicus today via Slice 2's adapter; a future Massport/SFO/international
adapter later), keeping this persistence bridge fully source-agnostic, exactly the same separation
Slice 3 itself keeps from Slice 2. `IntelligenceReviewResult(source_assertion_id, decision)` is a plain,
frozen, ORM-free dataclass with `.outcome`/`.reason` convenience properties reading straight through to
the embedded `SignalCandidateDecision` — no ORM instance is ever exposed.

## 5. Transaction behavior

`persist_intelligence_review()` never calls `session.commit()` — proven directly by
`TestTransactionOwnership.test_service_never_commits` (monkeypatches `session.commit` and asserts it is
never invoked). It calls `session.flush()` once, purely so a constraint violation surfaces immediately,
matching `app/services/discovery_evidence_persistence.py`'s own already-established "no hidden commits"
discipline exactly. The caller owns `commit()`/`rollback()` entirely —
`TestRollback.test_rollback_leaves_no_persisted_mutation` proves a `session.rollback()` after the call
leaves both new columns `NULL` when read back from a completely fresh `Session`/engine.

## 6. Identity/intelligence gate separation

`_identity_decision_from_assertion()` reads `source_assertion.identity_guard_decision` (a free-text
column) and maps it defensively to a real `AttachmentOutcome`: the literal string `"ATTACH_CONFIRMED"`
maps to itself; anything else — `"ATTACH_PROVISIONAL"`, `"REVIEW_REQUIRED"`, `"REJECT_CROSS_AIRPORT"`,
`"INSUFFICIENT_IDENTITY"`, `None` (a row from a pathway that never ran the guard, e.g. NASR/
USAspending), or any unrecognized string — maps to `AttachmentOutcome.INSUFFICIENT_IDENTITY`, which the
unmodified Slice 3 core already turns into `IDENTITY_NOT_CONFIRMED`. `TestIdentityGate` parametrizes
over all six cases (five real values plus `None`) and proves every one persists
`intelligence_review_decision == "IDENTITY_NOT_CONFIRMED"`. `identity_guard_decision` and
`intelligence_review_decision` remain two separate columns, set by two separate services, never merged
— proven directly by `TestMSPGoldenCase.test_identity_and_intelligence_decisions_are_visibly_separate`
(`"ATTACH_CONFIRMED"` vs `"REVIEW_REQUIRED"` on the same real row).

## 7. MSP result

The real chain — real PDF fixture → `extract_candidate_fragment()` → `extract_mac_claims()` (7 claims)
→ persisted through the **actual, unmodified** `persist_discovery_fragment()` (Slice 1-era) against an
isolated in-memory-via-tmp-file database, producing a real, governed `SourceAssertion` row with
`identity_guard_decision = "ATTACH_CONFIRMED"` → `persist_intelligence_review()` — persists:

```
intelligence_review_decision = "REVIEW_REQUIRED"
intelligence_review_reason   = "Material claim combination found: 2 category-qualifying claim
    type(s) present (explicit_document_fact, procedural_request). Financial roles (kept
    structurally distinct): advance_deposit_purchase_order=1590000.00 USD;
    cip_project_ceiling=19000000.00 USD. Named relationships: Runway Safe
    (requested_sole_source_vendor); Runway Safe (installation_oversight). Procedural
    request(s), pending - not approved/awarded/executed: advance-deposit Purchase Order,
    EMAS bed, runway pair 12R/30L. Dated/temporal facts: ... requested_pending_approval
    (as of 2024-08-28); ... historical_fact (as of 2023-12-18); ... planned_future_action
    (as of 2024-08-28). Human review required before any Signal is created or updated."
```

confirmed identical, verbatim, whether read from the in-session ORM object or reloaded from a brand new
`Session`/engine against the same file. `identity_guard_decision` on the same row remains
`"ATTACH_CONFIRMED"` throughout, structurally distinct from `intelligence_review_decision`. **Zero**
`Signal` rows exist before or after.

## 8. Financial semantics

`advance_deposit_purchase_order=1590000.00 USD` and `cip_project_ceiling=19000000.00 USD` both appear,
verbatim and separately, in the persisted `intelligence_review_reason` string — proven directly by
`TestFinancialSemantics.test_advance_deposit_and_cip_ceiling_remain_distinct_after_persistence`. The
SFO-$40M adversarial construction (bare EMAS-context fact + a weak `"mentioned_in_document"` relationship,
no `FinancialFact` at all — an unlabeled amount structurally cannot become one, per Slice 1's own
`semantic_role`-required invariant) persists a reason containing neither `"$40"` nor `"contract"`, and
every claim in `decision.material_claims` has `financial is None`.

## 9. Temporal semantics

The persisted reason for the real MSP row contains the literal substring `"planned_future_action"` and
never contains `"completed"` — proven by `TestTemporalSafety`, executed under the real 2026-08-19 session
date (`installation contract... planned_future_action (as of 2024-08-28)` is unaffected by how far past
2025 the evaluating system clock has moved, since Slice 3's own `evaluate_signal_candidate()` — unmodified
in this slice — never reads current time and this new persistence module adds no date/time logic of its
own).

## 10. Adversarial results (task's 12 numbered cases)

All twelve implemented and pass:

1. **MSP golden case → REVIEW_REQUIRED persisted** — §7 above.
2. **Identity not confirmed → IDENTITY_NOT_CONFIRMED / fail-closed** — §6 above, all six identity values.
3. **SFO-$40M unlabeled money cannot become contract value** — §8 above.
4. **$1.59M / $19M remain distinct** — §8 above.
5. **Planned-2025 wording stays planned, not completed, under the real 2026 system date** — §9 above.
6. **Repeated identical evaluation is idempotent** — `TestIdempotency`: two calls with the same claims
   produce identical `outcome`/`reason`, the persisted columns are unchanged between calls, and
   `SourceAssertion` row count stays at exactly 1.
7. **Rollback leaves no persisted mutation** — `TestRollback`, §5 above.
8. **Existing evidence/provenance fields remain unchanged** — `TestUnrelatedFieldsUnchanged` compares
   `raw_relevant_text`, `identity_guard_decision`, `identity_guard_reason`, `artifact_identity`,
   `source_locator`, `raw_fragment_hash`, `airport_id`, `assertion_type`, `evidence_quality`,
   `review_state` before/after — byte-identical.
9. **Zero Signal rows created or modified** — `TestNoSignalOrCanonicalWrites.test_zero_signal_rows_created_or_modified`.
10. **No canonical Airport/Runway/RunwayEnd/Installation writes** —
    `test_no_canonical_airport_runway_runway_end_installation_writes`, row counts across all five tables
    unchanged.
11. **Existing rows with NULL intelligence-review fields remain valid** —
    `TestNullRowsRemainValid.test_row_never_reviewed_keeps_null_fields` (ORM level) plus
    `test_upgrade_adds_only_the_expected_columns` (migration level, both columns `NULL` on every
    pre-existing row after `upgrade()`).
12. **Migration upgrade/downgrade preserves realistic incoming FKs** — §3 above.

## 11. No-Signal proof

`app/services/intelligence_review_persistence.py` contains zero references to `Signal` anywhere (no
import, no string literal) — confirmed by inspection. `TestNoSignalOrCanonicalWrites` proves this
functionally: querying `Signal` before and after `persist_intelligence_review()` returns the identical
empty list.

## 12. Idempotency

`persist_intelligence_review()` always recomputes `evaluate_signal_candidate(claims, context)` and
(re-)writes both columns rather than skipping when already set — since evaluation is pure and
deterministic (Slice 3, unmodified), a repeated call with the same inputs is idempotent in effect (the
identical value is written twice) while a later call after a legitimate change (e.g. an improved
extractor) keeps the persisted judgment in sync with the current computation, per this slice's own
explicit instruction ("decision/reason must reflect the current deterministic evaluation exactly"). It
never creates a second `SourceAssertion` row — proven by `TestIdempotency`'s row-count assertion.

## 13. Focused/full test counts

```
python -m pytest tests/test_intelligence_review_persistence.py \
    tests/test_intelligence_review_persistence_migration.py \
    tests/test_evidence_claim_semantics.py tests/test_mac_granicus_claims.py \
    tests/test_signal_candidate_evaluation.py tests/test_discovery_evidence_persistence.py \
    tests/test_model_contract.py tests/test_discovery_governed_evidence_migration.py -q
```

Result: **149 passed**, 0 failed.

```
python -m pytest -q
```

Result: **944 passed** (baseline 921 + 19 in `tests/test_intelligence_review_persistence.py` + 4 in
`tests/test_intelligence_review_persistence_migration.py`), 0 failed, 0 skipped. Reconfirmed identical
after the checkpoint review's own correction (§14a).

`python -m py_compile` on all seven changed/new Python files — no output, exit 0.
`git diff --check` — no output, exit 0.

## 13a. Checkpoint review correction

`tests/test_intelligence_review_persistence.py` imported `datetime.date`, `decimal.Decimal`,
`evidence_claim_semantics.FinancialFact`, and `intelligence_review_persistence.IntelligenceReviewResult`
but never referenced any of the four anywhere in the file (leftover from an earlier draft of the test
helpers). Removed all four as dead imports during the commit/push checkpoint review. No test behavior
changed — reran the full 19-test file plus the full suite (944 passed, unchanged) to confirm. No defect
was found in `app/services/intelligence_review_persistence.py`,
`scripts/migrate_intelligence_review_persistence_slice4.py`,
`tests/test_intelligence_review_persistence_migration.py`, or the identity-gate mapping logic — the
`_identity_decision_from_assertion()` fail-closed mapping was independently re-verified empirically
(all five real `AttachmentOutcome` string values round-trip to themselves; only `None`/malformed strings
fall back to `INSUFFICIENT_IDENTITY`), confirming the persisted `intelligence_review_reason` correctly
reports the *actual* identity state rather than a collapsed/generic one.

## 14. Exact files changed

**New:**
- `app/services/intelligence_review_persistence.py`
- `scripts/migrate_intelligence_review_persistence_slice4.py`
- `tests/test_intelligence_review_persistence.py`
- `tests/test_intelligence_review_persistence_migration.py`
- `docs/architecture/intelligence-review-persistence-slice4-report.md` (this file)

**Modified:**
- `app/models/source_assertion.py` — the two new columns (§2).
- `tests/test_model_contract.py` — added `intelligence_review_decision`/`intelligence_review_reason`
  entries to the `source_assertions` contract table, matching the identical format already used for
  `identity_guard_decision`/`identity_guard_reason`.
- `tests/test_capture_mac_discovery.py` — one pre-existing test,
  `test_apply_succeeds_after_running_the_real_migration_script`, needed a genuine fix: it hand-builds a
  pre-migration `source_assertions` table and previously ran only Slice 1's migration before exercising
  the capture runner's ORM-backed write path. Because the ORM model now declares the two new columns
  unconditionally, *any* `SELECT` against `SourceAssertion` — including the capture runner's own
  idempotency check, which the runner itself never intentionally touches these columns for — requires
  the physical table to carry them too. Fixed by also running this slice's own migration
  (`migrate_intelligence_review_persistence_slice4.upgrade()`) in that one test, after Slice 1's,
  documented inline. This is a mechanical, expected consequence of adding any new nullable column to an
  ORM-mapped table with a test fixture built from a hand-crafted (not `Base.metadata.create_all()`)
  schema — not a defect in the capture runner itself, which still correctly gates only on the columns
  it actually writes.

`app/services/evidence_claim_semantics.py`, `app/acquisition/mac_granicus_claims.py`,
`app/services/signal_candidate_evaluation.py`, and `app/models/signal.py` were all read for grounding
and **not modified**.

## 15. Real DB unchanged proof

`data/runway_safe.db`: SHA-256 `6be9c6f16b6e84fd67ccba7da3d7ac33bfd72c8d5479ea0dca046b9560771de0`, size
1,761,280 bytes, mtime `1787086520.3335173` — identical before this task began and after its completion.
The file was never opened by any test (every test in both new suites builds an isolated `tmp_path`
SQLite database) or by any command run in preparing this report. No `--allow-database-write` invocation
of either migration script against the default `data/runway_safe.db` path occurred at any point.

## 16. Recommended next slice

Per the design doc's own roadmap (§20, slice 5): a **read-only review-queue query/report** surfacing
`SourceAssertion` rows where `intelligence_review_decision IN ('REVIEW_REQUIRED', 'CONTRADICTED', ...)`,
rendering each row's already-persisted `intelligence_review_reason` (and, for a richer view, the
re-derived `material_claims` by re-running the appropriate source-specific extractor plus
`evaluate_signal_candidate()` on demand) for a human — no UI, no write path, no automatic promotion.
Should remain read-only against the real DB once run, and must not create, modify, or promote any
`Signal` row. Explicitly the last slice before any actual, human-approved Signal promotion action
(design doc slice 6), which itself remains manual and out of scope for any slice built so far.

---

`RWI_INTELLIGENCE_REVIEW_PERSISTENCE_SLICE4_COMPLETE`
