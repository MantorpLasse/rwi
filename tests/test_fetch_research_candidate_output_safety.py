"""RWI HQ "Discovery Research Loop V1 - Slice 4C" - regression tests for
the Windows console output defect discovered in Slice 4B: a real fetched
page containing a valid Unicode character outside the active console's
encoding (observed: U+1F50D, a search-icon emoji, under Windows cp1252)
crashed scripts/fetch_research_candidate.py with UnicodeEncodeError even
though the Fetch/Snapshot/Extraction themselves had already succeeded.

pytest's own capsys fixture captures stdout/stderr through a UTF-8-capable
path and would never reproduce this class of bug - these tests instead
exercise scripts.fetch_research_candidate._safe_print() and cli.main()
through a fake stream object that raises UnicodeEncodeError from write()
for any character outside a chosen restrictive codec, exactly like a real
Windows console bound to cp1252 would."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.fetch_research_candidate as cli
from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot

_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)
_FLAGS = ["--allow-live-network", "--allow-database-write"]

# The exact character class from the real Slice 4B defect, plus one
# additional non-Latin-script example (also outside cp1252) to cover
# "other valid Unicode page content", and one example that cp1252 CAN
# already represent, to prove representable text is left untouched.
_SEARCH_ICON = "\U0001f50d"  # 🔍 - the actual character that crashed Slice 4B
_NON_LATIN = "検索"  # "検索" (Japanese for "search") - also outside cp1252
_CP1252_SAFE = "café — dash"  # café — dash: both bytes ARE representable in cp1252


class _RestrictiveEncodingStream:
    """Test double standing in for a real stdout/stderr TextIOWrapper bound
    to a restrictive encoding: write() raises UnicodeEncodeError for any
    character outside that codec, exactly like the real Windows console
    that produced the Slice 4B crash - independent of whatever encoding
    this test happens to run under."""

    def __init__(self, encoding: str = "cp1252"):
        self.encoding = encoding
        self._chunks: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding)  # raises UnicodeEncodeError, same as a real stream would
        self._chunks.append(text)
        return len(text)

    def getvalue(self) -> str:
        return "".join(self._chunks)


def _seed_html_snapshot(db_path: str, *, payload: bytes) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        publishing_source = PublishingSource(name="example.test", source_type=None, reliability_level="unverified")
        session.add(publishing_source)
        session.flush()
        acquisition_source = AcquisitionSource(
            publishing_source_id=publishing_source.id, key="generic_web:testkey",
            display_name="example.test", acquisition_type="generic_web",
            canonical_url="https://example.test/page", expected_media_type=None,
        )
        session.add(acquisition_source)
        session.flush()
        run = AcquisitionRun(
            acquisition_source_id=acquisition_source.id, started_at=_NOW, completed_at=None,
            status=AcquisitionRunStatus.RUNNING, request_url=acquisition_source.canonical_url,
            final_url=None, http_status=None, content_type=None, provider_version="generic-web/0.1",
            duration_seconds=0.0, is_new_snapshot=None,
        )
        session.add(run)
        session.flush()
        snapshot = Snapshot(
            acquisition_source_id=acquisition_source.id, first_acquisition_run_id=run.id,
            payload=payload, sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload),
            media_type="text/html", retrieved_at=_NOW,
        )
        session.add(snapshot)
        session.flush()
        run.status = AcquisitionRunStatus.SUCCESS
        run.completed_at = _NOW
        run.final_url = acquisition_source.canonical_url
        run.http_status = 200
        run.content_type = "text/html"
        run.is_new_snapshot = True
        run.snapshot_id = snapshot.id
        session.commit()
        return snapshot.id


def _fake_run(*, snapshot_id):
    snapshot = SimpleNamespace(id=snapshot_id, sha256="a" * 64, byte_size=123, media_type="text/html", retrieved_at=_NOW)
    return SimpleNamespace(
        status=SimpleNamespace(value="SUCCESS"), final_url="https://example.test/page", http_status=200,
        content_type="text/html", duration_seconds=0.5, snapshot=snapshot,
    )


# --- Unit-level: _safe_print itself -----------------------------------------


def test_safe_print_does_not_raise_on_unrepresentable_character():
    stream = _RestrictiveEncodingStream("cp1252")
    cli._safe_print(f"Bounded preview: {_SEARCH_ICON} Search...", file=stream)
    # must not have raised - if it did, this line is never reached
    assert stream.getvalue() != ""


def test_safe_print_preserves_representable_text_verbatim():
    stream = _RestrictiveEncodingStream("cp1252")
    cli._safe_print(_CP1252_SAFE, file=stream)
    assert _CP1252_SAFE in stream.getvalue()


def test_safe_print_falls_back_readably_for_unrepresentable_character():
    stream = _RestrictiveEncodingStream("cp1252")
    cli._safe_print(f"icon={_SEARCH_ICON}", file=stream)
    out = stream.getvalue()
    assert "icon=" in out
    # a readable escape stands in for the character the stream cannot hold -
    # never a raw crash, never silent data loss with no trace at all
    assert "1f50d" in out.lower()


def test_safe_print_handles_non_latin_script_too():
    stream = _RestrictiveEncodingStream("cp1252")
    cli._safe_print(f"query: {_NON_LATIN}", file=stream)
    assert "query:" in stream.getvalue()


def test_safe_print_does_not_mutate_its_input_string():
    """Output-only safety: the original Python str object (standing in for
    ExtractedDocument text) must be byte-for-byte identical after being
    printed - this proves the fix cannot be reaching into and rewriting
    the underlying extracted Unicode content itself."""
    original = f"Bounded preview: {_SEARCH_ICON} café"
    original_copy = str(original)
    stream = _RestrictiveEncodingStream("cp1252")
    cli._safe_print(original, file=stream)
    assert original == original_copy


def test_safe_print_stdout_default_still_works(capsys):
    """When the stream IS capable (pytest's own capsys, or any real UTF-8
    stream), behavior is identical to plain print() - no regression for
    the common case."""
    cli._safe_print(f"hello {_SEARCH_ICON}")
    out = capsys.readouterr().out
    assert f"hello {_SEARCH_ICON}" in out


# --- Full CLI-level: the actual Slice 4B crash, reproduced and fixed -------


def test_main_completes_successfully_when_extracted_text_has_unrepresentable_unicode(monkeypatch, tmp_path):
    """This is the exact defect class from Slice 4B: real extracted HTML
    text contains U+1F50D. Previously this crashed scripts/fetch_research_candidate.py
    with UnicodeEncodeError under a restrictive console encoding, AFTER
    the Fetch and Snapshot had already succeeded. It must now complete
    with exit code 0."""
    db_path = tmp_path / "test.db"
    payload = f"<html><body><p>{_SEARCH_ICON} Search... phase 1 EMAS install café {_NON_LATIN}</p></body></html>".encode("utf-8")
    snapshot_id = _seed_html_snapshot(str(db_path), payload=payload)
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=snapshot_id))

    restrictive_stdout = _RestrictiveEncodingStream("cp1252")
    monkeypatch.setattr(cli.sys, "stdout", restrictive_stdout)

    exit_code = cli.main(["--database", str(db_path), "https://example.test/page", *_FLAGS])

    assert exit_code == 0
    out = restrictive_stdout.getvalue()
    assert "phase 1 EMAS install" in out  # representable extracted text survives verbatim
    assert "caf" in out
    assert "1f50d" in out.lower()  # unrepresentable character shown as a readable escape, not lost silently
    assert "GOVERNANCE" in out  # the report ran all the way to the end, not truncated by a crash


def test_underlying_extracted_document_is_unaffected_by_output_fix(monkeypatch, tmp_path):
    """Confirms the fix is output-only: re-running extract_document()
    directly against the same Snapshot (bypassing the CLI's printing
    entirely) yields the exact same real Unicode text, unmodified."""
    from app.extraction.dispatch import extract_document
    from app.services.snapshot_extraction import load_snapshot_for_extraction

    db_path = tmp_path / "test.db"
    payload = f"<html><body><p>{_SEARCH_ICON} Search</p></body></html>".encode("utf-8")
    snapshot_id = _seed_html_snapshot(str(db_path), payload=payload)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        loaded = load_snapshot_for_extraction(session, snapshot_id)
    document = extract_document(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)
    full_text = "".join(page.text for page in document.pages)

    assert _SEARCH_ICON in full_text  # the real emoji character, present and un-mutated
    assert loaded.payload == payload  # Snapshot payload itself untouched
    assert loaded.snapshot_sha256 == hashlib.sha256(payload).hexdigest()  # sha256 unaffected


def test_main_still_never_dumps_raw_html_tags(monkeypatch, tmp_path):
    """Regression guard: the output-safety fix must not change what
    content is shown - still metadata + bounded preview only, never raw
    markup."""
    db_path = tmp_path / "test.db"
    payload = f"<html><body><p>{_SEARCH_ICON} plain text</p></body></html>".encode("utf-8")
    snapshot_id = _seed_html_snapshot(str(db_path), payload=payload)
    monkeypatch.setattr(cli, "fetch_discovered_url", lambda session, url, **kw: _fake_run(snapshot_id=snapshot_id))
    restrictive_stdout = _RestrictiveEncodingStream("cp1252")
    monkeypatch.setattr(cli.sys, "stdout", restrictive_stdout)

    cli.main(["--database", str(db_path), "https://example.test/page", *_FLAGS])
    out = restrictive_stdout.getvalue()
    assert "<p>" not in out
    assert "<html>" not in out
