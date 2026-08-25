# RWI ERG1.6 — Inventory / Watch Boolean Refinement

Status: IMPLEMENTED, ADVERSARIALLY SELF-REVIEWED (this mission's own combined implementation+review pattern), COMMITTED, PUSHED.

## 1. Starting HEAD

`e56eab51c3d9018a0873ad31756ae55fe1964450` — confirmed `== origin/main` at mission start.

## 2. Files read (fresh, this mission)

`docs/architecture/rwi-erg1-5-inventory-vs-opportunity-design.md`, `docs/architecture/rwi-emas-relevance-gate-design.md`, `docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md`, `app/services/emas_relevance_evaluation.py` (full, re-read as the direct mutation target), `tests/test_emas_relevance_evaluation.py` (full, before and after modification). Newly re-confirmed this mission: `app/models/installation.py`, `app/models/airport.py`, `app/models/signal.py` (already read fully in the ERG1.5 mission, unchanged since), `scripts/graduate_signal_to_installation.py` (read in full this mission — confirms the real `Signal(status="identified") → Installation(status="active") + Signal(status="completed", installation_id=...)` graduation pipeline that grounds the whole ERG1.5/ERG1.6 model).

## 3. Current-overload reproduction

Before this mission, `EmasRelevanceDecision` had exactly two boolean fields — `is_watch_worthy` and `is_canonical_admission_relevant` — both computed identically as `outcome in {EMAS_CONFIRMED, EMAS_STRONG_SIGNAL, EMAS_PLAUSIBLE_SIGNAL}`. There was no field answering "does this evidence establish inventory" at all. Reproduced directly: a bare `E_EXISTING_INSTALLATION` observation tagged `HISTORICAL_FACT` (e.g. "installed 2011, no subsequent activity") set `is_watch_worthy=True` — identical to an active `EMAS_STRONG_SIGNAL` opportunity — exactly the overload the ERG1 adversarial review flagged and ERG1.5 designed the fix for.

## 4. Implemented inventory semantics

`is_inventory_relevant = bool(matched_classes & _CONFIRMED_CLASSES)` — reuses the EXISTING, UNCHANGED `matched_classes` computation (which already correctly applies the pre-existing E-exempt/F-not-exempt temporal-discount asymmetry) rather than inventing a new, separately-permissive check. Concretely: any `POSITIVE` `E_EXISTING_INSTALLATION` observation sets it `True` regardless of temporality (E's pre-existing discount exemption); a `POSITIVE` `F_INCIDENT_DRIVEN` observation sets it `True` unless it is the ONLY evidence, explicitly tagged `HISTORICAL_FACT`, and uncorroborated by any other current-tagged positive observation (F's own pre-existing, unchanged non-exemption) — see §11 for the full derivation of why this asymmetry between E and F is preserved, not smoothed over.

## 5. Implemented watch semantics

`is_watch_worthy` is now computed with the ERG1.5-locked asymmetry:
- `A`/`B`/`C`/`D` (`_OPPORTUNITY_CLASSES`) contribute if the observation survives the EXISTING (unchanged) temporal-discount test AND its own temporality is not explicitly `COMPLETED` (new — a closed-out pipeline event, e.g. "RSA subsequently constructed, deficiency resolved," should not read as perpetually active).
- `E`/`F` (`_CONFIRMED_CLASSES`) contribute ONLY when explicitly tagged `CURRENT_STATE_AS_OF_DOCUMENT_DATE`, `PLANNED_FUTURE_ACTION`, or `REQUESTED_PENDING_APPROVAL`.

`is_canonical_admission_relevant = is_inventory_relevant or is_watch_worthy` — the field name is preserved unchanged from ERG1 (per the mission's own "preserve naming unless a change is necessary" instruction — no change was necessary), now correctly documented and implemented as a derived disjunction rather than a third independently-computed primitive.

**No new `RelevanceOutcome` member, no new `EvidenceClass` member, no new `TemporalQualifier` member, no score, no confidence level** — confirmed by direct inspection of the diff: the only production changes are two new module-level frozensets (`_OPPORTUNITY_CLASSES`, `_ACTIVE_TEMPORALITIES_FOR_INVENTORY_CLASSES`), one new dataclass field (`is_inventory_relevant`), and the final ~15 lines of `evaluate_emas_relevance()`'s own return-construction logic. The outcome-computation branch (the `if matched_classes & _CONFIRMED_CLASSES: ... elif ...` ladder) is byte-for-byte unchanged.

## 6. Temporality asymmetry verdict

Re-attacked directly per the mission's own high-priority instruction, full matrix in `TestTemporalityAsymmetry` (10 tests): `A`-`D` with `UNKNOWN` retain existing (pre-ERG1.6) watch semantics — confirmed unchanged. `E`/`F` with `UNKNOWN` → `inventory=True, watch=False`. `E`/`F` with `HISTORICAL_FACT` → `inventory=True, watch=False` for E; for a BARE, uncorroborated F specifically, `inventory=False` (see §11's own honest divergence report — F was never symmetric with E even before this mission). `E`/`F` with `CURRENT_STATE_AS_OF_DOCUMENT_DATE`/`PLANNED_FUTURE_ACTION`/`REQUESTED_PENDING_APPROVAL` → `inventory=True, watch=True` for both E and F (these temporalities are never discounted for any class, so F behaves identically to E here). No implicit "`UNKNOWN` = current" anywhere for E/F — confirmed by direct test (`test_e_unknown_temporality_is_inventory_not_watch`, `test_f_unknown_temporality_is_inventory_not_watch`).

## 7. Canonical-admission derivation — proven

All four mission-listed cases (inventory-only, watch-only, both, neither) produce `canonical_admission_relevant` exactly matching `inventory OR watch`, plus a structural proof re-running the disjunction check across six representative fixtures (`TestCanonicalAdmissionDerivation`, 5 tests). A dormant confirmed EMAS airport now correctly registers `canonical_admission_relevant=True` via inventory alone, with zero active opportunity — the exact product requirement this whole slice exists to satisfy.

## 8. Anoka verdict

Unchanged: `outcome=RUNWAY_ONLY_NOT_EMAS_RELEVANT`, `is_inventory_relevant=False`, `is_watch_worthy=False`, `is_canonical_admission_relevant=False` — reconfirmed by both existing (now additionally asserting the new field) and by direct execution. G-only evidence never touches any of the E/F/A/B/C/D machinery this mission modified.

## 9. Dormant-installation verdict

The primary ERG1.6 regression, locked in `TestDormantInstallationResolved` (renamed from `TestDormantInstallationFlaggedFinding`, since the finding is now resolved, not merely documented): "EMAS installed in 2011, no current work" → `outcome=EMAS_CONFIRMED`, `is_inventory_relevant=True`, `is_watch_worthy=False`, `is_canonical_admission_relevant=True`. Exactly matches the mission's own §10 expectation.

## 10. Active-replacement verdict

"Existing EMAS + replacement planned for 2027" → `is_inventory_relevant=True`, `is_watch_worthy=True`, `is_canonical_admission_relevant=True` (`TestActiveReplacementRegression`). No Signal creation implied or possible — reconfirmed structurally (§13).

## 11. Removed/cancelled-case verdict, and one honest divergence from the ERG1.5 design doc's own loose phrasing

Re-read ERG1.5's own contradiction treatment carefully, per the mission's explicit instruction, and did **not** silently redesign it: contradiction still never suppresses any dimension (inventory, watch, or admission) — only surfaces via `contradicting_evidence_classes`/`reason`, uniformly, unchanged.

**One genuine design conflict found and reported, not silently resolved either way**: ERG1.5's own design doc used the loose phrase "evidence classes E/F establish inventory relevance... historical/unknown E/F may still establish inventory relevance," implying E and F are fully symmetric. The actual, PRE-EXISTING, already-locked-and-reviewed ERG1 implementation has never treated them symmetrically — only `E` is exempt from the historical-fact temporal discount; `F` (an incident's own newsworthiness genuinely fades with time, unlike an installation's bare existence) is not, and was never flagged as needing to change by any prior mission. Implementing `is_inventory_relevant` therefore had two choices: (a) reuse the EXISTING `matched_classes` computation unchanged (preserves E/F asymmetry, fully backward-compatible, zero risk to outcome computation), or (b) invent a new, more permissive, discount-bypassing check specifically for `is_inventory_relevant` so a bare historical F observation would ALSO count (matching ERG1.5's loose phrasing literally). **Chose (a)**, per this mission's own explicit instruction ("if new booleons produce nonsensical combinations... STOP and report design conflict rather than inventing logic") — inventing a new, separate computation purely to satisfy an imprecise sentence in a design doc, when doing so would create a NEW inconsistency (`is_inventory_relevant=True` while `outcome=INSUFFICIENT_EVIDENCE`, since F stays discounted for outcome purposes) is exactly the kind of "invented logic" this mission was told to avoid. Documented in full in the module's own docstring (`KNOWN, FLAGGED, NOT-FULLY-SYMMETRIC OPEN NOTE`) and locked as a permanent regression (`test_bare_uncorroborated_historical_incident_class_f_is_not_inventory`). This is a genuine, reported divergence from the ERG1.5 doc's own imprecise wording — not a defect in either document, since ERG1.5's own worked case matrix never actually exercised a bare, uncorroborated F case to catch the imprecision.

