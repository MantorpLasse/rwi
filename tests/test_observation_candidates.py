from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, Observation, ObservationType, PublishingSource
from app.repositories import ObservationRepository
from app.services import (
    CandidateStatus,
    ObservationCandidate,
    ObservationCandidateService,
)


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        test_engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


def foundation(session: Session):
    document = Document(source=PublishingSource(name="FAA"), title="EMAS map")
    other_document = Document(
        source=PublishingSource(name="Other"), title="Other evidence"
    )
    active = ObservationType(
        key="airport.emas.product",
        display_label="Product",
        description="Product claim",
        value_type="raw_text",
    )
    second = ObservationType(
        key="airport.emas.installation_year_display",
        display_label="Year display",
        description="Year display claim",
        value_type="raw_text",
    )
    inactive = ObservationType(
        key="airport.emas.inactive",
        display_label="Inactive",
        description="Inactive claim",
        value_type="raw_text",
        active=False,
    )
    session.add_all([document, other_document, active, second, inactive])
    session.commit()
    return document, other_document, active, second, inactive


def candidate(**values):
    defaults = {
        "observation_type_key": "airport.emas.product",
        "raw_value": "  EMASMAX\nsource line  ",
    }
    defaults.update(values)
    return ObservationCandidate(**defaults)


def result_for(session, document_id, item, execute=False):
    service = ObservationCandidateService(session)
    batch = service.execute(document_id, [item]) if execute else service.dry_run(document_id, [item])
    return batch.results[0]


def test_candidate_is_typed_frozen_and_supports_optional_fields():
    item = candidate(
        normalized_value="EMASMAX",
        extraction_confidence=0.8,
        evidence_locator="marker 1",
        extraction_method="table parser",
        extractor_version="v1",
        source_record_key="row-1",
    )
    assert item.observation_type_key == "airport.emas.product"
    assert item.source_record_key == "row-1"
    with pytest.raises(FrozenInstanceError):
        item.raw_value = "changed"
    with pytest.raises(TypeError):
        ObservationCandidate(
            observation_type_key="airport.emas.product",
            raw_value="value",
            document_id=99,
        )


@pytest.mark.parametrize("raw_value", [None, "", "  \n "])
def test_missing_empty_or_whitespace_raw_value_is_rejected(engine, raw_value):
    with Session(engine) as session:
        document, *_ = foundation(session)
        result = result_for(session, document.id, candidate(raw_value=raw_value))
        assert result.status is CandidateStatus.REJECTED_VALIDATION
        assert "raw_value_required" in {error.code for error in result.errors}


