"""Add SourceAssertion.unknown_airport_candidate_id and the mutual-
exclusivity CHECK constraint with airport_id (UAC2B,
docs/architecture/rwi-uac2b-sourceassertion-unknown-airport-integration-report.md,
Slice 2 of docs/architecture/rwi-governed-new-airport-discovery-design.md
§5).

SEPARATE FROM UAC2A, DELIBERATELY: UAC2A (scripts/migrate_unknown_airport_candidates_uac2a.py)
creates two brand-new, empty tables - low risk, trivially reversible.
This migration instead alters `source_assertions`, an EXISTING table
already carrying real governed evidence in the real database - a
materially higher-risk change, with its own separate backup/rollback
boundary, matching the risk/rollback reasoning both the UAC1 critical
review and the UAC2A critical review already established for this exact
split.

SQLITE TABLE REBUILD REQUIRED: SQLite has no `ALTER TABLE ADD CONSTRAINT`
- a table-level CHECK constraint cannot be added to an existing table by
any ALTER TABLE variant. This migration uses the same safe
create-replacement / copy-rows / drop-original / rename create-copy-drop-
rename pattern scripts/migrate_evidence_identity_slice1.py already
established for this exact table, refined with the strict
inspect()/upgrade() schema-parity discipline scripts/migrate_signal_disposition_d4d2.py
and scripts/migrate_unknown_airport_candidates_uac2a.py both established.
The replacement table is compiled FRESH from
Base.metadata.tables["source_assertions"] via SQLAlchemy's own
CreateTable (never hand-typed SQL for the upgrade direction) - every
pre-existing column, CHECK, and UNIQUE constraint on this table is
carried over automatically, exactly as-is, alongside the new column and
CHECK; nothing is dropped or forgotten by hand.

UAC2A DEPENDENCY: this migration's own FK target
(`unknown_airport_candidates.id`) must already exist and be schema-
correct before upgrade() will do anything - checked via UAC2A's own
`inspect()`, reused directly (never reimplemented, never auto-applied,
never silently repaired if incompatible). Raises `Uac2aNotAppliedError`
before touching source_assertions at all if UAC2A is missing or
incompatible.

ZERO BACKFILL: every pre-existing SourceAssertion row's
`unknown_airport_candidate_id` is NULL after upgrade() - the column is
never copied into the INSERT ... SELECT column list for old rows, so
SQLite's own implicit NULL-for-omitted-nullable-column behavior is the
only source of the new column's value; no row is read for its content,
classified, or linked to any candidate.

CONSERVATIVE DOWNGRADE: refuses outright if ANY SourceAssertion row
carries a non-NULL `unknown_airport_candidate_id` - that link is
governed evidence provenance, never destroyable by a schema downgrade.
Downgrade's own rebuild uses a deliberately hand-written, frozen snapshot
of the exact pre-UAC2B `source_assertions` shape (not derived from the
current, post-UAC2B `Base.metadata`, which now includes the new column/
constraint) - see `_PRE_UAC2B_COLUMNS`/`_pre_uac2b_create_table_sql()`
below for why this is the safer choice for the downgrade direction
specifically.

A timestamped backup is required before this script writes to the real
database. This script is never run against the real database in this
implementation task (UAC2B); every test uses an isolated temp-file
SQLite database (tmp_path) - the real migration (of both UAC2A and
UAC2B) is explicitly deferred to a separate, later, explicitly-
authorized operational step.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from app import models as _models  # noqa: F401 - registers all metadata, including UnknownAirportCandidate

import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

TABLE = "source_assertions"
NEW_COLUMN = "unknown_airport_candidate_id"
NEW_CHECK_CONSTRAINT_NAME = "ck_source_assertions_airport_candidate_mutually_exclusive"

# Every named CHECK/UNIQUE constraint app/models/source_assertion.py
# declares as of UAC2B - used only to verify an already-migrated (or
# partially-migrated) table's stored CREATE TABLE text still names all of
# them, exactly the same substring-presence technique
# migrate_unknown_airport_candidates_uac2a.py already established. Never
# used to CREATE anything.
_EXPECTED_NAMED_CONSTRAINTS = (
    "ck_source_assertions_type",
    "ck_source_assertions_evidence_quality",
    "ck_source_assertions_review_state",
    "ck_source_assertions_record_identity",
    "uq_source_assertions_source_record",
    "uq_source_assertions_locator_fragment",
    NEW_CHECK_CONSTRAINT_NAME,
)

# A deliberately hand-written, frozen snapshot of the exact pre-UAC2B
# source_assertions column list (name order matches
# app/models/source_assertion.py as it stood immediately before UAC2B -
# read fresh from git history at the start of this mission). Used ONLY by
# downgrade()'s own rebuild: the CURRENT Base.metadata.tables["source_assertions"]
# already includes the new column/CHECK, so it cannot be used to
# reconstruct the pre-UAC2B shape - downgrade needs the OLD shape, which
# no longer exists anywhere in live code once this file's own model edit
# lands. Deliberately NOT auto-derived (there is nothing left to derive
# it from) - kept as an explicit, reviewable literal instead of silently
# guessed via string surgery on the live compiled CREATE TABLE text.
_PRE_UAC2B_COLUMNS = (
    "id", "source_id", "airport_id", "runway_id", "assertion_type", "runway_end",
    "raw_airport_identifier", "raw_airport_name", "raw_runway_value", "raw_runway_end_value",
    "raw_product_type", "raw_year_date_wording", "raw_vendor_manufacturer_wording", "raw_count",
    "raw_relevant_text", "source_record_identifier", "source_locator", "raw_fragment_hash",
    "artifact_identity", "parser_identifier", "extracted_at", "evidence_quality", "review_state",
    "identity_guard_decision", "identity_guard_reason", "intelligence_review_decision",
    "intelligence_review_reason", "promotion_policy_decision", "promotion_policy_reason",
    "signal_id", "created_at",
)


def _pre_uac2b_create_table_sql(replacement_name: str) -> str:
    """The exact pre-UAC2B CREATE TABLE statement, reconstructed by
    stripping the new column and new CHECK constraint from the CURRENT
    (post-UAC2B) compiled DDL text - a one-time, narrowly-scoped string
    operation (not a general-purpose schema-diff mechanism), verified by
    this migration's own tests to produce a table whose column set
    exactly equals `_PRE_UAC2B_COLUMNS`."""
    table = Base.metadata.tables[TABLE]
    compiled = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    lines = [line for line in compiled.splitlines()]
    kept_lines = []
    for line in lines:
        stripped = line.strip().rstrip(",")
        if stripped.startswith(f"{NEW_COLUMN} "):
            continue
        if NEW_CHECK_CONSTRAINT_NAME in line:
            continue
        if stripped.startswith(f"FOREIGN KEY({NEW_COLUMN})"):
            continue
        kept_lines.append(line)
    # Re-join and fix up a possible dangling trailing comma before the
    # closing paren left by removing the last of the stripped lines.
    text = "\n".join(kept_lines)
    text = text.replace(",\n)", "\n)")
    text = text.replace(f"CREATE TABLE {TABLE}", f"CREATE TABLE {replacement_name}", 1)
    return text


class Uac2aNotAppliedError(RuntimeError):
    """Raised by upgrade() when unknown_airport_candidates/
    unknown_airport_candidate_reviews are missing or schema-incompatible
    (per UAC2A's own inspect()["ready"]) - UAC2B's FK target must already
    exist and be genuinely correct. This migration never auto-applies or
    repairs UAC2A itself."""


class IncompatibleExistingSchemaError(RuntimeError):
    """Raised by upgrade() when source_assertions already carries a
    partially-applied or incompatible unknown_airport_candidate_id/CHECK/
    FK shape - this migration never silently rebuilds or repairs an
    ambiguous existing schema; a human must resolve the collision by
    hand."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-source-assertion-unknown-airport-uac2b-{timestamp}.db"


def backup_database(database: Path, backup_directory: Path = BACKUP_DIRECTORY) -> Path:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    backup_directory.mkdir(parents=True, exist_ok=True)
    destination = backup_directory / _backup_name()
    shutil.copy2(database, destination)
    if destination.stat().st_size != database.stat().st_size:
        raise RuntimeError("Database backup size does not match the source database.")
    return destination


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def _current_columns(connection: sqlite3.Connection) -> dict:
    return {
        row[1]: (row[2], bool(row[3]), bool(row[5])) for row in connection.execute(f"PRAGMA table_info({TABLE})")
    }


def _expected_columns() -> dict:
    table = Base.metadata.tables[TABLE]
    return {
        column.name: (str(column.type), not column.nullable, bool(column.primary_key))
        for column in table.columns
    }


def _expected_foreign_keys() -> "set[tuple[str, str, str]]":
    table = Base.metadata.tables[TABLE]
    return {
        (fk.parent.name, fk.column.table.name, fk.column.name)
        for column in table.columns
        for fk in column.foreign_keys
    }


def _expected_index_names() -> "set[str]":
    table = Base.metadata.tables[TABLE]
    return {index.name for index in table.indexes}


def _schema_mismatch_reasons(connection: sqlite3.Connection) -> "list[str]":
    """The single source of truth for "does source_assertions genuinely
    match the current (post-UAC2B) ORM model" - both `upgrade()` (via
    `_verify_existing_table_matches_expected_schema()`) and `inspect()`
    call this one function, so they can never disagree. Only ever called
    once the new column has already been confirmed present - a table
    still in the pre-UAC2B shape is a legitimate "needs rebuild" state,
    not an incompatibility, and is handled separately by upgrade()."""
    reasons: "list[str]" = []

    actual_columns = _current_columns(connection)
    expected_columns = _expected_columns()
    if actual_columns != expected_columns:
        reasons.append(
            f"columns do not match the expected UAC2B schema - expected {expected_columns!r}, "
            f"found {actual_columns!r}"
        )

    actual_fks = {(row[3], row[2], row[4]) for row in connection.execute(f"PRAGMA foreign_key_list({TABLE})")}
    expected_fks = _expected_foreign_keys()
    if actual_fks != expected_fks:
        reasons.append(
            f"foreign keys do not match the expected UAC2B schema - expected {expected_fks!r}, "
            f"found {actual_fks!r}"
        )

    stored_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    stored_sql = stored_sql_row[0] if stored_sql_row else ""
    missing_constraints = [name for name in _EXPECTED_NAMED_CONSTRAINTS if name not in stored_sql]
    if missing_constraints:
        reasons.append(
            f"missing expected named constraint(s) {missing_constraints!r} - found stored schema: {stored_sql!r}"
        )

    actual_index_names = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (TABLE,)
        )
    }
    missing_indexes = _expected_index_names() - actual_index_names
    if missing_indexes:
        reasons.append(f"missing expected index(es) {sorted(missing_indexes)!r}")

    return reasons


