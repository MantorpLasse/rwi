from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.verification import VerificationStatus


class FactStatus(str, Enum):
    ACCEPTED = "accepted"
    RETIRED = "retired"


fact_verifications = Table(
    "fact_verifications",
    Base.metadata,
    Column("fact_id", ForeignKey("facts.id", ondelete="RESTRICT"), primary_key=True),
    Column(
        "verification_id",
        ForeignKey("verifications.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_facts_valid_range",
        ),
        CheckConstraint(
            "supersedes_fact_id IS NULL OR supersedes_fact_id != id",
            name="ck_facts_not_self_superseding",
        ),
        CheckConstraint(
            "status != 'retired' OR supersedes_fact_id IS NOT NULL",
            name="ck_facts_retirement_has_predecessor",
        ),
        UniqueConstraint(
            "supersedes_fact_id", name="uq_facts_supersedes_fact_id"
        ),
        Index("ix_facts_subject", "subject_type", "subject_identifier"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fact_type_key: Mapped[str] = mapped_column(String(150), index=True)
    subject_type: Mapped[str] = mapped_column(String(50))
    subject_identifier: Mapped[str] = mapped_column(String(200))
    accepted_value: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[FactStatus] = mapped_column(
        SqlEnum(
            FactStatus,
            name="fact_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            validate_strings=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    supersedes_fact_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("facts.id", ondelete="RESTRICT"), index=True
    )

    supersedes: Mapped[Optional["Fact"]] = relationship(
        back_populates="superseded_by",
        foreign_keys=[supersedes_fact_id],
        remote_side=[id],
    )
    superseded_by: Mapped[list["Fact"]] = relationship(
        back_populates="supersedes",
        foreign_keys=[supersedes_fact_id],
        passive_deletes="all",
    )
    supporting_verifications: Mapped[list["Verification"]] = relationship(
        secondary=fact_verifications,
        passive_deletes="all",
    )


@event.listens_for(Fact, "before_update")
def _prevent_fact_update(_mapper, _connection, target: Fact) -> None:
    state = inspect(target)
    if any(
        state.attrs[column.key].history.has_changes()
        for column in state.mapper.column_attrs
    ):
        raise ValueError("Fact is immutable after creation")


@event.listens_for(Fact, "before_insert")
def _require_accepted_verification_support(_mapper, _connection, target: Fact) -> None:
    if not target.supporting_verifications:
        raise ValueError("Fact requires at least one supporting Verification")
    if any(
        verification.status is not VerificationStatus.ACCEPTED
        for verification in target.supporting_verifications
    ):
        raise ValueError("Fact support must use accepted Verifications")


@event.listens_for(Fact, "before_delete")
def _prevent_fact_delete(_mapper, _connection, _target: Fact) -> None:
    raise ValueError("Fact cannot be deleted; create a superseding Fact")


@event.listens_for(Fact.supporting_verifications, "append")
@event.listens_for(Fact.supporting_verifications, "remove")
def _prevent_persisted_support_change(target: Fact, _value, _initiator) -> None:
    if inspect(target).persistent:
        raise ValueError("Fact support is immutable after creation")
