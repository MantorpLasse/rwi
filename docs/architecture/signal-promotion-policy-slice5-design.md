# Signal promotion policy — Slice 5 design

**Architecture/policy investigation only. No implementation, no schema change, no
migration, no database write, no Signal created or modified, no review-queue
implementation, no UI, no deployment, no commit, no push.** Baseline: branch
`main`, HEAD `d6af3e914c89829e24946b2ceed5065a15b461b4`, full suite 944 passed.
One read-only real-DB check performed (§14) — `data/runway_safe.db` was opened
`mode=ro` only, never written.

## 1. Purpose

Slices 1–4 built a fully governed, deterministic pipeline that ends at a
*persisted judgment*: `SourceAssertion.intelligence_review_decision` /
`.intelligence_review_reason`, computed by `evaluate_signal_candidate()`
(Slice 3) and written by `persist_intelligence_review()` (Slice 4). Nothing
downstream of that judgment exists yet — no `Signal` is ever created from it,
automatically or otherwise. This document designs the **policy layer** that
decides, for evidence that has already cleared intelligence review, what
happens next: which cases could someday be safely automated, which need a
human's judgment, and which should never become a `Signal` at all from the
evidence as it currently stands. It answers a governance question, not an
engineering one — no code is written here.

## 2. Current implemented pipeline

```
authoritative source (e.g. MAC Granicus board record)
    -> Snapshot (app/models/acquisition.py, immutable)
    -> CandidateFragment (app.services.discovery_candidate_fragment, pure)
    -> Evidence Attachment Guard (app.services.evidence_attachment_guard, pure,
       deterministic) -> AttachmentOutcome
    -> governed SourceAssertion (app.services.discovery_evidence_persistence,
       persists identity_guard_decision/identity_guard_reason)
    -> Claims (app.acquisition.mac_granicus_claims.extract_mac_claims - source-
       specific adapter -> tuple[Claim, ...], app.services.evidence_claim_semantics
       core, pure)
    -> SignalCandidateDecision (app.services.signal_candidate_evaluation.
       evaluate_signal_candidate, pure, source-agnostic)
    -> intelligence_review_decision / intelligence_review_reason
       (app.services.intelligence_review_persistence.persist_intelligence_review,
       additive columns on the SAME SourceAssertion row)
    -> ??? <- THIS DESIGN TASK
```

Every stage through `intelligence_review_reason` is committed, pushed, and
tested (944 passing tests). **Nothing writes to `Signal` anywhere in this
chain.** The real production database (`data/runway_safe.db`) has never had
either additive migration (Slice 1's `identity_guard_*` columns are applied;
confirmed by read-only inspection in this task — Slice 4's
`intelligence_review_*` columns are **not yet** applied to the real DB, only
to test fixtures) — `SourceAssertion` #222 (the real MSP row) still shows only
`identity_guard_decision = "ATTACH_CONFIRMED"` on the real database; its
`intelligence_review_decision` would be `"REVIEW_REQUIRED"` if the Slice 4
migration and a review pass were ever run against it, per §14 below.

## 3. Current SignalCandidate semantics

Investigating whether `SignalCandidateOutcome.REVIEW_REQUIRED` is doing two
jobs — **yes, but only within itself, and only ambiguously for one of the
two**. Reading `evaluate_signal_candidate()` (unmodified, Slice 3) closely:

- Five of its six outcomes (`IDENTITY_NOT_CONFIRMED`, `INSUFFICIENT_MATERIALITY`,
  `CONTRADICTED`, `DUPLICATE_WITHIN_EVIDENCE`, `STALE_OR_SUPERSEDED`) are
  **entirely about the evidence's own quality** — they answer "is this
  evidence usable at all," and every one of them already means, in effect,
  "this cannot become a Signal from what we currently have." None of them
  needs a *further* judgment layered on top.
- The sixth, `REVIEW_REQUIRED`, currently answers exactly one question —
  "does this evidence clear the materiality bar" (design doc S9: at least one
  category-qualifying claim combined with corroborating structure) — and its
  *name* answers a second, different question by fiat: "...and therefore a
  human must look at it." Today those two answers are forced to be the same
  answer, because no finer distinction exists. `REVIEW_REQUIRED` is real
  evidence of the exact conflation this task asks about: it is impossible,
  from `SignalCandidateOutcome` alone, to tell "the real MSP case — pending
  requests and a budget ceiling, genuinely ambiguous" apart from "a hypothetical
  case where an official record explicitly says a contract was awarded, an
  amount, and a vendor, with nothing left to interpret." Both currently
  produce identically-shaped `REVIEW_REQUIRED` decisions.

## 4. Why the policy layer is separate

**Recommendation: a new, separate, pure function — do not redesign
`SignalCandidateOutcome` or `evaluate_signal_candidate()`.** Two independent
reasons converge on the same answer:

1. **The five non-`REVIEW_REQUIRED` outcomes need nothing new.** They map
   directly and trivially onto this design's own `DO_NOT_PROMOTE` lane (§5) —
   there is no finer distinction to make for evidence that is contradicted,
   stale, unconfirmed, duplicate, or immaterial. Only the `REVIEW_REQUIRED`
   population needs a *new* split. Redesigning the whole six-member
   vocabulary to serve a question five-sixths of it doesn't have would be
   exactly the kind of scope creep this project's own prior checkpoints have
   repeatedly refused (Slice 2's and Slice 3's own reports both explicitly
   declined to touch the Slice 1 core absent a genuine defect — the same
   discipline applies here, one level up).
2. **The new question needs inputs `SignalCandidateDecision` doesn't carry.**
   Auto-eligibility (§6) depends on source authority/tier (§15) and, for some
   claim shapes, multi-source corroboration (§16) — neither concept exists
   anywhere in `Claim`, `ClaimProvenance`, or `SignalCandidateContext` today.
   Adding them to the Slice 1/3 cores would inflate two already-tested, already
   -proven, deliberately minimal modules to answer a question that is
   genuinely *downstream* of what they were built to answer. A new layer can
   take `SignalCandidateDecision` (reusing its materiality judgment and reason
   verbatim, never recomputing it), the same `claims` tuple (for the finer
   per-claim role/qualifier inspection promotion policy needs), and a small,
   new, explicit context object carrying exactly the additional facts §6
   requires — nothing torn open, nothing re-litigated.

