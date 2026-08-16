# Evidence Identity Slice 4 report

## Fact-sheet artifact findings

The preserved `docs/sources/2011-03-07_faa-emas-fact-sheet.pdf` contains
extractable EMAS Installations table text. A narrow explicit manifest preserves
six priority rows with deterministic PDF page/table/airport locators and hashes.
The 2016 Skybrary mirror did not yield equivalent usable text in this pass.

## Recoverable assertions and dry run

Six `historical`/`partial` aggregate assertions would be created: JFK page 2
`2 / 1996(1999)/2007`; BOS page 3 `2 / 2005/2006`; MDW `4 / 2006/2007`;
ORD `2 / 2008`; LGA `2 / 2005`; FLL `2 / 2004`. Raw parenthesized wording is
preserved without interpreting it. Counts stay aggregate and create no systems.

Dry run: candidates 6, would create 6, already present 0, skipped 0.

## NASR findings

The existing parser correctly filters only `ARREST_DEVICE_CODE=EMAS` and has
raw `ARPT_ID/RWY_ID/RWY_END_ID` fields, but no NASR APT_ARS artifact/cycle is
checked in. Therefore no NASR assertion is recoverable yet (0 candidates;
skipped reason: no preserved artifact). Future controlled capture must preserve
cycle, archive hash and row locator before assertion backfill; it must never
modify runway or installation rows.

## Files and validation

- Added `app/evidence/faa_fact_sheet_manifest.py`.
- Added `scripts/backfill_faa_fact_sheet_evidence.py`.
- Added `tests/test_faa_fact_sheet_manifest.py`.
- Added this report.

Focused tests: 21 passed. Full suite: 347 passed. `git diff --check` passed.
No real database write occurred; public export remains unchanged.

## Blockers and next step

Apply is deliberately pending approval. Before any write report the resolved
DB path, a fresh timestamped backup, exact `python -m
scripts.backfill_faa_fact_sheet_evidence --apply` command, counts 6/0/0,
written table `source_assertions`, and all other domain tables unchanged.
Next, obtain a controlled NASR artifact capture and add an EMAS-only manifest
from its raw rows.
