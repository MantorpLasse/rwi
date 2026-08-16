# Evidence Identity Slice 6D: CGF Human-Reviewed Physical Installation Pilot

## Result

The CGF pilot is implemented as an explicit dry-run-first command. It has not
been applied to the real development database. It does not touch MDW, legacy
Installations, SourceAssertions, Signals, Incidents, Sources, UI, or export.

## Read-only precheck

- CGF Airport exists: ID **57**, FAA code `CGF`.
- Existing legacy runway is ID **58**, designation `6/24`.
- Assertions 101, 102, 198, and 199 exist, are CGF `runway_end` assertions,
  and preserve the Slice 6A discrete-end evidence.
- Assertion 54 exists as reviewed `airport_inventory` aggregate evidence.
- Before the dry run, `physical_installation_identities` and
  `installation_assertion_links` each contained zero rows.

## Evidence and canonical placement

| Candidate | Assertions | Retained source wording | Placement |
|---|---|---|---|
| CGF end 06 | 101, 198 | Cuyahoga `6/24`, end `06`; NASR `06/24`, end `06` | Airport 57, runway null, end `06` |
| CGF end 24 | 102, 199 | Cuyahoga `6/24`, end `24`; NASR `06/24`, end `24` | Airport 57, runway null, end `24` |

The existing runway is not used as canonical placement. Although the Cuyahoga
wording exactly matches legacy `6/24`, NASR uses `06/24`; selecting a shared
runway FK would make an alias/formatting judgment. The pilot safely preserves
only the human-reviewed ends. No vendor, product, year, lifecycle, replacement,
or legacy mapping is created.

## Proposed decisions

All four links are initial `SAME_PHYSICAL_INSTALLATION` decisions with
`actor = human:rwi-owner`, a non-empty evidence-based reason, a review
timestamp, and no superseded link:

- 101 -> reviewed CGF end 06 identity
- 198 -> reviewed CGF end 06 identity
- 102 -> reviewed CGF end 24 identity
- 199 -> reviewed CGF end 24 identity

Assertion 54 remains unlinked.

## Idempotency

The command uses a narrowly scoped, explicit two-entry CGF pilot manifest
(`cgf-reviewed-end-06-v1` and `cgf-reviewed-end-24-v1`). It finds an existing
pilot result only when both listed assertions have SAME links to the same
identity with the expected airport/null-runway/end placement. This is not a
global airport/runway/end identity rule. An incompatible partial result is a
blocker rather than an automatic merge.

## Dry run

Dry run output is clean: **2** identities would be created and **4** links
would be created, for assertions 101, 198, 102, and 199. It reported no
already-present records and no blockers. No write occurred.

## Files changed

- `scripts/apply_cgf_physical_installation_pilot.py`
- `tests/test_cgf_physical_installation_pilot.py`
- this report

## Tests and checks

- Focused tests: **7 passed**.
- Full suite: **356 passed**, 1345 warnings.
- Python compilation: passed.
- `git diff --check`: passed.

Focused coverage verifies dry run behavior, separate end identities, target
assignment, unlinked aggregate assertion 54, actor/reason, idempotency, and no
legacy Installation mutation. Existing static-export tests remain green; the
pilot adds no public output path.

## Real database apply plan -- not executed

Resolved database:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\runway_safe.db`

Fresh backup to create after approval:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\backups\\runway_safe-pre-evidence-identity-slice6d-cgf-pilot-20260816-073630.db`

Exact apply command:

```powershell
$env:DEBUG='false'; .\\.venv\\Scripts\\python.exe -m scripts.apply_cgf_physical_installation_pilot --apply
```

Expected writes: exactly **2** `physical_installation_identities` rows and
**4** `installation_assertion_links` rows. The only writable tables are those
two reconciliation tables. Airports, Runways, Installations, Incidents,
Signals, Sources, SourceAssertions, and all existing rows are guaranteed
unchanged.

## Blockers

None. The controlled pilot is ready for separate apply approval. Do not begin
MDW work or Slice 6D follow-on activity without a new instruction.


## Final approved apply verification

The backup was created before apply at
`C:\Runwaysafe\runway-safe-intelligence\data\backups\runway_safe-pre-evidence-identity-slice6d-cgf-pilot-20260816-073630.db`
with byte size **626,688**.

The approved apply created exactly **2** physical identities (IDs 1 and 2) and
**4** reconciliation links. Identity 1 is Airport 57, runway NULL, end `06`;
identity 2 is Airport 57, runway NULL, end `24`.

Assertions 101/198 target identity 1 only; assertions 102/199 target identity 2
only. All four links are `SAME_PHYSICAL_INSTALLATION`, use
`human:rwi-owner`, have a non-empty evidence reason and review timestamp, and
have no superseded link. Assertion 54 has zero links. No vendor, product, year,
lifecycle, replacement, or legacy mapping data was created.

A read-only row-by-row comparison with the backup confirmed no changes to
Airports (86), Runways (59), legacy Installations (149), Incidents (26),
Signals (68), Sources (69), or SourceAssertions (221). `PRAGMA
foreign_key_check` returned zero violations. Repeat dry-run reports both
entries already present and zero would-create. Focused tests passed (7), the
full suite passed (356), Python compilation passed, and `git diff --check`
passed.
