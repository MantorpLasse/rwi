"""RWI Mission #15B Part E - offline tests for
app.services.selection_source_metadata.build_discovery_source_metadata_for_snapshot.
Temp SQLite file DB, no network."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot
from app.services.selection_source_metadata import build_discovery_source_metadata_for_snapshot
from app.services.snapshot_extraction import build_document_identity


def _seed(db_path: str, *, retrieved_at: datetime | None = None) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        publisher = PublishingSource(
            name="Metropolitan Airports Commission", source_type="government", reliability_level="official",
        )
        session.add(publisher)
        session.flush()
        source = AcquisitionSource(
            publishing_source=publisher, key="example:metadata-test", display_name="EMAS Procurement Memo",
            acquisition_type="http", canonical_url="https://example.com/doc.pdf", active=True,
        )
        session.add(source)
        session.flush()
        run = AcquisitionRun(
            source=source, started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
            status=AcquisitionRunStatus.SUCCESS, request_url=source.canonical_url, provider_version="test/1",
            duration_seconds=0.1,
        )
        session.add(run)
        session.flush()
        payload = b"%PDF-1.4 minimal"
        snapshot = Snapshot(
            source=source, first_acquisition_run=run, payload=payload, sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload), media_type="application/pdf",
            retrieved_at=retrieved_at or datetime.now(UTC),
        )
        session.add(snapshot)
        session.commit()
        return snapshot.id


def test_document_identity_matches_canonical_builder(tmp_path):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        snapshot = session.get(Snapshot, snapshot_id)
        metadata = build_discovery_source_metadata_for_snapshot(session, snapshot_id)
        assert metadata.document_identity == build_document_identity("example:metadata-test", snapshot.sha256)


def test_field_mapping_from_acquisition_and_publishing_source(tmp_path):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        metadata = build_discovery_source_metadata_for_snapshot(session, snapshot_id)
        assert metadata.title == "EMAS Procurement Memo"
        assert metadata.url == "https://example.com/doc.pdf"
        assert metadata.publisher == "Metropolitan Airports Commission"
        assert metadata.reliability_level == "official"
        assert metadata.source_type == "web_discovery"


def test_published_date_never_inferred_from_retrieved_at(tmp_path):
    """Mission #15A Part E / #15B Part E: Snapshot.retrieved_at is a fetch
    timestamp, never a publication date - must never leak into
    published_date even though a real, non-null retrieved_at exists."""
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed(db_path, retrieved_at=datetime(2024, 3, 15, tzinfo=UTC))
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        metadata = build_discovery_source_metadata_for_snapshot(session, snapshot_id)
        assert metadata.published_date is None


def test_missing_snapshot_raises_value_error(tmp_path):
    db_path = str(tmp_path / "test.db")
    _seed(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        with pytest.raises(ValueError):
            build_discovery_source_metadata_for_snapshot(session, 9999)
