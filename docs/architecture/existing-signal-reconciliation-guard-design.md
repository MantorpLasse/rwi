# Existing-Signal Reconciliation Guard — Design

Status: design only. No production code, schema, or data changed by this document.
Baseline: HEAD `99e9bfda155bb1d9f0c89b98f1cdc1e20ac64e30`, 1225 tests passing.
Golden case: SourceAssertion #222 (MSP, MAC PD&E memo, 2024-08) vs. Signal #67 (MSP,
Runway Safe shareholder letter, 2025-05), reconciled by a human via
`ReviewerAction #2 (MARK_DUPLICATE, duplicate_of_signal_id=67)`.

**Hardened at a review/correct/commit checkpoint (same HEAD, 2026-08-19).** The
original version of this document proposed a "2 or more independent supporting
categories" threshold for `POSSIBLE_EXISTING_SIGNAL_MATCH`. That rule was found,
under adversarial review, to be an unsafe disguised score — see Section 18 for the
full correction record. The rule is now: **at least one identity-bearing anchor is
required; compatibility evidence (category, vendor, temporal proximity, year),
however many kinds co-occur, can never by itself trigger a block.** Sections 5, 6, 8,
9, 10, 14, and 15 were rewritten accordingly. Read Section 18 first if you read this
document before the checkpoint.

## 1. Executive summary

RWI's governed pipeline (identity guard → intelligence review → promotion policy →
human review → `ReviewerAction` → governed Signal creation) has no step that asks,
*before* creating a Signal, whether an existing Signal already represents the same
underlying real-world project. The MSP case shows this gap is real: SourceAssertion
#222 was heading toward a brand-new Signal until a human, working the review queue,
recognized #222 as an earlier stage of the same EMAS replacement project already
represented by Signal #67, and recorded `MARK_DUPLICATE` instead of approval.

This document designs a conservative, explainable **reconciliation guard** that sits
immediately before governed Signal creation and answers one narrow question: is there
an existing Signal that *might* represent the same underlying project as this new
evidence? It never merges evidence, never scores confidence, never uses text
similarity, and never resolves ambiguity itself. Its only three outcomes are
`CLEAR_TO_CREATE`, `POSSIBLE_EXISTING_SIGNAL_MATCH` (which blocks creation and routes
to a human), and `ALREADY_LINKED` (idempotent no-op). The design follows the
pure-decision-core / DB-adapter split already proven three times in this codebase
(`evaluate_signal_candidate`, `evaluate_promotion_policy`) and reuses the append-only,
human-actor-recorded reconciliation pattern already proven once for a different axis
(`InstallationAssertionLink` / `PhysicalInstallationIdentity`).

The design also documents a real, structural gap: `Signal` has no link at all to
`PhysicalInstallationIdentity`, so even a confirmed same-physical-installation
reconciliation today gives the guard no automatic path to "therefore same Signal
candidate." That gap is named, not solved, here (Section 10).

This document is a specification for a future implementation slice (R1 onward,
Section 16). Nothing in it is implemented by this task.

## 2. Current architecture findings

Freshly re-read for this task: `app/services/governed_signal_creation.py`,
`app/models/signal.py`, `app/models/source_assertion.py`,
`app/models/reviewer_action.py`, `app/models/physical_installation_identity.py`, and
`app/services/promotion_policy_evaluation.py` (enum/class shapes). Read-only inspected
the real rows for Signal #67 and SourceAssertion #222 (Section 8).

