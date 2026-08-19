# SignalCandidate evaluation core — Slice 3 report

Slice 3 of the roadmap in [evidence-to-signal-semantics-design.md](evidence-to-signal-semantics-design.md),
consuming claims produced by the Slice 2 MAC adapter
([mac-granicus-claim-extraction-slice2-report.md](mac-granicus-claim-extraction-slice2-report.md)) via
the unmodified Slice 1 core
([evidence-claim-semantics-core-report.md](evidence-claim-semantics-core-report.md)).

## 1. Starting HEAD

`425a0143fd11b2c50635c414c3e27b5df6760765` ("Add MAC evidence claim extraction"), branch `main`,
matched `origin/main`. Baseline full pytest: 886 passed, verified before implementation began.

## 2. API

```python
def evaluate_signal_candidate(
    claims: tuple[Claim, ...],
    context: SignalCandidateContext,
) -> SignalCandidateDecision
```

in `app/services/signal_candidate_evaluation.py`. `SignalCandidateContext(identity_decision:
AttachmentOutcome, superseded: bool = False, superseded_reason: str | None = None)` is the minimal
explicit identity/staleness input the task's own §7/§14 asked for — no `SourceAssertion`, no DB.

## 3. Decision outcomes

`SignalCandidateOutcome`: `REVIEW_REQUIRED`, `INSUFFICIENT_MATERIALITY`, `IDENTITY_NOT_CONFIRMED`,
`CONTRADICTED`, `DUPLICATE_WITHIN_EVIDENCE`, `STALE_OR_SUPERSEDED` — the exact six from the design
doc's §10 table, none renamed, none dropped, no `AUTO_PROMOTE` added. No implementation pressure
surfaced to make any outcome semantically redundant, so no design-vocabulary change was needed or
proposed.

## 4. Identity gate

Reuses `app.services.evidence_attachment_guard.AttachmentOutcome` directly (both pure, dependency-free
modules) rather than inventing a second, parallel identity-state enum. Only `ATTACH_CONFIRMED` is
treated as sufficient to run intelligence review — `ATTACH_PROVISIONAL`, `REVIEW_REQUIRED`,
`REJECT_CROSS_AIRPORT`, and `INSUFFICIENT_IDENTITY` all resolve to `IDENTITY_NOT_CONFIRMED`. This is
deliberately **stricter** than the design doc's own §10 table (which allowed `ATTACH_PROVISIONAL`
through), per this slice's own explicit instruction ("ATTACH_PROVISIONAL must NOT be treated as
confirmed unless the approved architecture explicitly allows it... prefer fail-closed"). The gate runs
before claims are even inspected — proven by `test_identity_not_confirmed`
(`material_claims == ()`).

## 5. Materiality rules

Explicit rule combination, no fuzzy scoring (`_is_materially_interesting`): there must be at least one
`EXPLICIT_DOCUMENT_FACT`/`PROCEDURAL_REQUEST` claim ("something is asserted or requested") **and** at
least one of:
- a second, distinct `EXPLICIT_DOCUMENT_FACT`/`PROCEDURAL_REQUEST` claim sharing the same `subject`
  (task combo A — lifecycle expiry + replacement required, both about the same physical thing),
- any claim carrying a `financial` fact (combo C — procurement + financial),
- any claim carrying a `relationship` fact (combo B — replacement + named vendor),
- any claim carrying a non-`UNKNOWN` `temporal` qualifier (combo D — future/completed activity + event
  context).

A single bare event/action claim with no corroborating structure (§19's "generic EMAS mention,"
"static runway inventory fact," "old non-material descriptive fact") does not clear the bar. A
relationship or financial claim with no event/action claim at all (§19's "generic vendor mention,"
"airport identity only") does not clear it either — `Claim`'s category dimension is required, not
optional, for materiality.

