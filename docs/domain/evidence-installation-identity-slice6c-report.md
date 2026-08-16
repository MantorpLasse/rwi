# Evidence Identity Slice 6C Report

## Result

Slice 6C adds the schema/model/validation foundation only. It did not migrate
the real development database, create identities or reconciliation decisions,
touch legacy Installation rows, change SourceAssertions, or alter public export.

## Files changed

- `app/models/physical_installation_identity.py`: new physical identity and
  append-only reconciliation-decision models.
- `app/services/physical_installation_reconciliation.py`: explicit validation
  and creation functions; no matching/reconciliation logic.
- `scripts/migrate_evidence_identity_slice6c.py`: additive reversible SQLite
  migration script, following this repository's established migration-script
  convention (there is no Alembic environment in this repository).
- `app/models/__init__.py`, `app/models/airport.py`, and
  `app/models/source_assertion.py`: model registration and relationships.
- `tests/test_physical_installation_reconciliation.py` and
  `tests/test_model_contract.py`: focused and schema-contract coverage.

## Final schema

`physical_installation_identities` contains only `id`, required `airport_id`,
nullable reviewed `runway_id`, nullable reviewed `runway_end`, and `created_at`.
There is intentionally no airport/runway/end uniqueness constraint. Vendor,
manufacturer, product, installation year, lifecycle, legacy mapping, and
replacement fields are absent.

`installation_assertion_links` contains assertion ID, nullable physical identity
ID, governed outcome, reason, actor, reviewed timestamp, and superseded-link ID.
It has foreign keys to SourceAssertion, PhysicalInstallationIdentity, and the
prior decision. Database checks enforce governed outcomes and require a physical
target for all resolved outcomes.

## Validation and audit rules

The service validates existing Airport, Runway, SourceAssertion, and target
identity references; supplied runway ownership by the identity airport; governed
outcomes; non-empty reason and actor; resolved targets; and compatible
same-assertion supersession. Decisions are append-only: updates and deletes of a
persisted decision fail. A changed conclusion is a new link that references the
prior link. SourceAssertion is not modified.

## UNRESOLVED and actor convention

`UNRESOLVED` may have a null physical target. Its exact meaning is: the
assertion was reviewed but no physical identity can currently be selected. This
is smaller and more truthful than placeholder identities. `SAME` and
`DIFFERENT` require a real target.

`actor` is a required short audit label. Current human convention is
`human:<researcher-label>`; a future proposal must use a distinct `ai:<system>`
label. There is no users, roles, authentication, or workflow implementation.
An AI label is never a human review.

## Migration behavior

The migration creates only:

- `physical_installation_identities`
- `installation_assertion_links`

It is additive, creates zero rows, and has an isolated-database tested
downgrade. It does not read or modify Airport, Runway, Installation, Incident,
Signal, Source, or SourceAssertion rows.

## Tests and checks

- Focused reconciliation/model tests: **10 passed**.
- Full suite: **354 passed**, **1341 existing/deprecation warnings**.
- Python compilation: passed.
- `git diff --check`: passed.

Focused coverage includes multiple identities at one end, nullable placement,
required airport, runway-airport consistency, all outcomes, null-target
UNRESOLVED, supersession, immutable prior decisions, SourceAssertion
immutability, unlinked aggregate evidence, CGF-shaped separate identities,
MDW-safe non-automation, and isolated upgrade/downgrade.

## Development-database migration and verification

Resolved database:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\runway_safe.db`

Before the approved migration, neither future table existed and their implied
counts were zero. A fresh backup was created at:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\backups\\runway_safe-pre-evidence-identity-slice6c-20260816-073034.db`

The backup byte size is **593,920**. The approved migration then completed with
these only new tables: `physical_installation_identities` and
`installation_assertion_links`. Both exist and contain **0** rows.

The migration command was:

```powershell
$env:DEBUG='false'; .\\.venv\\Scripts\\python.exe -m scripts.migrate_evidence_identity_slice6c --database data/runway_safe.db --allow-database-write
```

Row-for-row comparison against the backup confirmed no changes to Airports
(86), Runways (59), Installations (149), Incidents (26), Signals (68), Sources
(69), or SourceAssertions (221). `PRAGMA foreign_key_check` returned zero
violations. A fresh application connection reported `PRAGMA foreign_keys = 1`.

## Proposed migration operation -- not executed

Fresh backup to create before approval:

`C:\\Runwaysafe\\runway-safe-intelligence\\data\\backups\\runway_safe-pre-evidence-identity-slice6c-<UTC-timestamp>.db`

Exact migration command:

```powershell
$env:DEBUG='false'; .\\.venv\\Scripts\\python.exe -m scripts.migrate_evidence_identity_slice6c --database data/runway_safe.db --allow-database-write
```

Tables written: `physical_installation_identities`,
`installation_assertion_links`.

Guaranteed unchanged: Airports, Runways, Installations, Incidents, Signals,
Sources, SourceAssertions, and all existing data rows.

## Blockers and next slice

There is no implementation blocker. Real migration requires explicit approval
and the required fresh backup. After that, the recommended next slice is a
separately approved, human-reviewed CGF pilot that creates no legacy mapping
and no automatic reconciliation. MDW remains a stress case, not pilot data.
