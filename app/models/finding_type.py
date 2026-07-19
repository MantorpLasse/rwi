from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base


FINDING_TYPE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


class FindingType(Base):
    __tablename__ = "finding_types"
    __table_args__ = (
        CheckConstraint(
            "key GLOB '[A-Z]*' "
            "AND key NOT GLOB '*[^A-Z0-9_]*' "
            "AND instr(key, '__') = 0 "
            "AND substr(key, -1, 1) != '_'",
            name="ck_finding_types_upper_snake_key",
        ),
        UniqueConstraint("key", name="uq_finding_types_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(150))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    intelligence_records: Mapped[list["Intelligence"]] = relationship(
        back_populates="finding_type", passive_deletes="all"
    )

    @validates("key")
    def validate_key(self, _attribute_name: str, value: str) -> str:
        normalized = value.strip()
        if not FINDING_TYPE_KEY_PATTERN.fullmatch(normalized):
            raise ValueError("FindingType key must use UPPER_SNAKE_CASE")
        if self.key is not None and normalized != self.key:
            raise ValueError("FindingType key is immutable")
        return normalized
