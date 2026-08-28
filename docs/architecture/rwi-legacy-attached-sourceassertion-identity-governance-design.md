# RWI Legacy-Attached SourceAssertion Identity Governance — Design

ARCHITECTURE/DESIGN ONLY. No code, no database writes, no migrations, no commits. GMU SourceAssertion 81 is the concrete control case throughout, but every conclusion below is derived from, and intended to apply to, the general class — confirmed by a real, read-only blast-radius query (§9) to be **216 of RWI's 231 real SourceAssertions (93.5%)**, not an isolated anomaly.

## 1. Problem statement

`resolve_effective_identity_guard_decision()` (EB5) — the single, real, unmodified authority every downstream governance step (intelligence review, promotion policy, Signal creation) consults for "what identity decision should I trust right now" — returns `INSUFFICIENT_IDENTITY` for any `SourceAssertion` whose `identity_guard_decision` column is `NULL`, **regardless of whether that assertion already has a real, correct `airport_id`.** A large, legitimate class of RWI's real evidence (FAA NASR feeds, the bulk FAA Tableau map, USAspending grants, FAA Fact Sheets, some news imports) was attached to its Airport directly by a legacy import script, before the modern identity-guard pipeline (evidence-attachment-guard → KAR → EB4/EB5) existed. These rows are not identity-ambiguous in any human sense — GMU SourceAssertion 81, the control case, has `airport_id=63` set and its own raw text explicitly says "GREENVILLE" and names Runway 1 — but they are structurally invisible to every governed downstream consumer because no machine ever ran the guard over them, and none can, for reasons established fresh in §1a below. This document designs the smallest safe mechanism for a human to close that gap, one assertion at a time, without rewriting history and without weakening any existing governed path.

### 1a. Why the four existing candidate paths cannot process SA81 (re-derived fresh, GMU as control case)

