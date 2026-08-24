# UAC7 — Wire the Live MAC Discovery Runner to the Governed UAC3 Orchestrator

Status: IMPLEMENTED, ADVERSARIALLY REVIEWED, COMMITTED. §14 below documents the review's own independent findings and the two strengthening changes it made before commit.

## 1. Problem this mission closes

The RWI Post-Rehearsal Operational Readiness Review found that `scripts/capture_mac_discovery.py` — the only production, network-capable live-acquisition entry point in the repository — called `app.services.discovery_evidence_persistence.persist_discovery_fragment()` directly in its apply phase, and never imported or called `app.services.unknown_airport_discovery_integration.resolve_or_persist_discovery_identity()` (the UAC3 orchestrator). `persist_discovery_fragment()` has no code path to `find_or_create_unknown_airport_candidate()`, so a real MAC committee document naming an airport RWI does not know about would have been persisted as an ordinary "unresolved" `SourceAssertion` (`airport_id=NULL`, `unknown_airport_candidate_id=NULL`) and never become a reviewable `UnknownAirportCandidate`. Controlled Rehearsal #1 proved the generic UAC3→UAC4→EB4→EB5 pipeline works end-to-end, but only via a hand-written orchestration script that called `resolve_or_persist_discovery_identity()` directly — never through the one real acquisition adapter.

UAC7 closes exactly this wiring gap. No real DB writes, no live network, no migration, no candidate resolution, no EB4/EB5 auto-trigger, no Signal creation, no architecture redesign.

## 2. Starting state

