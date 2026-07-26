from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import configure_mappers
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    AcquisitionRun,
    AcquisitionSource,
    Airport,
    Incident,
    Installation,
    PublishingSource,
    Runway,
    Signal,
    Source,
    Snapshot,
)


EXPECTED_COLUMNS = {
    "acquisition_sources": {
        "id": ("INTEGER", False, None, None),
        "publishing_source_id": ("INTEGER", False, None, None),
        "key": ("VARCHAR(150)", False, None, None),
        "display_name": ("VARCHAR(200)", False, None, None),
        "acquisition_type": ("VARCHAR(50)", False, None, None),
        "canonical_url": ("VARCHAR(1000)", False, None, None),
        "expected_media_type": ("VARCHAR(200)", True, None, None),
        "active": ("BOOLEAN", False, ("scalar", True), None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "updated_at": ("DATETIME", False, ("callable",), None),
    },
    "acquisition_runs": {
        "id": ("INTEGER", False, None, None),
        "acquisition_source_id": ("INTEGER", False, None, None),
        "snapshot_id": ("INTEGER", True, None, None),
        "started_at": ("DATETIME", False, None, None),
        "completed_at": ("DATETIME", True, None, None),
        "status": ("VARCHAR(18)", False, None, None),
        "request_url": ("VARCHAR(1000)", False, None, None),
        "final_url": ("VARCHAR(1000)", True, None, None),
        "http_status": ("INTEGER", True, None, None),
        "content_type": ("VARCHAR(200)", True, None, None),
        "response_headers": ("TEXT", True, None, None),
        "provider_version": ("VARCHAR(100)", False, None, None),
        "duration_seconds": ("FLOAT", False, None, None),
        "error_category": ("VARCHAR(100)", True, None, None),
        "error_detail": ("TEXT", True, None, None),
        "is_new_snapshot": ("BOOLEAN", True, None, None),
    },
    "snapshots": {
        "id": ("INTEGER", False, None, None),
        "acquisition_source_id": ("INTEGER", False, None, None),
        "first_acquisition_run_id": ("INTEGER", False, None, None),
        "payload": ("BLOB", False, None, None),
        "sha256": ("VARCHAR(64)", False, None, None),
        "byte_size": ("INTEGER", False, None, None),
        "media_type": ("VARCHAR(200)", True, None, None),
        "retrieved_at": ("DATETIME", False, None, None),
    },
    "airports": {
        "id": ("INTEGER", False, None, None),
        "iata_code": ("VARCHAR(3)", True, None, None),
        "icao_code": ("VARCHAR(4)", True, None, None),
        "faa_code": ("VARCHAR(5)", True, None, None),
        "name": ("VARCHAR(200)", False, None, None),
        "city": ("VARCHAR(100)", True, None, None),
        "state_region": ("VARCHAR(100)", True, None, None),
        "country": ("VARCHAR(100)", False, None, None),
        "latitude": ("FLOAT", True, None, None),
        "longitude": ("FLOAT", True, None, None),
        "website_url": ("VARCHAR(500)", True, None, None),
        "notes": ("TEXT", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "updated_at": ("DATETIME", False, ("callable",), ("callable",)),
    },
    "runways": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "designation": ("VARCHAR(20)", False, None, None),
        "length_m": ("INTEGER", True, None, None),
        "width_m": ("INTEGER", True, None, None),
        "surface": ("VARCHAR(50)", True, None, None),
        "notes": ("TEXT", True, None, None),
    },
    "publishing_sources": {
        "id": ("INTEGER", False, None, None),
        "name": ("VARCHAR(200)", False, None, None),
        "source_type": ("VARCHAR(50)", True, None, None),
        "homepage_url": ("VARCHAR(1000)", True, None, None),
        "country_code": ("VARCHAR(2)", True, None, None),
        "reliability_level": ("VARCHAR(30)", True, None, None),
        "notes": ("TEXT", True, None, None),
    },
    "sources": {
        "id": ("INTEGER", False, None, None),
        "title": ("VARCHAR(300)", False, None, None),
        "source_type": ("VARCHAR(50)", False, None, None),
        "publisher": ("VARCHAR(200)", True, None, None),
        "url": ("VARCHAR(1000)", True, None, None),
        "published_date": ("DATE", True, None, None),
        "retrieved_at": ("DATE", True, None, None),
        "document_reference": ("VARCHAR(200)", True, None, None),
        "page_number": ("VARCHAR(30)", True, None, None),
        "summary": ("TEXT", True, None, None),
        "reliability_level": ("VARCHAR(30)", False, ("scalar", "official"), None),
        "external_id": ("VARCHAR(200)", True, None, None),
    },
    "installations": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "source_id": ("INTEGER", True, None, None),
        "runway_end": ("VARCHAR(20)", True, None, None),
        "type": ("VARCHAR(30)", True, None, None),
        "manufacturer": ("VARCHAR(150)", True, None, None),
        "product_name": ("VARCHAR(100)", True, None, None),
        "install_year": ("INTEGER", True, None, None),
        "replacement_year": ("INTEGER", True, None, None),
        "status": ("VARCHAR(30)", True, None, None),
        "length_m": ("FLOAT", True, None, None),
        "width_m": ("FLOAT", True, None, None),
        "faa_accepted": ("BOOLEAN", True, None, None),
        "notes": ("TEXT", True, None, None),
        "confirmed_vendor": ("VARCHAR(150)", True, None, None),
    },
    "incidents": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "source_id": ("INTEGER", True, None, None),
        "incident_date": ("DATE", False, None, None),
        "aircraft_type": ("VARCHAR(100)", True, None, None),
        "operator": ("VARCHAR(150)", True, None, None),
        "incident_type": ("VARCHAR(100)", False, None, None),
        "emas_engaged": ("BOOLEAN", False, ("scalar", False), None),
        "injuries": ("VARCHAR(100)", True, None, None),
        "aircraft_damage": ("VARCHAR(100)", True, None, None),
        "summary": ("TEXT", True, None, None),
        "source_url": ("VARCHAR(1000)", True, None, None),
        "official_report_url": ("VARCHAR(1000)", True, None, None),
        "implies_replacement": ("BOOLEAN", False, ("scalar", True), None),
    },
    "signals": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "source_id": ("INTEGER", True, None, None),
        "title": ("VARCHAR(250)", False, None, None),
        "category": ("VARCHAR(50)", False, None, None),
        "confidence": ("VARCHAR(30)", False, None, None),
        "target_year": ("INTEGER", True, None, None),
        "notes": ("TEXT", True, None, None),
        "status": ("VARCHAR(50)", True, None, None),
        "planning_year": ("INTEGER", True, None, None),
        "procurement_year": ("INTEGER", True, None, None),
        "construction_start": ("DATE", True, None, None),
        "completion_date": ("DATE", True, None, None),
        "estimated_total_value_usd": ("NUMERIC(14, 2)", True, None, None),
        "estimated_emas_value_usd": ("NUMERIC(14, 2)", True, None, None),
        "probability_score": ("FLOAT", True, None, None),
        "supplier": ("VARCHAR(150)", True, None, None),
        "likely_supplier": ("VARCHAR(150)", True, None, None),
        "supplier_reason": ("TEXT", True, None, None),
        "last_verified_at": ("DATE", True, None, None),
        "manual_year_estimate": ("INTEGER", True, None, None),
        "confirmed_vendor": ("VARCHAR(150)", True, None, None),
        "installation_id": ("INTEGER", True, None, None),
    },
}

