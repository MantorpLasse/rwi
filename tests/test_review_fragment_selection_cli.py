"""RWI Mission #14B Part P (CLI) - offline tests for
scripts/review_fragment_selection.py. Temp SQLite file DB, no network."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import scripts.review_fragment_selection as cli
from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot


def _build_minimal_pdf(text: str) -> bytes:
    content = b"BT /F1 12 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(out)


def _seed_db(db_path: str) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        publisher = PublishingSource(name="example.com", source_type=None, reliability_level="unverified")
        session.add(publisher)
        session.flush()
        source = AcquisitionSource(
            publishing_source=publisher, key="example:cli-test", display_name="x", acquisition_type="http",
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
        payload = _build_minimal_pdf("EMAS installed at Example Municipal Airport this year.")
        snapshot = Snapshot(
            source=source, first_acquisition_run=run, payload=payload, sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload), media_type="application/pdf", retrieved_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.commit()
        return snapshot.id


def test_no_keep_flag_creates_no_candidate_fragment(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    exit_code = cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No --keep given" in out
    assert "KEPT" not in out


def test_keep_flag_shows_candidate_fragment_summary(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    exit_code = cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id), "--keep", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1 fragment(s) KEPT" in out
    assert "airport_names (extracted): ['Example Municipal Airport']" in out


def test_disclaimer_always_printed(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id)])
    out = capsys.readouterr().out
    assert "not evidence accepted" in out


def test_cli_never_writes_to_database(tmp_path):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id), "--keep", "1"])

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 1
        assert session.scalar(select(func.count()).select_from(AcquisitionRun)) == 1


def test_invalid_keep_value_fails_cleanly(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    exit_code = cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id), "--keep", "not-a-number"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "must be comma-separated integers" in err