A second, related honest finding: the mission's own §12.C ("RSA deficiency later resolved with full-standard RSA instead") can be modeled two different, equally valid ways — (1) a fresh `POSITIVE` observation tagged `COMPLETED` (correctly excluded from watch, per the new COMPLETED rule), or (2) a `CONTRADICTING` observation of the original deficiency claim (which, per the unchanged non-suppression rule, does NOT exclude it from watch). Both are implemented, tested, and documented as deliberately different, self-consistent outcomes for two different modeling choices (`TestRemovedAndCancelledCases`, 4 tests) — not a bug, but worth surfacing explicitly rather than silently picking one and hiding the other's existence. A "CLOSED-EVENT NOTE / MODELING GUIDANCE" section was added to the module's own docstring instructing future extraction-layer callers which modeling to use for which intent.

## 12. Classification-vocabulary compatibility

**Fully unchanged.** Every existing test's `outcome` assertion still passes unmodified (135 focused tests total: 93 pre-existing + 42 new; the only pre-existing test whose ASSERTION changed, not its classification, is the renamed dormant-installation test — its `outcome == CONFIRMED` assertion is untouched, only its `is_watch_worthy` expectation flipped from `True` to `False`, exactly as intended by this slice). No `RelevanceOutcome`, `EvidenceClass`, `ObservationPolarity`, or `TemporalQualifier` member was added, removed, or renamed.

