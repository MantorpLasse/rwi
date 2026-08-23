# RWI EB4 — Post-Resolution Identity-Guard Re-Evaluation Service (Implementation Report)

Slice 4 of `docs/architecture/rwi-full-evidencebag-persistence-design.md`. Implementation-only mission; commit/push reserved for a separate adversarial EB4 review checkpoint.

## 1. Starting HEAD

`b134f94be97dae03b28a2f5c3bd7370799fc49af` (== `origin/main`).

## 2. Real DB checkpoint (before and after — unchanged)

SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size 1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25, `PRAGMA foreign_key_check`=`[]`, `PRAGMA integrity_check`=`ok`, no UAC or EB schema tables present.

## 3. Files read (fresh, this mission)

`app/services/unknown_airport_candidate_resolution.py` (UAC4), `app/models/source_assertion.py`, `app/models/unknown_airport_candidate.py`, `app/models/source_assertion_evidence_bag.py`, `app/models/identity_guard_evaluation.py`, `app/models/airport.py` (Airport/Runway/RunwayEnd), `app/services/evidence_attachment_guard.py`, `app/services/discovery_evidence_persistence.py` (current, post-EB3), `app/services/unknown_airport_discovery_integration.py`, `app/services/unknown_airport_candidate_persistence.py`; `tests/test_unknown_airport_candidate_resolution.py` for fixture conventions.

## 4. Files created

- `app/services/resolved_candidate_evidence_reevaluation.py`
- `tests/test_resolved_candidate_evidence_reevaluation.py` (36 tests)
- `docs/architecture/rwi-eb4-resolved-evidence-reevaluation-report.md` (this file)

## 5. Files modified

**No production file.** One test file: `tests/test_review_unknown_airport_candidate.py`. Its `TestDownstreamContinuationNote::test_no_reevaluation_service_exists` was UAC5B's own deliberate regression marker proving the architectural gap was real ("assert `ModuleNotFoundError` importing `app.services.resolved_candidate_evidence_reevaluation`"). Once the one full pytest run at the end of this implementation surfaced it as the sole failure across the entire suite, it was replaced with `test_reevaluation_service_now_exists_and_closes_the_uac5b_gap` - a positive proof, using the exact same resolved/INSUFFICIENT_IDENTITY fixture scenario as the neighboring test, that the service now exists and genuinely closes the gap (a real re-evaluation runs successfully against the exact case UAC5B could previously only document as blocked), while independently reconfirming the original historical decision remains untouched. This is the direct, intended, and only-honest consequence of EB4 successfully doing its job - not scope creep, and not a change to any EB1-EB4/UAC production or model file.

## 6. Service API

```
reevaluate_resolved_candidate_evidence(
    session, *, source_assertion_id: int, triggering_review_id: int | None = None,
) -> IdentityGuardReevaluationResult
```

`triggering_review_id` is an optional, purely-additive audit annotation (validated if supplied, never looked up or inferred by this function — see §10). Never commits; caller owns the transaction.

## 7. Eligibility/precondition verdict

Eligibility is purely structural: `SourceAssertion` exists, `airport_id IS NOT NULL`, an `SourceAssertionEvidenceBag` snapshot exists and passes independent verification. No gate on the *original* `identity_guard_decision` value was added — the mission's own "do not restrict arbitrarily" instruction is honored literally: an originally `ATTACH_CONFIRMED` assertion is technically re-evaluable (it simply passes the same structural checks any other resolved assertion does), matching "probably technically possible but operationally unnecessary" without inventing an artificial exclusion. `unknown_airport_candidate_id == NULL` is not checked separately — `SourceAssertion`'s own DB-level mutual-exclusivity `CheckConstraint` already guarantees `airport_id IS NOT NULL` implies it, so checking `airport_id` alone is complete and non-redundant.

## 8. Snapshot validation verdict

