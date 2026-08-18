# MSP Authoritative Discovery Provider — Pilot

The first real, narrowly-scoped `AcquisitionProvider` for RWI, proving the
full discovery chain against one real, authoritative source family:

```
AcquisitionProvider (MACGranicusAcquisitionProvider)
    -> AcquisitionService / Snapshot (existing, unmodified)
    -> source-specific extraction (app.acquisition.mac_granicus_extractor)
    -> CandidateFragment (existing, unmodified)
    -> EvidenceBag (existing, unmodified adapter)
    -> Evidence Attachment Guard (existing, unmodified)
    -> governed persistence service (existing, unmodified, isolated DB only)
```

**Pilot only.** No real-DB migration, no real-DB write, no Signal
creation, no n8n, no scheduler, no broad crawler, no public UI change, no
deployment, no commit, no push. Baseline: branch `main`, HEAD
`4ed6018dea5ce5a530841e113e332561934cfe6d`.

## 1. Source family choice

**Chosen: `metroairports.granicus.com`** — the Metropolitan Airports
Commission's official Granicus meeting-management platform (committee
agendas, itemized consent actions, linked memo/PDF documents).

**Rejected: `metroairports.org/documents`** (the MAC's general document
library, initially the more obvious candidate). Direct research in this
pilot found its full-text search does **not** filter server-side over
plain HTTP: a raw `curl` of `?search_api_fulltext=` (unfiltered) and
`?search_api_fulltext=EMAS` (filtered) returned **byte-identical**
document listings (verified by diffing the two raw HTML responses — the
only difference was an unrelated pagination-hash query parameter). This
is a JS/AJAX-driven search widget, not a real server-side query string —
confirmed, not assumed, before rejecting it. Its pagination (`?page=N`)
*does* work over plain HTTP, but without working full-text filtering,
reaching a specific older document would require scanning many pages of
an undifferentiated, all-document-types feed — not a "small number of
requests" operation.

**Confirmed via direct `curl`/`httpx` research** (not WebFetch's
summarizing layer, which unreliably claimed the `.org` search **was**
filtering when it demonstrably was not):

| Property | `metroairports.granicus.com` |
|---|---|
| Base domain | `metroairports.granicus.com` |
| URL pattern | `ViewPublisher.php?view_id=N` (committee meeting archive) → `AgendaViewer.php?view_id=N&clip_id=M` (302 → `GeneratedAgendaViewer.php`, itemized agenda) → `MetaViewer.php?view_id=N&clip_id=M&meta_id=K` (linked document) |
| Listing/archive behavior | Real, working, plain-HTTP pagination — one committee's entire meeting history in one `ViewPublisher.php` page, reverse-chronological, `clip_id` range observed 109–2569 for one committee (view_id=1) |
| Document format | PDF (`Content-Type: application/pdf`, confirmed by `HEAD` request) |
| Date metadata | Meeting date on each archive row (`"Sep 3, 2024"`); PDF's own `Last-Modified`/`ETag` HTTP headers (confirmed: `Last-Modified: Wed, 28 Aug 2024 14:18:26 GMT` on the real document) |
| Document IDs | `view_id`/`clip_id`/`meta_id` — stable, sequential, URL-embedded, never derived from search text |
| Pagination/archive structure | Full meeting history in one page per committee (`view_id`); no further pagination needed for a bounded recent-window scan |
| Link stability | Confirmed stable — same `meta_id`-addressed PDF re-fetched at two different times in this pilot returned byte-identical content and the same `Last-Modified` header |
| Historical accessibility | Confirmed — a 2024-08-28 memo (2 years old relative to this pilot) fetched successfully via the same, unauthenticated mechanism as the most recent 2026-08-17 meeting |
| Cookies/JS required | **No** — every fetch in this pilot (`ViewPublisher.php`, `AgendaViewer.php`'s redirect, `GeneratedAgendaViewer.php`, `MetaViewer.php`) succeeded via plain unauthenticated `httpx`/`curl` GET requests |
| Rate limiting/anti-bot | None observed in this pilot's ~10 real requests; not stress-tested (deliberately, per the polite-fetching instruction) |

## 2. Acquisition behavior

`app/acquisition/mac_granicus.py`:

- `MACGranicusAcquisitionProvider` — read-only fetch of ONE already-
  discovered document URL, matching `app.acquisition.faa.FAAAcquisitionProvider`'s
  exact protocol (`.source_url`, `.version`, `.retrieve() -> AcquisitionPayload`).
  Plugs directly into the **existing, unmodified**
  `app.services.acquisition.AcquisitionService` — proven by test
  (`test_provider_plugs_into_existing_unmodified_acquisition_service`):
  no subclassing, no service change, exactly the "provider interface is
  already generic" finding from the lifecycle design (S2a).
- Repeated identical fetch → same `Snapshot` reused, `AcquisitionRunStatus.NO_CHANGE`
  (`test_repeated_identical_fetch_is_deduped_by_content_hash`).
- Changed content → new `Snapshot` (`test_changed_content_produces_a_new_snapshot`).
- No parallel storage: acquisition uses `AcquisitionSource`/`AcquisitionRun`/
  `Snapshot` exactly as they already exist; this module adds zero new
  tables/fields.

`discover_recent_meetings()` / `discover_agenda_items()` — the source
family's own listing/archive mechanism, used for **discovery** (finding
candidate document URLs), kept structurally separate from acquisition
(fetching one URL's bytes) and from extraction (deciding what's inside).
Neither function accepts a search-query-shaped parameter at all
(`test_discovery_functions_accept_no_search_query_parameter`) —
document identity (`MACGranicusAgendaItemCandidate.acquisition_source_key`,
`f"mac.granicus.document.{view_id}.{clip_id}.{meta_id}"`) is derived
purely from the archive's own stable addressing, never from what an
orchestration loop was searching for.

