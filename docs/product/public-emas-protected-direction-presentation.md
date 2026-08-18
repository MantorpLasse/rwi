# Public EMAS Presentation — Protected Direction + Physical Provenance

Presentation-layer slice on top of the completed nationwide NASR EMAS
runway-end promotion (`main` @ `e62ca7b07f56aec209c3fb046ec12857693b14fb`).
**No database write, no model/schema change, no ingestion change, no
reconciliation write, no deployment** — verified throughout (§15).

## 1. Problem

The public "EMAS idag" section rendered the governed `runway_end` value
directly — the canonical **physical** RunwayEnd location an authoritative
source (NASR, or a reviewed identity) reports. That is factually correct,
but the BOS/ORH research
(`docs/domain/emas-runway-end-semantics-and-nasr-promotion-analysis.md`)
found that operators typically describe a bed by the *reciprocal*,
protected-operational direction instead (Massport's own language: a bed
NASR reports at `04L` is "Runway 22R's EMAS"). A visitor asking "which
runway does this protect?" was being answered with the wrong-feeling
number, even though the underlying data was never wrong.

## 2. Physical-vs-protected semantic contract (unchanged, inherited)

`SourceAssertion.runway_end` / `PhysicalInstallationIdentity.runway_end_id`
continue to mean exactly the canonical **physical** RunwayEnd location —
this task did not touch that contract, any governed row, or any writer.
The protected operational direction is a **presentation-layer-only**
concept: the reciprocal `RunwayEnd` on the same canonical `Runway`,
derived fresh on every export, never stored.

## 3. Why topology derivation is safe

Every governed `Runway` has exactly two `RunwayEnd` children — verified
nationwide with zero exceptions (`RunwayEnd` count = `Runway` count × 2
exactly). "The other `RunwayEnd` on the same `Runway`" is therefore a
pure, 100%-deterministic relationship traversal
(`runway_end.runway.runway_ends`, minus itself) — never designation
parsing, heading arithmetic, or string manipulation. Verified directly by
test with an asymmetric-suffix pair (`6L/24`) and an L/R-suffixed pair
(`15L/33R`), where naive heading math would be more fragile than a plain
topology lookup.

## 4. Fail-closed rules

`_find_canonical_runway_end()` returns `None` (never guesses) when the
physical designation matches zero or more than one canonical `RunwayEnd`
at the airport. `_protected_direction()` returns `None` when the parent
`Runway` does not have exactly two governed `RunwayEnd` rows. In either
case, the item **still renders** — using the physical value as the
primary label instead of a derived one (e.g. `"Bana 13"` instead of
`"Bana {reciprocal}"`) — so raw authoritative evidence is never hidden
just because a derived label couldn't be produced. Covered by 3 dedicated
tests (zero-match, multi-match, malformed-topology).

## 5. NASR pathway

Unchanged data source (`SourceAssertion.runway_end`, populated by the
already-committed promotion writer). Presentation now derives and shows
the protected direction as the primary label, the physical value and NASR
cycle as provenance, and the existing "this is presence evidence only —
not install year/manufacturer/replacement/cost" caveat, verbatim.

## 6. Reviewed-identity pathway

Unchanged data source and governance
(`PhysicalInstallationIdentity`/`InstallationAssertionLink`, human-
reviewed, currently MDW + CGF only). Renders through the exact same
derivation helper and item shape as the NASR pathway — same primary-label
logic, same fail-closed rules — with `evidence_basis="reviewed"` and the
label "Granskad identitet" instead of "FAA NASR aktuell förekomst".
Nothing about the underlying reconciliation governance was changed;
this only changes how an already-reviewed row is *labeled* on the page.

## 7. Deduplication behavior

