# Evidence Identity Implementation Slice 1 report

## Result

Slice 1 is complete. It repairs the confirmed SQLite source-FK defect, adds an
empty internal `SourceAssertion` persistence foundation, and closes the
private-Signal-notes-to-public-Installation-notes graduation path. It does not
reconcile, migrate, merge, delete, rewrite, or otherwise reinterpret any of
the 149 existing `Installation` rows.

## Files created

- `app/models/source_assertion.py`
- `scripts/migrate_evidence_identity_slice1.py`
- `tests/test_source_assertions.py`
- `docs/domain/evidence-installation-identity-slice1-report.md`

## Files modified

- `app/database.py` — enables SQLite foreign-key enforcement for every
  application connection.
- `app/models/__init__.py` — exports `SourceAssertion`.
- `app/models/source.py` — adds the source-to-assertions relationship.
- `app/models/airport.py` — adds airport/runway-to-assertions relationships.
- `scripts/graduate_signal_to_installation.py` — no longer copies private
  `Signal.notes` to public `Installation.notes`.
- `tests/test_model_contract.py` — updates the model contract for the new
  internal table and relationships.
- `tests/test_graduate_signal_to_installation.py` — verifies graduation leaves
  public installation notes empty rather than promoting private notes.

## Migration created

`scripts/migrate_evidence_identity_slice1.py` is the one-time, explicit SQLite
migration. It requires `--allow-database-write`, verifies the selected target
read-only before writing, creates a timestamped backup using the repository
convention, refuses to proceed if logical source-ID orphans exist, rebuilds
only the malformed source-FK declarations, and creates `source_assertions`.

## Database integrity investigation

The defect was confirmed against SQLAlchemy metadata, migration history, and
the live development database `data/runway_safe.db`.

- SQLAlchemy models declared `Installation.source_id`, `Signal.source_id`, and
  `Incident.source_id` as references to `sources.id`.
- Live SQLite `PRAGMA foreign_key_list` showed all three pointed instead to the
  dropped `sources_old.id` table.
- SQLite foreign-key enforcement was disabled (`PRAGMA foreign_keys = 0`).
- `PRAGMA foreign_key_check` returned 243 violations before repair.
- The live database contained 68 `Source` rows, 149 Installation rows, 68
  Signal rows, and 26 Incident rows. Each non-null `source_id` was found in
  the current `sources` table: zero logical source-ID orphans.

The cause is the historic `ensure_source_url_nullable` table rebuild in
`scripts/add_rw_shareholder_letter_signals.py`: it renamed `sources` to
`sources_old`, copied rows to a new `sources`, then dropped `sources_old`.
SQLite rewrote dependent FK declarations during the rename. Source IDs were
copied unchanged, so the relationships were logically valid but never
enforced.

## Repair performed

Before the database write, the migration verified the target and created:

`data/backups/runway_safe-pre-evidence-identity-slice1-20260816-055109.db`

The migration rebuilt `installations`, `signals`, and `incidents` only to
change `source_id` constraints from `sources_old.id` to `sources.id`, retaining
every column value and index. It then created the empty `source_assertions`
table. No source or legacy entity row was deleted or rewritten.

After repair:

- `PRAGMA foreign_key_check` returned no violations.
- all three source foreign keys reference `sources.id`.
- a fresh application connection reports `PRAGMA foreign_keys = 1`.
- row counts remain Sources 68, Installations 149, Signals 68, Incidents 26;
  SourceAssertions 0.

## SourceAssertion fields

`SourceAssertion` stores one upstream claim/record before reconciliation:

- identity/links: `id`, required `source_id`, optional `airport_id`, optional
  `runway_id`, optional explicit `runway_end`;
- governed classification: `assertion_type`, `evidence_quality`, `review_state`;
- raw evidence: airport identifier/name, runway/end values, product/type,
  year/date wording, vendor/manufacturer wording, count, relevant source text;
- upstream provenance: source-record identifier, source locator, raw-fragment
  hash, artifact identity, parser/import identifier, extraction timestamp; and
- `created_at`.

All raw/evidence fields remain nullable when the source does not provide them.
No `Installation` relationship is present, so a new assertion cannot imply a
physical identity or create/link an Installation automatically.

## Assertion vocabulary

The governed assertion types are:

- `airport_inventory`
- `runway`
- `runway_end`
- `physical_system`
- `historical`
- `project_construction`

Evidence quality supports `unverified_candidate`, `direct_strong`,
`corroborated`, `partial`, and `ambiguous`; review state is `unreviewed` or
`reviewed`. This permits incomplete source evidence without promoting its
granularity.

## Idempotency strategy

Two database-enforced identities implement the approved source-record rule:

1. `(source_id, source_record_identifier)` where an upstream stable record ID
   exists.
2. `(source_id, artifact_identity, source_locator, raw_fragment_hash)` as the
   fallback record identity.

An assertion must provide a source-record identifier or both a deterministic
locator and raw-fragment hash. Airport/type/year, title, URL, coordinates, and
similarity are not keys. Two distinct records at the same airport with the
same product and year remain distinct.

## Public/private boundary and graduation result

`SourceAssertion` is internal only: it is not loaded by static-export views,
templates, or `data.json`. The existing static-export exclusion of
`Signal.notes` and `manual_year_estimate` remains intact.

The graduation leak was confirmed: the prior code copied private
`Signal.notes` into public `Installation.notes`. Graduation now preserves its
manual airport/runway/source/type/year/vendor behavior while leaving
`Installation.notes` unset. A private annotation therefore cannot become
public installation evidence automatically.

## Test and validation results

- Focused suite: **28 passed** (`test_model_contract`, `test_source_assertions`,
  `test_graduate_signal_to_installation`, and `test_static_export`).
- Full suite: **340 passed**.
- `git diff --check`: passed with no whitespace errors.

The focused tests cover model construction, vocabulary, nullable unknown
evidence, source/airport/runway relationships, both idempotency forms,
duplicate rejection, same-airport/type/year distinction, ambiguous raw year
preservation, no Installation creation, static export privacy, graduation
privacy, and FK repair/integrity.

## Blockers

None. The FK repair required no ambiguous data choice because every existing
non-null source reference still matched a current `Source` row.

## Explicit legacy-data confirmation

The 149 existing Installation rows were **not reconciled, migrated, merged,
deleted, or rewritten**. No existing FAA CSV, FAA fact-sheet, Tableau,
USAspending, or manual-research data was backfilled into SourceAssertions, and
no importer behavior was changed.