Three independent checks, all before any guard call: (1) stored `schema_version` column equals `EVIDENCE_BAG_SCHEMA_VERSION`; (2) `hash_serialized_evidence_bag(stored payload)` equals the stored `evidence_bag_hash` column; (3) the stored payload deserializes cleanly under `deserialize_evidence_bag()`'s own strict rules. All six tamper scenarios (payload changed, hash changed, schema_version column changed, malformed JSON, unsupported embedded version) are independently attacked in `TestTamperAttacks`, each proven to raise `TamperedEvidenceBagSnapshotError` **before** any guard call (spied and asserted `== 0`) and **before** any `IdentityGuardEvaluation` row exists.

## 9. Exact-original-evidence verdict

**HOLDS.** The snapshot is deserialized exactly once (`evidence = deserialize_evidence_bag(snapshot.evidence_bag_json)`) and that same value is passed directly to `evaluate_attachment(candidate, evidence)` — no reconstruction from `SourceAssertion`'s own lossy `raw_*` text fields anywhere in this module, and no second extraction call. `test_reject_cross_airport_proves_uac5_gap_closed` directly proves the UAC5 failure mode is closed: a `contradicting_issuers` fact is confirmed present in the persisted snapshot's own JSON, then re-evaluation correctly produces `REJECT_CROSS_AIRPORT`, never a false `ATTACH_CONFIRMED`.

## 10. Canonical-Airport target derivation

Derived **solely** from `SourceAssertion.airport_id` at call time — never from `UnknownAirportCandidate` provenance, which UAC4's own two execution functions (`resolve_candidate_to_existing_airport()` / `create_airport_from_approved_candidate()`) **clear** (`assertion.unknown_airport_candidate_id = None`) the moment they set `airport_id`, confirmed by direct re-reading of that module's current code. There is no honest way to reconstruct "which candidate resolution produced this attribution" from the current `SourceAssertion` row after UAC4 has run — this module never tries. Auditability of *which review* triggered a given re-evaluation is answered by the optional `triggering_review_id` parameter (a caller-supplied annotation, validated but never derived) plus UAC4's own already-existing `UnknownAirportCandidateReview` history — a structurally separate concern from the guard question this module answers.

## 11. Current-topology semantics

Explicitly documented in the module's own docstring and independently proven by `TestChangedTopology`: EB4 replays the *original* `EvidenceBag` against the airport's *current* canonical `Runway`/`RunwayEnd` topology at call time — this repository keeps no historical snapshot of topology-as-of-discovery-time, so reconstructing the original guard environment is structurally impossible. Each `IdentityGuardEvaluation` is therefore a time-indexed interpretation, not a re-creation; `created_at` is what makes the resulting history legible. Proven concretely: an assertion evaluated as `ATTACH_PROVISIONAL` against an airport with no matching runway on record becomes `ATTACH_CONFIRMED` once a matching `Runway`/`RunwayEnd` is added and re-evaluated — the first evaluation row is untouched, a second row records the new interpretation.

## 12. Guard invocation verdict