EXPECTED_PRIMARY_KEYS = {table_name: ("id",) for table_name in EXPECTED_COLUMNS}

EXPECTED_FOREIGN_KEYS = {
    "acquisition_sources": {("publishing_source_id", "publishing_sources.id")},
    "acquisition_runs": {
        ("acquisition_source_id", "acquisition_sources.id"),
        ("snapshot_id", "snapshots.id"),
    },
    "snapshots": {
        ("acquisition_source_id", "acquisition_sources.id"),
        ("first_acquisition_run_id", "acquisition_runs.id"),
    },
    "airports": set(),
    "runways": {("airport_id", "airports.id")},
    "publishing_sources": set(),
    "sources": set(),
    "installations": {
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("source_id", "sources.id"),
    },
    "incidents": {
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("source_id", "sources.id"),
    },
    "signals": {
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("source_id", "sources.id"),
        ("installation_id", "installations.id"),
    },
}

EXPECTED_INDEXES = {
    "acquisition_sources": {
        ("ix_acquisition_sources_publishing_source_id", ("publishing_source_id",), False),
    },
    "acquisition_runs": {
        ("ix_acquisition_runs_acquisition_source_id", ("acquisition_source_id",), False),
        ("ix_acquisition_runs_snapshot_id", ("snapshot_id",), False),
    },
    "snapshots": {
        ("ix_snapshots_acquisition_source_id", ("acquisition_source_id",), False),
        ("ix_snapshots_sha256", ("sha256",), False),
    },
    "airports": {
        ("ix_airports_country", ("country",), False),
        ("ix_airports_faa_code", ("faa_code",), False),
        ("ix_airports_iata_code", ("iata_code",), False),
        ("ix_airports_icao_code", ("icao_code",), False),
        ("ix_airports_name", ("name",), False),
    },
    "runways": {
        ("ix_runways_airport_id", ("airport_id",), False),
        ("ix_runways_designation", ("designation",), False),
    },
    "publishing_sources": set(),
    "sources": {
        ("ix_sources_source_type", ("source_type",), False),
        ("uq_sources_external_id", ("external_id",), True),
    },
    "installations": {
        ("ix_installations_airport_id", ("airport_id",), False),
        ("ix_installations_runway_id", ("runway_id",), False),
        ("ix_installations_source_id", ("source_id",), False),
        ("ix_installations_type", ("type",), False),
        ("ix_installations_install_year", ("install_year",), False),
        ("ix_installations_status", ("status",), False),
    },
    "incidents": {
        ("ix_incidents_airport_id", ("airport_id",), False),
        ("ix_incidents_runway_id", ("runway_id",), False),
        ("ix_incidents_source_id", ("source_id",), False),
        ("ix_incidents_emas_engaged", ("emas_engaged",), False),
        ("ix_incidents_incident_date", ("incident_date",), False),
    },
    "signals": {
        ("ix_signals_airport_id", ("airport_id",), False),
        ("ix_signals_runway_id", ("runway_id",), False),
        ("ix_signals_source_id", ("source_id",), False),
        ("ix_signals_title", ("title",), False),
        ("ix_signals_category", ("category",), False),
        ("ix_signals_confidence", ("confidence",), False),
        ("ix_signals_target_year", ("target_year",), False),
        ("ix_signals_status", ("status",), False),
        ("ix_signals_planning_year", ("planning_year",), False),
        ("ix_signals_procurement_year", ("procurement_year",), False),
        ("ix_signals_installation_id", ("installation_id",), False),
    },
}