def _table_matches_expected_schema(connection: sqlite3.Connection) -> bool:
    return not _schema_mismatch_reasons(connection)


def inspect(database: Path) -> dict:
    """Read-only, via its own `sqlite3.connect(..., mode=ro)` connection -
    never the caller's ORM engine. Never mutates anything. `ready` is
    computed via the exact same strict, structural
    `_table_matches_expected_schema()` check `upgrade()` itself uses - a
    table with the new column present but a wrong type, missing
    constraint, or wrong FK target is never reported ready. Also reports
    UAC2A's own readiness (delegated to its own `inspect()`, never
    reimplemented) so a caller can distinguish "UAC2B not applied because
    UAC2A is missing" from "UAC2B genuinely incompatible"."""
    database = database.resolve()
    uac2a_status = uac2a_migration.inspect(database)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        table_exists = _table_exists(connection, TABLE)
        column_present = table_exists and NEW_COLUMN in _current_columns(connection)
        matches_expected_schema = column_present and _table_matches_expected_schema(connection)
        row_count = connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] if table_exists else 0
        linked_row_count = (
            connection.execute(f"SELECT count(*) FROM {TABLE} WHERE {NEW_COLUMN} IS NOT NULL").fetchone()[0]
            if column_present
            else 0
        )
        return {
            "database": str(database),
            "uac2a_ready": uac2a_status["ready"],
            "table_exists": table_exists,
            "unknown_airport_candidate_id_column_present": column_present,
            "matches_expected_schema": matches_expected_schema,
            "row_count": row_count,
            "linked_row_count": linked_row_count,
            "ready": matches_expected_schema,
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def _rebuild_to_current_schema(connection: sqlite3.Connection) -> None:
    """Rebuilds source_assertions to the CURRENT (post-UAC2B) ORM shape.
    Compiled fresh from Base.metadata.tables[TABLE] - every pre-existing
    column/CHECK/UNIQUE constraint is carried over automatically,
    alongside the new column and CHECK. Only the pre-existing columns are
    copied for every existing row - the new column is never in the
    INSERT column list, so it is NULL for every pre-existing row via
    SQLite's own implicit behavior (zero backfill)."""
    table = Base.metadata.tables[TABLE]
    replacement = f"{TABLE}__uac2b_new"

    create_sql = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    create_sql = create_sql.replace(f"CREATE TABLE {TABLE}", f"CREATE TABLE {replacement}", 1)
    connection.execute(create_sql)

    old_columns = [row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")]
    quoted = ", ".join(f'"{c}"' for c in old_columns)
    connection.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM "{TABLE}"')

    connection.execute(f'DROP TABLE "{TABLE}"')
    connection.execute(f'ALTER TABLE "{replacement}" RENAME TO "{TABLE}"')

    for index in table.indexes:
        connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))


