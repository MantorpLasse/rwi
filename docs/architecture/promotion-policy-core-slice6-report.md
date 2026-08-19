# Promotion policy core — Slice 6 report

Implements Slice 6 of the roadmap in
[signal-promotion-policy-slice5-design.md](signal-promotion-policy-slice5-design.md),
building on the unmodified Slice 3 core
([signal-candidate-core-slice3-report.md](signal-candidate-core-slice3-report.md)) and
Slice 1 claim core
([evidence-claim-semantics-core-report.md](evidence-claim-semantics-core-report.md)).

## 1. Starting HEAD

`95de0a6f8e43a9a7136251ba014d9af77593b859` ("Document signal promotion policy"), branch
`main`, matched `origin/main`. Baseline full pytest: 944 passed, verified before
implementation began.

## 2. Core API

```python
def evaluate_promotion_policy(
    signal_candidate: SignalCandidateDecision,
    claims: tuple[Claim, ...],
    context: PromotionPolicyContext,
) -> PromotionPolicyDecision
```

in `app/services/promotion_policy_evaluation.py`. Pure, deterministic, no I/O.
Assumes `signal_candidate` was already computed by `evaluate_signal_candidate(claims, ...)`
against the *same* claims tuple — documented explicitly as a precondition in
the module docstring, matching the same convention Slice 4's own
`persist_intelligence_review()` already established for its own identity-gate
precondition.

## 3. Policy outcomes

`PromotionPolicyOutcome`: `AUTO_ELIGIBLE`, `HUMAN_REVIEW_REQUIRED`,
`DO_NOT_PROMOTE` — the exact three from the design doc's §5, no `AUTO_PROMOTE`
member, no automatic-write member of any kind.

## 4. Candidate-outcome mapping

`SignalCandidateOutcome != REVIEW_REQUIRED` is checked first, before any
claim is inspected: `INSUFFICIENT_MATERIALITY`, `IDENTITY_NOT_CONFIRMED`,
`CONTRADICTED`, `DUPLICATE_WITHIN_EVIDENCE`, and `STALE_OR_SUPERSEDED` all map
directly to `DO_NOT_PROMOTE` — proven by `TestSignalCandidateOutcomeMapping`
(one test per value, plus a sixth confirming `REVIEW_REQUIRED` is the only
value that proceeds past the gate). Only `REVIEW_REQUIRED` reaches the
eligibility rules in §7.

## 5. PromotionPolicyContext

```python
@dataclass(frozen=True)
class PromotionPolicyContext:
    source_authority_tier: SourceAuthorityTier | None = None
    corroborating_source_count: int = 1
    requires_corroboration: bool = False
    superseded: bool = False
```

No ORM object, no raw source text, no provider-specific field anywhere.
`requires_corroboration` is a new field beyond the design doc's own three-field
sketch, added to make the corroboration rule (§14) genuinely testable and
explicit rather than implicit — the caller states whether *this* claim shape
is known to need more than one source; this module never infers that itself
(§14).

## 6. Source-authority tiers

`SourceAuthorityTier` (`TIER_1_PRIMARY_OFFICIAL`, `TIER_2_OFFICIAL_GOVERNMENT`,
`TIER_3_CREDIBLE_SECONDARY`, `TIER_4_UNVERIFIED`) is an **in-memory policy type
only** — never written to `Source.reliability_level` or any other column, and
this module never imports `app.models` or touches the database in any way
(confirmed by AST purity check, §20). Only `TIER_1_PRIMARY_OFFICIAL` satisfies
the source-authority rule; `None` (unknown), `TIER_2`, `TIER_3`, and `TIER_4`
all fail closed to blocking `AUTO_ELIGIBLE` — proven directly by
`TestSourceAuthorityTier` (unknown, Tier 2, and Tier 3 all tested explicitly
against an otherwise-maximally-clean award claim).

## 7. AUTO_ELIGIBLE rules

All conjunctive, no scoring:

1. `SignalCandidateOutcome == REVIEW_REQUIRED` (§4).
2. Not `context.superseded` (checked unconditionally, before any claim rule —
   §16).
3. At least one claim's own `category` is `EXPLICIT_DOCUMENT_FACT`/
   `PROCEDURAL_REQUEST` **and** that same claim's `temporal.qualifier` is
   `HISTORICAL_FACT`/`COMPLETED` — an event that has actually happened, never
   merely planned or requested.
