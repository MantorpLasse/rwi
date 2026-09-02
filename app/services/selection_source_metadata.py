"""Selection/Snapshot -> DiscoverySourceMetadata adapter (Mission #15B Part
E, following Mission #15A Part H/Section 7's findings).

Builds the Source-creation metadata for a Selection-produced
CandidateFragment from the same preserved AcquisitionSource/
PublishingSource/Snapshot records Mission #12B's extraction layer already
reads (app.services.snapshot_extraction.load_snapshot_for_extraction).
Read-only: performs no write, no inference of missing fields, and no
validation against network/live state.

document_identity reuses Mission #12B's own canonical constructor
(build_document_identity) unmodified - never a second, parallel
construction of the same value, so a CandidateFragment's
artifact_identity and this adapter's Source.external_id are always
derived identically from the same (AcquisitionSource.key, Snapshot.sha256)
pair.

published_date is ALWAYS None: Snapshot.retrieved_at is a fetch
timestamp (when RWI acquired the document), never the document's own
publication date - substituting one for the other would be exactly the
"unsafe to infer" case Mission #15A Part E warned against. A real
publication date, if ever durably known, must come from a separate,
explicit extraction step (CandidateFragment.publication_date, currently
never populated by app.selection.structured_extraction - see that
module's own scope note) - never fabricated here.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Snapshot
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata
from app.services.snapshot_extraction import build_document_identity

__all__ = ["build_discovery_source_metadata_for_snapshot"]


def build_discovery_source_metadata_for_snapshot(session: Session, snapshot_id: int) -> DiscoverySourceMetadata:
    """Read-only. Raises ValueError (never fabricates a weaker identity),
    matching load_snapshot_for_extraction's own convention exactly, if the
    Snapshot does not exist or its AcquisitionSource cannot be resolved."""
    snapshot = session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"No Snapshot with id={snapshot_id!r} exists.")

    acquisition_source = snapshot.source
    if acquisition_source is None or not acquisition_source.key:
        raise ValueError(
            f"Snapshot id={snapshot_id!r} has no resolvable AcquisitionSource.key - "
            "refusing to fabricate a weaker document identity."
        )

    publishing_source = acquisition_source.publishing_source

    return DiscoverySourceMetadata(
        document_identity=build_document_identity(acquisition_source.key, snapshot.sha256),
        title=acquisition_source.display_name,
        source_type="web_discovery",
        publisher=publishing_source.name if publishing_source is not None else None,
        url=acquisition_source.canonical_url,
        published_date=None,  # never inferred from retrieved_at or anything else - see module docstring.
        reliability_level=publishing_source.reliability_level if publishing_source is not None else "unverified",
    )