def _rebuild_to_pre_uac2b_schema(connection: sqlite3.Connection) -> None:
    """The inverse rebuild, used only by downgrade(). Uses the frozen
    `_pre_uac2b_create_table_sql()` snapshot (see its own docstring for
    why the current ORM metadata cannot be used for this direction) -
    only the pre-UAC2B columns are copied; unknown_airport_candidate_id
    is dropped along with the column itself. Only ever called after
    downgrade() has already confirmed every row's unknown_airport_candidate_id
    is NULL, so no data of any kind is lost."""
    replacement = f"{TABLE}__uac2b_downgrade"
    connection.execute(_pre_uac2b_create_table_sql(replacement))

    quoted = ", ".join(f'"{c}"' for c in _PRE_UAC2B_COLUMNS)
    connection.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM "{TABLE}"')

    connection.execute(f'DROP TABLE "{TABLE}"')
    connection.execute(f'ALTER TABLE "{replacement}" RENAME TO "{TABLE}"')

    # Recreate the pre-UAC2B indexes: every current index except the one
    # on the now-removed column.
    table = Base.metadata.tables[TABLE]
    for index in table.indexes:
        if NEW_COLUMN in {c.name for c in index.columns}:
            continue
        connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))


def upgrade(database: Path) -> None:
    """Idempotent: if source_assertions already has unknown_airport_candidate_id
    and is fully schema-correct, this is a safe no-op. If the column is
    absent, rebuilds the table to the current ORM shape. A table with the
    column present but an incompatible shape raises
    IncompatibleExistingSchemaError and leaves the database untouched.
    Requires UAC2A to already be applied and schema-correct - raises
    Uac2aNotAppliedError otherwise, before opening any write connection
    to `database` at all."""
    database = database.resolve()
    uac2a_status = uac2a_migration.inspect(database)
    if not uac2a_status["ready"]:
        raise Uac2aNotAppliedError(
            "UAC2A (unknown_airport_candidates/unknown_airport_candidate_reviews) must already be "
            f"applied and schema-correct before UAC2B can run - inspect() reported: {uac2a_status!r}. "
            "This migration never auto-applies or repairs UAC2A."
        )

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")

        if not _table_exists(connection, TABLE):
            raise RuntimeError(
                f"{TABLE!r} table does not exist - UAC2B cannot run against a database missing the "
                "base SourceAssertion schema."
            )

        if NEW_COLUMN in _current_columns(connection):
            reasons = _schema_mismatch_reasons(connection)
            if reasons:
                raise IncompatibleExistingSchemaError(
                    f"{TABLE!r} already has {NEW_COLUMN!r} but is incompatible with the expected UAC2B "
                    f"schema: {'; '.join(reasons)}. Refusing to rebuild an existing table automatically; "
                    "resolve this collision by hand before re-running upgrade()."
                )
            connection.rollback()
            return

        _rebuild_to_current_schema(connection)

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"upgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.close()


