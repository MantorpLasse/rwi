# AI-Discovery Candidate Fragment — Core Implementation Report

Implements the first pure core slice of the approved candidate-envelope
architecture (`docs/architecture/ai-discovery-candidate-envelope-lifecycle.md`).
**This slice is `CandidateFragment` + the deterministic
`CandidateFragment → EvidenceBag` adapter only** — no database write, no
schema change, no `Source`/`SourceAssertion` persistence, no ingestion
integration, no crawler, no n8n, no Signal creation, no public UI change,
no commit, no push. Baseline: branch `main`, HEAD
`386bb884584853f3a7a4bf20a9ff64428a51c2a7`.

## 1. Architecture basis

Built directly on two already-existing, already-real precedents rather
than invented fresh:

- **`app/acquisition/faa_emas_parser.py::FAAEmasCandidate`/`FAAEmasParseReport`**
  — the repository's own existing "extraction boundary" shape: raw
  extracted fields, a `source_locator`, fail-closed typed errors, a
  versioned parser identity. `CandidateFragment` generalizes this shape
  (§14 below has the exact comparison).
- **`app/services/evidence_attachment_guard.py`** (committed,
  `386bb88`) — `CandidateFragment`'s entire purpose is to feed this,
  unmodified, via one pure adapter function.

Consistent with `docs/architecture/ai-discovery-candidate-envelope-lifecycle.md`
§6: `CandidateFragment` begins at the **fragment** level only —
`CandidateDocument`/`CandidateResource` are **not** introduced as new
objects; document/resource identity remains `AcquisitionSource`/
`AcquisitionRun`/`Snapshot`, entirely untouched by this slice (not even
imported).

## 2. `CandidateFragment` contract

`app/services/discovery_candidate_fragment.py`, a frozen dataclass with
three required fields and everything else optional, defaulting to empty:

```python
CandidateFragment(
    artifact_identity: str,   # REQUIRED - which parent document/Snapshot
    source_locator: str,      # REQUIRED - where within it
    raw_text: str,            # REQUIRED - original extracted text, verbatim
    airport_identifiers: frozenset[str] = frozenset(),
    airport_names: frozenset[str] = frozenset(),
    locations: frozenset[str] = frozenset(),
    runway_ends: frozenset[str] = frozenset(),
    runway_pairs: frozenset[str] = frozenset(),
    issuers: frozenset[str] = frozenset(),
    contradicting_names: frozenset[str] = frozenset(),
    contradicting_issuers: frozenset[str] = frozenset(),
    contradicting_locations: frozenset[str] = frozenset(),
    project_identifiers: frozenset[str] = frozenset(),
    contract_identifiers: frozenset[str] = frozenset(),
    money_values: tuple[ExtractedMoney, ...] = (),
    dates: tuple[ExtractedDate, ...] = (),
    terminology_hits: frozenset[str] = frozenset(),
    language: str | None = None,
    document_title: str | None = None,
    url: str | None = None,
    publication_date: date | None = None,
    parser_identifier: str | None = None,
    extracted_at: datetime | None = None,
    discovery_context: DiscoveryContext | None = None,
)
```

Fails closed (raises `CandidateFragmentError` with a typed
`CandidateFragmentErrorCode`, mirroring `FAAEmasParseErrorCode`'s own
convention) if any of the three required fields is empty/whitespace-only
— `MISSING_ARTIFACT_IDENTITY`, `MISSING_SOURCE_LOCATOR`, `EMPTY_RAW_TEXT`.

**Field classification, exactly per the task's own framework:**

| Class | Fields |
|---|---|
| REQUIRED | `artifact_identity`, `source_locator`, `raw_text` |
| OPTIONAL EXTRACTED → mapped to `EvidenceBag` | `airport_identifiers`, `airport_names`, `locations`, `runway_ends`, `runway_pairs`, `issuers`, `contradicting_names`, `contradicting_issuers`, `contradicting_locations` |
| OPTIONAL EXTRACTED → extraction facts only, never guard input | `project_identifiers`, `contract_identifiers`, `money_values`, `dates`, `terminology_hits`, `language` |
| AUDIT_ONLY | `document_title`, `url`, `publication_date`, `parser_identifier`, `extracted_at`, `discovery_context` |
| NOT_NEEDED (deliberately omitted) | a separate `organizations` field (§5) |

## 3. Fragment identity

`(artifact_identity, source_locator, fragment_hash)`, exposed as the
`.identity` property — reuses exactly the shape already described in the
lifecycle design (§6/§17 there): `SourceAssertion.source_locator`/
`raw_fragment_hash`/`artifact_identity` semantics, not a new scheme.
`fragment_hash` (a separate `.fragment_hash` property) is the SHA-256 of
`raw_text`, computed once at construction (`__post_init__`, matching the
normalize-once posture `CandidateAirport`/`EvidenceBag` already use).
Proven deterministic by test: identical `(artifact_identity,
source_locator, raw_text)` always yields identical `.identity`; changing
any one of the three changes it.