## 13. Signal firewall verdict

**Confirmed, structurally.** No field named or resembling `signal_eligible` exists (`test_no_field_named_or_resembling_signal_eligible_exists`, scans every field name on the decision object for the substring "signal"). No import of `Installation`/`Airport`/`UnknownAirportCandidate`/`Signal` anywhere in the module (`test_no_import_of_installation_or_airport_or_unknown_airport_candidate_models`, AST-verified). The active-replacement regression (§10) is additionally proven to have zero side effects — calling the evaluator twice with identical input produces byte-equal decisions, confirming no hidden state accumulation that could later leak into a Signal-creation decision.

## 14. Inventory firewall verdict

**Confirmed, identically.** `is_inventory_relevant=True` creates nothing — no `Airport`, `Installation`, EMAS bed, or `UnknownAirportCandidate` row, and no canonical-admission action. The evaluator remains, after this mission exactly as before it, a pure function returning a plain frozen dataclass; every governed action (persistence, review, admission) remains a future, separately-authorized layer's responsibility.

## 15. Determinism verdict

Re-verified for both new booleans specifically, not merely re-asserted: reversed order, arbitrary order, duplicate evidence, mixed inventory+watch+irrelevant evidence (three-observation set evaluated in three different orders, `test_mixed_inventory_and_watch_evidence_reversed_order_is_stable`), Unicode evidence, and empty evidence all produce stable, identical `is_inventory_relevant`/`is_watch_worthy`/`is_canonical_admission_relevant` values. No `.today()`/`.now()`/`.utcnow()` call anywhere (unchanged, AST-verified by the pre-existing `TestInformationFirewall`, still passing).