4. Every `FinancialFact` present in the (deduplicated) claim set carries a
   `semantic_role` on the allowlist (§10) — vacuously satisfied if no
   financial claim exists at all.
5. Every `RelationshipFact` present carries a `role` on the allowlist (§13) —
   vacuously satisfied if no relationship claim exists at all.
6. `context.source_authority_tier == TIER_1_PRIMARY_OFFICIAL` exactly (§6).
7. Corroboration satisfied — `not context.requires_corroboration` or
   `context.corroborating_source_count >= 2` (§14).

Rules 4 and 5 are deliberately evaluated across the **whole** deduplicated
claim set, not only the single claim that satisfies rule 3 — a conservative
reading chosen over the design doc's own more literal "if *that* claim
carries..." phrasing (§27 below documents this as a deliberate,
safety-preserving implementation refinement, not a defect). Any claim
anywhere in the set with an unsafe or unrecognized financial/relationship
role blocks `AUTO_ELIGIBLE`, regardless of which claim satisfies rule 3.

## 8. HUMAN_REVIEW_REQUIRED rules

Anything that reaches `SignalCandidateOutcome.REVIEW_REQUIRED`, is not
superseded, but fails one or more of rules 3–7 above. `blocking_reasons`
names exactly which rule(s) failed (e.g. `no_happened_event`,
`unsafe_financial_role:...`, `unsafe_relationship_role:...`,
`source_authority_tier:...`, `insufficient_corroboration`) — a reviewer, or
the future review-queue read path, can see precisely why a case did not
qualify without re-deriving it.

## 9. DO_NOT_PROMOTE rules

Exactly the five `SignalCandidateOutcome` values from §4, plus
`context.superseded == True` regardless of how strong the claims otherwise
are (§16). Confirmed policy-only: this module never touches
`SourceAssertion`/`Source`/any persisted row — the evidence itself is
untouched by a `DO_NOT_PROMOTE` decision (§19).

## 10. Financial allowlist

