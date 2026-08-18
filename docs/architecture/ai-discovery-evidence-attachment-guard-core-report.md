# AI-Discovery Evidence Attachment Guard — Core Implementation Report

Implements the first, isolated core slice of the approved design
(`docs/architecture/ai-discovery-evidence-attachment-guard.md`). **This
slice is the deterministic decision core only** — no ingestion
integration, no database write, no schema change, no migration, no
USAspending retrofit, no crawler, no n8n integration, no Signal/Fact/
Intelligence creation, no public UI change, no commit, no push, no
deployment. Baseline: branch `main`, HEAD
`cb8558bfd5fd7b968abaa11259542114c9e48225`.

## 1. Design implemented

The full design document (read in full before implementation) was
implemented with one deliberate, transparently-documented refinement to
its decision-contract wording — see §5 below for exactly what and why.
Every other rule (contradiction-first, evidence categories, runway
normalization reuse, fragment-scoped evaluation, AI/deterministic
boundary) is implemented as designed.

## 2. Public/core API

`app/services/evidence_attachment_guard.py`, exported names:

```python
evaluate_attachment(candidate: CandidateAirport, evidence: EvidenceBag) -> AttachmentDecision
evaluate_attachment_for_candidates(evidence: EvidenceBag, candidates: list[CandidateAirport]) -> dict[object, AttachmentDecision]
candidate_airport_from_airport_like(airport_like, *, aliases=frozenset(), known_issuers=frozenset()) -> CandidateAirport

EvidenceCategory, AttachmentOutcome  # str+Enum
CandidateAirport, EvidenceBag, EvidenceItem, ContradictionItem, AttachmentDecision  # frozen dataclasses
```

`evaluate_attachment()` is the pure core: one evidence fragment, one
candidate airport, no database, no network, no mutation.
`evaluate_attachment_for_candidates()` is a thin, still-pure orchestrator
that resolves cross-candidate ambiguity (§5).
`candidate_airport_from_airport_like()` is an optional, duck-typed
convenience builder — never called by `evaluate_attachment()` itself, and
the guard module has zero import of `app.models`/`sqlalchemy`/`httpx`
(proven by test, §9).

## 3. Evidence-bag design

`EvidenceBag` (frozen dataclass) carries exactly what the A–K cases and
the design doc require — no speculative fields:

- `identifiers`, `runway_ends`, `runway_pairs` — compared directly
  against the candidate's own data. A non-matching identifier or a
  runway token independently corroborated elsewhere is **self-verifying**
  contradiction; no external reference table is needed to know two
  different code strings identify different airports.
- `names`, `issuers`, `locations` — positive-only against a *matching*
  candidate. Free-text strings are not self-verifying the way a coded
  identifier or topology token is, so an unmatched name/issuer/location
  is never automatically treated as contradiction.
- `contradicting_names`, `contradicting_issuers`, `contradicting_locations`
  — populated **only by the caller** (an extractor, a reference-table
  lookup, a human), never inferred by this module. This is the mechanism
  that implements design doc §5's "issuer/name/location known to belong
  to a different, specific airport" contradiction rule without requiring
  the guard to consult any external directory itself.
- `alternate_airport_runway_ends`/`alternate_airport_runway_pairs` —
  optional, strongest form of runway-elsewhere corroboration, used only
  when the caller has already resolved which specific other airport a
  token belongs to.
- `document_title`/`project_number`/`contract_number`/`url` — carried
  through for audit context only, never used in the decision algorithm.

All fields default to empty — a fragment need not contain every evidence
type, and an entirely empty bag is a legitimate, expected input
(`INSUFFICIENT_IDENTITY`, never an error).

## 4. Candidate-airport design

`CandidateAirport` is a small, DB-free view: `id` (opaque, threaded
through to the decision, never inspected), `name`, `identifiers`,
`aliases`, `city_location`, `canonical_runway_ends`,
`canonical_runway_pairs`, `known_issuers`. Every field normalizes once in
`__post_init__` (case-folded text, `normalize_end`/`normalize_pair` for
topology) so every downstream comparison is cheap and never re-derives
normalization — the same defensive-normalization posture
`physical_installation_identity_linking.py` already takes toward
supposedly-already-normalized canonical data, applied consistently here.

