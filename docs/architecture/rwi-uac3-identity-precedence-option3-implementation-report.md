# RWI UAC3 Identity Precedence — Option 3 Implementation

Status: IMPLEMENTED. NOT COMMITTED, NOT PUSHED — a separate, explicitly-authorized adversarial review governs this change.

## 1. Problem (from `docs/architecture/rwi-uac3-identity-precedence-review.md`)

Real Anoka case: `airport_names={"Anoka County-Blaine Airport"}`, `runway_pairs={"18/36"}`, several unrelated known airports sharing that common runway heading, none named Anoka. Old behavior: `REVIEW_REQUIRED`/`AMBIGUOUS_KNOWN_IDENTITY` — the explicit name is silently ignored. A more serious, related defect: a *single* non-ambiguous topology match with an unmatched explicit name reached `ATTACH_PROVISIONAL`/`KNOWN_CANONICAL_ATTACHMENT` — silently attaching to the wrong airport with no human review at all.

## 2. Locked rule (re-derived from the review, refined once during implementation)

The review's own literal text checks only NAME corroboration. Verifying it against the review's own required non-regression (§9: "exact identifier known match + unrelated name must remain unaffected") showed a NAME-only check would incorrectly override that case too — an identifier-confirmed candidate has no NAME evidence, only IDENTIFIER, so a NAME-only corroboration check would wrongly treat it as uncorroborated. **Refined to: the override fires only when NO supplied candidate's own positive evidence includes NAME *or* IDENTIFIER** — both are the guard's two "explicit identity claim" categories (as opposed to RUNWAY_TOPOLOGY/ISSUER/LOCATION, which corroborate but don't assert identity). This was verified empirically before implementation (see the review's own §5 matrix rows I and L) and again after.

Precise rule: `resolve_or_persist_discovery_identity()`'s known-or-ambiguous bucket is bypassed when (a) `best_outcome` is `ATTACH_CONFIRMED`/`ATTACH_PROVISIONAL`/`REVIEW_REQUIRED`, (b) at least one candidate was evaluated, (c) the fragment carries a formable name claim (`_extract_unknown_airport_candidate_seed()`, unmodified), and (d) no candidate's positive evidence includes NAME or IDENTIFIER.

## 3. Production files modified

- `app/services/unknown_airport_discovery_integration.py`: added `_EXPLICIT_IDENTITY_CATEGORIES`, `_any_candidate_has_explicit_identity_match()`, and the override logic in `resolve_or_persist_discovery_identity()`. `attachment_outcome` in the result still reports the true underlying guard verdict even when the override fires (audit transparency, mirroring the existing convention). `reason` text distinguishes the override case from the pre-existing "no known match at all" case.
- `scripts/capture_mac_discovery.py`: `plan_governed_persistence()`'s own `would_form_unknown_airport_candidate` mirror updated in lockstep, reusing the same new helper (imported, not duplicated). When the override applies, `candidate_id` (and therefore `attached_airport_id`/`attached_airport_code`) is reset to `None` so the preview never claims an attachment apply will not actually make.

**`evidence_attachment_guard.py` was not modified** — confirmed by `git status`. All per-candidate evidence evaluation, contradiction semantics, and cross-candidate ambiguity resolution remain exactly as before; this change only decides which bucket UAC3's routing is allowed to trust.

## 4. Tests modified/created

- `tests/test_unknown_airport_discovery_integration.py`: new `TestIdentityPrecedenceOption3` class (9 tests) — real Anoka shape, single-wrong-topology safety proof, exact-name-with-topology-collision non-regression, identifier-contradiction non-regression, identifier-known-match-with-unrelated-name non-regression, issuer+topology-confirmed-with-unrelated-name (the deeper row-L risk), location-only non-regression, two-explicit-names firewall (two variants), and a no-downstream-side-effects/EMAS-firewall proof.
- `tests/test_capture_mac_discovery_uac7_wiring.py`: split the pre-existing ambiguous-identity test into a genuine "no name" non-regression and a new "uncorroborated explicit name" test proving the new behavior through the real runner; added a single-wrong-topology-through-the-runner test and a fingerprint-change test proving preview/apply consistency across the routing flip.

## 5. Results

**Anoka result**: `UNKNOWN_AIRPORT_CANDIDATE`, `attachment_outcome=REVIEW_REQUIRED`, `attached_airport_id=None`, candidate seeded with `raw_name="Anoka County-Blaine Airport"`. Confirmed both via synthetic fixture (unit tests) and via a read-only replay of the real, previously-fetched Anoka PDF against the real DB (§9 below) — no new live network access needed, since the artifact was already cached from the 5F mission.

**Single-wrong-topology result**: `UNKNOWN_AIRPORT_CANDIDATE`, `attachment_outcome=ATTACH_PROVISIONAL`, `attached_airport_id=None` — the wrong-attachment risk is closed.

**Exact-name known non-regression**: unaffected, still `KNOWN_CANONICAL_ATTACHMENT`/`AMBIGUOUS_KNOWN_IDENTITY` exactly as before in every tested shape.

**No-name non-regression**: unaffected in all tested shapes — the override never engages without a formable name claim.