DEFAULT_CASCADE = frozenset({"merge", "save-update"})
DELETE_ORPHAN_CASCADE = frozenset(
    {"delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"}
)

EXPECTED_RELATIONSHIPS = {
    "AcquisitionSource": {
        "publishing_source": ("PublishingSource", "acquisition_sources", DEFAULT_CASCADE),
        "runs": ("AcquisitionRun", "source", DEFAULT_CASCADE),
        "snapshots": ("Snapshot", "source", DEFAULT_CASCADE),
    },
    "AcquisitionRun": {
        "source": ("AcquisitionSource", "runs", DEFAULT_CASCADE),
        "snapshot": ("Snapshot", "runs", DEFAULT_CASCADE),
    },
    "Snapshot": {
        "source": ("AcquisitionSource", "snapshots", DEFAULT_CASCADE),
        "first_acquisition_run": ("AcquisitionRun", None, DEFAULT_CASCADE),
        "runs": ("AcquisitionRun", "snapshot", DEFAULT_CASCADE),
    },
    "Airport": {
        "runways": ("Runway", "airport", DELETE_ORPHAN_CASCADE),
        "signals": ("Signal", "airport", DELETE_ORPHAN_CASCADE),
        "installations": ("Installation", "airport", DELETE_ORPHAN_CASCADE),
        "incidents": ("Incident", "airport", DELETE_ORPHAN_CASCADE),
    },
    "Runway": {
        "airport": ("Airport", "runways", DEFAULT_CASCADE),
        "signals": ("Signal", "runway", DEFAULT_CASCADE),
        "installations": ("Installation", "runway", DEFAULT_CASCADE),
        "incidents": ("Incident", "runway", DEFAULT_CASCADE),
    },
    "Signal": {
        "airport": ("Airport", "signals", DEFAULT_CASCADE),
        "runway": ("Runway", "signals", DEFAULT_CASCADE),
        "source": ("Source", None, DEFAULT_CASCADE),
        "installation": ("Installation", None, DEFAULT_CASCADE),
    },
    "PublishingSource": {
        "acquisition_sources": ("AcquisitionSource", "publishing_source", DEFAULT_CASCADE),
    },
    "Installation": {
        "airport": ("Airport", "installations", DEFAULT_CASCADE),
        "runway": ("Runway", "installations", DEFAULT_CASCADE),
        "source": ("Source", None, DEFAULT_CASCADE),
    },
    "Source": {},
    "Incident": {
        "airport": ("Airport", "incidents", DEFAULT_CASCADE),
        "runway": ("Runway", "incidents", DEFAULT_CASCADE),
        "source": ("Source", None, DEFAULT_CASCADE),
    },
}