def test_unknown_and_inactive_types_are_distinguished(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        batch = ObservationCandidateService(session).dry_run(
            document.id,
            [
                candidate(observation_type_key="unknown.type"),
                candidate(observation_type_key="airport.emas.inactive"),
            ],
        )
        assert [item.status for item in batch.results] == [
            CandidateStatus.REJECTED_UNKNOWN_TYPE,
            CandidateStatus.REJECTED_INACTIVE_TYPE,
        ]
        assert [item.errors[0].code for item in batch.results] == [
            "observation_type_unknown",
            "observation_type_inactive",
        ]


@pytest.mark.parametrize("confidence", [None, "", "  ", 0.0, 0.5, "0.75", 1.0])
def test_nullable_and_in_range_confidence_is_approved(engine, confidence):
    with Session(engine) as session:
        document, *_ = foundation(session)
        result = result_for(
            session,
            document.id,
            candidate(extraction_confidence=confidence),
        )
        assert result.status is CandidateStatus.APPROVED


@pytest.mark.parametrize(
    ("confidence", "code"),
    [
        (-0.01, "confidence_out_of_range"),
        (1.01, "confidence_out_of_range"),
        (float("nan"), "confidence_invalid"),
        (float("inf"), "confidence_invalid"),
        (float("-inf"), "confidence_invalid"),
        (True, "confidence_invalid"),
        (False, "confidence_invalid"),
        ("malformed", "confidence_invalid"),
    ],
)
def test_invalid_confidence_is_rejected(engine, confidence, code):
    with Session(engine) as session:
        document, *_ = foundation(session)
        result = result_for(
            session,
            document.id,
            candidate(extraction_confidence=confidence),
        )
        assert result.status is CandidateStatus.REJECTED_VALIDATION
        assert code in {error.code for error in result.errors}


def test_missing_document_rejects_every_input_without_persistence(engine):
    with Session(engine) as session:
        foundation(session)
        batch = ObservationCandidateService(session).execute(
            999999, [candidate(), candidate(raw_value="another")]
        )
        assert [item.input_index for item in batch.results] == [0, 1]
        assert all(item.errors[0].code == "document_not_found" for item in batch.results)
        assert session.scalar(select(func.count(Observation.id))) == 0


def test_exact_batch_duplicates_are_deterministic_and_only_first_is_approved(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        items = [candidate(), candidate(), candidate(raw_value="different")]
        service = ObservationCandidateService(session)
        first = service.dry_run(document.id, items)
        second = service.dry_run(document.id, items)
        expected = [
            CandidateStatus.APPROVED,
            CandidateStatus.SKIPPED_BATCH_DUPLICATE,
            CandidateStatus.APPROVED,
        ]
        assert [item.status for item in first.results] == expected
        assert [item.status for item in second.results] == expected
        assert [item.input_index for item in first.results] == [0, 1, 2]


def test_same_raw_with_different_type_or_locator_is_not_collapsed(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        batch = ObservationCandidateService(session).dry_run(
            document.id,
            [
                candidate(),
                candidate(observation_type_key="airport.emas.installation_year_display"),
                candidate(evidence_locator="different marker"),
            ],
        )
        assert all(item.status is CandidateStatus.APPROVED for item in batch.results)


def test_invalid_candidate_does_not_enter_duplicate_seen_set(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        batch = ObservationCandidateService(session).dry_run(
            document.id,
            [candidate(extraction_confidence=True), candidate(extraction_confidence=None)],
        )
        assert [item.status for item in batch.results] == [
            CandidateStatus.REJECTED_VALIDATION,
            CandidateStatus.APPROVED,
        ]


def test_empty_optional_text_normalizes_to_null_on_creation(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        result = result_for(
            session,
            document.id,
            candidate(
                normalized_value="",
                evidence_locator=" ",
                extraction_method="",
                extractor_version="\n",
                source_record_key="",
            ),
            execute=True,
        )
        assert result.status is CandidateStatus.CREATED
        item = session.get(Observation, result.created_observation_id)
        assert item.normalized_value is None
        assert item.evidence_locator is None
        assert item.extraction_method is None
        assert item.extractor_version is None


def test_dry_run_never_writes_commits_or_mutates_inputs(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        commits = []
        event.listen(session, "after_commit", lambda _session: commits.append(True))
        item = candidate(normalized_value="candidate")
        before = repr(item)
        first = ObservationCandidateService(session).dry_run(document.id, [item])
        second = ObservationCandidateService(session).dry_run(document.id, [item])
        assert first == second
        assert first.committed is False
        assert commits == []
        assert repr(item) == before
        assert session.scalar(select(func.count(Observation.id))) == 0


def test_execution_maps_fields_uses_repository_and_commits_atomically(engine, monkeypatch):
    with Session(engine) as session:
        document, other_document, *_ = foundation(session)
        calls = []
        original_create = ObservationRepository.create

        def tracking_create(repository, observation):
            calls.append(observation)
            return original_create(repository, observation)

        monkeypatch.setattr(ObservationRepository, "create", tracking_create)
        items = [
            candidate(
                normalized_value="EMASMAX",
                extraction_confidence="0.9",
                evidence_locator="marker 8",
                extraction_method="manual parser",
                extractor_version="v2",
                source_record_key="transient-row",
            ),
            candidate(raw_value="second claim"),
        ]
        batch = ObservationCandidateService(session).execute(document.id, items)

        assert batch.committed is True
        assert [item.status for item in batch.results] == [
            CandidateStatus.CREATED,
            CandidateStatus.CREATED,
        ]
        assert len(calls) == 2
        stored = session.scalars(select(Observation).order_by(Observation.id)).all()
        assert [item.id for item in stored] == [
            result.created_observation_id for result in batch.results
        ]
        assert all(item.document_id == document.id for item in stored)
        assert all(item.document_id != other_document.id for item in stored)
        assert stored[0].raw_value == "  EMASMAX\nsource line  "
        assert stored[0].normalized_value == "EMASMAX"
        assert stored[0].extraction_confidence == 0.9
        assert stored[0].evidence_locator == "marker 8"
        assert stored[0].extraction_method == "manual parser"
        assert stored[0].extractor_version == "v2"
        assert not hasattr(stored[0], "source_record_key")


def test_validation_failures_and_duplicates_do_not_block_valid_creation(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        repeated = candidate(raw_value="created once")
        batch = ObservationCandidateService(session).execute(
            document.id,
            [
                candidate(raw_value=""),
                candidate(observation_type_key="unknown"),
                candidate(observation_type_key="airport.emas.inactive"),
                repeated,
                repeated,
            ],
        )
        assert [item.status for item in batch.results] == [
            CandidateStatus.REJECTED_VALIDATION,
            CandidateStatus.REJECTED_UNKNOWN_TYPE,
            CandidateStatus.REJECTED_INACTIVE_TYPE,
            CandidateStatus.CREATED,
            CandidateStatus.SKIPPED_BATCH_DUPLICATE,
        ]
        assert session.scalar(select(func.count(Observation.id))) == 1


def test_persistence_failure_rolls_back_all_and_returns_safe_results(engine, monkeypatch):
    with Session(engine) as session:
        document, *_ = foundation(session)
        original_create = ObservationRepository.create
        calls = 0

        def fail_second(repository, observation):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("SECRET INTERNAL TRACE DETAIL")
            return original_create(repository, observation)

        monkeypatch.setattr(ObservationRepository, "create", fail_second)
        batch = ObservationCandidateService(session).execute(
            document.id,
            [candidate(raw_value="first"), candidate(raw_value="second")],
        )

        assert batch.committed is False
        assert all(
            item.status is CandidateStatus.FAILED_PERSISTENCE
            for item in batch.results
        )
        assert all(item.created_observation_id is None for item in batch.results)
        assert all(item.errors[0].code == "persistence_failed" for item in batch.results)
        assert "SECRET" not in " ".join(
            error.message for item in batch.results for error in item.errors
        )
        assert session.scalar(select(func.count(Observation.id))) == 0


def test_created_observation_remains_immutable_and_no_candidate_table_exists(engine):
    with Session(engine) as session:
        document, *_ = foundation(session)
        result = result_for(session, document.id, candidate(), execute=True)
        item = session.get(Observation, result.created_observation_id)
        item.raw_value = "changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    assert "observation_candidates" not in inspect(engine).get_table_names()
