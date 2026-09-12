from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

from app.database import Base

# RWI HQ "Signal Amendment Audit Trail — Append-Only Governance" mission,
# implementing the approved design in
# docs/architecture/rwi-signal-amendment-audit-trail-design.md (§5-9).
#
#     SignalAmendmentAction (one row per human-approved amend_signal() call)
#         + SignalAmendmentFieldChange (one row per field that call changed)
#         -> STOP (the live Signal row remains current accepted state; these
#            two tables preserve append-only HISTORY only, never the current
#            value itself)
#
# Modeled directly on app.models.signal_disposition.SignalDisposition/
# SignalDispositionMember - the closest existing structural precedent for a
# "header decision + typed child rows, atomically inserted together"
# append-only pair (immutability enforced via before_update/before_delete
# ORM event listeners, not just convention; a plain free-text `reviewer`
# identity column - RWI has no auth/user-management infrastructure to FK
# to instead).
#
# ONE DELIBERATE STRUCTURAL DIFFERENCE from every other append-only table in
# this codebase (ReviewerAction, SignalPublicationAction, SignalDisposition,
# InstallationAssertionLink): NO `supersedes_id`. Every one of those needs
# one because ITS OWN table is the only place "what is the CURRENT decision"
# can be answered (by recency). An amendment's "current state" question is
# answered by a completely different, already-existing place - the live
# `Signal` row itself always holds the current value. This table only ever
# answers "how did it get here," which a plain, ordered append list already
# does perfectly with no supersession chain to walk (design doc §5).
#
# NO speculative metadata was added beyond the approved design: no `status`,
# no publication-state field, no workflow-state field, no JSON blob, no
# free-form action vocabulary - this is a plain historical event record,
# nothing else.


