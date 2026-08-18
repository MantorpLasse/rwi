# AI-Discovery Evidence Attachment Guard — Design

**Architecture/design only. No database write, no production code, no
model/schema change, no static-site change, no commit, no push, no
deployment.** Baseline: branch `main`, HEAD
`cb8558bfd5fd7b968abaa11259542114c9e48225`.

## 1. Problem statement

Before any AI-discovered document or claim may become airport-bound
evidence in RWI (a `SourceAssertion` with `airport_id` set, a `Signal`, or
any future "Fact"/"Intelligence" record), RWI needs a deterministic answer
to one question:

> **May this discovered document/evidence be attached to this specific
> candidate airport?**

This is a pre-attachment gate, not a reconciliation mechanism — it runs
*before* evidence exists in governed form at all, deciding only whether an
airport identity link is safe to make, never what the evidence *means*.
It must fail closed: when identity is not sufficiently established, no
attachment happens, and nothing is silently guessed.

## 2. Lessons from SFO/MSP

The SFO 2026 EMAS temporal-evidence pilot
(`docs/product/sfo-2026-emas-temporal-evidence-pilot.md`) found a real,
dated, dollar-figure-bearing "Runway Safe + EMAS + sole-source
procurement" document repeatedly surfacing for searches targeting SFO.
The document was a genuine Metropolitan Airports Commission (Minneapolis-
St. Paul) memo about **Runway 30L** — a designation that does not exist
at SFO (SFO's only runways are `1L/19R` and `1R/19L`). The document
matched on **topical keyword overlap** (Runway Safe, EMAS, sole-source,
dollar figures) while containing **zero** SFO-identifying content and, on
close reading, **explicit contradicting content**: a different issuing
agency (Metropolitan Airports Commission, not San Francisco Airport
Commission) and a runway designation structurally impossible at SFO.

Two lessons drive this design directly:

1. **The search query is not evidence.** "SFO" appearing in the query
   that surfaced a document says nothing about what the document itself
   contains. Only content *inside* the candidate document counts.
2. **Runway-designation impossibility is a cheap, deterministic,
   high-value contradiction signal**, entirely computable from data RWI
   already owns (canonical `Runway`/`RunwayEnd` topology) — no AI
   judgment required.

## 3. Existing RWI mechanisms that can be reused

Nothing here is being redesigned from scratch — the guard is a
generalization of patterns RWI has already built and proven:

| Existing mechanism | File | What it already solves | Reused how |
|---|---|---|---|
| Runway/runway-end normalization | `app/services/runway_identity.py` (`normalize_end`, `normalize_pair`, `is_two_ended_pair_shape`, `AmbiguousRunwayDesignationError`) | "04L" vs "4L" formatting differences, source-agnostic, already ICAO-heading-based (not US-specific) | Used as-is for all runway-token normalization in the guard — never reimplemented |
| Canonical topology membership lookup | `app/services/physical_installation_identity_linking.py` (`_ends_by_designation_for_airport`, `resolve_identity`) | Deterministic, fail-closed resolution of a free-text runway-end string against one airport's own canonical `RunwayEnd` set, with explicit `CROSS_AIRPORT`/`AMBIGUOUS`/`UNRESOLVED` outcomes, never guesses | Directly reused for the guard's own topology-membership check — same join pattern, same normalization call, same fail-closed posture |
| Fail-closed airport resolution | `scripts/import_usaspending_grants.py::resolve_airport()` | Identifier (FAA Loc ID) evidence outranks name/location evidence; an organization/recipient name alone is never sufficient to create or confirm airport identity; unresolved cases are preserved, never guessed | The guard generalizes this exact hierarchy (identifier > topology > name/location > nothing) into a reusable, source-agnostic contract; `resolve_airport()` becomes a candidate *caller* of the guard (§12), not something the guard reimplements |
| Evidence preserved even when unattached | `SourceAssertion.airport_id` (nullable) + `import_all()`'s `UNRESOLVED` handling | A grant whose airport can't be established still keeps its evidence (title, description, raw identifiers) rather than being discarded | Directly reused as the guard's own storage contract (§11) — `REJECT`/`REVIEW_REQUIRED`/`INSUFFICIENT` all leave `airport_id = NULL`, never silently drop the source |
| Fragment-level evidence identity | `SourceAssertion.source_locator` / `raw_fragment_hash` / `artifact_identity` | One `Source` (a whole document) can already yield multiple `SourceAssertion` rows, each independently identified | Reused for multi-airport documents (§8, case K) — the guard runs per (fragment, candidate airport), not per whole document |
| One-off, evidence-cited identity corrections | `scripts/correct_allegheny_airport_identity.py`, `scripts/correct_morristown_airport_identity.py` | Historical cases where an organization/authority name was mistakenly treated as airport identity | Confirms the guard's core rule (org/authority name ≠ airport identity) is not new — it already governs the fixed state of these two rows; the guard makes the rule apply *before* ingestion instead of via after-the-fact correction |
| Human-gated reconciliation, immutable audit trail | `app/services/physical_installation_reconciliation.py` (`create_physical_installation_identity`, `record_reconciliation_decision`), `InstallationAssertionLink` (append-only, `actor`+`reason` required) | Canonical identity is never auto-created; every decision is attributable and permanent | The guard sits strictly *before* this layer — it can permit a `SourceAssertion` to carry an `airport_id`, but never creates a `PhysicalInstallationIdentity` itself; that remains exactly as human-gated as it is today |

No part of this design duplicates or weakens any of the above — it fills
the one gap none of them cover: a **document-discovery-time**,
**source-agnostic** identity check, usable before a specialized importer
like `resolve_airport()` even exists for a given evidence type.

## 4. Identity evidence model

Evidence is extracted from one document *fragment* (not necessarily a
whole document — see §8 case K) as a normalized "evidence bag," entirely
independent of how it was extracted (regex, AI, human). Each item belongs
to exactly one category:

**Airport identifiers** — FAA Loc ID, ICAO code, IATA code, or an
equivalent country-specific local identifier, found verbatim in the
fragment.

**Airport names** — canonical name, a known alias, or the airport's known
authority/operator name, found verbatim in the fragment.

**Runway topology tokens** — one or more runway/runway-end designations
found in the fragment, normalized via `normalize_end`/`normalize_pair`.

**Document context** — issuer/publisher organization, a known
procurement-owner/authority, a project or contract number, the source
URL/domain, the document title.

**Location** — city/state (or the equivalent administrative division
outside the US) named in the fragment.

Not every fragment will contain every type — a fragment with zero
extractable evidence of any kind is `INSUFFICIENT_IDENTITY` by
construction (§7), never an error.

## 5. Contradiction model

For a given candidate airport X, contradictory evidence is:

- An airport identifier explicitly present in the fragment that is
  **not** one of X's own identifiers, **and** is a real, resolvable
  identifier (of any known airport, or even an unrecognized-but-
  structurally-valid one — a wrong code is still evidence *against* X
  even if RWI doesn't yet have a canonical row for it).
- An airport name explicitly present that is a known alias of a
  **different** airport than X.
- A runway/runway-end token that (a) does **not** exist in X's own
  canonical topology, **and** (b) is independently corroborated as
  belonging elsewhere — either because it matches another *specific*
  airport's canonical topology, or because the same fragment separately
  names a different airport/authority. A topology token merely absent
  from X's canonical set, with nothing else pointing elsewhere, is
  **not** contradiction on its own (§6) — RWI's own topology data may
  simply be incomplete.
- A city/state/administrative-division explicit in the fragment that
  conflicts with X's own `city`/`state_region`.
- A document issuer that is a **known** authority for a different,
  specific airport (via the issuer→airport reference described in §11).

**Contradiction always wins.** Any one of the above, found against
candidate X, vetoes every positive category found for X in the same
evaluation — this is a deliberate, absolute rule, not a weighted score:
strong contradictory evidence must be able to overrule any amount of weak
positive evidence, exactly as the SFO/MSP case requires (topical
similarity across many keywords still lost to one real contradiction).

## 6. Runway/topology normalization

The guard performs **zero** original runway-string logic. Every token is
passed through the existing `app/services/runway_identity.py` functions
exactly as already used elsewhere in the repository:

- `normalize_end()` for single-end tokens (`"04L"` → `"4L"`).
- `normalize_pair()` / `is_two_ended_pair_shape()` for pair-shaped tokens
  (`"22L/04R"` → `"4R/22L"`).
- Topology membership uses the identical join pattern already proven in
  `physical_installation_identity_linking.py::_ends_by_designation_for_airport()`
  — every `RunwayEnd` for candidate X, normalized, checked for
  intersection with the fragment's normalized tokens.

**Two deliberate asymmetries, both already implicit in the existing
`CROSS_AIRPORT`/`UNRESOLVED` split in `physical_installation_identity_linking.py`:**

1. **Token exists in X's topology** → positive evidence (§4).
2. **Token absent from X's topology, with no other airport corroborated**
   → *not* contradiction, only the *absence* of one possible positive
   signal — RWI's canonical inventory not (yet) covering every airport
   worldwide must never be conflated with "this document is about a
   different airport."
3. **Token absent from X's topology, but a specific different airport is
   independently identified in the same fragment (by name, identifier, or
   issuer) whose own canonical topology *does* contain the token** →
   contradiction (§5) — this is exactly the SFO/MSP shape: SFO lacks
   "30L," and the same document names an authority resolvable to MSP,
   whose canonical topology (once ingested) would contain it.
4. **Compound/pair tokens are stronger evidence than single headings.** An
   exact pair match (e.g., `"1R/19L"`) is far less likely to collide
   across unrelated airports than a bare single heading (`"9"`, `"27"`,
   shared by hundreds of airports worldwide) — the decision contract
   (§7) weights these differently rather than treating "a runway number
   was found" as one undifferentiated signal.

No airport-specific designation hack is introduced anywhere in this
model — every rule above is generic.

## 7. Deterministic decision contract

**Input**: one evidence bag (§4) for one (fragment, candidate airport X)
pair. **Output**: exactly one of five outcomes, plus the specific
evidence items that produced it (never a bare label with no reasoning).

```
evaluate_attachment(candidate_airport, evidence_bag) -> AttachmentDecision(
    outcome: str,
    positive_categories: list[EvidenceItem],   # which categories matched X, and how
    contradicting_evidence: list[EvidenceItem],# what vetoed it, if anything
    reason: str,                               # human-readable, cites the specific items above
)
```

**Algorithm (strict order — contradiction is checked unconditionally
first):**

1. **Contradiction check** (§5). Any match → `REJECT_CROSS_AIRPORT`.
   Stop; positive evidence is still recorded in the decision object for
   audit, but cannot change the outcome.
2. Count **independent positive categories** that matched X (§4:
   identifier, name, topology, issuer, location — each category counts
   once regardless of how many individual tokens within it matched;
   restating the same fact five times in an AI summary is still one
   category, not five — directly enforces the AI boundary in §10).
   - An **identifier match** or a **compound/pair topology match**
     (§6.4) counts as a "strong" category on its own.
   - A **single-heading topology match**, a bare **name** match, or a
     bare **location** match counts as a "weak" category alone.
3. **≥1 strong category, no contradiction** → `ATTACH_CONFIRMED`.
4. **≥2 independent categories (any strength), no contradiction, all
   agreeing on the same single candidate X** → `ATTACH_CONFIRMED`.
5. **Exactly 1 weak category, no contradiction** → `ATTACH_PROVISIONAL`.
6. **Positive evidence found, but it agrees equally well with more than
   one candidate airport (multi-candidate ambiguity), no contradiction
   ruling any of them out** → `REVIEW_REQUIRED`.
7. **No positive evidence and no contradiction** → `INSUFFICIENT_IDENTITY`.

**What each outcome permits downstream** (the guard itself creates
nothing — this is a contract for callers):

| Outcome | May create `SourceAssertion` with `airport_id` set? | May feed human-gated reconciliation (`PhysicalInstallationIdentity`)? | May generate/promote a `Signal`? |
|---|---|---|---|
| `ATTACH_CONFIRMED` | Yes | Yes, as candidate input — reconciliation itself stays human-gated, unchanged | Yes, at normal confidence |
| `ATTACH_PROVISIONAL` | Yes, `evidence_quality="unverified_candidate"` | No — insufficient to propose a physical-identity candidate | No — evidence only, never an investor-facing claim on one weak signal |
| `REVIEW_REQUIRED` | No (`airport_id = NULL`), candidates recorded in `reason`/raw fields | No | No |
| `REJECT_CROSS_AIRPORT` | No (`airport_id = NULL`), contradiction recorded in `reason` | No | No |
| `INSUFFICIENT_IDENTITY` | No (`airport_id = NULL`) | No | No |

This mirrors, and generalizes, the existing three-way split already live
in `resolve_airport()` (`RESOLVED_EXISTING`/`RESOLVED_NEW`/`UNRESOLVED`) —
see §12 for how that function becomes a caller of this contract rather
than a parallel implementation.

## 8. Worked examples

| # | Case | Evidence found | Outcome | Why |
|---|---|---|---|---|
| A | SFO/MSP false-positive (the pilot's own case) | Candidate X=SFO. Fragment contains "Metropolitan Airports Commission" (issuer resolvable to MSP, not SFO) + "Runway 30L" (absent from SFO's topology, present in MSP's) | `REJECT_CROSS_AIRPORT` | Two independent contradiction signals (issuer + topology-elsewhere) veto everything, including "EMAS"/"Runway Safe"/dollar-figure keyword overlap, which are not identity evidence at all |
| B | Genuine SFO Runway 1R/19L evidence (FAA Construction Impact Report) | Candidate X=SFO. Fragment contains "SFO" (identifier) + "RWY 1R/19L" (compound pair, matches SFO's own topology) | `ATTACH_CONFIRMED` | Identifier match alone is already a strong category (step 3); the pair-topology match independently corroborates it |
| C | BOS evidence, public protected-direction naming ("Runway 22R") | Candidate X=BOS. Fragment issued by Massport (known BOS authority) + names "Runway 22R," which **is** a real canonical `RunwayEnd` at BOS (the reciprocal of `4L`, per BOS's own topology) | `ATTACH_CONFIRMED` | Issuer match + topology-membership match (the guard checks *membership*, not *which specific NASR physical assertion it corresponds to* — that finer distinction is the separate, already-built, human-gated protected-direction reconciliation layer, not this guard's job) |
| D | ORH dual physical/protected naming ("Runway 29 Departure EMAS (R/W 11 End)") | Candidate X=ORH. Both "29" and "11" are real canonical RunwayEnds at ORH; issuer is MPA (ORH's authority) | `ATTACH_CONFIRMED` | Issuer + two independently-matching topology tokens |
| E | USAspending grant with embedded FAA Loc ID | Candidate airport resolved via `LOC_ID_PATTERN` match | `ATTACH_CONFIRMED` | An identifier match is the strongest single category by design (step 3) — this is exactly `resolve_airport()`'s existing `RESOLVED_EXISTING`/`RESOLVED_NEW` behavior, unchanged in effect |
| F | USAspending grant, city/state only (no Loc ID), unique existing Airport match | One weak category (location) only | `ATTACH_PROVISIONAL` | **This differs from `resolve_airport()`'s current behavior**, which treats a unique city/state match as a full resolution today. Flagged explicitly in §13 as a deliberate design tension requiring a human decision before any retrofit, not silently changed here |
| G | Allegheny historical recipient-name failure | Recipient/organization name only ("Allegheny County Airport Authority"), no identifier, no topology, no unique city/state match at the time | `INSUFFICIENT_IDENTITY` (or `REVIEW_REQUIRED` if another Airport shared the city/state) | An organization/recipient name alone is never a positive-evidence category in this model — matches the fix already applied via `correct_allegheny_airport_identity.py`, generalized to apply before ingestion rather than after |
| H | Morristown historical recipient-name failure | Same shape as G | `INSUFFICIENT_IDENTITY` | Same reasoning as G |
| I | Valid airport identity, no runway reference | Candidate X. Fragment names X explicitly ("San Francisco International Airport" / "SFO"), no runway token at all | `ATTACH_PROVISIONAL` (name alone) or `ATTACH_CONFIRMED` if an explicit identifier is also present | An airport-level claim (e.g., a budget figure with no runway-end specificity) may legitimately need no topology evidence at all — sufficiency should also depend on the assertion's own claim type (an airport-wide claim vs. a runway-end-specific claim), noted as a refinement in §13 |
| J | Valid runway designation, weak airport identity | Fragment names "Runway 1R/19L" but no airport name/identifier/issuer at all | `ATTACH_PROVISIONAL` if only one existing RWI airport has that exact pair; `REVIEW_REQUIRED` if more than one canonical airport shares it | A compound-pair match is strong on its own only when it is *unique* across RWI's own canonical set; uniqueness is checked, never assumed |
| K | Document mentioning multiple airports | A regional capital bill naming both X and Y, each with their own identifiable sentence/fragment | Evaluated **per fragment**: `ATTACH_CONFIRMED` for X on X's own fragment, independently `ATTACH_CONFIRMED`/`REJECT`/etc. for Y on Y's fragment | The guard is never invoked once per whole `Source` — it is invoked once per (fragment, candidate), reusing `SourceAssertion`'s existing `source_locator`/`raw_fragment_hash` granularity so two claims in one document never contaminate each other's decision |

## 9. International readiness

**Universal, already built or directly reusable, no U.S. dependency:**

- The decision algorithm itself (§7) — categories, veto logic,
  strong/weak weighting — references no country, language, or agency.
- Runway-designation normalization (`runway_identity.py`) — ICAO
  heading/suffix numbering is the global civil-aviation standard, not a
  US convention; this module is already usable for any country's
  runways as-is.
- Topology-membership checking against canonical `Runway`/`RunwayEnd` —
  works identically for any airport once its canonical inventory exists
  in RWI, regardless of country.
- ICAO codes — already a global 4-letter identifier, already a first-
  class `Airport.icao_code` field.

**U.S.-specific, must stay confined to the *extraction* layer, never
baked into the guard itself:**

- FAA Loc ID pattern-matching (`LOC_ID_PATTERN` in
  `import_usaspending_grants.py`) — a US-only identifier shape; the
  guard's own "identifier" category must accept *any* recognized
  identifier type (ICAO/IATA/FAA-LID/a future country-specific code),
  never assume FAA-LID is the only kind.
- USAspending's specific English-language beneficiary-sentence regex
  (`BENEFICIARY_PATTERN`) — belongs entirely to that one importer's
  extraction step, never to the guard.
- `Airport.city`/`state_region` as two free-text English fields, matched
  via `ilike` — the two-column shape is fine internationally (province,
  région, prefecture, etc. all fit), but the *matching/alias* logic
  (accents, transliteration, administrative-division naming conventions)
  is not designed here and is a real, open gap (§13).
- English airport-name aliasing — a non-English name/alias table is
  extraction-layer work (AI-assisted translation is explicitly permitted
  there, §10), never something the guard computes itself.
- The issuer→airport reference table (§11) is architecturally universal
  (any country's authority can be a row) but is currently seeded with
  only US examples — extending it internationally requires no redesign,
  only more rows.

**Verdict**: the guard's *decision core* is already country-agnostic by
construction; only the *evidence-extraction* adapters feeding it are
U.S.-shaped today, and the design keeps them cleanly separated so
international support is additive, not a rewrite.

## 10. AI / deterministic boundary

**AI MAY:**
- Discover candidate documents/URLs via web search or any other channel.
- Extract candidate airport names/codes/runway tokens/issuer names into
  the normalized evidence-bag shape (§4).
- Translate native-language terms into that same normalized shape.
- Summarize document content for human review.
- Propose a candidate airport X for a fragment — this proposal is itself
  just another input to the deterministic guard, never a decision.

**AI MUST NOT:**
- Decide the attachment outcome itself — no model judgment call ever
  substitutes for the algorithm in §7.
- Silently override contradictory canonical identity (e.g., reasoning
  "it's probably still about SFO" despite a contradicting identifier).
- Invent an airport mapping for an unfamiliar code/name without an actual
  canonical or reference-table match.
- Convert its own search-relevance ranking into identity evidence — a
  document being the AI's top result for a query naming airport X is
  **not** evidence the document is about X (the single literal lesson of
  §2).
- Create or promote a `Signal`, `PhysicalInstallationIdentity`, or any
  future "Intelligence" record directly — those stay exactly as
  human-gated as they are today; the guard's most permissive outcome
  (`ATTACH_CONFIRMED`) only ever authorizes an evidence-level
  `SourceAssertion`.
- Publish an investor-facing claim because several *weak, uncorrelated*
  clues agree — §7 step 2's category-counting rule (restating one fact
  five ways is still one category) exists specifically to block this.

**Where deterministic validation takes over**: immediately after AI (or
any other extractor) produces the normalized evidence bag. Everything
from that point — contradiction checking, topology membership, category
counting, the final outcome — is pure, deterministic, unit-testable code,
identical regardless of which crawler, agent, or language produced the
input.

## 11. Storage/auditability recommendation

**No schema change is required for the dominant path.** `ATTACH_CONFIRMED`
and `INSUFFICIENT_IDENTITY`/`REJECT_CROSS_AIRPORT` map cleanly onto the
existing, already-proven pattern: `SourceAssertion.airport_id` set (or
left `NULL`), `raw_airport_identifier`/`raw_airport_name` carrying what
was actually found, `evidence_quality`/`review_state` carrying source
confidence — exactly the shape `import_all()`'s `UNRESOLVED` handling
already uses today.

**Two small, additive, non-breaking columns are recommended** to avoid
silently discarding the guard's own reasoning — the same "never discard
evidence" principle already applied everywhere else in RWI, not a new
concept:

- `SourceAssertion.identity_guard_decision` (nullable string) — one of
  the five outcome names (§7), queryable/auditable, deliberately
  **separate** from `evidence_quality` (which describes *source*
  reliability, not the *attachment decision* — conflating the two would
  blur two different concepts the way `evidence_quality` and
  `review_state` are already deliberately kept separate today).
- `SourceAssertion.identity_guard_reason` (nullable text) — free text
  citing the specific contradicting or supporting evidence, mirroring the
  existing, already-proven `InstallationAssertionLink.reason` convention.

**Multi-candidate cases** (`REVIEW_REQUIRED` with more than one plausible
airport) do **not** need a new list-shaped column — reuse the existing
one-row-per-fragment pattern: a single `SourceAssertion` with
`airport_id = NULL` and both candidates named in
`raw_airport_identifier`/`identity_guard_reason` is sufficient for the
common case; if per-candidate structure is ever needed, it can reuse
`SourceAssertion`'s existing fragment-identity fields (§8 case K) rather
than a new table.

**Explicitly not recommended**: a new dedicated "AttachmentDecision"
table. The two additive columns above are the minimum honest cost of full
auditability; a whole new table would over-engineer a need `SourceAssertion`
already almost entirely satisfies.

## 12. Proposed implementation slices (not built in this task)

1. **Pure decision function only.** A new, standalone, read-only service
   module (e.g. `app/services/evidence_attachment_guard.py`), exposing
   `evaluate_attachment()` (§7) and the evidence-bag dataclass, reusing
   `runway_identity.py` and the topology-lookup pattern from
   `physical_installation_identity_linking.py` exactly as documented
   above. No ingestion integration yet. Fully unit-testable in isolation
   using synthetic fixtures for exactly the A–K cases in §8 — this alone
   can be built and proven correct without touching any live pipeline.
2. **Additive schema columns** (§11), following the existing
   `ensure_source_external_id_column()`-style idiom (add-column-if-
   missing, safe to run repeatedly) — wired into a **new** discovery
   pathway first, never retrofit onto the already-working NASR/USAspending
   pipelines in the same slice, to avoid destabilizing proven ingestion.
3. **Retrofit `resolve_airport()`** to call the shared guard instead of
   its own bespoke logic, as an explicit, reviewed change — this is
   exactly where the §8 case F design tension (a bare city/state match
   currently resolves fully; the new model would demote it to
   `ATTACH_PROVISIONAL`) must be decided by a human, not silently changed.
4. **Build the first issuer→airport reference table**, seeded only with
   authorities already encountered in this repository's own research
   (Massport → BOS/ORH, MAC → MSP, SFO Airport Commission → SFO, MPA →
   ORH/Worcester-area contracts) — small, explicit, extensible, never
   auto-scraped.
5. **Only after 1–4 are stable**: wire an actual AI-discovery pipeline
   (n8n or otherwise) through the guard as its mandatory pre-attachment
   gate — the guard must be a prerequisite dependency of that pipeline,
   never something the pipeline can bypass.

## 13. Risks / unresolved questions

- **Incomplete canonical topology risk.** A real, foreign, or simply
  not-yet-ingested airport will fail topology checks not because evidence
  is wrong but because RWI's own data is incomplete — §6 already encodes
  this as "absence ≠ contradiction," but this asymmetry must be tested
  explicitly, not merely assumed correct.
- **Issuer/alias table maintenance.** A manually curated issuer→airport
  table doesn't scale globally on its own; ownership and update process
  are undecided.
- **Multi-airport metro regions.** Documents legitimately discussing
  several nearby airports (e.g., "Bay Area airports," "New York airports")
  may not fragment as cleanly as case K assumes — needs more real
  examples before the fragment-boundary assumption is trusted broadly.
- **The "≥2 categories ⇒ CONFIRMED" / "1 strong category ⇒ CONFIRMED"
  thresholds are judgment calls**, not derived from a larger evidence
  base — worth revisiting once the guard has processed more real cases,
  not hardened prematurely.
- **Staleness.** A guard decision computed against today's canonical
  topology could become wrong if that topology is later corrected (e.g.,
  a designation renumbering) — no re-evaluation trigger is designed here.
- **§8 case F tension is unresolved by design** — deliberately left as an
  explicit human decision (§12 slice 3), not resolved in this document.
- **Assertion-type-aware sufficiency** (§8 case I) — whether an
  airport-wide claim should require less evidence than a runway-end-
  specific claim is noted as a plausible refinement, not designed in
  full here.

## Confirmation: code/DB/site unchanged

No file under `app/`, `scripts/`, `tests/`, or the static-export pipeline
was modified. No SQL write was executed — this task performed no database
interaction at all (pure repository reading). No static site was
rebuilt.
