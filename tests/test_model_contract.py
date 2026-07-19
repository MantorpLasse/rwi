from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import configure_mappers
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Airport,
    Document,
    EmasBed,
    EmasInstallation,
    Incident,
    Observation,
    ObservationType,
    Project,
    PublishingSource,
    Runway,
    RunwayEnd,
    Source,
)


EXPECTED_COLUMNS = {
    "airports": {
        "id": ("INTEGER", False, None, None),
        "iata_code": ("VARCHAR(3)", True, None, None),
        "icao_code": ("VARCHAR(4)", True, None, None),
        "faa_code": ("VARCHAR(5)", True, None, None),
        "name": ("VARCHAR(200)", False, None, None),
        "city": ("VARCHAR(100)", True, None, None),
        "state_region": ("VARCHAR(100)", True, None, None),
        "country": ("VARCHAR(100)", False, None, None),
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
    "runway_ends": {
        "id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", False, None, None),
        "designation": ("VARCHAR(20)", False, None, None),
        "heading": ("INTEGER", True, None, None),
        "resa_length_m": ("INTEGER", True, None, None),
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
    "documents": {
        "id": ("INTEGER", False, None, None),
        "source_id": ("INTEGER", False, None, None),
        "title": ("VARCHAR(300)", False, None, None),
        "document_type": ("VARCHAR(50)", True, None, None),
        "url": ("VARCHAR(1000)", True, None, None),
        "published_date": ("DATE", True, None, None),
        "accessed_date": ("DATE", True, None, None),
        "document_reference": ("VARCHAR(200)", True, None, None),
        "summary": ("TEXT", True, None, None),
        "revision": ("VARCHAR(100)", True, None, None),
        "content_hash": ("VARCHAR(128)", True, None, None),
        "status": ("VARCHAR(30)", False, ("scalar", "active"), None),
    },
    "project_documents": {
        "project_id": ("INTEGER", False, None, None),
        "document_id": ("INTEGER", False, None, None),
    },
    "observation_types": {
        "id": ("INTEGER", False, None, None),
        "key": ("VARCHAR(150)", False, None, None),
        "display_label": ("VARCHAR(200)", False, None, None),
        "description": ("TEXT", False, None, None),
        "value_type": ("VARCHAR(30)", False, None, None),
        "active": ("BOOLEAN", False, ("scalar", True), None),
        "created_at": ("DATETIME", False, ("callable",), None),
    },
    "observations": {
        "id": ("INTEGER", False, None, None),
        "document_id": ("INTEGER", False, None, None),
        "observation_type_id": ("INTEGER", False, None, None),
        "raw_value": ("TEXT", False, None, None),
        "normalized_value": ("TEXT", True, None, None),
        "extraction_confidence": ("FLOAT", True, None, None),
        "evidence_locator": ("TEXT", True, None, None),
        "extraction_method": ("VARCHAR(50)", True, None, None),
        "extractor_version": ("VARCHAR(100)", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "supersedes_observation_id": ("INTEGER", True, None, None),
    },
    "emas_beds": {
        "id": ("INTEGER", False, None, None),
        "runway_end_id": ("INTEGER", False, None, None),
        "manufacturer": ("VARCHAR(150)", True, None, None),
        "product_name": ("VARCHAR(100)", True, None, None),
        "installation_year": ("INTEGER", True, None, None),
        "replacement_year": ("INTEGER", True, None, None),
        "status": ("VARCHAR(30)", False, None, None),
        "length_m": ("FLOAT", True, None, None),
        "width_m": ("FLOAT", True, None, None),
        "faa_accepted": ("BOOLEAN", True, None, None),
        "notes": ("TEXT", True, None, None),
        "is_current": ("BOOLEAN", False, ("scalar", True), None),
    },
    "projects": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "title": ("VARCHAR(250)", False, None, None),
        "project_type": ("VARCHAR(50)", False, None, None),
        "status": ("VARCHAR(50)", False, None, None),
        "confidence_level": ("VARCHAR(30)", False, None, None),
        "planning_year": ("INTEGER", True, None, None),
        "procurement_year": ("INTEGER", True, None, None),
        "construction_start": ("DATE", True, None, None),
        "completion_date": ("DATE", True, None, None),
        "estimated_total_value_usd": ("NUMERIC(14, 2)", True, None, None),
        "estimated_emas_value_usd": ("NUMERIC(14, 2)", True, None, None),
        "probability_score": ("FLOAT", False, ("scalar", 5.0), None),
        "supplier": ("VARCHAR(150)", True, None, None),
        "likely_supplier": ("VARCHAR(150)", True, None, None),
        "supplier_reason": ("TEXT", True, None, None),
        "description": ("TEXT", True, None, None),
        "last_verified_at": ("DATE", True, None, None),
    },
    "emas_installations": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "runway_end": ("VARCHAR(20)", True, None, None),
        "manufacturer": ("VARCHAR(150)", True, None, None),
        "product_name": ("VARCHAR(100)", True, None, None),
        "installation_year": ("INTEGER", True, None, None),
        "replacement_year": ("INTEGER", True, None, None),
        "status": ("VARCHAR(30)", False, None, None),
        "length_m": ("FLOAT", True, None, None),
        "width_m": ("FLOAT", True, None, None),
        "faa_accepted": ("BOOLEAN", True, None, None),
        "notes": ("TEXT", True, None, None),
    },
    "sources": {
        "id": ("INTEGER", False, None, None),
        "project_id": ("INTEGER", False, None, None),
        "title": ("VARCHAR(300)", False, None, None),
        "source_type": ("VARCHAR(50)", False, None, None),
        "publisher": ("VARCHAR(200)", True, None, None),
        "url": ("VARCHAR(1000)", False, None, None),
        "published_date": ("DATE", True, None, None),
        "accessed_date": ("DATE", True, None, None),
        "document_reference": ("VARCHAR(200)", True, None, None),
        "page_number": ("VARCHAR(30)", True, None, None),
        "summary": ("TEXT", True, None, None),
        "reliability_level": ("VARCHAR(30)", False, ("scalar", "official"), None),
    },
    "incidents": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
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
    },
}

