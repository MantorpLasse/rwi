# Public Canonical Runway Inventory — Product Slice Report

Turns the completed U.S. canonical runway foundation (76/76 airports,
`Runway`=180, `RunwayEnd`=360, 0 unresolved/ambiguous/conflict) into the
first visible public-product improvement identified in
[`docs/product/bos-orh-public-intelligence-gap-analysis.md`](bos-orh-public-intelligence-gap-analysis.md).
**This task made no database write** — every change is in the public
export/view/template layer only. EMAS publication behavior is deliberately
unchanged.

## 1. Old suppression behavior

`app/static_export/build.py::_airport_view()` unconditionally omitted the
`runways` field from every airport's public view model, citing
`docs/ui/mdw-runway-diagnosis.md`. `app/static_export/templates/airport_detail.html`
correspondingly never rendered a "Banor" section at all.

## 2. Why the old rationale was obsolete

`docs/ui/mdw-runway-diagnosis.md` was written **before the canonical
`RunwayEnd` model existed** — it states verbatim *"There is no `RunwayEnd`
table or model in this codebase"* and that MDW had exactly **1** `Runway`
row at the time (a "non-exhaustive, one-row-per-airport placeholder").
Both statements are now false: `RunwayEnd` exists and is complete for all
76 U.S. airports, and MDW alone now has **4** `Runway` rows, each with
governed `length_m`/`width_m`/`surface` populated from NASR. The condition
the original code comment said to wait for ("Restore once RWI has a
governed canonical runway/runway-end inventory") is met.

## 3. Public semantics chosen

Per this task's explicit scope: publish **only the physical runway pair
designation** (e.g. `4L/22R`), sourced from the canonical `Runway` model.

- **No EMAS association implied** — the new "Banor" section says nothing
  about EMAS; "EMAS idag" remains entirely separate, driven only by
  `reviewed_identities`/`nasr_presence`, unchanged.
- **No `RunwayEnd` exposure** — only `Runway.designation` (the pair) is
  shown, not individual `RunwayEnd` rows, ids, or reconciliation state.
- **Length/surface deliberately NOT shown.** Checked read-only across all
  180 governed `Runway` rows: `length_m`/`width_m` are 100% populated, but
  `surface` — though also 100% populated — is a **stylistic mix**: some
  rows carry pre-canonical human-readable text (`"Asphalt/Concrete"`)
  while NASR-sourced canonical rows carry raw FAA codes (`ASPH`, `CONC`,
  `ASPH-CONC`, `GRVL`, `TURF`). Per this task's own instruction ("if
  consistency is poor, show designation only"), designation-only was
  chosen — a code-value badge like `ASPH` without translation would be
  the kind of "misleading partial presentation" the instructions warn
  against, and building a surface-code glossary mapping is out of scope
  for this slice.
- **No formatting reinvention.** Runway designations are rendered exactly
  as canonically stored (`9/27`, not `09/27`) — the repository's existing,
  established convention (confirmed identical for BOS/ORH/MDW/CGF before
  this change), not a new zero-padding scheme.

## 4. Implementation path

Data flow: `Airport.runways` (canonical `Runway` rows, already complete)
→ `_runway_view()` (new, in `app/static_export/build.py`) → `_airport_view()`'s
`runways` field (previously omitted here entirely) → `airport_detail.html`'s
new "Banor" card (pill list, reusing the exact `<span class="pill status">`
markup pattern already used for "EMAS idag") → `data.json`'s
`airports[].runways` array.

Files changed:

- `app/static_export/build.py` — added `_runway_view()` (designation-only
  projection); added `selectinload(Airport.runways)` to the airports
  query; replaced the stale suppression comment with the `runways=...`
  field.
- `app/static_export/presentation.py` — added a `"runways": "Banor"` /
  `"Runways"` (sv/en) label.
- `app/static_export/templates/airport_detail.html` — added a "Banor"
  card (always rendered, in the main column so it appears regardless of
  whether the rare `website_url` sidebar exists); removed the stale
  suppression comment; updated the grid-2 explanatory comment.
- `tests/test_static_export.py` — 2 existing tests updated (their old
  assertions directly encoded the suppression behavior), 4 new tests
  added.
- `tests/test_apply_canonical_runway_inventory_us_clean_batch.py` and
  `tests/test_apply_canonical_runway_inventory_us_newly_clean_batch.py` —
  1 test each renamed/updated (same reason).

No schema, model, migration, ingestion, or reconciliation code was
touched.

## 5. BOS result

Read-only verified against the real database and via a local (non-deployed)
static export rebuild. BOS (Airport id 3) publishes all 6 canonical
runways: `14/32`, `15L/33R`, `15R/33L`, `4L/22R`, `4R/22L`, `9/27`.

## 6. ORH result

ORH (Airport id 44) publishes both canonical runways: `11/29`, `15/33`.

## 7. Nationwide/generic behavior

No airport-specific code exists anywhere in the implementation. Verified
read-only against the real database for 4 representative airports:

| Airport | Runways published |
|---|---|
| BOS (id 3) | 14/32, 15L/33R, 15R/33L, 4L/22R, 4R/22L, 9/27 |
| ORH (id 44) | 11/29, 15/33 |
| MDW (id 12) | 13L/31R, 13R/31L, 4L/22R, 4R/22L |
| CGF (id 57) | 6/24 (smallest-inventory case) |

Also covered by 2 new isolated tests seeding synthetic 6-runway and
2-runway airports under generic names (not BOS/ORH), plus a zero-runway
airport test for graceful empty-state handling.

## 8. EMAS publication deliberately unchanged

`reviewed_identities`/`nasr_presence` logic in `_airport_view()` was not
touched. Verified three ways:

1. Read-only real-DB re-query after implementation: MDW/CGF's 6 protected
   `PhysicalInstallationIdentity` links unchanged; BOS/ORH still have 0
   reviewed identities and 0 promoted NASR-presence rows, exactly as
   before.
2. Generated (local-only) BOS/ORH pages both still show exactly:
   *"Ingen aktuell EMAS-förekomst är publicerad från granskad eller
   FAA-cykelbaserad evidens."*
3. New regression test
   `test_build_site_runway_publication_does_not_affect_emas_publication_rules`
   asserts an airport with governed runways but no reviewed/promoted
   evidence still shows the unresolved-EMAS empty state.

No `raw_runway_end_value → runway_end` promotion, no
`PhysicalInstallationIdentity` creation, and no installation
current-state logic were introduced — all remain exactly as before this
task, reserved for a future, separate reconciliation slice.

## 9. Tests

`tests/test_static_export.py`: 18 tests (14 baseline + 4 new) — full
BOS-shape (6 runways) and ORH-shape (2 runways) publication, reciprocal-
end designation rendering, designation-only public projection (no id/
length/surface leak), graceful empty state for a runway-less airport, and
explicit confirmation that EMAS publication rules are unaffected. Also
updated (not counted as new): `test_build_site_shows_unconfirmed_runway_pill_instead_of_a_pill_with_no_end`
(now asserts runways *do* appear, since that is now correct) and
`test_build_site_does_not_expose_runway_end_or_runway_end_id_anywhere`
(unchanged assertions, still passes — confirms no `RunwayEnd`/id leak).

`tests/test_apply_canonical_runway_inventory_us_clean_batch.py` and
`..._newly_clean_batch.py`: 1 test each renamed from
`..._still_suppresses_banor_and_leaks_nothing` to
`..._publishes_banor_and_leaks_nothing`, now asserting the newly-applied
airport's canonical runway appears under "Banor" post-apply, while
`RunwayEnd`/`runway_end_id` internals still never leak.

## 10. Static export verification

Regenerated `site/` locally via `python -m scripts.export_static_site
--output site` — **for validation only, not deployed**. Confirmed:

- BOS's and ORH's generated detail pages both contain a populated "Banor"
  card with exactly the expected designations.
- `data.json`'s `airports[].runways` entries contain only `{"designation":
  "..."}` — no `id`, `length_m`, `width_m`, or `surface` — for every
  airport checked (BOS/ORH/MDW/CGF).
- "EMAS idag" sections unchanged for both airports (empty state, same
  text as before).
- No `runway_end_id`/`RunwayEnd` string anywhere in any generated `.html`
  file or `data.json`, across the full 87-page site.
- No Jinja rendering errors; existing Signals/Projects/Timeline content
  still present and unaffected.
- No new CSS was needed — the new section reuses the exact `.pill.status`
  / `.card-body` markup pattern already used by "EMAS idag".

## 11. Public-boundary safety

`_runway_view()` returns exactly one field, `designation` (a plain
string). No `Runway.id`, no `RunwayEnd` rows, no
`PhysicalInstallationIdentity`, no `SourceAssertion`/reconciliation
internals, and no confidence/provenance mechanics are reachable from the
new view model or template. Verified directly (§10) and by test
(`assert all(set(r.keys()) == {"designation"} for r in published_runways)`).

## 12. DB unchanged proof

| | Before this task | After this task |
|---|---|---|
| DB size | 667648 bytes | 667648 bytes |
| DB mtime | unchanged | unchanged |
| `Runway` count | 180 | 180 |
| `RunwayEnd` count | 360 | 360 |
| U.S. planner | 76 `ALREADY_COMPLETE`, 0 unresolved/ambiguous/conflict | unchanged |

No database write of any kind was performed in this task — every change
is in application code (`app/static_export/`) and tests.

## 13. Next recommended EMAS/reconciliation slice

Unchanged from the prior gap analysis and web research pilot: promote the
115 already-ingested, `direct_strong`-quality NASR current-EMAS-presence
`SourceAssertion` rows (`raw_runway_end_value` → `runway_end`) — a pure
normalization step reusing the already-proven MDW pilot mechanism,
generalized instead of airport-specific — followed by a narrowly-scoped,
evidence-backed reconciliation of BOS's now-resolved 22R/33L identity
(per `docs/product/bos-orh-authoritative-web-research-pilot.md`) using the
same investigate → dry-run → apply pattern already used for Allegheny and
Morristown. **Neither was performed in this task**, per explicit
instruction.
