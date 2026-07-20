from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AcquisitionRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    NO_CHANGE = "no_change"
    PARTIAL_SUCCESS = "partial_success"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PERMISSION_FAILURE = "permission_failure"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"


class AcquisitionSource(Base):
    __tablename__ = "acquisition_sources"
    __table_args__ = (UniqueConstraint("key", name="uq_acquisition_sources_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    publishing_source_id: Mapped[int] = mapped_column(
        ForeignKey("publishing_sources.id", ondelete="RESTRICT"), index=True
    )
    key: Mapped[str] = mapped_column(String(150))
    display_name: Mapped[str] = mapped_column(String(200))
    acquisition_type: Mapped[str] = mapped_column(String(50))
    canonical_url: Mapped[str] = mapped_column(String(1000))
    expected_media_type: Mapped[Optional[str]] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    publishing_source: Mapped["PublishingSource"] = relationship(
        back_populates="acquisition_sources"
    )
    runs: Mapped[list["AcquisitionRun"]] = relationship(
        back_populates="source", foreign_keys="AcquisitionRun.acquisition_source_id"
    )
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="source")


class AcquisitionRun(Base):
    __tablename__ = "acquisition_runs"
    __table_args__ = (
        CheckConstraint("duration_seconds >= 0", name="ck_acquisition_runs_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    acquisition_source_id: Mapped[int] = mapped_column(
        ForeignKey("acquisition_sources.id", ondelete="RESTRICT"), index=True
    )
    snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "snapshots.id",
            ondelete="RESTRICT",
            name="fk_acquisition_runs_snapshot_id",
            use_alter=True,
        ),
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[AcquisitionRunStatus] = mapped_column(
        SqlEnum(
            AcquisitionRunStatus,
            name="acquisition_run_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            validate_strings=True,
        )
    )
    request_url: Mapped[str] = mapped_column(String(1000))
    final_url: Mapped[Optional[str]] = mapped_column(String(1000))
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    content_type: Mapped[Optional[str]] = mapped_column(String(200))
    response_headers: Mapped[Optional[str]] = mapped_column(Text)
    provider_version: Mapped[str] = mapped_column(String(100))
    duration_seconds: Mapped[float] = mapped_column(Float)
    error_category: Mapped[Optional[str]] = mapped_column(String(100))
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    is_new_snapshot: Mapped[Optional[bool]] = mapped_column(Boolean)

    source: Mapped["AcquisitionSource"] = relationship(
        back_populates="runs", foreign_keys=[acquisition_source_id]
    )
    snapshot: Mapped[Optional["Snapshot"]] = relationship(
        back_populates="runs", foreign_keys=[snapshot_id], post_update=True
    )


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint(
            "acquisition_source_id", "sha256", "byte_size",
            name="uq_snapshots_source_hash_size",
        ),
        CheckConstraint("byte_size >= 0", name="ck_snapshots_byte_size"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    acquisition_source_id: Mapped[int] = mapped_column(
        ForeignKey("acquisition_sources.id", ondelete="RESTRICT"), index=True
    )
    first_acquisition_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "acquisition_runs.id",
            ondelete="RESTRICT",
            name="fk_snapshots_first_acquisition_run_id",
        ),
        unique=True,
    )
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[Optional[str]] = mapped_column(String(200))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    source: Mapped["AcquisitionSource"] = relationship(back_populates="snapshots")
    first_acquisition_run: Mapped["AcquisitionRun"] = relationship(
        foreign_keys=[first_acquisition_run_id]
    )
    runs: Mapped[list["AcquisitionRun"]] = relationship(
        back_populates="snapshot", foreign_keys="AcquisitionRun.snapshot_id"
    )


@event.listens_for(AcquisitionSource, "before_update")
def _protect_acquisition_source_identity(_mapper, _connection, target: AcquisitionSource) -> None:
    if inspect(target).attrs.key.history.has_changes():
        raise ValueError("AcquisitionSource key is immutable")


@event.listens_for(Snapshot, "before_update")
def _prevent_snapshot_update(_mapper, _connection, target: Snapshot) -> None:
    state = inspect(target)
    if any(
        state.attrs[column.key].history.has_changes()
        for column in state.mapper.column_attrs
    ):
        raise ValueError("Snapshot is immutable")


@event.listens_for(Snapshot, "before_delete")
def _prevent_snapshot_delete(_mapper, _connection, _target: Snapshot) -> None:
    raise ValueError("Snapshot cannot be deleted")


@event.listens_for(AcquisitionRun, "before_update")
def _protect_completed_run(_mapper, _connection, target: AcquisitionRun) -> None:
    history = inspect(target).attrs.status.history
    previous = history.deleted[0] if history.deleted else None
    if previous is not AcquisitionRunStatus.RUNNING:
        raise ValueError("Completed AcquisitionRun is immutable")


from app.models.document import PublishingSource  # noqa: E402
