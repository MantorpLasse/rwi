"""RWI Mission #12B Part S (CLI) - offline tests for
scripts/extract_snapshot_text.py. Temp SQLite file DB (matches this
repo's existing script-CLI test convention), no network."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.extract_snapshot_text as cli
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
        payload = _build_minimal_pdf("hello from page one")
        snapshot = Snapshot(
            source=source, first_acquisition_run=run, payload=payload, sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload), media_type="application/pdf", retrieved_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.commit()
        return snapshot.id


def test_parser_requires_snapshot_id_and_database():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["--database", "x.db"])


def test_summary_output_contains_disclaimer_and_no_raw_dump_by_default(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    exit_code = cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "not evidence or a verified claim" in out
    assert "Status: SUCCESS" in out
    assert "hello from page one" not in out  # no page text dumped without --page


def test_page_flag_prints_requested_page_text(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    exit_code = cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id), "--page", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "hello from page one" in out


def test_out_of_range_page_fails_cleanly(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    exit_code = cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id), "--page", "99"])
    err = capsys.readouterr().err
    assert exit_code == 3
    assert "not extracted" in err


def test_missing_snapshot_fails_cleanly_no_traceback(tmp_path, capsys: pytest.CaptureFixture):
    db_path = str(tmp_path / "test.db")
    Base.metadata.create_all(create_engine(f"sqlite:///{db_path}"))
    exit_code = cli.main(["--database", db_path, "--snapshot-id", "999999"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "Could not load Snapshot" in err


def test_cli_never_writes_to_database(tmp_path):
    db_path = str(tmp_path / "test.db")
    snapshot_id = _seed_db(db_path)
    cli.main(["--database", db_path, "--snapshot-id", str(snapshot_id)])

    from sqlalchemy import select, func

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 1
        assert session.scalar(select(func.count()).select_from(AcquisitionRun)) == 1