The resulting shape, matching this task's own suggestion almost exactly:

```
SignalCandidateDecision
        +
    Claims
        +
    provenance/evidence context (NEW, small, explicit)
        v
PromotionPolicyDecision
```

## 5. PromotionPolicy outcomes

Names kept as suggested — `AUTO_ELIGIBLE` / `HUMAN_REVIEW_REQUIRED` /
`DO_NOT_PROMOTE` — they read unambiguously against this repository's existing
`ALL_CAPS_WITH_UNDERSCORES` outcome convention (`AttachmentOutcome`,
`SignalCandidateOutcome`) and no existing naming pattern argues for anything
else.

| `SignalCandidateOutcome` | `PromotionPolicyOutcome` | Why |
|---|---|---|
| `IDENTITY_NOT_CONFIRMED` | `DO_NOT_PROMOTE` | No confirmed subject to promote anything about. |
| `INSUFFICIENT_MATERIALITY` | `DO_NOT_PROMOTE` | Not investor intelligence by itself. |
| `CONTRADICTED` | `DO_NOT_PROMOTE` | Cannot be safely interpreted. |
| `DUPLICATE_WITHIN_EVIDENCE` | `DO_NOT_PROMOTE` | Nothing new to evaluate. |
| `STALE_OR_SUPERSEDED` | `DO_NOT_PROMOTE` | A newer document already governs the real-world status. |
| `REVIEW_REQUIRED` | `AUTO_ELIGIBLE` **or** `HUMAN_REVIEW_REQUIRED` | The new split — §6/§7. |

**`AUTO_ELIGIBLE` is an eligibility classification, not a write instruction** —
restating the task's own framing precisely, because it is the single most
important invariant in this whole design (see invariant #10, §24): it means
*"this evidence/claim set satisfies deterministic structural conditions that
could eventually permit automation, once a separately-authorized policy
decides to turn automation on."* Nothing in this design, and nothing any
slice recommended here would build, ever writes a `Signal` from an
`AUTO_ELIGIBLE` decision by itself.

`DO_NOT_PROMOTE` is likewise **not permanent-forever** for a given real-world
event — it is a judgment about *this evidence, as currently understood*.
New evidence (a later document) can produce a new, independent claim set that
reaches a different outcome; nothing here ever "unlocks" an old
`DO_NOT_PROMOTE` decision by aging or being reconsidered — matching this
whole project's own `HISTORY NEVER DELETED` principle (already used for
`Snapshot` immutability and `InstallationAssertionLink.supersedes_link_id`).

## 6. AUTO_ELIGIBLE rules

All conjunctive (every rule must hold) — no scoring, no confidence weighting,
matching the task's own explicit instruction not to use "high confidence" as
a criterion:

1. `SourceAssertion.identity_guard_decision == "ATTACH_CONFIRMED"` exactly —
   reuses Slice 4's own already-stricter-than-the-original-design-doc gate;
   `ATTACH_PROVISIONAL` is never sufficient here either.
2. `SignalCandidateOutcome == REVIEW_REQUIRED` (materiality already
   established by the unmodified Slice 3 core; identity, contradiction,
   staleness, and duplication have already been ruled out).
