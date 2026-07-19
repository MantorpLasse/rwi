"""Governed creation of one immutable Fact from accepted Verifications.

The service owns commit and rollback for promotion. Subject identity and the
accepted value are explicit inputs because they are not owned by Verification.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models import Fact, FactStatus, Verification, VerificationStatus
from app.repositories import FactRepository, VerificationRepository


@dataclass(frozen=True, slots=True)
class FactPromotionError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class FactPromotionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def promote(
        self,
        verification_ids: Sequence[int],
        *,
        subject_type: str,
        subject_identifier: str,
        accepted_value: str,
        valid_from: date | None = None,
        valid_to: date | None = None,
    ) -> Fact:
        ids = tuple(verification_ids)
        self._validate_request(ids, subject_type, subject_identifier, accepted_value)

        verification_repository = VerificationRepository(self.session)
        verifications: list[Verification] = []
        for verification_id in ids:
            verification = verification_repository.get_by_id(verification_id)
            if verification is None:
                raise FactPromotionError(
                    "verification_not_found",
                    f"Verification {verification_id} does not exist.",
                )
            if verification.status is not VerificationStatus.ACCEPTED:
                raise FactPromotionError(
                    "verification_not_accepted",
                    f"Verification {verification_id} is not accepted.",
                )
            verifications.append(verification)

        fact_type_key, reviewed_value = self._validate_atomic_support(verifications)
        if accepted_value != reviewed_value:
            raise FactPromotionError(
                "accepted_value_mismatch",
                "Accepted value does not match the reviewed Observation value.",
            )

        fact = Fact(
            fact_type_key=fact_type_key,
            subject_type=subject_type,
            subject_identifier=subject_identifier,
            accepted_value=accepted_value,
            valid_from=valid_from,
            valid_to=valid_to,
            status=FactStatus.ACCEPTED,
            supporting_verifications=verifications,
        )
        try:
            FactRepository(self.session).create(fact)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return fact

    @staticmethod
    def _validate_request(
        verification_ids: tuple[int, ...],
        subject_type: str,
        subject_identifier: str,
        accepted_value: str,
    ) -> None:
        if not verification_ids:
            raise FactPromotionError(
                "verification_ids_required",
                "At least one Verification ID is required.",
            )
        if len(set(verification_ids)) != len(verification_ids):
            raise FactPromotionError(
                "duplicate_verification_ids",
                "Verification IDs must be unique.",
            )
        for field, value in (
            ("subject_type", subject_type),
            ("subject_identifier", subject_identifier),
            ("accepted_value", accepted_value),
        ):
            if not isinstance(value, str) or not value.strip():
                raise FactPromotionError(
                    f"{field}_required",
                    f"{field} must contain non-whitespace text.",
                )

    @staticmethod
    def _validate_atomic_support(
        verifications: Sequence[Verification],
    ) -> tuple[str, str]:
        type_keys = {
            verification.observation.observation_type.key
            for verification in verifications
        }
        if len(type_keys) != 1:
            raise FactPromotionError(
                "conflicting_verification_types",
                "Verifications do not support one Fact type.",
            )

        reviewed_values = {
            verification.observation.normalized_value
            if verification.observation.normalized_value is not None
            else verification.observation.raw_value
            for verification in verifications
        }
        if len(reviewed_values) != 1:
            raise FactPromotionError(
                "conflicting_verification_values",
                "Verifications contain conflicting Observation values.",
            )
        return next(iter(type_keys)), next(iter(reviewed_values))