| `semantic_role` | Auto-safe? |
|---|---|
| `contract_award_amount` | **Yes** |
| `purchase_order_amount` | No *(design doc's own conditional role, deliberately left out this generation — §27)* |
| `grant_award` | No *(same reasoning)* |
| `authorized_ceiling` / `cip_project_ceiling` | No |
| `advance_deposit` / `advance_deposit_purchase_order` | No |
| `estimated_project_cost` | No |
| any other/unrecognized role | No (allowlist, not a blocklist — fails closed) |

Never mapped to `Signal.estimated_total_value_usd`/`estimated_emas_value_usd`
or any other Signal field — this module has no `Signal` import at all
(§20). Proven by `TestFinancialAllowlist` (CIP ceiling, advance deposit,
estimated project cost all block; `contract_award_amount` qualifies).

## 11. Procedural rules

| State (as category/temporal express it) | Policy |
|---|---|
| requested / recommended (`PROCEDURAL_REQUEST` + `REQUESTED_PENDING_APPROVAL`) | Never `AUTO_ELIGIBLE` — `no_happened_event`. |
| approved / authorized | Conditional — only if the claim's own `temporal.qualifier` is `HISTORICAL_FACT`/`COMPLETED`, never `REQUESTED_PENDING_APPROVAL`. |
| awarded / executed / completed | Potentially `AUTO_ELIGIBLE` if rules 4–7 also pass. |

Never inferred from time passage — proven by
`test_no_current_time_reinterprets_planned_as_completed`, run under the real
2026-08-19 session date against a 2024-dated planned claim.

## 12. Temporal rules

Only `HISTORICAL_FACT`/`COMPLETED` satisfy rule 3. `PLANNED_FUTURE_ACTION` and
`REQUESTED_PENDING_APPROVAL` never do (`test_planned_future_state_blocks_auto_eligibility`,
`test_requested_procedural_state_blocks_auto_eligibility`).
`CURRENT_STATE_AS_OF_DOCUMENT_DATE` and `UNKNOWN` likewise never satisfy rule
3 (neither is in `_HAPPENED_QUALIFIERS`). Zero `date.today()`/`.now()`/
`.utcnow()` calls anywhere in the module — AST-verified
(`TestNoCurrentTimeDependency`).

## 13. Relationship rules

| `RelationshipFact.role` | Auto-safe? |
|---|---|
| `awarded_contractor` | **Yes** |
| `confirmed_contract_vendor` | **Yes** |
| `requested_sole_source_vendor` | No |
| `installation_oversight` | No |
| `mentioned_in_document` | No |
| any other/unrecognized role | No |

Proven directly: `test_sole_source_relationship_alone_does_not_equal_award`
and `test_oversight_relationship_alone_does_not_equal_award` both pair an
otherwise-genuinely-happened event with a non-award relationship and confirm
`HUMAN_REVIEW_REQUIRED`, never `AUTO_ELIGIBLE` — a real relationship existing
is not, by itself, proof of an award.

## 14. Corroboration policy

`context.requires_corroboration` (caller-supplied, never inferred) gates
whether `corroborating_source_count >= 2` is required. Default
(`requires_corroboration=False`, `corroborating_source_count=1`) means a
single sufficiently-authoritative source is treated as adequate for an
explicit single-document award/completion claim, per the design doc's own
§13 conclusion — proven by
`test_explicit_tier1_event_needs_no_corroboration_by_default`. When the
caller sets `requires_corroboration=True` and only one source is established,
the result is `HUMAN_REVIEW_REQUIRED` with `insufficient_corroboration` in
`blocking_reasons` (`test_missing_required_corroboration_forces_human_review`);
raising the count to 2 flips the same case to `AUTO_ELIGIBLE`
(`test_corroborated_when_required_qualifies`). No "N sources = true" logic —
independence is never inferred, only ever asserted by the caller.

## 15. MSP #222 result

Real chain: PDF fixture → `extract_candidate_fragment()` → `extract_mac_claims()`
(7 claims) → `evaluate_signal_candidate()` → `REVIEW_REQUIRED` →
`evaluate_promotion_policy()` **with `TIER_1_PRIMARY_OFFICIAL` context** (the
most favorable authority context possible, to isolate that the claim
*content* — not missing authority — is what blocks it):

```
Promotion outcome: HUMAN_REVIEW_REQUIRED
Reason: HUMAN_REVIEW_REQUIRED: material evidence is supported (SignalCandidate
    REVIEW_REQUIRED), but automatic eligibility is blocked by: financial semantic
    role(s) not on the auto-safe allowlist: advance_deposit_purchase_order,
    cip_project_ceiling; relationship role(s) not an explicit award/vendor-
    confirmation role: installation_oversight, requested_sole_source_vendor.
    This evidence is not discarded - human review determines whether it should
    become a Signal.
blocking_reasons: ('unsafe_financial_role:advance_deposit_purchase_order,cip_project_ceiling',
    'unsafe_relationship_role:installation_oversight,requested_sole_source_vendor')
```

Notably, claim F's own `temporal.qualifier` **is** `HISTORICAL_FACT` (the CIP
listing genuinely was approved, past tense), so rule 3 (an actually-happened
event) is satisfied — exactly matching the design doc's own §14 observation.
What blocks MSP is rules 4 and 5, independently: `cip_project_ceiling`/
`advance_deposit_purchase_order` are not on the financial allowlist, and
`requested_sole_source_vendor`/`installation_oversight` are not on the
relationship allowlist. **`HUMAN_REVIEW_REQUIRED`, never `DO_NOT_PROMOTE`** —
proven directly (`test_msp_is_not_do_not_promote`) — MSP remains material
intelligence, not discarded evidence.

## 16. Explicit-award result

Synthetic claim: `EXPLICIT_DOCUMENT_FACT`, `HISTORICAL_FACT`,
`financial=FinancialFact(12_500_000, "USD", "contract_award_amount")`,
`relationship=RelationshipFact("Vendor X", "awarded_contractor")`, evaluated
with `TIER_1_PRIMARY_OFFICIAL`: **`AUTO_ELIGIBLE`**. All three eligibility
reasons recorded (happened event, safe financial role, safe relationship
role, Tier 1 authority, corroboration satisfied). Confirmed this is an
*eligibility classification only* — the reason string explicitly states "does
not create, update, or publish any Signal"
(`test_auto_eligible_reason_explicitly_disclaims_writing_a_signal`), and no
`Signal` import exists anywhere in the module (§20).

## 17. Completion result

Synthetic claim: `EXPLICIT_DOCUMENT_FACT`, `COMPLETED`, **no financial or
relationship fact at all**, evaluated with `TIER_1_PRIMARY_OFFICIAL`:
**`AUTO_ELIGIBLE`**. Rules 4/5 are vacuously satisfied (nothing present to
misclassify) — the narrowest, safest possible eligible shape, exactly as the
design doc's own §16 anticipated.

## 18. SFO-$40M result