`evaluate_attachment()` (single-candidate primitive) is called with exactly one `CandidateAirport`, built via the existing, unmodified `candidate_airport_from_airport_like()` — never `evaluate_attachment_for_candidates()`, and no global candidate-selection is re-run. Structural consequence, stated plainly in the module docstring and proven by `test_review_required_is_structurally_unreachable` (asserts `evaluate_attachment_for_candidates` is not even imported into the module's namespace): a single-candidate re-evaluation can **never** produce `AttachmentOutcome.REVIEW_REQUIRED` — that outcome is only decidable by comparing multiple candidates, per `evaluate_attachment_for_candidates()`'s own docstring. This is an honest, structural limitation of EB4's own narrower question, not an oversight.

## 13–16. ATTACH_CONFIRMED / REJECT_CROSS_AIRPORT / INSUFFICIENT_IDENTITY / REVIEW_REQUIRED verdicts

All three reachable outcomes persist verbatim, unmodified, uncollapsed (`TestOutcomeMatrix`). `REVIEW_REQUIRED` is proven structurally unreachable (§12) rather than merely untested.

## 17. Evaluation persistence verdict

Every `IdentityGuardEvaluation` field is correctly populated: `source_assertion_id`, `evidence_bag_snapshot_id` (derived from the loaded snapshot — never caller-supplied), `evaluated_against_airport_id`, `outcome`/`reason` (verbatim from the real guard decision), `triggering_review_id` (optional, validated), `created_at` (model default). The composite FK from EB1 structurally guarantees the snapshot referenced always belongs to the same `source_assertion_id`.

## 18. Append-only repeat verdict

**HOLDS.** Two calls for the identical `source_assertion_id` produce two distinct `IdentityGuardEvaluation` rows — proven directly (`test_repeated_reevaluation_is_never_deduplicated`).

## 19. Changed-topology verdict

**HOLDS**, see §11.

## 20. Historical SourceAssertion firewall

**HOLDS.** `SourceAssertion.identity_guard_decision`/`identity_guard_reason` captured before, re-evaluated once and twice, confirmed byte-identical after (`TestFirewalls::test_original_guard_fields_never_mutated`). Structurally guaranteed, not just tested: this module never assigns to either attribute anywhere in its own code (confirmed by direct inspection — the only writes in the module are to a new `IdentityGuardEvaluation` instance).

## 21. Snapshot immutability firewall

**HOLDS.** Payload, hash, schema_version, and `created_at` on the `SourceAssertionEvidenceBag` captured before and confirmed identical after re-evaluation (`test_snapshot_never_mutated`) — consistent with EB1's own DB-level `before_update` immutability guard, which this module never attempts to bypass (it only ever reads the snapshot).

## 22. Tamper/fail-closed verdict

**HOLDS**, see §8. All six attack variants (A–F from the mission) fail loud before any guard call and before any evaluation row.

## 23. Malformed-state verdict

**HOLDS.** A dangling `airport_id` (only reachable with FK enforcement disabled) raises a clear `ValueError`; a still-candidate-linked or fully-unresolved assertion raises `UnresolvedSourceAssertionError`; a cross-assertion snapshot forgery attempt is rejected by EB1's own composite `ForeignKeyConstraint` at the DB layer (independently re-verified with FK enforcement explicitly enabled for that one test, since SQLite does not enforce FKs by default) — documented as a DB-layer guarantee, distinct from and in addition to the service's own logic, which never intentionally selects another assertion's snapshot (`_get_existing_assertion`-style lookup is keyed by `source_assertion_id` throughout).

## 24. Transaction/rollback verdict

**HOLDS**, proven by real failure injection (`session.flush` monkeypatched to raise) followed by `session.rollback()`: no evaluation row survives, the snapshot is untouched. No hidden commit anywhere in the module (grep-confirmed).

## 25. No-autoflush/read-only verdict

**A genuine defect was found and fixed during this same implementation phase** (not a pre-existing committed defect — caught by this mission's own `TestNoAutoflushOfUnrelatedState` test before any review checkpoint): the first version of this function wrapped only the candidate-construction/guard-call portion in `session.no_autoflush`, but `session.get()`/`session.scalar()` during the *earlier* precondition-checking phase (loading the `SourceAssertion`, `Airport`, snapshot, and optional review) **also** autoflush by default — so an unrelated, invalid pending object elsewhere in the caller's session could be silently (and incorrectly) flushed during that phase. Fixed by wrapping the **entire** read-only phase (from the first `session.get()` through the guard call) in one `session.no_autoflush` block. A second, related refinement: the final write uses `session.flush(objects=[evaluation])` (a SQLAlchemy-scoped flush) rather than a blanket `session.flush()`, so the service's own explicit write can never drag in other unrelated pending state the caller still has assembled elsewhere in the same session — verified directly by adding a `NOT NULL`-violating unrelated pending object before calling the service and confirming it survives untouched.

## 26. Query-shape verdict

Fixed, small, bounded per call: one `SELECT` for the `SourceAssertion`, one for the `Airport`, one for the snapshot, one (lazy) for `Airport.runways`/`RunwayEnd` (via `candidate_airport_from_airport_like()`, unmodified), one optional `SELECT` for `triggering_review_id` validation, one scoped `INSERT` for the new evaluation. No query-per-EvidenceBag-field or query-per-runway-member; this is a single-assertion service and no batching/N+1 concern applies (no batch service was built, per mission's own explicit instruction not to build one prematurely).

## 27. Migration-chain MATCH lifecycle

**HOLDS.** `test_full_lifecycle_match_existing` builds a database through real migrations only (baseline → UAC2A → UAC2B → EB2, never `create_all()` as the final schema state), runs the complete synthetic lifecycle (discovery → `UnknownAirportCandidate` → `SourceAssertion` + EvidenceBag snapshot → `MATCH_EXISTING_AIRPORT` review → UAC4 execution → EB4 re-evaluation), and confirms every historical layer (snapshot, review, evaluation) remains independently queryable with zero `Signal` rows created.

## 28. Migration-chain CREATE lifecycle

**HOLDS, and the honest edge case is documented rather than forced.** `test_full_lifecycle_create_new_airport_no_topology_yet` proves a brand-new `Airport` created via `CREATE_NEW_AIRPORT` has no `Runway` topology at all; the original evidence's runway claim cannot be confirmed against nothing (absence, not contradiction, per the guard's own established rule), so the correct, actual outcome is `ATTACH_PROVISIONAL` (one positive category — the name match — only), **never** an artificially-forced `ATTACH_CONFIRMED` merely because a human created the Airport.

## 29. Information-firewall verdict

**HOLDS**, both by AST-based import inspection (`governed_signal_creation`, `promotion_policy`, `unknown_airport_candidate_resolution`, `unknown_airport_candidate_persistence`, and all network libraries confirmed absent from the module's own import list) and by direct code inspection (no `Airport`/`Runway`/`Signal` construction anywhere in the module — only reads).

## 30. International verdict

**HOLDS.** Swedish, Portuguese, and Japanese airport names round-trip through the full discovery → resolution → re-evaluation lifecycle; the snapshot's own JSON payload is confirmed to contain the exact Unicode name text (parametrized `TestInternational`).

## 31. Downstream EB5 seam

Deliberately not implemented, per mission's own explicit instruction. The natural seam: a future EB5 consumer would query the **latest** `IdentityGuardEvaluation` per `source_assertion_id` (by `created_at`, the same "current state derived by recency, not cached" convention already established by `get_latest_unknown_airport_candidate_review()`) and treat `outcome == ATTACH_CONFIRMED` as a signal that the assertion may become eligible to re-enter downstream intelligence review — but EB5 must explicitly choose how the *original* `identity_guard_decision` (permanent historical fact) and the *latest* re-evaluation (time-indexed interpretation) relate; EB4 itself makes no precedence claim between them.

## 32. Defects found

One, found and fixed during this same implementation phase (not a pre-existing committed defect): the `no_autoflush` scope was initially too narrow, covering only the candidate-construction/guard-call portion rather than the entire read-only precondition-checking phase — see §25. No defect found in EB1, EB2, EB3, or UAC4's already-committed code; none were modified.

## 33. Corrections made

(1) Widened the `session.no_autoflush` block to cover the entire read-only phase, from the first `session.get()` through the guard call. (2) Changed the final write from a blanket `session.flush()` to a scoped `session.flush(objects=[evaluation])`, so the service's own write never touches unrelated pending caller state either.

## 34. Focused tests

36 new tests in `tests/test_resolved_candidate_evidence_reevaluation.py`, covering the full A–X matrix. Broader regression run (this file plus `test_unknown_airport_candidate_resolution.py`, `test_evidence_bag_persistence.py`, `test_evidence_bag_migration.py`, `test_evidence_bag_discovery_persistence.py`, `test_discovery_evidence_persistence.py`, `test_unknown_airport_discovery_integration.py`, `test_unknown_airport_candidate_persistence.py`, `test_evidence_attachment_guard.py`, `test_capture_mac_discovery.py`): 484 passed, 0 failed.

## 35. Full pytest

Run twice: the first full run at initial readiness surfaced exactly one failure (`tests/test_review_unknown_airport_candidate.py::TestDownstreamContinuationNote::test_no_reevaluation_service_exists`, addressed in §5); after the fix, a second, final full run: **3059 passed, 0 failed**, 666.53s (0:11:06).

## 36. py_compile

`app/services/resolved_candidate_evidence_reevaluation.py`, `tests/test_resolved_candidate_evidence_reevaluation.py` — both compile cleanly.

## 37. git diff --check

Clean — no tracked file was modified at all (no existing production file needed a change), so there is nothing to check beyond the new files themselves, which contain no whitespace errors.

## 38. Real DB before/after proof

Identical before and after (§2) — SHA-256, size, disposition counts, FK/integrity checks, and UAC/EB schema absence all unchanged.

## 39. git status

Exactly two new files (`app/services/resolved_candidate_evidence_reevaluation.py`, `tests/test_resolved_candidate_evidence_reevaluation.py`) beyond this report and the pre-existing, unrelated untracked files already present at mission start. No existing tracked file modified. No commit made, per mission policy.

(Superseded by the adversarial review addendum below: `tests/test_review_unknown_airport_candidate.py` was subsequently modified once, for the reason given there.)

---

RWI_EB4_RESOLVED_EVIDENCE_REEVALUATION_IMPLEMENTATION_COMPLETE

---

## Adversarial Review Addendum

Independent fresh review against the implementation above (never trusting its own prose). Found and fixed **three genuine production defects**, all confirmed reachable through the public API by direct empirical attack before being fixed, and covered by new permanent regression tests:

**1. False cross-candidate provenance (high-priority, as flagged by the review mission).** The original `triggering_review_id` validation only checked that the supplied id referenced *some* real `UnknownAirportCandidateReview` row — it never checked that the review had anything to do with the assertion being re-evaluated. Empirically reproduced: re-evaluating SourceAssertion A (resolved to Airport A) while passing `triggering_review_id` for a completely unrelated Candidate B's review (resolved to Airport B) was silently accepted, fabricating a false causal audit link. Fixed by cross-checking `review.candidate.resolved_airport_id == airport.id` — `UnknownAirportCandidate.resolved_airport_id` is set by *both* UAC4 execution paths (`resolve_candidate_to_existing_airport()` and `create_airport_from_approved_candidate()` alike), so this single check covers MATCH_EXISTING_AIRPORT and CREATE_NEW_AIRPORT reviews uniformly, with no schema widening. A REJECT_CANDIDATE/DEFER review's candidate never has `resolved_airport_id` set, so such reviews are correctly rejected too, with no special-casing. Two new regression tests: cross-candidate rejection, and REJECT_CANDIDATE-review rejection.

**2. N+1 query per runway (§26).** The original Airport load used a plain `session.get(Airport, ...)` with no eager loading. `candidate_airport_from_airport_like()`'s own docstring explicitly documents that avoiding a query-per-runway is the *caller's* responsibility (via `selectinload`, the same convention `app/static_export/build.py` already establishes for this identical Airport→Runway→RunwayEnd shape) — this service wasn't doing it. Empirically measured with a real SQL listener: an airport with 5 runways cost 10 statements versus 6 for a comparable case with proper eager loading. Fixed with `selectinload(Airport.runways).selectinload(Runway.runway_ends)`. New regression test proves query count no longer scales with runway count.

**3. Snapshot-selection silently-picks-one risk (§4, defense-in-depth).** `session.scalar(select(...))` returns the first row of a multi-row result without complaint; only EB1's own DB-level `unique=True` constraint prevented more than one snapshot per assertion from ever existing. Switched to `session.scalars(...).one_or_none()`, which raises `MultipleResultsFound` instead of silently choosing one if that constraint were ever bypassed. New regression test rebuilds `source_assertion_evidence_bags` without its own UNIQUE constraint (SQLite enforces UNIQUE indexes independently of `PRAGMA foreign_keys`, so the table itself had to be rebuilt) and confirms the service fails loud rather than picking an arbitrary snapshot.

**Additional strengthened coverage (not a defect, but a genuine gap in proof strength):** the original REJECT_CROSS_AIRPORT test proved the full snapshot correctly rejects, but did not construct the explicit counterfactual the review mission specifically asked for. Added `test_lossy_bag_would_have_falsely_confirmed_full_bag_correctly_rejects`: the same identifier evidence (definitive alone) with the contradiction fact dropped — exactly what the pre-EB1 lossy `raw_*` columns could carry — is proven to flip `evaluate_attachment()`'s own verdict to a false `ATTACH_CONFIRMED`, while EB4's real re-evaluation (using the actual preserved snapshot) correctly returns `REJECT_CROSS_AIRPORT`. This is the sharpest possible proof that EB1–EB4 closed UAC5's own worked failure case, not merely a plausible-sounding assertion.

**Candidate-construction parity (§11), investigated and found correct, not a defect.** `scripts/capture_mac_discovery.py` (the one real production caller of `candidate_airport_from_airport_like()`) supplies a `known_issuers` kwarg derived from its own MAC-specific `KNOWN_ISSUER_REFERENCE` lookup table. EB4 does not. This is **not** a parity gap: `known_issuers` is never a persisted, canonical `Airport`-model field — it is a producer-specific, in-memory annotation that exists only inside that one capture script's own domain knowledge. EB4, a source-neutral, producer-agnostic re-evaluation service, has no principled way to reconstruct it for an arbitrary re-evaluation call, and inventing a substitute would itself violate the source-neutrality this whole pipeline enforces. Documented honestly: an original discovery-time decision that depended on issuer evidence from a specific producer's own enrichment step may re-evaluate differently at EB4 time purely because that enrichment is unavailable generically — this is an intentional, unavoidable boundary, not a bug, and any future EB5/reporting layer consuming `IdentityGuardEvaluation` rows should be aware of it.

**Everything else independently re-verified, not merely re-read:** write-path/snapshot-selection inventory re-confirmed by direct grep of every `SourceAssertionEvidenceBag(`/`IdentityGuardEvaluation(` construction site (still exactly one each, both in this module); exact-evidence-fidelity re-proven; single-candidate/REVIEW_REQUIRED-unreachable claim re-confirmed structurally (`evaluate_attachment_for_candidates` is not even imported into the module's namespace); ATTACH_PROVISIONAL/ATTACH_CONFIRMED/INSUFFICIENT_IDENTITY persistence re-verified; append-only repeat and changed-topology behavior re-verified; historical `SourceAssertion` and snapshot firewalls re-verified with before/after field capture; the no-autoflush fix re-attacked and reconfirmed sound; transaction rollback re-attacked with real failure injection; both MATCH and CREATE migration-chain lifecycles re-run against real migrations; legacy-row fail-closed behavior re-verified; the "should modern known-airport assertions be re-evaluable too" question (§30) reconfirmed as a correct, honest generalization — eligibility is purely structural (`airport_id` set + valid snapshot), never gated on UAC origin, and nothing in the design doc restricts it to UAC-resolved rows only; international/Unicode fixtures re-verified; information firewall re-confirmed by AST import inspection (no `governed_signal_creation`/`promotion_policy`/`unknown_airport_candidate_resolution`/`unknown_airport_candidate_persistence`/network imports anywhere in the module).

**Final counts after fixes:** 41 tests in `tests/test_resolved_candidate_evidence_reevaluation.py` (was 36; +5: cross-candidate provenance, REJECT_CANDIDATE-review rejection, multi-snapshot fail-loud, counterfactual lossy-bag proof, query-count bound). Broad regression sweep (this file + `test_review_unknown_airport_candidate.py` + `test_unknown_airport_candidate_resolution.py` + EB1/EB2/EB3/UAC4/UAC5/guard/MAC-capture test files): 536 passed, 0 failed. `py_compile` and `git diff --check` clean. Real DB re-verified byte-identical after all review work. Pending: one final full pytest run, then commit and push if sound.