**Documented gap, not solved**: `suggested_signal_category` is never populated in this generation.
`Claim` (Slice 1) has no structured "event type" field — populating it would require either reading
raw document text (this module never does) or hard-coding one source family's own role vocabulary
(e.g. MAC's `advance_deposit_purchase_order`) into an otherwise source-agnostic module, which would
violate task §21's "do not generalize the MAC adapter prematurely." Every `REVIEW_REQUIRED` decision
carries an explicit `warnings` entry stating this rather than silently guessing — per task §15's own
instruction ("if category vocabulary is insufficient: report it, do not silently mutate Signal
schema").

## 6. Financial safety

`_financial_summary()` de-duplicates by `(semantic_role, amount, currency)` and renders each distinct
financial fact as `role=amount currency` — never merged, never renamed to a different role. For MSP,
the reason string contains both `advance_deposit_purchase_order=1590000.00 USD` and
`cip_project_ceiling=19000000.00 USD` as two separate segments; no code path anywhere produces a
`contract_value`/`vendor_revenue` string, since the module only ever echoes `FinancialFact.semantic_role`
values that already exist on the input claims — it never re-labels or infers one.

## 7. Procedural safety

`_procedural_summary()` lists `PROCEDURAL_REQUEST` claim subjects under a fixed, honest template
segment: `"Procedural request(s), pending - not approved/awarded/executed: ..."`. The template string
never varies based on claim content — it is always this exact negation — so a still-pending PO request
can never be rendered as "approved" or "awarded" regardless of what other claims are present. Proven by
`test_procedural_request_preserved`.

## 8. Temporal safety

`_temporal_summary()` renders each claim's own `temporal.qualifier.value` and `as_of_date` verbatim,
never recomputing or reinterpreting either. `PLANNED_FUTURE_ACTION` stays `PLANNED_FUTURE_ACTION`
regardless of the real system date the evaluation runs under — proven directly by
`test_planned_future_claim_unaffected_by_real_system_date`, run under the actual 2026-08-19 session
date. AST-verified: zero `.today()`/`.now()`/`.utcnow()` calls anywhere in the module.

## 9. Combination rules

All five of task §11's named combinations map onto §5's rule set: A (lifecycle+replacement) →
same-subject-pair; B (replacement+vendor) → event + relationship; C (procurement+financial) → event +
financial; D (future installation+replacement context) → event + active-temporal; E
(completion/status change + prior context) → event + active-temporal (a `COMPLETED` qualifier is
non-`UNKNOWN`, so it counts identically to a planned/historical one). No MSP-specific string or role
name appears anywhere in the rule logic — confirmed by inspection (`grep` for "MAC"/"MSP"/"Runway
Safe"/"EMAS" inside `signal_candidate_evaluation.py` returns nothing).

## 10. Duplicate handling

`unique_claims = tuple(dict.fromkeys(claims))` de-duplicates via the claim core's own frozen/hashable
structural equality before any materiality check runs — corroborating evidence is never double-counted.
`DUPLICATE_WITHIN_EVIDENCE` fires specifically when the **entire** input collapses to one distinct claim
repeated (`len(unique_claims) == 1 and len(claims) > 1`) — a narrow, literal reading of task §12 ("the
candidate consists **only** of duplicate copies of already-equivalent evidence"). A duplicate claim
alongside genuinely distinct evidence is silently de-duplicated and evaluation proceeds normally —
proven by `test_duplicates_alongside_distinct_evidence_still_counted_once`.

## 11. Contradiction handling

One narrow rule (`_detect_contradiction`): if two claims in the same set name **different parties** for
the **identical relationship role on the identical subject**, the set cannot be safely interpreted (who,
in fact, holds that role?) → `CONTRADICTED`. Same party on a different subject is not a contradiction
— proven directly. **Documented limitation** (task §13's own explicit allowance): this is the one
contradiction shape `Claim`'s existing structural fields (subject + role + party) can express without
inference. Broader contradictions — e.g. one claim implying a project was cancelled while another
implies it proceeds — are not detected, because the Claim Core has no negation/status-change vocabulary
to express that safely today. No NLP or keyword-based conflict inference was added to work around this;
the gap is reported, not silently patched.

**Checkpoint review correction**: `material_claims` on a `CONTRADICTED` decision originally included
**every** relationship claim in the input set, not just the ones actually in conflict — a caller
combining several unrelated relationship claims (e.g. a real conflict about "EMAS bed" sole-source
vendor alongside an unrelated "Terminal roof" oversight claim) would have seen the unrelated claim
lumped into `material_claims` even though it had nothing to do with the contradiction. Fixed
`_detect_contradiction` to return only the specific claims sharing the conflicting `(subject, role)`
key. Added `test_material_claims_excludes_unrelated_relationship_claims` as a regression test. Does
not change the outcome or reason text for any existing case, including the real MSP set (which has no
contradiction at all).

## 12. Stale/superseded handling

`STALE_OR_SUPERSEDED` fires only from the explicit `context.superseded` flag — never from a claim's own
`as_of_date` being old. `test_old_date_alone_does_not_trigger_staleness` proves a claim dated 1999
evaluated today does not trigger this outcome by itself.

## 13. MSP golden-case result

`extract_mac_claims()`'s real 7-claim output (A, B, C, D, F, H, I from Slice 2), evaluated with
`identity_decision=ATTACH_CONFIRMED`, produces `REVIEW_REQUIRED`. Reason text (abridged):

> Material claim combination found: 2 category-qualifying claim type(s) present
> (explicit_document_fact, procedural_request). Financial roles (kept structurally distinct):
> advance_deposit_purchase_order=1590000.00 USD; cip_project_ceiling=19000000.00 USD. Named
> relationships: Runway Safe (requested_sole_source_vendor); Runway Safe (installation_oversight).
> Procedural request(s), pending - not approved/awarded/executed: advance-deposit Purchase Order,
> EMAS bed, runway pair 12R/30L. Dated/temporal facts: ... requested_pending_approval (as of
> 2024-08-28); ... historical_fact (as of 2023-12-18); ... planned_future_action (as of 2024-08-28).
> Human review required before any Signal is created or updated.

`material_claims` contains all 7 unique claims. Never says "$19,000,000 contract" or "PO awarded" —
proven by `test_material_reasons_preserved`, `test_financial_roles_preserved_distinctly`,
`test_procedural_request_preserved`.

## 14. SFO-$40M result

Constructed per task §18 as an evidence set consistent with how a fail-closed extractor (Slice 2's own
proven discipline) would actually hand claims to this evaluator: a bare `EXPLICIT_DOCUMENT_FACT`
("SFO is evaluating EMAS options") plus a weak `RELATIONSHIP` claim (`Runway Safe`,
role=`"mentioned_in_document"`) — **no** `FinancialFact` at all, because an unlabeled $40M amount
structurally cannot become one (`FinancialFact.semantic_role` has no default — verified directly via
`inspect.signature` in `test_no_financial_fact_can_be_constructed_without_a_semantic_role`). Result:
`REVIEW_REQUIRED` (an explicitly allowed outcome per task §18), reason contains neither `"$40"` nor
`"contract"` nor `"awarded"`, and every claim in `material_claims` has `financial is None`. A second
variant with only the bare event claim and no relationship at all resolves to
`INSUFFICIENT_MATERIALITY`. Neither variant ever produces the forbidden "$40M Runway Safe contract"
conclusion.

## 15. Low-materiality cases

All five from task §19 implemented and pass: airport-identity-only (empty tuple), generic EMAS mention,
static runway inventory fact, generic vendor mention (relationship with no event claim), old
non-material descriptive fact. All resolve to `INSUFFICIENT_MATERIALITY`.

## 16. High-materiality cases

All six from task §20 implemented and pass, all resolve to `REVIEW_REQUIRED`: explicit new replacement
(two same-subject event claims), explicit contract award (event + financial + relationship), major
repair after cost overrun (event + financial), planned new installation (event + active temporal),
vendor-backed procurement (procedural request + relationship), completion/status change (event +
`COMPLETED` temporal). None triggers any auto-promotion language.

## 17. International readiness

`test_non_us_non_usd_claim_set_reaches_review_required_identically`: a synthetic Haneda-style claim set
(JPY currency, a non-Runway-Safe vendor name, no FAA/MAC/MSP/English-specific vocabulary in any role or
category value) reaches `REVIEW_REQUIRED` through the identical rule path as MSP, with `"JPY"` and
`"advance_deposit"` both preserved verbatim in the reason. The module itself contains zero references to
`"MAC"`, `"MSP"`, `"FAA"`, `"Runway Safe"`, or `"USD"` (confirmed by inspection).

## 18. Purity/determinism

AST-verified (`TestPurity`, `TestNoCurrentTimeDependency`): imports are exactly `__future__`,
`dataclasses`, `enum`, `app.services.evidence_attachment_guard`, `app.services.evidence_claim_semantics`
— no `sqlalchemy`, `app.database`, `app.models`, `httpx`, `requests`, no `Signal` import anywhere, and
both transitive dependencies (`evidence_attachment_guard.py`, its own `runway_identity.py` dependency)
were independently re-confirmed pure before reuse. Zero `.today()`/`.now()`/`.utcnow()` calls.
`test_same_input_produces_identical_decision` proves determinism directly.

## 19. Focused tests

```
python -m pytest tests/test_signal_candidate_evaluation.py tests/test_evidence_claim_semantics.py \
    tests/test_mac_granicus_claims.py tests/test_discovery_candidate_fragment.py \
    tests/test_mac_granicus_extractor.py tests/test_evidence_attachment_guard.py -q
```

Result: **187 passed** (updated during the checkpoint review — one regression test was added for the
§25 correction; originally 186).

## 20. Full pytest

```
python -m pytest -q
```

Result: **921 passed** (baseline 886 + 35 tests in `tests/test_signal_candidate_evaluation.py`), 0
failed, 0 skipped. (Originally reported as 920/34 before the checkpoint review added one regression
test.)

## 21. py_compile

`python -m py_compile app/services/signal_candidate_evaluation.py tests/test_signal_candidate_evaluation.py`
— no output, exit 0.

## 22. git diff --check

No output, exit 0.

## 23. Exact files changed

- `app/services/signal_candidate_evaluation.py` (new)
- `tests/test_signal_candidate_evaluation.py` (new)
- `docs/architecture/signal-candidate-core-slice3-report.md` (new, this file)

`app/services/evidence_claim_semantics.py`, `app/acquisition/mac_granicus_claims.py`,
`app/acquisition/mac_granicus_extractor.py`, and `app/models/signal.py` were all read for grounding and
**not modified** (`git diff --stat`/`git status --porcelain` against each confirmed empty). No `Signal`
row created, modified, or queried anywhere in this slice.

## 24. git status

```
?? app/services/signal_candidate_evaluation.py
?? tests/test_signal_candidate_evaluation.py
?? docs/architecture/signal-candidate-core-slice3-report.md
```

plus the same pre-existing, unrelated untracked items already present at task start
(`docs/architecture/evidence-to-signal-semantics-design.md`,
`docs/product/sfo-2026-emas-temporal-evidence-pilot.md`, `docs/research/`, `docs/ui/...`). Nothing
staged, nothing committed, nothing pushed.

## 25. Design corrections discovered

No defect was found in `evidence_claim_semantics.py`, `mac_granicus_claims.py`,
`mac_granicus_extractor.py`, or `app/models/signal.py` during this slice; none was modified. One
deliberate policy tightening relative to the design doc (§4 above — `ATTACH_PROVISIONAL` no longer
sufficient) was applied directly per this slice's own explicit instruction, not discovered as a defect.

**Checkpoint review corrections** (both in `signal_candidate_evaluation.py` itself, found and fixed
during the commit/push review):

1. **Dead code**: `_is_concrete()` was defined (a per-claim "carries a concrete attachment" test) but
   never called anywhere — a leftover from an earlier design iteration before materiality logic was
   rewritten as whole-set bucket checks directly inline in `_is_materially_interesting`. Removed.
   `TemporalQualifier` remains used elsewhere in the module, so no import was affected.
2. **Imprecise `material_claims` on `CONTRADICTED`** (§11 above): originally included every
   relationship claim in the input set rather than only the ones actually in conflict. Fixed
   `_detect_contradiction` to return the specific conflicting claims; added
   `test_material_claims_excludes_unrelated_relationship_claims`. Neither correction changes any
   outcome or reason text for a case without a genuine contradiction, including the real MSP set.

## 26. Whether ready for Slice 4

Yes. `SignalCandidateDecision` is a complete, stable, in-memory result shape with no ORM dependency —
exactly what the design doc's own §17/§18 additive-persistence recommendation (two nullable
`SourceAssertion` columns, `intelligence_review_decision`/`intelligence_review_reason`) needs as its
input.

## 27. Recommended Slice 4 scope

Per the design doc's own roadmap (§20, slice 4): **additive persistence only** — the two nullable
`SourceAssertion` columns (`intelligence_review_decision VARCHAR(30)`, `intelligence_review_reason
TEXT`), following the exact migration pattern already proven twice
(`scripts/migrate_discovery_governed_evidence_slice1.py`), populated by calling
`evaluate_signal_candidate()` and writing its `outcome`/`reason` verbatim — no schema field for
`material_claims` or `suggested_signal_category` (both remain re-derivable, in-memory only, per the
design doc's own "claims are pure and re-derivable, only the decision persists" principle). Explicitly
not in Slice 4's scope: any review-queue UI, any `Signal` write, any real-DB migration (a dry-run/design
step first, matching the discipline already used for the identity-guard columns and the discovery
evidence columns before them).

---

`RWI_EVIDENCE_TO_SIGNAL_CANDIDATE_CORE_SLICE3_COMPLETE`
