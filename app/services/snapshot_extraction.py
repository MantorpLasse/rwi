"""Read-only DB boundary for Generic PDF Extraction (RWI Mission #12B
Part O).

Deliberately separate from app.extraction.generic_pdf's pure parsing
core: this module's only job is "given a Snapshot id, read the exact
preserved bytes and the provenance needed to build a document identity" -
one SELECT, never a write. `session.add`/`session.delete`/`session.commit`/
a write-intent `session.flush()` never appear anywhere in this file.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Snapshot


def build_document_identity(acquisition_source_key: str, snapshot_sha256: str) -> str:
    """The one, centralized construction path for extraction document
    identity (Mission #12B Part D): AcquisitionSource.key + Snapshot.sha256.
    Never derived from SearchResult/SearchQuery/airport seed/triage band/
    provider rank - those types are never even imported by this module."""
    return f"{acquisition_source_key}:{snapshot_sha256}"


@dataclass(frozen=True)
class SnapshotExtractionInput:
    """Everything app.extraction.generic_pdf.extract_pdf() needs, read
    once from the database and handed off as plain, immutable values -
    the parsing core itself never touches a Session."""

    payload: bytes
    media_type: str | None
    document_identity: str
    snapshot_id: int
    snapshot_sha256: str
    acquisition_source_key: str


def load_snapshot_for_extraction(session: Session, snapshot_id: int) -> SnapshotExtractionInput:
    """Read-only. Raises ValueError (never fabricates a weaker identity)
    if the Snapshot does not exist or its AcquisitionSource cannot be
    established - the latter should be unreachable in a healthy database
    (Snapshot.acquisition_source_id is a required, non-nullable FK) but
    is checked explicitly rather than assumed."""
    snapshot = session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"No Snapshot with id={snapshot_id!r} exists.")

    acquisition_source = snapshot.source
    if acquisition_source is None or not acquisition_source.key:
        raise ValueError(
            f"Snapshot id={snapshot_id!r} has no resolvable AcquisitionSource.key - "
            "refusing to fabricate a weaker document identity."
        )

    return SnapshotExtractionInput(
        payload=snapshot.payload,
        media_type=snapshot.media_type,
        document_identity=build_document_identity(acquisition_source.key, snapshot.sha256),
        snapshot_id=snapshot.id,
        snapshot_sha256=snapshot.sha256,
        acquisition_source_key=acquisition_source.key,
    )