## 3. Provider contract

Deliberately narrow — matches the task's own boundary exactly:

- Source-family-specific (MAC Granicus only).
- Read-only HTTP fetch, `httpx`, timeouts from existing `app.config.settings`.
- Deterministic source keys (`acquisition_source_key`), deterministic
  document identity (URL-derived).
- **No** DB writes inside the provider (`test_provider_module_imports_no_database_layer`
  greps the module source for `SessionLocal`/`app.database`/`app.models` —
  none present).
- **No** Source/SourceAssertion logic, **no** guard logic, **no** Signal
  logic anywhere in this module.

## 4. Extraction behavior

`app/acquisition/mac_granicus_extractor.py` — rule-based (regex), not
AI-assisted, in this slice. Split into a thin PDF-bytes-to-text layer
(`_extract_text`, pdfplumber) and a pure-text extraction layer
(`_fragment_from_text`), mirroring the existing
`app/acquisition/faa_construction_report.py` convention.

- **Relevance gate**: a small, topical keyword list (`EMAS`, "engineered
  material arresting", "arresting system", "runway safety area", "runway
  rehabilitation/reconstruction/replacement/resurfacing/repair) —
  deliberately **not** including "Runway Safe" (the vendor name), per the
  task's own explicit instruction. A document with none of these terms
  produces `None`, not a `CandidateFragment`
  (`test_non_relevant_text_produces_no_candidate_fragment`).
- **Runway extraction**: `Runway 30L` → end `"30L"`; `Runway 12R-30L`
  (MAC's own hyphenated pair notation, confirmed from the real document —
  *not* the slash notation `normalize_pair()` expects) → normalized to
  `"12R/30L"` before being placed in `runway_pairs`, plus both ends
  individually.
