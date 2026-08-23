# RWI EB5 — Downstream Consumption of Identity Re-Evaluation (Implementation Report)

Slice 5 of `docs/architecture/rwi-full-evidencebag-persistence-design.md`. Implementation-only mission; commit/push reserved for a separate adversarial EB5 review checkpoint.

## 1. Starting HEAD

`c99b94cf341288db38698de38428331d71f51192` (== `origin/main`).

## 2. Real DB checkpoint (before and after — unchanged)

SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size 1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25, `PRAGMA foreign_key_check`=`[]`, `PRAGMA integrity_check`=`ok`, no UAC or EB schema tables present.

## 3. Files read (fresh, this mission)

`app/models/source_assertion.py`, `app/models/identity_guard_evaluation.py`, `app/models/source_assertion_evidence_bag.py`, `app/services/resolved_candidate_evidence_reevaluation.py`, `app/services/intelligence_review_persistence.py`, `app/services/evidence_attachment_guard.py`, `app/services/governed_signal_creation.py`, `app/services/promotion_policy_persistence.py`, `app/services/human_review_queue.py`, `app/services/fleet_health_review_rules.py`, `app/services/reviewer_action_persistence.py` (every reader of `identity_guard_decision`/`identity_guard_reason` repo-wide, via grep); relevant existing tests for each.

## 4. Exact downstream blocker

`app/services/intelligence_review_persistence.py`'s `_identity_decision_from_assertion(source_assertion)` (the identity gate feeding `evaluate_signal_candidate()`) read **only** `source_assertion.identity_guard_decision` — the permanent historical column — and never consulted `IdentityGuardEvaluation` at all. A SourceAssertion whose original decision was `INSUFFICIENT_IDENTITY` but whose latest EB4 re-evaluation was `ATTACH_CONFIRMED` was still fed `INSUFFICIENT_IDENTITY`, producing `IDENTITY_NOT_CONFIRMED` from intelligence review regardless of the later confirmation. Reproduced fresh: `tests/test_effective_identity_guard_decision.py::TestBlockerReproduction`. `promotion_policy_persistence.py`'s `persist_promotion_policy()` independently imports and calls the exact same private helper for the exact same purpose — a second, genuine consumer of the same seam, not a duplicate to be left alone.

## 5. Precedence rule