`candidate_airport_from_airport_like()` builds one from any ORM-shaped
object via **duck typing** — it never imports `app.models.Airport`,
proven directly by `test_candidate_airport_from_airport_like_builds_correct_topology`,
which passes a hand-written stand-in class, not the real ORM class. This
keeps the guard module itself importable and testable with zero database
dependency, while still making it trivial for a real caller to build a
`CandidateAirport` from a live `Airport` row (whose `.runways`/
`.runway_ends` relationships should already be eager-loaded, exactly the
existing convention in `app/static_export/build.py` — documented directly
in the function's own docstring, not assumed).

## 5. Contradiction-first flow, and the one documented refinement

Implemented exactly as designed: `evaluate_attachment()` computes
positive and contradicting evidence across all five categories, and if
**any** contradiction is found — regardless of source, regardless of how
much positive evidence also exists — the outcome is unconditionally
`REJECT_CROSS_AIRPORT`. Nothing overrides this.

**The one refinement, stated plainly**: the design document's §7 wording
suggested a compound/pair runway-topology match could, alone, be "strong"
enough to reach `ATTACH_CONFIRMED` on its own (§6.4 of the design doc).
Implementing this literally inside a function that evaluates **one
candidate in isolation** creates an internal contradiction with the
design doc's own worked case J ("valid runway designation, weak airport
identity → must not over-confirm merely from runway coincidence") and
with task instruction §9 ("If the current design cannot cleanly
distinguish these two outcomes, STOP and report the ambiguity rather than
inventing semantics").

Rather than silently picking one reading, the implementation resolves
this by **splitting responsibility across two functions**, which the
design doc itself already anticipated needing (case J's own text: "…if
only one existing RWI airport has that exact pair; REVIEW_REQUIRED if
more than one canonical airport shares it" — inherently a multi-candidate
concept):

- `evaluate_attachment()` (single candidate, in isolation): only an
  **identifier match** is strong enough to confirm alone (no other real
  airport shares a given identifier — this is inherently unique, no
  cross-candidate check needed). Any other single category — including a
  compound-pair runway match — reaches `ATTACH_PROVISIONAL`, never
  `ATTACH_CONFIRMED`, alone. Two or more independent categories together
  (of any kind) reach `ATTACH_CONFIRMED`. `REVIEW_REQUIRED` is
  **structurally never returned by this function** — a single candidate
  cannot know whether some other candidate would explain the same
  evidence equally well.
- `evaluate_attachment_for_candidates()` (new, thin, still pure): runs
  the above against every supplied candidate, then checks whether more
  than one candidate independently reached `ATTACH_CONFIRMED`/
  `ATTACH_PROVISIONAL` for the *same* evidence bag. If so, every such
  candidate is downgraded to `REVIEW_REQUIRED` — this is exactly where a
  compound-pair match's "strength" actually matters: it is rare enough
  that, in practice, it will very often turn out to be unique across the
  real candidate set, at which point the single qualifying candidate's
  own `ATTACH_PROVISIONAL` (or `ATTACH_CONFIRMED`, if corroborated by a
  second category) stands unchanged.

This keeps every single-candidate outcome self-consistent and impossible
to over-claim from, while still producing all five outcomes overall,
exactly as case J requires. See §9 for the exact test that proves this
(`test_case_J_runway_only_unique_across_candidates_is_provisional` /
`test_case_J_runway_shared_by_two_candidates_becomes_review_required`).

## 6. Positive evidence rules (exact machine rules)

For candidate X and evidence bag E, `evaluate_attachment(X, E)`:

1. Compute contradiction across all 5 categories (§5). Non-empty → `REJECT_CROSS_AIRPORT`, stop.
2. Compute the **set of distinct positive categories** that matched (identifier / name / runway_topology / issuer / location) — each category counts once no matter how many individual tokens matched within it.
3. `IDENTIFIER ∈ categories` → `ATTACH_CONFIRMED`.
4. `len(categories) >= 2` → `ATTACH_CONFIRMED`.
5. `len(categories) == 1` (and it is not `IDENTIFIER`, which was already handled in step 3) → `ATTACH_PROVISIONAL`.
6. `len(categories) == 0` → `INSUFFICIENT_IDENTITY`.

No weighted/ML-style confidence score anywhere — every branch is a plain,
inspectable set-membership/count check, consistent with every other
fail-closed classifier already in this repository
(`resolve_airport()`, `resolve_identity()`, the NASR classifier).

## 7. Exact outcome semantics

| Outcome | Meaning | Reachable from |
|---|---|---|
| `ATTACH_CONFIRMED` | Strong enough for evidence-level attachment; still never auto-creates a `PhysicalInstallationIdentity` (design doc §7 table, unchanged) | `evaluate_attachment()` directly |
| `ATTACH_PROVISIONAL` | Exactly one non-identifier category; candidate-evidence only | `evaluate_attachment()` directly |
| `REVIEW_REQUIRED` | Ambiguous across ≥2 candidates for the same evidence | `evaluate_attachment_for_candidates()` only — never returned by the single-candidate function |
| `REJECT_CROSS_AIRPORT` | Any contradiction found | `evaluate_attachment()` directly |
| `INSUFFICIENT_IDENTITY` | No positive or contradicting evidence at all | `evaluate_attachment()` directly |

**`ATTACH_PROVISIONAL` vs. `REVIEW_REQUIRED` — confirmed not
redundant, exact machine distinction**: `ATTACH_PROVISIONAL` answers "is
*this one* candidate, alone, sufficiently well-evidenced?" — no other
candidate is consulted or even known about; the axis is **evidence
strength for a single airport** (exactly one category matched, none of
them an identifier). `REVIEW_REQUIRED` answers a completely different
question — "does *more than one* candidate independently reach a
qualifying outcome for the *same* evidence?" — the axis is **ambiguity
across airports**, and it can only ever be computed by
`evaluate_attachment_for_candidates()`, which has visibility into the
full candidate set that a single `evaluate_attachment()` call
structurally does not. A concrete case that only makes sense once both
exist side by side: a compound runway-pair match evaluated against one
candidate in isolation is `ATTACH_PROVISIONAL` (case J, one candidate,
one category); the identical evidence evaluated against the *same*
candidate set where a second, different airport genuinely shares that
exact pair is `REVIEW_REQUIRED` for *both* candidates (case J's own
second half) — not because either candidate's own evidence changed, but
because the question being asked changed from "is this enough?" to "is
this enough to tell these two apart?". No overlap, no redundancy: a
result can be `ATTACH_PROVISIONAL` for a candidate evaluated alone and
never becomes `REVIEW_REQUIRED` unless a second qualifying candidate is
introduced, and conversely `REVIEW_REQUIRED` never arises from
evidence-strength concerns alone (a single weak-evidence candidate,
checked alone, is `ATTACH_PROVISIONAL`, never `REVIEW_REQUIRED`).

## 8. Reason/audit structure

`AttachmentDecision` is a frozen dataclass: `outcome`, `reason` (a
human-readable string citing the specific categories/values that drove
the decision — never a bare label), `positive_evidence` (tuple of
`EvidenceItem(category, value, detail)`), `contradicting_evidence`
(tuple of `ContradictionItem(category, value, detail)`). Every code path
constructs the full object — there is no path that returns only an
outcome with no reasoning. Nothing is persisted to the database in this
slice (per instruction) — this structure exists specifically so the
future `identity_guard_decision`/`identity_guard_reason` columns from the
design doc's §11 can be evaluated against real decision output before
committing to that schema change.

## 9. A–K case results

All implemented as dedicated tests in `tests/test_evidence_attachment_guard.py`,
using synthetic `CandidateAirport`/`EvidenceBag` fixtures shaped like the
real BOS/ORH/SFO/MSP/Allegheny/Morristown cases — never the real
database.

| Case | Test(s) | Result |
|---|---|---|
| A. SFO/MSP false positive | `test_case_A_sfo_msp_false_positive_rejects_cross_airport`, `test_case_A_same_evidence_confirms_for_the_real_airport_msp` | `REJECT_CROSS_AIRPORT` for SFO; `ATTACH_CONFIRMED` for the real airport (MSP) with the identical evidence |
| B. Genuine SFO 1R/19L evidence | `test_case_B_genuine_sfo_evidence_confirms` | `ATTACH_CONFIRMED` |
| C. BOS protected-direction naming | `test_case_C_bos_protected_direction_naming_confirms` | `ATTACH_CONFIRMED` |
| D. ORH dual physical/protected naming | `test_case_D_orh_dual_physical_protected_naming_confirms` | `ATTACH_CONFIRMED` |
| E. USAspending embedded FAA Loc ID | `test_case_E_usaspending_embedded_faa_loc_id_confirms` | `ATTACH_CONFIRMED` |
| F. USAspending city/state only | `test_case_F_usaspending_city_state_only_is_provisional_not_confirmed` | `ATTACH_PROVISIONAL` — **deliberately differs from `resolve_airport()`'s current full-resolution behavior**; flagged, not retrofitted (§15) |
| G. Allegheny recipient-name failure | `test_case_G_allegheny_recipient_name_alone_is_insufficient`, `test_case_G_allegheny_recipient_name_never_reaches_confirmed_even_if_naively_extracted` | `INSUFFICIENT_IDENTITY` in both the correctly-modeled case (no evidence at all) and a worst-case naive-extraction fixture |
| H. Morristown recipient-name failure | `test_case_H_morristown_recipient_name_alone_is_insufficient` | `INSUFFICIENT_IDENTITY` |
| I. Valid identity, no runway | `test_case_I_valid_airport_identity_no_runway_reference_is_provisional`, `test_case_I_valid_airport_identity_plus_identifier_confirms` | `ATTACH_PROVISIONAL` (name alone) / `ATTACH_CONFIRMED` (identifier present) |
| J. Valid runway, weak identity | `test_case_J_runway_only_unique_across_candidates_is_provisional`, `test_case_J_runway_shared_by_two_candidates_becomes_review_required` | `ATTACH_PROVISIONAL` when unique to one candidate; `REVIEW_REQUIRED` for both when two candidates genuinely share the exact pair |
| K. Multiple-airport document | `test_case_K_multiple_airport_document_is_fragment_scoped`, `test_case_K_explicit_other_airport_identifier_in_same_fragment_rejects` | Strong BOS-fragment evidence never attaches to ORH; a fragment naming a different airport's real identifier is rejected for the wrong candidate, not merely insufficient |

## 10. Adversarial case results

All 10 required adversarial cases implemented and passing:

1. Correct code + unrelated topical text → still `ATTACH_CONFIRMED` (topical noise has no evidence category, so no effect).
2. Wrong code + matching runway string → `REJECT_CROSS_AIRPORT` (contradiction wins even with real topology overlap).
3. Correct name + impossible runway (corroborated) → `REJECT_CROSS_AIRPORT`.
4. Operator managing multiple RWI airports (Massport → BOS and ORH) → confirms independently for each, driven by each candidate's own `known_issuers`, never a single inferred "owner."
5. Runway valid at many airports, nothing else → `ATTACH_PROVISIONAL`, never confirms alone.
6. `04L` vs `4L` → identical result both ways, no false contradiction from formatting.
7. Search-query-only "evidence" → `INSUFFICIENT_IDENTITY` — proven structurally: there is no field in `EvidenceBag` for "the airport the search targeted," so it cannot be evidence even by accident.
8. Issuer says MSP, (implicit) query says SFO → `REJECT_CROSS_AIRPORT`.
9. Fragment names both SFO and MSP explicitly → `REJECT_CROSS_AIRPORT` for **both**, evaluated independently (a fragment naming two real, different airport identifiers together is not safely attributable to either without better fragmentation — a deliberate, fail-closed consequence documented in §15, not a bug).
10. Alias matches, but an explicit different ICAO also present → `REJECT_CROSS_AIRPORT` (identifier-level contradiction outranks an alias match).

## 11. Existing helper reuse

- `app.services.runway_identity.normalize_end`/`normalize_pair`/
  `is_two_ended_pair_shape`/`AmbiguousRunwayDesignationError` — imported
  and used directly, exactly as designed; zero runway-string logic
  reimplemented anywhere in the guard.
- The topology-membership *pattern* (normalize once, compare against a
  precomputed per-candidate set) mirrors
  `physical_installation_identity_linking.py::_ends_by_designation_for_airport()`/
  `resolve_identity()` exactly, including the same "defensive
  re-normalization of already-canonical data" posture — not imported
  (that module is DB-coupled by design, taking a live `Session`), but its
  *approach* is faithfully carried over into this DB-free module, as the
  design doc's §3 table specified.
- No other helper module needed changes; nothing in `runway_identity.py`
  or `physical_installation_identity_linking.py` was modified.

## 12. International-readiness result

`test_international_haneda_confirms_with_no_us_specific_data` and
`test_international_native_alias_counts_as_name_evidence` build a fully
synthetic Tokyo Haneda (`RJTT`/`HND`) candidate with no FAA-shaped
identifier, no U.S. state, and a native-script (`羽田空港`) alias, and
prove: (a) an ICAO identifier alone confirms exactly like an FAA/IATA one
would — the guard's identifier category has no U.S.-specific shape; (b) a
non-English alias string participates in name-matching identically to an
English one, since matching is plain case-folded string equality with no
language-specific logic anywhere. Confirms the design doc §9 verdict
directly: the decision core has no structural U.S. dependency — verified
by test, not merely asserted.

## 13. Side-effect/purity proof

- `test_evaluate_attachment_is_deterministic_across_repeated_calls` — 5
  repeated calls with identical inputs produce byte-identical outcome,
  reason, and evidence tuples.
- `test_evaluate_attachment_does_not_mutate_its_inputs` — deep-copies the
  candidate and evidence bag before calling, asserts both are unchanged
  afterward (both dataclasses are `frozen=True`, so this is also
  structurally enforced, not just observed).
- `test_dataclasses_are_frozen_and_reject_mutation` — asserts attempted
  attribute mutation on both `CandidateAirport` and `EvidenceBag` raises
  `dataclasses.FrozenInstanceError`.
- `test_evaluate_attachment_performs_no_module_level_io_imports` — parses
  the module's own source into an AST and inspects the actual
  `ast.Import`/`ast.ImportFrom` nodes for `sqlalchemy`, `httpx`,
  `requests`, `app.database`, or `app.models` — structural proof, not
  just behavioral observation, that this module cannot reach a database
  or the network even if a future edit tried to sneak it in without a
  corresponding test failure. **Corrected during the final commit review**
  (`RWI_EVIDENCE_ATTACHMENT_GUARD_CORE_REVIEW_COMMIT_PUSH`): the original
  version of this test checked source-text line prefixes rather than
  actual import nodes, which both false-positived (a docstring's prose
  mention of "app.models" as text, not an import, once briefly broke this
  same test during initial development) and would have false-negatived on
  an aliased or multi-name import a line-prefix check doesn't anticipate.
  The AST-based version is immune to both failure modes.

## 14. Tests

`tests/test_evidence_attachment_guard.py` — **33 passed**, covering all
A–K worked cases, all 10 adversarial cases, 2 international fixtures, 4
purity/determinism proofs, and the `candidate_airport_from_airport_like()`
builder.

Combined focused run (`test_evidence_attachment_guard.py` +
`test_runway_identity_normalization.py` +
`test_physical_installation_reconciliation.py` +
`test_physical_installation_identity_linking.py`): **79 passed** — no
regression in any reused helper module.

Full suite: **675 passed** (642 pre-existing baseline + 33 new) — zero
regressions anywhere in the repository.

`py_compile` on both new files: clean. `git diff --check`: exit 0.

## 15. Limitations

- **Case F design tension is intentionally left unresolved by this
  slice**, per the design doc's own explicit deferral: a bare
  city/state-only match now yields `ATTACH_PROVISIONAL` in this module,
  while `resolve_airport()` still independently treats a *unique*
  city/state match as a full resolution today. `scripts/import_usaspending_grants.py`
  was **not** touched in this task (explicitly prohibited) — this
  divergence is surfaced, not silently harmonized, exactly as instructed.
- **Adversarial case 9's fail-closed-for-both behavior** (a fragment
  naming two real airports' identifiers together rejects for both) is a
  deliberate consequence of "an identifier not matching the candidate is
  self-evidently contradictory," not a special rule — it assumes
  fragments are already reasonably single-airport-scoped by the
  extraction layer (as design doc §8 case K itself specifies: "the guard
  is invoked once per fragment, never once per whole document"). A
  poorly-fragmented multi-airport document will safely reject rather than
  guess, but will not automatically split itself into per-airport pieces
  — that responsibility stays entirely with the extraction layer.
- **The `≥2 categories ⇒ CONFIRMED` / `identifier alone ⇒ CONFIRMED`
  thresholds remain judgment calls**, not empirically derived — the
  design doc's own §13 already flagged this; this slice does not change
  that status, only implements the specific thresholds as designed (with
  the §5 refinement).
- **No schema change, no persistence** — `identity_guard_decision`/
  `identity_guard_reason` remain unimplemented by design; this slice's
  own `AttachmentDecision.reason` structure exists specifically to let a
  human evaluate whether those columns are worth adding, based on real
  decision output, before that commitment is made.
- **The issuer/alias reference data used in every test is hand-authored
  for this task** (e.g., knowing Massport governs both BOS and ORH) — no
  general-purpose issuer→airport reference table exists yet; building one
  is design doc §12 slice 4, explicitly out of scope here.

## 16. Recommended next integration slice

Per the design doc's own §12 ordering, and unchanged by anything learned
during this implementation: the **next** slice should still be the
additive `SourceAssertion` schema columns
(`identity_guard_decision`/`identity_guard_reason`), wired into a **new**
discovery pathway first — never a retrofit of the already-working NASR/
USAspending pipelines in the same slice. The retrofit of
`resolve_airport()` (design doc §12 slice 3) should come only after that,
as its own explicit, human-reviewed change, specifically because of the
case F divergence this slice surfaced (§15) — that divergence needs a
human decision, not a silent code change, before `resolve_airport()`'s
behavior is altered.
