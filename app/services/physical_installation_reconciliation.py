"""Validation and creation of reviewed physical-installation decisions.

This module intentionally contains no matching or automatic reconciliation.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Airport, PhysicalInstallationIdentity, Runway, SourceAssertion
from app.models.physical_installation_identity import InstallationAssertionLink, RECONCILIATION_OUTCOMES


def create_physical_installation_identity(
    session: Session, *, airport_id: int, runway_id: int | None = None, runway_end: str | None = None
) -> PhysicalInstallationIdentity:
    identity = PhysicalInstallationIdentity(airport_id=airport_id, runway_id=runway_id, runway_end=runway_end)
    _validate_identity(session, identity)
    session.add(identity)
    return identity


def record_reconciliation_decision(
    session: Session,
    *,
    assertion_id: int,
    physical_installation_id: int | None,
    outcome: str,
    reason: str,
    actor: str,
    supersedes_link_id: int | None = None,
) -> InstallationAssertionLink:
    if outcome not in RECONCILIATION_OUTCOMES:
        raise ValueError("outcome must be governed")
    if not reason.strip() or not actor.strip():
        raise ValueError("reason and actor are required for reviewed decisions")
    assertion = session.get(SourceAssertion, assertion_id)
    if assertion is None:
        raise ValueError("referenced SourceAssertion does not exist")
    identity = session.get(PhysicalInstallationIdentity, physical_installation_id) if physical_installation_id else None
    if physical_installation_id is not None and identity is None:
        raise ValueError("referenced PhysicalInstallationIdentity does not exist")
    if outcome != "UNRESOLVED" and identity is None:
        raise ValueError("resolved outcome requires a PhysicalInstallationIdentity")
    previous = session.get(InstallationAssertionLink, supersedes_link_id) if supersedes_link_id else None
    if supersedes_link_id is not None:
        if previous is None or previous.assertion_id != assertion_id:
            raise ValueError("superseded decision must exist and concern the same assertion")
    link = InstallationAssertionLink(
        assertion_id=assertion_id,
        physical_installation_id=physical_installation_id,
        outcome=outcome,
        reason=reason.strip(),
        actor=actor.strip(),
        supersedes_link_id=supersedes_link_id,
    )
    session.add(link)
    return link


def _validate_identity(session: Session, identity: PhysicalInstallationIdentity) -> None:
    if session.get(Airport, identity.airport_id) is None:
        raise ValueError("referenced Airport does not exist")
    if identity.runway_id is not None:
        runway = session.get(Runway, identity.runway_id)
        if runway is None or runway.airport_id != identity.airport_id:
            raise ValueError("runway must belong to the physical identity airport")