Claim set: a bare `EXPLICIT_DOCUMENT_FACT` ("SFO is evaluating EMAS options,"
no temporal attached) plus a `RELATIONSHIP` claim naming Runway Safe with role
`"mentioned_in_document"` — **no `FinancialFact` at all**, since an unlabeled
$40M amount structurally cannot become one (Slice 1's own hard invariant,
`FinancialFact.semantic_role` has no default). Evaluated with
`TIER_1_PRIMARY_OFFICIAL`: **`HUMAN_REVIEW_REQUIRED`**, never `AUTO_ELIGIBLE`
— blocked independently by `no_happened_event` (the fact claim carries no
temporal qualifier at all) and `unsafe_relationship_role` (`mentioned_in_document`
is not allowlisted). A second variant constructs `SignalCandidateDecision`
directly with `INSUFFICIENT_MATERIALITY` and confirms the outcome-mapping
gate alone (§4) already routes it to `DO_NOT_PROMOTE` before any claim rule
even runs. **Neither branch ever produces `AUTO_ELIGIBLE`**, and the reason
string never contains `"$40"` or `"contract"` in either case — proven
directly.

## 19. International readiness

A synthetic Haneda-style claim (JPY currency, a non-US vendor name,
`awarded_contractor` relationship, `HISTORICAL_FACT`) reaches `AUTO_ELIGIBLE`
through the identical rule path as the domestic golden case — no currency
comparison, no English-text dependency, no source-family-specific logic
anywhere in the module (confirmed: zero occurrences of `"MAC"`, `"MSP"`,
`"FAA"`, `"Runway Safe"`, `"USD"` in `promotion_policy_evaluation.py` itself).

## 20. Purity/determinism

AST-verified: imports are exactly `__future__`, `dataclasses`, `enum`,
`app.services.evidence_claim_semantics`, `app.services.signal_candidate_evaluation`
— no `sqlalchemy`, `app.database`, `app.models`, `httpx`, `requests`, and no
`Signal` name imported anywhere (`TestPurity`, using AST import-node
inspection rather than substring search — a naive `"import Signal"` search
was tried first and correctly caught as a false positive against
`SignalCandidateDecision`/`SignalCandidateOutcome`, then fixed to inspect
actual import names). Zero `.today()`/`.now()`/`.utcnow()` calls
(`TestNoCurrentTimeDependency`). `test_same_input_produces_identical_decision`
confirms determinism directly.

## 21. Focused tests

```
python -m pytest tests/test_promotion_policy_evaluation.py tests/test_signal_candidate_evaluation.py \
    tests/test_evidence_claim_semantics.py tests/test_mac_granicus_claims.py \
    tests/test_evidence_attachment_guard.py tests/test_discovery_candidate_fragment.py -q
```

Result: **206 passed**, 0 failed. (Original implementation-time run used only
the first four files, 138 passed; the checkpoint review widened this to
include the guard and candidate-fragment suites given
`promotion_policy_evaluation.py` sits downstream of the whole chain, and
added one regression test — §27a.)

## 22. Full pytest

```
python -m pytest -q
```

Result: **982 passed** (baseline 944 + 38 tests in
`tests/test_promotion_policy_evaluation.py`), 0 failed, 0 skipped.
(Originally reported as 981/37 before the checkpoint review added one
regression test.)

## 23. py_compile

`python -m py_compile app/services/promotion_policy_evaluation.py tests/test_promotion_policy_evaluation.py`
— no output, exit 0.

## 24. git diff --check

No output, exit 0.

## 25. Exact files changed

- `app/services/promotion_policy_evaluation.py` (new)
- `tests/test_promotion_policy_evaluation.py` (new)
- `docs/architecture/promotion-policy-core-slice6-report.md` (new, this file)

`app/services/evidence_claim_semantics.py` and
`app/services/signal_candidate_evaluation.py` were both re-read in full for
grounding and **not modified** — `git status --porcelain` against both
confirmed empty; no genuine defect was found in either during this task.

## 26. git status

```
?? app/services/promotion_policy_evaluation.py
?? tests/test_promotion_policy_evaluation.py
?? docs/architecture/promotion-policy-core-slice6-report.md
```

plus the same pre-existing, unrelated untracked items already present at
task start. Nothing staged, nothing committed, nothing pushed.

## 27. Design corrections discovered

No defect was found in any previously-committed file. One deliberate
implementation refinement of the Slice 5 design's own language, documented
transparently:

**Rules 4/5 evaluated across the whole claim set, not only the anchor
claim.** The design doc's §6 phrasing ("if *that* claim carries a
FinancialFact...") reads as attaching rules 4/5 to the single claim
satisfying rule 3. In practice, this repository's own real extractor (Slice
2's `mac_granicus_claims.py`) virtually always splits financial and
relationship facts across *separate* claims from the one carrying the
"happened" temporal qualifier (exactly as MSP's own real claims show: claim F
carries both the historical qualifier and the CIP-ceiling financial fact
together, but claim I's relationship is on an entirely separate claim from
any temporal-qualified one). A strictly per-claim reading would therefore
either (a) never find a realistic claim set satisfying rules 3+4+5
simultaneously on one object, making `AUTO_ELIGIBLE` nearly unreachable even
for genuinely clean cases, or (b) require an artificial single-claim
construction unlike anything a real extractor produces. Implemented instead
as: rule 3 requires *some* claim with a happened-event qualifier; rules 4/5
require *every* financial/relationship claim *anywhere* in the deduplicated
set to be allowlisted. This is strictly *more* conservative than the literal
per-claim reading (any unsafe claim anywhere blocks eligibility, not just an
unsafe claim on the anchor), never less safe, and is exercised directly by
`test_sole_source_relationship_alone_does_not_equal_award` (an unsafe
relationship on a *different* claim from the happened-event anchor still
blocks). Documented here rather than silently substituted, per this task's
own "correct only genuine defects" discipline extended to a design-language
ambiguity resolved in the safer direction.

## 27a. Checkpoint review correction

No defect was found in `promotion_policy_evaluation.py` itself during the
commit/push checkpoint review — the whole-claim-set refinement (§7/§27 above)
was independently re-traced by hand against the checkpoint's own explicit
example ("a safe completion claim plus an unsafe financial claim in the same
candidate must not silently become AUTO_ELIGIBLE") and confirmed correct.
That exact scenario, however, had no *dedicated* test — the existing
`test_cip_ceiling_blocks_auto_eligibility` attaches the unsafe financial fact
to the *same* claim that carries the happened-event qualifier, which does not
isolate the whole-set behavior from ordinary per-claim behavior. Added
`test_safe_completion_claim_still_blocked_by_unrelated_unsafe_financial_claim`
(a genuinely completed, otherwise-clean claim paired with a second, unrelated
claim carrying `cip_project_ceiling`) to close this coverage gap and prove
the refinement directly rather than by inference from other tests. No code
change was needed — only the test suite grew. The whole-claim-set behavior is
also explicitly characterized as conservative *only in the safe direction*:
it can cause a technically-eligible event to require human review because of
unrelated ambiguous content in the same claim set, but it can never let an
unsafe claim slip through by being scoped too narrowly — the correct
trade-off for `AUTO_ELIGIBLE`'s own "must be narrow" principle.

**`purchase_order_amount` and `grant_award` deliberately excluded from the
financial allowlist** (§10) — the design doc itself named both as merely
*conditionally* safe, pending a nuance ("issued not requested," "already
obligated not merely applied for") this module cannot verify from a
`FinancialFact` alone. Left out entirely rather than approximated, narrower
than the design's own ceiling — consistent with Slice 5's own "AUTO_ELIGIBLE
must be narrow" principle.

## 28. Whether ready for Slice 7

Yes. `PromotionPolicyDecision` is a complete, stable, in-memory result shape
with no ORM dependency — exactly what an additive persistence slice
(mirroring Slice 4's own pattern) would need as its input.

## 29. Recommended Slice 7 scope

Per the design doc's own roadmap (§25): **additive persistence only** — two
nullable `SourceAssertion` columns (`promotion_policy_decision VARCHAR(30)`,
`promotion_policy_reason TEXT`), following the exact migration pattern
already proven twice (Slice 1, Slice 4), populated by calling
`evaluate_promotion_policy()` and writing its `outcome`/`reason` verbatim —
genuinely needed once a real review queue (Slice 8) exists, so a query
doesn't have to recompute the policy for every row on every page load, the
same justification Slice 4 itself already used. Explicitly not in Slice 7's
scope: any review-queue UI, any `Signal` write, any real-DB migration (a
design/dry-run step first, matching every prior slice's own discipline), and
not yet the source-authority-tier infrastructure gap (design doc's own
"Slice 8.5"-shaped prerequisite, still unaddressed and out of scope here).

---

`RWI_PROMOTION_POLICY_CORE_SLICE6_COMPLETE`