- **`identity_guard_decision=None` is insufficient on its own.** `intelligence_review_persistence.py`'s own module docstring explicitly names this exact case ("a row from a pathway that never ran the guard at all, e.g. NASR/USAspending") and documents the fail-closed default: mapped to `AttachmentOutcome.INSUFFICIENT_IDENTITY`, which `evaluate_signal_candidate()`'s identity gate (which runs *before claims are even inspected*) turns into `IDENTITY_NOT_CONFIRMED` unconditionally.
- **KAR (`SourceAssertionIdentityResolution`) cannot process it — twice over, at both the service and schema level.** The service's own precondition (`SourceAssertionAlreadyResolvedError`) refuses any assertion whose `airport_id` is already set — SA81's is (`63`). Independently, and more fundamentally, the model itself makes it *structurally impossible* regardless of that precondition: `evidence_bag_snapshot_id` is a `NOT NULL` column enforced by a composite `ForeignKeyConstraint` against `SourceAssertionEvidenceBag.(id, source_assertion_id)` — SA81 has zero rows in that table (confirmed fresh: `source_assertion_evidence_bags` currently holds only ids 223–231, none of which is 81). No row could be inserted even if the service-level precondition were somehow bypassed.
- **EB4 (`resolved_candidate_evidence_reevaluation.py`) cannot replay it** — it requires an *existing* `SourceAssertionEvidenceBag` snapshot to replay against current topology (`MissingEvidenceBagSnapshotError` otherwise); SA81 has none.
- **Live discovery cannot re-run it** — the evidence-attachment-guard only ever runs inside the live-discovery pipeline (`discovery_evidence_persistence.py`) against a freshly-extracted `CandidateFragment`. Re-running it for SA81 would mean re-fetching and re-extracting the *original* USAspending grant document today, producing a structurally *new* SourceAssertion (per this repository's own fragment-identity/dedup model), not a decision about the existing row 81 — and even then, no `usaspending_grant` claims/fragment extractor exists in this codebase (only MAC/Granicus has one).
- **EB5 confirms this empirically, read-only, via the real unmodified service** (re-run fresh in this mission): `resolve_effective_identity_guard_decision(session, source_assertion_id=81)` → `original_decision=INSUFFICIENT_IDENTITY`, `latest_evaluation_id=None`, `effective_decision=INSUFFICIENT_IDENTITY`, `basis=ORIGINAL_DECISION`, `is_identity_confirmed=False`.

## 2. The eligible legacy class

**Definition**: a `SourceAssertion` is eligible for this mechanism if and only if **all** of the following hold, checked freshly at review time (never cached, never assumed from a prior check):

1. `airport_id IS NOT NULL` (already attached to a real, existing canonical Airport — this mechanism never attaches an unresolved assertion; that remains KAR/UAC territory).
2. `unknown_airport_candidate_id IS NULL` (guaranteed by the existing DB-level mutual-exclusivity constraint, re-checked anyway — never a candidate-linked row).
3. `identity_guard_decision IS NULL` (never ran the guard at all — this is *not* for assertions with a real but unfavorable guard decision like `REJECT_CROSS_AIRPORT`; those already have a machine answer and belong to KAR/EB4, not this mechanism).
4. No `SourceAssertionEvidenceBag` snapshot exists for it (confirms it predates EB1-EB3; a row *with* a real EvidenceBag should go through EB4/KAR, the paths built for exactly that evidence shape, never this one).
5. `signal_id IS NULL` (no Signal has been created from it yet — a row that somehow already produced a Signal despite ungoverned identity is a data-integrity anomaly outside this mechanism's scope, not something to paper over).
6. The referenced Airport (`airport_id`) still exists at review time (trivially true via the FK, re-verified anyway as a defensive read).
7. `raw_relevant_text` (or another populated raw evidence field) is non-empty — a row with **no preserved text at all** cannot honestly be reviewed by a human against anything; fails closed to ineligible, never silently approved on the strength of `airport_id` alone.
8. No prior row of this mechanism's own new entity (§4) exists for this assertion with a *conflicting* outcome that hasn't been explicitly superseded (checked structurally, not inferred).

**Deliberately NOT part of eligibility, by design**: `parser_identifier`/`source_type` are **descriptive**, used only for reporting and prioritization (§9), never as a gate — restricting eligibility to a specific parser string would be exactly the "GMU exception" this mission forbids, and would silently exclude a legitimate future legacy pathway with a different parser tag. The *general* structural shape (1)–(8) above is the only gate, matching KAR1's own "no GMU-specific logic" discipline verbatim.

**Fails closed, explicitly**: any assertion where a human reviewer cannot find the target airport's identity actually supported by the assertion's own preserved raw text (name, city, IATA/ICAO, or an unambiguous topology match) is **not** eligible for `CONFIRM_EXISTING_ATTACHMENT` — the reviewer's only honest option there is `REJECT_EXISTING_ATTACHMENT` or `DEFER_IDENTITY_REVIEW` (§4). Eligibility to *use* the mechanism is not the same as eligibility to *confirm* — the mechanism itself stays permissive at the structural level; the human decision inside it is what fails closed on weak evidence.

## 3. Decision semantics — recommended: **B, a new append-only entity**

| Option | Verdict | Why |
|---|---|---|
| **A. Extend `SourceAssertionIdentityResolution` (KAR)** | **Rejected** | Structurally impossible without changing KAR's own schema: `evidence_bag_snapshot_id NOT NULL` is enforced by a composite FK, not a soft precondition. Making it nullable would weaken KAR's own causal-integrity guarantee for every *other*, unrelated row that legitimately does have a snapshot — exactly the "do not weaken the normal discovery/KAR paths" instruction this mission repeats. KAR's own vocabulary (`ATTACH_TO_EXISTING_AIRPORT`/`REJECT_ATTACHMENT`/`DEFER_IDENTITY_REVIEW`) also presumes an *unresolved* assertion (`airport_id IS NULL` at review time); reusing it for an *already-attached* row would silently overload what "ATTACH" means. |
| **C. Reuse `ReviewerAction`** | **Rejected** | `ReviewerAction` answers a structurally different, later question ("should a Signal be created from this already-identity-resolved, already-intelligence-reviewed evidence") — reusing it for identity would blur two governance questions this codebase has "repeatedly refused" to blur (KAR1's own module docstring states this design principle verbatim, listing exactly this kind of conflation as the anti-pattern). |
| **D. Something smaller** | **Considered, rejected** | A single boolean flag on `SourceAssertion` itself was considered and rejected outright: it would have no audit trail, no reviewer identity, no reason, no snapshot, and — most importantly — would require mutating the historical-fact row itself, violating the historical-fact firewall every other governance table in this pipeline (KAR1, EB4, ReviewerAction) treats as sacred. |
| **B. New entity: `SourceAssertionLegacyIdentityAttestation`** | **Recommended** | Mirrors KAR1's own shape almost exactly (append-only, immutable, `reviewer`/`reason`/`created_at`, `supersedes_attestation_id` for change-of-mind), but anchors its causal integrity to a **new, honestly-named, review-time-only snapshot** (§5) instead of `SourceAssertionEvidenceBag` — never claiming a discovery-time EvidenceBag existed when it didn't. |

**Both facts stay visible, permanently, exactly as the mission requires**: `SourceAssertion.identity_guard_decision` remains `NULL` forever for these rows — that is the permanent, honest historical fact "this legacy importer attached the assertion before modern identity governance existed," never overwritten, never backfilled with a fabricated `ATTACH_CONFIRMED`. The new entity's own row is the separate, equally permanent fact "a human later reviewed the preserved evidence, at time T, and confirmed/rejected/deferred that existing attachment." A reader (or EB5, §6) consulting both sees the true, two-part story, not one row pretending to be the other's history.

## 4. Allowed human outcomes

Smallest sufficient vocabulary — three members, deliberately mirroring KAR1's own three-member shape (not a new count invented for its own sake), but semantically distinct (KAR1's actions are about attaching an *unresolved* assertion; these are about a human reviewing an *already-attached* one):

- **`CONFIRM_EXISTING_ATTACHMENT`** — "I reviewed the preserved raw evidence against this Airport's canonical identity, myself, and I confirm the existing `airport_id` is correct." Requires `matched_airport_id` to equal the assertion's own current `airport_id` at review time (a structural consistency check, not a free choice — this mechanism never *moves* an assertion to a *different* airport; that would be a mutation of the historical attachment fact, out of scope, and dangerously close to "human can attach any assertion to any Airport," the exact bypass this design must not become).
- **`REJECT_EXISTING_ATTACHMENT`** — "I reviewed the preserved evidence and it does *not* actually support this Airport." Records the rejection as a permanent, append-only fact. **Must not** silently set `airport_id=NULL` or move the assertion anywhere — that would itself be an identity-governance action (re-triggering KAR/UAC eligibility) requiring its own, separately-authorized, separately-reviewed governed mechanism and human decision. A rejection here only ever means: "downstream consumers must continue to treat this row's effective identity as unconfirmed" (which, mechanically, is already exactly what happens today — see §6, `REJECT_EXISTING_ATTACHMENT` and "no attestation at all" have the *same* downstream effect, `INSUFFICIENT_IDENTITY`/`IDENTITY_NOT_CONFIRMED` — the value of recording it explicitly is the audit trail and the "someone already looked at this and it's wrong" signal for a human, not a different machine outcome). Any actual repair (re-attaching to the correct Airport, forming an `UnknownAirportCandidate`, etc.) is an explicitly separate, future, separately-authorized governed action, never a side effect of this one.
- **`DEFER_IDENTITY_REVIEW`** — "I looked, and I can't confidently decide yet." Legitimately repeatable over time (multiple `DEFER` rows for the same assertion are fine, mirroring KAR1's own precedent for `DEFER_IDENTITY_REVIEW`/`REJECT_ATTACHMENT`).

No `CREATE_NEW_AIRPORT`-shaped option — out of scope by construction, matching KAR1's own explicit exclusion for the identical reason (that is a UAC/candidate-formation question).

## 5. Evidence / causal integrity — the review-time snapshot

**We must not manufacture a historical EvidenceBag.** The new entity therefore never references `SourceAssertionEvidenceBag` at all — it carries its **own**, honestly-named, review-time-only snapshot, structurally modeled on `SourceAssertionEvidenceBag`'s own shape (JSON payload + SHA-256 hash + schema version, immutable via the same `before_update`/`before_delete` event-listener pattern) but never claiming discovery-time provenance:

```
SourceAssertionLegacyIdentityAttestation
  id
  source_assertion_id            FK -> source_assertions.id
  action                         CHECK IN ('CONFIRM_EXISTING_ATTACHMENT','REJECT_EXISTING_ATTACHMENT','DEFER_IDENTITY_REVIEW')
  matched_airport_id             nullable; required iff action=CONFIRM, and must equal
                                  source_assertion.airport_id at write time (checked in the service)
  reviewed_snapshot_json         TEXT, NOT NULL - see fields below
  reviewed_snapshot_hash         SHA-256 of reviewed_snapshot_json, NOT NULL
  reason                         TEXT, NOT NULL
  reviewer                       plain free-text identity, NOT NULL
  created_at
  supersedes_attestation_id      nullable, self-referential, audit-only (mirrors KAR1)
```

`reviewed_snapshot_json` captures, **at review time**, exactly:
- `source_assertion_id`, `airport_id` (the value *at that moment*)
- `source_id`, `source_type`, `parser_identifier`
- `raw_relevant_text`, `raw_product_type`, `raw_airport_identifier`, `raw_airport_name`, `raw_runway_value`, `raw_runway_end_value`, `assertion_type`, `evidence_quality`
- the target Airport's own canonical identifiers *at review time*: `iata_code`, `icao_code`, `faa_code`, `name`
- an explicit `snapshot_taken_at` timestamp

This is deliberately **not** a `SourceAssertionEvidenceBag` (different table, different name, never inserted into `source_assertion_evidence_bags`) and is never presented anywhere as "the evidence the identity guard saw at discovery time" — it is labeled, in its own model docstring and every consumer, as "what a human reviewer actually looked at, and when." `reviewed_snapshot_hash` lets a later reader detect, deterministically, whether the *live* `SourceAssertion`/`Airport` rows have since diverged from what was reviewed (staleness — see below), the same mechanism KAR's own `evidence_bag_hash` pattern already established for a structurally identical purpose.

**Staleness detection**: a consumer (or a future CLI, mirroring `resolve_source_assertion_identity.py`'s own inspect mode) can recompute the *current* live snapshot shape and compare its hash against the attestation's own `reviewed_snapshot_hash`. A mismatch — the assertion's `raw_relevant_text` changed (should never happen; these columns are otherwise write-once in practice) or, far more plausibly, the target Airport's own canonical identifiers changed since review — makes the attestation **stale**, flagged for re-review, never silently trusted. This is the identical conceptual mechanism R4C's `StaleReconciliationConfirmationError` already uses for a structurally analogous "the world changed since a human confirmed this" problem — reused as a pattern, not duplicated as code.

## 6. EB5 integration

`resolve_effective_identity_guard_decision()` gains exactly one new, additional fallback tier, inserted **below** `LATEST_REEVALUATION` (EB4) and **above** the bare `ORIGINAL_DECISION`:

```
1. If a currently-trustworthy IdentityGuardEvaluation (EB4) exists → LATEST_REEVALUATION (unchanged, highest precedence — a real replay of real original evidence against current topology always outranks a human's own judgment of preserved raw text, which is a weaker evidentiary shape by construction).
2. ELSE IF SourceAssertion.identity_guard_decision is a real, non-null value → ORIGINAL_DECISION (unchanged).
3. ELSE IF a currently-trustworthy (non-stale, non-superseded) latest
   SourceAssertionLegacyIdentityAttestation exists for this assertion:
     - action=CONFIRM_EXISTING_ATTACHMENT, snapshot not stale, matched_airport_id == current airport_id
         -> effective_decision = ATTACH_CONFIRMED, basis = LEGACY_HUMAN_ATTESTATION
     - action=REJECT_EXISTING_ATTACHMENT or DEFER_IDENTITY_REVIEW, or a stale/inconsistent CONFIRM
         -> falls through to step 4 (no different from "no attestation at all" -
            an explicit REJECT/DEFER never manufactures a positive result)
4. ELSE -> ORIGINAL_DECISION (== INSUFFICIENT_IDENTITY for this legacy class, unchanged default).
```

A new `EffectiveIdentityGuardDecisionBasis.LEGACY_HUMAN_ATTESTATION` member makes the provenance explicit and auditable — a caller can always tell "this came from a real machine re-evaluation," "this is the untouched original decision," or "this came from a later governed human attestation over evidence the guard itself never saw," never conflating the three. **Precedence rationale**: EB4 outranks this new tier because it is a real, deterministic replay of the *actual* original evidence against *current* topology — strictly stronger evidence than a human's own reading of preserved text. This tier outranks the bare original decision only because, for this specific legacy class, the bare original decision is *always* `INSUFFICIENT_IDENTITY` (no information) — the new tier adds real information (a governed human judgment) that was previously entirely absent, never *overriding* a real negative machine decision (`REJECT_CROSS_AIRPORT` rows are excluded from eligibility at intake, §2 criterion 3).

**Idempotency**: identical to KAR1's own already-proven pattern — "latest row wins," multiple `DEFER`/`REJECT` rows are legitimate and simply mean "still unresolved," and a second `CONFIRM_EXISTING_ATTACHMENT` call with unchanged inputs is a legitimate re-confirmation (a new row, not an error) exactly as KAR1 already treats repeated `DEFER_IDENTITY_REVIEW`. **Conflicting resolutions** (e.g., a `CONFIRM` followed later by a `REJECT`) are handled by recency alone — the same "current state is derived by recency, never by walking a chain" convention every append-only table in this pipeline already uses; `supersedes_attestation_id` remains audit-only metadata.

## 7. Security / misuse adversarial analysis

| Scenario | Outcome |
|---|---|
| Already-attached assertion actually points to the wrong Airport | Eligibility (§2) never checks *correctness*, only *shape* — the human reviewer is expected to catch this and record `REJECT_EXISTING_ATTACHMENT`. The mechanism cannot itself detect a wrong attachment; it only ever provides the evidence for a human to judge. |
| `airport_id` changes after attestation | Detected by the staleness hash (§5) — `reviewed_snapshot_json`'s own `airport_id` field would no longer match the live row; any consumer comparing hashes sees a stale attestation. |
| Source text does not identify the Airport at all | Fails eligibility criterion 7 (no usable raw text) or, if text exists but doesn't name/support the airport, the reviewer's only honest option is `REJECT`/`DEFER` — `CONFIRM` on unsupported text is a human error the mechanism cannot structurally prevent (same limitation KAR1 itself has for its own `ATTACH_TO_EXISTING_AIRPORT`), mitigated by requiring `reason` to be non-empty and reviewed against the persisted snapshot forever. |
| Only runway topology (no name) supports identity | Legitimately a `DEFER` or even `CONFIRM` case if topology is genuinely unambiguous (e.g., a unique runway designation) — same judgment call KAR/the evidence-attachment-guard already make; not a new risk this mechanism introduces. |
| Ambiguous airport name / conflicting IATA-ICAO | Reviewer's own judgment; `reason` must explain it; nothing here differs from any other human-reviewed governance step in this pipeline. |
| Assertion already has a modern EvidenceBag | **Excluded by eligibility criterion 4** — such a row belongs to KAR/EB4, never this mechanism. |
| Assertion already has KAR history | Cannot happen — KAR requires `airport_id IS NULL`; this class requires `airport_id IS NOT NULL`; the two are mutually exclusive by construction, not merely convention. |
| Repeated human confirmations | Legitimate, idempotent (§6) — a fresh `CONFIRM` row each time, never an error, never a duplicate-prevention special case (matches DEFER's own established precedent). |
| Confirmation followed by rejection | Legitimate — recency wins (§6); the confirmed period is still fully visible in history, never erased. |
| Rejected assertion later needs repair | **Explicitly out of scope** — any re-attachment is a separate, future, separately-authorized governed action (§4). |
| Synthetic/rehearsal data | Eligibility criteria are purely structural (§2) and would apply equally to a rehearsal row — no special-casing either way; a rehearsal Airport/assertion is exactly as reviewable as a real one, which is correct (rehearsal data is deliberately shaped to mirror real data throughout this program). |
| Assertion with no preserved raw evidence | **Excluded by eligibility criterion 7** — fails closed to ineligible. |

No scenario above resolves to a silent, ungoverned `ATTACH_CONFIRMED` — every path either requires an explicit, reasoned, auditable human decision or fails closed to "still unconfirmed," identical to today's status quo.

## 8. GMU SA81 control-case walkthrough (read-only, mental application of the design — nothing written)

- **Eligible?** Yes, cleanly, against every criterion in §2: `airport_id=63` (set), `unknown_airport_candidate_id=NULL`, `identity_guard_decision=NULL`, no EvidenceBag (confirmed empty), `signal_id=NULL`, Airport 63 exists, `raw_relevant_text` is populated and substantial, no prior attestation exists.
- **Evidence available for human review**: the assertion's own `raw_relevant_text` — "PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA... THIS PROJECT EXTENDS THE RUNWAY 1/19 SAFETY AREAS TO 600 FEET... THIS GRANT FUNDS THE SECOND PHASE, WHICH CONSISTS OF DESIGN AND CONSTRUCTION OF THE RUNWAY 1 ENGINEERED MATERIAL ARRESTING SYSTEM (EMAS)... INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR AIRPORTS ASSOCIATED WITH GREENVILLE, SOUTH CAROLINA" — explicitly names Greenville and Runway 1/19; `raw_product_type='EMAS'`.
- **Target Airport 63 identity** (re-confirmed fresh): "Greenville Downtown," IATA `GMU`, ICAO `KGMU`.
- **Decision a human could legitimately record**: `CONFIRM_EXISTING_ATTACHMENT`, `matched_airport_id=63`, reason citing the explicit "GREENVILLE, SOUTH CAROLINA" + "RUNWAY 1/19" text match — a genuinely easy, low-risk case precisely because the raw text is unambiguous (not every one of the 216 will be this clean; §9).
- **Immutable snapshot that would be stored**: `source_assertion_id=81`, `airport_id=63`, `source_id=18`, `source_type='usaspending_grant'`, `parser_identifier='legacy-source-backfill-v1'`, the full `raw_relevant_text` above, `raw_product_type='EMAS'`, `assertion_type='project_construction'`, `evidence_quality='direct_strong'`, Airport 63's own `{iata_code: GMU, icao_code: KGMU, faa_code: GMU, name: "Greenville Downtown"}`, and a `snapshot_taken_at` timestamp.
- **What EB5 would return afterward**: `effective_decision=ATTACH_CONFIRMED`, `basis=LEGACY_HUMAN_ATTESTATION` (not `LATEST_REEVALUATION`, not `ORIGINAL_DECISION` — clearly provenanced as coming from this new tier).
- **What would still block intelligence review**: exactly one remaining, independent gate — **no `usaspending_grant` claims extractor exists**, so `persist_intelligence_review()` still has no `tuple[Claim, ...]` to evaluate even once identity resolves. This is the next, genuinely separate gate this design deliberately does not solve (mission's own explicit instruction) — confirming identity alone does not unblock the whole chain; it only removes the *first* of at least two independent blockers.

## 9. Blast-radius analysis (real DB, read-only, zero rows modified)

Of 231 real `SourceAssertion` rows: 220 have `airport_id` set, 6 are candidate-linked, 5 are fully unresolved. Of the 220 airport-linked rows, only **4** have ever gone through the modern identity guard (`identity_guard_decision IS NOT NULL`) — the KAR/EB4 rows produced by this session's own prior missions (Finding 2/4 and the rehearsal fixture). **The remaining 216 (93.5% of all real SourceAssertions) are exactly this legacy-attached/unguarded class:**

| source_type | count | parser_identifier | count |
|---|---|---|---|
| `faa_nasr_apt_ars` | 112 | `faa-nasr-apt-ars/2026-08-06` | 112 |
| `faa_tableau` | 70 | `import_faa_csv.py/legacy-csv-backfill-v1` | 70 |
| `usaspending_grant` | 25 | `legacy-source-backfill-v1` | 25 |
| `faa_fact_sheet` | 6 | `faa-fact-sheet-manifest-v1` | 6 |
| `news` | 3 | `discrete-manifest-v1` | 3 |

All 216: zero have an `EvidenceBag`; zero have `signal_id` set; `evidence_quality` splits 140 `direct_strong` / 76 `partial` (none `unverified_candidate`/`ambiguous`/`corroborated` in this specific group).

**Verdict: this is unambiguously a systematic legacy-governance gap, not a GMU anomaly.** A secondary, important observation for future prioritization (not a change to eligibility, §2): the two largest groups (`faa_nasr_apt_ars`, `faa_tableau`, 182 rows combined) are structured, geocoded, machine-readable feeds already carrying `ARPT_ID`/city/state fields — their identity is typically *self-evident* from their own structure, unlike narrative `usaspending_grant`/`news` rows (28 combined) where a human genuinely must read prose to judge identity. A future, separately-designed and separately-authorized slice might reasonably treat these two groups differently in tooling ergonomics (e.g., a review queue sorted to surface the harder narrative cases first) — but this document does not propose any automated/bulk shortcut for the structured feeds; §10's own no-go boundary applies equally to all 216.

## 10. Minimal future implementation slice (design only, not built here)

- **Model**: one new file, `app/models/source_assertion_legacy_identity_attestation.py` — `SourceAssertionLegacyIdentityAttestation` per §5, additive only, no existing model file touched (mirrors KAR1's own "fully additive" precedent).
- **Migration**: one new script, structurally identical to `migrate_source_assertion_identity_resolution_kar1.py` (additive-only, idempotent `upgrade()`, conservative `downgrade()` refusing on any row present, mandatory timestamped backup, `--allow-database-write` gate, zero backfill).
- **Service**: one new file, `app/services/source_assertion_legacy_identity_attestation.py` — one governed function, `record_legacy_identity_attestation(session, *, source_assertion_id, action, reason, reviewer, matched_airport_id=None) -> ...Result`, enforcing eligibility (§2) and building the review-time snapshot (§5) itself (never trusting a caller-supplied snapshot).
- **EB5 modification**: the smallest possible additive change to `effective_identity_guard_decision.py` — one new fallback tier (§6) and one new `EffectiveIdentityGuardDecisionBasis` member. No existing branch's behavior changes for any row that already has a real `identity_guard_decision` or a real `IdentityGuardEvaluation`.
- **CLI**: one new script mirroring `resolve_source_assertion_identity.py`'s exact inspect/dry-run/write contract (`--source-assertion-id` alone → inspect; add `--action/--reviewer/--reason[/--matched-airport-id]` → dry-run via a rolled-back transaction; add `--allow-database-write` → the only writing invocation).
- **Tests**: model migration tests, service precondition/idempotency/immutability tests, CLI dry-run/write-gate tests, an EB5 integration test proving precedence against both EB4 and the bare original decision, and a firewall test proving `identity_guard_decision`/`SourceAssertionEvidenceBag` are never touched by any part of this slice.
- **Explicit no-go boundaries for this slice**: no USAspending (or any other) claims extractor; no Signal creation or lifecycle change of any kind; no discovery-pipeline change; no Airport repair/re-attachment path; **no bulk/batch confirmation of any kind** — the CLI operates on exactly one `--source-assertion-id` per invocation, matching this mission's own explicit "no bulk auto-confirmation" instruction, regardless of the 216-row blast radius found in §9.

## 11. Recommendation

**GO** — the design is sound, small, additive, and directly reuses every relevant existing pattern in this codebase (KAR1's row shape, EB1's snapshot shape, EB4/EB5's precedence-and-fallback model, KAR's CLI contract) rather than inventing new ones. It closes a real, large (93.5% of real evidence), previously-undiagnosed governance gap without weakening any existing path.

RWI_LEGACY_ATTACHED_IDENTITY_GOVERNANCE_DESIGN_COMPLETE
