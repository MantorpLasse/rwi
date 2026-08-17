# USAspending Airport Resolution — Fail-Closed Fix

Fixes the root cause identified in
[`docs/domain/allegheny-unresolved-airport-investigation.md`](allegheny-unresolved-airport-investigation.md)
and flagged as adjacent, out-of-scope debt in
[`docs/domain/canonical-runway-foundation-merge-readiness-review.md`](canonical-runway-foundation-merge-readiness-review.md)
§8. **The real database was not touched anywhere in this task** — every
reproduction and every proof below used isolated, in-memory SQLite
databases only.

## 1. Historical problem

`scripts/import_usaspending_grants.py::resolve_airport()` had a fallback
path that, when a USAspending grant's beneficiary city/state matched no
existing `Airport`, created a **new** `Airport` row named after the
grant's `recipient_name` — the organization that received the money, not
necessarily the airport itself. A grant recipient can be an airport
*authority* operating more than one facility, so naming a new canonical
`Airport` after it fabricates identity RWI cannot actually support. This
violates the project's fail-closed principle: ambiguous or unresolved
airport identity must never be guessed into existence.

## 2. Allegheny example / root cause

Airport id 75 was created exactly this way: the grant's recipient was
"Allegheny County Airport Authority" (the public authority operating both
Pittsburgh International and Allegheny County Airport), its beneficiary
sentence named "West Mifflin, Pennsylvania" with no existing `Airport`
match, so `resolve_airport()` created a new `Airport` literally named
"Allegheny County Airport Authority" — an organization, not an airport.
This was already investigated, corroborated with independent NASR
evidence, and deterministically corrected (id 75 is now `faa_code=AGC`,
`name=Allegheny County Airport`) in a prior, narrowly-scoped task. That
correction never touched `import_usaspending_grants.py` itself, so the
same fallback branch remained live in current code — this task fixes the
ingestion code, not the already-corrected row.

## 3. Previous resolver decision flow

`resolve_airport(session, grant)` tried two patterns in order:

1. **Embedded Loc ID** (`AIRPORT\s*\(([A-Z0-9]{3,4})\)` against the grant
   description). If matched: look up an existing `Airport` by
   `iata_code`/`icao_code`/`faa_code`; if none, **create** a new `Airport`
   with that code.
2. **Beneficiary sentence** (`FEDERAL FUNDING FOR AIRPORTS ASSOCIATED
   WITH <CITY>, <STATE>.`). If matched: look up an existing `Airport` by
   `city`/`state_region` (case-insensitive).
   - Exactly one match → use it.
   - More than one match (ambiguous) → return "no airport", nothing
     created.
   - Zero matches → **create** a new `Airport` named after
     `grant.recipient_name.title()` — **the bad fallback**.
   - Neither pattern matched at all → return "no airport", nothing
     created.

`import_all()` then either created a `Source` + high-confidence `Signal`
(airport resolved) or silently skipped the grant entirely — **no `Source`
was created either** — whenever `resolve_airport()` returned `None`. This
meant every unresolved grant (ambiguous match, no-match fallback prevented
manually, or fully unattributable) left **zero trace** in the database:
not just no `Airport`, but no evidence at all.

## 4. Reproduction

Reproduced in an isolated in-memory database with a grant structurally
identical to the real Allegheny case but using a fictional
airport/authority/city so it never depends on production Airport id 75:

- Recipient: `"Fremont County Airport Authority"` (an authority, not an
  airport)
- Description names an exact runway (`RUNWAY 9/27`) and beneficiary city/
  state (`RIVERTON, WYOMING`)
- No embedded Loc ID
- No existing `Airport` for Riverton, Wyoming

**Before the fix**, calling `resolve_airport()` on this grant:

```
Resolver result: airport = <Airport>, created = True
  Airport row created:
    name: Fremont County Airport Authority
    city: Riverton
    state_region: Wyoming
    faa_code: None
```

An `Airport` named after the authority was fabricated, exactly
reproducing the Allegheny defect on a non-production case.

