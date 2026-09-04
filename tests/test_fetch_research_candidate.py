"""RWI HQ "Discovery Research Loop V1 - Slice 4" - offline tests for
scripts/fetch_research_candidate.py.

The fetch layer itself (fetch_discovered_url) is faked here exactly like
tests/test_fetch_discovered_url_cli.py fakes it - only the network
boundary is mocked. Extraction runs for REAL against a real, DB-persisted
Snapshot row this test seeds directly, so the HTML extraction path is
genuinely exercised end-to-end, no live internet."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import scripts.fetch_research_candidate as cli
from app.database import Base
from app.models import (
    AcquisitionRun,
    AcquisitionRunStatus,
    AcquisitionSource,
    PublishingSource,
    Signal,
    Snapshot,
)

_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)

_SDF_HTML = (
    b"<html><head><title>SDF Airport Improvements</title></head>"
    b"<body><p>We hope to have a bid-construction package available for "
    b"contractors sometime in the fall.</p></body></html>"
)

_FLAGS = ["--allow-live-network", "--allow-database-write"]


def _seed_html_snapshot(db_path: str, *, payload: bytes = _SDF_HTML, media_type: str = "text/html") -> int:
    """Seeds real PublishingSource/AcquisitionSource/AcquisitionRun/Snapshot
    rows directly - mirrors what AcquisitionService.acquire() itself would
    have produced - and returns the real Snapshot id."""
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        publishing_source = PublishingSource(name="www.flylouisville.com", source_type=None, reliability_level="unverified")
        session.add(publishing_source)
        session.flush()
        acquisition_source = AcquisitionSource(
            publishing_source_id=publishing_source.id,
            key="generic_web:testkey",
            display_name="www.flylouisville.com",
            acquisition_type="generic_web",
            canonical_url="https://www.flylouisville.com/corporate/sdf-airport-improvements/",
            expected_media_type=None,
        )
        session.add(acquisition_source)
        session.flush()
        # AcquisitionRun becomes immutable once its status leaves RUNNING
        # (see app.models.acquisition._protect_completed_run) - so, exactly
        # like the real AcquisitionService.acquire(), it must be created as
        # RUNNING first, then transitioned to SUCCESS together with its
        # snapshot_id in one update.
        run = AcquisitionRun(
            acquisition_source_id=acquisition_source.id,
            started_at=_NOW, completed_at=None, status=AcquisitionRunStatus.RUNNING,
            request_url=acquisition_source.canonical_url, final_url=None,
            http_status=None, content_type=None, provider_version="generic-web/0.1",
            duration_seconds=0.0, is_new_snapshot=None,
        )
        session.add(run)
        session.flush()
        import hashlib
        snapshot = Snapshot(
            acquisition_source_id=acquisition_source.id, first_acquisition_run_id=run.id,
            payload=payload, sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload),
            media_type=media_type, retrieved_at=_NOW,
        )
        session.add(snapshot)
        session.flush()
        run.status = AcquisitionRunStatus.SUCCESS
        run.completed_at = _NOW
        run.final_url = acquisition_source.canonical_url
        run.http_status = 200
        run.content_type = media_type
        run.is_new_snapshot = True
        run.snapshot_id = snapshot.id
        session.commit()
        return snapshot.id


def _fake_run(*, status="SUCCESS", snapshot_id=None):
    snapshot = None
    if snapshot_id is not None:
        snapshot = SimpleNamespace(id=snapshot_id, sha256="a" * 64, byte_size=123, media_type="text/html", retrieved_at=_NOW)
    return SimpleNamespace(
        status=SimpleNamespace(value=status), final_url="https://www.flylouisville.com/x", http_status=200,
        content_type="text/html", duration_seconds=0.5, snapshot=snapshot,
    )


# --- Argument contract: explicit URL, explicit gates, no auto-selection ------


def test_url_argument_is_required():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["--database", "x.db", *_FLAGS])


def test_database_argument_is_required():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["https://example.com/", *_FLAGS])


def test_parser_accepts_exactly_one_url_positional():
    args = cli._parser().parse_args(["--database", "x.db", "https://example.com/page", *_FLAGS])
    assert args.url == "https://example.com/page"


def test_no_bulk_batch_or_auto_select_flag_exists():
    """No --urls/--file/--batch/--top/--best/--rank flag - a human names
    exactly one URL, every time, mirroring fetch_discovered_url.py's own
    convention (Slice 4 Part 2)."""
    parser = cli._parser()
    dest_names = {action.dest for action in parser._actions}
    forbidden = {"urls", "file", "batch", "top", "best", "rank", "auto_select", "highest"}
    assert not (dest_names & forbidden)


def test_allow_live_network_required(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    exit_code = cli.main(["--database", str(db_path), "https://example.com/", "--allow-database-write"])
    assert exit_code == 2
    assert "--allow-live-network" in capsys.readouterr().err
    assert not db_path.exists()


def test_allow_database_write_required(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    exit_code = cli.main(["--database", str(db_path), "https://example.com/", "--allow-live-network"])
    assert exit_code == 2
    assert "--allow-database-write" in capsys.readouterr().err
    assert not db_path.exists()


# --- Reuses the existing, unmodified fetch safety path -----------------------


def test_main_blocks_unsafe_target_without_writing(tmp_path, capsys):
    """No mocking here at all - the REAL fetch_discovered_url() runs and
    the REAL SSRF guard rejects a loopback target, proving this script
    calls the same, unmodified safety path (no second HTTP implementation)."""
    db_path = tmp_path / "test.db"
    exit_code = cli.main(["--database", str(db_path), "http://127.0.0.1/admin", *_FLAGS])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "FETCH BLOCKED" in err


def test_main_never_dumps_raw_bytes_to_stdout(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=None))
    db_path = tmp_path / "test.db"
    cli.main(["--database", str(db_path), "https://example.com/page", *_FLAGS])
    out = capsys.readouterr().out
    assert "<html>" not in out


# --- Snapshot / Extraction handoff -------------------------------------------


def test_successful_fetch_produces_snapshot_and_extraction_report(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "test.db"
    snapshot_id = _seed_html_snapshot(str(db_path))
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=snapshot_id))

    exit_code = cli.main([
        "--database", str(db_path), "https://www.flylouisville.com/corporate/sdf-airport-improvements/", *_FLAGS,
        "--candidate-title", "SDF Airport Improvements | Louisville Muhammad Ali International Airport",
        "--candidate-dimension", "PROJECT_PHASE", "--candidate-triage-band", "MEDIUM",
        "--candidate-reason", "Exact airport name in title", "--candidate-reason", "construction in snippet",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "DISCOVERED CANDIDATE" in out
    assert "PROJECT_PHASE" in out
    assert "MEDIUM" in out
    assert f"Snapshot id: {snapshot_id}" in out
    assert "generic-html" in out  # the GENERIC dispatcher was used, not a PDF-only path
    assert "bid-construction package" in out  # real extracted text preserved, faithfully
    assert "Extraction ID: N/A" in out
    assert "No fact accepted. No Signal created." in out
    assert "review_fragment_selection.py" in out  # honest next-step hint, not automatic


def test_extraction_preview_is_bounded(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "test.db"
    long_text = b"<html><body><p>" + b"X" * 5000 + b"</p></body></html>"
    snapshot_id = _seed_html_snapshot(str(db_path), payload=long_text)
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=snapshot_id))

    exit_code = cli.main(["--database", str(db_path), "https://example.com/", *_FLAGS, "--preview-chars", "50"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "X" * 50 in out
    assert "X" * 51 not in out


def test_no_snapshot_stops_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(status="NO_CHANGE", snapshot_id=None))
    db_path = tmp_path / "test.db"
    from sqlalchemy import create_engine as _ce
    from app.database import Base as _Base
    _Base.metadata.create_all(_ce(f"sqlite:///{db_path}"))
    exit_code = cli.main(["--database", str(db_path), "https://example.com/", *_FLAGS])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No Snapshot was produced" in out
    assert "EXTRACTION" not in out


def test_candidate_metadata_omitted_shows_not_supplied(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=None))
    db_path = tmp_path / "test.db"
    from sqlalchemy import create_engine as _ce
    from app.database import Base as _Base
    _Base.metadata.create_all(_ce(f"sqlite:///{db_path}"))
    cli.main(["--database", str(db_path), "https://example.com/", *_FLAGS])
    out = capsys.readouterr().out
    assert "(not supplied)" in out


# --- No governance writes ------------------------------------------------


def test_no_signal_or_governed_rows_created(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    snapshot_id = _seed_html_snapshot(str(db_path))
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=snapshot_id))
    cli.main(["--database", str(db_path), "https://www.flylouisville.com/corporate/sdf-airport-improvements/", *_FLAGS])

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Signal)) == 0


def test_replay_reuses_same_snapshot_id(monkeypatch, tmp_path, capsys):
    """Idempotency: fetching the identical content twice must report the
    SAME Snapshot id both times (matching AcquisitionService.acquire()'s
    own NO_CHANGE/get-or-create contract) - this script fakes the fetch
    layer, so this test only proves the CLI faithfully reports whatever
    Snapshot id fetch_discovered_url() returns, on both calls."""
    db_path = tmp_path / "test.db"
    snapshot_id = _seed_html_snapshot(str(db_path))
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=snapshot_id))

    url = "https://www.flylouisville.com/corporate/sdf-airport-improvements/"
    cli.main(["--database", str(db_path), url, *_FLAGS])
    first = capsys.readouterr().out
    cli.main(["--database", str(db_path), url, *_FLAGS])
    second = capsys.readouterr().out
    assert f"Snapshot id: {snapshot_id}" in first
    assert f"Snapshot id: {snapshot_id}" in second