EXPECTED_PRIMARY_KEYS = {table_name: ("id",) for table_name in EXPECTED_COLUMNS}
EXPECTED_PRIMARY_KEYS["project_documents"] = ("project_id", "document_id")

EXPECTED_FOREIGN_KEYS = {
    "airports": set(),
    "runways": {("airport_id", "airports.id")},
    "runway_ends": {("runway_id", "runways.id")},
    "publishing_sources": set(),
    "documents": {("source_id", "publishing_sources.id")},
    "project_documents": {
        ("project_id", "projects.id"),
        ("document_id", "documents.id"),
    },
    "observation_types": set(),
    "observations": {
        ("document_id", "documents.id"),
        ("observation_type_id", "observation_types.id"),
        ("supersedes_observation_id", "observations.id"),
    },
    "emas_beds": {("runway_end_id", "runway_ends.id")},
    "projects": {("airport_id", "airports.id"), ("runway_id", "runways.id")},
    "emas_installations": {("airport_id", "airports.id"), ("runway_id", "runways.id")},
    "sources": {("project_id", "projects.id")},
    "incidents": {("airport_id", "airports.id"), ("runway_id", "runways.id")},
}

EXPECTED_INDEXES = {
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
    "runway_ends": {
        ("ix_runway_ends_runway_id", ("runway_id",), False),
    },
    "publishing_sources": set(),
    "documents": {
        ("ix_documents_source_id", ("source_id",), False),
    },
    "project_documents": set(),
    "observation_types": set(),
    "observations": {
        ("ix_observations_document_id", ("document_id",), False),
        ("ix_observations_observation_type_id", ("observation_type_id",), False),
        ("ix_observations_supersedes_observation_id", ("supersedes_observation_id",), False),
    },
    "emas_beds": {
        ("ix_emas_beds_installation_year", ("installation_year",), False),
        ("ix_emas_beds_runway_end_id", ("runway_end_id",), False),
        ("ix_emas_beds_status", ("status",), False),
        ("uq_emas_beds_current_runway_end", ("runway_end_id",), True),
    },
    "projects": {
        ("ix_projects_airport_id", ("airport_id",), False),
        ("ix_projects_confidence_level", ("confidence_level",), False),
        ("ix_projects_planning_year", ("planning_year",), False),
        ("ix_projects_probability_score", ("probability_score",), False),
        ("ix_projects_procurement_year", ("procurement_year",), False),
        ("ix_projects_project_type", ("project_type",), False),
        ("ix_projects_runway_id", ("runway_id",), False),
        ("ix_projects_status", ("status",), False),
        ("ix_projects_title", ("title",), False),
    },
    "emas_installations": {
        ("ix_emas_installations_airport_id", ("airport_id",), False),
        ("ix_emas_installations_installation_year", ("installation_year",), False),
        ("ix_emas_installations_runway_id", ("runway_id",), False),
        ("ix_emas_installations_status", ("status",), False),
    },
    "sources": {
        ("ix_sources_project_id", ("project_id",), False),
        ("ix_sources_source_type", ("source_type",), False),
    },
    "incidents": {
        ("ix_incidents_airport_id", ("airport_id",), False),
        ("ix_incidents_emas_engaged", ("emas_engaged",), False),
        ("ix_incidents_incident_date", ("incident_date",), False),
        ("ix_incidents_runway_id", ("runway_id",), False),
    },
}

