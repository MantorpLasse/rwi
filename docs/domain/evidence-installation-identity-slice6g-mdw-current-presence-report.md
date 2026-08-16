# Evidence Identity Slice 6G: MDW Current-Presence Pilot

## Result

A dedicated MDW pilot is implemented with dry-run as the default. It has not
written to the real development database. It creates only reviewed current-
presence identities from the preserved FAA NASR 2026-08-06 evidence; it makes
no historical continuity, lifecycle, vendor, product, year, replacement,
designation-alias, legacy-mapping, or public-export claim.

## Evidence resolution

MDW resolves to Airport ID **12**. The governed NASR Source is resolved by
its deterministic external identity
`faa_nasr:airport_csv:2026-08-06:06_Aug_2026_APT_CSV.zip`. The command verifies
each raw APT_ARS representation: `ARPT_ID=MDW`, `ARREST_DEVICE_CODE=EMAS`,
`EFF_DATE=2026/08/06`, and exactly one matching runway end. It fails closed
for missing, duplicate, malformed, or unexpected end evidence.

| Assertion | Raw runway / end | Locator | Proposed identity |
|---|---|---|---|
| 145 | `04R/22L` / `04R` | `APT_ARS.csv:line=168` | Airport 12, runway NULL, end `04R` |
| 146 | `04R/22L` / `22L` | `APT_ARS.csv:line=169` | Airport 12, runway NULL, end `22L` |
| 147 | `13L/31R` / `13L` | `APT_ARS.csv:line=170` | Airport 12, runway NULL, end `13L` |
| 148 | `13L/31R` / `31R` | `APT_ARS.csv:line=171` | Airport 12, runway NULL, end `31R` |

Runway IDs are deliberately null. Existing runway history makes a canonical
FK more speculative than useful; the reviewed runway-end identity is safer.

## Proposed links and guardrails

The dry run proposes one initial `SAME_PHYSICAL_INSTALLATION` link per NASR
assertion, all with `actor = human:rwi-owner`, non-empty evidence reason,
review timestamp on apply, and no supersession. Each reason says the claim is
current presence only and contains no historical-continuity assertion.

The following remain intentionally unlinked: historical 22L assertion 103;
FAA Tableau aggregate assertion 26; FAA historical aggregate assertion 106;
the Chicago CIP repair Signal/source evidence; all legacy Installation rows;
and runway-designation history. In particular, 22L current NASR assertion 146
is not linked to historical 2014 assertion 103.

## Idempotency

The script identifies the governed NASR source and validates the exact
four-end set. For each assertion it treats an existing SAME link to an identity
with the expected Airport 12 / null runway / reviewed end placement as already
present. An incompatible or multiply-linked assertion blocks execution rather
than merging records. This is scoped to the MDW pilot; it is not a global
airport or runway-end uniqueness rule.

## Dry run

Real-development-database dry run reported: **4** identities would create;
**4** links would create; **0** already present; **0** skipped/unresolved; and
no blockers. Existing protected-table state was read-only throughout.

## Files changed

- `scripts/apply_mdw_current_presence_pilot.py`
- `tests/test_mdw_current_presence_pilot.py`
- this report

## Tests and safety checks

- Focused tests: **8 passed**.
- Full suite: **359 passed**, 1351 warnings.
- Python compilation: passed.
- `git diff --check`: passed.

Focused tests cover four candidates, four distinct same-airport identities,
mapping, historical/aggregate non-linkage, no legacy mutation, idempotency,
and malformed NASR evidence failing closed. Existing static-export tests remain
green, and this slice has no public export path.

## Apply plan -- not executed

1. Resolved database:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\runway_safe.db`

2. Fresh backup to create after approval:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\backups\\runway_safe-pre-evidence-identity-slice6g-mdw-current-presence-20260816-075710.db`

3. Exact apply command:

```powershell
$env:DEBUG='false'; .\\.venv\\Scripts\\python.exe -m scripts.apply_mdw_current_presence_pilot --apply
```

Only `physical_installation_identities` and `installation_assertion_links`
would be written. Airports, Runways, legacy Installations, Incidents, Signals,
Sources, SourceAssertions, existing CGF identities/links, and public output are
guaranteed unchanged. No approval to apply has been given in this slice.

## Final approved apply verification

Backup before apply:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\backups\\runway_safe-pre-evidence-identity-slice6g-mdw-current-presence-20260816-075710.db`

Byte size: **626,688**.

The apply created four MDW identities, IDs 3–6, at Airport 12: `04R`, `22L`,
`13L`, and `31R`, all with runway NULL. It created four SAME links:
145 -> 3, 146 -> 4, 147 -> 5, and 148 -> 6. All use `human:rwi-owner`,
current-presence-only reasons, review timestamps, and no supersession.

Assertions 103, 26, and 106 have zero links. There are no other MDW assertion
links, so CIP/repair/designation evidence was not linked. No lifecycle,
replacement, vendor, product, year, or historical-continuity data was created.
The existing CGF identities and links are unchanged.

Row-by-row comparison with the backup confirmed no changes to Airports (86),
Runways (59), legacy Installations (149), Incidents (26), Signals (68),
Sources (69), or SourceAssertions (221). `PRAGMA foreign_key_check` returned
zero violations. Repeat dry run reports all four entries already present and
zero would-create. Focused tests passed (8), full suite passed (359), Python
compilation passed, and `git diff --check` passed.
