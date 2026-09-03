"""RWI Mission #26D - offline tests for scripts/persist_selected_fragments.py's
--known-airport-id mode (app.services.known_airport_evidence_persistence).

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
from scripts.persist_selected_fragments import PersistConfig, PersistRunnerError, run_persist
from tests.test_persist_selected_fragments import PAGE_TEXT, SENTENCE_1, _fragment_index, _seed

_S1_START = PAGE_TEXT.index(SENTENCE_1)
_S1_END = _S1_START + len(SENTENCE_1)


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


def _airport_row(db_path: str, airport_id: int) -> Airport:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        return session.get(Airport, airport_id)


# --- 1-5, 9-11: known Airport + valid fragment -> SourceAssertion staged,
# exact field values, Airport untouched ------------------------------------


def test_known_airport_apply_field_values_exact(seeded_db):
    db_path, ids = seeded_db
    before_airport = _airport_row(db_path, ids["lcy_id"])
    assert before_airport.latitude is None and before_airport.longitude is None

    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    assert preview["mode"] == "known_airport"
    assert preview["known_airport"] == {
        "airport_id": ids["lcy_id"], "name": "London City Airport", "iata_code": "LCY",
        "icao_code": "EGLC", "country": "United Kingdom",
    }
    applied = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True,
            expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    assert applied["applied"] is True
    assert applied["blockers"] == []

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion).order_by(SourceAssertion.id.desc()))
        assert assertion.airport_id == ids["lcy_id"]  # 2
        assert assertion.assertion_type == "airport_inventory"  # 3
        assert assertion.unknown_airport_candidate_id is None  # 4
        assert assertion.runway_id is None  # 5
        assert assertion.evidence_quality == "unverified_candidate"
        assert assertion.review_state == "unreviewed"
        assert assertion.identity_guard_decision is None
        assert assertion.identity_guard_reason is None
        assert assertion.intelligence_review_decision is None
        assert assertion.promotion_policy_decision is None
        assert SENTENCE_1 in assertion.raw_relevant_text
        assert assertion.raw_fragment_hash == hashlib.sha256(assertion.raw_relevant_text.encode("utf-8")).hexdigest()

    after_airport = _airport_row(db_path, ids["lcy_id"])
    assert after_airport.name == before_airport.name  # 9
    assert after_airport.iata_code == before_airport.iata_code
    assert after_airport.icao_code == before_airport.icao_code
    assert after_airport.country == before_airport.country
    assert after_airport.latitude is None  # 10
    assert after_airport.longitude is None  # 11


# --- 6-8: no EvidenceBag / UAC / ReviewerAction ----------------------------


def test_known_airport_apply_no_evidence_bag_uac_reviewer_action(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True,
            expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    after = _counts(db_path)
    assert after["SourceAssertionEvidenceBag"] == before["SourceAssertionEvidenceBag"]
    assert after["UnknownAirportCandidate"] == before["UnknownAirportCandidate"]
    assert after["ReviewerAction"] == before["ReviewerAction"]
    assert after["Runway"] == before["Runway"]
    assert after["Installation"] == before["Installation"]
    assert after["Signal"] == before["Signal"]
    assert after["Airport"] == before["Airport"]  # no Airport created
    assert after["Source"] == before["Source"] + 1
    assert after["SourceAssertion"] == before["SourceAssertion"] + 1


# --- 12: unknown Airport ID fails closed -----------------------------------


def test_known_airport_unknown_id_fails_closed(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    before_sha = _db_sha(db_path)
    with pytest.raises(PersistRunnerError):
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=999999))
    assert _counts(db_path) == before
    assert _db_sha(db_path) == before_sha


# --- 13: identical replay is idempotent ------------------------------------


def test_known_airport_replay_idempotent(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    fingerprint = preview["plan_fingerprint"]
    first = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True, expected_fingerprint=fingerprint,
        )
    )
    after_first = _counts(db_path)

    preview2 = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    assert preview2["plan_fingerprint"] == fingerprint
    second = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True, expected_fingerprint=fingerprint,
        )
    )
    after_second = _counts(db_path)

    assert after_second == after_first
    assert second["apply_result"][0]["source_assertion_id"] == first["apply_result"][0]["source_assertion_id"]
    assert second["apply_result"][0]["source_assertion_created"] is False
    assert second["apply_result"][0]["source_created"] is False


# --- 14: different Airport ID must NOT silently reuse another Airport's
# assertion for the SAME exact fragment content ------------------------------


def test_known_airport_conflicting_airport_id_not_silently_reused(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    preview1 = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True,
            expected_fingerprint=preview1["plan_fingerprint"],
        )
    )
    before = _counts(db_path)

    # SAME exact fragment (same document, same locator/hash), different
    # known_airport_id (ytz instead of lcy).
    preview2 = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["ytz_id"],
            keep_indices=frozenset({idx}),
        )
    )
    assert preview2["planned_evidence"][0]["conflict"] is not None
    assert preview2["planned_evidence"][0]["source_assertion_would_be_created"] is False

    applied2 = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["ytz_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True,
            expected_fingerprint=preview2["plan_fingerprint"],
        )
    )
    assert applied2["applied"] is False
    assert any("AMBIGUOUS_PROVENANCE_CONFLICT" in b for b in applied2["blockers"])
    assert _counts(db_path) == before  # no new/mutated row


# --- 15: manual exact-range fragment works through the new seam -----------


def test_known_airport_manual_range_apply_succeeds(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END, keep_indices=frozenset({1}),
        )
    )
    assert preview["fragments_kept"] == 1
    applied = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            manual_page=1, manual_start_char=_S1_START, manual_end_char=_S1_END, keep_indices=frozenset({1}),
            apply=True, allow_database_write=True, expected_fingerprint=preview["plan_fingerprint"],
        )
    )
    assert applied["applied"] is True
    assert applied["blockers"] == []
    after = _counts(db_path)
    assert after["Source"] == before["Source"] + 1
    assert after["SourceAssertion"] == before["SourceAssertion"] + 1

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion).order_by(SourceAssertion.id.desc()))
        assert assertion.raw_relevant_text == SENTENCE_1
        assert assertion.source_locator == f"page:1;chars:{_S1_START}-{_S1_END}"
        assert assertion.airport_id == ids["lcy_id"]
        assert assertion.assertion_type == "airport_inventory"


# --- Preview writes nothing -------------------------------------------------


def test_known_airport_preview_writes_nothing(seeded_db):
    db_path, ids = seeded_db
    before_sha = _db_sha(db_path)
    before_counts = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    assert _db_sha(db_path) == before_sha
    assert _counts(db_path) == before_counts


# --- Safety gates ------------------------------------------------------------


def test_known_airport_missing_write_authorization_raises(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
                keep_indices=frozenset({idx}), apply=True, allow_database_write=False,
            )
        )
    assert _counts(db_path) == before


def test_known_airport_wrong_fingerprint_no_write(seeded_db):
    db_path, ids = seeded_db
    before = _counts(db_path)
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    result = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}), apply=True, allow_database_write=True,
            expected_fingerprint="deadbeef" * 8,
        )
    )
    assert result["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in result["blockers"])
    assert _counts(db_path) == before


def test_known_airport_assertion_type_not_allowlisted_rejected(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
                known_airport_assertion_type="project_construction", keep_indices=frozenset({idx}),
            )
        )


# --- Mutual exclusivity with the other two modes' own flags ----------------


def test_known_airport_rejects_stage_only_combination(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"], stage_only=True,
            )
        )


def test_known_airport_rejects_candidate_airport_id(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
                candidate_airport_ids=(ids["ytz_id"],),
            )
        )


def test_known_airport_rejects_no_known_candidates(seeded_db):
    db_path, ids = seeded_db
    with pytest.raises(PersistRunnerError):
        run_persist(
            PersistConfig(
                database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
                no_known_candidates=True,
            )
        )


# --- Full raw text preserved exactly in preview -----------------------------


def test_known_airport_preview_shows_full_untruncated_text(seeded_db):
    db_path, ids = seeded_db
    idx = _fragment_index(
        run_persist(PersistConfig(database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"])),
        SENTENCE_1,
    )
    preview = run_persist(
        PersistConfig(
            database=db_path, snapshot_id=ids["snapshot_id"], known_airport_id=ids["lcy_id"],
            keep_indices=frozenset({idx}),
        )
    )
    entry = preview["planned_evidence"][0]
    assert entry["raw_text"] in PAGE_TEXT
    assert SENTENCE_1 in entry["raw_text"]
    assert entry["airport_id"] == ids["lcy_id"]
    assert entry["assertion_type"] == "airport_inventory"