DEFAULT_CASCADE = frozenset({"merge", "save-update"})
DELETE_ORPHAN_CASCADE = frozenset(
    {"delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"}
)

EXPECTED_RELATIONSHIPS = {
    "Airport": {
        "runways": ("Runway", "airport", DELETE_ORPHAN_CASCADE),
        "projects": ("Project", "airport", DELETE_ORPHAN_CASCADE),
        "installations": ("EmasInstallation", "airport", DELETE_ORPHAN_CASCADE),
        "incidents": ("Incident", "airport", DELETE_ORPHAN_CASCADE),
    },
    "Runway": {
        "airport": ("Airport", "runways", DEFAULT_CASCADE),
        "runway_ends": ("RunwayEnd", "runway", DEFAULT_CASCADE),
        "projects": ("Project", "runway", DEFAULT_CASCADE),
        "installations": ("EmasInstallation", "runway", DEFAULT_CASCADE),
        "incidents": ("Incident", "runway", DEFAULT_CASCADE),
    },
    "RunwayEnd": {
        "runway": ("Runway", "runway_ends", DEFAULT_CASCADE),
        "emas_beds": ("EmasBed", "runway_end", DEFAULT_CASCADE),
    },
    "EmasBed": {"runway_end": ("RunwayEnd", "emas_beds", DEFAULT_CASCADE)},
    "Project": {
        "airport": ("Airport", "projects", DEFAULT_CASCADE),
        "runway": ("Runway", "projects", DEFAULT_CASCADE),
        "sources": ("Source", "project", DELETE_ORPHAN_CASCADE),
        "documents": ("Document", "projects", DEFAULT_CASCADE),
    },
    "PublishingSource": {
        "documents": ("Document", "source", DEFAULT_CASCADE),
    },
    "Document": {
        "source": ("PublishingSource", "documents", DEFAULT_CASCADE),
        "projects": ("Project", "documents", DEFAULT_CASCADE),
        "observations": ("Observation", "document", DEFAULT_CASCADE),
    },
    "EmasInstallation": {
        "airport": ("Airport", "installations", DEFAULT_CASCADE),
        "runway": ("Runway", "installations", DEFAULT_CASCADE),
    },
    "Source": {"project": ("Project", "sources", DEFAULT_CASCADE)},
    "Incident": {
        "airport": ("Airport", "incidents", DEFAULT_CASCADE),
        "runway": ("Runway", "incidents", DEFAULT_CASCADE),
    },
    "ObservationType": {
        "observations": ("Observation", "observation_type", DEFAULT_CASCADE),
    },
    "Observation": {
        "document": ("Document", "observations", DEFAULT_CASCADE),
        "observation_type": ("ObservationType", "observations", DEFAULT_CASCADE),
        "supersedes": ("Observation", "superseded_by", DEFAULT_CASCADE),
        "superseded_by": ("Observation", "supersedes", DEFAULT_CASCADE),
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
        Document.__name__,
        EmasBed.__name__,
        Runway.__name__,
        RunwayEnd.__name__,
        Project.__name__,
        PublishingSource.__name__,
        EmasInstallation.__name__,
        Source.__name__,
        Incident.__name__,
        Observation.__name__,
        ObservationType.__name__,
    ] == [
        "Airport",
        "Document",
        "EmasBed",
        "Runway",
        "RunwayEnd",
        "Project",
        "PublishingSource",
        "EmasInstallation",
        "Source",
        "Incident",
        "Observation",
        "ObservationType",
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