## 5. New fail-closed rule

**A new canonical `Airport` is created ONLY when the grant text embeds an
actual FAA/ICAO/IATA-shaped Loc ID** (the existing `LOC_ID_PATTERN` match).
Every other case is `UNRESOLVED`:

- No Loc ID and no beneficiary city/state sentence at all (pure state
  block-grant-administration record).
- A beneficiary city/state sentence with more than one matching existing
  `Airport` (ambiguous).
- A beneficiary city/state sentence with **zero** matching existing
  `Airport` — the fallback that fabricated Allegheny/Morristown/etc. is
  now removed. A recipient organization name is never used, alone, as
  canonical Airport identity, and city/state alone is never sufficient
  either.

`resolve_airport()` now returns a small `AirportResolution` result
(`app/scripts/import_usaspending_grants.py`) instead of a `(Airport |
None, bool)` tuple, with an explicit `status` of `resolved_existing`,
`resolved_new`, or `unresolved`, plus `raw_identifier`/`raw_name`/`reason`
carrying exactly what the grant text said — used to preserve evidence
(§7).

## 6. Deterministic resolution rules (exact code change)

`scripts/import_usaspending_grants.py`:

- **New `AirportResolution` dataclass** (`status`, `airport`,
  `raw_identifier`, `raw_name`, `reason`) and three status constants
  (`RESOLVED_EXISTING`, `RESOLVED_NEW`, `UNRESOLVED`).
- **`resolve_airport()` rewritten** to return `AirportResolution` instead
  of a tuple. Behavior for the two deterministic paths — matching an
  existing `Airport` by code, matching an existing `Airport` by
  city/state, and creating a new `Airport` from an embedded Loc ID — is
  **unchanged**. The only behavior removed is the recipient-name/city-
  state new-`Airport` fallback; that branch, and the fully-unattributable
  branch, now both return `UNRESOLVED` with the raw evidence attached
  instead of either fabricating an `Airport` or returning bare `None`.
- **`import_all()` updated**: the `Source` row is now created
  **unconditionally** (it has no `Airport` foreign key — see §7) rather
  than only when an airport was resolved. A `Signal` is created only for
  `resolved_existing`/`resolved_new` (`Signal.airport_id` is a required,
  non-nullable column — it structurally cannot represent "no airport").
  For `UNRESOLVED`, a `SourceAssertion` is created instead (§7).

No model/schema change was needed — `Source.airport_id` doesn't exist
(`Source` was already airport-independent), and `SourceAssertion.airport_id`
was already nullable.

## 7. Evidence preservation behavior for unresolved grants

**Preferred existing mechanism, not a new one.** `SourceAssertion`'s own
docstring is "one upstream record's claim, preserved before any identity
reconciliation" — exactly the "evidence exists, identity doesn't (yet)"
state this task needed, already used elsewhere in the repository
(`scripts/backfill_legacy_source_assertions.py` already creates
`assertion_type="project_construction"` rows with `airport_id=None` for
exactly this shape of upstream record). No new table, column, or
reconciliation subsystem was introduced.

For every `UNRESOLVED` grant, `import_all()` now creates:

- A `Source` row — `title` (`"USAspending grant: {recipient}"`),
  `summary` (the full grant description text), `document_reference`
  (award ID), `url` (USAspending award URL), `external_id`
  (`"usaspending:{grant.external_id}"`, unique — the same idempotency key
  already used for resolved grants).
- A `SourceAssertion` row — `airport_id=NULL`,
  `assertion_type="project_construction"`, `raw_airport_name` (the
  recipient name, exactly as it would have named the fabricated
  `Airport`), `raw_airport_identifier` (the extracted `"City, State"`
  text, when a beneficiary sentence was found), `raw_relevant_text` (the
  full grant description), `source_record_identifier` (the same
  `external_id`, satisfying the model's own record-identity constraint
  and reusing the established idempotency convention),
  `evidence_quality="unverified_candidate"`, `review_state="unreviewed"`.