3. **At least one claim asserts an event that has actually happened**, per
   the document's own words — `category` in `{EXPLICIT_DOCUMENT_FACT,
   PROCEDURAL_REQUEST}` is not enough by itself; the claim's own `temporal`
   must carry `qualifier` in `{HISTORICAL_FACT, COMPLETED}` — never
   `PLANNED_FUTURE_ACTION` or `REQUESTED_PENDING_APPROVAL`, which by
   definition describe something that has *not yet* happened.
4. **If that claim carries a `FinancialFact`**, its `semantic_role` must be
   in an explicit, curated allowlist (§9) — never a role known to represent a
   ceiling, estimate, or deposit.
5. **If that claim carries a `RelationshipFact`**, its `role` must explicitly
   assert a confirmed/awarded relationship (§10) — never a requested,
   recommended, or merely-mentioned one.
6. **Zero blocking warnings specific to this claim set** — a new,
   case-specific field on `PromotionPolicyDecision` (`blocking_reasons`,
   §22), distinct from `SignalCandidateDecision.warnings` (which today always
   carries the generic, structural "`suggested_signal_category` not
   populated" note — that is a scope statement about the evaluator, not a
   red flag about this particular evidence, and must not by itself block
   eligibility).
7. **Source authority is explicit and Tier 1** (§15) — **currently a real,
   unresolved infrastructure gap**, flagged as a blocker below, not solved
   here.
8. **No unresolved multi-source corroboration requirement outstanding**
   (§16) for claim shapes that need it.

## 7. HUMAN_REVIEW_REQUIRED rules

Everything that reaches `SignalCandidateOutcome.REVIEW_REQUIRED` but fails
**any** §6 rule. In practice, today, this is the common case — every real
claim set this repository has ever produced (the real MSP #222 chain) lands
here, because rule 3 (an actually-happened event) and rule 7 (source
authority) are the two hardest bars to clear, and MSP's own evidence is
explicitly still a staff *request*, not a recorded decision. Concretely
`HUMAN_REVIEW_REQUIRED` covers:

- Procedural request vs. approval ambiguity (MSP's own claims C/D exactly).
- A future plan with no execution evidence yet (MSP's own claim H).
- Multiple valid, distinct financial roles present with none of them an
  auto-safe role (MSP's own claims D/F together).
- A real vendor relationship that is not itself an award (MSP's own claims
  C/I — sole-source *request* and installation *oversight*, neither an
  award).
- A materially-interesting claim combination whose event status is
  incomplete (lifecycle expiry + replacement required, with no confirmed
  procurement outcome yet).
- More than one authoritative source describing the same real-world project
  with details that don't obviously reconcile (design doc §11's own Signal 67
  / SourceAssertion #78 / #222 situation, still real and still unlinked).
- `suggested_signal_category` genuinely unresolved for a case where the
  category *matters* for how a human should read it (rare in practice today,
  since `suggested_signal_category` is always `None` in this generation —
  §14 of the Slice 3 report — but the policy layer should carry this reason
  explicitly rather than silently proceeding as if category were known).
- Evidence that might be superseded but the newer document hasn't been
  positively linked yet (a softer version of `STALE_OR_SUPERSEDED`, which
  requires an *explicit* superseded flag — an *unconfirmed suspicion* of
  staleness belongs here, not in `DO_NOT_PROMOTE`).
- Procurement semantics from an unfamiliar jurisdiction/source family where
  the reviewer doesn't yet have a proven read on what a given procedural
  phrase actually commits the authority to.

The task's own instruction stands: **this queue must contain things
requiring judgment, not every discovery.** `INSUFFICIENT_MATERIALITY`-shaped
evidence never reaches this lane at all (§5's table already routes it to
`DO_NOT_PROMOTE`) — a generic "airport has an EMAS bed" mention never becomes
a human's problem to read.

## 8. DO_NOT_PROMOTE rules

Directly inherited from §5's table for five of the six `SignalCandidateOutcome`
values, plus the same materiality/contradiction/staleness reasoning restated
in evidence-specific terms per the task's own list: identity not confirmed,
insufficient materiality, contradiction, stale/superseded against a newer
authoritative source, a generic EMAS mention, a static runway inventory fact,
an unlabeled amount with nothing else attached, a vendor mention with no
relevant event, and rejected cross-airport evidence (which never even reaches
`SignalCandidateOutcome` — the identity guard already stops it at
`REJECT_CROSS_AIRPORT`, one layer earlier).

**Strong preference, matched directly**: `DO_NOT_PROMOTE` evidence is **never
deleted**. `SourceAssertion.raw_relevant_text` and every provenance field
remain exactly as governed and persisted (Slice 1/4's own already-proven
append-only discipline — nothing in this design proposes touching that).
Whether it should remain *searchable/auditable* is a UI/query-surface
question this design does not need to resolve to define the policy itself —
recommended default: yes, remain queryable (e.g. by
`intelligence_review_decision`/a future `promotion_policy_decision` column),
exactly the same posture already taken for `REVIEW_REQUIRED` rows today, but
excluded from the active human review queue (§18) by construction.

## 9. Financial rules

The task's seven example roles, evaluated individually — **not treated
equivalently**, per the task's own explicit instruction:

| `semantic_role` | Auto-safe? | Reasoning |
|---|---|---|
| `contract_award_amount` | **Yes**, paired with an explicit awarded-vendor relationship claim (§10) | Represents a confirmed, executed transaction value, stated as such by the source itself. |
| `purchase_order_amount` | **Conditional** — only when the *same or a paired* claim's procedural/temporal state is "issued/executed," never "requested" | The role name alone is ambiguous — MSP's own real evidence proves a PO amount can just as easily be a *request* (claim D, `advance_deposit_purchase_order`, `REQUESTED_PENDING_APPROVAL`). Role name is necessary but not sufficient; the claim's own procedural state must also be checked. |
| `grant_award` | **Yes**, only if the granting body's own record states the award is already obligated, not merely applied for | Mirrors the (currently unsafe) intent of `scripts/import_usaspending_grants.py`'s own automated write — this design would make that same intent finally safe by checking the underlying claim's temporal state, something that importer never does today. |
| `authorized_ceiling` / `cip_project_ceiling` | **No** | A ceiling authorizes spending *up to* an amount — it is not a specific transaction. This is the literal, real MSP claim F. |
| `advance_deposit` / `advance_deposit_purchase_order` | **No** | A deposit toward a not-yet-finalized purchase. The literal, real MSP claim D. |
| `estimated_project_cost` | **No** | An estimate, never a confirmed transaction. |
| unlabeled / `None` | **Structurally impossible already** | `FinancialFact.semantic_role` has no default (Slice 1's own hard invariant, AST-verified) — an amount with no role can never reach this decision as a `FinancialFact` at all. |

**Signal model check** (detailed in §22): `Signal` has exactly two generic
financial fields, `estimated_total_value_usd` and `estimated_emas_value_usd`
— neither carries any semantic-role tag. This is the *exact*, already-real
collapse the whole evidence-to-signal architecture exists to prevent (design
doc §2's own finding, restated: `scripts/import_usaspending_grants.py` line
290 writes `grant.award_amount` straight into `estimated_total_value_usd`
with zero role preservation — the one fully-automated Signal-writing pathway
that already exists in this repository, and the literal shape of danger a
`cip_project_ceiling` figure would be if it ever reached that same code
path). **This remains a real blocker for any future automatic *write*, even
once a claim reaches `AUTO_ELIGIBLE`** — flagged, not solved, per this task's
own explicit "no schema change" boundary.

## 10. Procedural rules

| Procedural state (as the claim's own category/temporal/relationship
express it) | Promotion policy |
|---|---|
| requested / recommended | Never `AUTO_ELIGIBLE` — by definition, an outcome is not yet known. MSP's own claims C/D exactly. |
| approved / authorized | Conditional — only when the confirming document is *itself* the deciding body's own record stating the decision was actually made (not a staff recommendation asking for it) — i.e. the claim's `temporal.qualifier` must be `HISTORICAL_FACT`/`COMPLETED`, never `REQUESTED_PENDING_APPROVAL`. |
| awarded / executed | Eligible for `AUTO_ELIGIBLE` when paired with an explicit vendor + amount + scope, per §6 rules 4/5. |
| completed | The cleanest, most conservative eligible shape (§16 golden case) — no financial-role ambiguity even possible if no dollar figure is asserted at all. |

The task's own example stands exactly as stated: a `FOR ACTION`/`REQUESTED`
claim (MSP's own real shape) should almost certainly never auto-promote an
"award" Signal — and under these rules, structurally cannot: rule 3 (§6)
requires `HISTORICAL_FACT`/`COMPLETED`, which a `REQUESTED_PENDING_APPROVAL`
claim never carries.

## 11. Temporal rules

| `TemporalQualifier` | Promotion policy |
|---|---|
| `HISTORICAL_FACT` | Eligible, subject to §6 rules 4/5 if financial/relationship facts are attached. |
| `CURRENT_STATE_AS_OF_DOCUMENT_DATE` | Never alone sufficient — describes a *state* ("the bed currently exists"), not a promotable *event*. Routes to `HUMAN_REVIEW_REQUIRED` or `DO_NOT_PROMOTE` depending on materiality. |
| `PLANNED_FUTURE_ACTION` | Never `AUTO_ELIGIBLE` — has not happened yet, by definition. MSP's own claim H exactly. |
| `REQUESTED_PENDING_APPROVAL` | Never `AUTO_ELIGIBLE` — same reasoning, procedural rather than temporal framing. MSP's own claims C/D. |
| `COMPLETED` | Eligible, same as `HISTORICAL_FACT`. |
| `UNKNOWN` | Never `AUTO_ELIGIBLE` — fails closed; an unknown temporal state cannot license automation. |

**No calendar-time inference anywhere in this design** — restating the task's
own example precisely: a 2024 statement planning a bid in 2025 is not,
and can never become, an automatically-inferred "executed in 2026" event
merely because 2026 has arrived (proven structurally already at the Slice 3
level — `evaluate_signal_candidate()` contains zero `date.today()`/`.now()`
calls, AST-verified — and this policy layer inherits that same discipline by
construction: it would need its own new `date.today()` call to violate this,
which no rule in §6–§11 requires or permits).

## 12. Source-authority rules

The task's four-tier sketch, evaluated against **what RWI's models can
represent today**:

| Tier | Description | Currently representable? |
|---|---|---|
| 1 | Airport/operator/authority/regulator's own primary record (e.g. the MAC Commission's own board minutes — the real MAC Granicus source family) | **Not deterministically.** `Source.reliability_level` (free string) is set to `"official"` for both this AND Tier-2-shaped records (see below) — no field distinguishes them. |
| 2 | Government procurement / official grant / authoritative government filing (e.g. a USAspending grant record — a record *about* the airport, not the airport's own statement) | Same field, same value (`"official"`), same non-distinction. |
| 3 | Credible news/reporting | `Source.reliability_level` is used informally as `"unverified"` in some pathways, but never systematically for tiering news vs. primary sources. |
| 4 | Aggregator/search snippet/general AI result | No distinct representation at all. |

**Finding, stated plainly**: `Source.reliability_level` exists but is used, in
every real pathway in this codebase, as a coarse, informally-populated
three-value convention (`"official"` / `"internal"` / `"unverified"`), not a
real tier hierarchy. `"official"` is applied uniformly to the FAA NASR
archive, USAspending grant records, and (were it ever set at all — it
currently is not) a MAC Granicus board memo — three genuinely different
authority levels under the task's own four-tier sketch, all currently
indistinguishable by field value alone. **This is a real, concrete blocker
for deterministic `AUTO_ELIGIBLE` gating** — flagged per the task's own
explicit instruction, not solved here (no schema change in this task).

Not a speculative gap, either: it is resolvable *without* inventing a new
subsystem, because the source-specific **acquisition provider** already knows
its own tier by construction — `app/acquisition/mac_granicus.py` only ever
scrapes the Metropolitan Airports Commission's own Granicus meeting-management
system (definitionally Tier 1 for MSP), and `scripts/import_usaspending_grants.py`
only ever reads USAspending (definitionally Tier 2) — the tier is knowable at
ingestion time, it is just not currently *labeled* on the `Source` row in a
granular enough way to gate a policy decision on it deterministically. A
future, small, additive change (a real tier value on `reliability_level`, or
a new column) would close this gap without redesigning anything — but that
is schema work, explicitly out of scope for this design task.

## 13. Multi-evidence rules

**Recommendation: depends on claim shape, not on a fixed source count.**

- An explicit, dated, Tier-1-primary-source claim of a completed/awarded
  event needs **only one** such source — the record IS the authoritative
  account of the decision it itself made (the Commission's own minutes
  recording the Commission's own vote need no second document to "confirm"
  what the Commission already, definitionally, knows it decided).
- A claim built on **inference rather than an explicit statement** — e.g.
  "Runway Safe is the FAA's only approved EMAS product, so it's *probably*
  who got this contract" — must **never** reach `AUTO_ELIGIBLE` regardless of
  how many documents restate the same inference; restating an inference is
  not corroboration. (This case is structurally already excluded anyway —
  such a claim would never carry an `awarded_contractor`-shaped
  `RelationshipFact` in the first place, since that role is reserved for
  explicit statements per §10 — but it's worth naming directly, since it is
  exactly the shape of reasoning a future, careless extractor could
  introduce.)
- Where a real event genuinely spans multiple, independently-produced
  authoritative documents (design doc §11's own still-unlinked Signal 67 /
  SourceAssertion #78 / #222 example) and no single one, alone, states the
  full award/amount/vendor combination, `AUTO_ELIGIBLE` should require
  genuine, independent corroboration across at least two Tier-1/2 sources —
  **not implemented or detectable today**: RWI has no structural way to tell
  "two claims from genuinely independent primary sources" apart from "the
  same press release re-syndicated twice" (both would currently look like
  two `Claim` objects with matching content and different
  `ClaimProvenance.artifact_identity`). Flagged as a second, real
  infrastructure gap, not solved here.

## 14. MSP #222 result

Read-only inspection of the real database confirms `SourceAssertion` id 222
still carries `identity_guard_decision = "ATTACH_CONFIRMED"`,
`airport_id = 45` (MSP), and the real `intelligence_review_*` columns do not
yet exist on the real database (Slice 4's migration has not been applied
there — only to isolated test fixtures, matching every prior slice's own
STOP boundary). The real `Signal` table still holds 68 rows, unchanged, none
citing this evidence.

Re-running the full chain against the real fixture (as Slice 4's own test
suite already does, in-memory) gives `SignalCandidateOutcome.REVIEW_REQUIRED`
for the 7 real claims (A, B, C, D, F, H, I). Applying §6's rules:

- Rule 3 (an actually-happened event): **fails**. No claim carries
  `temporal.qualifier` in `{HISTORICAL_FACT, COMPLETED}` *combined with* an
  event this project cares about promoting — claim F is `HISTORICAL_FACT`,
  but it describes only the *CIP ceiling's own approval*, whose financial
  role (`cip_project_ceiling`) is explicitly excluded by rule 4 anyway; every
  other claim is `REQUESTED_PENDING_APPROVAL` (C, D) or `PLANNED_FUTURE_ACTION`
  (H) or carries no temporal qualifier at all (A, B, I).
- Rule 4 (financial role): **fails** for both financial claims present — D is
  `advance_deposit_purchase_order`, F is `cip_project_ceiling`, neither
  allowlisted.
- Rule 5 (relationship role): **fails** for both relationship claims — C is
  `requested_sole_source_vendor` (explicitly scoped "pending Commission
  authorization, not a confirmed award"), I is `installation_oversight`
  (explicitly not an award/contractor role).

**Result: `HUMAN_REVIEW_REQUIRED`** — matching the task's own expected
result, but reached here by explicit structural rule failures (three
independent ones, not merely one), not by a general "this feels ambiguous"
judgment. This is real evidence the §6 rules do what they are meant to: the
real, single hardest test case this repository has ever produced correctly
fails every auto-eligibility gate for reasons that are individually
inspectable and traceable to the real memo's own actual wording (a staff
request, a budget ceiling, an oversight role — never an award).

## 15. Explicit-award golden case

*"Airport Commission awards Runway Safe a $12.5M contract for replacement of
the Runway 27 EMAS."* Constructed with explicit claims: `EXPLICIT_DOCUMENT_FACT`,
`temporal.qualifier = HISTORICAL_FACT` (an executed board vote, past tense),
`financial = FinancialFact(amount=12_500_000, currency="USD",
semantic_role="contract_award_amount")`, `relationship =
RelationshipFact(party="Runway Safe", role="awarded_contractor")`, subject
scoped to "Runway 27 EMAS replacement," `identity_guard_decision =
"ATTACH_CONFIRMED"`.

Checking §6 rule by rule: rule 1 ✓ (by construction), rule 2 ✓ (this claim
set clears `SignalCandidateOutcome`'s own materiality bar easily — an event
claim with both financial and relationship facts attached), rule 3 ✓
(`HISTORICAL_FACT`), rule 4 ✓ (`contract_award_amount` is allowlisted), rule 5
✓ (`awarded_contractor` is an explicit award role), rule 6 ✓ (no case-specific
ambiguity — nothing in the claim set contradicts itself), rule 8 ✓ (a single
Tier-1 primary source is sufficient for an explicit awarded-event claim, per
§13).

**Rule 7 (source authority) is the only one this case cannot pass today** —
not because the *claim* is ambiguous (it is maximally clean, deliberately
constructed to leave nothing to interpret), but because **no field on
`Source` can currently prove, deterministically, that the document is a
genuine Tier-1 primary record** rather than a Tier-2/3/4 restatement of the
same fact. **Conclusion: this case correctly evaluates to `AUTO_ELIGIBLE`
under the claim-structural rules alone, but implementing that evaluation
today would still require closing the source-authority gap first (§12) — the
claim semantics are not the blocker; the missing source-tier infrastructure
is.** This is the single most important concrete finding of this design: even
the *best possible* real-world case cannot be marked `AUTO_ELIGIBLE` today
purely on claim content — a small, targeted, non-schema-inventing
enhancement to how `Source.reliability_level` is populated is the actual
remaining prerequisite, not a change to the claim or policy model.

## 16. Completion golden case

*"Replacement of the Runway 11 EMAS was completed on August 15, 2026."*
`EXPLICIT_DOCUMENT_FACT`, `temporal.qualifier = COMPLETED`,
`as_of_date = 2026-08-15`, identity confirmed, source authoritative (by
construction), no contradiction, **no financial claim at all**.

This is the **most conservative possible eligible shape** — rules 4 and 5
(§6) are vacuously satisfied (no financial or relationship fact is present to
misclassify), so the only rules doing real work are 1–3 (identity, materiality,
completed-event temporal state) and 7 (source authority, the same
already-identified gap). A pure completion/status-change claim carries
strictly *fewer* ways to go wrong than an award-with-financial-details claim
(§15) — no currency/amount/role mismatch is even possible to construct.
**Recommendation: if/when the source-authority gap (§12) is ever closed,
completion/status-change claims with no financial component are the single
safest starting point for any real automation pilot** — narrower even than
explicit-award cases, and worth calling out as a distinct, even more
conservative first step in §25's roadmap.

## 17. SFO-$40M adversarial case

Replaying the original danger exactly: correct SFO identity
(`ATTACH_CONFIRMED`), a `RELATIONSHIP` claim naming Runway Safe with a weak,
non-award role (`"mentioned_in_document"` — the same construction already
proven in the Slice 3 checkpoint), and **no `FinancialFact` for the $40M at
all**, because Slice 1's own hard invariant (`FinancialFact.semantic_role`
has no default) makes it structurally impossible to represent an unlabeled
amount as a financial claim in the first place — there is no code path in
this entire pipeline, from extraction through this new policy layer, that
could construct one.

Tracing the outcome: `SignalCandidateOutcome` for this claim set is either
`INSUFFICIENT_MATERIALITY` (if the bare event claim alone doesn't clear
Slice 3's own materiality bar) or `REVIEW_REQUIRED` (if a relationship claim
is present too, per Slice 3's own rule — either is a legitimate, already-
tested Slice 3 result). If `INSUFFICIENT_MATERIALITY`: §5's table routes this
straight to `DO_NOT_PROMOTE`, never reaching the §6 rules at all. If
`REVIEW_REQUIRED`: §6 rule 5 fails immediately — `"mentioned_in_document"` is
not an awarded-relationship role — so the result is `HUMAN_REVIEW_REQUIRED`.

**Expected result confirmed: `NEVER AUTO_ELIGIBLE`, under either branch.**
The exact policy invariant that blocks it is invariant #3 (§24): *"Unlabeled
money never becomes contract value"* — enforced two layers deep, structurally
at Slice 1 (no `FinancialFact` can be built without a role) and again at this
policy layer's own rule 4 (even a hypothetically-mislabeled financial claim
would still need an allowlisted role to pass). No single point of failure
protects this case; two independent layers would both have to be wrong
simultaneously for "$40M Runway Safe contract" to ever be written.

## 18. Review-queue consequence

Only `HUMAN_REVIEW_REQUIRED` rows populate the active queue. Not
`AUTO_ELIGIBLE` (unless a future, separately-authorized audit-sampling policy
explicitly opts in — this design does not build that), not `DO_NOT_PROMOTE`.
A reviewer needs, for each queued row, exactly the fields the task names —
all of which already exist somewhere in the already-built chain, none
requiring new storage beyond what a read-only query joins together at read
time:

- Airport (`SourceAssertion.airport_id` → `Airport`).
- Source (`SourceAssertion.source_id` → `Source`, including whatever tier
  information §12's future fix eventually adds).
- Raw fragment (`SourceAssertion.raw_relevant_text`, already preserved
  verbatim).
- Claims (re-derivable on demand by re-running the appropriate source-family
  extractor against the same raw text — never persisted separately, per the
  original design doc's own §5 "claims are pure and re-derivable" principle,
  still correct and still not revisited by this task).
- `SignalCandidateDecision.reason` (`SourceAssertion.intelligence_review_reason`,
  already persisted).
- The new policy reason (`PromotionPolicyDecision.reason` — not yet
  persisted anywhere; a genuine open question for Slice 7, §25).
- Financial roles, procedural state, temporal state — all already present,
  distinctly, inside the re-derived claims' own `FinancialFact`/`category`/
  `TemporalContext` fields; the queue view should render them, never
  re-summarize or re-interpret them.
- Contradictions/warnings (`SignalCandidateDecision.material_claims`/
  `.warnings`, plus this new layer's own `blocking_reasons`).
- A suggested action — but only ever a *suggestion* a human can override, per
  §19's own explicit constraint.

## 19. Reviewer actions

Conceptual outcomes, in the same terse style as the task's own list:
`APPROVE_SIGNAL`, `REJECT_SIGNAL`, `DEFER`, `NEEDS_MORE_EVIDENCE`,
`MARK_DUPLICATE`.

**Strong preference confirmed and extended**: a reviewer must **never**
silently rewrite evidence semantics. If a reviewer disagrees with how a
claim's financial role, procedural state, or temporal qualifier was
extracted (e.g. they believe `advance_deposit_purchase_order` should really
have been read as something else), that disagreement is a signal the
*extractor* has a defect — the fix belongs in the source-specific adapter
(Slice 2-shaped work, its own review/test/commit cycle), never as an
inline edit a reviewer makes to the claim before approving a Signal.
`APPROVE_SIGNAL` should always mean "I read the preserved raw evidence and
the re-derived claims, and I choose to create/update a Signal using values I
explicitly select from what's already there" — never "I typed in a different
number." This mirrors the already-existing, already-correct precedent: the
design doc's own §6/§7 already established that Signal field population at
promotion time is "a deliberate, reviewed HUMAN CHOICE of which claim's value
to use, never an automatic copy" — this design extends the same discipline to
say a human choice is also never a human *rewrite*.

## 20. Signal-write boundary

Explicit invariants that must hold before **any** `Signal` row is created,
for both routes:

**A. Human-approved route** — all of:
- The `SourceAssertion.identity_guard_decision` is `"ATTACH_CONFIRMED"`.
- A `SignalCandidateDecision` with outcome `REVIEW_REQUIRED` exists and is
  persisted.
- A human reviewer explicitly chose `APPROVE_SIGNAL` against that specific,
  re-derivable claim set.
- Every financial/vendor/date field the reviewer sets traces to a specific,
  named claim the reviewer read — never a default, never a guess, never a
  raw-text re-scan.
- The created/updated `Signal.source_id` cites the real `Source`; provenance
  is never fabricated.

**B. Future automatic route** — all of A's non-human conditions, plus:
- `PromotionPolicyDecision.outcome == AUTO_ELIGIBLE`.
- The source-authority gap (§12) has actually been closed (a real,
  deterministic tier value exists and was checked) — not assumed.
- A separate, explicit, human-authorized policy toggle exists and is on for
  this specific claim shape/source family (this design does **not** define
  what that toggle looks like — Slice 10, §25 — only that one must exist and
  default off).
- The write itself is auditable after the fact: which claims, which policy
  rule combination, and which toggle authorized it must all be reconstructable
  from what gets persisted — a gap flagged in §22 (`Signal` has no field
  recording *how* a row came to exist).

## 21. Publication boundary

**Investigated directly in the real code** (`app/static_export/build.py`):
`_is_public_signal()` is a two-line function that excludes exactly `Signal`
ids `52` and `54` — a hardcoded quarantine of two specific bad rows — and
**every other `Signal` row is included in the public static export
unconditionally**. There is no `Signal.published`/`Signal.public` field, no
review-state gate, nothing separating "this row exists in the database" from
"this row is live on the public site" beyond that one two-ID exclusion list.

**Finding: `CREATE_SIGNAL` and `PUBLISH_SIGNAL` are, today, the same
effective boundary.** This is fine for the current, fully-human,
one-Signal-at-a-time world (a human creating a Signal already intends to
publish it). It becomes **unsafe** the moment any automation is introduced,
even narrowly: an `AUTO_ELIGIBLE` case that a future policy is allowed to
*create internally* for audit/staging purposes must not, by the same act,
instantly become public-facing investor intelligence with no further human
step. **Recommendation: any future slice that writes a `Signal` from an
automated path must first add a real `published`/equivalent boolean (schema
work, explicitly out of scope here) so `CREATE_SIGNAL` and `PUBLISH_SIGNAL`
can be genuinely separate actions** — not implemented, not designed in
detail, flagged as a hard prerequisite for Slice 10 specifically (§25).

## 22. Signal-model fitness

Classified per the task's three-way scale, against what future auto-promotion
(not today's human-only usage) would need to preserve:

| Concern | `Signal` field(s) | Classification |
|---|---|---|
| Event category | `category` (free string, curated presentation mapping in `_CATEGORY`) | **SUFFICIENT** — already generic, already works for human-chosen values. |
| Status/procedural state | `status` (free string, `STATUS_PRESENTATION`: identified→cip/master_plan/alp/environmental_review→funded→design→procurement→under construction→completed) | **LIMITED_BUT_USABLE** — reasonable lifecycle granularity, but no field distinguishes *requested* from *approved* the way `PROCEDURAL_REQUEST`/`TemporalQualifier.REQUESTED_PENDING_APPROVAL` already can at the claim level; a human currently has to infer this from `notes` text. |
| Temporal meaning | `planning_year`/`procurement_year`/`construction_start`/`completion_date` (four separate, independently-nullable fields) | **LIMITED_BUT_USABLE** — reasonably rich, but no explicit qualifier field; which of four dates is populated is the only signal of what kind of date it is. |
| Financial semantic role | `estimated_total_value_usd`/`estimated_emas_value_usd` (two generic `Numeric(14,2)` fields, no role tag) | **BLOCKER_FOR_AUTO_PROMOTION** — the exact, already-real collapse this whole architecture exists to prevent (§9). |
| Vendor | `confirmed_vendor` (confirmed fact) vs. `likely_supplier` (RWI's own inference) | **LIMITED_BUT_USABLE** — a real, already-correct semantic split exists (confirmed vs. inferred), but collapses every relationship *role* (material supplier, installation oversight, awarded contractor) into one flat confirmed-vendor string. |
| Provenance | `source_id` (single, optional FK to one `Source`, the whole document — no fragment/claim link) | **BLOCKER_FOR_AUTO_PROMOTION** — already flagged as a real gap in the original design doc (§2/§3); becomes materially worse once any automated write exists, since there is then no way to prove *which specific claims* justified an automatically-created row. |
| Evidence source (tier) | `source_id` → `Source.reliability_level` | **LIMITED_BUT_USABLE**, bottlenecked by §12's own finding — the link exists, the tier granularity behind it does not. |
| Review state | *(no field exists)* | **BLOCKER_FOR_AUTO_PROMOTION** — `Signal` cannot currently record whether a row was created by a human review action or (hypothetically) automatically, nor whether it has been published (§21). |

No redesign recommended in this task, per its own explicit instruction — this
table exists to make the real gaps legible for whoever eventually scopes the
schema work, not to close them now.

## 23. Pure policy-core recommendation

**Recommend Slice 6 implement exactly this, and nothing more:**

```python
def evaluate_promotion_policy(
    signal_candidate: SignalCandidateDecision,
    claims: tuple[Claim, ...],
    context: PromotionPolicyContext,
) -> PromotionPolicyDecision
```

`PromotionPolicyContext` — the "small, new, explicit context object" §4
argues for, carrying exactly what §6–§13 need and nothing `SignalCandidateContext`
already carries:

```python
@dataclass(frozen=True)
class PromotionPolicyContext:
    source_authority_tier: str | None   # None until §12's gap is closed - fails closed
    corroborating_source_count: int = 1 # independently-provenanced sources supporting the same event
    superseded: bool = False            # reused verbatim from SignalCandidateContext's own field