- **Issuer extraction — a genuine, real finding**: the real memo **never
  once spells out "Metropolitan Airports Commission"** — it only ever
  refers to itself as **"MAC"** ("MAC's purpose", "MAC has broad
  powers"). The extractor recognizes both the full name and the bare
  `MAC` self-reference (word-boundary, case-sensitive to avoid matching
  the common lowercase word), confirmed correct and locked in by test
  (`test_issuer_extraction_recognizes_bare_mac_self_reference`,
  `test_lowercase_mac_word_is_not_mistaken_for_the_issuer`).
- **Vendor extraction**: structural, not vendor-specific — matches
  `"sole source procurement with X for"` and `"Purchase Order to X in
  the amount of"` phrasing (both real, recurring MAC memo boilerplate),
  never a literal `"Runway Safe"` search. Proven generic by test with a
  different vendor name substituted
  (`test_vendor_extraction_is_not_hardcoded_to_runway_safe`). Vendor
  findings are reported alongside the `CandidateFragment` for audit, not
  fed into the guard (which has no vendor concept) and not added as a
  new `CandidateFragment` field (that dataclass's own docstring already
  explains why "organizations" was deliberately not added as separate
  from `issuers`).
- **Money extraction**: `$1,590,000.00` (context: `advance_deposit`),
  `$19,000,000.00` (context: `cip_project_ceiling`) — both correctly
  distinguished, never collapsed into one figure.
- **Date extraction**: `August 28, 2024` (`memo_date`), `December 18,
  2023` (`prior_approval_date`).
- **Contract/project identifiers**: `"Consent Item 2.3.2"`, `"2024-2030
  CIP"`.

## 5. Real documents reviewed

**94 real agenda items**, across **4 real meetings**, discovered via the
archive's own listing mechanism (never hardcoded):

| Meeting | Committee | Items | Relevant |
|---|---|---|---|
| Aug 17, 2026 (clip 2569) | Full Commission | — | 0 |
| Aug 3, 2026 (clip 2565) | Planning, Development and Environment | — | 0 |
| Aug 3, 2026 (clip 2566) | Operations, Finance and Administration | — | 0 |
| **Sep 3, 2024 (clip 2349)** | **Planning, Development and Environment** | 23 | **1** |

(Item counts for the three most-recent 2026 meetings, plus the 23 for the
historical meeting, sum to the reported 94.) **Zero false positives**
across all 94 real items — every one of the 93 non-EMAS items (restroom
upgrades, telecom equipment, radio purchases, budget adjustments, a
sustainability update, etc.) was correctly judged not relevant; the one
genuinely EMAS-relevant item was correctly found.

## 6. CandidateFragments

**1 real `CandidateFragment`** built from the one relevant, fetched
document (a 1,085,250-byte, real PDF, `Content-Type: application/pdf`,
`Last-Modified: 2024-08-28`).

## 7. MSP guard results

`evaluate_attachment_for_candidates(bag, [MSP, SFO])` against the real
fragment and real RWI canonical MSP topology: **`ATTACH_CONFIRMED`** —
2 independent positive categories (`issuer` = "Metropolitan Airports
Commission" matching MSP's known issuer; `runway_topology` = `30L`/`12R`/
`12R/30L`, all genuinely present in MSP's real canonical `Runway`/
`RunwayEnd` inventory), none contradicted.

## 8. SFO cross-airport rejection result

**Honest finding, not the task's assumed shorthand**: the real fragment
reaches **`INSUFFICIENT_IDENTITY`** for SFO through the currently-
committed, unmodified `CandidateFragment` → `EvidenceBag` adapter — not
`REJECT_CROSS_AIRPORT`. Precise mechanism: this specific real document
never names an airport identifier, name, or location (only an issuer and
a runway token) — the guard's contradiction logic requires either (a) an
identifier that doesn't match the candidate, or (b) a pre-classified
`contradicting_names`/`_issuers`/`_locations`, or (c)
`alternate_airport_runway_ends`/`_pairs` corroborating that the runway
token belongs to a *specific, different* airport. None of these currently
flow from `candidate_fragment_to_evidence_bag()`, so the absent-only
topology token ("30L" simply isn't in SFO's own set) is correctly treated
as *absence*, not *contradiction* — exactly the guard's documented,
deliberate asymmetry (`docs/architecture/ai-discovery-evidence-attachment-guard.md`
S6, rule 2: "RWI's canonical inventory not covering every airport must
never be conflated with 'this document is about a different airport'").

**This is still safe**: both `INSUFFICIENT_IDENTITY` and
`REJECT_CROSS_AIRPORT` leave `airport_id = NULL` — SFO never gets this
evidence attached either way. The task's specific expectation of
`REJECT_CROSS_AIRPORT` assumed a mechanism (an issuer→airport reference
table, or cross-airport topology corroboration) that the guard design's
own documents (`ai-discovery-evidence-attachment-guard.md` S12, listed as
future "slice 4") explicitly defer as **not yet built**.

**Supplementary demonstration** (`test_cross_airport_rejection_mechanism_works_when_alternate_topology_is_supplied`,
deliberately bypassing `CandidateFragment` to use `EvidenceBag`'s own
**already-existing** `alternate_airport_runway_ends`/`_pairs` field
directly): when orchestration supplies MSP's real canonical topology as
independently-resolved corroboration, the **same real evidence**
correctly reaches `REJECT_CROSS_AIRPORT` for SFO. The mechanism is real
and works — it is simply not yet wired through `CandidateFragment` (see
§15, recommended next slice).

## 9. Isolated persistence result

`persist_discovery_fragment()` against an isolated in-memory DB (with
`scripts/migrate_discovery_governed_evidence_slice1.py`'s columns present
via `Base.metadata.create_all()`): exactly **one** `Source` and **one**
`SourceAssertion` created, `airport_id = MSP`, `identity_guard_decision =
"ATTACH_CONFIRMED"`, `raw_relevant_text` byte-identical to the extracted
fragment text. Never touches SFO's row. **Never touches the real
database** — every persistence test in this pilot uses its own isolated
`sqlite:///:memory:` engine.

## 10. Idempotency

- **Rediscovery** (same `document_identity`, called twice): second call
  reuses the same `Source` and `SourceAssertion` (`source_created=False`,
  `source_assertion_created=False`).
- **Changed fragment text** (same document, appended addendum text): new,
  independent `SourceAssertion` (`source_assertion_created=True`,
  different id), `Source` still reused.
- **Real repeated fetch**: `AcquisitionService`'s own existing content-
  hash dedup reuses the same `Snapshot` and reports `NO_CHANGE`
  (proven with the provider plugged directly into the unmodified
  service).

## 11. Temporal / change-detection readiness

The Granicus archive is naturally monitoring-friendly without any new
mechanism: `ViewPublisher.php?view_id=N` grows by appending new,
higher-numbered `clip_id` rows at the top as new meetings occur (the live
pilot run found the most recent real meeting at clip 2569, dated the same
day as this pilot). A future scheduled slice could:

- `NEW_DOCUMENT`: re-run `discover_recent_meetings(view_id, max_meetings=N)`
  periodically; any `clip_id` not seen before is new.
- `UNCHANGED_DOCUMENT`: re-fetching an already-seen `MetaViewer.php` URL
  hits `AcquisitionService`'s existing content-hash dedup
  (`AcquisitionRunStatus.NO_CHANGE`) automatically — no new logic needed.
- `CHANGED_DOCUMENT`: a same-URL, different-bytes fetch (e.g. a corrected
  memo re-uploaded under the same `meta_id`) would produce a new
  `Snapshot` via the same existing hash-comparison mechanism.

**Not implemented in this pilot** (per instruction) — no scheduler, no
n8n wiring — but nothing here blocks it; it is purely an orchestration
concern layered on top of what already exists.

## 12. Investor-relevant findings

| Type | Statement |
|---|---|
| `EXPLICIT_DOCUMENT_FACT` | Runway 30L (part of Runway 12R/30L) EMAS bed "has reached its life expectancy and requires replacement." |
| `EXPLICIT_DOCUMENT_FACT` | $1,590,000.00 advance-deposit Purchase Order requested to Runway Safe (sole-source), to secure a production slot. |
| `EXPLICIT_DOCUMENT_FACT` | $19,000,000.00 total CIP ceiling for the "2025 30L EMAS Replacement," approved 2023-12-18. |
| `EXPLICIT_DOCUMENT_FACT` | "The Runway Safe EMAS bed is the only proprietary product approved by the FAA" (the memo's own stated rationale for sole-source). |
| `EXPLICIT_DOCUMENT_FACT` | A separate installation contract "will be bid in 2025 ... under the oversight of Runway Safe" — material supply and installation are explicitly two separate contracts. |
| `DERIVED_IDENTITY_ATTACHMENT` | This document belongs to MSP (via issuer + real canonical-topology match — §7), not SFO or any other candidate. |
| `INVESTOR_SIGNAL_CANDIDATE` | A real, dated, dollar-denominated Runway Safe EMAS-replacement procurement action at a major hub — genuine market-activity signal (not created as a `Signal` in this pilot). |
| `CORROBORATION_WITH_EXISTING_RWI_DATA` | RWI **already independently holds** related MSP EMAS evidence from a **different** source: `SourceAssertion` id 78 (USAspending grant, Phase 3, 475 ft of the same Runway 12R/30L EMAS safety area, FAA-funded) and `Signal` id 67 ("MSP EMAS-order (Runway Safe confirmed vendor)"). This MAC memo is a genuinely **new**, complementary, corroborating document from an independent official source — not a duplicate. |
| `NOT_ESTABLISHED` | Whether the $19M CIP ceiling or the $1.59M deposit represents the *final* contract value (the memo itself only authorizes the deposit and a sole-source relationship, not a final total). |
| `NOT_ESTABLISHED` | Installation timing/completion (the 2025 installation contract is explicitly "will be bid in 2025," not yet awarded as of this document). |
| `NOT_ESTABLISHED` | Revenue recognition, profitability, or shipment timing for Runway Safe — none of these are in-scope for this document type at all. |

## 13. No-Signal boundary

Proven structurally and by test: neither `app/acquisition/mac_granicus.py`
nor `app/acquisition/mac_granicus_extractor.py` imports `app.models.Signal`
at all; `test_no_signal_or_canonical_fact_rows_created` runs the full
pipeline end to end and asserts `Signal` count is zero. Even though §12's
findings are genuinely investor-relevant, **no `Signal` was created** —
per instruction, this pilot produces only the `SignalCandidate`-style
analysis in §12's table; the Signal-promotion threshold (design doc
§13/§12 slice 6) remains undesigned and is explicitly out of scope here.

## 14. Web-fetch limitations

- `metroairports.org/documents`'s full-text search does not work over
  plain HTTP (§1) — ruled out as the discovery mechanism, not used
  anywhere in the final provider.
- No blocked/failed fetches occurred in this pilot's real run (0 of 5 real
  HTTP requests in the final automated run failed) — the earlier
  exploratory `curl` research (§1) is what surfaced the `.org` search
  limitation before it could become a provider defect.
- Not tested: sustained/high-frequency fetching, since the task
  explicitly asks for a small, polite request budget — this pilot's
  entire live run (research + automated pilot run) totaled under 15 real
  HTTP requests against the live site.

## 15. Lessons for future providers

1. **Verify a listing/search mechanism actually filters server-side
   before building around it** — a WebFetch summary confidently (and
   wrongly) reported the `.org` site's search as filtering; only a raw
   `curl` diff of two responses exposed that it does not. Always verify
   with a raw HTTP diff, not a summarizing tool's claim.
2. **An issuing body's own self-reference in real documents may not match
   its "official" full name** — this MAC memo never once spells out
   "Metropolitan Airports Commission." A source-specific extractor must
   inspect real documents for the actual self-reference convention used
   (here: bare "MAC"), not assume the full legal name always appears.
3. **A source family's own pair-notation convention may not match RWI's
   canonical `"/"` separator** — MAC's own memos write "12R-30L"
   (hyphen); `runway_identity.normalize_pair()` requires `"/"`. A
   source-specific extractor is the correct, narrow place to reformat
   this — not a change to the shared normalization module.
4. **Vendor-name-generic regexes over recurring procurement-boilerplate
   phrasing generalize better than a hardcoded vendor list** — the same
   two patterns used here would extract a different vendor from a
   differently-worded MAC memo using the same template.
5. **`CandidateFragment`'s adapter does not currently carry
   `alternate_airport_runway_ends`/`_pairs`** — a real, concrete gap
   found by this pilot (§8), worth closing in a future slice specifically
   because it is the difference between `INSUFFICIENT_IDENTITY` and a
   more informative `REJECT_CROSS_AIRPORT` for single-issuer,
   single-topology-token documents like this one.
6. **A 302-redirect-then-generated-content pattern (`AgendaViewer.php` →
   `GeneratedAgendaViewer.php`) is common in Granicus-based municipal/
   agency sites** — `httpx`'s `follow_redirects=True` handles it
   transparently; worth remembering for any future Granicus-hosted
   authority (many US airport authorities and municipalities use this
   exact platform).

## 16. Recommended next slice

1. **Wire `alternate_airport_runway_ends`/`_pairs` (or a small,
   explicit issuer→airport reference table) through `CandidateFragment`/
   `candidate_fragment_to_evidence_bag()`** — directly closes the §8/§15
   gap, turning today's safe-but-uninformative `INSUFFICIENT_IDENTITY`
   into a genuinely more auditable `REJECT_CROSS_AIRPORT` for documents
   shaped like this one.
2. **A real, gated capture script** (`--allow-live-network
   --allow-database-write`, mirroring `app/scripts/capture_faa_emas.py`'s
   own discipline exactly) that runs this pilot's discovery+acquisition
   path against the real database — only after the real-DB migration
   (`scripts/migrate_discovery_governed_evidence_slice1.py --upgrade`) is
   separately, explicitly approved and applied.
3. **Generalize `discover_recent_meetings`/`discover_agenda_items` to a
   second MAC committee `view_id`** (e.g. Full Commission, view_id
   observed as a distinct listing from PD&E) to prove the discovery layer
   isn't accidentally narrowed to one committee's HTML shape.
4. Only after 1–3: a **scheduled/repeated discovery slice**, reusing this
   exact provider — cheap by construction because `Snapshot`'s existing
   content-hash dedup already makes reruns idempotent (§10/§11).