Nothing is discarded: recipient, city/state (when present), the full
award description (which, as in the Allegheny case, often names an exact
runway designation), and the award/URL reference are all preserved and
queryable — reviewable later without re-fetching from USAspending.

## 8. Deterministic existing-Airport behavior

Unchanged. A grant whose embedded Loc ID or beneficiary city/state
matches exactly one existing `Airport` reuses that `Airport` and creates
a `Signal` exactly as before (`RESOLVED_EXISTING`).

## 9. Deterministic new-Airport behavior

Unchanged. A grant with an embedded Loc ID that matches no existing
`Airport` still creates a new one from that Loc ID (`RESOLVED_NEW`) — the
only difference from before is this is now the *only* way a new `Airport`
is created by this importer.

## 10. Ambiguous-case behavior

A beneficiary city/state matching more than one existing `Airport` is
`UNRESOLVED` (was already "no airport" before this fix, but previously
lost all evidence too — now the `Source`/`SourceAssertion` are preserved
exactly as for any other unresolved case).

## 11. Idempotency

Unchanged mechanism, now covering the unresolved path too:
`import_all()` checks `Source.external_id` **before** doing any
resolution work; on a rerun, an already-imported grant (resolved *or*
unresolved) is skipped entirely (`stats["already_imported"] += 1`), so no
duplicate `Source`, `SourceAssertion`, or `Airport` is ever created.
Verified directly: importing the same unresolved grant twice produces
exactly one `Source` and one `SourceAssertion` on the second run
(`unattributable == 0`, `already_imported == 1`).

## 12. Tests

`tests/test_import_usaspending_grants.py` — 15 pre-existing tests, 3 net
new (18 total; 2 tests were rewritten in place to assert the corrected
behavior rather than being counted as new):

- `test_resolve_airport_matches_existing_via_embedded_loc_id`,
  `test_resolve_airport_matches_existing_via_beneficiary_city_state` —
  unchanged behavior, updated only for the new `AirportResolution` return
  shape.
- `test_resolve_airport_fails_closed_when_beneficiary_city_state_has_no_existing_airport`
  (rewritten from `..._creates_new_airport_from_beneficiary_sentence`) —
  the historical bad case (Morristown), now asserts `UNRESOLVED` and zero
  `Airport` rows instead of `created=True`.
- `test_resolve_airport_fails_closed_for_allegheny_shaped_authority_recipient`
  (new) — the Allegheny-shaped authority-recipient reproduction from §4,
  asserting `UNRESOLVED`, zero `Airport` rows, and the exact
  raw name/identifier/reason preserved.
- `test_resolve_airport_creates_new_airport_from_embedded_loc_id_when_unknown`
  — unchanged behavior (deterministic new-Airport creation still works).
- `test_resolve_airport_fails_closed_when_ambiguous` (rewritten from
  `..._returns_none_when_ambiguous`) — same ambiguous scenario, now
  checking the `UNRESOLVED` status/reason.
- `test_resolve_airport_fails_closed_when_unattributable` (rewritten from
  `..._returns_none_when_unattributable`) — same no-pattern-match
  scenario, now checking `UNRESOLVED`/`reason`.
- `test_import_all_creates_signal_for_matched_airport` — unchanged
  behavior, unaffected by the fix.
- `test_import_all_fails_closed_and_preserves_evidence_for_beneficiary_only_match`
  (rewritten from `..._creates_new_airport_when_unmatched`) — full
  pipeline: zero `Airport`/`Signal` rows, but `Source` and
  `SourceAssertion` created with the exact recipient/city-state/
  description/evidence-quality/review-state values.
- `test_import_all_fails_closed_for_allegheny_shaped_authority_recipient`
  (new) — full-pipeline version of §4's reproduction.
- `test_import_all_preserves_evidence_when_totally_unattributable`
  (rewritten from `..._skips_unattributable_grants`) — the
  no-pattern-match-at-all case: previously asserted **zero** `Source`
  rows; now asserts a `Source` and an unresolved `SourceAssertion` are
  both preserved.