```

`PromotionPolicyDecision`:

```python
@dataclass(frozen=True)
class PromotionPolicyDecision:
    outcome: PromotionPolicyOutcome              # AUTO_ELIGIBLE / HUMAN_REVIEW_REQUIRED / DO_NOT_PROMOTE
    reason: str                                   # deterministic, template-built, same discipline as Slice 3
    auto_eligibility_reasons: tuple[str, ...] = () # which §6 rules this claim set DID satisfy
    review_reasons: tuple[str, ...] = ()           # which §7 shapes triggered human review
    blocking_reasons: tuple[str, ...] = ()         # which §6 rules FAILED, specifically (§6 rule 6's own field)
```

No DB, no ORM, no `Signal` import — the same purity discipline already
proven twice (Slice 1, Slice 3), AST-verifiable the same way. Minimal
context: exactly the three fields above; nothing about UI, review-queue
state, or reviewer identity belongs in this pure core (that's Slice 7/8/9
territory, §25).

## 24. Safety invariants

The task's own twelve, confirmed and, where useful, tied to where each is
*already* enforced in committed code (not merely asserted here):

1. **Identity confirmation does not imply Signal eligibility** — already true
   structurally: `ATTACH_CONFIRMED` alone yields `REVIEW_REQUIRED` at best in
   Slice 3, never a Signal; this design adds §6's further rules on top,
   never treats identity alone as sufficient.
2. **Materiality does not imply auto-promotion** — the entire point of §5's
   split; `REVIEW_REQUIRED` (materiality established) still requires every
   §6 rule to pass before `AUTO_ELIGIBLE`.
3. **Unlabeled money never becomes contract value** — enforced twice,
   independently (§17): Slice 1's own structural guarantee (no
   `FinancialFact` without a role) and this layer's own allowlist (§9).
4. **Procedural request never becomes award** — enforced by §6 rule 3
   (temporal qualifier must be `HISTORICAL_FACT`/`COMPLETED`) and §10's own
   table; MSP's own real evidence is the proof case (§14).
5. **Planned never becomes completed because time passed** — already
   AST-proven zero-current-time-dependency at Slice 3; this layer inherits
   that by construction (§11) and adds no new time-reading code.
6. **Contradicted evidence never auto-promotes** — `CONTRADICTED` routes to
   `DO_NOT_PROMOTE` directly (§5's table), never reaching §6 at all.
7. **Source authority must be explicit for auto-eligibility** — §6 rule 7,
   §12; currently unsatisfiable for any real case, by design (fails closed,
   not fails open, when the tier is unknown).
8. **Human review does not rewrite source evidence** — §19's own explicit
   constraint; a disagreement with an extractor's output is an extractor bug
   report, never an inline review-time edit.
9. **`DO_NOT_PROMOTE` does not delete evidence** — §8's own explicit
   confirmation; `SourceAssertion` rows remain exactly as governed and
   persisted regardless of promotion outcome.
10. **`AUTO_ELIGIBLE` does not necessarily mean auto-published** — restated
    directly in §5 and §20; eligibility and the act of writing are two
    separate, independently-gated things, one of which (the actual write
    path) this design does not build.
11. **Every Signal must retain provenance** — already true today
    (`Signal.source_id`), though only at document granularity (§22's own
    flagged limitation); this design does not weaken it and flags exactly
    where it would need to strengthen before automation.
12. **Public publication is a separate policy decision if needed** — §21's
    own central finding: it is *not* separate today, and must become so
    before any automated Signal-creation path is built (Slice 10
    prerequisite).

## 25. Recommended implementation slices

Largely as sketched in the task, with two adjustments justified by this
design's own findings — both explained, not silently substituted:

- **Slice 6: pure `PromotionPolicyDecision` core** (§23) — as suggested,
  first, since nothing else can be built without it.
- **Slice 7: persist the policy decision, if genuinely needed** — as
  suggested, mirroring Slice 4's own additive-columns discipline exactly
  (`SourceAssertion.promotion_policy_decision`/`.promotion_policy_reason`).
  Genuinely needed once a real review queue (Slice 8) exists, so a query
  doesn't have to recompute the policy for every row on every page load —
  the same justification Slice 4 itself already used for persisting
  `intelligence_review_decision`.
- **Slice 8: read-only human review queue** — as suggested, surfacing only
  `HUMAN_REVIEW_REQUIRED` rows (§18), no write path.
- **Adjustment — a "Slice 8.5"-shaped prerequisite before Slice 9, not a
  full slice of its own**: closing the source-authority gap (§12) enough
  that at least one real, currently-`HUMAN_REVIEW_REQUIRED`-by-default case
  could genuinely reach `AUTO_ELIGIBLE` under real data. Without this, Slice
  9's own human-approval workflow would never see an `AUTO_ELIGIBLE` row to
  contrast against, and Slice 10 would have literally nothing to opt into.
  This is schema/data work (tagging real `Source` rows with a real tier),
  explicitly not designed here, but worth naming as a recognized gap between
  Slice 8 and Slice 9 rather than silently discovering it later.
- **Slice 9: human-approved Signal promotion** — as suggested, but should
  also close the `CREATE_SIGNAL`/`PUBLISH_SIGNAL` schema gap (§21) *before*
  or *within* this slice, since even a human-only promotion action benefits
  from that separation (a reviewer approving a Signal for internal record-
  keeping before it's ready for the public site is a real, useful workflow
  this repository doesn't have yet, independent of automation).
- **Slice 10: optional `AUTO_ELIGIBLE` automation with explicit opt-in** — as
  suggested, explicitly last, explicitly requiring the §20-B invariants
  (toggle default off, auditability) and the §21 publication-boundary fix
  already in place.

## 26. Risks/unresolved questions

- **Source-authority tiering is the single largest open gap** (§12/§15) —
  every golden case in this document (§15, §16) is blocked on it, not on
  claim semantics. Closing it is small in code terms (tag `Source` rows at
  ingestion) but requires a real, human policy decision about what counts as
  Tier 1 for each current and future source family — a governance question,
  not an engineering one, and squarely out of this task's own scope.
- **Multi-source corroboration detection does not exist** (§13) — two claims
  from genuinely independent primary sources are currently indistinguishable,
  structurally, from the same content re-syndicated. Matters only for claim
  shapes that need corroboration at all (most explicit single-document
  awards don't); still unresolved.
- **`Signal`'s financial/provenance/review-state limitations** (§22) are real
  and will need schema work before any actual automated write is safe — not
  before eligibility can be *evaluated*, which this design's policy core can
  do entirely without touching `Signal`.
- **The `CREATE_SIGNAL`/`PUBLISH_SIGNAL` separation** (§21) does not exist
  and is a hard prerequisite specifically for Slice 10, arguably useful even
  sooner (Slice 9) for purely human workflows.
- **`suggested_signal_category` remains unpopulated** (Slice 3's own
  documented gap) — this design does not change that, and none of §6's
  rules depend on it; a reviewer or a future promotion action still has to
  choose the category by hand, same as today.
- **This design assumes `evaluate_promotion_policy()` only ever runs on rows
  that already reached `SignalCandidateOutcome.REVIEW_REQUIRED`** — worth
  confirming explicitly as a precondition when Slice 6 is actually built,
  the same way Slice 4's own `persist_intelligence_review()` explicitly
  documents its own identity-gate precondition.

---

`RWI_SIGNAL_PROMOTION_POLICY_SLICE5_DESIGN_COMPLETE`
