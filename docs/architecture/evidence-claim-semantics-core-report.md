# Evidence Claim Semantics — Core Slice 1 Report

Implements Slice 1 of
`docs/architecture/evidence-to-signal-semantics-design.md`: the
pure, deterministic core answering *"what does authoritative evidence
actually say?"* — never *"should RWI create a Signal?"* (that remains
`SignalCandidate`, Slice 3, not built here). Baseline: branch `main`,
HEAD `bbe6a11332588d0f056ee107c40d4fd38ab6d5ca`.

## 1. Starting HEAD

`main` @ `bbe6a11332588d0f056ee107c40d4fd38ab6d5ca`, matched
`origin/main`.

## 2. Core API

`app/services/evidence_claim_semantics.py` — one module, zero
dependencies beyond the standard library (`dataclasses`, `datetime`,
`decimal`, `enum`). Public surface: `ClaimCategory`, `ProvenanceKind`,
`TemporalQualifier`, `TemporalContext`, `FinancialFact`,
`RelationshipFact`, `ClaimProvenance`, `Claim` — all frozen dataclasses
or `str`+`Enum` (matching the exact typed-outcome convention already
established by `AttachmentOutcome`/`AcquisitionRunStatus`).

## 3. Claim structure

```python
Claim(
    category: ClaimCategory,
    subject: str,
    statement: str,
    provenance: ClaimProvenance,
    provenance_kind: ProvenanceKind = EXPLICIT,
    financial: FinancialFact | None = None,
    temporal: TemporalContext | None = None,
    relationship: RelationshipFact | None = None,
)
```

`financial`/`temporal`/`relationship` are independently optional and
combinable — a single claim may carry any subset (proven directly:
claim D combines `PROCEDURAL_REQUEST` + `financial` + `temporal`; claim
F combines `EXPLICIT_DOCUMENT_FACT` + `financial` + `temporal`; claim H
is `TEMPORAL_STATEMENT` with `temporal` only, no financial component).

## 4. Claim categories

`ClaimCategory`: `EXPLICIT_DOCUMENT_FACT`, `PROCEDURAL_REQUEST`,
`TEMPORAL_STATEMENT`, `RELATIONSHIP`. Deliberately **no**
`FINANCIAL_SEMANTIC` or `NEGATIVE_CONSTRAINT` category member — the
design doc's own table listed these as row labels for exposition, but on
implementation both turned out to be **attachments**, not epistemic
categories of their own: `financial` is an optional dimension any claim
may carry (an `EXPLICIT_DOCUMENT_FACT` or a `PROCEDURAL_REQUEST` can
both have a `FinancialFact`), and a negative constraint is scoped to the
specific `FinancialFact` it protects (`FinancialFact.not_established`),
never a freestanding claim (see §20, design correction).

`ProvenanceKind`: `EXPLICIT` (text states it directly) / `DERIVED`
(assembled from other claims/context) — kept orthogonal to `category`
per the design doc's own "explicit vs derived semantics" requirement.

## 5. Financial semantic roles

`FinancialFact(amount: Decimal, currency: str, semantic_role: str,
not_established: tuple[str, ...] = ())`. `semantic_role` is **required**
(no default — `inspect.signature` confirms no default value exists) and
deliberately **free-text**, not a closed enum, mirroring how
`Signal.category` is itself a plain string column with only a
presentation-layer curated mapping. The dataclass's own field set —
`{amount, currency, semantic_role, not_established}` — has **no**
`cost`/`value`/`contract_value` field for a role-less number to collapse
into; this is enforced structurally, not by a runtime check (proven by
`test_financial_fact_has_no_generic_cost_or_value_field`).

MSP's two real amounts remain permanently distinct objects:
`amount=1,590,000 USD, role=advance_deposit_purchase_order` and
`amount=19,000,000 USD, role=cip_project_ceiling` — proven unequal,
proven never mergeable (no merge operation exists anywhere in the
module).

## 6. Temporal qualifiers

`TemporalQualifier`: `HISTORICAL_FACT`,
`CURRENT_STATE_AS_OF_DOCUMENT_DATE`, `PLANNED_FUTURE_ACTION`,
`REQUESTED_PENDING_APPROVAL`, `COMPLETED`, `UNKNOWN`. The last two
(`COMPLETED`, `UNKNOWN`) complete the design doc's own 4-member sketch
per this slice's own task instruction ("at minimum: historical/current,
future/planned, completed, unknown") — not a contradiction of the
approved design, a completion of it (see §20).

