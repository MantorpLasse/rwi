"""RWI Mission #25J1 (architecture sign-off: Mission #25I) - offline tests
for scripts/persist_selected_fragments.py's --stage-only mode.

Every test builds its own isolated temp-file SQLite database via
Base.metadata.create_all(); no network, no LLM, never touches
data/runway_safe.db. Reuses tests/test_persist_selected_fragments.py's own
established seeding/fixture conventions rather than duplicating them."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Airport,
    Installation,
    ReviewerAction,
    Runway,
    Signal,
    Source,
    SourceAssertion,
    SourceAssertionEvidenceBag,
    UnknownAirportCandidate,
)
from scripts.persist_selected_fragments import (
    PersistConfig,
    PersistRunnerError,
    run_persist,
)
from tests.test_persist_selected_fragments import PAGE_TEXT, SENTENCE_1, _fragment_index, _seed


@pytest.fixture()
def seeded_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    ids = _seed(db_path)
    return db_path, ids


def _counts(db_path: str) -> dict:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        return {
            "Source": session.scalar(select(func.count(Source.id))),
            "SourceAssertion": session.scalar(select(func.count(SourceAssertion.id))),
            "SourceAssertionEvidenceBag": session.scalar(select(func.count(SourceAssertionEvidenceBag.id))),
            "UnknownAirportCandidate": session.scalar(select(func.count(UnknownAirportCandidate.id))),
            "ReviewerAction": session.scalar(select(func.count(ReviewerAction.id))),
            "Airport": session.scalar(select(func.count(Airport.id))),
            "Runway": session.scalar(select(func.count(Runway.id))),
            "Installation": session.scalar(select(func.count(Installation.id))),
            "Signal": session.scalar(select(func.count(Signal.id))),
        }


def _db_sha(db_path: str) -> str:
    return hashlib.sha256(open(db_path, "rb").read()).hexdigest()


# --- 1: preview performs zero writes -----------------------------------


def test_stage_only_preview_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before_sha = _db_sha(db_path)
    before_counts = _counts(db_path)

    preview = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True))
    idx = _fragment_index(preview, SENTENCE_1)
    run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )

    assert _db_sha(db_path) == before_sha
    assert _counts(db_path) == before_counts


# --- 2-4: stage-only apply creates exactly Source + SourceAssertion -----


def test_stage_only_apply_creates_source_and_assertion(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)

    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    assert preview["fragments_kept"] == 1
    assert len(preview["planned_evidence"]) == 1
    fingerprint = preview["plan_fingerprint"]

    applied = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=fingerprint,
        )
    )
    assert applied["applied"] is True
    assert applied["blockers"] == []
    assert len(applied["apply_result"]) == 1

    after = _counts(db_path)
    assert after["Source"] == before["Source"] + 1
    assert after["SourceAssertion"] == before["SourceAssertion"] + 1


def test_stage_only_apply_field_values_exact(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion).order_by(SourceAssertion.id.desc()))
        assert assertion.assertion_type == "project_construction"
        assert assertion.evidence_quality == "unverified_candidate"
        assert assertion.review_state == "unreviewed"
        assert assertion.airport_id is None
        assert assertion.runway_id is None
        assert assertion.unknown_airport_candidate_id is None
        assert assertion.identity_guard_decision is None
        assert assertion.identity_guard_reason is None
        assert assertion.intelligence_review_decision is None
        assert assertion.intelligence_review_reason is None
        assert assertion.promotion_policy_decision is None
        assert assertion.promotion_policy_reason is None
        assert SENTENCE_1 in assertion.raw_relevant_text
        assert assertion.raw_fragment_hash == hashlib.sha256(assertion.raw_relevant_text.encode("utf-8")).hexdigest()
        assert assertion.source_locator is not None
        assert assertion.artifact_identity is not None

        source = session.get(Source, assertion.source_id)
        assert source.external_id == f"discovery:{assertion.artifact_identity}"


def test_stage_only_apply_no_evidence_bag_uac_reviewer_action(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    after = _counts(db_path)
    assert after["SourceAssertionEvidenceBag"] == before["SourceAssertionEvidenceBag"]
    assert after["UnknownAirportCandidate"] == before["UnknownAirportCandidate"]
    assert after["ReviewerAction"] == before["ReviewerAction"]


def test_stage_only_apply_no_domain_mutation(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    after = _counts(db_path)
    assert after["Airport"] == before["Airport"]
    assert after["Runway"] == before["Runway"]
    assert after["Installation"] == before["Installation"]
    assert after["Signal"] == before["Signal"]


# --- 17: replay is idempotent -------------------------------------------


def test_stage_only_replay_idempotent(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    fingerprint = preview["plan_fingerprint"]
    first = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=fingerprint,
        )
    )
    after_first = _counts(db_path)

    # Replay: preview again (fingerprint should reproduce identically since
    # the underlying fragment text is unchanged), then apply again.
    preview2 = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    assert preview2["plan_fingerprint"] == fingerprint
    second = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=fingerprint,
        )
    )
    after_second = _counts(db_path)

    assert after_second == after_first  # no duplicate row created
    assert second["apply_result"][0]["source_assertion_id"] == first["apply_result"][0]["source_assertion_id"]
    assert second["apply_result"][0]["source_assertion_created"] is False
    assert second["apply_result"][0]["source_created"] is False


# --- Safety: write-authorization / fingerprint / zero-kept gates --------


def test_stage_only_missing_write_authorization_raises(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
                apply=True, allow_database_write=False,
            )
        )
    assert _counts(db_path) == before


def test_stage_only_wrong_fingerprint_no_write(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    result = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint="deadbeef" * 8,
        )
    )
    assert result["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in result["blockers"])
    assert _counts(db_path) == before


def test_stage_only_zero_kept_fragments_no_write(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    # keep_indices pointing at a position far beyond any real fragment ->
    # apply_keep_decisions() keeps nothing (no error, just zero KEEPs).
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({9999}), stage_only=True)
    )
    assert preview["fragments_kept"] == 0
    assert preview["planned_evidence"] == []
    applied = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({9999}), stage_only=True,
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    assert applied["applied"] is True
    assert applied["apply_result"] == []
    assert _counts(db_path) == before


# --- Mutual exclusivity with the full-governed path's own flags ---------


def test_stage_only_rejects_candidate_airport_id(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
                candidate_airport_ids=(ids["lcy_id"],),
            )
        )


def test_stage_only_rejects_no_known_candidates(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True, no_known_candidates=True)
        )


# --- Full raw text preserved exactly in preview (Part G: no truncation) -


def test_stage_only_preview_shows_full_untruncated_text(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True)), SENTENCE_1
    )
    preview = run_persist(
        PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], keep_indices=frozenset({idx}), stage_only=True)
    )
    entry = preview["planned_evidence"][0]
    # The persisted text (raw_text) must be an EXACT substring of the real
    # extracted page - never truncated for preview display purposes.
    assert entry["raw_text"] in PAGE_TEXT
    assert SENTENCE_1 in entry["raw_text"]
