# Evidence / Installation Identity — Slice 2 report

## Status

Implementation, isolated tests, approved real-database backfill, and
post-write verification are complete.

## Source archaeology and safely recoverable families

| Source family | Dry-run candidates | Assertion type | Evidence quality | Decision |
|---|---:|---|---|---|
| Checked-in FAA Tableau CSV | 70 | `airport_inventory` | `partial` | Safe: checked-in artifact, deterministic CSV line locator and raw-row hash. It remains aggregate inventory evidence. |
| USAspending grants | 25 | `project_construction` | `direct_strong` | Safe: official source text and namespaced upstream external ID. |
| IIJA grants | 3 | `project_construction` | `direct_strong` | Safe: official source text and namespaced upstream external ID. |
| FAA construction report | 2 | `project_construction` | `direct_strong` | Safe: official source text and namespaced upstream external ID. |

FAA fact-sheet rows, NASR runway-end enrichment, and curated/manual/news,
Gadelius, international, shareholder-letter and graduation-derived rows are
not mechanically backfilled in this slice. Their legacy source link/notes can
be valuable evidence, but the repository does not recover a consistent
upstream record/mark/table-line locator and immutable raw fragment for every
claim. Converting their interpreted `Installation` rows would violate the
requirement not to treat an Installation as original source evidence.

## Dry-run result

The no-write command was:

```powershell
$env:DEBUG='false'; .\.venv\Scripts\python.exe -m scripts.backfill_legacy_source_assertions --csv emas_airports_usa.csv
```

It found 100 candidate source records, all absent from `source_assertions`.
After explicit approval, the same command with `--apply` created all 100.
The pre-write backup is
`data/backups/runway_safe-pre-evidence-identity-slice2-20260816-061000.db`
(454,656 bytes).

- 70 `airport_inventory` / partial FAA CSV assertions;
- 30 `project_construction` / direct-strong official grant/report assertions;
- no skipped record in this checked-in-artifact run;
- no automatic Installation, Signal, Incident, Airport, or Runway mutation.

The 70 FAA CSV rows differ from the 69 FAA-backed legacy Installation rows:
the CSV is preserved at its own source-record granularity and is not collapsed
by the legacy `(airport, type)` importer behavior.

## Legacy Installation evidence coverage analysis

This is classification only; no Installation was changed.

| Classification | Rows | Meaning |
|---|---:|---|
| Strong recoverable upstream evidence | 0 | No physical-system legacy row has been mechanically promoted as strong evidence. |
| Partial recoverable evidence | 69 | FAA Tableau-linked rows have a checked-in aggregate CSV record, but no physical-system location/year/vendor identity. |
| Ambiguous evidence | 80 | 61 fact-sheet and 19 curated/manual/news-linked rows require record-level archaeology/review before assertion backfill. |
| No safely recoverable upstream evidence | 0 | Every legacy Installation retains a source link, but source links alone are not sufficient for a SourceAssertion backfill. |

Ambiguous examples include composite fact-sheet count/year/replacement wording,
manual notes that blend source quotation with RWI interpretation, and
NASR-derived location enrichment whose relationship to a legacy Installation
was not a source-record identity decision. CGF’s two explicit end claims
remain distinct legacy evidence; this slice neither links nor reconciles them.

## Idempotency

The backfill uses Slice 1’s database constraints only:

- stable external source record: `(source_id, source_record_identifier)`;
- FAA CSV fallback: `(source_id, artifact_identity, source_locator,
  raw_fragment_hash)`.

No airport/product/year, title, URL, coordinate, or similarity-based key is
used. Two FAA source records for the same airport/type/year remain separate
assertions when their locators/hashes differ.

## Files created/modified

- Created `scripts/backfill_legacy_source_assertions.py`.
- Created `tests/test_backfill_legacy_source_assertions.py`.
- Created this report.

No import behavior, static export, public template, public JavaScript, or
existing record was modified in Slice 2.

## Validation

- Focused tests: **26 passed**.
- Full suite: **343 passed**.
- `git diff --check`: passed.

Focused coverage includes deterministic dry run, repeat-run idempotency,
same-airport/type distinct source records, nullable unknown runway/end fields,
non-invention, and no Installation/Signal mutation. Slice 1’s export privacy
tests remain in the focused run; SourceAssertion is still absent from the
static export by construction.

## Blockers and pending approval

There is no blocker for the completed safe 100-record subset. Fact-sheet and
manual/research source-record recovery remains intentionally deferred rather
than guessed.

## Explicit non-reconciliation confirmation

No Installation identity reconciliation occurred. The existing 149
Installation rows were not merged, split, replaced, deleted, migrated, or
reinterpreted. No SourceAssertion is linked to an Installation.

## Post-write verification

- `source_assertions`: 100 rows exactly.
- Assertion types: `airport_inventory` 70; `project_construction` 30.
- Evidence quality: `partial` 70; `direct_strong` 30.
- Source family: FAA Tableau 70; USAspending 25; IIJA 3; FAA construction
  report 2.
- `PRAGMA foreign_key_check`: zero violations.
- Backup-vs-live field comparison: Airports 86, Runways 59, Installations
  149, Incidents 26, Signals 68, and Sources 68 are all row-for-row
  identical. In particular, all 149 Installation rows and every Signal and
  Incident row are unchanged.
- Public `site/data.json` contains no SourceAssertion/internal-evidence field.
- A repeat dry run reports `already_present: 100` and no `would_create` value.
- Post-write focused tests: **26 passed**; full suite: **343 passed**;
  `git diff --check` passed.