- HEAD: `4c706c593ce01a2fab74b791a43e8da409710a8d` — matched.
- Real DB checkpoint: SHA `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `PRAGMA foreign_key_check=[]`, `integrity_check=ok`, all six rehearsal rows present exactly as expected — matched.
- Working tree: pre-existing untracked docs/screenshots from other in-progress work preserved untouched.

## 3. Files read fresh before implementing

`scripts/capture_mac_discovery.py`, `app/services/unknown_airport_discovery_integration.py`, `app/services/discovery_evidence_persistence.py`, `app/services/evidence_attachment_guard.py`, `app/services/unknown_airport_candidate_persistence.py` (signature only), `app/services/discovery_candidate_fragment.py` (field shape), `app/acquisition/mac_granicus.py` (source confirmation, prior mission), `tests/test_capture_mac_discovery.py` (all 695 lines), `tests/test_unknown_airport_discovery_integration.py` (test index, ~1000+ lines — 60+ existing test names enumerated to confirm what UAC3 already proves at the service level, so this mission's own new tests would not duplicate it).

## 4. Exact pre-UAC7 bypass (confirmed fresh)

`scripts/capture_mac_discovery.py` line 677 (pre-change):
```python
apply_results.append(persist_discovery_fragment(session, meta, enriched_fragment, candidates))
```
Import block imported `persist_discovery_fragment`/`DiscoveryPersistenceResult` from `discovery_evidence_persistence` and nothing from `unknown_airport_discovery_integration`. This call happens unconditionally for every fragment the apply phase processes, regardless of guard outcome — so the fourth routing branch (no known match + a formable candidate seed) was structurally unreachable from this runner.

## 5. Orchestrator contract (`resolve_or_persist_discovery_identity`)

Signature: `resolve_or_persist_discovery_identity(session, source_metadata: DiscoverySourceMetadata, fragment: CandidateFragment, candidate_airports: list[CandidateAirport]) -> DiscoveryIdentityResolutionResult`. Never commits; caller owns the transaction.

Five guard outcomes bucket into four routing outcomes:

| Guard outcome(s) | Routing outcome | Persistence called |
|---|---|---|
| ATTACH_CONFIRMED, ATTACH_PROVISIONAL | `KNOWN_CANONICAL_ATTACHMENT` | `persist_discovery_fragment()` |
| REVIEW_REQUIRED | `AMBIGUOUS_KNOWN_IDENTITY` | `persist_discovery_fragment()` |
| REJECT_CROSS_AIRPORT / INSUFFICIENT_IDENTITY, seed **not** formable | `UNRESOLVED_IDENTITY` | `persist_discovery_fragment()` |
| REJECT_CROSS_AIRPORT / INSUFFICIENT_IDENTITY, seed formable (exactly one alphabetic airport name) | `UNKNOWN_AIRPORT_CANDIDATE` | `find_or_create_unknown_airport_candidate()` + `persist_candidate_linked_source_assertion()` |

`DiscoveryIdentityResolutionResult` is **not** drop-in identical to `DiscoveryPersistenceResult`: it adds `outcome` (`DiscoveryIdentityOutcome`), `attachment_outcome` (the underlying `AttachmentOutcome`), `unknown_airport_candidate_id`, `unknown_airport_candidate_created`; it drops `evaluated_candidate_ids`; `attached_unknown_airport_candidate_id` becomes `unknown_airport_candidate_id`. This required adapting the runner's result-handling code, not a mechanical swap.

## 6. Production change — `scripts/capture_mac_discovery.py` only

Exactly one production file modified, as targeted. Four changes:

1. **Imports**: dropped `persist_discovery_fragment`/`DiscoveryPersistenceResult`; added `resolve_or_persist_discovery_identity`, `DiscoveryIdentityResolutionResult`, and (reused, not reimplemented — matching the file's existing `_select_primary` reuse precedent) `_extract_unknown_airport_candidate_seed` from `unknown_airport_discovery_integration`.
2. **Apply-phase call site**: `persist_discovery_fragment(session, meta, enriched_fragment, candidates)` → `resolve_or_persist_discovery_identity(session, meta, enriched_fragment, candidates)`. This is the wiring fix itself — one line.
3. **Result rendering** (`report["apply_result"]`): now exposes `routing_outcome` (the `DiscoveryIdentityOutcome`), `attachment_outcome` (the underlying `AttachmentOutcome`), plus `source_id`, `source_created`, `source_assertion_id`, `source_assertion_created`, `attached_airport_id`, `unknown_airport_candidate_id`, `unknown_airport_candidate_created`, `evidence_bag_snapshot_id` — no ORM objects, no generic `persisted=True` collapsing a candidate creation into invisibility.
4. **Plan/fingerprint extension** (`PlannedGovernedEvidence`, `plan_governed_persistence()`, `compute_plan_fingerprint()`): added a `would_form_unknown_airport_candidate: bool` field, computed in the read-only planning pass by reusing `_extract_unknown_airport_candidate_seed()` (a pure function of the fragment alone — no I/O) gated on the already-computed `outcome` being in the "no known match" bucket. This field is now part of the fingerprint's hashed content — see §9 for why.

No changes to `unknown_airport_discovery_integration.py`, `discovery_evidence_persistence.py`, `evidence_attachment_guard.py`, or `unknown_airport_candidate_persistence.py` — confirmed by `git status`.

## 7. Files created

- `tests/test_capture_mac_discovery_uac7_wiring.py` — 8 new tests, all going through `run_capture()` itself (never calling the UAC3 service directly), proving the wiring rather than re-proving UAC3's own already-exhaustive service-level test suite.
- This report.

## 8. Verdicts

**8. Production wiring verdict: DONE.** The apply phase now calls `resolve_or_persist_discovery_identity()`, verified both by direct code inspection and by `test_strong_unknown_candidate_routes_through_runner_to_uac3` creating a real `UnknownAirportCandidate` through the actual runner entry point.

**9. Known-airport compatibility verdict: PRESERVED.** `test_apply_creates_source_and_source_assertion_only` (updated) confirms identical persisted state for the MSP fixture: same `SourceAssertion` identity, same `airport_id`, same `identity_guard_decision="ATTACH_CONFIRMED"`, same reason, `unknown_airport_candidate_id` explicitly None. All 15 pre-existing MSP/SFO/fingerprint/idempotency/backup/isolation tests in `tests/test_capture_mac_discovery.py` passed unmodified against the new code — only one test needed updating, and only because the JSON report's `outcome` key was deliberately split into `routing_outcome`/`attachment_outcome` (§6.3), never because underlying persisted behavior changed.

**10. Strong-unknown runner verdict: PROVEN.** `test_strong_unknown_candidate_routes_through_runner_to_uac3` — through the real runner, a fictional-airport fragment with no known-airport match produces exactly one `UnknownAirportCandidate`, one candidate-linked `SourceAssertion` (`airport_id=NULL`, `unknown_airport_candidate_id` populated), one `SourceAssertionEvidenceBag`, zero `Airport`, zero `Signal`, zero `IdentityGuardEvaluation` rows.

**11. Convergence verdict: PROVEN.** `test_unknown_candidate_replay_through_runner_converges_no_duplicates` — replaying the identical fragment through the runner a second time reuses the same candidate (`unknown_airport_candidate_created=False` on replay) and the same `SourceAssertion`; no runner-specific dedup layer was added, this is entirely inherited from UAC1/UAC3's own idempotency.

**12. Weak-identity verdict: PROVEN.** `test_weak_identity_through_runner_stays_unresolved_no_candidate` — a fragment with zero airport names still persists as `UNRESOLVED_IDENTITY` with no candidate manufactured; evidence is preserved (`source_assertions == 1`), not lost.

**13. Ambiguous-known verdict: PROVEN.** `test_ambiguous_known_identity_through_runner_never_forms_candidate` — two known airports sharing identical runway topology both downgrade to `REVIEW_REQUIRED`; the runner correctly routes to `AMBIGUOUS_KNOWN_IDENTITY` and never fabricates a candidate. Required an unambiguous shared runway-pair fixture (an initial "09/27" fixture attempt was silently excluded from candidate selection by the runway-identity normalizer's own ambiguity handling — not a UAC7 defect, just fixture choice; switched to the already-proven-safe "4/22" designation).

**14. All-conflict/new-candidate verdict: PROVEN.** `test_all_known_conflict_coherent_new_routes_to_unknown_candidate` — a known airport whose canonical topology matches the fragment's runway mention but whose identifier contradicts the fragment's claimed identifier still REJECT_CROSS_AIRPORTs (contradiction unconditionally vetoes positive evidence); with no known candidate accepting the evidence and a coherent single airport name present, the runner routes to `UNKNOWN_AIRPORT_CANDIDATE`. Confirms `select_candidate_airports()` is purely topology/SFO-driven — a candidate with only a matching identifier and no runway overlap is never even evaluated, a real (pre-existing, unchanged-by-UAC7) characteristic of this runner worth naming for future work.

**15. Result-reporting verdict: DONE.** `apply_result` entries now expose `routing_outcome`, `attachment_outcome`, `unknown_airport_candidate_id`, `unknown_airport_candidate_created`, `evidence_bag_snapshot_id` explicitly — verified directly by every new test's assertions on these exact fields.

**16. Preview/read-only verdict: PRESERVED.** `test_preview_of_unknown_candidate_case_creates_zero_rows` — a dry run whose plan says `would_form_unknown_airport_candidate=True` still creates zero rows of every kind (`Airport`, `SourceAssertion`, `UnknownAirportCandidate`, `IdentityGuardEvaluation`, `Signal`). `_extract_unknown_airport_candidate_seed()` reused for planning is a pure function with no I/O, confirmed by reading its implementation.

**17. Write-gate verdict: UNCHANGED.** `--apply`/`--allow-database-write` cross-requirement logic in `run_capture()` was not touched; both pre-existing tests (`test_apply_requires_allow_database_write`, `test_allow_database_write_requires_apply`) pass unmodified. No new automatic-write path was introduced — the UAC3 routing decision happens inside the same already-apply-gated code block as before.

**18. Network-gate verdict: UNCHANGED.** `--allow-live-network` logic was not touched; `test_live_network_requires_explicit_flag` and both dry-run-with-live-network tests pass unmodified. No test in this mission used live network; all used `fixture_documents` with a monkeypatched extractor.

**19. Expected-fingerprint verdict: EXTENDED, HIGH PRIORITY ITEM CLOSED.** This was the mission's own flagged high-priority risk. Before this change, the plan/fingerprint had **no visibility at all** into whether apply would route into the new UAC3 candidate-formation branch — `guard_outcome` alone didn't distinguish "insufficient identity, stays unresolved" from "insufficient identity, forms a governed candidate." Added `would_form_unknown_airport_candidate` to both `PlannedGovernedEvidence` and the fingerprint's hashed row tuple. It is itself content-derived (a pure function of the fragment plus the already-content-derived `guard_outcome`), so it does not reintroduce the target-DB-state dependency the fingerprint deliberately excludes elsewhere.

**20. Preview/apply state-change verdict: PROVEN CLOSED.** `test_topology_change_between_preview_and_apply_is_detected_not_silently_applied` — preview computes a fingerprint under "no known match" state; canonical topology is then mutated so the identical fragment would now `ATTACH_CONFIRMED` to a real known airport; applying with the stale fingerprint is refused (`FINGERPRINT_MISMATCH`), zero rows written. A fresh preview against the new state correctly reflects the flip and a fresh, correct apply succeeds normally. No TOCTOU gap exists between preview and apply for the new routing branch.

**21. Transaction/rollback verdict: PROVEN.** `test_runner_commit_boundary_rolls_back_whole_batch_on_uac3_failure` — two fragments in one apply call, the second's call into the orchestrator engineered to raise; the runner's single end-of-loop `session.commit()` is never reached, and the `Session` context manager's rollback-on-exit discards the first fragment's already-flushed work too. Zero rows of any kind survive. This is the runner's own commit-boundary guarantee, distinct from (and not a duplicate of) UAC3's own internal atomicity tests (`test_no_hidden_commit_rollback_undoes_everything`, `test_candidate_creation_and_assertion_persistence_share_one_atomic_unit`, already present in `tests/test_unknown_airport_discovery_integration.py`).

**22. Existing MAC regression verdict: ZERO DRIFT.** All 31 tests in `tests/test_capture_mac_discovery.py` pass; only one assertion needed updating (§9), and only for the JSON report shape, never for persisted DB state or guard/routing behavior.

**23. Information-firewall verdict: RESPECTED.** No test or production change in this mission executes UAC4 resolution, creates an Airport, runs EB4 reevaluation or EB5 resolution, creates an intelligence review, promotion, Signal, or publish action. `identity_guard_evaluations` count is asserted `== 0` in the strong-unknown test explicitly. Human review remains a separate, later, explicitly-gated step (`scripts/review_unknown_airport_candidate.py`), untouched.

## 9. Defects found and corrections made

One defect found and fixed during implementation, not left for the review checkpoint: the plan/fingerprint had zero visibility into the new UAC3 routing branch, meaning a topology change between preview and apply could — before the fix — have silently flipped the routing decision (e.g., from "would form a governed candidate" to "would attach to a known airport," or vice versa) without the fingerprint handshake catching it, since `would_form_unknown_airport_candidate` didn't exist as a field at all until this mission added it. Closed by adding the field to `PlannedGovernedEvidence`/the fingerprint hash and proving the fix with `test_topology_change_between_preview_and_apply_is_detected_not_silently_applied` (§8.20). No other defects found.

Two fixture-design issues surfaced and were corrected during test authoring (not code defects): (a) an initial ambiguous-known-identity fixture used runway pair "09/27," which the runway-identity normalizer's ambiguity handling silently excluded from `select_candidate_airports()`'s topology query, producing `UNKNOWN_AIRPORT_CANDIDATE` instead of the intended `AMBIGUOUS_KNOWN_IDENTITY` — switched to the already-proven-safe "4/22" designation used elsewhere in this test suite; (b) an initial all-conflict fixture supplied only an identifier code with no runway topology, so the runner's own topology-driven `select_candidate_airports()` never selected the seeded airport as a candidate at all (`candidates=[]`), producing `INSUFFICIENT_IDENTITY` instead of the intended `REJECT_CROSS_AIRPORT` — corrected by adding matching runway topology to the fixture so the candidate is actually selected, with the identifier mismatch then triggering the intended contradiction. Both are fixture-authoring corrections, not production-code defects; both also newly document (for future work, not fixed here) that this runner's candidate-selection is strictly topology/SFO-driven, never identifier-driven alone.

## 14. Adversarial review findings

An independent adversarial review pass re-derived every claim above from the diff and fresh code reading rather than trusting this report, per its own governing instruction. Two substantive findings, both addressed before commit:

**Fingerprint sufficiency (the review's primary target).** The review asked whether `would_form_unknown_airport_candidate` alone is sufficient fingerprint material, or whether a subtler TOCTOU gap survives — e.g., could preview name one candidate airport and apply silently name a different one, under the same fingerprint? Proven **not possible in production**: `CandidateFragment.fragment_hash` (already part of `fragment_identity`, already in the fingerprint since before UAC7) hashes `raw_text` only, and `extract_candidate_fragment()` is a pure function of `raw_text` (pdfplumber text → deterministic regex/rule extraction, no network, no LLM, no clock-dependent content). For identical `fragment_identity`, `airport_names` — and therefore the `UnknownAirportCandidate` seed's `raw_name` — is provably identical between preview and apply; the "Foo Airport at preview, Bar Airport at apply" scenario cannot occur through the real extractor.

However, the review then constructed the artificial case a non-pure or buggy extractor *could* produce — identical `fragment_identity` (same `artifact_identity`/`source_locator`/`raw_text`) but different `airport_names` returned on replay — and found that the fingerprint genuinely does **not** catch it (`test_evidence_bag_replay_conflict_fails_loud_through_runner_not_swallowed` proves `dry2["plan_fingerprint"] == dry["plan_fingerprint"]` despite the differing content). This is not a defect in the fingerprint design — it is a second, independent, deeper layer (EB3's `ConflictingEvidenceBagReplayError`, `app.services.discovery_evidence_persistence._reconcile_replay_snapshot()`, pre-existing and unmodified by UAC7) that exists precisely to catch content divergence the fingerprint's DB-state-independence deliberately cannot. The new test proves that deeper layer still fires correctly *through the runner*, including the harder case where `find_or_create_unknown_airport_candidate()` has already flushed a second candidate row before the conflict surfaces one call later — and that the runner's commit-boundary atomicity (§8.21) discards that partially-flushed row too. No fingerprint-material change was made; the finding is that the fingerprint and EB3's replay-conflict check are two complementary, independently-sufficient safety nets for two different failure classes (state changes vs. content non-determinism), and both were independently verified to hold.

**Test-quality gap.** `test_ambiguous_known_identity_through_runner_never_forms_candidate` originally verified only report fields and a candidate-table count, never the actual persisted `SourceAssertion` row for the ambiguous-known-identity branch. Strengthened to assert `airport_id is None`, `unknown_airport_candidate_id is None`, and `identity_guard_decision == "REVIEW_REQUIRED"` directly against the database, closing the "weak count-only assertions" risk the review was instructed to check for.

No defects were found in `unknown_airport_discovery_integration.py`, `discovery_evidence_persistence.py`, or `evidence_attachment_guard.py` (none were modified by UAC7, confirmed by `git status` showing zero changes outside the runner/tests/docs); no upstream UAC3/EB3 scope was widened. No EB4 auto-trigger, live pilot, or downstream automation was added, per the review's own explicit prohibition.

## 10. Test results

- Focused: `tests/test_capture_mac_discovery_uac7_wiring.py` — 9 passed (8 from implementation + 1 added during review).
- Combined: `tests/test_capture_mac_discovery_uac7_wiring.py` + `tests/test_capture_mac_discovery.py` + `tests/test_unknown_airport_discovery_integration.py` — 87 passed.
- Full suite (post-review, final): `python -m pytest -q` — **3105 passed, 0 failed**, 786.51s (0:13:06). Baseline before this mission was 3096; delta of exactly 9 matches the 9 UAC7 wiring tests now in the file (8 from implementation + 1 added during adversarial review). Zero regressions anywhere in the suite.
- `python -m py_compile` on all three touched/created files: clean.
- `git diff --check`: clean (only benign LF/CRLF line-ending warnings, no whitespace errors).

## 11. Real DB proof

Before and after this entire mission, `data/runway_safe.db`: SHA `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, size 2,097,152 bytes, `PRAGMA foreign_key_check=[]`, `integrity_check=ok` — byte-identical, independently re-verified after implementation. No live network was used anywhere in this mission; every test uses an isolated temp-file SQLite database and either `httpx.MockTransport` (pre-existing tests, unchanged) or a monkeypatched extractor (this mission's new tests).

## 12. Commit policy

Not committed, not pushed, per mission instruction. A separate UAC7 adversarial-review checkpoint will inspect and commit.

## 13. Recommended next step

UAC7 adversarial review checkpoint, followed (pending its own separate authorization) by Controlled Live Pilot #1 as designed in the Post-Rehearsal Operational Readiness Review — now upgradeable from Pilot 5A (known-airport-only) to full Pilot 5B (including unknown-airport routing), since the wiring gap that previously constrained the pilot's scope is now closed.
