"""Governed derivation of immutable Intelligence from current accepted Facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Fact, FactStatus, Intelligence, IntelligenceStatus
from app.repositories import (
    FactRepository,
    FindingTypeRepository,
    IntelligenceRepository,
)


@dataclass(frozen=True, slots=True)
class IntelligenceDerivationError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class IntelligenceDerivationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def derive(
        self,
        finding_type_key: str,
        fact_ids: Sequence[int],
        *,
        title: str,
        summary: str,
    ) -> Intelligence:
        try:
            ids = tuple(fact_ids)
            self._validate_request(ids, title, summary)

            finding_type = FindingTypeRepository(self.session).get_by_key(
                finding_type_key
            )
            if finding_type is None:
                raise IntelligenceDerivationError(
                    "finding_type_not_found",
                    f"FindingType {finding_type_key!r} does not exist.",
                )
            if not finding_type.is_active:
                raise IntelligenceDerivationError(
                    "finding_type_inactive",
                    f"FindingType {finding_type_key!r} is inactive.",
                )

            fact_repository = FactRepository(self.session)
            facts = self._resolve_facts(fact_repository, ids)
            current_ids = {fact.id for fact in fact_repository.list_current()}
            for fact in facts:
                if fact.status is not FactStatus.ACCEPTED:
                    raise IntelligenceDerivationError(
                        "fact_not_accepted",
                        f"Fact {fact.id} is not accepted.",
                    )
                if fact.id not in current_ids:
                    raise IntelligenceDerivationError(
                        "fact_not_current",
                        f"Fact {fact.id} is not current.",
                    )

            intelligence = Intelligence(
                finding_type=finding_type,
                title=title,
                summary=summary,
                status=IntelligenceStatus.ACTIVE,
                derived_at=datetime.now(UTC),
                supporting_facts=facts,
            )
            IntelligenceRepository(self.session).create(intelligence)
            self.session.commit()
            return intelligence
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _validate_request(
        fact_ids: tuple[int, ...], title: str, summary: str
    ) -> None:
        if not fact_ids:
            raise IntelligenceDerivationError(
                "fact_ids_required",
                "At least one Fact ID is required.",
            )
        if len(set(fact_ids)) != len(fact_ids):
            raise IntelligenceDerivationError(
                "duplicate_fact_ids",
                "Fact IDs must be unique.",
            )
        for field, value in (("title", title), ("summary", summary)):
            if not isinstance(value, str) or not value.strip():
                raise IntelligenceDerivationError(
                    f"{field}_required",
                    f"{field} must contain non-whitespace text.",
                )

    @staticmethod
    def _resolve_facts(
        repository: FactRepository, fact_ids: Sequence[int]
    ) -> list[Fact]:
        facts: list[Fact] = []
        for fact_id in fact_ids:
            fact = repository.get_by_id(fact_id)
            if fact is None:
                raise IntelligenceDerivationError(
                    "fact_not_found",
                    f"Fact {fact_id} does not exist.",
                )
            facts.append(fact)
        return facts
