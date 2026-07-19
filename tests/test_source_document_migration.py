from argparse import Namespace
from datetime import date

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.migration_metadata import target_metadata


BASELINE_REVISION = "8edd52d34c76"
HEAD_REVISION = "f2a7c84e9d10"

LEGACY_ROWS = (
    (1, 1, "Resolution 025-2024 – Amended Common Ground Recommendation Airport Map", "ALP", "Pitkin County", "http://www.aspenairport.com/wp-content/uploads/2024/07/bocc.res_.025.2024-Amending-Res-105-2020.pdf", date(2024, 5, 16), "official"),
    (2, 2, "Runway 6 Departure End EMAS Project", "Procurement", "Manchester-Boston Regional Airport", "https://www.flymanchester.com/", date(2026, 6, 1), "official"),
    (3, 3, "FAA Airport Construction Impact Report", "FAA", "FAA", "https://www.faa.gov/", date(2026, 7, 1), "official"),
    (4, 4, "FAA Airport Construction Impact Report", "FAA", "FAA", "https://www.faa.gov/", date(2026, 7, 1), "official"),
    (5, 5, "Runway 8/26 Runway Safety Improvements Project", "Airport", "Fulton County", "https://www.fultoncountyga.gov/", date(2026, 5, 11), "official"),
    (6, 6, "2026–2031 Capital Improvements Program", "CIP", "Broome County", "https://broomecountyny.gov/", date(2026, 1, 1), "official"),
    (7, 7, "California Aeronautics Capital Improvement Plan", "CIP", "Caltrans", "https://dot.ca.gov/", date(2025, 6, 1), "official"),
    (8, 8, "Metropolitan Airports Commission Capital Improvement Program", "CIP", "Metropolitan Airports Commission", "https://www.metroairports.org/", date(2025, 12, 1), "official"),
    (9, 9, "Port Authority Board Agenda – EMAS planning authorization", "Authority", "Port Authority of New York and New Jersey", "https://www.panynj.gov/", date(2026, 3, 19), "official"),
    (10, 10, "Final Environmental Assessment", "Environmental", "Cape Cod Gateway Airport", "https://flyhya.com/", date(2025, 11, 4), "official"),
    (11, 11, "MKC Airport Master Plan – Existing Conditions", "Master Plan", "Kansas City Aviation Department", "https://mkc.airportstudy.net/", date(2026, 1, 7), "official"),
    (12, 12, "Internal watch item", "Watchlist", "Runway Safe Intelligence", "https://www.flychicago.com/midway/", date(2026, 7, 17), "internal"),
)


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    config.cmd_opts = Namespace(x=["allow_database_write=true"])
    return config


def _insert_legacy_fixture(database_url: str) -> list[tuple]:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for identifier in range(1, 13):
                connection.execute(
                    text("INSERT INTO airports (id, name, country, created_at, updated_at) VALUES (:id, :name, 'USA', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                    {"id": identifier, "name": f"Airport {identifier}"},
                )
                connection.execute(
                    text("INSERT INTO projects (id, airport_id, title, project_type, status, confidence_level, probability_score) VALUES (:id, :id, :title, 'safety', 'planned', 'planned', 5.0)"),
                    {"id": identifier, "title": f"Project {identifier}"},
                )

            for legacy_id, project_id, title, source_type, publisher, url, published_date, reliability in LEGACY_ROWS:
                connection.execute(
                    text("""
                        INSERT INTO sources (
                            id, project_id, title, source_type, publisher, url,
                            published_date, accessed_date, document_reference,
                            page_number, summary, reliability_level
                        ) VALUES (
                            :id, :project_id, :title, :source_type, :publisher, :url,
                            :published_date, '2026-07-17', NULL, NULL, NULL, :reliability
                        )
                    """),
                    {
                        "id": legacy_id,
                        "project_id": project_id,
                        "title": title,
                        "source_type": source_type,
                        "publisher": publisher,
                        "url": url,
                        "published_date": published_date,
                        "reliability": reliability,
                    },
                )

            return connection.execute(text("SELECT * FROM sources ORDER BY id")).all()
    finally:
        engine.dispose()


def test_expand_migration_normalizes_eligible_legacy_sources_and_downgrades_safely(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'expand.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, BASELINE_REVISION)
    legacy_before = _insert_legacy_fixture(database_url)

    command.upgrade(config, HEAD_REVISION)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM publishing_sources")) == 10
            assert connection.scalar(text("SELECT COUNT(*) FROM documents")) == 10
            assert connection.scalar(text("SELECT COUNT(*) FROM project_documents")) == 11
            assert connection.execute(text("SELECT * FROM sources ORDER BY id")).all() == legacy_before
            assert connection.scalar(text("SELECT COUNT(*) FROM sources WHERE title = 'Internal watch item'")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM documents WHERE title = 'Internal watch item'")) == 0

            faa_links = connection.execute(text("""
                SELECT pd.project_id
                FROM project_documents AS pd
                JOIN documents AS d ON d.id = pd.document_id
                WHERE d.title = 'FAA Airport Construction Impact Report'
                ORDER BY pd.project_id
            """)).scalars().all()
            assert faa_links == [3, 4]

            statuses = dict(
                connection.execute(
                    text("SELECT status, COUNT(*) FROM documents GROUP BY status")
                ).all()
            )
            assert statuses == {"active": 1, "incomplete": 9}
            connection.execute(text("PRAGMA foreign_keys=ON"))
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            assert compare_metadata(MigrationContext.configure(connection), target_metadata) == []
    finally:
        engine.dispose()

    command.downgrade(config, BASELINE_REVISION)
    engine = create_engine(database_url)
    try:
        assert not {"publishing_sources", "documents", "project_documents"} & set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM sources ORDER BY id")).all() == legacy_before
    finally:
        engine.dispose()


def test_fresh_database_upgrades_from_empty_to_head(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    command.upgrade(_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) == set(target_metadata.tables) | {"alembic_version"}
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), target_metadata) == []
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