Both pathways are merged into one list, keyed by the resolved canonical
`RunwayEnd.id` (or a raw fallback key when unresolvable, so an
unresolvable item still renders standalone rather than being silently
dropped). **Reviewed identity is inserted first and always wins** a
collision — an unreviewed NASR assertion describing the same physical bed
as an already-reviewed identity is dropped from the public list entirely,
never shown as a second, lower-confidence duplicate of the same bed.
Verified by test (`test_reviewed_and_nasr_same_bed_deduplicates_to_one_item`,
`test_reviewed_identity_wins_presentation_precedence_over_nasr`). In the
real, current database this collision case does not currently occur in
practice — the promotion writer's own `ALREADY_LINKED` exclusion already
prevents a NASR assertion at an already-reviewed physical end from ever
being promoted in the first place — so the mechanism is proven correct by
test but not currently exercised by production data (§14: 6 reviewed + 97
NASR = 103 total, zero collisions).

## 8. Public view-model contract

Replaces the former `reviewed_identities`/`nasr_presence` fields (both
named the ambiguous `runway_end`, both physical-only, never deduplicated
against each other) with one field, `current_emas`, a list of items shaped:

```json
{
  "primary_label": "Bana 22R",
  "physical_runway_end": "04L",
  "protected_runway_direction": "22R",
  "evidence_basis": "nasr",
  "evidence_basis_label": "FAA NASR aktuell förekomst",
  "provenance_text": "Fysisk placering enligt FAA NASR: bana 04L. NASR-cykel 2026-08-06. Uppgiften beskriver förekomst vid banände, inte projektstatus eller fysisk historik."
}
```

No database ID, `runway_end_id`, assertion ID, review-state, or
classifier category (`AUTO_RESOLVABLE`, `direct_strong`, etc.) is present
— verified by test and by direct inspection of the generated `data.json`
and HTML.

**Decision on the old fields**: retired, not preserved alongside the new
one. Both old fields were exactly the "ambiguous public field simply
named `runway_end`" this task's own brief warns against, no known external
consumer of `data.json` exists, and keeping three overlapping structures
(two legacy physical-only + one new derived) would itself be the kind of
confusing duplication this slice exists to remove. Confirmed no test or
code anywhere still references the old field names or the retired helper
functions (`_public_identity_view`, `_nasr_presence_view`).

## 9. HTML presentation

Reuses the site's existing visual language exactly — no new CSS, no new
components. Each current-EMAS item renders as one existing `.pill.status`
(the protected-direction primary label) plus two lines of existing
`.timeline-meta` (muted secondary/tertiary text): the evidence-basis
label, then the provenance sentence. Information hierarchy matches the
brief: primary (protected direction, as the pill) → secondary (physical
location, first line of provenance text) → tertiary (source/cycle
caveat, appended to the same line for NASR items only). The empty-state
and "current status unverified" branches are unchanged.

## 10. `data.json` contract

Documented above (§8) and in the model itself
(`app/static_export/build.py::_current_emas_item()`/`_current_emas_views()`).
No field silently changed meaning — the old physical-only fields were
removed outright rather than reinterpreted, and the new field's semantics
are named explicitly (`physical_runway_end` vs. `protected_runway_direction`)
rather than reusing the ambiguous `runway_end` name for either value.

## 11. Nationwide validation counts

Generated locally from the real (now-promoted) database, not deployed:

| | Count |
|---|---|
| Total public current-EMAS items | 103 |
| Airports with current-EMAS publication | 58 |
| Successfully derived protected directions | 103 |
| Derivation failures | 0 |
| Deduplicated evidence collisions | 0 (mechanism proven by test; not exercised by current production data — see §7) |
| Reviewed-identity items | 6 |
| NASR-presence-only items | 97 |
| Airports with duplicate primary labels | 0 |
| Malformed/empty labels | 0 |
| Cross-airport/cross-runway leakage | 0 (verified against the true canonical `RunwayEnd` designations directly, not the display-only `Runway.designation` string — see §16) |

103 = 6 + 97 exactly, confirming the zero-collision result independently.

## 12. Representative airport examples

- **ADS** (Addison, NASR-only, single bed): physical `16` → primary
  `"Bana 34"`.
- **SFO** (multi-bed, 4 items): `1L↔19R`, `1R↔19L` cross-derive correctly
  in both directions, no duplicates.
- **FLL** (multi-bed, 4 items): `10L↔28R`, `10R↔28L`, all four items
  correct and distinct.
