"""Validate and atomically convert temporary candidates into Observations.

Dry-run performs no writes or commits. ``execute`` owns commit and rollback so
the approved set is persisted as one transaction. Candidates are never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models import Document, Observation, ObservationType
from app.repositories import ObservationRepository, ObservationTypeRepository


ConfidenceInput = float | int | str | None


@dataclass(frozen=True, slots=True)
class ObservationCandidate:
    observation_type_key: str
    raw_value: str
    normalized_value: Optional[str] = None
    extraction_confidence: ConfidenceInput = None
    evidence_locator: Optional[str] = None
    extraction_method: Optional[str] = None
    extractor_version: Optional[str] = None
    source_record_key: Optional[str] = None


class CandidateStatus(str, Enum):
    APPROVED = "approved"
    CREATED = "created"
    REJECTED_VALIDATION = "rejected_validation"
    REJECTED_UNKNOWN_TYPE = "rejected_unknown_type"
    REJECTED_INACTIVE_TYPE = "rejected_inactive_type"
    SKIPPED_BATCH_DUPLICATE = "skipped_batch_duplicate"
    FAILED_PERSISTENCE = "failed_persistence"


@dataclass(frozen=True, slots=True)
class CandidateError:
    field: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CandidateResult:
    input_index: int
    status: CandidateStatus
    candidate: ObservationCandidate
    errors: tuple[CandidateError, ...] = ()
    warnings: tuple[str, ...] = ()
    possible_persisted_match_ids: tuple[int, ...] = ()
    created_observation_id: Optional[int] = None


@dataclass(frozen=True, slots=True)
class CandidateBatchResult:
    document_id: int
    results: tuple[CandidateResult, ...]
    committed: bool


@dataclass(frozen=True, slots=True)
class _PreparedCandidate:
    candidate: ObservationCandidate
    observation_type: ObservationType
    normalized_value: Optional[str]
    extraction_confidence: Optional[float]
    evidence_locator: Optional[str]
    extraction_method: Optional[str]
    extractor_version: Optional[str]
    source_record_key: Optional[str]

    def comparison_key(self) -> tuple[object, ...]:
        # Exact batch comparison includes every Observation-backed value plus
        # the transient source key. Raw text is deliberately not normalized.
        return (
            self.observation_type.key,
            self.candidate.raw_value,
            self.normalized_value,
            self.extraction_confidence,
            self.evidence_locator,
            self.extraction_method,
            self.extractor_version,
            self.source_record_key,
        )


class ObservationCandidateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def dry_run(
        self, document_id: int, candidates: Sequence[ObservationCandidate]
    ) -> CandidateBatchResult:
        results, _prepared = self._validate(document_id, candidates)
        return CandidateBatchResult(
            document_id=document_id,
            results=results,
            committed=False,
        )

    def execute(
        self, document_id: int, candidates: Sequence[ObservationCandidate]
    ) -> CandidateBatchResult:
        results, prepared = self._validate(document_id, candidates)
        approved_indexes = [
            result.input_index
            for result in results
            if result.status is CandidateStatus.APPROVED
        ]
        if not approved_indexes:
            return CandidateBatchResult(document_id, results, committed=False)

        repository = ObservationRepository(self.session)
        created_ids: dict[int, int] = {}
        try:
            for input_index in approved_indexes:
                item = prepared[input_index]
                observation = repository.create(
                    Observation(
                        document_id=document_id,
                        observation_type_id=item.observation_type.id,
                        raw_value=item.candidate.raw_value,
                        normalized_value=item.normalized_value,
                        extraction_confidence=item.extraction_confidence,
                        evidence_locator=item.evidence_locator,
                        extraction_method=item.extraction_method,
                        extractor_version=item.extractor_version,
                    )
                )
                created_ids[input_index] = observation.id
            self.session.commit()
        except Exception:
            self.session.rollback()
            failure = CandidateError(
                field="persistence",
                code="persistence_failed",
                message="The approved candidate set could not be persisted.",
            )
            failed_results = tuple(
                replace(result, status=CandidateStatus.FAILED_PERSISTENCE, errors=(failure,))
                if result.status is CandidateStatus.APPROVED
                else result
                for result in results
            )
            return CandidateBatchResult(document_id, failed_results, committed=False)

        created_results = tuple(
            replace(
                result,
                status=CandidateStatus.CREATED,
                created_observation_id=created_ids[result.input_index],
            )
            if result.status is CandidateStatus.APPROVED
            else result
            for result in results
        )
        return CandidateBatchResult(document_id, created_results, committed=True)

    def _validate(
        self, document_id: int, candidates: Sequence[ObservationCandidate]
    ) -> tuple[tuple[CandidateResult, ...], dict[int, _PreparedCandidate]]:
        candidate_items = tuple(candidates)
        document = self.session.get(Document, document_id)
        if document is None:
            error = CandidateError(
                field="document",
                code="document_not_found",
                message="The target Document does not exist.",
            )
            return (
                tuple(
                    CandidateResult(
                        input_index=index,
                        status=CandidateStatus.REJECTED_VALIDATION,
                        candidate=candidate,
                        errors=(error,),
                    )
                    for index, candidate in enumerate(candidate_items)
                ),
                {},
            )

        type_repository = ObservationTypeRepository(self.session)
        type_cache: dict[str, Optional[ObservationType]] = {}
        results: list[CandidateResult] = []
        prepared: dict[int, _PreparedCandidate] = {}
        seen: set[tuple[object, ...]] = set()

        for index, candidate in enumerate(candidate_items):
            result, item = self._validate_candidate(
                index, candidate, type_repository, type_cache
            )
            if item is not None:
                comparison_key = item.comparison_key()
                if comparison_key in seen:
                    result = replace(
                        result,
                        status=CandidateStatus.SKIPPED_BATCH_DUPLICATE,
                    )
                else:
                    seen.add(comparison_key)
                    prepared[index] = item
            results.append(result)

        return tuple(results), prepared

    @staticmethod
    def _validate_candidate(
        index: int,
        candidate: ObservationCandidate,
        type_repository: ObservationTypeRepository,
        type_cache: dict[str, Optional[ObservationType]],
    ) -> tuple[CandidateResult, Optional[_PreparedCandidate]]:
        errors: list[CandidateError] = []

        if not isinstance(candidate.observation_type_key, str) or not candidate.observation_type_key:
            observation_type = None
            type_status = CandidateStatus.REJECTED_UNKNOWN_TYPE
            errors.append(
                CandidateError(
                    "observation_type_key",
                    "observation_type_unknown",
                    "ObservationType key is missing or unknown.",
                )
            )
        else:
            if candidate.observation_type_key not in type_cache:
                type_cache[candidate.observation_type_key] = type_repository.get_by_key(
                    candidate.observation_type_key
                )
            observation_type = type_cache[candidate.observation_type_key]
            if observation_type is None:
                type_status = CandidateStatus.REJECTED_UNKNOWN_TYPE
                errors.append(
                    CandidateError(
                        "observation_type_key",
                        "observation_type_unknown",
                        "ObservationType key is unknown.",
                    )
                )
            elif not observation_type.active:
                type_status = CandidateStatus.REJECTED_INACTIVE_TYPE
                errors.append(
                    CandidateError(
                        "observation_type_key",
                        "observation_type_inactive",
                        "ObservationType is inactive.",
                    )
                )
            else:
                type_status = CandidateStatus.APPROVED

        if not isinstance(candidate.raw_value, str) or not candidate.raw_value.strip():
            errors.append(
                CandidateError(
                    "raw_value",
                    "raw_value_required",
                    "Raw value must contain non-whitespace text.",
                )
            )

        optional_values: dict[str, Optional[str]] = {}
        for field_name in (
            "normalized_value",
            "evidence_locator",
            "extraction_method",
            "extractor_version",
            "source_record_key",
        ):
            value = getattr(candidate, field_name)
            if value is not None and not isinstance(value, str):
                errors.append(
                    CandidateError(
                        field_name,
                        "optional_text_invalid",
                        f"{field_name} must be text or null.",
                    )
                )
                optional_values[field_name] = None
            else:
                optional_values[field_name] = value if value and value.strip() else None

        confidence, confidence_error = _validate_confidence(
            candidate.extraction_confidence
        )
        if confidence_error is not None:
            errors.append(confidence_error)

        if errors:
            status = (
                type_status
                if type_status
                in {
                    CandidateStatus.REJECTED_UNKNOWN_TYPE,
                    CandidateStatus.REJECTED_INACTIVE_TYPE,
                }
                else CandidateStatus.REJECTED_VALIDATION
            )
            return CandidateResult(index, status, candidate, tuple(errors)), None

        assert observation_type is not None
        item = _PreparedCandidate(
            candidate=candidate,
            observation_type=observation_type,
            normalized_value=optional_values["normalized_value"],
            extraction_confidence=confidence,
            evidence_locator=optional_values["evidence_locator"],
            extraction_method=optional_values["extraction_method"],
            extractor_version=optional_values["extractor_version"],
            source_record_key=optional_values["source_record_key"],
        )
        return CandidateResult(index, CandidateStatus.APPROVED, candidate), item


def _validate_confidence(
    value: ConfidenceInput,
) -> tuple[Optional[float], Optional[CandidateError]]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, CandidateError(
            "extraction_confidence",
            "confidence_invalid",
            "Extraction confidence must be a finite number.",
        )
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None, CandidateError(
            "extraction_confidence",
            "confidence_invalid",
            "Extraction confidence must be a finite number.",
        )
    if not math.isfinite(confidence):
        return None, CandidateError(
            "extraction_confidence",
            "confidence_invalid",
            "Extraction confidence must be a finite number.",
        )
    if not 0.0 <= confidence <= 1.0:
        return None, CandidateError(
            "extraction_confidence",
            "confidence_out_of_range",
            "Extraction confidence must be between 0.0 and 1.0.",
        )
    return confidence, None