**`create_signal_from_approved_review(session, source_assertion, *, title, category,
confidence, ...)`** — the sole governed path to creating a Signal from human review.
Gates on, in order: identity guard == `ATTACH_CONFIRMED`, intelligence review ==
`REVIEW_REQUIRED`, promotion policy == `HUMAN_REVIEW_REQUIRED`, `airport_id` present,
latest `ReviewerAction` == `APPROVE_SIGNAL`. Validates human-selected fields
(`status` not in a disallowed-terminal-state set, `runway_id` belongs to the same
airport if given). If `source_assertion.signal_id` is already set, compares a
`(title, category, confidence)` signature against the existing Signal — reuses on
match, raises on drift (idempotency for *repeat calls on the same SourceAssertion*,
a different problem from this task's). Otherwise constructs a new `Signal(published=False,
...)`, flushes, sets `source_assertion.signal_id`, flushes again. **This is the exact
insertion point for the reconciliation guard** (Section 12): it must run after the
existing governance gates and *before* the `signal_id is None` branch reaches
`Signal(...)` construction.

**`link_source_assertion_to_duplicate_signal(session, source_assertion)`** — the
existing consumer of `MARK_DUPLICATE`. Requires the latest `ReviewerAction` to be
exactly `MARK_DUPLICATE`, resolves the target via `duplicate_of_signal_id`, and is
idempotent/drift-safe the same way `create_signal_from_approved_review` is. This is
already the correct *post-human-decision* linking mechanism; the reconciliation guard
does not replace it, it decides whether a human needs to reach it (Section 13).

**`Signal`** fields confirmed by fresh read: `id, airport_id (required), runway_id
(optional, whole Runway only — no runway-end column), source_id (optional, single
document), installation_id, title, category, confidence, target_year, notes (private),
manual_year_estimate (private), source_notes (public), status, planning_year,
procurement_year, construction_start, completion_date, estimated_total_value_usd,
estimated_emas_value_usd, probability_score, supplier, likely_supplier,
supplier_reason, confirmed_vendor, last_verified_at, published, created_at,
updated_at`, plus relationships `airport, runway, source, installation`, and the
Slice-9C-added `supporting_source_assertions` (reverse of `SourceAssertion.signal_id`,
`passive_deletes=True`). **No field or relationship connects `Signal` to
`PhysicalInstallationIdentity`** — confirmed by reading `signal.py` in full. This is
the single largest schema gap for reconciliation (Section 10).

**`SourceAssertion`** fields confirmed relevant to reconciliation: `airport_id,
runway_id (optional), runway_end (free text), source_id, artifact_identity,
identity_guard_decision, intelligence_review_decision, promotion_policy_decision,
signal_id (nullable FK to signals.id), installation_assertion_links (to
`InstallationAssertionLink`), reviewer_actions`. Cardinality: many SourceAssertions
may point at one Signal; one SourceAssertion points at most one Signal.

**`ReviewerAction`**: `REVIEWER_ACTIONS = (APPROVE_SIGNAL, REJECT_SIGNAL, DEFER,
NEEDS_MORE_EVIDENCE, MARK_DUPLICATE)`, append-only via `before_update`/`before_delete`
event listeners raising `ValueError`, `duplicate_of_signal_id` CHECK-constrained to be
non-null iff action is `MARK_DUPLICATE`. `get_latest_reviewer_action()` orders by
`(created_at DESC, id DESC)` — "latest" means most recently recorded, not a
chain-walk.

**`PhysicalInstallationIdentity` / `InstallationAssertionLink`** (Slice 6C) — the
existing precedent for a *different* reconciliation axis: "is this evidence about the
same underlying physical EMAS installation." `RECONCILIATION_OUTCOMES =
(SAME_PHYSICAL_INSTALLATION, DIFFERENT_PHYSICAL_INSTALLATION, UNRESOLVED)`.
`InstallationAssertionLink` is append-only (same immutability pattern as
`ReviewerAction`), human-`actor`-recorded, `reason`-carrying, and supports
`supersedes_link_id` for correction-by-superseding rather than mutation. This is the
closest existing precedent for *shape* (an append-only, human-adjudicated,
evidence-linking decision with an explicit "unresolved" state) even though it operates
on physical installations, not Signals, and even though nothing today connects its
output to `Signal`.

**`promotion_policy_evaluation.py`**: confirms `SourceAuthorityTier`,
`PromotionPolicyOutcome` enums and `evaluate_promotion_policy(...)` as another
instance of the pure-core pattern this design follows.

## 3. Problem definition

Governed Signal creation today asks "is this SourceAssertion's own evidence strong
enough and human-approved?" It never asks "does this project already have a Signal?"
Nothing upstream of `create_signal_from_approved_review()` looks sideways across
existing Signals. The MSP case worked out correctly only because a human happened to
notice the overlap while working the review queue and chose `MARK_DUPLICATE` over
`APPROVE_SIGNAL` — the system offered no structural help toward that recognition, and
nothing would have stopped `APPROVE_SIGNAL` + Signal creation from producing a
duplicate Signal #69 representing the same project as #67.

The guard designed here formalizes and automates the *detection* of "this might
already exist," strictly upstream of human judgment, without ever automating the
*decision*. The decision — same project or not — remains exclusively human, exercised
through the existing `MARK_DUPLICATE` / `APPROVE_SIGNAL` vocabulary.

## 4. Decision-layer placement

Recommendation: mirror the `evaluate_signal_candidate()` / `evaluate_promotion_policy()`
split exactly.

- **Pure core** — `evaluate_existing_signal_reconciliation(source_assertion_snapshot,
  claims, candidate_signals, context) -> ExistingSignalReconciliationDecision`. Takes
  plain dataclasses, not live ORM objects: a snapshot of the new evidence (airport_id,
  runway_id, runway_end text, claims, artifact_identity, source chronology) and a list
  of `ReconciliationCandidateSignal` snapshots (id, airport_id, runway_id, category,
  confidence, confirmed_vendor, likely_supplier, source_notes-derived structured
  fields where available, supporting_source_assertion snapshots, created_at). No
  session, no query, no I/O, no randomness, no wall-clock reads beyond what's passed
  in `context`. Fully unit-testable with plain data.
- **Adapter** — `find_reconciliation_candidates(session, source_assertion) ->
  list[ReconciliationCandidateSignal]`. The only DB-touching piece. Narrows the
  universe (at minimum by `airport_id`; optionally by `runway_id` when populated on
  both sides) and converts ORM rows to snapshots. It may use SQL filtering to keep the
  candidate set small, but per the Core Safety Principle, its filtering is a *search
  optimization*, never a decision — the pure core must still independently classify
  every candidate it receives, and the adapter must never pre-rank, pre-score, or
  drop a candidate for a reason that isn't also independently defensible as "this
  candidate cannot possibly be the same project" (e.g., different airport).

**Critical evaluation of the split**: this is the same split proven three times
already in this codebase, so it is very likely correct. The one respect in which this
task differs from the two precedents: `evaluate_signal_candidate` and
`evaluate_promotion_policy` each look at *one* SourceAssertion's own claims in
isolation. This guard is inherently *relational* — it compares new evidence against
a set of existing Signals. That relational shape doesn't break the pure-core pattern,
but it does mean the "pure core" signature must accept a *list* of candidates (not a
single ORM row pre-fetched by the caller), and the outcome must be able to name
*multiple* candidates (Section 5) rather than reducing to one comparison. This is
still ORM-free and deterministic given the same snapshot inputs, so the pattern holds.

## 5. Outcome model

```
outcome: Literal["CLEAR_TO_CREATE", "POSSIBLE_EXISTING_SIGNAL_MATCH", "ALREADY_LINKED"]
```

- **`ALREADY_LINKED(signal_id)`** — `source_assertion.signal_id` is already set. This
  is checked first, before any candidate discovery or evidence classification runs at
  all (it's cheaper and unambiguous). Idempotent: repeated calls against the same
  already-linked SourceAssertion always return the same outcome with the same
  `signal_id`. This mirrors the existing idempotency behavior in
  `create_signal_from_approved_review()` and `link_source_assertion_to_duplicate_signal()`
  and must not duplicate or conflict with it — the guard *observes*
  `source_assertion.signal_id`, it never sets it.
- **`POSSIBLE_EXISTING_SIGNAL_MATCH(candidate_signal_ids: tuple[int, ...], reasons:
  tuple[str, ...])`** — one or more existing Signals carry at least one genuine
  **identity anchor** (Section 6) connecting them to the new evidence. **Must fail
  closed on multiple candidates**: if two or more Signals each independently carry an
  anchor, all are returned together in `candidate_signal_ids`; the guard never ranks
  or picks a "best" one. Choosing among multiple candidates (or rejecting all of them)
  is a human decision, exercised via `MARK_DUPLICATE(duplicate_of_signal_id=<chosen>)`
  or a future explicit "none of these" resolution (Section 13). Blocks Signal creation
  (Section 12).
- **`CLEAR_TO_CREATE(advisory_candidate_signal_ids: tuple[int, ...] = (),
  advisory_reasons: tuple[str, ...] = ())`** — no existing Signal in the candidate set
  carries an identity anchor, so nothing blocks creation. If one or more candidates
  nonetheless accumulate `SUPPORTING_EVIDENCE`-tier compatibility signals (category,
  vendor, temporal proximity) without an anchor, those candidates and the reasons for
  their compatibility are carried as **non-blocking advisory metadata** on the same
  `CLEAR_TO_CREATE` outcome — visible to a reviewer, acted on at their discretion
  (e.g. by choosing `MARK_DUPLICATE` anyway), but never capable of stopping Signal
  creation by itself. Signal creation may proceed, subject to every existing
  governance gate, in every `CLEAR_TO_CREATE` case regardless of whether advisory
  candidates are present.

**Corrected threshold rule (post-checkpoint — see Section 18):**
`POSSIBLE_EXISTING_SIGNAL_MATCH` requires **at least one `STRONG_IDENTITY_EVIDENCE`
anchor** (Section 6) for the candidate. `SUPPORTING_EVIDENCE`-tier compatibility
signals — category, vendor, temporal proximity, shared year — can **never** trigger
`POSSIBLE_EXISTING_SIGNAL_MATCH` on their own, no matter how many distinct kinds
co-occur. Counting independent weak categories and declaring a match once "enough" of
them agree is a disguised score with a threshold of N; the Core Safety Principle
prohibits scoring identity, not just prohibiting the specific word "confidence." An
identity anchor is not a stronger signal on the same scale as compatibility evidence —
it is evidence of a structurally different kind (the same physical thing, the same
literal document, the same canonical location), and only that kind of evidence may
gate creation. Compatibility evidence remains valuable — it is exactly what should
appear in `advisory_reasons` — but it informs, it does not block.

## 6. Structural evidence taxonomy

Each evidence axis is classified independently and never collapsed into one score.
Post-checkpoint (Section 18), axes are additionally tagged **[ANCHOR]** or
**[COMPATIBILITY]**: only an `[ANCHOR]` axis reaching `STRONG_IDENTITY_EVIDENCE` may
ever gate `POSSIBLE_EXISTING_SIGNAL_MATCH`; every `[COMPATIBILITY]` axis, regardless
of its own tier, may only ever contribute to `CLEAR_TO_CREATE`'s non-blocking advisory
metadata, never to a block, and never in combination with other `[COMPATIBILITY]`
axes.

| Axis | Classification | Anchor/Compat | Notes |
|---|---|---|---|
| Same `airport_id` | `INSUFFICIENT_ALONE` | [COMPATIBILITY] | Near-universal precondition for even considering a candidate; never sufficient alone, never an anchor. |
| Same canonical `runway_id` (populated, non-null, on both the candidate Signal and a structurally-resolved runway for the new evidence — not free text) | `STRONG_IDENTITY_EVIDENCE` when populated and equal / `NOT_CURRENTLY_REPRESENTABLE` when either side is null | [ANCHOR] | A real, checkable anchor today when both sides happen to carry it — but `SourceAssertion.runway_id` and `Signal.runway_id` are both frequently null in practice (both are null for #222 and #67 — confirmed by fresh read-only inspection at this checkpoint), so this anchor is usable only intermittently. A differing non-null `runway_id` on both sides is a *disconfirming* signal (Section 9) — it does not merely fail to help, it actively argues against the candidate. |
| Shared physical-installation identity: the new SourceAssertion's own `InstallationAssertionLink.outcome == SAME_PHYSICAL_INSTALLATION` names the same `physical_installation_id` as an `InstallationAssertionLink` already recorded for one of the candidate Signal's `supporting_source_assertions` | `STRONG_IDENTITY_EVIDENCE` when both links exist and agree / `NOT_CURRENTLY_REPRESENTABLE` when either side lacks a link | [ANCHOR] | **Corrected at this checkpoint**: the original document classified this entire axis `NOT_CURRENTLY_REPRESENTABLE` because `Signal` has no direct FK to `PhysicalInstallationIdentity`. That was an overstatement — the anchor is derivable *transitively*, today, with no schema change, by joining through `SourceAssertion.installation_assertion_links` on both the new assertion and the candidate's `supporting_source_assertions`. It requires no new column. In practice it is still usable only when *both* sides have already been through governed installation reconciliation (Slice 6C) — confirmed via fresh inspection at this checkpoint: #222 has zero `InstallationAssertionLink` rows, and #67 had zero `supporting_source_assertions` before its human resolution, so this anchor is unavailable for the golden case specifically, but it is a real, near-term-buildable anchor for future cases and should not be written off as a hard schema gap (Section 10). |
| Event/category semantic overlap (e.g. both "replacement") | `SUPPORTING_EVIDENCE` | [COMPATIBILITY] | Weak alone; categories are coarse and shared across many distinct projects at an airport — MSP itself currently has two "replacement" Signals (#41 and #67) for two different runway ends of the same runway pair (Section 9). Never an anchor, never combinable with other compatibility axes to produce a block. |
| Lifecycle-stage relationship (candidate's evidence describes a later, more confirmed stage of an event the new evidence describes earlier) | `SUPPORTING_EVIDENCE` | [COMPATIBILITY] | See Section 7 — requires temporal + semantic reasoning together, never inferred from either alone. Never an anchor. |
| Vendor/relationship party-name match (claim `RelationshipFact.party` vs. `Signal.confirmed_vendor`/`likely_supplier`) | `SUPPORTING_EVIDENCE` | [COMPATIBILITY] | Explicitly never sufficient alone per the Core Safety Principle, regardless of exact string match, and never an anchor even combined with other compatibility axes — a shared vendor at a busy airport is common and proves nothing about which project. |
| Temporal/chronological compatibility (candidate's supporting evidence postdates the new evidence, consistent with "later stage of the same multi-year project") | `SUPPORTING_EVIDENCE` | [COMPATIBILITY] | Direction matters — evidence for an *earlier* stage than an already-completed candidate is weaker/absent as supporting evidence, not automatically disqualifying. Never an anchor. |
| Target/planning/procurement/construction/completion year fields | `INSUFFICIENT_ALONE` | [COMPATIBILITY] | Coarse, frequently null, and a shared year alone says nothing about project identity. |
| Source chronology alone (candidate created after/before the new assertion) | `INSUFFICIENT_ALONE` | [COMPATIBILITY] | Says something about possible sequencing, nothing about identity by itself. |
| Financial amount equality or resemblance | `UNSAFE_FOR_RECONCILIATION` | never [COMPATIBILITY] nor [ANCHOR] | Per Core Safety Principle. May only ever be used to help *rule out* a candidate when roles are structurally incompatible (e.g., a candidate's only amount is an operating-budget figure with a role incompatible with any EMAS capital project) — never to confirm one, never to contribute to advisory metadata either. See the live example in Section 8. |
| Title/free-text similarity, and all other raw free text (`raw_relevant_text`, `raw_runway_value`, `raw_runway_end_value`, `source_notes`) | `UNSAFE_FOR_RECONCILIATION` | never [COMPATIBILITY] nor [ANCHOR] | Explicitly forbidden by the Core Safety Principle — even when, as in the MSP #41/#222 case (Section 9), the raw text is the *only* place a human-readable distinction (e.g. "12R end" vs. "30L") actually exists. The guard must accept that it cannot see this distinction rather than reach into raw text for it. |
| Existing SourceAssertion provenance — new assertion's `artifact_identity`/`source_id` exactly matches provenance already recorded on the candidate (i.e., literally the same source document already contributed to that Signal) | `STRONG_IDENTITY_EVIDENCE` | [ANCHOR] | The strongest possible signal: the same document already produced this Signal. Not applicable to the MSP case (#222's source #70 differs from both #67's source #45 and #41's source #15) but a real, checkable axis via `Signal.supporting_source_assertions`. |
| Existing Signal's `supporting_source_assertions` sharing airport + category + a `SUPPORTING_EVIDENCE`-tier overlap with the new evidence (aggregate, not single-document) | `SUPPORTING_EVIDENCE` | [COMPATIBILITY] | Distinct from the single-document case above; weaker because it aggregates several independently-weak signals from the *linked assertions*, not the candidate's own fields. Never an anchor regardless of how many linked assertions agree. |

## 7. Lifecycle-stage reconciliation

The MSP case is not two documents describing the same textual state — #222 describes
a *requested, not-yet-confirmed* sole-source relationship and an advance-deposit PO
request; #67 describes a *confirmed* vendor and a signed order. They are the same
underlying project at two different lifecycle stages, and the reconciliation
mechanism must recognize that relationship **without rewriting either side's
evidence toward the other's state**.

Concretely: when a human resolves #222 → #67 via `MARK_DUPLICATE`, this must never
cause `#222`'s own extracted claims (`requested_sole_source_vendor`, "not a confirmed
award") to be edited, re-labelled, or have their `relationship.role` changed to
`confirmed`. `SourceAssertion.signal_id = 67` records only "this evidence is now
associated with Signal #67," not "this evidence's claims are now equivalent to
Signal #67's fields." The claims persisted from #222 remain exactly what MAC's
2024-08 memo said, forever — a later Signal confirming the vendor does not, and must
never, promote an earlier requested-vendor claim into a confirmed-vendor claim. This
is Invariant 2 in Section 15, and it is the direct generalization of the financial-
role-preservation discipline (never collapsing `advance_deposit_purchase_order` into
`project_total`) already established for claims in this codebase — applied here to
relationship state instead of financial state.