- **MDW** (reviewed identity, 4 items): all 4 already-approved physical
  values (`04R`, `22L`, `13L`, `31R`) correctly cross-derive to their
  reciprocals, all labeled "Granskad identitet".
- **CGF** (reviewed identity, 2 items): `06↔24` correctly cross-derived,
  both labeled "Granskad identitet".

## 13. BOS/ORH exclusion confirmation

Both confirmed to render `current_emas: []` — the empty-state message is
shown, exactly as before this task, and their canonical runway inventory
("Banor") renders normally and unaffected (BOS: all 6 runways; ORH: both
runways). Nothing from the recent BOS/ORH web research was used to
populate or bypass their still-`REVIEW_REQUIRED` status.

## 14. Tests

`tests/test_static_export.py` — 18 new tests, all against isolated
in-memory databases: physical→protected derivation (basic, topology-not-
arithmetic, asymmetric-suffix), zero-match/multi-match/malformed-topology
fail-closed, physical value preserved separately from the derived label,
primary label uses protected direction, NASR-only rendering with cycle/
caveat, reviewed-identity rendering, dedup + precedence (2 tests),
REVIEW_REQUIRED-shaped exclusion (BOS-shaped and ORH-shaped), no internal
ID leakage, explicit `data.json` field semantics, canonical runway
inventory unaffected, and zero database mutation during build. All 18
pass; the 18 pre-existing static-export tests continue to pass unchanged.

## 15. DB unchanged proof

| | Before | After |
|---|---|---|
| Path | `data/runway_safe.db` | (same) |
| Size | 667648 bytes | 667648 bytes |
| mtime | `1787004353.2183805` | `1787004353.2183805` |
| SHA-256 | `23338863aff466e8ea1841c215177a3d2f6098495e713b7f15ece9595d944559` | (same) |
| `Runway` | 180 | 180 |
| `RunwayEnd` | 360 | 360 |
| `PhysicalInstallationIdentity` | 6 | 6 |
| `InstallationAssertionLink` | 8 | 8 |
| The 9 REVIEW_REQUIRED assertions | all `NULL` | all `NULL` |

Confirmed identical before implementation, after tests, and after the
final local static-generation run.

## 16. Remaining presentation/product limitations

- **Discovered, pre-existing, out-of-scope data-quality finding**: 13 of
  180 canonical `Runway.designation` strings (e.g. LIT's `"04L/22R"`,
  BUR's `"08/26"`) retain a leading zero that their own child
  `RunwayEnd.designation` rows do not (e.g. `"4L"`/`"22R"`) — a legacy
  formatting inconsistency predating this task, unrelated to and
  unaffected by the EMAS presentation work (this derivation never reads
  `Runway.designation` at all, only `RunwayEnd.designation`, so it is
  correct regardless). Visible effect: the same physical runway pair can
  show `"04L/22R"` in the "Banor" card and `"4L"`/`"22R"` in the "EMAS
  idag" card of the same page for these 13 airports. Not fixed here — a
  future, separate, narrowly-scoped data-normalization task should
  re-derive these 13 `Runway.designation` strings via
  `normalize_pair()`.
- The deduplication/precedence mechanism (§7) is proven correct by test
  but has zero live examples in the current dataset to visually confirm
  on the deployed site — worth re-checking once a future reconciliation
  batch creates a case where both pathways describe the same bed.
- The 9 REVIEW_REQUIRED assertions (BOS, ORH, BGM, LEX, ELM) remain
  entirely unpublished by design; a future, separate, evidence-backed
  reconciliation slice is still the correct next step for those, not a
  presentation change.

## 17. Deployment recommendation

**Ready for review, not yet deployed** (per instruction). The change is
presentation-only, backward-incompatible only in the sense of retiring
two internal field names with no known external consumer, fully covered
by new and pre-existing tests, and verified against the real (already-
promoted) database with zero derivation failures and zero unexpected
leakage across all 103 public items. Recommend: human visual review of
the local `site/` output (this report's examples are a good starting
set — ADS, SFO, FLL, MDW, CGF, BOS, ORH), then a deploy decision as its
own separate, explicit step.
