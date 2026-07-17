# Sprint 1 Complete – 2026-07-17

## Outcome

Sprint 1 established a maintainable core data foundation for RWI. The project progressed from a working monolithic MVP to a tested, domain-oriented SQLAlchemy model package with explicit database initialization and migration infrastructure.

## Completed

- Improved the project documentation, architecture rules, glossary, current-state guidance, and development context. The runway model now explicitly requires exactly two runway ends, and EMAS history is preserved per runway end.
- Refactored the monolithic `app/models.py` into an `app/models/` package while preserving the existing schema, imports, relationships, cascades, defaults, and application behavior.
- Added SQLAlchemy contract tests that lock the legacy table, column, key, index, default, relationship, and cascade behavior before further domain changes.
- Added `RunwayEnd` with its runway relationship and uniqueness constraint on runway and designation.
- Added `EmasBed` with historical records, a current-state flag, and a SQLite partial unique index allowing at most one current bed per runway end.
- Preserved the legacy `EmasInstallation` model for later controlled migration.
- Removed schema creation as an import side effect of `app.main`. New databases can be initialized explicitly with `python -m scripts.init_db`; the explicit seed command continues to create missing tables before seeding.
- Configured Alembic to use project settings and the shared SQLAlchemy metadata. Online database access requires an explicit safety flag.
- Created the initial Alembic baseline revision, generated against an isolated empty SQLite database. It creates all eight current tables and supports upgrade, downgrade, and re-upgrade.
- Expanded the automated test suite from 1 test to 24 tests. Tests use isolated in-memory or temporary SQLite databases and do not depend on the working database.

## Final Architecture State

The current mapped schema contains `Airport`, `Runway`, `RunwayEnd`, `EmasBed`, `EmasInstallation`, `Project`, `Source`, and `Incident`. Models are exported through `app.models`, Alembic sees the same shared metadata, and schema lifecycle operations are explicit. `RunwayEnd` and `EmasBed` represent the target domain while `EmasInstallation` remains available for a future data-preserving transition.

## Lessons Learned

- Capture the existing schema contract before refactoring it.
- Separate structural refactoring from domain and migration changes.
- Never rely on application imports to create database tables.
- Generate a baseline against an empty database; comparing against an already-matching database produces an unusable empty revision.
- Rehearse migrations through upgrade, metadata comparison, foreign-key validation, downgrade, and re-upgrade on disposable databases.
- Preserve legacy structures until data mapping and rollback behavior are proven.

## Recommended Sprint 2 Focus

Establish a verified data transition workflow: stamp the existing database only after approval, create and validate two runway ends per runway, map legacy `EmasInstallation` records into `EmasBed` without data loss, and then update seed/import and read workflows. Keep source traceability and historical preservation as acceptance criteria before expanding CRUD or document ingestion.
