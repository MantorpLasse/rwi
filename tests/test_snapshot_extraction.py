"""RWI Mission #12B Part S (Provenance) - offline tests for
app.services.snapshot_extraction. Synthetic in-memory SQLite DB, matching
this repo's established test convention. No network."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot
from app.services.snapshot_extraction import build_document_identity, load_snapshot_for_extraction


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_snapshot(session: Session, *, payload: bytes = b"%PDF-1.4 fake", media_type: str = "application/pdf"):
    publisher = PublishingSource(name="Example", source_type=None, reliability_level="unverified")
    session.add(publisher)
    session.flush()
    source = AcquisitionSource(
        publishing_source=publisher, key="example:abc123", display_name="x", acquisition_type="http",
        canonical_url="https://example.com/doc.pdf", active=True,
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
    import hashlib

    snapshot = Snapshot(
        source=source, first_acquisition_run=run, payload=payload, sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload), media_type=media_type, retrieved_at=datetime.now(UTC),
    )
    session.add(snapshot)
    session.commit()
    return snapshot, source


def test_build_document_identity_is_deterministic_composite():
    assert build_document_identity("key123", "sha456") == "key123:sha456"


def test_load_snapshot_returns_exact_payload_and_identity():
    engine = _engine()
    with Session(engine) as session:
        snapshot, source = _seed_snapshot(session)
        loaded = load_snapshot_for_extraction(session, snapshot.id)
        assert loaded.payload == snapshot.payload
        assert loaded.media_type == "application/pdf"
        assert loaded.document_identity == f"{source.key}:{snapshot.sha256}"
        assert loaded.snapshot_id == snapshot.id
        assert loaded.snapshot_sha256 == snapshot.sha256
        assert loaded.acquisition_source_key == source.key


def test_missing_snapshot_raises_value_error_not_fabricated_identity():
    engine = _engine()
    with Session(engine) as session:
        with pytest.raises(ValueError):
            load_snapshot_for_extraction(session, 999999)


def test_document_identity_never_derived_from_search_or_triage_concepts():
    """Structural proof: the loader module never imports anything from
    app.discovery at all."""
    import ast

    tree = ast.parse(open("app/services/snapshot_extraction.py", encoding="utf-8").read())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert not any(name.startswith("app.discovery") for name in imports)


def test_loader_never_writes(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    with Session(engine) as session:
        snapshot, _ = _seed_snapshot(session)

        def fail_add(*_a, **_k):
            raise AssertionError("load_snapshot_for_extraction must never call session.add()")

        def fail_commit(*_a, **_k):
            raise AssertionError("load_snapshot_for_extraction must never call session.commit()")

        monkeypatch.setattr(session, "add", fail_add)
        monkeypatch.setattr(session, "commit", fail_commit)
        load_snapshot_for_extraction(session, snapshot.id)  # must not raise via the monkeypatched guards above
