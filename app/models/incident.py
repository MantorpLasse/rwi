from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, event, insert, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Incident(Base):
    """A runway excursion / EMAS activation.

    An activation destroys the arresting material, so it almost always
    means a future replacement order. Every insert automatically creates
    a matching high-confidence Signal - no manual review step.
    """

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)

    incident_date: Mapped[date] = mapped_column(Date, index=True)
    aircraft_type: Mapped[Optional[str]] = mapped_column(String(100))
    operator: Mapped[Optional[str]] = mapped_column(String(150))
    incident_type: Mapped[str] = mapped_column(String(100))
    emas_engaged: Mapped[bool] = mapped_column(default=False, index=True)
    injuries: Mapped[Optional[str]] = mapped_column(String(100))
    aircraft_damage: Mapped[Optional[str]] = mapped_column(String(100))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    official_report_url: Mapped[Optional[str]] = mapped_column(String(1000))
    implies_replacement: Mapped[bool] = mapped_column(Boolean, default=True)

    # See app/models/signal.py's created_at/updated_at for why these are
    # nullable (added retroactively, existing rows left NULL).
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    airport: Mapped["Airport"] = relationship(back_populates="incidents")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="incidents")
    source: Mapped[Optional["Source"]] = relationship()


def _replacement_signal_title(connection, target: "Incident") -> str:
    """Names the airport (and runway, if known) instead of the bare, repeated
    "Replacement expected after incident on {date}" every incident used to get -
    that read identically across dozens of unrelated airports in the signals list."""
    from app.models.airport import Airport, Runway

    airport_name = connection.execute(
        select(Airport.name).where(Airport.id == target.airport_id)
    ).scalar_one()

    runway_designation = None
    if target.runway_id is not None:
        runway_designation = connection.execute(
            select(Runway.designation).where(Runway.id == target.runway_id)
        ).scalar_one_or_none()

    if runway_designation:
        return (
            f"{airport_name} — Runway {runway_designation} EMAS-ersättning väntas "
            f"efter incident ({target.incident_date})"
        )
    return f"{airport_name} — EMAS-ersättning väntas efter incident ({target.incident_date})"


@event.listens_for(Incident, "after_insert")
def _create_replacement_signal(_mapper, connection, target: "Incident") -> None:
    from app.models.signal import DEFAULT_SCORE_BY_CONFIDENCE, Signal

    if not target.implies_replacement:
        return

    confidence = "high"
    connection.execute(
        insert(Signal.__table__).values(
            airport_id=target.airport_id,
            runway_id=target.runway_id,
            source_id=target.source_id,
            title=_replacement_signal_title(connection, target),
            category="replacement_after_incident",
            confidence=confidence,
            status="identified",
            probability_score=DEFAULT_SCORE_BY_CONFIDENCE[confidence],
            target_year=None,
            notes=target.summary,
        )
    )
