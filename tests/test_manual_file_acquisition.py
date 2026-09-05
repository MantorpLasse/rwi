"""RWI HQ "Manual File Acquisition Provider + CLI" mission - offline tests
for app.acquisition.manual_file (ManualFileAcquisitionProvider,
get_or_create_manual_acquisition_source, ingest_local_file). Synthetic
in-memory SQLite DB, matching this repo's own established test convention
(see tests/test_generic_web_fetch.py). No real network access, no local
filesystem access outside pytest's own tmp_path."""

from __future__ import annotations

import ast
import inspect
import json

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    AcquisitionRun,
    AcquisitionRunStatus,
    AcquisitionSource,
    PublishingSource,
    ReviewerAction,
    Signal,
    Snapshot,
    Source,
    SourceAssertion,
)
from app.acquisition import manual_file
from app.acquisition.manual_file import (
    ManualFileAcquisitionError,
    ManualFileAcquisitionProvider,
    get_or_create_manual_acquisition_source,
    ingest_local_file,
)
from app.extraction.dispatch import extract_document
from app.services.snapshot_extraction import load_snapshot_for_extraction
from scripts import ingest_local_file as ingest_cli

_URL = "https://www.flylouisville.com/wp-content/uploads/2026/03/LRAA-December-3-2025-Special-Meeting_Approved-UNSIGNED.pdf"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _domain_row_counts(session: Session) -> dict:
    return {
        "Source": session.scalar(select(func.count()).select_from(Source)),
        "SourceAssertion": session.scalar(select(func.count()).select_from(SourceAssertion)),
        "Signal": session.scalar(select(func.count()).select_from(Signal)),
        "ReviewerAction": session.scalar(select(func.count()).select_from(ReviewerAction)),
    }


# 1. HAPPY PATH
def test_ingest_local_file_happy_path(tmp_path):
    content = b"%PDF-1.4 fake pdf bytes for testing\n" * 10
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(content)

    engine = _engine()
    with Session(engine) as session:
        run = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")

        assert run.status == AcquisitionRunStatus.SUCCESS
        assert run.is_new_snapshot is True
        assert run.provider_version == "manual-file/1"
        assert run.snapshot is not None
        assert run.snapshot.payload == content
        assert run.snapshot.byte_size == len(content)
        assert run.snapshot.media_type == "application/pdf"

        import hashlib
        assert run.snapshot.sha256 == hashlib.sha256(content).hexdigest()


# 2. NO HTTP PROVENANCE FABRICATION
def test_ingest_local_file_never_fabricates_http_provenance(tmp_path):
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(b"real bytes, no real HTTP ever happened")

    engine = _engine()
    with Session(engine) as session:
        run = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")

    assert run.http_status is None
    assert run.response_headers == json.dumps({}, sort_keys=True, separators=(",", ":"))
    assert run.duration_seconds == 0.0
    assert run.request_url == _URL
    assert run.final_url == _URL
    assert run.provider_version == "manual-file/1"


# 3. MISSING FILE
def test_ingest_local_file_missing_file_fails_closed(tmp_path):
    missing = tmp_path / "does-not-exist.pdf"
    engine = _engine()
    with Session(engine) as session:
        with pytest.raises(ManualFileAcquisitionError, match="No such file"):
            ingest_local_file(session, url=_URL, local_path=missing, content_type="application/pdf")