- `test_import_all_does_not_manufacture_duplicate_unresolved_rows_on_rerun`
  (new) — reruns an unresolved grant twice; exactly one `Source` and one
  `SourceAssertion`, `unattributable` is 0 on the rerun.
- `test_import_all_is_idempotent_on_rerun` — unchanged behavior for the
  resolved-airport path.

Also run: `tests/test_source_assertions.py`,
`tests/test_backfill_legacy_source_assertions.py` (both pass unchanged,
confirming no conflict with the existing `SourceAssertion` domain).

## 13. Isolated validation

Run against isolated in-memory databases only (never the real one),
covering all required cases:

| Case | Result |
|---|---|
| A. Deterministic existing Airport (city/state) | `resolved_existing`, reused the existing row |
| B. Deterministic new Airport (embedded Loc ID) | `resolved_new`, created with the real code |
| C. Unresolved authority/operator case | `unresolved`, **no** Airport created, reason recorded |
| D. Ambiguous case (two matching Airports) | `unresolved`, reason recorded, no duplicate/guessed match |
| E. Repeated unresolved import | First run: 1 `Source` + 1 `SourceAssertion`, 0 Airports. Second run: `already_imported=1`, `unattributable=0`, still exactly 1 `Source`/1 `SourceAssertion`, 0 Airports |

No real USAspending network fetch or real-database import was performed
in this task.

## 14. Real DB read-only scan for other historical fallback-created candidates

Read-only query against `data/runway_safe.db` for the exact
self-documenting `notes` marker the buggy fallback always wrote
(`"...approximated from the USAspending grant recipient..."`):

| id | name | city | state_region | faa_code | iata_code | icao_code |
|---|---|---|---|---|---|---|
| 74 | Town Of Morristown | Morristown | New Jersey | MMU | MMU | KMMU |
| 75 | Allegheny County Airport | West Mifflin | Pennsylvania | AGC | — | KAGC |

Both rows carry the historical fallback's `notes` marker. Airport 75 was
already deterministically corrected in a prior task (name and codes both
fixed; the stale `notes` text just wasn't removed, since that correction
was scoped to identifiers only). **Airport 74 still has the wrong `name`**
("Town Of Morristown", the recipient's name) despite already having real
`faa_code`/`iata_code`/`icao_code` values (`MMU`/`MMU`/`KMMU` — Morristown
Municipal Airport) — apparently enriched with identifiers at some point
without the `name` itself ever being corrected. A broader query for any
`Airport` with **all three** identifier codes `NULL` (the fully-unresolved
shape) returned **zero rows** — no other currently-unresolved fallback
survivor exists beyond these two, and both already carry real identifiers.

No row was read-written, merged, or modified — this is a read-only
finding for follow-up consideration only, per this task's explicit scope.

## 15. Real DB remained untouched

| | Before this task | After this task |
|---|---|---|
| DB size | `667648` bytes | `667648` bytes |
| DB mtime | unchanged | unchanged |
| `Runway` count | 180 | 180 |
| `RunwayEnd` count | 360 | 360 |
| Canonical classification | 76 `ALREADY_COMPLETE`, 0 unresolved/ambiguous/conflict | unchanged |

No real-database import, migration, or write of any kind was performed in
this task. Every reproduction and proof above used an isolated in-memory
SQLite database, created fresh per test/script run.

## 16. Recommended follow-up

- **Airport id 74's `name`** ("Town Of Morristown") should be corrected to
  the airport's real name (Morristown Municipal Airport) in a small,
  separate, evidence-backed correction task — the same shape of fix
  already applied to Airport 75, scoped to that one row's `name` field
  only. Not performed here per this task's explicit "no cleanup" scope.
- The two rows' stale `notes` text (referencing the now-fixed ingestion
  bug) could be updated or cleared in the same follow-up, for accuracy —
  low priority, cosmetic only.
- No other currently-unresolved USAspending-fallback candidates were
  found; no further ingestion-code follow-up is identified beyond this
  fix.
