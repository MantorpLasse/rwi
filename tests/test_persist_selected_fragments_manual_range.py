"""RWI Mission #25J2 - offline integration tests for
scripts/persist_selected_fragments.py's --manual-page/--manual-start-char/
--manual-end-char CLI, combined with #25J1's --stage-only mode.

Every test builds its own isolated temp-file SQLite database; no network,
no LLM, never touches data/runway_safe.db. Reuses
tests/test_persist_selected_fragments.py's own established seeding
conventions (PAGE_TEXT contains real target sentences at known offsets)."""

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
from scripts.persist_selected_fragments import PersistConfig, PersistRunnerError, run_persist
from tests.test_persist_selected_fragments import PAGE_TEXT, SENTENCE_1, _seed


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


_S1_START = PAGE_TEXT.index(SENTENCE_1)
_S1_END = _S1_START + len(SENTENCE_1)


# --- 14: partial CLI manual args rejected --------------------------------


def test_partial_manual_args_rejected(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True, manual_page=1))


# --- 15: manual preview performs zero writes -----------------------------


def test_manual_preview_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before_sha = _db_sha(db_path)
    before_counts = _counts(db_path)

    report = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
        )
    )
    assert report["fragments_selected"] == 1
    assert report["fragments_preview"][0]["text"] == SENTENCE_1

    assert _db_sha(db_path) == before_sha
    assert _counts(db_path) == before_counts


# --- 16: manual apply without explicit KEEP writes nothing --------------


def test_manual_apply_without_keep_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
        )
    )
    applied = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    # No --keep given -> zero fragments kept -> nothing to apply.
    assert applied["fragments_kept"] == 0
    assert applied["apply_result"] == []
    assert _counts(db_path) == before


# --- 17: manual full-governed apply rejected -----------------------------


def test_manual_full_governed_apply_rejected(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"],
                manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
                candidate_airport_ids=(ids["lcy_id"],),
            )
        )


def test_manual_wrong_keep_index_rejected(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
                manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
                keep_indices=frozenset({2}),
            )
        )


# --- 18: manual stage-only apply succeeds --------------------------------


def test_manual_stage_only_apply_succeeds(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
            keep_indices=frozenset({1}),
        )
    )
    assert preview["fragments_kept"] == 1
    fingerprint = preview["plan_fingerprint"]

    applied = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END,
            keep_indices=frozenset({1}), apply=True, allow_database_write=True, expected_fingerprint=fingerprint,
        )
    )
    assert applied["applied"] is True
    assert applied["blockers"] == []
    assert len(applied["apply_result"]) == 1

    after = _counts(db_path)
    assert after["Source"] == before["Source"] + 1
    assert after["SourceAssertion"] == before["SourceAssertion"] + 1

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion).order_by(SourceAssertion.id.desc()))
        assert assertion.raw_relevant_text == SENTENCE_1  # exact, not a superset/paraphrase
        assert assertion.source_locator == f"page:1;chars:{_S1_START}-{_S1_END}"
        assert assertion.assertion_type == "project_construction"
        assert assertion.evidence_quality == "unverified_candidate"
        assert assertion.review_state == "unreviewed"
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id is None
        assert assertion.identity_guard_decision is None
        assert assertion.intelligence_review_decision is None
        assert assertion.promotion_policy_decision is None


# --- 19: replay is idempotent --------------------------------------------


def test_manual_stage_only_replay_idempotent(seeded_db):
    db_path, ids = seeded_db
    cfg_kwargs = dict(
        database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
        manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END, keep_indices=frozenset({1}),
    )
    preview = run_persist(PersistConfig(**cfg_kwargs))
    fingerprint = preview["plan_fingerprint"]
    first = run_persist(
        PersistConfig(**cfg_kwargs, apply=True, allow_database_write=True, expected_fingerprint=fingerprint)
    )
    after_first = _counts(db_path)

    preview2 = run_persist(PersistConfig(**cfg_kwargs))
    assert preview2["plan_fingerprint"] == fingerprint
    second = run_persist(
        PersistConfig(**cfg_kwargs, apply=True, allow_database_write=True, expected_fingerprint=fingerprint)
    )
    after_second = _counts(db_path)

    assert after_second == after_first
    assert second["apply_result"][0]["source_assertion_id"] == first["apply_result"][0]["source_assertion_id"]
    assert second["apply_result"][0]["source_assertion_created"] is False
    assert second["apply_result"][0]["source_created"] is False


# --- Integration safety: no governance state, machine Selection unaffected


def test_manual_stage_only_no_governance_state_created(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END, keep_indices=frozenset({1}),
        )
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True,
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END, keep_indices=frozenset({1}),
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    after = _counts(db_path)
    assert after["SourceAssertionEvidenceBag"] == before["SourceAssertionEvidenceBag"]
    assert after["UnknownAirportCandidate"] == before["UnknownAirportCandidate"]
    assert after["ReviewerAction"] == before["ReviewerAction"]
    assert after["Airport"] == before["Airport"]
    assert after["Runway"] == before["Runway"]
    assert after["Installation"] == before["Installation"]
    assert after["Signal"] == before["Signal"]


def test_machine_selection_still_default_without_manual_args(seeded_db):
    """Manual-range support must not break normal Selection behavior -
    the exact same regression #25J1's own tests already prove, reconfirmed
    here as a direct contrast against the manual-mode tests above."""
    db_path, ids = seeded_db
    report = run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], stage_only=True))
    assert report["manual_range"] is None
    assert report["fragments_selected"] > 1  # normal machine Selection finds multiple real fragments
