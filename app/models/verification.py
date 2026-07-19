from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Enum as SqlEnum, Float, ForeignKey, String, Text, event, inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class VerificationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNDECIDED = "undecided"


class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_verifications_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[VerificationStatus] = mapped_column(
        SqlEnum(
            VerificationStatus,
            name="verification_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            validate_strings=True,
        )
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(200))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    observation: Mapped["Observation"] = relationship(back_populates="verifications")


@event.listens_for(Verification, "before_update")
def _prevent_verification_update(_mapper, _connection, target: Verification) -> None:
    state = inspect(target)
    if any(
        state.attrs[column.key].history.has_changes()
        for column in state.mapper.column_attrs
    ):
        raise ValueError("Verification is immutable after creation")


@event.listens_for(Verification, "before_delete")
def _prevent_verification_delete(_mapper, _connection, _target: Verification) -> None:
    raise ValueError("Verification cannot be deleted; create another review")
