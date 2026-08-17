from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


RECONCILIATION_OUTCOMES = (
    "SAME_PHYSICAL_INSTALLATION",
    "DIFFERENT_PHYSICAL_INSTALLATION",
    "UNRESOLVED",
)


class PhysicalInstallationIdentity(Base):
    """A human-reviewed canonical identity for one physical EMAS system."""

    __tablename__ = "physical_installation_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    runway_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runways.id"), nullable=True, index=True)
    runway_end: Mapped[Optional[str]] = mapped_column(String(20))
    # Canonical runway-end placement (docs/domain/canonical-runway-runway-end-design.md).
    # Deliberately separate from the free-text `runway_end` above, which is
    # never overwritten: this column is only ever set by an explicit,
    # human-approved linking decision (see design doc S7) - never inferred,
    # never auto-populated from `runway_end`, however "obvious" a match looks.
    runway_end_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runway_ends.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    airport: Mapped["Airport"] = relationship(back_populates="physical_installation_identities")
    runway: Mapped[Optional["Runway"]] = relationship(back_populates="physical_installation_identities")
    canonical_runway_end: Mapped[Optional["RunwayEnd"]] = relationship(back_populates="physical_installation_identities")
    assertion_links: Mapped[list["InstallationAssertionLink"]] = relationship(back_populates="physical_installation")


class InstallationAssertionLink(Base):
    """An append-only human-reviewed assertion-to-identity reconciliation decision."""

    __tablename__ = "installation_assertion_links"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('SAME_PHYSICAL_INSTALLATION', 'DIFFERENT_PHYSICAL_INSTALLATION', 'UNRESOLVED')",
            name="ck_installation_assertion_links_outcome",
        ),
        CheckConstraint(
            "(outcome = 'UNRESOLVED') OR physical_installation_id IS NOT NULL",
            name="ck_installation_assertion_links_target_for_resolved",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)
    physical_installation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("physical_installation_identities.id"), nullable=True, index=True
    )
    outcome: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(100))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    supersedes_link_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("installation_assertion_links.id"), nullable=True, index=True
    )

    assertion: Mapped["SourceAssertion"] = relationship(back_populates="installation_assertion_links")
    physical_installation: Mapped[Optional[PhysicalInstallationIdentity]] = relationship(back_populates="assertion_links")
    supersedes: Mapped[Optional["InstallationAssertionLink"]] = relationship(
        remote_side="InstallationAssertionLink.id", foreign_keys=[supersedes_link_id]
    )


@event.listens_for(InstallationAssertionLink, "before_update")
def _prevent_reconciliation_decision_update(_mapper, _connection, _target) -> None:
    raise ValueError("Reconciliation decisions are immutable; create a superseding link instead.")


@event.listens_for(InstallationAssertionLink, "before_delete")
def _prevent_reconciliation_decision_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Reconciliation decisions are auditable and cannot be deleted.")