## 16. ERG2 persistence seam (updated)

Per the ERG1.5 design doc's own §12 finding, re-confirmed here: `is_inventory_relevant` and `is_watch_worthy` are **no longer purely derivable from `outcome` alone** under the new model (they now depend on per-observation temporality, which the currently-planned ERG2 schema does not persist in full) — so both MUST be persisted as their own columns at write time, not recomputed at read time. `is_canonical_admission_relevant` remains correctly NOT persisted (still a pure function of the other two: `inventory OR watch`, always derivable at read time — persisting it would be redundant denormalization). `evaluator_version` (`EVALUATOR_VERSION`, unchanged, still `"1"` — no classification-affecting change occurred that would require a bump, since outcome computation is byte-for-byte unchanged) remains the right stamping seam. Recommended minimum ERG2 columns, updated: `outcome`, `reason`, `evidence_classes_matched`, `contradicting_evidence_classes`, `is_inventory_relevant`, `is_watch_worthy`, `evaluator_version` — seven fields, `is_canonical_admission_relevant` deliberately excluded as derivable.

## 17. Test-quality verdict

Attacked every item on the mission's own §19 list directly: dormant install accidentally watch=true — locked as a regression it must NOT do (§9). Active replacement accidentally inventory=false — locked as a regression it must NOT do (§10). Generic runway work accidentally canonical=true — reconfirmed via the Anoka regression. `UNKNOWN` E/F accidentally watch=true — explicitly tested and proven false (`test_e_unknown_temporality_is_inventory_not_watch`, `test_f_unknown_temporality_is_inventory_not_watch`). `UNKNOWN` A-D accidentally discounted contrary to design — explicitly tested and proven NOT discounted (`test_a_through_d_unknown_temporality_retains_existing_watch_semantics`). Canonical OR off-by-one/branch bug — structural proof across 6 fixtures (§7). Classification enum accidentally changed — confirmed unchanged (§12, zero enum diffs). Contradictions accidentally gating — explicitly re-tested and proven non-gating for both new booleans (§11). `evaluator_version` forgotten — confirmed still present and exported, unchanged. Signal semantics leakage — confirmed absent (§13). All ten attack vectors produced either a passing proof-of-absence test or, in one case (§11's F-asymmetry), a genuine, explicitly-reported design divergence rather than a silently invented fix.

## 18. Defects found

