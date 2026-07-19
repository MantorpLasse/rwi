from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.fact import FactStatus


class IntelligenceStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


intelligence_facts = Table(
    "intelligence_facts",
    Base.metadata,
    Column(
        "intelligence_id",
        ForeignKey("intelligence.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("fact_id", ForeignKey("facts.id", ondelete="RESTRICT"), primary_key=True),
)


class Intelligence(Base):
    __tablename__ = "intelligence"
    __table_args__ = (
        CheckConstraint(
            "supersedes_intelligence_id IS NULL OR supersedes_intelligence_id != id",
            name="ck_intelligence_not_self_superseding",
        ),
        CheckConstraint(
            "status = 'active' OR supersedes_intelligence_id IS NOT NULL",
            name="ck_intelligence_inactive_has_predecessor",
        ),
        UniqueConstraint(
            "supersedes_intelligence_id",
            name="uq_intelligence_supersedes_intelligence_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    finding_type_id: Mapped[int] = mapped_column(
        ForeignKey("finding_types.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[IntelligenceStatus] = mapped_column(
        SqlEnum(
            IntelligenceStatus,
            name="intelligence_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            validate_strings=True,
        ),
        index=True,
    )
    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    supersedes_intelligence_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("intelligence.id", ondelete="RESTRICT"), index=True
    )

    supersedes: Mapped[Optional["Intelligence"]] = relationship(
        back_populates="superseded_by",
        foreign_keys=[supersedes_intelligence_id],
        remote_side=[id],
    )
    superseded_by: Mapped[list["Intelligence"]] = relationship(
        back_populates="supersedes",
        foreign_keys=[supersedes_intelligence_id],
        passive_deletes="all",
    )
    supporting_facts: Mapped[list["Fact"]] = relationship(
        secondary=intelligence_facts,
        passive_deletes="all",
    )
    finding_type: Mapped["FindingType"] = relationship(
        back_populates="intelligence_records"
    )


@event.listens_for(Intelligence, "before_insert")
def _require_accepted_fact_support(_mapper, _connection, target: Intelligence) -> None:
    if not target.supporting_facts:
        raise ValueError("Intelligence requires at least one supporting Fact")
    if any(fact.status is not FactStatus.ACCEPTED for fact in target.supporting_facts):
        raise ValueError("Intelligence support must use accepted Facts")


@event.listens_for(Intelligence, "before_update")
def _prevent_intelligence_update(_mapper, _connection, target: Intelligence) -> None:
    state = inspect(target)
    if any(
        state.attrs[column.key].history.has_changes()
        for column in state.mapper.column_attrs
    ):
        raise ValueError("Intelligence is immutable after creation")


@event.listens_for(Intelligence, "before_delete")
def _prevent_intelligence_delete(_mapper, _connection, _target: Intelligence) -> None:
    raise ValueError("Intelligence cannot be deleted; create a superseding Intelligence")


@event.listens_for(Intelligence.supporting_facts, "append")
@event.listens_for(Intelligence.supporting_facts, "remove")
def _prevent_persisted_support_change(target: Intelligence, _value, _initiator) -> None:
    if inspect(target).persistent:
        raise ValueError("Intelligence support is immutable after creation")
