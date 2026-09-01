"""RWI Mission #11B Part M (CLI) - offline tests for
scripts/fetch_discovered_url.py. No real network, temp SQLite file DB
(the script builds its own engine from a --database path, matching
scripts/add_london_city_emas.py's own convention)."""

from __future__ import annotations

import argparse

import pytest

import scripts.fetch_discovered_url as cli


def test_url_argument_is_required():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["--database", "x.db"])


def test_database_argument_is_required():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["https://example.com/"])


def test_parser_accepts_exactly_one_url_positional():
    args = cli._parser().parse_args(["--database", "x.db", "https://example.com/page"])
    assert args.url == "https://example.com/page"
    assert args.database == "x.db"


def test_main_blocks_unsafe_target_without_writing(tmp_path, capsys: pytest.CaptureFixture):
    db_path = tmp_path / "test.db"
    exit_code = cli.main(["--database", str(db_path), "http://127.0.0.1/admin"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "FETCH BLOCKED" in err


def test_main_never_dumps_raw_bytes_to_stdout(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture):
    """Even on a (mocked) successful fetch, the CLI must print only
    metadata - never the retrieved document content."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    def fake_fetch_discovered_url(session, url, **kwargs):
        return SimpleNamespace(
            status=SimpleNamespace(value="SUCCESS"),
            final_url=url,
            http_status=200,
            content_type="text/html",
            duration_seconds=0.42,
            snapshot=SimpleNamespace(id=1, sha256="a" * 64, byte_size=123),
        )

    monkeypatch.setattr(cli, "fetch_discovered_url", fake_fetch_discovered_url)
    db_path = tmp_path / "test.db"
    exit_code = cli.main(["--database", str(db_path), "https://example.com/page"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "a" * 64 in out  # the hash, safe to show
    assert "<html>" not in out  # never raw content
    assert "not evidence" in out.lower()


def test_no_bulk_or_multi_url_flag_exists():
    """The CLI accepts exactly one URL positional - no --urls/--file/--batch
    flag exists (Mission #11B Part K: "Do NOT automatically fetch all HIGH
    triage results")."""
    parser = cli._parser()
    dest_names = {action.dest for action in parser._actions}
    assert not any(name in dest_names for name in ("urls", "file", "batch", "high", "triage"))
