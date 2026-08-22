"""Add the UAC1 unknown-airport candidate tables (UAC2A,
docs/architecture/rwi-governed-new-airport-discovery-design.md, Slice 1 -
see docs/architecture/rwi-uac1-unknown-airport-candidate-persistence-report.md
for the reviewed, committed models this migration creates schema for).

Additive only: creates exactly two new tables (and their indexes/CHECK/
UNIQUE constraints) - `unknown_airport_candidates` and
`unknown_airport_candidate_reviews` - reused directly from the ORM models
(app.models.unknown_airport_candidate.UnknownAirportCandidate/
UnknownAirportCandidateReview) via SQLAlchemy's own CreateTable/CreateIndex
compilation, the same technique scripts/migrate_signal_disposition_d4d2.py
and scripts/migrate_evidence_identity_slice6c.py already use, so the
created schema can never drift from the model definition; no CHECK/FK/
UNIQUE clause is ever hardcoded here as literal text. No existing table,
column, or row is read, changed, merged, or deleted - no ALTER TABLE
appears anywhere in this script.

ONE SCRIPT FOR BOTH TABLES, DELIBERATELY: this migrates BOTH UAC1 tables
together, matching the established `migrate_evidence_identity_slice6c.py`
precedent (`TABLES = ("physical_installation_identities",
"installation_assertion_links")` - a parent identity table plus its own
append-only review/link child, created in one script) and the UAC1
critical review's own corrected migration-boundary guidance (see the UAC1
report's "UAC2 migration-boundary verdict"). `SourceAssertion.
unknown_airport_candidate_id` (a materially different, higher-risk change
to an EXISTING, already-migrated, real-data-bearing table) is explicitly
NOT part of this migration - that remains a separate, later, UAC2B slice.

NO BACKFILL, EVER: `upgrade()` never inserts a row into either new table
under any circumstance. Both tables are always empty immediately after a
normal upgrade (see `tests/test_unknown_airport_candidate_migration.py`'s
own `TestZeroBackfill`) - this migration creates schema only; candidate
discovery, fingerprinting, airport matching, and review recording all
remain the job of app.services.unknown_airport_candidate_persistence,
never this script.

PARTIAL-SCHEMA SAFETY: `upgrade()` treats "the table already exists" as
requiring proof it is the RIGHT table, not just any table with the right
name - `_verify_existing_table_matches_expected_schema()` compares the
persisted column set (name/type/nullable), primary key, foreign-key
targets, and the presence of every named CHECK/UNIQUE constraint this
migration's own target schema declares, against what is actually on disk
(the identical strict-comparison technique
scripts/migrate_signal_disposition_d4d2.py's own review-checkpoint fix
established, reused verbatim here rather than reimplemented, so
`inspect()["ready"]` can never disagree with what `upgrade()` would
actually do). A mismatch on either table raises
`IncompatibleExistingSchemaError` before anything is touched - this
script never drops, rebuilds, or otherwise "fixes" a pre-existing table
with an unexpected shape. A single missing table (the other one already
present with a verified-matching shape) is a legitimate, safely
completable partial state.

CONSERVATIVE DOWNGRADE: refuses outright - raising, no partial change
committed - if EITHER new table contains any row at all. Persisted
unknown-airport candidates and their append-only human review history are
exactly the kind of governed evidence/audit history this whole slice
exists to make permanent; a schema downgrade must never be the mechanism
that silently discards it (matching migrate_signal_disposition_d4d2.py's
own identical discipline). Only when both tables are confirmed empty does
downgrade proceed, dropping `unknown_airport_candidate_reviews` before
`unknown_airport_candidates` (child before parent, the FK-safe order).

A timestamped backup is required before this script writes to the real
database. This script is never run against the real database in this
implementation task (UAC2A); every test uses an isolated temp-file SQLite
database (tmp_path) - the real migration is explicitly deferred to a
separate, later, explicitly-authorized operational step.
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

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

# Parent (candidate) before child (review) - the order every create/verify
# step below relies on; downgrade() reverses it (child before parent, the
# FK-safe drop order).
TABLES = ("unknown_airport_candidates", "unknown_airport_candidate_reviews")

# The named CHECK/UNIQUE constraints this migration's own target schema
# declares, keyed by table - used only to verify an ALREADY-EXISTING
# table's stored CREATE TABLE text still names them (a cheap, robust
# presence check via substring search, immune to whitespace/formatting
# differences between SQLite's own stored SQL and SQLAlchemy's compiled
# text). Never used to CREATE anything - table creation always compiles
# fresh from Base.metadata.tables, never from this literal list.
_EXPECTED_NAMED_CONSTRAINTS = {
    "unknown_airport_candidates": ("uq_unknown_airport_candidates_fingerprint",),
    "unknown_airport_candidate_reviews": (
        "ck_unknown_airport_candidate_reviews_action",
        "ck_unknown_airport_candidate_reviews_match_target_required",
        "ck_unknown_airport_candidate_reviews_match_target_only_for_match",
    ),
}


class IncompatibleExistingSchemaError(RuntimeError):
    """Raised by upgrade() when a table with the expected name already
    exists but its persisted shape does not match
    app.models.unknown_airport_candidate's own current model - this
    migration never drops, rebuilds, or otherwise silently reconciles an
    unexpected existing table; a human must resolve the collision by
    hand."""


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-unknown-airport-candidates-uac2a-{timestamp}.db"


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


def _expected_columns(name: str) -> dict:
    """(col_name -> (type_affinity_str, notnull, is_pk)) derived fresh from
    the live ORM model - never hardcoded, so it can never drift from
    app.models.unknown_airport_candidate."""
    table = Base.metadata.tables[name]
    return {
        column.name: (str(column.type), not column.nullable, bool(column.primary_key))
        for column in table.columns
    }


def _expected_foreign_keys(name: str) -> "set[tuple[str, str, str]]":
    table = Base.metadata.tables[name]
    return {
        (fk.parent.name, fk.column.table.name, fk.column.name)
        for column in table.columns
        for fk in column.foreign_keys
    }


def _expected_index_names(name: str) -> "set[str]":
    """The plain (non-UNIQUE-constraint-backed) index names this
    migration's own target schema declares for `name`, derived fresh from
    the live ORM model - e.g. `ix_unknown_airport_candidates_resolved_airport_id`
    for `resolved_airport_id: Mapped[...] = mapped_column(..., index=True)`.
    Deliberately excludes the UNIQUE constraint's own SQLite-generated
    autoindex (`sqlite_autoindex_...`), which is not a `table.indexes`
    member and is already covered by the named-constraint presence check
    in `_schema_mismatch_reasons()` below - checking for it here would be
    redundant, not additive."""
    table = Base.metadata.tables[name]
    return {index.name for index in table.indexes}


def _schema_mismatch_reasons(connection: sqlite3.Connection, name: str) -> "list[str]":
    """The single source of truth for "does this already-existing table
    genuinely match app.models.unknown_airport_candidate's current model" -
    returns an empty list if it matches exactly, or one human-readable
    reason string per mismatch found otherwise. Both `upgrade()` (via
    `_verify_existing_table_matches_expected_schema()`, which raises using
    this same list) and `inspect()` (via `_table_matches_expected_schema()`,
    a boolean wrapper) call this one function, so they can never disagree
    about whether an existing table is genuinely compatible - the same
    discipline scripts/migrate_signal_disposition_d4d2.py's own
    review-checkpoint fix established.

    Deliberately compares STRUCTURED facts (column name/type/nullability/
    PK, foreign-key targets, and named-constraint presence via a substring
    search over the table's own stored `sqlite_master.sql` text) rather
    than a full raw-text comparison against a freshly compiled CREATE
    TABLE statement - SQLite's own stored SQL and SQLAlchemy's compiled
    output can legitimately differ in whitespace/quoting/clause order for
    an otherwise IDENTICAL table, which a naive full-text comparison would
    misreport as incompatible.
    """
    reasons: "list[str]" = []

    actual_columns_raw = connection.execute(f"PRAGMA table_info({name})").fetchall()
    actual_columns = {
        row[1]: (row[2], bool(row[3]), bool(row[5])) for row in actual_columns_raw  # name: (type, notnull, pk)
    }
    expected_columns = _expected_columns(name)
    if actual_columns != expected_columns:
        reasons.append(
            f"columns do not match the expected UAC1 schema - expected {expected_columns!r}, "
            f"found {actual_columns!r}"
        )

    actual_fks = {
        (row[3], row[2], row[4]) for row in connection.execute(f"PRAGMA foreign_key_list({name})")
    }
    expected_fks = _expected_foreign_keys(name)
    if actual_fks != expected_fks:
        reasons.append(
            f"foreign keys do not match the expected UAC1 schema - expected {expected_fks!r}, "
            f"found {actual_fks!r}"
        )

    stored_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    stored_sql = stored_sql_row[0] if stored_sql_row else ""
    missing_constraints = [
        constraint_name
        for constraint_name in _EXPECTED_NAMED_CONSTRAINTS[name]
        if constraint_name not in stored_sql
    ]
    if missing_constraints:
        reasons.append(
            f"missing expected named constraint(s) {missing_constraints!r} - found stored schema: {stored_sql!r}"
        )

    # Review-checkpoint fix (UAC2A critical review): a table with every
    # expected column/FK/named-constraint correct, but missing one or
    # more of the ORM model's own plain (non-UNIQUE-backed) indexes,
    # previously passed this check silently - reproduced directly: such a
    # table is never re-indexed by upgrade() either, since upgrade() only
    # creates indexes for a table it creates fresh (the `continue` branch
    # for an already-existing table skips the index-creation loop
    # entirely). Not a data-safety defect - SQLite still enforces every
    # NOT NULL/FK/CHECK/UNIQUE constraint without the index, only query
    # performance on that column degrades - but a genuine "does not
    # actually match the ORM model" gap, closed the same way the
    # named-constraint check already is: comparing the expected set
    # against what is actually present in sqlite_master, never against
    # extra/unexpected indexes (an extra index is harmless and never
    # flagged - only a MISSING expected one is).
    actual_index_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (name,)
        )
    }
    missing_indexes = _expected_index_names(name) - actual_index_names
    if missing_indexes:
        reasons.append(f"missing expected index(es) {sorted(missing_indexes)!r}")

    return reasons


def _table_matches_expected_schema(connection: sqlite3.Connection, name: str) -> bool:
    """Boolean wrapper over `_schema_mismatch_reasons()` - the one function
    `inspect()`'s own `ready` computation calls, so it can never report a
    table "ready" that `upgrade()` would actually refuse."""
    return not _schema_mismatch_reasons(connection, name)


def _verify_existing_table_matches_expected_schema(connection: sqlite3.Connection, name: str) -> None:
    """Raises IncompatibleExistingSchemaError if a table named `name`
    exists but its persisted shape does not match
    app.models.unknown_airport_candidate's current model - called only
    when the table already exists (upgrade() never calls this for a table
    it is about to create fresh)."""
    reasons = _schema_mismatch_reasons(connection, name)
    if reasons:
        raise IncompatibleExistingSchemaError(
            f"table {name!r} already exists but is incompatible with the expected UAC1 schema: "
            f"{'; '.join(reasons)}. Refusing to alter or rebuild an existing table automatically; "
            "resolve this collision by hand before re-running upgrade()."
        )


def inspect(database: Path) -> dict:
    """Read-only, via its own `sqlite3.connect(..., mode=ro)` connection -
    never the caller's ORM engine. Returns enough deterministic
    information for a future UAC2B schema gate to compose alongside the
    existing D4D2-established `inspect()` shape: table existence, column
    sets, the named-constraint presence check, foreign-key targets, row
    counts, and a real `PRAGMA foreign_key_check` result. Never mutates
    anything. `ready` is computed via the exact same strict, structural
    `_table_matches_expected_schema()` check `upgrade()` itself uses - a
    table with every expected column NAME present but a wrong type,
    missing constraint, or wrong FK target is never reported ready."""
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        tables_exist = {name: _table_exists(connection, name) for name in TABLES}
        columns = {
            name: (
                {row[1] for row in connection.execute(f"PRAGMA table_info({name})")}
                if tables_exist[name]
                else set()
            )
            for name in TABLES
        }
        foreign_keys = {
            name: (
                sorted(
                    (row[3], row[2], row[4])  # (from_column, ref_table, to_column)
                    for row in connection.execute(f"PRAGMA foreign_key_list({name})")
                )
                if tables_exist[name]
                else []
            )
            for name in TABLES
        }
        stored_sql = {
            name: (
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()[0]
                if tables_exist[name]
                else None
            )
            for name in TABLES
        }
        named_constraints_present = {
            name: {
                constraint_name: (stored_sql[name] is not None and constraint_name in stored_sql[name])
                for constraint_name in _EXPECTED_NAMED_CONSTRAINTS[name]
            }
            for name in TABLES
        }
        counts = {
            name: (connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] if tables_exist[name] else 0)
            for name in TABLES
        }
        matches_expected_schema = {
            name: (tables_exist[name] and _table_matches_expected_schema(connection, name))
            for name in TABLES
        }
        ready = matches_expected_schema["unknown_airport_candidates"] and matches_expected_schema[
            "unknown_airport_candidate_reviews"
        ]
        return {
            "database": str(database),
            "tables_exist": tables_exist,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "named_constraints_present": named_constraints_present,
            "matches_expected_schema": matches_expected_schema,
            "counts": counts,
            "ready": ready,
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def upgrade(database: Path) -> None:
    """Idempotent: creates only the tables that do not yet exist. A table
    that already exists is verified against the current ORM model
    (`_verify_existing_table_matches_expected_schema`) rather than assumed
    correct - a genuine mismatch raises `IncompatibleExistingSchemaError`
    and leaves the database completely untouched (the whole operation runs
    inside one transaction). Never inserts a row into either table under
    any circumstance."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for name in TABLES:
            if _table_exists(connection, name):
                _verify_existing_table_matches_expected_schema(connection, name)
                continue
            table = Base.metadata.tables[name]
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            for index in table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=sqlite.dialect())))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"upgrade() would leave foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def downgrade(database: Path) -> None:
    """Refuses outright (raises, no partial change committed) if EITHER
    table contains any row - persisted unknown-airport candidates and
    their append-only human review history are permanent audit evidence,
    never discardable by a schema downgrade. Only when both tables are
    confirmed empty does this drop them, in FK-safe order
    (`unknown_airport_candidate_reviews` before `unknown_airport_candidates`)."""
    connection = sqlite3.connect(database.resolve())
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        row_counts = {
            name: (
                connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                if _table_exists(connection, name)
                else 0
            )
            for name in TABLES
        }
        nonempty = {name: count for name, count in row_counts.items() if count > 0}
        if nonempty:
            raise RuntimeError(
                "downgrade() refused: the following table(s) contain recorded candidate/review history "
                f"and would be destroyed: {nonempty!r}. No table was dropped."
            )
        for name in reversed(TABLES):
            if _table_exists(connection, name):
                connection.execute(f"DROP TABLE {name}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
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
