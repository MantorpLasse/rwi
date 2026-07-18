from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.database import Base


OBSERVATION_VALUE_TYPES = frozenset({"enumeration", "integer", "raw_text"})


class ObservationType(Base):
    __tablename__ = "observation_types"
    __table_args__ = (
        CheckConstraint(
            "value_type IN ('enumeration', 'integer', 'raw_text')",
            name="ck_observation_types_value_type",
        ),
        UniqueConstraint("key", name="uq_observation_types_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(150))
    display_label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    @validates("key")
    def validate_immutable_key(self, _attribute_name: str, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("ObservationType key must not be empty")
        if self.key is not None and normalized != self.key:
            raise ValueError("ObservationType key is immutable")
        return normalized

