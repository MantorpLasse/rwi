# Evidence Identity Slice 3B report

## Files changed

- `app/evidence/discrete_manifest.py`
- `scripts/backfill_discrete_manifest.py`
- `tests/test_discrete_manifest.py`
- this report

## Explicit manifest candidates

Three candidates are accepted: CGF end 06 and end 24 from the Cuyahoga County
completion-release claim (2018; raw runway `6/24`; raw ends `06`/`24`), and
MDW 22L from the retained PRWeb claim (first greenEMAS bed, November 2014).
Each preserves source URL, publisher, exact retained fragment, source-specific
script locator, raw fragment hash, raw wording, type and review/evidence state.
CGF candidates share a fragment but have distinct locators and ends, so cannot
collapse. No canonical Runway FK or vendor is inferred.

## Skipped candidates

Gadelius ZRH/RUN/DZA/SCN/HND/NHT/CGH entries are intentionally skipped. The
available list is an RWI-authored retained summary, not a demonstrable upstream
per-entry fragment/locator. ORD is likewise ambiguous (“shortly after”). FAA
Tableau and fact-sheet aggregates are rejected by manifest vocabulary.

## Validation and dry run

Validation rejects missing source identity/locator/fragment and unsupported
aggregate assertion types. The dry run against the development database found:

- candidates: 3
- would create: 3
- already present: 0
- skipped: 0

It writes only `source_assertions` when explicitly invoked with `--apply`; it
cannot mutate Airport, Runway, Installation, Incident, Signal or Source.
No apply was run.

Focused tests: 21 passed. Full suite: 345 passed. `git diff --check` passed.
Static-export privacy tests remain passing; the manifest/assertions are not
public output.

## Database safety

No real development-database write occurred. A future apply requires explicit
approval after reporting the resolved database path, a new timestamped backup,
the exact apply command, count 3 / already-present 0 / skipped 0, written table
`source_assertions`, and all guaranteed-unchanged tables.

## Recommended Slice 4

After approval to apply this narrow manifest, capture primary fact-sheet and
NASR source artifacts/locators before expanding candidates. Do not begin
assertion-to-Installation reconciliation until those assertions are reviewed.
