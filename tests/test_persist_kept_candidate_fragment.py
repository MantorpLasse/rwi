"""RWI HQ "Human KEEP -> Governed Evidence Persistence Bridge" mission -
tests for scripts/persist_kept_candidate_fragment.py (Part 10, offline
only - synthetic in-memory/temp-file SQLite, no network, no production DB
touched anywhere in this file)."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    AcquisitionRun,
    AcquisitionRunStatus,
    AcquisitionSource,
    PublishingSource,
    ReviewerAction,
    Runway,
    Signal,
    Source,
    SourceAssertion,
)
from app.models.acquisition import Snapshot
from app.extraction.dispatch import extract_document
from app.selection.fragment_selection import select_fragments
from app.selection.review import ReviewDecision, apply_keep_decisions
from app.services.known_airport_evidence_persistence import KnownAirportEvidenceConflictError
from app.services.snapshot_extraction import load_snapshot_for_extraction
from scripts.persist_kept_candidate_fragment import (
    build_metadata_from_kept_fragments,
    main,
    persist_kept_fragments,
    preview_kept_fragments,
)

# --- Fixtures --------------------------------------------------------------

# Calibrated (see mission recon) so select_fragments() produces exactly the
# fragment counts these tests assert on - real select_fragments() logic,
# never mocked or bypassed.
SDF_HTML = (
    b"<html><body>"
    b"<p>Construction updates for the terminal parking garage continue through the spring "
    b"with no major disruptions expected for travelers this season at all.</p>"
    b"<p>Phase 1 of the East Runway's EMAS will be installed this year as part of the "
    b"ongoing safety area program at the airport.</p>"
    b"</body></html>"
)

_MHT_FILLER = (
    "Unrelated administrative filler text about parking rates schedules and shuttle bus "
    "operations for passengers. "
) * 8

MHT_HTML = (
    "<html><body>"
    "<p>REPLACEMENT OF RUNWAY 6 DEPARTURE END ENGINEERED MATERIAL ARRESTING SYSTEM EMAS "
    "project overview for the committee.</p>"
    f"<p>{_MHT_FILLER}</p>"
    "<p>The EMAS bid opening will occur at the airport administration office next month "
    "per the procurement notice.</p>"
    f"<p>{_MHT_FILLER}</p>"
    "<p>Overall project completion for the EMAS installation is anticipated in 2027 "
    "pending contractor selection.</p>"
    "</body></html>"
).encode()


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_airport(session, **overrides) -> Airport:
    defaults = dict(name="Standiford", iata_code="SDF", country="USA")
    defaults.update(overrides)
    airport = Airport(**defaults)
    session.add(airport)
    session.flush()
    return airport


def seed_snapshot(session, *, payload: bytes, media_type: str = "text/html", url: str = "https://example.test/doc") -> Snapshot:
    publisher = PublishingSource(name="Example Publisher", reliability_level="unverified")
    session.add(publisher)
    session.flush()
    key = f"example:{hashlib.sha256(url.encode()).hexdigest()[:16]}"
    acquisition_source = AcquisitionSource(
        publishing_source=publisher, key=key, display_name="Example Source",
        acquisition_type="http", canonical_url=url, active=True,
    )
    session.add(acquisition_source)
    session.flush()
    run = AcquisitionRun(
        source=acquisition_source, started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
        status=AcquisitionRunStatus.SUCCESS, request_url=url, provider_version="test/1", duration_seconds=0.1,
    )
    session.add(run)
    session.flush()
    snapshot = Snapshot(
        source=acquisition_source, first_acquisition_run=run, payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), media_type=media_type,
        retrieved_at=datetime.now(UTC),
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def build_selection(session, snapshot_id: int):
    loaded = load_snapshot_for_extraction(session, snapshot_id)
    document = extract_document(loaded.payload, document_identity=loaded.document_identity, media_type=loaded.media_type)
    return select_fragments(document)


def _kept(session, snapshot, *, keep_indices, title, url=None):
    selection = build_selection(session, snapshot.id)
    reviews = apply_keep_decisions(selection, keep_indices=frozenset(keep_indices), document_title=title, url=url or snapshot.source.canonical_url)
    return selection, tuple(r for r in reviews if r.decision == ReviewDecision.KEEP)


def _row_counts(session):
    return (len(session.new), len(session.dirty), len(session.deleted))


# --- PREVIEW / WRITE SAFETY --------------------------------------------


def test_preview_creates_zero_writes():
    session = make_session()
    airport = make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    before = _row_counts(session)
    previews = preview_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    after = _row_counts(session)

    assert before == (0, 0, 0)
    assert after == (0, 0, 0)
    assert session.query(Source).count() == 0
    assert session.query(SourceAssertion).count() == 0
    assert len(previews) == 1
    assert previews[0][1].source_would_be_created is True


def test_cli_requires_keep_before_persisting(tmp_path):
    db_path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = make_airport(session)
        snapshot = seed_snapshot(session, payload=SDF_HTML)
        snapshot_id = snapshot.id

    exit_code = main(["--database", str(db_path), "--snapshot-id", str(snapshot_id)])

    assert exit_code == 0
    with Session(create_engine(f"sqlite:///{db_path}")) as session:
        assert session.query(Source).count() == 0
        assert session.query(SourceAssertion).count() == 0


def test_cli_keep_without_write_flag_is_preview_only(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = make_airport(session)
        snapshot = seed_snapshot(session, payload=SDF_HTML, url="https://flylouisville.com/sdf-update")
        airport_id = airport.id
        snapshot_id = snapshot.id

    exit_code = main([
        "--database", str(db_path), "--snapshot-id", str(snapshot_id), "--keep", "1",
        "--airport-id", str(airport_id), "--document-title", "SDF Update",
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PREVIEW ONLY" in out
    assert "proposed Source: CREATE" in out
    with Session(create_engine(f"sqlite:///{db_path}")) as session:
        assert session.query(Source).count() == 0
        assert session.query(SourceAssertion).count() == 0


def test_cli_persists_and_prints_ids_clearly(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = make_airport(session)
        snapshot = seed_snapshot(session, payload=SDF_HTML, url="https://flylouisville.com/sdf-update")
        airport_id = airport.id
        snapshot_id = snapshot.id

    exit_code = main([
        "--database", str(db_path), "--snapshot-id", str(snapshot_id), "--keep", "1",
        "--airport-id", str(airport_id), "--document-title", "SDF Update",
        "--allow-database-write",
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "=== PERSISTED ===" in out
    assert "Source: CREATED" in out
    assert "SourceAssertion: CREATED" in out
    assert "Next step (Update & Report" in out
    with Session(create_engine(f"sqlite:///{db_path}")) as session:
        assert session.query(SourceAssertion).count() == 1
        assert session.query(Source).count() == 1


# --- SOURCE / SOURCEASSERTION PERSISTENCE -------------------------------


def test_source_created_then_reused_across_kept_fragments_same_document():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport", iata_code="MHT")
    snapshot = seed_snapshot(session, payload=MHT_HTML, url="https://flymanchester.com/bid-manual.pdf")
    _, kept = _kept(session, snapshot, keep_indices={1, 2, 3}, title="MHT Bid Manual")
    assert len(kept) == 3
    metadata = build_metadata_from_kept_fragments(kept, title="MHT Bid Manual", url=snapshot.source.canonical_url)

    results = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    assert len(results) == 3
    source_ids = {result.source_id for _, result in results}
    assert len(source_ids) == 1
    assert results[0][1].source_created is True
    assert results[1][1].source_created is False
    assert results[2][1].source_created is False
    assertion_ids = {result.source_assertion_id for _, result in results}
    assert len(assertion_ids) == 3
    assert session.query(Source).count() == 1
    assert session.query(SourceAssertion).count() == 3


def test_mht_distinct_fragment_text_preserved():
    session = make_session()
    airport = make_airport(session, name="Manchester-Boston Regional Airport", iata_code="MHT")
    snapshot = seed_snapshot(session, payload=MHT_HTML, url="https://flymanchester.com/bid-manual.pdf")
    _, kept = _kept(session, snapshot, keep_indices={1, 2, 3}, title="MHT Bid Manual")
    metadata = build_metadata_from_kept_fragments(kept, title="MHT Bid Manual", url=snapshot.source.canonical_url)

    results = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    texts = [session.get(SourceAssertion, result.source_assertion_id).raw_relevant_text for _, result in results]
    assert len(set(texts)) == 3
    assert any("REPLACEMENT OF RUNWAY 6" in t for t in texts)
    assert any("EMAS bid opening" in t for t in texts)
    assert any("Overall project completion" in t for t in texts)


def test_exact_fragment_text_and_url_and_airport_preserved():
    session = make_session()
    airport = make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML, url="https://flylouisville.com/sdf-update")
    selection, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    results = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    _, result = results[0]
    assertion = session.get(SourceAssertion, result.source_assertion_id)
    source = session.get(Source, result.source_id)
    assert assertion.raw_relevant_text == kept[0].candidate_fragment.raw_text
    assert "Phase 1 of the East Runway" in assertion.raw_relevant_text
    assert source.url == "https://flylouisville.com/sdf-update"
    assert assertion.airport_id == airport.id
    assert assertion.artifact_identity == kept[0].candidate_fragment.artifact_identity == selection.document_identity


def test_language_is_never_fabricated():
    session = make_session()
    make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")

    assert kept[0].candidate_fragment.language is None


def test_evidence_quality_and_review_state_are_stage_shaped():
    session = make_session()
    airport = make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    results = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    assertion = session.get(SourceAssertion, results[0][1].source_assertion_id)
    assert assertion.evidence_quality == "unverified_candidate"
    assert assertion.review_state == "unreviewed"
    assert assertion.identity_guard_decision is None
    assert assertion.intelligence_review_decision is None
    assert assertion.promotion_policy_decision is None


# --- SAFETY: NO SIGNAL / REVIEWERACTION / PUBLICATION -------------------


def test_no_signal_created_or_linked():
    session = make_session()
    airport = make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    results = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    assertion = session.get(SourceAssertion, results[0][1].source_assertion_id)
    assert session.query(Signal).count() == 0
    assert assertion.signal_id is None


def test_no_reviewer_action_created():
    session = make_session()
    airport = make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    assert session.query(ReviewerAction).count() == 0


def test_no_publication_side_effect():
    session = make_session()
    airport = make_airport(session)
    existing_signal = Signal(airport=airport, title="Existing Signal", category="new_installation", confidence="medium", published=True)
    session.add(existing_signal)
    session.commit()

    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    session.refresh(existing_signal)
    assert existing_signal.published is True


# --- IDEMPOTENCY / CONFLICTS --------------------------------------------


def test_rerun_is_idempotent():
    session = make_session()
    airport = make_airport(session)
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    results1 = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()
    results2 = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    assert results1[0][1].source_assertion_id == results2[0][1].source_assertion_id
    assert results1[0][1].source_assertion_created is True
    assert results2[0][1].source_assertion_created is False
    assert results2[0][1].source_created is False
    assert session.query(SourceAssertion).count() == 1
    assert session.query(Source).count() == 1


def test_failure_leaves_db_unchanged_on_conflicting_airport():
    session = make_session()
    airport = make_airport(session)
    other_airport = make_airport(session, name="Other Airport", iata_code="OTH")
    snapshot = seed_snapshot(session, payload=SDF_HTML)
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    with pytest.raises(KnownAirportEvidenceConflictError):
        persist_kept_fragments(session, kept, metadata, airport_id=other_airport.id, assertion_type="project_construction")
    session.rollback()

    assert session.query(SourceAssertion).count() == 1
    assert session.query(Source).count() == 1


# --- SDF / MHT BENCHMARKS (Part 8/9, test-fixture-shaped, never production) ----


def test_sdf_ambiguity_preserved_no_runway_end_or_signal_guessed():
    session = make_session()
    airport = make_airport(session, name="Standiford", iata_code="SDF")
    runway = Runway(airport=airport, designation="17L-35R")
    session.add(runway)
    session.flush()
    existing_signal = Signal(
        airport=airport, runway=runway, title="North EMAS Runway 17L-35R - design and bidding",
        category="new_installation", confidence="medium",
    )
    session.add(existing_signal)
    session.commit()

    snapshot = seed_snapshot(session, payload=SDF_HTML, url="https://flylouisville.com/sdf-update")
    _, kept = _kept(session, snapshot, keep_indices={1}, title="SDF Update")
    metadata = build_metadata_from_kept_fragments(kept, title="SDF Update", url=snapshot.source.canonical_url)

    # Deliberately no runway_id passed - the evidence never names a runway end.
    results = persist_kept_fragments(session, kept, metadata, airport_id=airport.id, assertion_type="project_construction")
    session.commit()

    assertion = session.get(SourceAssertion, results[0][1].source_assertion_id)
    assert assertion.runway_id is None
    assert assertion.signal_id is None
    assert "Phase 1 of the East Runway" in assertion.raw_relevant_text

    from app.services.update_report_change_detection import CandidateEvidenceInput, classify_candidate
    from app.services.update_report_vocabulary import MaterialChangeClassification

    classification = classify_candidate(session, CandidateEvidenceInput(source_assertion_id=assertion.id))
    assert classification.classification == MaterialChangeClassification.NEEDS_MORE_EVIDENCE
    assert classification.existing_signal_id is None
    assert classification.proposed_changes == {}