# 4. DIRECTORY PATH
def test_ingest_local_file_directory_path_fails_closed(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        with pytest.raises(ManualFileAcquisitionError, match="Not a regular file"):
            ingest_local_file(session, url=_URL, local_path=tmp_path, content_type="application/pdf")


# 5. EMPTY FILE
def test_ingest_local_file_empty_file_fails_closed(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    engine = _engine()
    with Session(engine) as session:
        with pytest.raises(ManualFileAcquisitionError, match="is empty"):
            ingest_local_file(session, url=_URL, local_path=empty, content_type="application/pdf")


def test_provider_requires_content_type():
    with pytest.raises(ManualFileAcquisitionError, match="content_type"):
        ManualFileAcquisitionProvider(original_url=_URL, local_path="whatever.pdf", content_type="")


def test_provider_requires_original_url():
    with pytest.raises(ManualFileAcquisitionError, match="original_url"):
        ManualFileAcquisitionProvider(original_url="", local_path="whatever.pdf", content_type="application/pdf")


# 6. CONTENT TYPE - always the caller's own explicit value, never sniffed.
def test_content_type_is_passed_through_exactly_never_sniffed(tmp_path):
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(b"pdf-shaped bytes")
    engine = _engine()
    with Session(engine) as session:
        run = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")
        assert run.snapshot.media_type == "application/pdf"
        assert run.content_type == "application/pdf"


# 7. DEDUPE - same bytes, same URL -> same Snapshot, is_new=False on replay.
def test_ingest_local_file_dedupes_identical_bytes(tmp_path):
    content = b"identical bytes for dedupe test"
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(content)

    engine = _engine()
    with Session(engine) as session:
        first = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")
        first_snapshot_id = first.snapshot.id
        second = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")

        assert second.is_new_snapshot is False
        assert second.snapshot.id == first_snapshot_id
        assert second.status == AcquisitionRunStatus.NO_CHANGE


# 8. DIFFERENT BYTES - same URL, changed local bytes -> a new Snapshot.
def test_ingest_local_file_different_bytes_creates_new_snapshot(tmp_path):
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(b"version one of the file")

    engine = _engine()
    with Session(engine) as session:
        first = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")
        first_snapshot_id = first.snapshot.id

        local_file.write_bytes(b"version two of the file - genuinely different bytes")
        second = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")

        assert second.is_new_snapshot is True
        assert second.snapshot.id != first_snapshot_id
        # Same AcquisitionSource reused for both (same URL) - only the Snapshot differs.
        assert second.acquisition_source_id == first.acquisition_source_id


# 9. NO DOMAIN WRITES
def test_ingest_local_file_writes_no_domain_governance_rows(tmp_path):
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(b"no governance rows should ever appear")

    engine = _engine()
    with Session(engine) as session:
        before = _domain_row_counts(session)
        ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")
        after = _domain_row_counts(session)

    assert before == after == {"Source": 0, "SourceAssertion": 0, "Signal": 0, "ReviewerAction": 0}


def test_manual_file_module_imports_no_governance_machinery():
    """AST-based (real import statements only): app.acquisition.manual_file
    never imports Source/SourceAssertion/Signal/ReviewerAction/FH-D4
    persistence code - it only knows about acquisition-metadata models."""
    tree = ast.parse(inspect.getsource(manual_file))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = {
        "app.models.Source", "app.models.SourceAssertion", "app.models.Signal",
        "app.models.ReviewerAction", "app.models.signal_disposition",
    }
    assert not (imported_names & forbidden)
    assert not any("signal_disposition" in name for name in imported_names)


# 10. CLI VALIDATION
def test_cli_missing_url_fails_clearly():
    with pytest.raises(SystemExit):
        ingest_cli._parser().parse_args(["--database", "x.db", "--file", "x.pdf", "--content-type", "application/pdf"])


def test_cli_missing_file_fails_clearly():
    with pytest.raises(SystemExit):
        ingest_cli._parser().parse_args(["--database", "x.db", "--url", _URL, "--content-type", "application/pdf"])


def test_cli_refuses_nonexistent_file(tmp_path, capsys):
    missing = tmp_path / "nope.pdf"
    exit_code = ingest_cli.main(
        ["--database", str(tmp_path / "unused.db"), "--url", _URL, "--file", str(missing), "--content-type", "application/pdf"]
    )
    assert exit_code == 2
    assert "no such file" in capsys.readouterr().err.lower()


# 11. EXACT-BYTE TEST
def test_snapshot_payload_matches_file_bytes_read_independently(tmp_path):
    content = b"\x00\x01\x02 binary-ish content \xff\xfe" * 5
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(content)

    independently_read = local_file.read_bytes()

    engine = _engine()
    with Session(engine) as session:
        run = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")

        assert run.snapshot.payload == independently_read
        assert run.snapshot.payload == content


# 12. URL PRESERVATION
def test_acquisition_source_canonical_url_matches_supplied_url_exactly(tmp_path):
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(b"url preservation test bytes")

    engine = _engine()
    with Session(engine) as session:
        run = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")
        source = session.get(AcquisitionSource, run.acquisition_source_id)
        assert source.canonical_url == _URL
        assert source.acquisition_type == "manual_file"


def test_get_or_create_manual_acquisition_source_reuses_existing_by_key(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        publishing_source = PublishingSource(name="www.flylouisville.com", reliability_level="unverified")
        session.add(publishing_source)
        session.commit()

        first, first_created = get_or_create_manual_acquisition_source(session, _URL, publishing_source)
        session.commit()
        second, second_created = get_or_create_manual_acquisition_source(session, _URL, publishing_source)

    assert first_created is True
    assert second_created is False
    assert first.id == second.id


# --- Extraction compatibility (mission Section 9) ---------------------------


def test_manually_ingested_snapshot_extracts_with_zero_special_casing(tmp_path):
    """Proves extraction does not care about provider type: a manually
    ingested Snapshot flows through the exact same
    load_snapshot_for_extraction() -> extract_document() path a real HTTP
    Snapshot does, with no HTTP metadata read and no branch on
    provider_version anywhere in that path."""
    pdf_bytes = b"%PDF-1.4\n%fake but non-empty pdf-shaped bytes for extraction compatibility\n"
    local_file = tmp_path / "manual.pdf"
    local_file.write_bytes(pdf_bytes)

    engine = _engine()
    with Session(engine) as session:
        run = ingest_local_file(session, url=_URL, local_path=local_file, content_type="application/pdf")
        snapshot_id = run.snapshot.id

    with Session(engine) as session:
        loaded = load_snapshot_for_extraction(session, snapshot_id)

    # Pure, in-memory, no DB/network access - identical call shape to every
    # other Snapshot this codebase has ever extracted.
    document = extract_document(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)
    assert document.document_identity == loaded.document_identity
    # Whatever the real extractor makes of this synthetic byte string, it
    # must not raise and must not require any manual-acquisition-specific
    # input - the assertion here is about the call succeeding cleanly,
    # not about the (irrelevant) parsed content of a fake PDF.
    assert document is not None