`TemporalContext.as_of_date` has **no default** producing "today" — the
caller must always supply the cited document's own date (or `None`
explicitly). **Hard test passed**: a claim built with
`qualifier=PLANNED_FUTURE_ACTION, as_of_date=2024-08-28` remains
`PLANNED_FUTURE_ACTION` regardless of what year the test process itself
runs in, because the module contains zero `datetime.now()`/
`date.today()`/`.utcnow()` calls anywhere (AST-verified, not merely
grepped — the module's own docstrings mention these function names in
prose, which a naive substring search would have false-flagged; caught
and fixed during this slice's own test-writing, see §20).

## 7. Procedural semantics

`ClaimCategory.PROCEDURAL_REQUEST` + `TemporalQualifier.REQUESTED_PENDING_APPROVAL`
together represent the design doc's own new finding: SourceAssertion
#222 is marked `FOR ACTION` — a staff *request* for Commission
authorization, not a record of an executed decision. Proven adversarially:
a `PROCEDURAL_REQUEST` claim and a hypothetical later `EXPLICIT_DOCUMENT_FACT`
"awarded" claim are constructed as two independent objects with different
`category` and `temporal.qualifier` values — nothing in this module ever
promotes one into the other; only a second, independent `Claim` (from a
second document) can represent an advanced real-world status.

## 8. Negative-constraint representation

`FinancialFact.not_established: tuple[str, ...]` — plain string labels,
no logical structure, no inference engine (per instruction). MSP's own
CIP-ceiling fact carries `not_established=("contract_value",
"confirmed_vendor_award_amount", "estimated_vendor_revenue")`, proven to
survive intact and remain distinct from the fact's own `semantic_role`.

## 9. Provenance design

`ClaimProvenance(artifact_identity, source_locator, fragment_hash,
raw_text_excerpt)` — reuses `CandidateFragment`'s/`SourceAssertion`'s
own existing identity field names verbatim, no new identity scheme.
Every MSP golden-case claim traces to the real, read-only-confirmed
values from `SourceAssertion` id 222: `artifact_identity =
"mac.granicus.document.4.2349.105406"`, `source_locator = "item-2.3.2"`,
`fragment_hash =
"76e5bf71cd2cb4759d3f9c1a568a14cf121626ede75ee00371a58f221852b4fa"`.
Claims are in-memory and re-derivable; `ClaimProvenance` implies no
database table.

## 10. MSP A–I golden-case result

All decomposed claims from the approved design (A, B, C, D, F, H, I —
E/G/J were folded into their parent claims per §4/§20) constructed and
tested successfully, each tracing to the same real fragment identity:

| Claim | Category | Financial | Temporal | Relationship |
|---|---|---|---|---|
| A | EXPLICIT_DOCUMENT_FACT | — | — | — |
| B | EXPLICIT_DOCUMENT_FACT | — | — | — |
| C | EXPLICIT_DOCUMENT_FACT | — | — | `sole_approved_manufacturer` |
| D | PROCEDURAL_REQUEST | $1.59M / `advance_deposit_purchase_order` | `REQUESTED_PENDING_APPROVAL`, 2024-08-28 | — |
| F | EXPLICIT_DOCUMENT_FACT | $19M / `cip_project_ceiling`, `not_established=(contract_value,...)` | `HISTORICAL_FACT`, 2023-12-18 | — |
| H | TEMPORAL_STATEMENT | — | `PLANNED_FUTURE_ACTION`, 2024-08-28 | — |
| I | RELATIONSHIP | — | — | `material_supplier_and_installation_oversight` |

## 11. SFO-$40M adversarial result

Three dedicated adversarial tests prove the core cannot represent
`airport + vendor + EMAS + $40M` as a vendor contract:
`test_unlabeled_amount_cannot_be_represented_as_a_vendor_contract`
(no `contract_value` field exists on `Claim` or `FinancialFact` at all —
checked structurally via `dataclasses.fields`), 
`test_topical_proximity_relationship_does_not_imply_award` (a
`mentioned_in_same_fragment` relationship role is never equal to a
`confirmed_contract_award` role), and
`test_large_amount_alone_is_not_a_relationship_or_award_claim` (a bare
`FinancialFact` with no `relationship` attached carries no vendor
association at all). Correct airport identity changes nothing about
this — the adversarial test explicitly builds a separate,
correctly-categorized identity claim alongside the ambiguous-amount
claim and confirms neither combination produces a contract-value field.

## 12. International-readiness result

`test_synthetic_non_us_non_usd_case_uses_the_identical_generic_shape`
builds a hypothetical Japanese-authority EMAS budget claim in EUR, with
a non-Runway-Safe vendor, using the identical `Claim`/`FinancialFact`/
`RelationshipFact`/`TemporalContext` shapes — no special-cased class, no
currency validation restricted to USD, no vendor-name dependency
anywhere in the core module (confirmed by inspection: zero occurrences
of `"MSP"`, `"MAC"`, `"FAA"`, `"Runway Safe"`, `"USD"` in
`evidence_claim_semantics.py` itself — those strings exist only in this
module's test file).

## 13. Purity/determinism result

AST-verified (not grepped): zero imports of `sqlalchemy`, `app.database`,
`app.models`, `httpx`, `requests`, `urllib`, `socket`; zero `Call` nodes
naming `now`/`today`/`utcnow` anywhere in the module (the module's own
prose *mentions* these names to explain their absence — a naive
substring test would have false-flagged this, and did during
development; fixed to AST-based detection, see §20). No `open(`, no
`os.`, no `pathlib`/`Path(` usage. Every public type is a frozen
dataclass or `str`+`Enum`; equal constructions produce equal, hashable
instances.

## 14. Focused tests

`tests/test_evidence_claim_semantics.py` — **31 passed**, covering
immutability, deterministic equality, financial-role separation (5
tests), temporal safety including the exact "2024 claim evaluated in a
later year stays PLANNED_FUTURE_ACTION" hard test, procedural (FOR
ACTION) semantics, negative constraints, provenance, the full MSP A–I
golden case (9 tests), 3 SFO-$40M adversarial tests, 1 international/
non-USD case, and 4 purity/import-boundary tests.

## 15. Full pytest result

**851 passed** (820 baseline + 31 new).

## 16. py_compile

`PY_COMPILE_OK` on both new files.

## 17. git diff --check

`GIT_DIFF_CHECK_OK`.

## 18. Exact files changed

- `app/services/evidence_claim_semantics.py` (new).
- `tests/test_evidence_claim_semantics.py` (new).
- `docs/architecture/evidence-claim-semantics-core-report.md` (new, this
  file).

No other file created, modified, migrated, or written. No database
touched at any point in this slice (one read-only confirmation of
`SourceAssertion` #222's provenance identity, performed before writing
the golden-case fixture, per the task's own explicit allowance).

## 19. git status

Only the three files above, plus pre-existing, unrelated untracked items
from earlier sessions — nothing staged, nothing committed.

## 20. Design corrections discovered during implementation

1. **`FINANCIAL_SEMANTIC` and `NEGATIVE_CONSTRAINT` are not
   `ClaimCategory` members.** The approved design doc's own decomposition
   table listed these as row labels alongside `EXPLICIT_DOCUMENT_FACT`
   etc., but implementing claim D (`PROCEDURAL_REQUEST` + a financial
   fact) and claim F (`EXPLICIT_DOCUMENT_FACT` + a financial fact)
   directly showed that "financial" is an **attachment**, not a
   competing epistemic category — a claim's `category` answers "what
   kind of statement is this" while `financial` answers "does it also
   assert a specific amount." Similarly, the design doc's own §4 already
   called claim G "not an extracted claim... a boundary the
   claim/promotion model itself must enforce" — implementation confirmed
   this belongs on `FinancialFact.not_established`, scoped to the
   specific amount it protects, not as a freestanding claim. Neither
   change contradicts the approved architecture; both resolve an
   ambiguity the design doc itself flagged as unresolved (G's own
   wording already hedged "not an extracted claim").
2. **`TemporalQualifier` gained `COMPLETED`/`UNKNOWN`.** The design
   doc's own S6 table listed 4 qualifiers for the MSP example
   specifically; this slice's own task instruction (S5) independently
   named "completed statement" and "unknown temporal state" as required
   minimums. Both were added; neither conflicts with the design doc,
   which never claimed its 4-member sketch was exhaustive.
3. **A naive `"datetime.now(" not in source` test is unsafe** for a
   module whose own docstrings need to explain, in prose, what it does
   *not* do — caught during this slice's own test-writing (not a defect
   in the shipped module, a defect in an early draft of one test),
   fixed by switching to AST `Call`-node detection, matching the same
   discipline already used for `SessionLocal`-absence checks in the
   capture-runner review.

## 21. Whether core is ready for Slice 2

**Yes.** Every type Slice 2 (`SourceAssertion` → raw-text claim
extraction, source-family-specific, starting with the MAC extractor) will
need to construct already exists, is proven against the real MSP case,
and is proven adversarially safe against the exact failure class Slice 2
must not reintroduce.

## 22. Recommended exact scope of Slice 2

A narrow, MAC-Granicus-specific adapter
(`app/acquisition/mac_granicus_claim_extractor.py` or folded into the
existing `mac_granicus_extractor.py`) that consumes an already-built
`CandidateFragment` (its `money_values: tuple[ExtractedMoney, ...]`,
`dates: tuple[ExtractedDate, ...]`, `terminology_hits`, `issuers`) and
produces `tuple[Claim, ...]` — reusing `ExtractedMoney.context_label` as
the direct source of `FinancialFact.semantic_role` (already computed,
already proven correct for both MSP amounts in the live pilot), never
re-parsing raw text from scratch. Explicitly **not** in Slice 2's scope:
`SignalCandidate` evaluation (Slice 3), any persistence (Slice 4), any
review-queue mechanism (Slice 5), any Signal promotion (Slice 6) — each
remains its own separately-approved step, per the design doc's own
roadmap.
