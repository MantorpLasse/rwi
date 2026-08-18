# Cross-Airport Evidence Wiring — Report

Closes the one concrete gap the MSP discovery-provider pilot found
(`docs/product/msp-authoritative-discovery-provider-pilot.md` §8/§15):
`CandidateFragment` had no way to carry alternate-airport runway-topology
evidence through to `EvidenceBag`, so a real, single-issuer,
single-topology-token document like the MAC EMAS procurement memo could
only ever reach `INSUFFICIENT_IDENTITY` for a wrong candidate (SFO),
never the more informative `REJECT_CROSS_AIRPORT`.

**Not a guard redesign.** `app/services/evidence_attachment_guard.py` is
untouched. `EvidenceBag.alternate_airport_runway_ends`/
`alternate_airport_runway_pairs` already existed and already worked
exactly as before — this slice only wires a caller-supplied value into
that existing field via `CandidateFragment`, through one new, small,
optional module.

## What the MSP pilot exposed

The real MAC memo names an issuer ("MAC"/"Metropolitan Airports
Commission") and runway tokens ("30L", "12R/30L") but never an airport
identifier, name, or location. Evaluated through the unmodified
`CandidateFragment → EvidenceBag` adapter: MSP correctly reached
`ATTACH_CONFIRMED` (issuer + topology, 2 categories); SFO reached
`INSUFFICIENT_IDENTITY`, not `REJECT_CROSS_AIRPORT`, because the guard's
contradiction check for a topology token requires either a non-matching
identifier, a pre-classified `contradicting_*` field, or
`alternate_airport_runway_ends`/`_pairs` — and none of those flowed from
`candidate_fragment_to_evidence_bag()` at the time.

## Why `INSUFFICIENT_IDENTITY` was previously correct

It was not a bug. The guard's own documented asymmetry
(`docs/architecture/ai-discovery-evidence-attachment-guard.md` §6, rule
2) is deliberate: a runway token simply *absent* from a candidate's own
topology, with nothing else pointing elsewhere, must never be treated as
contradiction — RWI's canonical inventory not (yet) covering every
airport must never be conflated with "this document is about a different
airport." Without a way to positively confirm the token belongs to one
*specific, real* other airport, `INSUFFICIENT_IDENTITY` was the only
honest, fail-closed answer. This slice adds that positive-confirmation
path; it does not relax the asymmetry.

## How alternate-airport topology now enters the evidence path

```
CandidateFragment (extraction envelope, unmodified core semantics)
    + two new OPTIONAL fields: alternate_airport_runway_ends / _pairs
      (default empty; NEVER populated by extraction itself)
        |
        v  app.services.candidate_fragment_enrichment
           .enrich_with_alternate_airport_topology(fragment, *,
               known_other_airport_runway_ends, known_other_airport_runway_pairs)
        |  -- caller-supplied, real, already-known topology of ONE
        |     specific other airport; the function computes the
        |     INTERSECTION with the fragment's OWN already-extracted
        |     runway_ends/runway_pairs (via app.services.runway_identity,
        |     reused, not reimplemented) and returns a NEW fragment
        |     carrying only that intersection.
        v
candidate_fragment_to_evidence_bag() (unmodified core logic, one new
    1:1 passthrough line: alternate_airport_runway_ends/_pairs)
        v
EvidenceBag.alternate_airport_runway_ends / _pairs (pre-existing field,
    behavior byte-for-byte unchanged)
        v
evaluate_attachment() / evaluate_attachment_for_candidates()
    (completely unmodified)
```

### Exact API/field changes

- `app/services/discovery_candidate_fragment.py`:
  - `CandidateFragment` gains two new fields, both `frozenset[str] =
    field(default_factory=frozenset)`: `alternate_airport_runway_ends`,
    `alternate_airport_runway_pairs`. Backward compatible — every
    existing call site uses keyword arguments and the fields default to
    empty, so no existing construction changes behavior.
  - `candidate_fragment_to_evidence_bag()` gains one new mapping:
    `alternate_airport_runway_ends=fragment.alternate_airport_runway_ends`,
    `alternate_airport_runway_pairs=fragment.alternate_airport_runway_pairs`
    — a pure 1:1 passthrough, no computation.
- `app/services/candidate_fragment_enrichment.py` — **new module**,
  one public function: `enrich_with_alternate_airport_topology(fragment,
  *, known_other_airport_runway_ends=frozenset(),
  known_other_airport_runway_pairs=frozenset()) -> CandidateFragment`.
- `app/services/evidence_attachment_guard.py` — **unmodified**.
- No model, migration, persistence, NASR, USAspending, Signal, or
  static-export file touched.

## Enrichment boundary

`enrich_with_alternate_airport_topology()` is deliberately:

- **Not part of extraction.** Neither `app/acquisition/mac_granicus_extractor.py`
  nor any other extractor calls it — extraction still only ever produces
  raw, un-enriched fragments (proven by `test_real_document_is_insufficient_for_sfo_without_enrichment`).
- **Not part of `CandidateFragment`.** The dataclass itself never
  computes, infers, or looks up alternate topology — the two new fields
  are plain, caller-set values, exactly like `contradicting_names`/
  `_issuers`/`_locations` already were.
- **Not part of the guard.** `evaluate_attachment()` still only ever
  *consumes* `alternate_airport_runway_ends`/`_pairs`; it never computes
  them.
- **Has no concept of any specific airport, provider, or source family.**
  It takes plain `frozenset[str]` runway-designation strings — no
  "MSP"/"MAC"/"SFO" anywhere in its own code. A caller cannot get
  alternate-airport evidence merely by asserting "this came from an
  MSP-focused provider" — the function only ever surfaces the genuine
  **intersection** between what extraction actually found in the
  fragment's own text and the topology the caller explicitly supplies
  (proven by `test_naming_the_other_airports_topology_alone_is_insufficient_without_real_overlap`
  and `test_enrichment_with_no_overlap_produces_no_alternate_evidence`).
- **No DB, no HTTP, no search-query context** — proven both structurally
  (the function's signature has no query/search/seed parameter) and
  behaviorally (`test_discovery_context_does_not_affect_enrichment_output`).

## Real MSP vs SFO result after the fix

Real fragment (the MAC EMAS procurement memo fixture), evaluated in one
`evaluate_attachment_for_candidates(bag, [MSP, SFO])` call:

| | Before enrichment | After enrichment (MSP's real canonical topology supplied) |
|---|---|---|
| MSP | `ATTACH_CONFIRMED` (issuer + topology) | `ATTACH_CONFIRMED` (unchanged) |
| SFO | `INSUFFICIENT_IDENTITY` | **`REJECT_CROSS_AIRPORT`** |

SFO's rejection reason (verbatim, generated entirely by the unmodified
guard): *"Contradicting identity evidence found against candidate 'San
Francisco International Airport': runway_topology='30L',
runway_topology='12R', runway_topology='12R/30L'. Contradiction always
vetoes positive evidence, regardless of topical overlap."* — grounded in
the actual matched runway tokens; the words "provider," "MAC," and "MSP"
never appear in it (asserted directly by test).

## Contradiction provenance

The enrichment call in this slice's tests passes `known_other_airport_runway_ends
= MSP.canonical_runway_ends` (MSP's real, already-known canonical
topology, the same `CandidateAirport` object built for guard evaluation)
— exactly the kind of "explicit deterministic enrichment step outside
CandidateFragment/guard, using known candidate-airport topology" the task
required. In a real orchestration pipeline, the caller would only supply
this once it has *independently* established (e.g. via the fragment's own
issuer-match positive evidence) which real airport the fragment belongs
to — the enrichment step never does that resolution itself; it only
carries an already-established fact's topology forward.

## Regression results

152 passed across the full regression battery: `test_discovery_candidate_fragment.py`,
`test_evidence_attachment_guard.py`, `test_discovery_evidence_persistence.py`
(genuine SFO/BOS/ORH → `ATTACH_CONFIRMED`, Allegheny-like/Morristown-like
→ `INSUFFICIENT_IDENTITY`, Haneda international → `ATTACH_CONFIRMED`
unchanged, multi-airport ambiguity → `REVIEW_REQUIRED` unchanged),
`test_discovery_governed_evidence_migration.py`, the four MSP
pilot/provider/extractor test files, the new enrichment test file, and
`test_model_contract.py` (unaffected — no DB model touched).

## Focused / full test counts

- `test_candidate_fragment_enrichment.py`: 14 tests (all new).
- `test_mac_msp_pilot_guard_evaluation.py`: 4 tests (3 replaced/rewritten,
  1 net-new — the SFO case now covers pre- and post-enrichment
  separately, plus the new primary MSP-confirmed/SFO-rejected proof).
- Net-new tests added to the suite by this slice: 15.
- Combined regression battery (new files + CandidateFragment + guard +
  persistence + migration + MSP pilot + model contract): 152 passed.
- Full suite: **789 passed** (774 baseline + 15 net-new).

## Files changed

- `app/services/discovery_candidate_fragment.py` (modified — two new
  fields, one new adapter mapping line).
- `app/services/candidate_fragment_enrichment.py` (new).
- `tests/test_candidate_fragment_enrichment.py` (new).
- `tests/test_mac_msp_pilot_guard_evaluation.py` (modified — the SFO
  case now demonstrates both the pre- and post-enrichment result).
- `docs/product/cross-airport-evidence-wiring-report.md` (new, this
  file).

No database schema, migration, persistence service, NASR, USAspending,
Signal, or static-export/UI file was touched.

## Recommendation

The gated real-DB capture script (`docs/product/msp-authoritative-discovery-provider-pilot.md`
§16, recommended-next-slice item 2) may proceed — this was the one
concrete blocker that pilot's own findings identified, and it is now
closed, proven against the same real fragment, with no change to any
persisted schema or governed pipeline. The capture script itself, and
applying `scripts/migrate_discovery_governed_evidence_slice1.py --upgrade`
against the real database, remain separate, explicitly-approved future
steps — not performed here.