def _default_contract(default):
    if default is None:
        return None
    if default.is_callable:
        return ("callable",)
    if default.is_scalar:
        return ("scalar", default.arg)
    return ("sql", str(default.arg))


def test_all_current_models_are_exported_from_app_models():
    assert [
        Airport.__name__,
        AcquisitionRun.__name__,
        AcquisitionSource.__name__,
        Incident.__name__,
        Installation.__name__,
        PublishingSource.__name__,
        Runway.__name__,
        Signal.__name__,
        Source.__name__,
        Snapshot.__name__,
    ] == [
        "Airport",
        "AcquisitionRun",
        "AcquisitionSource",
        "Incident",
        "Installation",
        "PublishingSource",
        "Runway",
        "Signal",
        "Source",
        "Snapshot",
    ]


def test_configure_mappers_succeeds():
    configure_mappers()


def test_model_table_contract_is_unchanged():
    assert set(Base.metadata.tables) == set(EXPECTED_COLUMNS)

    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        actual_columns = {
            column.name: (
                str(column.type),
                column.nullable,
                _default_contract(column.default),
                _default_contract(column.onupdate),
            )
            for column in table.columns
        }
        actual_primary_key = tuple(column.name for column in table.primary_key.columns)
        actual_foreign_keys = {
            (column.name, foreign_key.target_fullname)
            for column in table.columns
            for foreign_key in column.foreign_keys
        }
        actual_indexes = {
            (index.name, tuple(column.name for column in index.columns), index.unique)
            for index in table.indexes
        }

        assert actual_columns == expected_columns
        assert actual_primary_key == EXPECTED_PRIMARY_KEYS[table_name]
        assert actual_foreign_keys == EXPECTED_FOREIGN_KEYS[table_name]
        assert actual_indexes == EXPECTED_INDEXES[table_name]


def test_model_relationship_contract_is_unchanged():
    configure_mappers()
    actual = {
        mapper.class_.__name__: {
            relationship.key: (
                relationship.mapper.class_.__name__,
                relationship.back_populates,
                frozenset(relationship.cascade),
            )
            for relationship in mapper.relationships
        }
        for mapper in Base.registry.mappers
    }

    assert actual == EXPECTED_RELATIONSHIPS


def test_current_metadata_creates_cleanly_in_isolated_sqlite():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            Base.metadata.create_all(connection)
            inspector = inspect(connection)

            assert set(inspector.get_table_names()) == set(EXPECTED_COLUMNS)
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