class SignalAmendmentAction(Base):
    """An append-only record of one human-approved amendment operation on
    one existing Signal (design doc §5-7).

    `source_assertion_id`/`reviewer_action_id` are BOTH deliberately
    nullable and independent of each other - a legacy/pre-governance Signal
    may be corrected on `reviewer` + `reason` alone, with neither reference
    supplied (design doc §10, mirroring app.services.signal_amendment's own
    already-existing, unchanged validation: these two references are
    optional but explicit, never inferred, never required). This table
    never reassigns or otherwise mutates `SourceAssertion.signal_id` -
    citing a SourceAssertion here is a read-only reference, not a link
    update.

    No `supersedes_id` - see module docstring. A correction to a previous
    amendment is simply another, independent `SignalAmendmentAction` row;
    the old (superseded) one remains, unedited, as part of the honest
    historical record.
    """

    __tablename__ = "signal_amendment_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity - matches ReviewerAction.reviewer/
    # SignalDisposition.reviewer exactly (see those models' own comments for
    # why: RWI has no authentication or user-management infrastructure).
    reviewer: Mapped[str] = mapped_column(String(100))

    # Optional, independent supporting-evidence references (design doc §10/
    # §11) - never required, never inferred from one another beyond the
    # existing app.services.signal_amendment compatibility check (if both
    # are supplied, the ReviewerAction must belong to the supplied
    # SourceAssertion - enforced in the service, not by a DB constraint,
    # since it is a cross-reference check, not a vocabulary rule).
    source_assertion_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_assertions.id"), nullable=True, index=True
    )
    reviewer_action_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("reviewer_actions.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    signal: Mapped["Signal"] = relationship()
    source_assertion: Mapped[Optional["SourceAssertion"]] = relationship()
    reviewer_action: Mapped[Optional["ReviewerAction"]] = relationship()
    field_changes: Mapped[list["SignalAmendmentFieldChange"]] = relationship(
        back_populates="action", passive_deletes=True,
    )


class SignalAmendmentFieldChange(Base):
    """One field's before/after value within one SignalAmendmentAction
    (design doc §5-8).

    `old_value`/`new_value` are canonical TEXT serializations (see
    app.services.signal_amendment_serialization) - a genuine SQL NULL
    represents Python `None` (never a sentinel string), so NULL stays
    cleanly distinguishable from an empty string. `field_name` alone
    determines how to deserialize these back to a typed value (via
    `Signal.__table__.columns[field_name].type` - never a stored type tag,
    since the field-to-type mapping is already fully determined by the
    Signal model itself).

    Deliberately NO DB CHECK constraint on `field_name`: unlike
    ReviewerAction.action/SignalDisposition.decision (fixed domain
    vocabularies unlikely to grow), `app.services.signal_amendment
    .ALLOWED_AMENDMENT_FIELDS` is explicitly expected to grow as future
    missions add more amendable fields (design doc §7) - enforcement
    already happens, sufficiently, in that Python-level allowlist at
    `amend_signal()`'s own validation step, before any row here is ever
    constructed. Coupling this column to a DB CHECK would force a schema
    migration every time that Python list changes, for no safety benefit.
    """

    __tablename__ = "signal_amendment_field_changes"
    __table_args__ = (
        UniqueConstraint("action_id", "field_name", name="uq_signal_amendment_field_changes_action_field"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("signal_amendment_actions.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(60), index=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    action: Mapped["SignalAmendmentAction"] = relationship(back_populates="field_changes")


@event.listens_for(SignalAmendmentAction, "before_update")
def _prevent_signal_amendment_action_update(_mapper, _connection, _target) -> None:
    raise ValueError("Signal amendment actions are immutable; record a new amendment instead.")


@event.listens_for(SignalAmendmentAction, "before_delete")
def _prevent_signal_amendment_action_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Signal amendment actions are auditable and cannot be deleted.")


@event.listens_for(SignalAmendmentFieldChange, "before_update")
def _prevent_signal_amendment_field_change_update(_mapper, _connection, _target) -> None:
    raise ValueError("Signal amendment field changes are immutable; record a new amendment instead.")


@event.listens_for(SignalAmendmentFieldChange, "before_delete")
def _prevent_signal_amendment_field_change_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Signal amendment field changes are auditable and cannot be deleted.")


# Transient (never mapped, never persisted) attribute name
# app.services.signal_amendment_history.record_signal_amendment() sets on a
# SignalAmendmentAction instance while - and only while - it is inserting
# that action's own initial field-change batch. Mirrors
# app.models.signal_disposition.ACCEPTING_INITIAL_MEMBERS_ATTR exactly,
# closing the identical class of gap that constant's own docstring
# documents: before_update/before_delete alone leave a freshly-committed
# action's own child set mutable forever afterward, because a brand-new
# INSERT of a SignalAmendmentFieldChange is neither an UPDATE nor a DELETE
# of any existing row, so neither listener above ever fires for it.
ACCEPTING_INITIAL_FIELD_CHANGES_ATTR = "_signal_amendment_accepting_initial_field_changes"


@event.listens_for(SignalAmendmentFieldChange, "before_insert")
def _prevent_field_change_insert_into_an_already_sealed_action(_mapper, _connection, target) -> None:
    """Fails closed by default, identical reasoning to
    app.models.signal_disposition's own
    _prevent_member_insert_into_an_already_sealed_disposition(): a field
    change can be inserted only while its owning `SignalAmendmentAction`
    (looked up via the current session's own identity map) carries
    `ACCEPTING_INITIAL_FIELD_CHANGES_ATTR` set `True` - which only
    `record_signal_amendment()` ever sets, and only for the brief window
    between constructing a brand-new action and finishing its own initial
    field-change batch. A transient, never-mapped, never-persisted plain
    Python instance attribute, not a database column - not a security
    boundary against a direct raw-SQL write, exactly like its
    SignalDispositionMember precedent."""
    session = object_session(target)
    action = session.get(SignalAmendmentAction, target.action_id) if session is not None else None
    if action is None or not getattr(action, ACCEPTING_INITIAL_FIELD_CHANGES_ATTR, False):
        raise ValueError(
            "Signal amendment field changes can only be created together with a brand-new "
            "SignalAmendmentAction, in the same operation - a field change cannot be appended to "
            "an already-persisted action afterward; record a new amendment instead."
        )