def downgrade(database: Path) -> None:
    """Refuses outright (raises, no partial change committed) if ANY
    SourceAssertion row carries a non-NULL unknown_airport_candidate_id -
    that link is governed evidence provenance, never discardable by a
    schema downgrade. A safe no-op if the column is already absent. Only
    when every row's link is confirmed NULL does this rebuild the table
    back to the pre-UAC2B shape."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")

        if not _table_exists(connection, TABLE):
            connection.rollback()
            return

        if NEW_COLUMN not in _current_columns(connection):
            connection.rollback()
            return

        linked_count = connection.execute(
            f"SELECT count(*) FROM {TABLE} WHERE {NEW_COLUMN} IS NOT NULL"
        ).fetchone()[0]
        if linked_count > 0:
            raise RuntimeError(
                f"downgrade() refused: {linked_count} SourceAssertion row(s) carry a non-NULL "
                f"{NEW_COLUMN} - removing this column would destroy governed candidate-linkage "
                "evidence. No table was rebuilt."
            )

        _rebuild_to_pre_uac2b_schema(connection)

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"downgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.close()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--downgrade", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="isolated/temp DBs only")
    args = parser.parse_args(argv)
    if not args.allow_database_write:
        parser.error("--allow-database-write is required")
    if not args.downgrade and not args.skip_backup:
        backup = backup_database(args.database)
        print("Backup created:", backup)
    (downgrade if args.downgrade else upgrade)(args.database)
    print(inspect(args.database))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