Practically, the guard reasons about lifecycle stage only as *temporal compatibility*
between candidate and new evidence (Section 6's "temporal/chronological compatibility"
row) — never by asserting a canonical stage vocabulary onto either side. No
`lifecycle_stage` enum is introduced by this design (see Section 10 and Section 17 on
why that's deferred, not solved, here).

## 8. MSP #222 → Signal #67 golden walkthrough

**This section was rewritten at the review checkpoint (Section 18).** The original
version concluded `POSSIBLE_EXISTING_SIGNAL_MATCH` here; under the corrected,
anchor-requiring rule, the honest answer is different, and this section says so
plainly rather than preserving the more satisfying-looking original conclusion.

**Pre-resolution state** (what the guard would see *before* the human recorded
`MARK_DUPLICATE`, reconstructed from the real, read-only-inspected rows — this
walkthrough does not use the recorded `MARK_DUPLICATE` as guard input, only the
underlying evidence that predates it; freshly re-verified at this checkpoint via a
new read-only query, not merely carried over from the prior investigation):

- New evidence: SourceAssertion #222, `airport_id=45`, `runway_id=None`,
  `raw_runway_value='12R/30L'` (free text, not resolved to a canonical runway),
  `source_id=70`, zero `InstallationAssertionLink` rows (confirmed fresh at this
  checkpoint — `SELECT * FROM installation_assertion_links WHERE assertion_id=222`
  returns no rows), claims include a `requested_sole_source_vendor`-shaped
  relationship claim naming Runway Safe, an `advance_deposit_purchase_order`
  financial claim ($1.59M), a `project_ceiling`/CIP financial claim ($19M), and
  category-qualifying claims consistent with "replacement."
- Candidate discovery: `find_reconciliation_candidates()` queries Signals with
  `airport_id=45`. **This returns two candidates, not one** — Signal #67
  (`runway_id=None, category='replacement', confidence='high',
  confirmed_vendor='Runway Safe', source_id=45`, a May-2025 confirmed-order
  shareholder letter) and Signal #41 (`runway_id=None, category='replacement',
  confidence='high', confirmed_vendor=None, source_id=15`, a USAspending FY2025
  grant record whose own `source_notes` states it reconstructs the EMAS "AT THE
  RUNWAY 12R END" of the same 12R/30L pair). The original walkthrough considered
  only #67; discovering #41 in this checkpoint's fresh inspection materially changes
  the analysis (see below and Section 9).
- Anchor-axis classification for **(#222, #67)**:
  - `runway_id` axis → `NOT_CURRENTLY_REPRESENTABLE` (both null).
  - Physical-installation-identity axis → `NOT_CURRENTLY_REPRESENTABLE` (#222 has zero
    `InstallationAssertionLink` rows; #67 had zero `supporting_source_assertions`
    before resolution, so there is nothing on the candidate side to link against
    either).
  - Source-document provenance → no overlap (`source_id=70` vs. `source_id=45`).
  - **No anchor axis reaches `STRONG_IDENTITY_EVIDENCE`. Zero identity anchors connect
    #222 to #67.**
- Anchor-axis classification for **(#222, #41)**: identical result — no populated
  `runway_id` on either side, no `InstallationAssertionLink` for #222, no
  `supporting_source_assertions` for #41, different `source_id` (70 vs. 15). **Zero
  identity anchors connect #222 to #41 either.**
- Compatibility-axis (non-blocking, advisory-only) classification:
  - vs. #67: category overlap (`replacement`/`replacement`) — `SUPPORTING_EVIDENCE`;
    vendor overlap (#222's requested-vendor claim names "Runway Safe"; #67's
    `confirmed_vendor='Runway Safe'`) — `SUPPORTING_EVIDENCE`; temporal compatibility
    (#67's 2025-05 letter postdates #222's 2024-08 memo, consistent with "later
    confirmed stage") — `SUPPORTING_EVIDENCE`.
  - vs. #41: category overlap (`replacement`/`replacement`) — `SUPPORTING_EVIDENCE`;
    no vendor field is populated on #41 at all, so the vendor axis contributes
    nothing; #41's `planning_year=2025` is weakly temporally compatible with #222's
    2024-08 memo — `SUPPORTING_EVIDENCE` (weak).
  - Financial amounts (the $1.59M/$19M on #222's side, $9.2M on #41's, none on #67's
    own record) → `UNSAFE_FOR_RECONCILIATION`, excluded entirely from both pairings.
  - Free text distinguishing "12R end" (#41) from "30L" (#222/#67) →
    `UNSAFE_FOR_RECONCILIATION`, excluded — the guard never sees this distinction,
    structurally, even though it is the one fact that would tell a human #41 is
    unrelated.
- **Result under the corrected rule**: with zero identity anchors for either
  candidate, the outcome is `CLEAR_TO_CREATE`, carrying non-blocking advisory
  metadata for both: `advisory_candidate_signal_ids=(41, 67)`,
  `advisory_reasons=("signal 67: same category (replacement)", "signal 67:
  named-vendor overlap: Runway Safe (requested on new evidence, confirmed on
  candidate)", "signal 67: candidate evidence postdates new evidence, consistent with
  a later project stage", "signal 41: same category (replacement)", "signal 41:
  weak temporal compatibility (planning_year=2025)")`. **The guard, as safely
  corrected, would not have blocked #222's Signal creation.** It would have shown a
  reviewer two advisory candidates, #67 (compatible on three independent axes) and
  #41 (compatible on essentially one, and in real life a wrong candidate — see
  Section 9), leaving the actual identification of "this is the same project as #67,
  not #41" to human judgment, exactly as happened in reality. This is the honest
  distinction Task 5 asks for: **MSP is human-obviously the same project (#222/#67);
  current structured data cannot prove enough identity for the guard to gate on it.**
  That is the correct, safe answer — not a design defect to be engineered away by
  loosening the anchor requirement (Section 4's own instruction).

**A real coincidence worth naming explicitly** (re-verified fresh at this checkpoint
via `ReviewerAction #1`'s own recorded reason text and Signal #67's `source_notes`):
Signal #67's own `source_notes` mentions "mUSD 19" — Runway Safe's own aggregate
multi-customer order intake for Jan-Apr 2024, an unrelated comparison figure in a
shareholder letter — which happens to equal the same numeral as #222's own
$19,000,000 CIP project ceiling (independently confirmed by the human reviewer's own
words in `ReviewerAction #1.reason`: "the $19,000,000.00 figure is a 2023-12-18 CIP
listing ceiling, not a confirmed contract value, vendor award amount, or vendor
revenue"). These are two completely unrelated financial facts (aggregate
multi-customer order intake vs. a single airport's capital-project ceiling) that
happen to share a numeral. This is a real, already-existing instance of adversarial
case G/E (Section 9), not a hypothetical — concrete evidence that financial-amount
matching must never be used as identity evidence, even when it looks like it lines
up, and this checkpoint's taxonomy (Section 6) accordingly excludes financial amounts
from advisory metadata entirely, not merely from the blocking anchor tier.

**Post-resolution state** (what the guard sees now, and will see on any future call
for #222): `source_assertion.signal_id == 67` is already set. The guard's first check
(Section 5) short-circuits before any candidate discovery or evidence classification:
`ALREADY_LINKED(signal_id=67)`. Idempotent on every subsequent call.

## 9. Negative / adversarial cases

**Rewritten at the review checkpoint (Section 18)** against the corrected,
anchor-requiring rule. Each case assumes the guard is evaluating new evidence against
a candidate Signal that shares the trait named, and must NOT reach
`POSSIBLE_EXISTING_SIGNAL_MATCH` (compatibility-only cases may still surface as
non-blocking advisory metadata on `CLEAR_TO_CREATE` — noted per case).

- **A — same airport + category + vendor, different runway/project.** New evidence:
  an EMAS install on Runway 4/22 at MSP naming Runway Safe. Candidate: Signal #67
  (Runway Safe, MSP, 12R/30L-era project). Under the *original* "2+ categories" rule,
  category(`SUPPORTING`) + vendor(`SUPPORTING`) = 2 → **false-positive block**. Under
  the corrected rule: neither axis is an anchor, so this can never gate creation
  regardless of how many compatibility axes agree → `CLEAR_TO_CREATE`, with
  `advisory_candidate_signal_ids=(67,)` for a human's awareness only. If both sides
  happen to carry a populated, differing canonical `runway_id`, that is a
  *disconfirming* anchor-tier signal that should suppress even the advisory note
  (Section 6).
- **B — same airport + vendor + temporally compatible, unrelated projects.** New
  evidence: a second, unrelated Runway Safe project at the same airport, evidenced a
  year after an existing Runway Safe Signal there. Vendor(`SUPPORTING`) +
  temporal(`SUPPORTING`) = 2 under the original rule → **false-positive block**.
  Corrected: no anchor → `CLEAR_TO_CREATE` with advisory metadata only.
- **C — same airport + category + same year, two independent runway-end
  replacements.** Category(`SUPPORTING`) + year-as-temporal-proxy(`SUPPORTING`) = 2
  under the original rule → **false-positive block**. Corrected: no anchor →
  `CLEAR_TO_CREATE`, advisory only.
- **D — real, live instance: MSP Signal #41 vs. SourceAssertion #222.** Not
  hypothetical — discovered during this checkpoint's fresh candidate-discovery
  inspection (Section 8). Signal #41 (`airport_id=45, category='replacement',
  confidence='high', source_id=15`) is a USAspending FY2025 grant whose own
  `source_notes` states it reconstructs the EMAS "AT THE RUNWAY 12R END" of the same
  12R/30L pair that SourceAssertion #222 concerns at the 30L end. Category overlap is
  real (`SUPPORTING_EVIDENCE`); the fact that distinguishes them — which runway end —
  exists *only* in raw free text on both sides (`UNSAFE_FOR_RECONCILIATION`, Section
  6), so the guard cannot see it either way. Corrected rule: no anchor for either
  side → `CLEAR_TO_CREATE`, with #41 appearing in advisory metadata alongside the
  correct candidate #67 — a real demonstration that the guard's advisory list can and
  will sometimes include an actually-unrelated Signal, which is exactly why
  compatibility evidence must never be strong enough to block by itself: a
  false-positive *block* on #41 would have been a genuine governance defect; a
  false-positive *advisory mention* of #41 costs a reviewer one glance and is
  recoverable.
- **E — large airport with multiple Runway Safe EMAS installations undergoing work
  in overlapping years.** Generalizes A/B/D: at a busy airport, vendor and category
  will *routinely* co-occur across genuinely distinct projects, and years will
  routinely overlap on a multi-year capital plan. Under the original rule this would
  have produced a chronic false-positive block on nearly every new Signal attempt at
  such an airport once two or three Runway Safe Signals already existed there — the
  single strongest argument in this review for rejecting the "2+ categories" rule
  outright, not merely patching it upward to "3+" or "4+" (any fixed compatibility-only
  count is beatable by a large-enough airport's own evidence volume; only requiring a
  structurally different *kind* of evidence, an anchor, is not).
- **F — financial amounts match but roles differ.** New evidence: a $19M claim with
  `semantic_role='project_ceiling'`. Candidate: a Signal whose
  `estimated_total_value_usd` happens to be $19M for an unrelated project.
  `UNSAFE_FOR_RECONCILIATION` — excluded entirely, contributes to neither an anchor
  nor advisory metadata, regardless of exact numeric match. (This is the live MSP
  $19M coincidence itself, Section 8.)
- **G — earlier planning evidence with no structural identity tie.** New evidence: a
  planning-stage claim with no named vendor, no shared source, no temporal marker
  beyond a bare year. Only `airport_id` matches any existing Signal. No anchor, no
  compatibility axis reaches `SUPPORTING_EVIDENCE` → `CLEAR_TO_CREATE`, no advisory
  metadata at all. (Very early planning evidence legitimately often has nothing yet
  to reconcile against; forcing even an advisory note here would produce constant
  noise for reviewers.)
- **H — SFO-style ambiguous financial evidence ($40M-style) must never reconcile
  merely on amount resemblance.** Same rule as case F, generalized: an SFO capital
  planning figure in the tens of millions must never be treated as identity or
  advisory evidence against any other Signal's dollar fields, regardless of proximity
  in value. This is the same discipline already enforced upstream by the identity
  guard and intelligence review for *within-assertion* financial-role handling; this
  design extends it to the *cross-Signal* axis explicitly so a future implementer
  doesn't quietly reintroduce amount-matching as a shortcut once anchor evidence
  proves sparse (which, per Section 10, it usually will).
- **I — international / non-USD case follows identical logic.** New evidence: a
  non-US airport, EMAS claim with amounts in EUR or local currency. No axis in
  Section 6 is currency- or country-specific; `UNSAFE_FOR_RECONCILIATION` for amounts
  applies regardless of currency, and `airport_id`-scoped candidate discovery works
  identically for any airport in the `airports` table. No special-casing is
  introduced, and none should be — a currency- or country-specific carve-out would
  itself violate the "no source-specific logic in the pure core" invariant
  (Section 15).
- **J — incomplete runway identity on only one side.** New evidence has a populated
  canonical `runway_id`; the candidate Signal's `runway_id` is null (the common case —
  Signal #67 and #41 both illustrate it). This is neither a match nor a mismatch — it
  is `NOT_CURRENTLY_REPRESENTABLE` for that pairing (Section 6), never treated as a
  weak positive ("well, it's not contradicted") and never treated as disconfirming.
  Only a populated-and-differing pair on *both* sides is disconfirming.
- **K — conflicting provenance.** New evidence's `source_id`/`artifact_identity`
  differs from every `supporting_source_assertion` already linked to a candidate, but
  the candidate's own `title`/`category` otherwise look highly compatible. Differing
  provenance is not itself disconfirming (most genuinely-the-same-project evidence
  legitimately comes from different documents over time — that's the entire MSP
  lifecycle-stage story, Section 7) — it simply means the provenance axis stays
  silent rather than contributing an anchor.

## 10. Signal-model fitness audit

**Reclassified at the review checkpoint (Section 18)** using the three-tier scheme
the checkpoint requested — `NON_BLOCKING` / `BLOCKER_FOR_MATCH_DETECTION` /
`BLOCKER_FOR_AUTOMATIC_RECONCILIATION` — in place of the original document's looser
`SUFFICIENT`/`LIMITED_BUT_USABLE`/`BLOCKER_FOR_SAFE_AUTOMATION` labels.
`BLOCKER_FOR_AUTOMATIC_RECONCILIATION` is marked N/A throughout: this guard never
auto-merges (Invariant 9), so nothing in this design ever needs to clear that bar: it
describes a hypothetical future automation this document explicitly does not
propose. This section documents gaps; it does not propose schema changes (out of
scope per Section 17).

| Field / mechanism | Classification | Why |
|---|---|---|
| `airport_id` | `NON_BLOCKING` | Required, reliable, exactly what candidate discovery should scope on. |
| `runway_id` | `BLOCKER_FOR_MATCH_DETECTION` in practice | Exists and is a real anchor when populated on both sides, but is frequently `NULL` in the actual corpus — confirmed null for both #67 and #41, the only two candidate Signals at MSP (Section 8) — so it cannot detect the golden case or its adversarial near-miss (#41) today. `BLOCKER_FOR_AUTOMATIC_RECONCILIATION`: N/A (no auto-merge is proposed). |
| Transitive link to `PhysicalInstallationIdentity` via `SourceAssertion.installation_assertion_links` (Section 6) | `BLOCKER_FOR_MATCH_DETECTION` in practice, `NON_BLOCKING` structurally | **Corrected at this checkpoint**: the original document classified this a hard `BLOCKER_FOR_SAFE_AUTOMATION`, implying a schema change was needed. It is not — the anchor is derivable today via a join, with zero new columns, whenever *both* the new SourceAssertion and one of the candidate's `supporting_source_assertions` have already been through governed installation reconciliation (Slice 6C). The practical blocker is data coverage, not schema: confirmed at this checkpoint that #222 has zero `InstallationAssertionLink` rows and #67 had zero `supporting_source_assertions` pre-resolution, so this anchor is unavailable for the golden case specifically, and will remain sparse for any Signal created outside the governed pipeline (most of the current corpus, including both #41 and #67). `BLOCKER_FOR_AUTOMATIC_RECONCILIATION`: N/A. |
| `source_id` (single document) / `supporting_source_assertions` (Slice 9C) | `BLOCKER_FOR_MATCH_DETECTION` for legacy Signals; `NON_BLOCKING` for Signals created through the governed pipeline going forward | Provenance-overlap anchor evidence (Section 6) needs `supporting_source_assertions` populated on the candidate; it only populates through `create_signal_from_approved_review()` and is empty for every Signal created outside that path — both #67 and #41 predate it and have zero (#67) or are themselves the sole non-governed record (#41). |
| `category` | `NON_BLOCKING` | Coarse (a handful of values), shared across many distinct projects per airport (Section 9 case E) — correctly scoped in this design as `[COMPATIBILITY]`-only, never an anchor, so its coarseness cannot cause an unsafe block. It is not a blocker for the guard's *safety*; it would be a blocker for any future attempt to use it as an anchor, which this design does not do. |
| `confidence` | `NON_BLOCKING` | Describes evidence strength for the Signal itself, not project identity; correctly excluded from the taxonomy (Section 6) entirely rather than misused. |
| No lifecycle-stage field | `BLOCKER_FOR_MATCH_DETECTION` for *automated* stage reasoning only | There is no typed vocabulary for "requested / planned / confirmed / under-construction / complete." The guard as designed here works around this by using temporal chronology as a weak, `[COMPATIBILITY]`-only proxy (Section 7), never an anchor, so this gap cannot cause an unsafe block — it can only ever mean a real duplicate goes undetected as an anchor and surfaces, at best, as an advisory note. `BLOCKER_FOR_AUTOMATIC_RECONCILIATION`: N/A. |
| `confirmed_vendor` / `likely_supplier` | `NON_BLOCKING` for the guard's safety; `BLOCKER_FOR_MATCH_DETECTION` for ever treating vendor as more than advisory | Free-text vendor name, no canonical vendor-identity table (no equivalent of `PhysicalInstallationIdentity` for vendors) — exact-string matching only, `[COMPATIBILITY]`-tier at best, by design never an anchor regardless of match confidence. |
| `estimated_total_value_usd` / `estimated_emas_value_usd` | `NON_BLOCKING` for the guard's safety (excluded entirely, Section 6); would be `BLOCKER_FOR_MATCH_DETECTION` for any future design that tried to use amounts positively — explicitly not this one. |
| `target_year` / `planning_year` / `procurement_year` / `construction_start` / `completion_date` | `NON_BLOCKING` | Individually `INSUFFICIENT_ALONE`/`[COMPATIBILITY]`; frequently null (all null on #67, only `planning_year` set on #41); can contribute to temporal-compatibility advisory reasoning (Section 6) when populated, never to a block. |

**Overall verdict for Task 9's question** ("does safe reconciliation require new
structured identity before R1/R2/R3 can safely deliver the intended behavior?"): **no.**
Because the corrected rule fails toward `CLEAR_TO_CREATE` (with advisory metadata)
whenever no anchor is found, sparse anchor data makes the guard *less useful* at
catching real duplicates like MSP — never *unsafe*. R1-R3 can ship now without any
schema prerequisite. What sparse anchor data does mean is that the guard's practical
hit rate on genuine duplicates will be low until more evidence flows through the
governed pipeline (Section 16) — a real, honest limitation to state up front, not a
safety gap to paper over.

## 11. Candidate-discovery boundary

`find_reconciliation_candidates(session, source_assertion) ->
list[ReconciliationCandidateSignal]` is the only piece of this design that touches
the database. Its responsibilities and limits:

- **Scope**: filter `Signal` rows to `airport_id == source_assertion.airport_id` at
  minimum. This is a hard bound — candidate discovery must never scan across
  airports (Invariant added in Section 15). Optionally narrow further by `runway_id`
  when both the new evidence and a candidate have one populated, but never use
  `runway_id IS NULL` as an exclusion (both #222 and #67 have `runway_id=None`, and
  excluding null-runway Signals would have hidden the golden case entirely).
- **What it returns**: plain snapshots (`ReconciliationCandidateSignal` dataclass),
  not live ORM `Signal` objects — the pure core must never hold a session-bound
  object. Snapshots include everything the taxonomy in Section 6 needs:
  `id, airport_id, runway_id, category, confidence, confirmed_vendor,
  likely_supplier, source_id, created_at`, plus a list of
  `supporting_source_assertion` snapshots (`id, source_id, artifact_identity,
  airport_id, installation_link_physical_installation_ids: tuple[int, ...]`) for the
  provenance-overlap axis and, per the checkpoint's Section 6/10 correction, the
  transitive physical-installation anchor axis — the adapter must also join each
  `supporting_source_assertion` against its own `InstallationAssertionLink` rows
  (`outcome == 'SAME_PHYSICAL_INSTALLATION'`) so the pure core can compare them
  against the new SourceAssertion's own links without doing any DB access itself.
  Likewise the new-evidence snapshot must carry the new SourceAssertion's own
  `installation_link_physical_installation_ids`.
- **What it must never do**: rank, score, or drop a candidate for any reason other
  than "structurally cannot be the same project" (different airport is the only such
  reason established here). SQL `WHERE` clauses that narrow by airport are search
  optimization; a SQL clause that tried to filter by "vendor LIKE" or "title similar
  to" would be smuggling the forbidden fuzzy-matching logic into the adapter layer
  instead of the core, which the Core Safety Principle forbids regardless of which
  layer it lives in.
- **Result size**: expected to be small in practice (a handful of Signals per
  airport at current data volumes); no pagination/limit design is needed at this
  scale, but the adapter should not assume it stays small forever — a future slice
  may need to bound it explicitly if an airport accumulates many Signals.

## 12. Governed-creation integration (not implemented by this task)

Insertion point: inside `create_signal_from_approved_review()`, after all five
existing governance gates (identity/intelligence/promotion/airport_id/latest-
ReviewerAction==APPROVE_SIGNAL) and after human-selected-field validation, but
**before** the `source_assertion.signal_id is not None` branch is reached.

```
decision = evaluate_existing_signal_reconciliation(
    source_assertion_snapshot, claims,
    find_reconciliation_candidates(session, source_assertion),
    context,
)
if decision.outcome == "ALREADY_LINKED":
    # existing signal_id-is-set branch handles this exactly as today; the guard's
    # ALREADY_LINKED here is redundant with that check by construction and is not
    # a new behavior — see Section 5.
    ...
elif decision.outcome == "POSSIBLE_EXISTING_SIGNAL_MATCH":
    raise ReconciliationRequiredError(decision)  # fails closed; no Signal created
else:  # CLEAR_TO_CREATE — decision.advisory_candidate_signal_ids may be non-empty;
       # logged/returned to the caller for review-queue display (Section 13), but
       # never inspected as a gating condition here.
    ...
```

- `POSSIBLE_EXISTING_SIGNAL_MATCH` must fail closed: no Signal is created, the call
  raises (mirroring the existing `raise ValueError` fail-closed style already used
  throughout this function for governance-gate failures), and `decision.
  candidate_signal_ids`/`decision.reasons` are surfaced to the caller so the human
  review surface (Section 13) can display them. **Corrected at this checkpoint**: this
  outcome is only reachable when at least one candidate carries a `STRONG_IDENTITY_
  EVIDENCE` anchor (Section 5/6) — the golden MSP case itself does *not* reach this
  branch pre-resolution (Section 8), so this branch remains, honestly, rarely
  exercised against the current real corpus. That is an accepted, correct consequence
  of the safety fix, not a sign the branch is unused/dead code.
- `ALREADY_LINKED` preserves the current idempotent reuse-or-raise-on-drift behavior
  unchanged — the guard does not replace that logic, it simply agrees with it before
  reaching it.
- `CLEAR_TO_CREATE` alone permits creation to proceed, still subject to every
  existing governance gate — the guard adds a gate, it doesn't relax any existing one.
  Its `advisory_candidate_signal_ids`/`advisory_reasons` (Section 5) travel with the
  result purely for display; `create_signal_from_approved_review()` must not branch on
  their presence or absence in any way — an implementation that quietly started
  blocking on "advisory candidates present" would silently reintroduce the compatibility-
  only threshold this checkpoint removed.

## 13. Human-review consequences

- **Is the `ReviewerAction` vocabulary sufficient?** Yes — `MARK_DUPLICATE` already
  exists and is exactly the right action for a human to take once shown a
  `POSSIBLE_EXISTING_SIGNAL_MATCH` flag with one candidate. Per Task 11's own
  instruction, the vocabulary should not be expanded unless genuinely necessary, and
  it is not necessary for the core flag/resolve loop.
- **What about "none of these candidates are actually the same project"?**
  **The corrected rule (Section 18) substantially reduces the urgency of this
  question.** Under the original "2+ categories" rule, a false-positive
  `POSSIBLE_EXISTING_SIGNAL_MATCH` would have hard-blocked creation, and a human
  needed *some* way to say "I checked, these are unrelated, proceed anyway" — a real
  gap the original document correctly flagged. Under the corrected rule, the common
  case of compatibility-only overlap (including the golden MSP case itself and the
  real #41 near-miss, Sections 8-9) never blocks at all — it surfaces as non-blocking
  advisory metadata, and a human who judges the advisory candidates irrelevant simply
  proceeds with `APPROVE_SIGNAL` exactly as today, no new action needed. The gap
  narrows to the genuinely rare case of a real anchor-backed
  `POSSIBLE_EXISTING_SIGNAL_MATCH` false positive (e.g., a mis-recorded
  `InstallationAssertionLink`) — still worth having an explicit answer for
  eventually, still deferred to R4, but no longer a gap in the guard's common-case
  safety story.
- **Does the review queue need a reconciliation-specific state/view?** Likely yes,
  eventually (R4) — a reviewer benefits from seeing `advisory_candidate_signal_ids`/
  `candidate_signal_ids` and `reasons` directly in the queue rather than discovering
  the overlap by memory or manual search, as happened for #222. This document does
  not design that view in detail (deferred, Section 17) beyond noting that
  `human_review_queue.py`'s existing `ReviewWorkflowState` pattern (Slice 9D) is the
  natural place to add a reconciliation-pending state once R1-R3 exist, and that a
  future queue view should visually distinguish blocking candidates from advisory-only
  ones rather than presenting both the same way.
- **Does `MARK_DUPLICATE` remain the resolution action?** Yes, unchanged — this
  design adds detection upstream of it, not a new resolution mechanism.

## 14. Explainability model

**Extended at the review checkpoint (Section 18)** to carry the new non-blocking
advisory fields alongside the original blocking fields — both are populated by the
same evaluation call, distinguished by which the outcome branch actually uses:

```python
@dataclass(frozen=True)
class ExistingSignalReconciliationDecision:
    outcome: Literal["CLEAR_TO_CREATE", "POSSIBLE_EXISTING_SIGNAL_MATCH", "ALREADY_LINKED"]
    candidate_signal_ids: tuple[int, ...] = ()   # populated only for POSSIBLE_EXISTING_SIGNAL_MATCH; anchor-backed
    signal_id: Optional[int] = None              # set only for ALREADY_LINKED
    reasons: tuple[str, ...] = ()                # anchor reasons for candidate_signal_ids
    advisory_candidate_signal_ids: tuple[int, ...] = ()  # populated only for CLEAR_TO_CREATE; compatibility-only, non-blocking
    advisory_reasons: tuple[str, ...] = ()               # compatibility reasons for advisory_candidate_signal_ids
```

`reasons` and `advisory_reasons` are short, structural, human-readable strings naming
*which* Section 6 axis agreed and for *which* candidate, and — critically post-
checkpoint — which tier it belongs to: `reasons` may only ever name `[ANCHOR]` axes
(e.g. `"signal 45: same physical installation identity (physical_installation_id=12)"`,
`"signal 45: same source document already linked"`); `advisory_reasons` may only ever
name `[COMPATIBILITY]` axes (e.g. `"signal 67: same category (replacement)"`,
`"signal 67: named-vendor overlap (Runway Safe)"`, `"signal 67: candidate evidence
postdates new evidence"`, `"signal 41: same category (replacement)"`) — never a
numeric score, never "confidence: 0.82," never a similarity percentage, and never a
compatibility reason appearing inside `reasons` or an anchor reason appearing inside
`advisory_reasons` (that mixing is exactly what made the original rule unsafe). This
mirrors the existing, shipped style of `identity_guard_reason`/
`intelligence_review_reason`/`promotion_policy_reason` (all plain sentences naming
which categories/roles were involved), so the guard's output reads consistently with
everything already surfaced to reviewers today.

## 15. Safety invariants

The 14 given, verbatim in spirit (wording tightened at the review checkpoint where
the corrected rule changes what "proves identity" means — see Section 18):

1. Reconciliation never rewrites evidence semantics — a SourceAssertion's own claims
   are never edited as a result of a reconciliation decision.
2. Reconciliation never promotes a requested relationship into a confirmed one
   (Section 7) merely because a later Signal confirms it.
3. Financial amount equality never proves project identity, and never contributes to
   advisory metadata either (Sections 6, 8, 9-F/H).
4. Same airport alone never proves identity (`INSUFFICIENT_ALONE`, Section 6).
5. Same vendor alone never proves identity, and combined with any number of other
   `[COMPATIBILITY]`-tier axes still never proves identity — vendor is never
   `[ANCHOR]`-tier under any combination (`SUPPORTING_EVIDENCE` only, Section 6;
   strengthened at the checkpoint — see Invariant 21).
6. Similar title/text alone never proves identity, and no raw free text of any kind
   (`raw_relevant_text`, `raw_runway_value`, `source_notes`, etc.) may enter the
   decision core at all (`UNSAFE_FOR_RECONCILIATION`, Section 6; see the real #41/#222
   "12R end" case, Section 9-D, where this is the only fact that would have
   disambiguated two real Signals, and the guard correctly cannot see it).
7. Multiple plausible matches are never auto-selected between — all are returned
   together and the choice is left to a human (Section 5).
8. `POSSIBLE_EXISTING_SIGNAL_MATCH` blocks Signal creation; there is no path to
   creating a Signal while a possible match is unresolved (Section 12). **Clarified
   at the checkpoint**: this outcome — and therefore this block — may only ever be
   reached via `[ANCHOR]`-tier evidence (Invariant 21); it is not reachable through
   `[COMPATIBILITY]`-tier evidence at any volume.
9. Reconciliation never publishes a Signal — it has no interaction with
   `Signal.published` at all.
10. Reconciliation never deletes historical evidence — it is a read-only decision
    over existing rows plus one new, append-only human resolution
    (`MARK_DUPLICATE`/`ReviewerAction`), exactly as already true today.
11. Human duplicate resolution remains append-only/auditable — unchanged; the guard
    adds a pre-check, it does not touch the existing `ReviewerAction` immutability
    machinery.
12. `ALREADY_LINKED` is idempotent (Section 5).
13. Source-specific logic must not enter the pure core — no MAC-specific,
    Granicus-specific, or MSP-specific branching anywhere in
    `evaluate_existing_signal_reconciliation`; all source-format handling stays in
    extraction, upstream of claims (Section 9-I).
14. International evidence follows the same rules — no currency- or country-specific
    carve-outs (Section 9-I).

Additional invariants identified during this design:

15. The pure core never queries the database, opens a session, or imports SQLAlchemy
    — enforceable the same way as prior pure cores in this codebase (AST-based
    purity check, per this session's established verification pattern).
16. Candidate discovery may narrow the universe but never itself constitutes a
    decision — any filtering beyond "different airport" must be independently
    re-derivable as a Section 6 classification inside the pure core, never assumed
    from SQL alone (Section 11).
17. A Signal's `supporting_source_assertions` are read-only inputs to reconciliation;
    the guard never adds, removes, or reorders them.
18. The guard runs only on the path to *creating* a new Signal; it is never consulted
    for, and never gates, `REJECT_SIGNAL`, `DEFER`, or `NEEDS_MORE_EVIDENCE` — those
    actions don't create a Signal, so there's nothing to reconcile against yet.
19. Candidate discovery is always airport-scoped and bounded; it must never become a
    full-table scan across all Signals for all airports (Section 11).
20. No confidence score, weighted sum, or fuzzy/text-similarity metric may appear
    anywhere in the decision core's logic or output.

Invariants added at the review checkpoint (Section 18):

21. **Compatibility evidence alone can never establish a reconciliation candidate
    without at least one identity-bearing anchor.** `[COMPATIBILITY]`-tier axes
    (category, vendor, temporal proximity, year, aggregate provenance overlap) may
    combine in any number and any pairing and must still never, by themselves, produce
    `POSSIBLE_EXISTING_SIGNAL_MATCH`. This is the central correction of this
    checkpoint (Section 18) and the direct answer to Task 3/14's request for this
    exact invariant: counting how many weak signals agree is not a substitute for one
    strong one, and no future change to this design may reintroduce a
    compatibility-only numeric or categorical threshold as a way to trigger a block.
22. A single anchor for one candidate never implies anything about a second,
    unrelated candidate in the same evaluation — anchor evidence is assessed
    per-candidate, never pooled or averaged across the candidate set (relevant once a
    real airport has 3+ Signals, not only the MSP two-candidate case, Section 9-E).
23. Disconfirming evidence (e.g., a populated, differing canonical `runway_id` on both
    sides) may suppress an advisory note but must never itself promote a
    `[COMPATIBILITY]` axis to `[ANCHOR]` status by "process of elimination" — ruling
    other candidates out is not the same operation as ruling one candidate in
    (Section 6, Section 9-A).

## 16. Recommended implementation roadmap

Suggested shape, adjusted from the task's own R1-R5 sketch after working through
Sections 2-14, and **re-adjusted at the review checkpoint (Section 18)** now that the
rule requires anchors: no schema-change prerequisite is inserted (Section 10's
verdict is that none is required for *safety*), but R1's own test fixtures must now
explicitly include the no-anchor case, since that is the common case, not an edge
case.

- **R1 — Pure decision core.** `evaluate_existing_signal_reconciliation()` +
  `ExistingSignalReconciliationDecision` + `ReconciliationCandidateSignal` snapshot
  dataclass, fully unit-tested against synthetic candidate lists including: the real
  MSP #222/#67/#41 shapes reconstructed as fixtures (not live DB rows) confirming the
  corrected `CLEAR_TO_CREATE`-with-advisory result (Section 8); every adversarial case
  in Section 9, including the two genuine false-positive traps (A/B/C/E) the original
  rule would have failed; and at least one synthetic anchor-bearing case (matching
  `runway_id`, or matching transitive `PhysicalInstallationIdentity` via
  `InstallationAssertionLink`, or shared `supporting_source_assertion` provenance) to
  prove `POSSIBLE_EXISTING_SIGNAL_MATCH` is reachable at all, since no real Signal in
  the current corpus exercises it. No DB, no integration. This can and should be built
  and reviewed in complete isolation, exactly like `evaluate_signal_candidate` was.
- **R2 — Candidate-discovery adapter.** `find_reconciliation_candidates()`, tested
  against a test DB fixture set, verifying it is airport-scoped, never
  runway-null-excluding, joins through `InstallationAssertionLink` on both the new
  assertion and each candidate's `supporting_source_assertions` (Section 11), and
  returns snapshots (not ORM objects) matching the R1 dataclass contract.
- **R3 — Governed-creation integration.** Wire R1+R2 into
  `create_signal_from_approved_review()` per Section 12, with a real, deliberate
  regression-test pass proving: (a) `CLEAR_TO_CREATE` cases behave exactly as today,
  including a case with non-empty advisory metadata that still does not block; (b) a
  synthetic anchor-bearing `POSSIBLE_EXISTING_SIGNAL_MATCH` case now fails closed
  where it previously would have created a duplicate Signal; (c) `ALREADY_LINKED` is
  unchanged; (d) re-running the actual historical #222 approval flow as a regression
  fixture produces `CLEAR_TO_CREATE` with advisory metadata naming both #67 and #41,
  not a block — proving the corrected rule against the one real case this design was
  built around. This is the first slice with any behavioral effect on production
  callers — everything in R1/R2 is additive and inert until this slice.
- **R4 — Human reconciliation workflow/queue presentation.** Surface
  `advisory_candidate_signal_ids`/`candidate_signal_ids`/`reasons` in the review queue
  (Section 13), visually distinguishing advisory-only from blocking; decide whether a
  no-match confirmation needs a new mechanism for the (now rarer) anchor-backed block
  case, or can remain procedural. Deliberately placed after R3 (not before, as a
  literal reading of the task's own sketch might suggest) — R3 already produces
  explainable reasons usable from a script/log even before any queue UI exists, so R3
  delivers the actual safety property (no accidental duplicate Signals, no
  false-positive blocks) first, and R4 is a pure usability improvement layered on top
  once that property already holds.
- **R5 — Real-data pilot beyond MSP.** Run R1-R3 read-only (dry-run mode, no
  creation blocked) across the existing airport/Signal corpus to see how often
  `POSSIBLE_EXISTING_SIGNAL_MATCH` fires at all (expected: rarely, per Section 10's
  data-sparsity finding) and how often `CLEAR_TO_CREATE`'s advisory metadata fires
  (expected: more often, and worth measuring for reviewer-fatigue risk before wiring
  it into a queue UI) — mirroring how earlier slices in this session (e.g., promotion
  policy) were dry-run-validated against real data before being wired into a blocking
  gate.
- **Optional, non-blocking future enhancement (not R0, not a prerequisite):**
  backfilling `InstallationAssertionLink` coverage for legacy Signals (like #41 and
  #67) so the transitive physical-installation anchor (Section 6, Section 10) becomes
  usable against the existing corpus, not only future governed-pipeline evidence. This
  would increase R1-R3's practical hit rate on real duplicates; it is explicitly not
  required for R1-R3 to ship safely (Section 10's verdict), so it is not inserted into
  the numbered sequence above.

## 17. Explicit non-goals / deferred gaps

- No schema changes are proposed or made by this document (Section 10's gaps are
  named, not solved — in particular, no direct `Signal`-to-`PhysicalInstallationIdentity`
  FK, and no lifecycle-stage field; the transitive `InstallationAssertionLink`-based
  anchor path identified at the checkpoint, Section 6/10, needs no schema change but
  is not backfilled by this document either).
- No `ReviewerAction` vocabulary expansion (Section 13) — deferred pending real usage
  data from R4/R5 on whether "no match confirmed" genuinely needs a persisted action
  for the anchor-backed block case.
- No automated re-scoring, weighting, or any numeric confidence metric anywhere in
  this design (Invariant 20), and — added at the checkpoint — no categorical
  compatibility-count threshold either (Invariant 21); both are the same class of
  mistake.
- No LLM-based or fuzzy/embedding-based similarity matching, for titles, vendor
  names, or anything else — the taxonomy in Section 6 is exhaustively enumerated and
  closed; nothing "similar enough" is ever evidence.
- No cross-airport candidate search (Invariant 19) — an airport boundary is treated
  as an absolute bound on the candidate universe, not a heuristic.
- No automatic lifecycle-stage inference or canonical stage vocabulary (Section 7) —
  temporal chronology is used only as weak, `[COMPATIBILITY]`-tier advisory evidence,
  never as a typed stage transition, and never as an anchor.
- No review-queue UI/view design beyond the pointer in Section 13 — deferred to R4.
- No change to `link_source_assertion_to_duplicate_signal()` or
  `create_signal_from_approved_review()`'s existing idempotency/drift logic — this
  design adds a gate, it does not touch either function's existing internals beyond
  the single insertion point in Section 12.
- No retroactive backfill of `InstallationAssertionLink` coverage for legacy Signals
  (Section 16's optional future enhancement) — noted as valuable, not undertaken here.
- This document does not implement, test, or run any of R1-R5 — it is a
  specification for future implementation slices only.

## 18. Review checkpoint corrections (2026-08-19, same HEAD)

This checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_GUARD_DESIGN_REVIEW_COMMIT_PUSH)
critically re-examined the original design and found one material defect, corrected
it, and made one positive refinement. Both are recorded here so the reasoning is
auditable rather than silently overwritten.

**Defect found — the "2+ independent supporting categories" rule was unsafe.**
The original Section 5 flagged `POSSIBLE_EXISTING_SIGNAL_MATCH` whenever two or more
`SUPPORTING_EVIDENCE`-tier categories (category, vendor, temporal compatibility, etc.)
co-occurred for a candidate. Constructing adversarial cases (Section 9, cases A/B/C/E)
showed this rule blocks Signal creation for genuinely unrelated projects whenever any
two weak, common signals happen to coincide — which, for a repeat vendor at a busy
airport, is routine rather than rare. This is a disguised score with threshold=2, in
violation of the Core Safety Principle's prohibition on scoring identity. **Fix**:
`POSSIBLE_EXISTING_SIGNAL_MATCH` now requires at least one `STRONG_IDENTITY_EVIDENCE`
**anchor** — a structurally different kind of evidence (same physical installation,
same canonical runway, same source document), never a count of compatibility signals.
Compatibility-only findings are preserved, not discarded — they now travel as
non-blocking `advisory_candidate_signal_ids`/`advisory_reasons` on `CLEAR_TO_CREATE`
(Sections 5, 14) instead of blocking creation. New Invariant 21 makes this permanent.

**Consequence for the golden case — corrected, not weakened.** Re-deriving the MSP
#222→#67 pre-resolution state under the corrected rule (Section 8) shows zero
identity anchors connect the two, so the honest result is `CLEAR_TO_CREATE` with
advisory metadata, not a block. This was not adjusted to preserve the original
document's more dramatic-looking conclusion (Section 4's own instruction: do not
weaken the rule merely to make the golden case automatically detectable). The correct
statement, per Task 5, is that MSP is human-obviously the same project while current
structured data cannot yet prove enough identity for the guard to gate on — that is a
safe, honest outcome, not a shortcoming to paper over.

**Real second candidate discovered.** Fresh candidate-discovery inspection during
this checkpoint surfaced Signal #41 (a USAspending grant for the *opposite* runway
end, 12R, of the same 12R/30L pair) as a second MSP candidate the original
walkthrough never considered. It is now used throughout Sections 8-9 as a live,
non-hypothetical instance of the "two runway ends, same pair" adversarial case, and
as concrete evidence for why the anchor requirement matters: under the original rule
it risked being flagged as a false-positive block; under the corrected rule it
correctly surfaces as advisory-only, alongside the correct candidate #67, leaving the
distinction to a human exactly as intended.

**Positive refinement — the PhysicalInstallationIdentity gap was overstated.** The
original Section 10 classified the entire physical-installation axis
`NOT_CURRENTLY_REPRESENTABLE` because `Signal` has no direct FK to
`PhysicalInstallationIdentity`. This checkpoint found a real, schema-change-free
anchor path: joining `SourceAssertion.installation_assertion_links` on both the new
assertion and a candidate's `supporting_source_assertions`. It is not usable for the
golden case (neither #222 nor #67 has the underlying links populated, confirmed by
fresh read-only inspection), but it is a genuine, buildable anchor for future cases
and should not have been written off as requiring a schema change. Sections 6, 10,
and 11 were corrected accordingly. This refinement was *not* used to weaken the
anchor requirement or to manufacture an anchor for MSP — it stands on its own as a
correction to the fitness audit.

Sections 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, and 17 were edited to reflect these
corrections. No other section required a factual or architectural change — the
Section 2 architecture findings, the Section 4 pure-core/adapter split, the Section 7
lifecycle-stage discipline, and the Section 3 problem definition were all re-verified
against fresh reads of current `main` at this checkpoint and found accurate as
originally written.

## Validation performed for this design task

- Read fresh, in full: `app/services/governed_signal_creation.py`,
  `app/models/signal.py`, `app/models/source_assertion.py`,
  `app/models/reviewer_action.py`, `app/models/physical_installation_identity.py`;
  class/enum shapes confirmed in `app/services/promotion_policy_evaluation.py`.
- Read-only inspected real Signal #67 and real SourceAssertion #222 via a read-only
  SQLite connection (`mode=ro`); no write statement was ever issued.
- Real DB hash/size/mtime confirmed identical before and after inspection:
  `sha256=71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`,
  size `1789952` bytes, mtime `1787158044.8543456`, both before and after.
- No production code, test, or schema file was modified by this task. Only this new
  document was created.

## Validation performed at the review checkpoint (2026-08-19, same HEAD)

- Freshly re-read, in full, on this checkpoint: `app/services/governed_signal_creation.py`,
  `app/services/reviewer_action_persistence.py`, `app/services/human_review_queue.py`,
  `app/models/signal.py`, `app/models/source_assertion.py`,
  `app/models/reviewer_action.py`, `app/models/physical_installation_identity.py`;
  `PromotionPolicyOutcome`/`SourceAuthorityTier`/`evaluate_promotion_policy` shapes
  re-confirmed in `app/services/promotion_policy_evaluation.py`. Every factual
  statement in the original document's Section 2 matched current `main` exactly —
  zero factual corrections were needed there.
- Read-only re-inspected real Signal #67 and real SourceAssertion #222 (full row,
  `mode=ro`), plus, newly this checkpoint: `installation_assertion_links` for
  assertion #222 (zero rows), both `reviewer_actions` rows for #222 (#1 APPROVE_SIGNAL,
  #2 MARK_DUPLICATE, full reason text), every MSP (`airport_id=45`) Signal (surfacing
  Signal #41, not previously considered), and Signal #41's full row.
- Real DB hash/size/mtime confirmed identical at the start of this checkpoint, after
  the mid-checkpoint inspection, and after the full test run:
  `sha256=71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`, size
  `1789952` bytes, mtime `1787158044.8543456` — all three captures identical to each
  other and to the original design task's own capture.
- No production code, test, schema, or migration file was modified. Only this
  document was edited.
