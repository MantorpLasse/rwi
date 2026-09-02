"""RWI Mission #15B - offline tests for
scripts/persist_selected_fragments.py. Every test builds its own isolated
temp-file SQLite database via Base.metadata.create_all() (which already
includes the EB2/UAC2A/UAC2B schema unconditionally, since those models
are already committed - no migration script needed for a fresh test DB).
No network, no LLM, never touches data/runway_safe.db."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    AcquisitionRun,
    AcquisitionRunStatus,
    AcquisitionSource,
    Installation,
    PublishingSource,
    Signal,
    Snapshot,
    Source,
    SourceAssertion,
    UnknownAirportCandidate,
)
from scripts.persist_selected_fragments import (
    PersistConfig,
    PersistRunnerError,
    build_engine,
    compute_selected_fragment_plan_fingerprint,
    run_persist,
)

FILLER = "Filler paragraph text unrelated to airports or arresting systems. " * 16  # ~1088 chars

SENTENCE_1 = "EMAS installed at London City Airport this year."
SENTENCE_2 = "The engineered materials arresting system at London City Airport passed inspection."
SENTENCE_3 = "EMAS was discussed generally in this section, with no specific airport named."
SENTENCE_4 = "EMAS reference: London City ACC Airport."
SENTENCE_YTZ = "EMAS was considered at Billy Bishop Toronto City Airport, but the landmass alternative was approved."
SENTENCE_UNKNOWN = "EMAS is proposed at Fictional Testville Regional Airport, pending funding approval."

PAGE_TEXT = FILLER.join(["", SENTENCE_1, SENTENCE_2, SENTENCE_3, SENTENCE_4, SENTENCE_YTZ, SENTENCE_UNKNOWN, ""])


def _build_minimal_pdf(text: str) -> bytes:
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    content = b"BT /F1 10 Tf 40 750 Td (" + escaped.encode("latin-1", errors="replace") + b") Tj ET"
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


def _seed(db_path: str, *, key: str = "example:persist-test", text: str = PAGE_TEXT) -> dict:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        publisher = PublishingSource(name="Example Publisher", source_type="government", reliability_level="official")
        session.add(publisher)
        session.flush()
        source = AcquisitionSource(
            publishing_source=publisher, key=key, display_name="Example Document",
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
        payload = _build_minimal_pdf(text)
        snapshot = Snapshot(
            source=source, first_acquisition_run=run, payload=payload, sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload), media_type="application/pdf", retrieved_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        lcy = Airport(name="London City Airport", country="United Kingdom", iata_code="LCY", icao_code="EGLC")
        ytz = Airport(name="Billy Bishop Toronto City Airport", country="Canada", iata_code="YTZ", icao_code="CYTZ")
        session.add_all([lcy, ytz])
        session.commit()
        return {"snapshot_id": snapshot.id, "lcy_id": lcy.id, "ytz_id": ytz.id}


@pytest.fixture()
def seeded_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    ids = _seed(db_path)
    return db_path, ids


def _fragment_index(report: dict, needle: str) -> int:
    for entry in report["fragments_preview"]:
        if needle in entry["text"]:
            return entry["fragment_index"]
    raise AssertionError(f"no fragment containing {needle!r} in {report['fragments_preview']}")


# --- 1-3: dry-run / KEEP-only writes nothing; explicit PERSIST gate required ---


def test_default_dry_run_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before = hashlib.sha256(open(db_path, "rb").read()).hexdigest()
    report = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    after = hashlib.sha256(open(db_path, "rb").read()).hexdigest()
    assert report["applied"] is False
    assert before == after


def test_keep_alone_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before = hashlib.sha256(open(db_path, "rb").read()).hexdigest()
    report = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1, 2, 3, 4}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    after = hashlib.sha256(open(db_path, "rb").read()).hexdigest()
    assert report["applied"] is False
    assert len(report["planned_evidence"]) > 0
    assert before == after
    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        assert session.scalar(select(SourceAssertion).limit(1)) is None


def test_explicit_persist_gate_required_apply_alone_refused(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
            candidate_airport_ids=(ids["lcy_id"],), apply=True,
        ))


# --- 4-7: write-authorization gates ---


def test_apply_requires_allow_database_write(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError, match="--allow-database-write"):
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], apply=True))


def test_allow_database_write_requires_apply(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError, match="--apply"):
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], allow_database_write=True))


def test_expected_fingerprint_required(seeded_db):
    db_path, ids = seeded_db
    report = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True, skip_backup=True,
    ))
    assert report["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in report["blockers"])


def test_fingerprint_mismatch_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before = hashlib.sha256(open(db_path, "rb").read()).hexdigest()
    report = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True,
        expected_fingerprint="0" * 64, skip_backup=True,
    ))
    after = hashlib.sha256(open(db_path, "rb").read()).hexdigest()
    assert report["applied"] is False
    assert before == after


# --- 8-9: Source metadata mapping (see also test_selection_source_metadata.py) ---


def test_exact_source_metadata_mapping_in_report(seeded_db):
    db_path, ids = seeded_db
    report = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    assert report["document_identity"].startswith("example:persist-test:")


# --- 10-17: exact Source/SourceAssertion provenance, reuse, idempotency ---


def test_full_authorized_persist_creates_expected_rows_known_airport(seeded_db):
    db_path, ids = seeded_db
    dry_all = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx1 = _fragment_index(dry_all, SENTENCE_1)
    dry_narrow = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx1}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry_narrow["plan_fingerprint"], skip_backup=True,
    ))
    assert applied["applied"] is True
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "KNOWN_CANONICAL_ATTACHMENT"
    assert result["attachment_outcome"] in ("ATTACH_CONFIRMED", "ATTACH_PROVISIONAL")
    assert result["attached_airport_id"] == ids["lcy_id"]
    assert result["source_created"] is True
    assert result["source_assertion_created"] is True

    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        source = session.get(Source, result["source_id"])
        assert source.external_id == f"discovery:{dry_narrow['document_identity']}"
        assertion = session.get(SourceAssertion, result["source_assertion_id"])
        assert SENTENCE_1 in assertion.raw_relevant_text
        assert assertion.review_state == "unreviewed"
        assert assertion.identity_guard_decision in ("ATTACH_CONFIRMED", "ATTACH_PROVISIONAL")


def test_idempotent_exact_replay_reuses_source_and_assertion(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    cfg = dict(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True, skip_backup=True,
    )
    first = run_persist(PersistConfig(**cfg, expected_fingerprint=dry["plan_fingerprint"]))
    second = run_persist(PersistConfig(**cfg, expected_fingerprint=dry["plan_fingerprint"]))
    assert first["apply_result"][0]["source_created"] is True
    assert second["apply_result"][0]["source_created"] is False
    assert second["apply_result"][0]["source_assertion_created"] is False
    assert first["apply_result"][0]["source_assertion_id"] == second["apply_result"][0]["source_assertion_id"]

    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        assert session.scalar(select(Source).where()) is not None
        assert len(list(session.scalars(select(Source)))) == 1
        assert len(list(session.scalars(select(SourceAssertion)))) == 1


def test_changed_snapshot_bytes_give_distinct_document_identity(tmp_path):
    db_path = str(tmp_path / "test.db")
    ids = _seed(db_path, key="example:persist-test", text=PAGE_TEXT)
    report_a = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))

    ids_b = _seed(db_path, key="example:persist-test-2", text=PAGE_TEXT + " extra")
    report_b = run_persist(PersistConfig(database=db_path, snapshot_id=ids_b["snapshot_id"]))

    assert report_a["document_identity"] != report_b["document_identity"]


def test_same_text_different_locator_remains_distinct(seeded_db):
    """Verify every fragment in the whole selection has its own, unique
    source_locator (distinct offsets), so replaying different fragments of
    otherwise-identical wording never collides on SourceAssertion identity."""
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    all_kept = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"],
        keep_indices=frozenset(range(1, dry["fragments_selected"] + 1)),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]),
    ))
    locators = [e["source_locator"] for e in all_kept["planned_evidence"]]
    assert len(locators) == len(set(locators))


# --- 18-19: bibliography / imperfect identity ---


def test_bibliography_fragment_not_forced_to_airport(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "London City ACC Airport")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    entry = dry2["planned_evidence"][0]
    assert entry["guard_outcome"] == "INSUFFICIENT_IDENTITY"
    assert entry["attached_airport_id"] is None

    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    assert applied["applied"] is True
    assert applied["apply_result"][0]["attached_airport_id"] is None


def test_bibliography_fragment_not_accepted_or_promoted(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "London City ACC Airport")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        assertion = session.get(SourceAssertion, applied["apply_result"][0]["source_assertion_id"])
        assert assertion.intelligence_review_decision is None
        assert assertion.promotion_policy_decision is None
        assert assertion.signal_id is None
        assert assertion.review_state == "unreviewed"


# --- 20-23: YTZ negative context / outcome-does-not-force / does-not-imply-acceptance ---


def test_ytz_rejection_context_preserved_exactly(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "landmass alternative")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["ytz_id"],),
    ))
    entry = dry2["planned_evidence"][0]
    # The fragment's raw_text is Selection's own window around the match
    # (surrounding context, by design) - the full rejection sentence must
    # appear byte-exact WITHIN it, in both the PREVIEW and the persisted row.
    assert SENTENCE_YTZ in entry["raw_text"]
    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["ytz_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        assertion = session.get(SourceAssertion, applied["apply_result"][0]["source_assertion_id"])
        assert SENTENCE_YTZ in assertion.raw_relevant_text
        assert assertion.raw_relevant_text == entry["raw_text"]  # exact fragment shown in preview, byte-for-byte
        assert "landmass alternative was approved" in assertion.raw_relevant_text
        # Never a field encoding "installed"/"has EMAS" - only the literal citation + identity match.
        assert assertion.assertion_type == "project_construction"


def test_attach_provisional_does_not_imply_accepted_evidence(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "landmass alternative")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["ytz_id"],),
    ))
    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["ytz_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        assertion = session.get(SourceAssertion, applied["apply_result"][0]["source_assertion_id"])
        assert assertion.identity_guard_decision in ("ATTACH_CONFIRMED", "ATTACH_PROVISIONAL")
        assert assertion.intelligence_review_decision is None
        assert assertion.signal_id is None
        assert session.scalar(select(Signal)) is None
        assert session.scalar(select(Installation)) is None


def test_insufficient_identity_does_not_force_airport_id(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "no specific airport named")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]),
    ))
    entry = dry2["planned_evidence"][0]
    assert entry["guard_outcome"] == "INSUFFICIENT_IDENTITY"
    assert entry["attached_airport_id"] is None


# --- 24-28: unknown-airport path ---


def test_unknown_airport_path_uses_uac(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "Fictional Testville Regional Airport")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]),
    ))
    assert dry2["planned_evidence"][0]["would_form_unknown_airport_candidate"] is True
    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]), apply=True, allow_database_write=True,
        expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "UNKNOWN_AIRPORT_CANDIDATE"
    assert result["unknown_airport_candidate_id"] is not None
    assert result["attached_airport_id"] is None

    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        uac = session.get(UnknownAirportCandidate, result["unknown_airport_candidate_id"])
        assert uac is not None
        assertion = session.get(SourceAssertion, result["source_assertion_id"])
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id == uac.id


def test_unknown_airport_persistence_creates_no_airport_signal_installation(seeded_db):
    db_path, ids = seeded_db
    engine = build_engine(__import__("pathlib").Path(db_path))
    with Session(engine) as session:
        airports_before = session.scalar(select(Airport)).__class__ and len(list(session.scalars(select(Airport))))

    dry = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"]))
    idx = _fragment_index(dry, "Fictional Testville Regional Airport")
    dry2 = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]),
    ))
    run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]), apply=True, allow_database_write=True,
        expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))

    with Session(engine) as session:
        assert len(list(session.scalars(select(Airport)))) == airports_before
        assert session.scalar(select(Signal)) is None
        assert session.scalar(select(Installation)) is None


# --- 29, 32: explicit candidate Airport IDs required / invalid ID refused ---


def test_missing_candidate_airport_ids_refused_when_fragments_kept(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError, match="MISSING_CANDIDATE_AIRPORT_IDS"):
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1})))


def test_no_known_candidates_explicit_opt_out_allowed(seeded_db):
    db_path, ids = seeded_db
    report = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}), no_known_candidates=True,
    ))
    assert report["applied"] is False
    assert report["planned_evidence"][0]["candidate_airport_ids"] == []


def test_invalid_candidate_airport_id_refuses(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError, match="INVALID_CANDIDATE_AIRPORT_ID"):
        run_persist(PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
            candidate_airport_ids=(999999,),
        ))


# --- 30-31: search seed / SelectionReason cannot affect IdentityGuard ---


def test_search_seed_alone_cannot_affect_identity_guard(seeded_db):
    db_path, ids = seeded_db
    with_seed = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],), identity_name="London City Airport", identity_iata="LCY",
    ))
    without_seed = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    assert with_seed["planned_evidence"][0]["guard_outcome"] == without_seed["planned_evidence"][0]["guard_outcome"]
    assert with_seed["planned_evidence"][0]["attached_airport_id"] == without_seed["planned_evidence"][0]["attached_airport_id"]


def test_search_seed_displayed_but_not_persisted_as_evidence(seeded_db):
    db_path, ids = seeded_db
    report = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], identity_name="London City Airport", identity_iata="LCY",
    ))
    assert report["search_seed_display_only"] == {"name": "London City Airport", "iata": "LCY", "icao": None}


# --- 33-35: backup / plan row counts / fingerprint consistency ---


def test_backup_created_before_apply(seeded_db, tmp_path):
    db_path, ids = seeded_db
    backup_dir = tmp_path / "backups"
    dry = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry["plan_fingerprint"], backup_directory=backup_dir,
    ))
    assert applied["applied"] is True
    from pathlib import Path
    assert Path(applied["backup_path"]).exists()
    assert Path(applied["backup_path"]).parent == backup_dir


def test_plan_row_counts_accurate(seeded_db):
    db_path, ids = seeded_db
    dry = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    entry = dry["planned_evidence"][0]
    assert entry["source_would_be_created"] is True
    assert entry["source_assertion_would_be_created"] is True

    applied = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],), apply=True, allow_database_write=True,
        expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    dry_again = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    entry2 = dry_again["planned_evidence"][0]
    assert entry2["source_would_be_created"] is False
    assert entry2["source_assertion_would_be_created"] is False


def test_preview_apply_fingerprint_consistency(seeded_db):
    db_path, ids = seeded_db
    dry_a = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    dry_b = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"],),
    ))
    assert dry_a["plan_fingerprint"] == dry_b["plan_fingerprint"]

    # Different explicit candidate set -> different fingerprint even if
    # the winning outcome/code would coincide (Mission #15B Part H).
    dry_c = run_persist(PersistConfig(
        database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({1}),
        candidate_airport_ids=(ids["lcy_id"], ids["ytz_id"]),
    ))
    assert dry_a["plan_fingerprint"] != dry_c["plan_fingerprint"]


def test_compute_fingerprint_is_deterministic_pure_function():
    fp1 = compute_selected_fragment_plan_fingerprint([], [1, 2, 3])
    fp2 = compute_selected_fragment_plan_fingerprint([], [3, 2, 1])
    assert fp1 == fp2  # order-independent over candidate ids