## 4. Raw/original evidence preservation

`raw_text` is stored and returned completely unmodified — no
normalization, no trimming, no case-folding happens to it anywhere in
this module (normalization is entirely the guard's job on the
*extracted* fields, never applied to the fragment's own source text).
Proven directly by `test_raw_text_preserved_verbatim` (including leading/
trailing whitespace preserved byte-for-byte).

**No `translated_text` field was added.** Per the lifecycle design's own
§19 conclusion (re-examined here, not merely assumed): a dedicated
translation field is deferred until a real future slice needs to show a
translation to a human reviewer — extraction may translate in-memory to
help itself find entities, but must always place the *original-language*
text in `raw_text`. The international fixture (§13 below) proves this
directly: the stored `raw_text` is the original Japanese string,
unmodified.

## 5. Extracted field model

Maps one-to-one onto `EvidenceBag`'s own five identity categories
(`identifiers`/`names`/`runway_ends`+`runway_pairs`/`issuers`/`locations`)
plus its three `contradicting_*` fields — no new categories invented.

**`organizations` was deliberately not added as a separate field** — it
would be a pure alias of `issuers` (issuer/publisher/authority), and
adding it would be exactly the kind of speculative duplicate field the
task brief warns against. Documented directly in the module's own
docstring, not just here.

**Extraction facts with no `EvidenceBag` equivalent** (`project_identifiers`,
`contract_identifiers`, `money_values`, `dates`, `terminology_hits`,
`language`) are carried on `CandidateFragment` for later correlation/
review use only — **never resolved to a database row**, never passed to
the guard. `project_identifiers`/`contract_identifiers` are the one
partial exception: the adapter joins them (sorted, comma-separated) into
`EvidenceBag`'s existing singular `project_number`/`contract_number`
audit-only fields (never used in any guard decision), purely so a
resulting `AttachmentDecision.reason` can mention them for a human
reader.

## 6. Money/date representation

Two small, minimal dataclasses, deliberately not a generic NLP ontology
(per instruction):

```python
ExtractedMoney(raw_text: str, numeric_value: Decimal | None, currency: str | None, context_label: str | None)
ExtractedDate(raw_text: str, normalized_date: date | None, semantic_role: str | None)
```

**The exact SFO 2026 $40M lesson is encoded directly**: `context_label`/
`semantic_role` default to `None` and are **never inferred** by this
module — a dollar figure found in a fragment stays semantically
`None`-labeled (an extraction fact with unknown role) unless upstream
extraction *explicitly* determined what it is the cost of (e.g.
`"advance_deposit"`, matching the real MSP memo's own $1,590,000 figure).
`test_money_extraction_shape_preserved_and_not_auto_interpreted` asserts
this directly, citing the SFO pilot by name in its own docstring. Neither
type is ever mapped into `EvidenceBag` — the guard has no financial
reasoning at all, and this module does not add any.

## 7. Project/contract identifier representation

`project_identifiers`/`contract_identifiers`: plain `frozenset[str]` of
raw strings (e.g. `"L1633"`, `"W306"`) — no DB lookup, no resolution to
an existing `Project`/`Signal` row anywhere in this module. Preserved for
later correlation only.

## 8. Search-context firewall verdict

**Structurally enforced, not just conventionally observed.**
`DiscoveryContext` (search query, discovery channel, seed airport,
discovery timestamp) is isolated in its own dataclass, and
`candidate_fragment_to_evidence_bag()` never references
`fragment.discovery_context` anywhere in its body. Proven three ways:

1. `test_search_context_excluded_from_evidence_bag` — builds a fragment
   whose `discovery_context.seed_airport == "SFO"` and whose real
   evidence concerns MSP, asserts the string `"SFO"` appears **nowhere**
   in any field of the resulting `EvidenceBag`.
2. `test_evidence_bag_identical_regardless_of_discovery_context` — three
   fragments, identical evidence, three different (including `None`)
   `discovery_context` values, all three produce a byte-identical
   `EvidenceBag` (`==`).
3. `test_adapter_never_reads_discovery_context_attribute` — the
   strongest proof: substitutes a `discovery_context` object whose
   `__getattr__` raises `AssertionError` on **any** attribute access, and
   confirms the adapter still succeeds — meaning no code path in the
   adapter can possibly read it, not merely that it happens not to in
   today's implementation.

## 9. `EvidenceBag` adapter behavior

`candidate_fragment_to_evidence_bag(fragment) -> EvidenceBag` — pure,
total (never raises for any valid `CandidateFragment`), one-to-one field
mapping (§5), no I/O, no Airport resolution, no confidence score, no
topology inference, no contradiction invented (contradiction fields are
passed through exactly as supplied — never classified by this module
itself, per instruction). `_join_or_none()` is the only non-trivial
transformation, and it is audit-text-only (never used in the decision).

## 10. SFO/MSP result

`test_sfo_msp_fragment_rejects_for_sfo`: a realistic fragment (Metropolitan
Airports Commission, Runway 30L, Runway Safe/EMAS terminology, a real
dollar figure with `context_label="advance_deposit"`, and a
`discovery_context` naming SFO as the search's seed airport) — built with
`contradicting_issuers` (not plain `issuers`) reflecting what upstream
orchestration would have already resolved before evaluating against SFO
specifically — reaches `REJECT_CROSS_AIRPORT`. This exposed and fixed one
real fixture bug during development (§17).

## 11. MSP result

`test_sfo_msp_fragment_confirms_for_msp`: the same underlying facts
(issuer + runway, this time in the *positive* `issuers` field, correctly
reflecting evaluation against the airport they actually describe) reach
`ATTACH_CONFIRMED`.

## 12. Genuine SFO result

`test_genuine_sfo_fragment_confirms`: identifier `"SFO"` + runway pair
`"1R/19L"` → `ATTACH_CONFIRMED`.

## 13. BOS / ORH results

`test_bos_massport_runway_22r_fragment_confirms` and
`test_orh_mpa_dual_naming_fragment_confirms`: issuer + topology-membership
→ `ATTACH_CONFIRMED` for both, exactly matching the guard's own
`test_case_C_*`/`test_case_D_*`. **Physical vs. protected direction is
never conflated** — `CandidateFragment`/the adapter carry only raw
runway-end **strings**; whether `"22R"` is BOS's NASR-reported physical
location or Massport's own public/protected-direction label is
semantically irrelevant at this layer (exactly as the guard itself
already established) — that finer distinction belongs entirely to the
separate, human-gated `PhysicalInstallationIdentity` reconciliation
layer, untouched by anything in this slice.

## 14. `FAAEmasCandidate` compatibility verdict

| | `FAAEmasCandidate` | `CandidateFragment` |
|---|---|---|
| Scope | One FAA Tableau installation record | Any discovered document fragment, source-agnostic |
| Raw fields | `airport_identifier_raw`, `airport_name_raw`, `city_raw`, `state_raw`, `installation_type_raw`, … | `airport_identifiers`, `airport_names`, `locations`, `runway_ends`/`pairs`, `issuers`, … — generalized categories matching `EvidenceBag`, not FAA-Tableau-column-specific |
| Fragment identity | `source_locator` (string) | `source_locator` + `artifact_identity` + `fragment_hash` (the same concept, made explicit/deterministic) |
| Fail-closed errors | `FAAEmasParseErrorCode` (Tableau-specific: `EXPECTED_TABLEAU_STRUCTURE_MISSING`, etc.) | `CandidateFragmentErrorCode` (generic: missing-required-field only) |
| Money/dates/project IDs | Not modeled | Modeled (§6/§7) — new territory `FAAEmasCandidate` never needed |
| Airport resolution | None (feeds a separate, not-yet-built promotion path) | None (feeds the guard) |

**What semantics are reused**: the raw-fields-plus-`source_locator`-plus-
fail-closed-typed-errors *shape*, directly.
**What `CandidateFragment` generalizes**: FAA/Tableau-specific raw column
names become the same five generic categories the guard already uses;
adds money/date/project-identifier facts `FAAEmasCandidate` had no need
for.
**What `FAAEmasCandidate` remains responsible for**: exactly what it does
today — the FAA EMAS Tableau parsing boundary, entirely untouched,
**not retrofitted in this slice**, per instruction.
**Would a future `FAAEmasCandidate → CandidateFragment` adapter be
feasible?** Yes, straightforwardly — `FAAEmasCandidate.airport_identifier_raw`
→ `CandidateFragment.airport_identifiers={value}`, `.source_locator` →
`.source_locator` directly, `.source_record_raw` could seed
`artifact_identity`/`raw_text` — but this is future work, not implied or
started here.

## 15. Determinism/purity verdict

- **`CandidateFragment` and its helper dataclasses (`ExtractedMoney`,
  `ExtractedDate`, `DiscoveryContext`) are all `frozen=True`** — proven by
  `test_candidate_fragment_and_helpers_are_frozen` (asserts
  `dataclasses.FrozenInstanceError` on attempted mutation of each).
- **Same input → same fragment identity**: `test_deterministic_fragment_hash`,
  `test_deterministic_fragment_identity`.
- **Same fragment → same `EvidenceBag`**: `test_adapter_is_deterministic`
  (5 repeated calls, identical `EvidenceBag` and identical downstream
  guard outcome every time).
- **No DB query, no DB write, no network access**: `test_no_db_or_network_imports`
  — AST-based (`ast.Import`/`ast.ImportFrom` node inspection, not string
  grep, matching the guard core report's own corrected convention),
  checks for `sqlalchemy`/`httpx`/`requests`/`app.database`/`app.models`.
- **No filesystem write, no model persistence**: structurally true — no
  file/DB-writing call appears anywhere in the module (`hashlib` and
  `dataclasses`/`enum`/`datetime`/`decimal` are the only non-project
  imports).
- **Inputs not mutated**: `test_adapter_does_not_mutate_its_input`
  (deep-copies the fragment before calling the adapter, asserts
  unchanged afterward — also structurally guaranteed by `frozen=True`).

## 16. Tests

`tests/test_discovery_candidate_fragment.py` — **34 passed**, covering
all 26 required scenarios (§21 of the task) plus 3 additional fail-closed
construction tests and one additional structural search-context proof.

Combined focused run (`test_discovery_candidate_fragment.py` +
`test_evidence_attachment_guard.py` + `test_runway_identity_normalization.py`):
**96 passed** — zero regression in the guard or its runway-normalization
dependency.

Full suite: **709 passed** (675 pre-existing baseline + 34 new) — zero
regressions anywhere in the repository.

`py_compile` on both new files: clean. `git diff --check`: exit 0.

## 17. Limitations

- **Corrected during the final commit review**
  (`RWI_DISCOVERY_CANDIDATE_FRAGMENT_CORE_REVIEW_COMMIT_PUSH`): a private
  helper function, `_freeze_strings()`, was defined in the original
  implementation but never called anywhere in the module — dead code left
  over from drafting. Removed; no behavior change (confirmed by an
  unchanged focused-test and full-suite pass count before and after).
- **One real fixture bug found and fixed during this task's own
  development**: the initial SFO/MSP-rejection fixture placed
  "Metropolitan Airports Commission" in the plain `issuers` field rather
  than `contradicting_issuers`. Since the adapter correctly never
  auto-classifies a found issuer as contradicting a given candidate
  (§9 — free-text issuer strings aren't self-verifying, exactly per
  instruction), this produced `INSUFFICIENT_IDENTITY` instead of the
  expected `REJECT_CROSS_AIRPORT`. **Not a bug in the module** — a
  correct demonstration that contradiction classification is genuinely
  an upstream responsibility this adapter must not perform itself. Fixed
  by correcting the test fixture, not the module.
- **One `CandidateFragment` cannot itself carry different contradiction
  classifications for different candidate airports.** The SFO-rejection
  and MSP-confirmation cases are therefore modeled as **two separate**
  fixtures (mirroring exactly how the guard's own existing test suite
  already does this for the identical scenario) rather than one shared
  fragment evaluated twice. A real orchestration layer would need its
  own per-candidate classification step (an issuer→airport reference
  table lookup, per the lifecycle design's §11) before building the
  final `EvidenceBag` for a specific candidate evaluation — this module
  does not attempt that step, by design.
- **No `content_type`/fragment-kind field** (e.g. distinguishing "full
  document" from "search snippet," which the lifecycle design's §6
  recommends conceptually) was added — no required test case needed it,
  and adding it speculatively would violate the "no speculative fields"
  instruction. Noted as a plausible future field, not built.
- **No alternate-airport-topology corroboration fields**
  (`alternate_airport_runway_ends`/`pairs`, present on `EvidenceBag`
  itself) are threaded through `CandidateFragment` — none of the required
  worked cases needed the strongest form of runway-elsewhere
  corroboration; `contradicting_issuers` alone was sufficient for the
  SFO/MSP case, matching the guard's own existing test coverage.

## 18. Recommended next persistence/integration slice

Unchanged from the lifecycle design's own §24 ordering, now with slice 1
complete: **slice 2** — the two additive `SourceAssertion` columns
(`identity_guard_decision`/`identity_guard_reason`), following the
existing `ensure_source_external_id_column()`-style add-if-missing idiom,
wired into a **new** discovery pathway first, never a retrofit of NASR/
USAspending in the same slice. Only after that: **slice 3**, one
controlled, narrowly-scoped `AcquisitionProvider` for one real, specific
source, proving the full chain (acquisition → this slice's extraction
envelope → guard → `SourceAssertion`) end to end for one real document
before generalizing further.
