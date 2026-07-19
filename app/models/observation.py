from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, event, inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0.0 AND extraction_confidence <= 1.0)",
            name="ck_observations_extraction_confidence",
        ),
        CheckConstraint(
            "supersedes_observation_id IS NULL OR supersedes_observation_id != id",
            name="ck_observations_not_self_superseding",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), index=True)
    observation_type_id: Mapped[int] = mapped_column(ForeignKey("observation_types.id", ondelete="RESTRICT"), index=True)
    raw_value: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text)
    extraction_confidence: Mapped[Optional[float]] = mapped_column(Float)
    evidence_locator: Mapped[Optional[str]] = mapped_column(Text)
    extraction_method: Mapped[Optional[str]] = mapped_column(String(50))
    extractor_version: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    supersedes_observation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("observations.id", ondelete="RESTRICT"), index=True)

    document: Mapped["Document"] = relationship(back_populates="observations")
    observation_type: Mapped["ObservationType"] = relationship(back_populates="observations")
    supersedes: Mapped[Optional["Observation"]] = relationship(
        back_populates="superseded_by", foreign_keys=[supersedes_observation_id], remote_side=[id]
    )
    superseded_by: Mapped[list["Observation"]] = relationship(
        back_populates="supersedes", foreign_keys=[supersedes_observation_id], passive_deletes="all"
    )


@event.listens_for(Observation, "before_update")
def _prevent_observation_update(_mapper, _connection, target: Observation) -> None:
    state = inspect(target)
    if any(state.attrs[column.key].history.has_changes() for column in state.mapper.column_attrs):
        raise ValueError("Observation is immutable after creation")


@event.listens_for(Observation, "before_delete")
def _prevent_observation_delete(_mapper, _connection, _target: Observation) -> None:
    raise ValueError("Observation cannot be deleted; create a superseding observation")