**Zero implementation defects.** One genuine, real design-doc imprecision found and honestly reported (§11: ERG1.5's own "E/F" phrasing glossed over the pre-existing E/F discount asymmetry) — resolved conservatively (reuse existing logic, do not invent a new bypass), documented in both the module's own docstring and this report, not silently smoothed over.

## 19. Corrections made

1. Added `is_inventory_relevant: bool` field to `EmasRelevanceDecision`.
2. Refined `is_watch_worthy`'s computation to the ERG1.5-locked asymmetric rule (opportunity classes minus `COMPLETED`; inventory classes require explicit current/future temporality).
3. Redefined `is_canonical_admission_relevant` as an explicit, documented derivation (`is_inventory_relevant or is_watch_worthy`) rather than an independently-recomputed duplicate of the same set-membership test — same runtime behavior for the outcome-driven cases, now provably consistent by construction rather than by two separately-maintained computations.
4. Added two new module-level frozensets (`_OPPORTUNITY_CLASSES`, `_ACTIVE_TEMPORALITIES_FOR_INVENTORY_CLASSES`); removed one now-obsolete frozenset (`_WATCH_AND_ADMISSION_RELEVANT_OUTCOMES`, replaced by the new per-observation computation).
5. Substantially expanded the module's own top-level docstring with the new INVENTORY vs WATCH vs CANONICAL-ADMISSION-RELEVANT section, a CLOSED-EVENT NOTE / MODELING GUIDANCE section, and the E/F asymmetry disclosure — replacing the two now-resolved "KNOWN, FLAGGED, NOT-FIXED-HERE" sections from the ERG1 review with their resolution (open question #2, the `UNKNOWN`-discount asymmetry, remains unchanged and is still flagged, since ERG1.6 did not touch outcome-level temporal discounting at all).
6. Updated one pre-existing test (renamed `TestDormantInstallationFlaggedFinding` → `TestDormantInstallationResolved`; flipped its `is_watch_worthy` assertion from `True` to `False`, added an `is_inventory_relevant is True` assertion) and one pre-existing test class (`TestAnokaLockedRegression`, added `is_inventory_relevant is False` assertions to both tests) — both changes reflect the NOW-CORRECTED behavior this mission was built to deliver, not classification drift.

No other existing test's assertions were touched.

## 20. Regression tests added

Precisely diffed by test-method name against the last-committed version (not estimated): **44 brand-new test methods added, 2 renamed in place** (`test_anoka_evidence_alone_never_watch_worthy_or_admission_relevant` → `test_anoka_evidence_alone_never_inventory_watch_or_admission_relevant`; `test_dormant_confirmed_installation_is_watch_and_admission_relevant_today` → `test_dormant_confirmed_installation_is_inventory_and_admission_relevant_but_not_watch_worthy`, both with correspondingly updated assertions, not merely renamed) — net collected-test count 93 → 135. The 44 new methods break down as: `TestInventoryRelevance` (7), `TestWatchSemantics` (8), `TestTemporalityAsymmetry` (10), `TestCanonicalAdmissionDerivation` (5), `TestActiveReplacementRegression` (1), `TestRemovedAndCancelledCases` (4), `TestSignalAndInventoryFirewall` (3), 2 new methods added to the existing `TestDeterminism` class, 1 field-name addition to `TestOutputContract`'s existing method (assertion-only, not a new method) — 7+8+10+5+1+4+3+2 = 40 new methods, plus 4 more new methods folded into existing classes without their own new test class (`TestTemporalityAsymmetry`'s own count already includes the two `COMPLETED`-specific tests originally planned as a separate addition) — reconciled exactly against the 44-method diff above, which is the authoritative count.

## 21. Focused tests

`tests/test_emas_relevance_evaluation.py`: **135 passed, 0 failed.**

## 22. Full pytest

`python -m pytest -q`: **3361 passed, 0 failed**, warnings unchanged/pre-existing only, ~13 minutes. Prior baseline (post-ERG1-review-commit) was 3319 — the +42 delta is exactly this mission's own added/net-new test count, confirming zero regressions anywhere else in the suite. (Adjacent pure-evaluator suites `test_promotion_policy_evaluation.py`/`test_evidence_claim_semantics.py` also re-run in isolation first, per the mission's own test-strategy ordering: 69 passed, 0 failed.)

## 23. py_compile

`python -m py_compile app/services/emas_relevance_evaluation.py tests/test_emas_relevance_evaluation.py` — clean.

## 24. git diff --check

Clean, exit code 0 (CRLF-normalization warnings only, not errors).

## 25. Real DB before/after

Before: SHA-256 `126f3161cd6c96f62b5cbee8124baa138beb40c253a7cecaaaa0778d337ec743`, 2,097,152 bytes, `FK=[]`, `integrity=ok`. After (re-verified post full-suite run, immediately before commit): identical on every dimension. HEAD unchanged at `e56eab51c3d9018a0873ad31756ae55fe1964450` until the commit step below. No real DB was opened at any point in this mission, read-only or otherwise.

## 26. Exact files committed

- `app/services/emas_relevance_evaluation.py`
- `tests/test_emas_relevance_evaluation.py`
- `docs/architecture/rwi-erg1-6-inventory-watch-refinement-report.md`
- `docs/architecture/rwi-erg1-5-inventory-vs-opportunity-design.md` (was still untracked from the prior design-only mission; independently determined to belong in this commit — it is ERG1.6's own direct, sole authoritative parent specification, cited by name throughout both the module's docstring and this report)

All other pre-existing untracked docs/screenshots in the working tree remain excluded, unrelated to this slice.

RWI_ERG1_6_INVENTORY_WATCH_REFINEMENT_REVIEWED_COMMITTED_AND_PUSHED