If a currently-trustworthy latest `IdentityGuardEvaluation` exists for a SourceAssertion with a current canonical `airport_id`, it is **fully authoritative** for current downstream eligibility — not merely an additional positive signal. A later negative evaluation makes a row not currently eligible even if the original historical decision was `ATTACH_CONFIRMED` (independently derived and locked, matching the mission's own expected architecture: EB4 is explicitly a replay against *current* topology, so a stale original positive is never silently reused). If no valid latest evaluation exists, falls back unchanged to the original historical decision. Full table (using the real 5-member `AttachmentOutcome` enum, not a hand-typed shadow vocabulary):

| Historical | Latest evaluation | Effective | Basis |
|---|---|---|---|
| any | none | historical, unchanged | `ORIGINAL_DECISION` |
| any | `ATTACH_CONFIRMED` | `ATTACH_CONFIRMED` (confirmed) | `LATEST_REEVALUATION` |
| any | `ATTACH_PROVISIONAL` | `ATTACH_PROVISIONAL` (not confirmed) | `LATEST_REEVALUATION` |
| any | `INSUFFICIENT_IDENTITY` | `INSUFFICIENT_IDENTITY` (not confirmed) | `LATEST_REEVALUATION` |
| any | `REJECT_CROSS_AIRPORT` | `REJECT_CROSS_AIRPORT` (not confirmed) | `LATEST_REEVALUATION` |
| any | `REVIEW_REQUIRED` (only reachable via DB corruption — EB4 itself never produces it) | `REVIEW_REQUIRED` (not confirmed) | `LATEST_REEVALUATION` |
| any | evaluation exists but `evaluated_against_airport_id` ≠ current `airport_id` | historical, unchanged | `INCONSISTENT_REEVALUATION` |
| any (airport_id NULL — candidate-linked or unresolved) | not consulted at all | historical, unchanged | `ORIGINAL_DECISION` |

## 6. Latest-evaluation selection rule

`ORDER BY created_at DESC, id DESC LIMIT 1` — the single most recent row by insertion order, tie-broken by id. Never "latest ATTACH_CONFIRMED"; always "latest evaluation, period," then interpret its outcome. Proven directly: a 6-step sequence (INSUFFICIENT → CONFIRMED → PROVISIONAL → CONFIRMED → REJECT → CONFIRMED) tracks only the most recent row at every step; identical timestamps are proven to break the tie by id.

## 7. Effective-state service / result contract

`app/services/effective_identity_guard_decision.py`, `resolve_effective_identity_guard_decision(session, *, source_assertion_id) -> EffectiveIdentityGuardDecision`. Read-only, no writes of any kind. Result fields: `source_assertion_id`, `original_decision`, `latest_evaluation_id`, `latest_evaluation_outcome`, `effective_decision`, `basis` (`EffectiveIdentityGuardDecisionBasis`: `ORIGINAL_DECISION` / `LATEST_REEVALUATION` / `INCONSISTENT_REEVALUATION`), plus a computed `is_identity_confirmed` property. Raises `SourceAssertionNotFoundError` (reused verbatim from EB4, not a second near-duplicate type) only when the assertion itself doesn't exist; every other malformed state returns an explicit, never-falsely-positive result.

## 8. Historical-decision preservation verdict

**HOLDS, absolutely.** The module performs zero writes — grep-confirmed no `session.add(`/`session.flush(`/`session.commit(` anywhere in it. `SourceAssertion.identity_guard_decision`/`identity_guard_reason` are read but never assigned to, in this module or in either of the two integration points it was wired into.

## 9. ATTACH_CONFIRMED verdict

**HOLDS** — case C in the precedence table, `is_identity_confirmed=True`.

## 10. ATTACH_PROVISIONAL verdict

**HOLDS, never promoted.** Explicitly tested: `ATTACH_PROVISIONAL` as the latest outcome yields `is_identity_confirmed=False`. No convenience upgrade anywhere in the module.

## 11. Negative-evaluation verdicts (INSUFFICIENT_IDENTITY / REJECT_CROSS_AIRPORT / REVIEW_REQUIRED)

**HOLD**, all three (plus PROVISIONAL) tested uniformly via one parametrized test over the full real `AttachmentOutcome` enum. `REVIEW_REQUIRED` is included even though EB4 itself can never produce it (single-candidate guard) — a malformed/direct-DB-bypass row with that outcome is still handled correctly (not confirmed), never assumed unreachable in practice.

## 12. Original-confirmed/later-negative verdict

**HOLDS, and independently re-derived rather than assumed.** `evaluate_attachment()`'s own contract review (EB4) already establishes that a re-evaluation is explicitly a replay against *current* canonical identity/topology — an original `ATTACH_CONFIRMED` that a later, more current replay contradicts is stale for *today's* purposes. Directly tested: original `ATTACH_CONFIRMED`, later `REJECT_CROSS_AIRPORT` → not currently eligible, while `SourceAssertion.identity_guard_decision` itself remains `ATTACH_CONFIRMED` forever. This is the sharpest proof that EB5 did not merely bolt on a positive override — it implements a real, symmetric precedence rule.

## 13. Candidate-linked firewall verdict

**HOLDS.** `airport_id IS NULL` (covering both still-candidate-linked and fully-unresolved rows, via `SourceAssertion`'s own mutual-exclusivity `CheckConstraint`) is checked *before* any `IdentityGuardEvaluation` query is even issued — such a row's basis is always `ORIGINAL_DECISION`, `latest_evaluation_id=None`, regardless of any synthetic/malformed evaluation row that might exist for it (only reachable via direct DB bypass — EB4 itself refuses to evaluate an unresolved assertion). Tested directly with a synthetic evaluation deliberately planted for such a row.

## 14. Evaluation-Airport consistency verdict

**HOLDS.** `evaluation.evaluated_against_airport_id != assertion.airport_id` (only reachable via corruption — EB4 always writes the assertion's own current `airport_id`) is never trusted: returns `basis=INCONSISTENT_REEVALUATION`, falls back to the historical decision, never silently uses a `CONFIRMED` evaluation recorded against a different Airport. Tested directly.

## 15. Triggering-review provenance verdict

**Confirmed NOT needed for eligibility**, exactly as the mission expected. `resolve_effective_identity_guard_decision()` never reads `IdentityGuardEvaluation.triggering_review_id` at all — the governed evaluation's own `outcome` is the identity result; provenance is a separate, already-answered auditability question (EB4's own review). No test needed to add a dependency that doesn't exist; verified by direct code inspection (the field is not referenced anywhere in the new module).

## 16. Topology/time semantics verdict

Documented explicitly in the module's own docstring: EB5 trusts the latest *existing* governed evaluation and never inspects current Runway/RunwayEnd topology itself, never triggers a new evaluation, and performs no hidden writes during reads. Proven structurally: the module never imports `reevaluate_resolved_candidate_evidence` or any evaluation-creating function (AST-verified), and a dedicated test confirms two calls to the resolver never change the `IdentityGuardEvaluation` row count.

## 17. Intelligence-review integration verdict

**Smallest possible point, wired.** `_identity_decision_from_assertion()` in `intelligence_review_persistence.py` (already the sole helper both `persist_intelligence_review()` and `persist_promotion_policy()` use) now forwards `resolve_effective_identity_guard_decision(...).effective_decision` instead of reading the historical column directly — a two-line signature change (`session` added as a parameter) plus a two-call-site update. No other line of `persist_intelligence_review()`/`evaluate_signal_candidate()` touched. All required scenarios from §12 of the mission verified: historically-confirmed unchanged; historically-insufficient + latest CONFIRMED now eligible; candidate-linked unresolved remains ineligible; legacy assertion (no snapshot/evaluation) unchanged.

## 18. Promotion-policy verdict

**Genuinely the same seam — fixed, not left alone.** `promotion_policy_persistence.py`'s `persist_promotion_policy()` imports and calls the exact same `_identity_decision_from_assertion()` helper independently of `persist_intelligence_review()` (confirmed by fresh reading before any change was made) — leaving it unfixed would have produced an inconsistent half-fixed state where intelligence review recognized a later confirmation but promotion policy did not, for the identical underlying question. Fixed by updating its one call site to the new two-argument signature; no precedence logic duplicated (the module still imports the shared helper, never reimplements the rule itself).

## 19. Governed-Signal firewall verdict

**HOLDS, deliberately unwidened.** `governed_signal_creation.py` and `reviewer_action_persistence.py` are **not modified at all** — both still check `source_assertion.identity_guard_decision != "ATTACH_CONFIRMED"` (the permanent historical column) directly, their own hard gate unchanged. Proven directly: a SourceAssertion with historical `INSUFFICIENT_IDENTITY` + a later `ATTACH_CONFIRMED` evaluation is shown to be `is_identity_confirmed=True` for intelligence-review purposes, yet `create_signal_from_approved_review()` still raises `ValueError` citing the historical value, and zero `Signal` rows are created. This is an intentional, permanent architectural boundary, not an oversight: a row whose canonical identity was only confirmed by a *later* re-evaluation can reach intelligence review and promotion-policy eligibility, but can never reach actual Signal creation through the current pipeline, since that gate's own hardcoded requirement is on the frozen historical fact. Documented here as an intentionally deferred issue for a future slice, not something EB5 resolves.

## 20. Fleet Health verdict

**Unaffected, re-confirmed.** `fleet_health_check.py`/`fleet_health_review_rules.py` were not modified (both are audit/diagnostic consumers, not eligibility gates — they only compare column values and emit warnings, never block anything). Full existing Fleet Health test suites re-run: 168 passed, 0 failed, no regression.

## 21. Legacy compatibility verdict

**HOLDS.** A legacy `SourceAssertion` with no snapshot and no evaluation resolves to `basis=ORIGINAL_DECISION`, `latest_evaluation_id=None`, `effective_decision` == the historical column exactly — byte-for-byte identical to pre-EB5 behavior. No snapshot requirement, no forced migration, no backfill.

## 22. Modern-known assertion verdict

**HOLDS, origin-neutral by construction.** The resolver's own logic never branches on how a SourceAssertion originated (UAC-resolved vs. ordinary known-airport discovery) — it only ever inspects `airport_id`/`identity_guard_decision`/`IdentityGuardEvaluation` rows, fields every canonical SourceAssertion shares regardless of provenance. Tested directly with an ordinary known-airport (`persist_discovery_fragment`, no UAC involvement) assertion reaching `ATTACH_CONFIRMED` via `ORIGINAL_DECISION` and, separately, via a later evaluation overriding an original `ATTACH_CONFIRMED` to `REJECT_CROSS_AIRPORT`.

## 23. Malformed-state verdict

**HOLDS.** Nonexistent SourceAssertion → raises `SourceAssertionNotFoundError`. Every other malformed scenario tested (dangling/inconsistent evaluation, candidate-linked with a planted evaluation, zero evaluations) returns an explicit, correctly-not-confirmed result — never a false positive, never a silent pick of a convenient row.

## 24. Query-shape verdict

Bounded, small, fixed per call regardless of evaluation history size: one `SELECT` for the SourceAssertion (`session.get`, identity-map-cacheable), at most one `SELECT` for the latest evaluation (`ORDER BY ... LIMIT 1`, not a full-table scan interpreted in Python). No N+1 risk since intelligence-review/promotion-policy both process one SourceAssertion per call (confirmed by reading their own call signatures — neither accepts a collection); no premature batching built.

## 25. MATCH migration lifecycle verdict

**HOLDS.** Real migrations only (baseline → UAC2A → UAC2B → EB2, never `create_all()` as final state) exercising the full chain: EB3 unknown discovery → UAC review MATCH_EXISTING → UAC4 execute → EB4 `ATTACH_CONFIRMED` → EB5 effective state (`is_identity_confirmed=True`, `basis=LATEST_REEVALUATION`) → original historical decision proven unchanged throughout.

## 26. CREATE migration lifecycle verdict

**HOLDS, and matches the mission's own expected honest edge case exactly.** First EB4 evaluation against a brand-new, topology-free Airport correctly yields `ATTACH_PROVISIONAL` (not forced to CONFIRMED) → EB5 correctly reports not-identity-confirmed. Legitimate canonical topology (`Runway`/`RunwayEnd`) is then added in the fixture, a **new** EB4 re-evaluation is run, yields `ATTACH_CONFIRMED`, and EB5 now correctly reports identity-confirmed via that new evaluation — an explicit end-to-end proof of the whole architecture's own time-indexed-interpretation design.

## 27. Repeated-evaluation/tie-break verdict

**HOLDS**, per §6/§8 above.

## 28. International verdict

**HOLDS.** Swedish, Portuguese, and Japanese airport names round-trip through the full discovery → resolution → evaluation → effective-state lifecycle; no MAC/FAA-specific assumption anywhere in the new module (AST-confirmed no vendor-specific import).

## 29. Information-firewall verdict

**HOLDS.** AST-based import inspection confirms `effective_identity_guard_decision.py` never imports `governed_signal_creation`, `promotion_policy_*`, `unknown_airport_candidate_resolution`, `unknown_airport_candidate_persistence`, or any network library; direct source-text check confirms no `session.add(`/`session.commit(`/`session.flush(` anywhere in the module. The only "action" it enables is the same, unmodified, human-gated `create_signal_from_approved_review()`/`record_reviewer_action()` path that already existed — never bypassed (§19).

## 30. Files modified

- `app/services/intelligence_review_persistence.py` (the shared identity-decision helper, widened to consult the new resolver; two-line signature change plus one internal call-site update)
- `app/services/promotion_policy_persistence.py` (one call-site update to the new signature — same shared helper, genuinely the same seam)
- `tests/test_intelligence_review_persistence.py` (two new EB5-integration tests added)
- `tests/test_promotion_policy_persistence.py` (one new EB5-integration test added)
- `tests/test_unknown_airport_candidate_resolution.py` (one pre-existing test's call site updated to the new two-argument signature — a genuine, minimal signature-compatibility fix, not a behavior change; its own class docstring updated to accurately describe that EB5 now closes the gap it documented, while the Signal-creation firewall remains deliberately intact — mirroring exactly how the analogous stale UAC5 marker was handled during the EB4 review)

## 31. Files created

- `app/services/effective_identity_guard_decision.py`
- `tests/test_effective_identity_guard_decision.py` (24 tests, covering the full A-level scenario matrix)
- `docs/architecture/rwi-eb5-downstream-identity-consumption-report.md` (this file)

## 32. Defects found

None in already-committed EB1–EB4/UAC code. The "defect" this whole mission addresses (the downstream blocker in §4) was a known, already-documented gap (UAC4's own adversarial review, `TestDownstreamContinuationIsNotYetReachable`), not a newly-discovered one.

## 33. Corrections made

None beyond the intended EB5 wiring itself — no incorrect design decision was made and then fixed during this implementation; the effective-state service, precedence rule, and both integration points worked correctly on first real test run (the only failure encountered, in `test_unknown_airport_candidate_resolution.py`, was a stale-signature compatibility issue in a pre-existing test, addressed in §30, not a defect in the new code).

## 34. Focused tests

24 new tests in `tests/test_effective_identity_guard_decision.py`; 3 new integration tests across `test_intelligence_review_persistence.py`/`test_promotion_policy_persistence.py`. Broad regression sweep (these plus `test_unknown_airport_candidate_resolution.py`, `test_review_unknown_airport_candidate.py`, `test_resolved_candidate_evidence_reevaluation.py`, EB1–EB3 tests, `test_governed_signal_creation.py`, `test_reviewer_action_persistence.py`, `test_human_review_queue.py`, Fleet Health suites): 556 + 168 = 724 passed, 0 failed.

## 35. Full pytest

Run once, at final readiness, per the mission's own instruction: **3092 passed, 0 failed**, 756.29s (0:12:36).

## 36. py_compile

All new/modified files compile cleanly.

## 37. git diff --check

Clean, no whitespace errors.

## 38. Real DB before/after proof

Identical throughout (§2) — SHA-256, size, disposition counts, FK/integrity checks, and UAC/EB schema absence all unchanged.

## 39. git status

Two new production files, three modified production/test files (the smallest possible integration footprint), one new test file, this report, beyond the pre-existing untracked files already present at mission start. No commit made, per mission policy.

---

RWI_EB5_DOWNSTREAM_IDENTITY_CONSUMPTION_IMPLEMENTATION_COMPLETE

---

## Adversarial Review Addendum

Independent fresh review against the implementation above. Found and fixed **two genuine, severe production defects**, both empirically reproduced before being fixed, plus independently re-confirmed the mission's own single highest-priority architecture question against the original, pre-existing design document rather than trusting the implementation report's own framing.

**1. Pre-EB2-migration deployment incompatibility (HIGH PRIORITY, exactly as the review mission flagged).** The real production database has never run the EB2 migration - no `identity_guard_evaluations` table exists. Before this fix, `resolve_effective_identity_guard_decision()` (and therefore `persist_intelligence_review()`/`persist_promotion_policy()`, its two real callers) unconditionally queried that table and raised a raw `sqlite3.OperationalError: no such table` for **every single call**, including SourceAssertions with no relationship to EB1-EB5 whatsoever. Empirically reproduced: a database built without the two EB1 tables (matching the real DB's own actual current schema) caused `persist_intelligence_review()` to crash on an ordinary, historically-`ATTACH_CONFIRMED` assertion that had worked correctly before EB5 existed. This would have broken intelligence-review/promotion-policy functionality for the entire production system the moment this code was deployed, well before any real EB2 migration mission ever ran. Fixed with `_identity_guard_evaluations_table_exists()`, a lightweight existence-only check (not a duplicate of EB2's own deep structural inspector) that queries `sqlite_master` through the Session's own connection (never `sqlalchemy.inspect()`/`engine.connect()`, which EB3's own earlier review already proved can corrupt an in-memory SQLite Session's transaction state). Table absent → clean fallback to the historical decision (`basis=ORIGINAL_DECISION`), matching the mission's own explicit expected policy A. Table present-but-malformed is deliberately left to fail loud at the real query (verified directly: a table with only two columns produces a real `OperationalError` at the actual query, never silently reinterpreted as "zero evaluations") - correctly distinguishing "never migrated" (safe fallback) from "migrated but broken" (fail loud), per the mission's own explicit requirement. Two new regression tests added, plus a third proving the fix's own schema-check never opens a second connection (the identical class of hazard, proactively guarded against).

**2. Hidden autoflush of unrelated pending state (same class of defect EB4's own review found once already, and the mission explicitly asked to apply the same scrutiny to).** The resolver's queries (`session.get()`, `session.scalars()`) were not wrapped in `session.no_autoflush`, despite the entire function being read-only. Empirically reproduced: a caller with an unrelated, `NOT NULL`-violating pending object elsewhere in the same Session had that object silently and prematurely flushed merely by calling the resolver with a plain `source_assertion_id` int (the function's own documented public API) - not a misuse of the API, a real caller-visible defect. Fixed by wrapping the entire function body in one `session.no_autoflush` block, mirroring EB4's own established fix exactly. One new regression test added, matching EB4's own test naming/structure precedent for this exact attack.

**3. Governed-Signal-creation architecture boundary (the mission's own designated single highest-priority question) - independently re-confirmed as verdict A (intentional, correct safety boundary), not merely trusted from the implementation report's own prose.** Re-read `docs/architecture/rwi-full-evidencebag-persistence-design.md`'s own §18 slice-by-slice definition, written and approved *before* any of EB1-EB5 was implemented: EB5 is explicitly scoped as "the first slice that would change `intelligence_review_persistence.py`'s own gate," described as making resolved evidence "actually reach `Signal`-eligible **territory**" - never described as reaching `governed_signal_creation.py` itself. The design document's own pre-existing scope boundary, not a post-hoc rationalization, confirms `governed_signal_creation.py`/`reviewer_action_persistence.py` correctly remain untouched: both still hard-require the permanent historical `identity_guard_decision` column directly, so a SourceAssertion confirmed only via a later re-evaluation can reach intelligence review and promotion-policy eligibility but can never reach actual Signal creation through the current pipeline. This is confirmed as an intentional, correctly-scoped, and *already-designed-in-advance* limitation - a future EB6-or-later slice's own explicit, separately-authorized decision, not an EB5 incompleteness. Re-verified directly (not just re-read): the existing firewall test still passes unmodified.

**Everything else independently re-verified, not merely re-read:** the precedence table re-derived and re-attacked against all 5 real `AttachmentOutcome` members plus the "no evaluation" and "inconsistent evaluation" cases; latest-selection/tie-break semantics re-confirmed (`created_at DESC, id DESC`, higher id wins on exact ties, in both directions); candidate-linked firewall and evaluation-Airport consistency re-attacked with malformed/synthetic evaluation rows; original-confirmed+later-negative re-proven end-to-end through the real `persist_discovery_fragment()` path (not just the resolver in isolation); both intelligence-review and promotion-policy integration points re-confirmed to consume the identical shared helper (no duplicated precedence logic - promotion policy was independently confirmed as a genuine second consumer of the same seam, correctly fixed alongside intelligence review rather than left half-fixed); `triggering_review_id` re-confirmed structurally irrelevant to eligibility; MATCH and CREATE migration-chain lifecycles re-run against real migrations only; Fleet Health suites re-run in full with zero regressions; information firewall re-confirmed by AST inspection (no writes, no forbidden imports).

**Final counts after fixes:** 29 tests in `tests/test_effective_identity_guard_decision.py` (was 24; +5: pre-EB2-schema fallback, malformed-schema-fails-loud, schema-check connection-identity, no-autoflush, plus the earlier-implementation governed-Signal-firewall test already present). Broad regression sweep (this file + `test_intelligence_review_persistence.py` + `test_promotion_policy_persistence.py` + `test_unknown_airport_candidate_resolution.py` + `test_review_unknown_airport_candidate.py` + `test_resolved_candidate_evidence_reevaluation.py` + EB1-EB3 tests + `test_governed_signal_creation.py` + `test_reviewer_action_persistence.py` + `test_human_review_queue.py` + Fleet Health suites): 695 passed, 0 failed. `py_compile` and `git diff --check` clean. Real DB re-verified byte-identical after all review work. Pending: one final full pytest run, then commit and push if sound.