**Multi-name firewall**: unaffected — UAC3's pre-existing "exactly one name" formability rule still governs; Option 3 never widens it. (One test correction made during implementation: a fixture I initially wrote incorrectly expected `UNRESOLVED_IDENTITY` for a two-name fragment whose topology still matched a candidate — the correct, unaffected behavior there is that the known-match path proceeds via topology alone, since multi-name ambiguity is only ever inspected on the "no known match" path. Fixed the test, not the code.)

**Identifier precedence verdict**: fully preserved — both the auto-veto (contradiction) and the "identifier alone is definitive" rule remain completely untouched and unaffected by the new override in every tested shape, including the specific case (row I) that motivated broadening the corroboration check beyond NAME alone.

**EMAS relevance firewall verdict**: confirmed — no field resembling `emas_relevant` exists on `UnknownAirportCandidate`; no call to UAC4, no Airport/Signal creation, no intelligence review/promotion/publish anywhere in the changed code, in either production file.

**Product principle, stated explicitly (adversarial review addition):** `UNKNOWN_AIRPORT_CANDIDATE` means exactly one thing — *"the source evidence identifies an airport that is not currently canonical in RWI."* It is not, and this change does not make it, any of the following three genuinely distinct governance questions:

- **IDENTITY DISCOVERY** (what this change does): does the evidence name a specific airport RWI doesn't already know about?
- **EMAS BUSINESS RELEVANCE** (not decided here, not even touched): does that airport have a genuine EMAS opportunity worth RWI's attention?
- **CANONICAL AIRPORT ADMISSION** (not decided here, not even touched): should that airport ever become a real, canonical `Airport` row in RWI's database?

Anoka County-Blaine Airport forming an `UnknownAirportCandidate` under Option 3 says nothing about whether it belongs in RWI's canonical fleet, and nothing in this change pre-decides that future policy — `CREATE_NEW_AIRPORT` remains a separate, human-gated, UAC4-only action, entirely untouched by this mission.

**UAC7 mirror verdict**: implemented, reusing the same helper (not a parallel algorithm) — `_any_candidate_has_explicit_identity_match()` is imported directly into `capture_mac_discovery.py`, matching the file's own established "reused directly, never duplicated" convention already used for `_extract_unknown_airport_candidate_seed()`.

**Preview/apply consistency verdict**: proven for the Anoka shape, the single-wrong-topology shape, the exact-name shape, and the no-name shape — no branch disagreement found in any tested case, and confirmed again via the real-document replay.

**Fingerprint verdict**: confirmed the fingerprint changes when Option 3 flips routing (both via a synthetic test and via the real Anoka document — old fingerprint `91e1f5de...`, new fingerprint `80b6724b...`), and confirmed a stale pre-change fingerprint is still correctly refused (`FINGERPRINT_MISMATCH`, zero writes) — no new fingerprint field was needed; the existing material already captures the change correctly, exactly as the design review anticipated.

**Replay/non-retroactivity verdict**: unaffected — no EvidenceBag content changes; the existing "never rewrite an already-resolved SourceAssertion" rule is untouched. No already-persisted real data is known to be affected (no MAC document with populated `airport_names` has ever actually been applied to the real database).

**Known-workflow regression verdict**: zero regressions found across MSP/SFO-style known-canonical, unresolved, ambiguous, and strong-unknown fixture suites — see the focused/full test results below.

## 6. Defects found and corrections made

One defect found and fixed **before** implementation, by verifying the review's own literal rule against its own required non-regression case: a NAME-only corroboration check would have broken identifier-confirmed known-airport attachment whenever an unrelated name was also present. Fixed by broadening the check to NAME-or-IDENTIFIER, matching the guard's own "explicit identity claim" category grouping. This is documented in §2, not a late-stage correction.

One test-authoring error found and fixed during test-writing, not a code defect: an initial multi-name regression test incorrectly expected `UNRESOLVED_IDENTITY` when topology still matched a candidate; corrected per §5's explanation.

One pre-existing test (`test_ambiguous_known_identity_through_runner_never_forms_candidate`) whose fixture happened to be structurally identical to the real Anoka case failed after the change — correctly, not a regression. Split into two tests: the original intent (pure "no name" ambiguity) preserved unchanged, and a new test confirming the now-correct "uncorroborated name" behavior.

## 7. Focused tests

`tests/test_unknown_airport_discovery_integration.py`: 57 passed. `tests/test_capture_mac_discovery_uac7_wiring.py`: 12 passed. `tests/test_capture_mac_discovery.py`: unchanged, passing. Combined targeted run: 86 passed, 0 failed.

## 8. Full pytest, py_compile, git diff --check

See the mission's own final report for exact numbers (recorded after this document was written).

## 9. Optional real Anoka read-only replay — performed, no new live network needed

The real Anoka PDF fetched during the 5F mission is already committed as `tests/fixtures/mac_granicus_anoka_runway_18_36_bid_memo_sample.pdf`. Re-ran extraction, candidate selection, guard evaluation, `plan_governed_persistence()`, and a read-only (rolled back, never committed) call to `resolve_or_persist_discovery_identity()` against the real database's real candidate set. Result: `UNKNOWN_AIRPORT_CANDIDATE`, `would_form_unknown_airport_candidate=True` in preview, exact agreement between preview and the orchestrator's own routing. Real DB confirmed byte-identical before and after (SHA `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, `unknown_airport_candidates` count unchanged at 1 — the pre-existing Controlled Rehearsal #1 row, not a new one).
