# AI-Discovery Candidate Envelope & Lifecycle — Design

**Design / repository-investigation only. No implementation, no schema
change, no database write, no migration, no ingestion integration.**
Baseline: branch `main`, HEAD `386bb884584853f3a7a4bf20a9ff64428a51c2a7`.

## 1. Purpose

Define the architectural object and lifecycle between *"the discovery
system found something"* and *"RWI is allowed to create governed evidence
from it,"* building on the now-committed, invariant deterministic
Evidence Attachment Guard
(`docs/architecture/ai-discovery-evidence-attachment-guard.md`,
`app/services/evidence_attachment_guard.py`). This document is the
missing layer **above** the guard (how a real document becomes an
`EvidenceBag`) and **below** it (what a decision is allowed to create).

## 2. Current repository reality

A full inventory was performed before proposing anything, specifically to
avoid inventing a parallel evidence architecture. The finding: **RWI
already has almost every piece this design needs — just narrower in
scope than the product's future ambition, and not yet connected end to
end.**

### 2a. Document/resource acquisition — already built, under-used

`app/models/acquisition.py` defines a complete, working, content-addressed
acquisition pipeline, currently exercised only for FAA sources:

- **`PublishingSource`** — who publishes a feed (e.g. "Federal Aviation
  Administration").
- **`AcquisitionSource`** — a stable, human-named acquisition target:
  `key` (unique, immutable once set — enforced by a `before_update` event
  listener), `canonical_url`, `acquisition_type`, `expected_media_type`,
  `active`.
- **`AcquisitionRun`** — one fetch attempt: `request_url`/`final_url`
  (redirect-aware), `http_status`, `content_type`, `response_headers`,
  `duration_seconds`, `provider_version`, a typed `AcquisitionRunStatus`
  enum (`RUNNING`/`SUCCESS`/`NO_CHANGE`/`UNAVAILABLE`/`BLOCKED`/
  `RATE_LIMITED`/etc.), immutable once completed.
- **`Snapshot`** — content-addressed, immutable, undeletable: `payload`
  (raw bytes), `sha256` + `byte_size` (unique together per
  `AcquisitionSource` — this **is** the existing document-level dedup
  mechanism), `media_type`, `retrieved_at`.

`app/services/acquisition.py::AcquisitionService.acquire()` orchestrates
all of this generically: given any `AcquisitionSource` and a provider
implementing `.retrieve() -> AcquisitionPayload` (`app/acquisition/faa.py`),
it fetches, hashes, dedupes against existing `Snapshot`s for that source,
records `SUCCESS` vs. `NO_CHANGE`, and fails closed into typed
`AcquisitionRunStatus` values on error. **The provider interface is
already generic** — only the two providers that exist today
(`FAAAcquisitionProvider`, `FAATableauAcquisitionProvider`) are
FAA-specific, not the machinery around them. `app/scripts/capture_faa_emas.py`
is the one real, gated (`--allow-live-network --allow-database-write`)
caller in production use.

### 2b. Snapshot → candidate parsing — a real precedent for extraction

`app/acquisition/faa_emas_parser.py::FAAEmasSnapshotParser` is exactly
the "extraction boundary" shape this design needs, already built once:
`.parse(snapshot_bytes, media_type) -> FAAEmasParseReport`, fails closed
with typed error codes (`EMPTY_PAYLOAD`, `MALFORMED_PAYLOAD`,
`EXPECTED_TABLEAU_STRUCTURE_MISSING`, `REQUIRED_AIRPORT_IDENTIFIER_MISSING`,
`DUPLICATE_SOURCE_LOCATOR`), versioned (`PARSER_VERSION`), and produces
`FAAEmasCandidate` objects — frozen dataclasses carrying **raw** extracted
fields (`airport_identifier_raw`, `airport_name_raw`, `city_raw`,
`state_raw`, …), a `source_locator` (fragment identity within the
document), and `source_record_raw` (the exact bytes of just that record).
This module currently only *rejects* every real payload it's been given
so far (no observed VizQL fixture exists yet) — but its **shape** is
exactly right and directly informs §7's `CandidateFragment` design below.

### 2c. Evidence citation — `Source` / `SourceAssertion`

- **`Source`** (A, G, L partial): title, `source_type`, `publisher`, `url`,
  `published_date`, `retrieved_at`, `document_reference`, `summary`,
  `reliability_level`, and a namespaced **`external_id`** (unique) — the
  dedup key every current importer actually uses in practice (e.g.
  `usaspending:{award_id}`, `faa_nasr:airport_csv:{cycle}:{filename}`).
  This is a **lighter-weight, separate** identity mechanism from
  `AcquisitionSource`/`Snapshot` — evidence citation, not byte-level
  document identity. The two systems currently do not talk to each other.
- **`SourceAssertion`** (C, E, F, I, J): "one upstream record's claim,
  preserved before any identity reconciliation" (its own docstring).
  `airport_id` is **nullable** — the only model in the repository
  designed from the start to hold evidence whose airport identity isn't
  yet resolved. Carries `raw_airport_identifier`/`raw_airport_name`/
  `raw_runway_value`/`raw_runway_end_value`/`raw_relevant_text`/etc. (raw
  text preserved, F), `evidence_quality` (`unverified_candidate` →
  `direct_strong`) and `review_state` (`unreviewed`/`reviewed`) (J), and
  **fragment identity** via `source_locator`/`raw_fragment_hash`/
  `artifact_identity`/`source_record_identifier` (I), enforced by two
  `UniqueConstraint`s: `(source_id, source_record_identifier)` and
  `(source_id, artifact_identity, source_locator, raw_fragment_hash)`.
  This is the **closest existing thing to a "candidate fragment record,"
  and it already assumes exactly one identity-resolution outcome per row**
  — a structural fact that matters directly for §22.

### 2d. Reviewed identity — `PhysicalInstallationIdentity` / `InstallationAssertionLink`

Human-gated, append-only, immutable-once-recorded (`before_update`/
`before_delete` triggers raise). Unaffected by, and unchanged by, this
design — the guard and everything above it in this document only ever
feeds `SourceAssertion`, never this layer directly.

### 2e. `Signal` — investor-facing, requires resolved identity

`airport_id: Mapped[int]` is **not nullable** — a `Signal` cannot exist
without a resolved `Airport`. This is already a hard product boundary:
discovery cannot create a `Signal` before identity is resolved, by
construction, regardless of anything this design adds.

### 2f. Existing dedup/idempotency mechanisms found (H, M)

Three **independent** dedup layers already exist, at three different
granularities, currently disconnected from one another:

1. `AcquisitionSource.key` (unique) + `Snapshot(source_id, sha256, byte_size)`
   (unique) — document/byte-content level.
2. `Source.external_id` (unique) — evidence-citation level, namespaced
   per importer (`usaspending:...`, `faa_nasr:...`).
3. `SourceAssertion`'s two composite unique constraints — fragment/record
   level, within one `Source`.

No document/resource ever currently flows through all three — USAspending
and NASR ingestion use only (2)+(3); FAA EMAS capture uses only (1). This
design's job is to make discovery flow through all three coherently,
**not to invent a fourth.**

### 2g. Document/Observation/Fact/Intelligence — not implemented

`app/models/__init__.py` exports exactly: `Airport`, `AcquisitionRun`,
`AcquisitionRunStatus`, `AcquisitionSource`, `Incident`, `Installation`,
`InstallationAssertionLink`, `PhysicalInstallationIdentity`,
`PublishingSource`, `Runway`, `RunwayEnd`, `Signal`, `Source`,
`SourceAssertion`, `Snapshot`. **No `Document`, `Observation`, `Fact`, or
`Intelligence` model exists** — those are architectural/product
vocabulary from the task brief, not current code. `Source` is RWI's
actual "document" concept today; `Snapshot` is its actual "byte content"
concept.

## 3. Problem statement

The Evidence Attachment Guard answers *"may this evidence fragment attach
to this candidate airport?"* — but nothing yet defines: what a
"fragment" is for an arbitrary discovered web resource; how raw
discovered text becomes an `EvidenceBag`; what happens after each guard
outcome; where document/resource identity and dedup live; how rejected-
for-one-airport evidence stays evaluable for a different airport; and
whether any schema change is actually needed once the lifecycle is
understood end to end.

## 4. Architectural boundaries

Four boundaries, matching four different trust levels, none of which
this design blurs:

1. **Acquisition boundary** (existing, reused): raw bytes in, `Snapshot`
   out. No interpretation, no airport concept at all.
2. **Extraction boundary** (existing precedent, generalized): `Snapshot`
   bytes in, `CandidateFragment`(s) out — raw strings only, no identity
   decision, may be AI-assisted, fails closed on unparseable input
   (`FAAEmasSnapshotParser`'s own pattern).
3. **Decision boundary** (existing, committed, invariant): `EvidenceBag` +
   `CandidateAirport` in, `AttachmentDecision` out — 100% deterministic,
   no I/O, already built and tested.
4. **Governance boundary** (existing, reused): `SourceAssertion` (evidence,
   airport-scoped or not) → human-gated `PhysicalInstallationIdentity`
   reconciliation → `Signal` promotion. Unchanged by this design.

## 5. `CandidateDocument` / `CandidateResource`

**Recommendation: this is not a new concept — it is `AcquisitionSource` +
`AcquisitionRun` + `Snapshot`, generalized beyond FAA providers.**

- **Uniquely identified by**: `AcquisitionSource.key` (a stable, chosen
  identifier — e.g. `"faa.emas.installations.tableau"` today; a future
  `"sfo.airport-commission.agenda.2026-08"` or a canonical-URL-hash-based
  key for ad hoc discovery) at the "what are we watching" level, and by
  `Snapshot.sha256`/`byte_size` at the "what exact bytes did we see" level.
- Discovery does **not** need a new document-identity scheme. It needs:
  (a) new, non-FAA `AcquisitionProvider` implementations (§21 worked
  examples show several), and (b) a convention for minting
  `AcquisitionSource.key` values for ad hoc, discovery-found URLs rather
  than only hand-curated ones (e.g. a normalized-canonical-URL-derived
  key, computed once and reused on rediscovery — never derived from
  search-query text, per the invariant in §7 of the guard design and §23
  below).
- `AcquisitionRun.is_new_snapshot` already answers "have we seen this
  exact content before" — this is the correct place for **document-level**
  dedup across however many discovery channels found the same URL (§17).

## 6. `CandidateFragment`

**Recommendation: generalize `FAAEmasCandidate`/`FAAEmasParseReport`'s
shape — do not invent a new pattern.**

- **Uniquely identified by**: `(document identity, source_locator)` —
  reusing `SourceAssertion.source_locator`/`raw_fragment_hash`/
  `artifact_identity` exactly, not a new fragment-identity scheme.
  `source_locator` should carry whatever addressing makes sense per
  document type (page/paragraph number, table row key, HTML element
  path, PDF page+block, JSON path) — deliberately left as a free-text
  convention per extractor, exactly as `FAAEmasCandidate.source_locator`
  already is.
- **Relationship to parent**: one `CandidateDocument`/`Snapshot` produces
  **zero or more** `CandidateFragment`s (a page with nothing
  EMAS-relevant produces zero — see §10 for why that matters for `Source`
  creation).
- **One document may produce fragments naming multiple airports** — each
  such fragment is still a single fragment (e.g., a regional capital bill
  paragraph naming two airports) evaluated against each named candidate
  independently (§8 case K in the guard design; worked example G below).
- **One fragment may legitimately mention multiple airports** — this is
  expected and handled entirely by the guard's own contradiction logic,
  not by the fragment-identity scheme: a well-fragmented document
  minimizes this, but the guard is safe even when it happens (rejects
  ambiguous/contradictory cases rather than guessing).
- **PDFs/HTML/search snippets/procurement tables** all map onto the same
  `(document, source_locator, raw_text)` shape — the *addressing
  convention* differs (page number, DOM path, snippet offset, row key)
  but the `CandidateFragment` dataclass itself does not need a
  type-specific variant. A **search snippet is explicitly the weakest
  fragment type**: it is frequently truncated, re-worded by the search
  engine, and detached from its real surrounding context — it should be
  usable only to *decide whether to acquire the full document* (§5), and
  should not, by itself, be treated as strong extraction input once the
  real document is available. A snippet-derived fragment used because the
  full document could not be retrieved should be flagged as such (a
  `content_type`/provenance field on the fragment, not a new concept).

### Worked example: SFO/MSP, mandatory

A search for "SFO EMAS Runway Safe 2026 contract" surfaces a URL that,
once fetched, becomes a `Snapshot` under some `AcquisitionSource` (its
`key` derived from the URL, **never** from the query text). Extraction
produces one `CandidateFragment` whose `raw_text` mentions "Metropolitan
Airports Commission" and "Runway 30L." **Nothing about this fragment
carries "SFO" as a fact** — SFO only ever appears as *the candidate this
fragment happens to be evaluated against*, supplied separately by
whatever orchestration loop is running discovery for SFO that day. The
fragment itself, evaluated against MSP as a candidate (independently, by
name/identifier match, not because the fragment "says" MSP), reaches
`ATTACH_CONFIRMED`. This is exactly current `evaluate_attachment` test
coverage (`test_case_A_*`), and this section defines precisely how a real
fragment reaches that call.

## 7. Evidence extraction

**Extraction answers "what strings/entities appear here?" — never "which
airport?"** Input: `CandidateFragment.raw_text` (+ `raw_html`/`raw_bytes`
if useful for the extractor). Output: the fields already defined by
`EvidenceBag` (§8) — nothing more.

- May be regex-based (exactly like `LOC_ID_PATTERN`/`BENEFICIARY_PATTERN`
  in `import_usaspending_grants.py` today), AI-assisted, or a hybrid.
- May translate native-language terms into the same normalized shape
  (§19) — but the **original-language raw text is always preserved** on
  the `CandidateFragment`/eventual `SourceAssertion.raw_relevant_text`,
  never replaced.
- **Must never emit an LLM confidence score into any identity field.**
  There is no field anywhere in `EvidenceBag`/`CandidateAirport` for a
  probability — this is a structural fact of the already-committed guard
  (`app/services/evidence_attachment_guard.py` has no numeric confidence
  concept at all), not a new rule invented here.
- An extractor's own **relevance judgment** ("does this fragment concern
  EMAS/runway-safety at all?") is a legitimate, separate, coarser
  decision than the guard's identity judgment — it decides whether a
  `CandidateFragment` is even worth building an `EvidenceBag` for at all
  (§10's `Source`-creation boundary depends on this).

## 8. `EvidenceBag` integration

No change needed to `EvidenceBag` itself (§4 of the core report already
enumerates exactly what it carries and why). This section defines the
**mapping contract** from extraction output to `EvidenceBag` construction:

| Extracted (raw) | `EvidenceBag` field |
|---|---|
| Airport codes/Loc IDs/ICAO/IATA found | `identifiers` |
| Airport/authority names found, matched against candidate | `names` |
| Runway/runway-end strings found | `runway_ends`/`runway_pairs` (raw, un-normalized — the guard normalizes) |
| Issuer/publisher/authority found | `issuers` |
| City/location found | `locations` |
| A name/issuer/location the extractor (or a reference-table lookup) has already resolved as belonging to a **specific, different** airport | `contradicting_names`/`contradicting_issuers`/`contradicting_locations` — **never inferred by the guard itself**, only ever supplied here |
| Project/contract number, document title, URL | audit-only `EvidenceBag` fields, never used in the decision |

Extraction produces **one `EvidenceBag` per (fragment, candidate airport)
pair it decides to evaluate** — not one bag "for the document." Which
candidates to evaluate a given fragment against is an orchestration
decision (§21 worked examples show the common patterns: a single expected
candidate for a targeted discovery run; a small plausible set for an
ambiguous authority).

## 9. Attachment Guard integration

Unchanged — `evaluate_attachment()`/`evaluate_attachment_for_candidates()`
consume exactly the `EvidenceBag` built in §8 and the `CandidateAirport`
already buildable via `candidate_airport_from_airport_like()`. This
design adds no new guard responsibility; it defines what happens on
either side of it.

## 10. Decision lifecycle

For each outcome, exactly what may happen next:

| Outcome | `Source` created? | `SourceAssertion` created? | `airport_id` set? | `Signal` eligible? | Human review queued? | Remains searchable/retryable? |
|---|---|---|---|---|---|---|
| `ATTACH_CONFIRMED` | Yes (if not already) | Yes | Yes | Only if the §12 Signal threshold is independently met | No | Yes (future stronger evidence still welcome) |
| `ATTACH_PROVISIONAL` | Yes (if not already) | Yes, `evidence_quality="unverified_candidate"` | Yes | No | Optional/low-priority | Yes — may be **re-evaluated** as new evidence arrives (§15) |
| `REVIEW_REQUIRED` | Yes (if not already) | Yes, `airport_id = NULL` | No | No | **Yes** | Yes |
| `REJECT_CROSS_AIRPORT` | Yes (if not already) | Yes, `airport_id = NULL` | No | No | No | Yes — remains independently evaluable against a **different** candidate (§13) |
| `INSUFFICIENT_IDENTITY` | Yes (if not already) | Yes, `airport_id = NULL` | No | No | No (unless flagged high-value by extraction) | Yes — may become resolvable later (§14) |

**Every outcome that reaches the guard at all results in a
`SourceAssertion`** (§11 explains why this is correct and does not turn
`SourceAssertion` into a crawler log). `Source` creation is a **document**
boundary, evaluated once per document, not once per outcome (§10 below).

## 11. Source creation boundary

**Recommendation: (C) — after useful fragment extraction, not on URL
discovery or raw retrieval.**

- **A. Immediately on URL discovery** — rejected. This is exactly the
  "millions of useless search results" pollution risk the task warns
  about, and it's also exactly where the SFO/MSP false signal originates
  if treated as evidence at all.
- **B. Only after successful retrieval** — rejected as the `Source`
  boundary, but this is precisely the existing `Snapshot`/`AcquisitionRun`
  boundary already, at a **different, cheaper layer** (§2a/§2f) — every
  real fetch is already recorded there, deduplicated by content hash,
  regardless of relevance. Retrieval-level bookkeeping does not need
  `Source` at all.
- **C. After useful fragment extraction — recommended.** A `Source` is
  created the first time a document's extraction produces at least one
  `CandidateFragment` the extractor judged topically relevant enough to
  build an `EvidenceBag` for (§7's relevance judgment) — regardless of
  what the guard later decides about airport identity. This matches
  exactly how `import_usaspending_grants.py::import_all()` already
  behaves: a `Source` (there, keyed by `external_id`) is created for
  every real grant record, whether or not `resolve_airport()` succeeds —
  the boundary is "is this a real thing worth citing," not "did identity
  resolve."
- **D. After guard acceptance** — rejected: this would silently discard
  `REJECT_CROSS_AIRPORT`/`INSUFFICIENT_IDENTITY` evidence that is still
  worth preserving (§13/§14), and would also mean the SAME document,
  re-discovered later and re-evaluated against a different candidate,
  has no existing `Source` to attach a new `SourceAssertion` to —
  reintroducing exactly the rediscovery-cost problem the task flags.

`Source.external_id` for a discovery-created `Source` should key off the
underlying `AcquisitionSource`/`Snapshot` identity (§5), not the search
query — preserving the "search context is never identity" invariant all
the way through document citation, not just fragment evaluation.

## 12. SourceAssertion creation boundary

**All five guard outcomes create a `SourceAssertion`** (§10 table) — the
existing nullable-`airport_id` design is precisely what makes this safe
and non-polluting: it already distinguishes "evidence exists" from
"identity resolved" as two separate facts, exactly the distinction §11
needs. What keeps this from becoming a generic crawler log is **upstream
of `SourceAssertion` entirely**: extraction's own relevance judgment (§7)
already filtered out everything that isn't EMAS/runway-safety-relevant
before a fragment ever reaches the guard. A `SourceAssertion` therefore
always represents *"a fragment RWI's own extraction judged worth
evaluating for airport identity"* — never *"a URL RWI happened to fetch."*

`identity_guard_decision`/`identity_guard_reason` (proposed, not
implemented — §22) are precisely the fields needed to distinguish the
four "not `ATTACH_CONFIRMED`" outcomes from each other on the same
`airport_id = NULL` row shape that `UNRESOLVED` already uses today —
without them, `REVIEW_REQUIRED`, `REJECT_CROSS_AIRPORT`, and
`INSUFFICIENT_IDENTITY` are currently indistinguishable from each other
and from today's plain `UNRESOLVED` USAspending case, which is a real
loss of information the current schema cannot represent.

## 13. Signal creation boundary

**A discovered, airport-confirmed document is not automatically
Signal-worthy.** `Signal` is investor-facing intelligence about a
*concrete, dated, material EMAS-relevant event* — a project, a funding
change, a maintenance/repair action, a procurement action — not "a
document exists and concerns this airport."

Recommended additional threshold, beyond `ATTACH_CONFIRMED`: extraction
must independently classify the fragment's **content**, not just its
identity, as describing one of a small, explicit set of event types
already implicit in `Signal.category`'s existing values
(`new_installation`/`replacement`/`maintenance`, per
`classify_category()` in `import_usaspending_grants.py`) **plus at least
one concrete fact** beyond bare existence — a date, an amount, a phase, a
contractor, a procurement action. *"Airport master plan PDF exists"*
carries no such fact and should remain a `SourceAssertion` only.
*"Master plan budgets EMAS replacement in FY2028"* carries an event type
(`replacement`) and a fact (FY2028) and clears the bar. This mirrors
exactly the distinction RWI's own `Signal` model already draws between
`identified` (bare candidate) and stronger `status` values — Signal
creation from discovery should default to `status="identified"`,
`confidence` reflecting the guard outcome
(`ATTACH_CONFIRMED`→higher, never from `ATTACH_PROVISIONAL` at all per
§10), exactly the same discipline `import_usaspending_grants.py::import_all()`
already applies today for its own Signal creation.

## 14. Rejected candidate handling

**Design around (document, candidate-airport) evaluation, never global
document rejection — exactly as instructed, and exactly what the current
`SourceAssertion(airport_id=NULL)` + a per-decision `identity_guard_decision`
already models correctly once added.**

- **Should RWI remember the rejection?** Yes — as a `SourceAssertion` row
  scoped to *that* (fragment, candidate) pair, `airport_id = NULL`,
  `identity_guard_decision = "REJECT_CROSS_AIRPORT"`, with the
  contradicting evidence preserved in `identity_guard_reason`.
- **Where / how long?** In `SourceAssertion`, indefinitely — consistent
  with every other evidence row in the system; nothing here is treated as
  more disposable than the rest of RWI's evidence.
- **Eligible for re-evaluation against MSP?** Yes, and it requires no
  special mechanism: the *same* underlying fragment (same
  `source_locator`, same `raw_text`) is simply run through
  `evaluate_attachment()` again with MSP as the candidate, producing its
  **own, independent** `SourceAssertion` row. The two rows are linked
  only by sharing the same `source_id`/fragment identity — there is no
  "this document is rejected" flag anywhere at the document level to
  contradict.
- **Is the document itself "bad"?** No — correctly never modeled that
  way. `AcquisitionSource`/`Snapshot` (§5) have no rejection concept at
  all, and should not gain one; only the (fragment, candidate) evaluation
  is rejected, never the document.

## 15. Provisional attachment

- **Can `ATTACH_PROVISIONAL` evidence carry `airport_id`?** Yes (§10
  table, consistent with the guard design's own contract) — it is weak,
  single-category evidence, but it IS attached, not merely candidate.
- **Can it appear publicly?** **No, by default.** Every public-facing
  pathway built this session (`current_emas` in
  `app/static_export/build.py`) is fed exclusively by the
  human-reviewed `PhysicalInstallationIdentity`/`InstallationAssertionLink`
  reconciliation layer or the separately-promoted `SourceAssertion.runway_end`
  NASR pathway — neither of which `ATTACH_PROVISIONAL` evidence
  qualifies for without an explicit further step. `ATTACH_PROVISIONAL`
  is deliberately a **backend-only, candidate-evidence state.**
- **Can it generate a `Signal`?** No (§13, §10 table) — one weak category
  is not the "concrete, dated, material fact" bar.
- **Does it require human promotion?** To become governed identity
  (`PhysicalInstallationIdentity`), yes — exactly the same human-gated
  path MDW/CGF/BOS/ORH already went through, unchanged.
- **Can stronger future evidence automatically promote it?** The
  **evaluation itself** can be automatically re-run (the guard is pure
  and idempotent) — if a second, independent fragment later supplies a
  second evidence category for the same (document-or-different-source,
  candidate) pair, a fresh `evaluate_attachment()` call naturally
  produces `ATTACH_CONFIRMED`. This is re-running the *guard*, not
  auto-editing the old `ATTACH_PROVISIONAL` row — the old row stays as
  its own immutable historical record (matching `SourceAssertion`'s
  existing append-only spirit); a new row records the stronger result.
  Promotion to *governed* `PhysicalInstallationIdentity` status,
  specifically, still requires a human decision regardless of how strong
  the automated evidence becomes — the guard's most permissive outcome
  never bypasses that layer (§7 of the guard design, unchanged).
- **Audit trail required?** The same `identity_guard_reason` already
  proposed for every non-confirmed row (§22) — `ATTACH_PROVISIONAL`'s
  own reason already names the single supporting category (§6 of the
  core report: "Exactly one positive evidence category…").

## 16. Review lifecycle

No UI designed here — conceptual queue contents only, reusing the
existing reconciliation pattern (`PhysicalInstallationIdentity`+
`InstallationAssertionLink`, human-gated, `actor`+`reason` required)
rather than inventing a new review concept.

A `REVIEW_REQUIRED` (or, at human discretion, `ATTACH_PROVISIONAL`/
`INSUFFICIENT_IDENTITY`) queue entry should expose, all already available
from existing fields with the §22 additions:

- The candidate fragment's `raw_relevant_text` (`SourceAssertion`).
- The parent `Source` (title, URL, publisher, retrieved date).
- Every candidate airport evaluated for this fragment, and each one's
  full `AttachmentDecision` (`identity_guard_decision`/
  `identity_guard_reason`, plus the positive/contradicting evidence
  detail already structured by `AttachmentDecision` — §8 of the core
  report) — for `REVIEW_REQUIRED` specifically, this means **more than
  one** candidate's decision, which is exactly the multi-candidate detail
  §22 discusses persisting.
- The relevant candidate airport(s)' own canonical topology, for a human
  to sanity-check the guard's topology-membership reasoning directly
  (exactly the same canonical `Runway`/`RunwayEnd` data already public).
- Discovery context (which `AcquisitionSource`, when).

Reviewer decisions should conceptually map onto the **existing**
`record_reconciliation_decision()` outcomes
(`SAME_PHYSICAL_INSTALLATION`/`DIFFERENT_PHYSICAL_INSTALLATION`/
`UNRESOLVED`) plus one new administrative action — "confirm the guard's
candidate airport and let it flow into `SourceAssertion.airport_id`"
(effectively promoting a `REVIEW_REQUIRED` row into the same shape an
`ATTACH_CONFIRMED` row would have had) — not a new decision vocabulary.

## 17. Deduplication / idempotency

No new identity scheme needed anywhere:

| Object | Identity | Existing mechanism reused |
|---|---|---|
| `CandidateDocument` | `AcquisitionSource.key` (stable target) + `Snapshot.sha256`/`byte_size` (exact content) | §5 — already unique-constrained |
| `CandidateFragment` | `(document identity, source_locator)` | `SourceAssertion.source_locator`/`raw_fragment_hash`/`artifact_identity` — already unique-constrained |
| `CandidateAirportEvaluation` (the guard's own output for one fragment × one candidate) | `(fragment identity, candidate airport id)` | New concept, but representable as `(SourceAssertion.source_id, source_record_identifier, airport_id-or-NULL-with-a-candidate-note)` — see §22 for the exact representation question |

**Never derived from search-query text**, per the invariant carried
through every layer (§6, §23). **Rerun behavior**: the same document
found via Google, Bing, an operator-site search, or a future n8n
workflow must resolve to the **same** `AcquisitionSource.key` (derived
from the canonical URL or an equivalent stable identifier, computed the
same way regardless of discovery channel) — `AcquisitionRun.is_new_snapshot`
already reports `False` on a re-fetch of unchanged content, and the
existing `Source.external_id`/`SourceAssertion` uniqueness constraints
already prevent a second identical citation row. **This means the
discovery-channel abstraction is already solved by existing schema** —
the open work is only in consistently computing the same
`AcquisitionSource.key` regardless of which channel found the URL first.

## 18. Change detection

Not implemented here — but the envelope already supports it without
further design work: `Snapshot`'s content-hash immutability means a
**new** `Snapshot` row (with `AcquisitionRun.is_new_snapshot = True`)
naturally and automatically appears exactly when a monitored document's
byte content changes (a revised procurement notice, a new CIP cycle, an
updated project page). Future change detection is therefore a matter of
(a) re-running the *same* `AcquisitionSource` periodically (an
orchestration/scheduling concern, §20) and (b) comparing the new
`Snapshot`'s extracted `CandidateFragment`s against the prior snapshot's
— an **extraction-layer** diff concern, not a new identity or schema
concept. Nothing in this design blocks that; nothing in this design
needs to anticipate it further than keeping `Snapshot` immutable and
content-addressed, which it already is.

## 19. International / multilingual

- **Original text is always preserved**: `Snapshot.payload` is raw bytes
  in whatever encoding/language the source used, untouched;
  `SourceAssertion.raw_relevant_text` is free-text with no language
  constraint. Neither needs a change.
- **Translation, if used, is a derived, clearly-separate artifact** —
  this design identifies a genuine, currently-unaddressed gap: there is
  **no existing field** for "translated version of this raw text." Not
  needed for the guard itself (identifier/topology matching is
  language-agnostic already — proven directly by the guard's own Haneda
  fixture, which matches a native-script alias with zero translation);
  potentially useful for **human review** (§16) and for **extraction**
  when an LLM is asked to find entities in a non-English fragment more
  reliably via a translated intermediate. Recommendation: **do not add a
  translated-text field in this slice** — extraction may translate
  in-memory to help itself extract entities, but must write the
  **original-language** raw text into `SourceAssertion.raw_relevant_text`
  as it already does for English sources; a dedicated translation field
  is deferred to whichever future slice first needs to show a translation
  to a human reviewer (§16), not invented speculatively here.
- **`ICAO` identifiers already generalize** — `CandidateAirport.identifiers`
  has no U.S.-specific shape (guard core report §12); an
  `AcquisitionSource`/`Source` for a non-U.S. authority needs no schema
  change, only a new `PublishingSource`/`AcquisitionSource` row and
  provider, exactly like adding any new U.S. one.
- **Local procurement identifiers, non-U.S. date/currency formats,
  non-U.S. authority structures** — all belong entirely to the
  **extraction layer** (§7), per-source-type, exactly like
  `LOC_ID_PATTERN`/`BENEFICIARY_PATTERN` are USAspending-specific today
  and live only in that one importer. No international extraction is
  implemented here, per instruction — only confirmed that nothing
  upstream of extraction (acquisition, the guard, `SourceAssertion`)
  structurally blocks it.

## 20. n8n / agent boundary

Restated precisely for the full pipeline (extends, does not change, the
guard design's own §10):

**MAY**: search; fetch (as an `AcquisitionProvider` implementation, §5);
classify a document's source type/media type; extract `CandidateFragment`s
and their raw text (§6–§7); translate for its own extraction purposes
(§19); build candidate `EvidenceBag`s (§8); propose which candidate
airport(s) a fragment should be evaluated against; summarize for human
review (§16).

**MUST NOT**: assign canonical airport identity without the guard (§9,
unchanged, invariant); create a `Signal` or `PhysicalInstallationIdentity`
directly, ever (§13, §16 — both remain human/rule-gated exactly as
today); infer a vendor from sole-manufacturer/monopoly status alone
(the SFO pilot's own explicit finding — a documented, not new, rule);
infer a project's cost from an unrelated project's cost; merge evidence
across airports (the guard's own contradiction-first design already
makes this structurally hard, but orchestration must not work around it
by, e.g., silently picking "the most likely" candidate itself instead of
calling `evaluate_attachment_for_candidates()`); or mint an
`AcquisitionSource.key` from search-query text rather than the resolved
canonical URL (§5, §17).

RWI's deterministic layers (the guard; `SourceAssertion`'s nullable-
`airport_id` design; `PhysicalInstallationIdentity`'s human gate) remain
the sole authority. n8n/agents are orchestration and extraction only,
never truth.

## 21. Worked examples

**A. SFO query discovers MSP Runway 30L memo.** Acquisition creates/reuses
an `AcquisitionSource` keyed off the memo's own URL (never the query) →
`Snapshot`. Extraction produces one `CandidateFragment` (issuer =
"Metropolitan Airports Commission," runway = "30L"). Orchestration
evaluates it against candidate SFO (why SFO was chosen is itself
orchestration bookkeeping, not evidence). `evaluate_attachment(SFO, bag)`
→ `REJECT_CROSS_AIRPORT`. `Source` created (extraction judged it
EMAS/runway-safety-relevant); `SourceAssertion` created, `airport_id =
NULL`, `identity_guard_decision = REJECT_CROSS_AIRPORT`. No `Signal`. If
MSP is ever separately a candidate for this fragment, a second,
independent `SourceAssertion` is created for MSP, reaching
`ATTACH_CONFIRMED`.

**B. Genuine SFO EMAS project page.** `AcquisitionSource` for
`fly.sfo.gov`'s relevant page. Fragment names "SFO" and "RWY 1R/19L."
`evaluate_attachment(SFO, bag)` → `ATTACH_CONFIRMED` (identifier alone).
`Source`+`SourceAssertion` created, `airport_id` = SFO. Whether a `Signal`
is created depends on §13's separate content threshold — a bare
"project page exists" fragment does not qualify; a fragment stating a
dated funding/construction fact does.

**C. BOS Massport Runway 22R source.** Fragment: issuer "Massport,"
runway "22R." `evaluate_attachment(BOS, bag)` → `ATTACH_CONFIRMED`
(issuer + topology-membership, exactly `test_case_C_*`).
`Source`+`SourceAssertion(airport_id=BOS)` created.

**D. ORH procurement document.** Fragment: issuer "MPA," runways "29"
and "11" (dual physical/protected naming). `evaluate_attachment(ORH, bag)`
→ `ATTACH_CONFIRMED` (`test_case_D_*`). Same as C.

**E. USAspending grant with embedded airport identifier.** Today's
*existing* `resolve_airport()` already does something equivalent to this
whole pipeline in miniature, pre-dating the guard. Under this design,
future USAspending ingestion would build an `EvidenceBag(identifiers={LOC_ID})`
and call the guard → `ATTACH_CONFIRMED` (identifier alone,
`test_case_E_*`) — behaviorally identical to today's `RESOLVED_EXISTING`/
`RESOLVED_NEW`. **Not retrofitted in this design** — §22 revisits when.

**F. Useful EMAS procurement with insufficient airport identity.**
Fragment clearly describes an arresting-bed procurement but has no
airport code, only a weak organization name. Extraction still judges it
topically relevant (creates `Source`). `evaluate_attachment(candidate,
bag)` → `INSUFFICIENT_IDENTITY` for whatever candidate(s) were tried (or
no candidate was even plausible enough to try). `SourceAssertion(airport_id=NULL,
identity_guard_decision=INSUFFICIENT_IDENTITY)` preserved. Later, a
second fragment (another source, a contract-number match, a human
tip) supplies an identifier or issuer match — a **fresh**
`evaluate_attachment()` call against the now-better-evidenced candidate
can reach `ATTACH_CONFIRMED`, without ever having contaminated any
canonical `Airport` in the meantime.

**G. Multi-airport authority document.** A regional capital-improvement
bill names both BOS and ORH (both governed by Massport in RWI's own
`known_issuers` data). Extraction produces two fragments (or one
fragment evaluated against two candidates, if not cleanly separable).
Each is independently confirmed against its own airport
(`test_adversarial_4_*` already proves this exact pattern) — no merging,
no "the document is about Massport, therefore both count as strong."

**H. International/native-language procurement document.** A Japanese
airport authority procurement notice names "羽田空港" (Haneda) and ICAO
"RJTT." Extraction (possibly AI-translation-assisted, §19) produces an
`EvidenceBag(identifiers={"RJTT"}, names={"羽田空港"})`. Original Japanese
text preserved in `raw_relevant_text`. `evaluate_attachment(HANEDA, bag)`
→ `ATTACH_CONFIRMED` (identifier alone — `test_international_haneda_*`
already proves the guard itself needs no change for this case; only a
`PublishingSource`/`AcquisitionSource`/extractor for the new authority is
new work).

## 22. Persistence / schema options

- **Option A — no new persistence, ephemeral candidates only.**
  Rejected as insufficient on its own: it would lose §14's rejection
  memory and §17's dedup entirely — RWI would re-fetch and re-evaluate
  the same rejected MSP memo every time SFO discovery runs.
- **Option B — small additive candidate tables** (a new
  `CandidateDocument`/`CandidateFragment`/`CandidateAirportEvaluation`
  schema, parallel to `Source`/`SourceAssertion`). Rejected as the
  starting point: it would duplicate almost everything `AcquisitionSource`/
  `Snapshot`/`SourceAssertion` already do, directly contradicting the
  explicit "avoid a parallel evidence architecture" instruction, for
  marginal gain over reusing what exists.
- **Option C — reuse `Source`/`SourceAssertion` entirely.** Very close to
  correct, and correct for the *common* case (§10–§12) — but has one
  concrete structural tension worth naming precisely rather than glossing
  over: `SourceAssertion`'s existing uniqueness constraints
  (`(source_id, source_record_identifier)` and
  `(source_id, artifact_identity, source_locator, raw_fragment_hash)`)
  assume **one row represents one identity-resolution outcome** for a
  given record — matching every existing use of the model (one physical
  claim, later reconciled). `REVIEW_REQUIRED`'s multi-candidate detail
  (§16 — a human needs to see *every* candidate's decision, not just one)
  does not fit cleanly into that single-row assumption without a schema
  change to the identity fields themselves, which is out of scope for
  "prefer no schema change."
- **Option D — Hybrid. Recommended.**
  1. **Document/resource identity + dedup**: reuse `AcquisitionSource`/
     `AcquisitionRun`/`Snapshot` exactly as they exist today, generalized
     only by adding new, non-FAA `AcquisitionProvider` implementations
     (§5). **No schema change.**
  2. **The single, resolved evidence record per fragment**: reuse
     `Source`/`SourceAssertion` exactly as designed, **plus the two
     additive columns already proposed and now more concretely justified**
     (`identity_guard_decision`, `identity_guard_reason` — §12, §22
     below) — one row per (fragment, the *primary* candidate orchestration
     actually resolved to, or `NULL`). **Small additive schema change,
     not a redesign.**
  3. **Multi-candidate detail for `REVIEW_REQUIRED` specifically**: do
     **not** build a new table yet. Encode the full
     `evaluate_attachment_for_candidates()` output (every candidate
     tried, each one's outcome + reason) as **structured text inside
     `identity_guard_reason`** for the single `SourceAssertion` row this
     produces (`airport_id = NULL` until a human resolves it) — the
     smallest option that still preserves full auditability for review
     (§16). **Graduate to a real `CandidateAirportEvaluation` table only
     if/when a real review-queue UI needs to query across candidates
     structurally** (e.g., "show me every fragment ambiguous between
     exactly BOS and ORH") — not speculatively now, matching this
     repository's own established pattern of adding structure only once
     a real need is proven (e.g., `PhysicalInstallationIdentity.runway_end_id`
     was added well after `runway_end` already existed, once linking was
     actually needed).

**This design does not implement Option D — it recommends it as the
target for whichever future slice actually adds persistence (§25).**

### Revisiting `identity_guard_decision`/`identity_guard_reason`

The original guard design (§11 there) proposed these speculatively,
before this lifecycle was fully worked through. **Re-examined here, not
assumed: they are now more clearly justified, not less.** Without them,
`REVIEW_REQUIRED`, `REJECT_CROSS_AIRPORT`, `INSUFFICIENT_IDENTITY`, and
today's existing plain `UNRESOLVED` (USAspending) case would all
collapse into the same indistinguishable `airport_id = NULL` shape —
a real, now-concretely-demonstrated loss of information (§12), not a
hypothetical one. They remain **not implemented in this design** (no
schema change here either), but the recommendation to add them is
strengthened, not weakened, by this fuller analysis.

## 23. Product safety invariants

1. **Search context is never evidence identity.** No field anywhere in
   `EvidenceBag`/`CandidateAirport` carries "the airport this search
   targeted" — structurally impossible to smuggle in, not just a
   convention (guard core report, adversarial test 7).
2. **Raw source evidence is preserved.** `Snapshot.payload` (bytes) and
   `SourceAssertion.raw_relevant_text` (text) are never overwritten or
   discarded, for any guard outcome including rejection.
3. **Translation never replaces original evidence.** §19 — translation is
   an extraction-time aid only; original-language text is what gets
   preserved.
4. **AI extraction is not canonical identity.** Extraction only ever
   produces an `EvidenceBag`; only the deterministic guard decides
   attachment (§7–§9).
5. **Contradiction beats weak positive evidence.** Unconditional,
   already proven by test, unchanged (guard §5/§7).
6. **Airport attachment passes through the deterministic guard — always,
   with no bypass.** No orchestration path in this design creates a
   `SourceAssertion.airport_id` value without a corresponding
   `AttachmentDecision`.
7. **Runway claims must match canonical topology where applicable** —
   membership-checked via the existing, unmodified `runway_identity.py`
   normalization, never heading arithmetic (guard §6, unchanged).
8. **Rejected-for-airport ≠ globally rejected document.** §14 — rejection
   is scoped to `(fragment, candidate)`, never to the document or
   `Snapshot`.
9. **Provisional ≠ confirmed.** §15 — different public-visibility,
   different Signal-eligibility, different promotion path.
10. **Signal ≠ merely discovered document.** §13 — requires a concrete,
    dated, material fact, not bare existence/identity confirmation.
11. **Canonical truth requires provenance.** Every governed row this
    pipeline can produce traces back to a `Source`/`Snapshot`/
    `SourceAssertion` chain — nothing is ever asserted without a citation,
    consistent with every existing RWI mechanism.
12. **Cross-airport evidence must never silently merge.** §6/§14/§20 — a
    fragment naming two airports produces independent evaluations, never
    a combined or averaged one.

## 24. Recommended implementation slices

Reordered from the task's suggested sequence based on what already
exists — the highest-value, lowest-risk next step is **not** a new
crawler, and not new persistence; it's the pure extraction-boundary code
that has a direct, real precedent already in the repository:

1. **`CandidateFragment` + extraction-boundary pure dataclasses**,
   modeled directly on `FAAEmasCandidate`/`FAAEmasParseReport`'s existing
   shape (raw fields, `source_locator`, fail-closed typed errors,
   versioned parser identity) — plus a pure, tested
   `CandidateFragment → EvidenceBag` builder contract (§8's mapping
   table, made concrete in code). No persistence, no DB, extends the
   already-committed guard's own test-fixture patterns. Smallest,
   safest, most directly buildable-on-what-exists slice.
2. **The two additive `SourceAssertion` columns**
   (`identity_guard_decision`/`identity_guard_reason`), following the
   existing `ensure_source_external_id_column()`-style add-if-missing
   idiom — wired into a **new** pathway first, never a retrofit of
   NASR/USAspending in the same slice (unchanged recommendation from the
   guard core report).
3. **One controlled, narrowly-scoped discovery adapter** — a single new
   `AcquisitionProvider` for one specific, real, already-identified
   source (e.g., one airport authority's agenda page), reusing
   `AcquisitionService`/`AcquisitionSource`/`Snapshot` exactly as they
   exist today, generalized to exactly one new provider — proving the
   whole chain end to end for one real document before generalizing
   further.
4. **Guard integration** into that one adapter's own ingestion script:
   wire extraction (slice 1) → `EvidenceBag` → guard → `SourceAssertion`
   (slice 2's columns), for that one real source only.
5. **Review workflow** (conceptual queue query against
   `SourceAssertion.identity_guard_decision IN (REVIEW_REQUIRED, …)` —
   no UI) — validated against whatever slice 3/4 actually produces.
6. **Signal promotion rule** (§13's threshold), as its own explicit,
   separately-reviewed rule set, informed by real slice 3/4 output rather
   than designed in the abstract.
7. **Scheduled/repeated discovery + n8n orchestration** — calling the
   same slice-3 adapter repeatedly; cheap by construction because
   `Snapshot`'s existing content-hash dedup already makes reruns
   idempotent (§17/§18).
8. **International adapters** — new `PublishingSource`/
   `AcquisitionSource`/extractor combinations for non-U.S. authorities,
   plus (only if slice 5's real review-queue usage proves it necessary)
   a dedicated translated-text field (§19).

## Final validation

No code changed. No database interaction occurred — this task performed
read-only repository inspection only (model/service source files, no ORM
session opened, no query executed).
