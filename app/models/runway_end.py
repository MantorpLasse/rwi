from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RunwayEnd(Base):
    """One canonical, normalized directional threshold of a Runway.

    See docs/domain/canonical-runway-runway-end-design.md. Deliberately
    identity-only (runway_id + normalized designation) - no lifecycle,
    history, alias, geometry, or lighting fields; those are explicitly
    deferred until a real product need exists for them.
    """

    __tablename__ = "runway_ends"
    __table_args__ = (UniqueConstraint("runway_id", "designation", name="uq_runway_ends_runway_designation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    runway_id: Mapped[int] = mapped_column(ForeignKey("runways.id"), index=True)
    designation: Mapped[str] = mapped_column(String(10))

    runway: Mapped["Runway"] = relationship(back_populates="runway_ends")
    physical_installation_identities: Mapped[list["PhysicalInstallationIdentity"]] = relationship(
        back_populates="canonical_runway_end"
    )
