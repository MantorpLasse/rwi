from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

from app.database import Base

# D4D1 of docs/architecture/fh-d4-signal-disposition-design.md (§5-11) - the
# smallest persisted mechanism for a human to record that an FH-D4
# candidate group of already-existing Signals is DISTINCT or represents the
# SAME_REAL_WORLD_EFFORT. Deliberately NOT a reuse or extension of
# ReviewerAction: every ReviewerAction row's subject is a SourceAssertion
# (evidence not yet linked to a Signal); this table's subject is always two
# or more already-existing, already-persisted Signals being compared to
# each other, a structurally different relationship ReviewerAction has no
# field capable of representing (see the design doc's own §2 "Why R4 cannot
# be reused").
SIGNAL_DISPOSITION_DECISIONS = ("DISTINCT", "SAME_REAL_WORLD_EFFORT")

# Review-checkpoint addition (D4D1 critical review): the transient (never
# mapped, never persisted) Python attribute name
# app.services.signal_disposition_persistence.record_signal_group_disposition()
# sets on a SignalDisposition instance while - and only while - it is
# inserting that disposition's own initial member batch. See
# _prevent_member_insert_into_an_already_sealed_disposition() below for why
# this exists: before_update/before_delete alone leave a disposition's own
# member SET mutable forever after creation (a fresh INSERT is neither an
# UPDATE nor a DELETE of any existing row, so neither listener fires) - a
# real, reproduced gap where `session.add(SignalDispositionMember(
# disposition_id=<already-committed id>, signal_id=<new>))` silently
# extended an already-reviewed group's own historical membership. This
# attribute is the single, minimal signal that closes it without a new
# persisted column, a DB trigger, or any change to CHECK-constraint-level
# enforcement.
ACCEPTING_INITIAL_MEMBERS_ATTR = "_d4d1_accepting_initial_members"


class SignalDisposition(Base):
    """An append-only human governance decision about a GROUP of two or more
    already-existing Signals (design doc §5-11).

    Modeled directly on ReviewerAction (app/models/reviewer_action.py) - the
    established structural precedent for a human-reviewed, append-only
    decision record in this pipeline: immutability enforced the same way
    (ORM before_update/before_delete event listeners, not just convention),
    `reviewer` a plain free-text identity field (no auth/user-management FK,
    matching ReviewerAction.reviewer and
    InstallationAssertionLink.actor exactly - RWI has no such
    infrastructure), and `supersedes_id` a self-referential nullable FK so a
    reviewer changing their mind appends a new row rather than editing the
    old one.

    UNLIKE ReviewerAction, this table's own `supersedes_id` requires
    (service-level, app.services.signal_disposition_persistence, not a DB
    CHECK - no cross-table set-equality CHECK is expressible in SQLite) that
    the new disposition's member Signal-id set is IDENTICAL to the
    superseded disposition's own member set (design doc §8) - a disposition
    for a differently-shaped group (e.g. a candidate pair that later grew to
    a triple) can never claim to supersede the old, narrower disposition;
    it is always a wholly new, independent, initially-unreviewed row.

    No `canonical_signal_id`, no confidence/score/ranking field, no
    publication-state field, no resolution-workflow field, no fingerprint,
    no raw evidence, no provider/vendor field - deliberately absent
    (design doc §5.2, §20 "Explicit non-goals"). This table records only
    that a human compared a specific set of Signal ids and reached one of
    exactly two conclusions; nothing about WHY those Signals were compared,
    what evidence backs either Signal, or what should happen next is
    represented here.
    """

    __tablename__ = "signal_dispositions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('DISTINCT', 'SAME_REAL_WORLD_EFFORT')",
            name="ck_signal_dispositions_decision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)

    # Plain free-text identity - matches ReviewerAction.reviewer exactly (see
    # that model's own comment for why: RWI has no authentication or
    # user-management infrastructure).
    reviewer: Mapped[str] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Self-referential: a later reviewer decision supersedes an earlier one
    # for the SAME exact member-set (service-enforced, see module docstring
    # above) without deleting or editing it. "Current" state for a given
    # member set is derived by recency (created_at then id, the exact
    # tie-break get_latest_reviewer_action() already uses) - a future D4D3
    # read service's responsibility, not this model's or this table's own.
    supersedes_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("signal_dispositions.id"), nullable=True, index=True
    )

    supersedes: Mapped[Optional["SignalDisposition"]] = relationship(
        remote_side="SignalDisposition.id", foreign_keys=[supersedes_id]
    )
    members: Mapped[list["SignalDispositionMember"]] = relationship(
        back_populates="disposition", passive_deletes=True,
    )


class SignalDispositionMember(Base):
    """One Signal's membership in one SignalDisposition (design doc §5-6).

    A real, DB-enforced foreign key to `signals.id` on every member row -
    deliberately NOT a serialized/JSON list of ids on the header row
    (design doc §4 Option A, explicitly rejected: a serialized column
    cannot be referentially checked by `PRAGMA foreign_key_check`, and this
    project's every other governance table - ReviewerAction,
    InstallationAssertionLink, SourceAssertion.signal_id - already uses a
    real FK for exactly this reason).

    No `ON DELETE` clause on `signal_id`, and `passive_deletes=True` on
    `Signal`'s own reverse side of this relationship (mirroring
    `Signal.supporting_source_assertions`'s already-verified precedent) -
    together these mean SQLAlchemy never pre-emptively nulls or detaches
    this row when its Signal is deleted; the real database-level FOREIGN
    KEY constraint is left to decide, and with no ON DELETE clause it
    refuses the delete outright while any disposition still names that
    Signal (see app/models/signal.py's own identical reasoning for
    supporting_source_assertions).

    UNIQUE(disposition_id, signal_id): a Signal cannot appear twice within
    one disposition's own member list.

    A member row can be inserted ONLY while its owning SignalDisposition is
    marked as still accepting its initial member batch (see
    `ACCEPTING_INITIAL_MEMBERS_ATTR` and
    `_prevent_member_insert_into_an_already_sealed_disposition()` below) -
    review-checkpoint addition, closing a real, reproduced gap where a
    member could otherwise be appended to an already-committed, already-
    "immutable" disposition's own member set via a plain `session.add()`,
    since inserting a brand-new row is neither an UPDATE nor a DELETE of
    any existing row and so never triggered either of this table's own
    pre-existing immutability listeners.
    """

    __tablename__ = "signal_disposition_members"
    __table_args__ = (
        UniqueConstraint("disposition_id", "signal_id", name="uq_signal_disposition_members_disposition_signal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    disposition_id: Mapped[int] = mapped_column(ForeignKey("signal_dispositions.id"), index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)

    disposition: Mapped["SignalDisposition"] = relationship(back_populates="members")
    signal: Mapped["Signal"] = relationship()


@event.listens_for(SignalDisposition, "before_update")
def _prevent_signal_disposition_update(_mapper, _connection, _target) -> None:
    raise ValueError("Signal dispositions are immutable; record a superseding disposition instead.")


@event.listens_for(SignalDisposition, "before_delete")
def _prevent_signal_disposition_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Signal dispositions are auditable and cannot be deleted.")


@event.listens_for(SignalDispositionMember, "before_update")
def _prevent_signal_disposition_member_update(_mapper, _connection, _target) -> None:
    raise ValueError("Signal disposition members are immutable; record a superseding disposition instead.")


@event.listens_for(SignalDispositionMember, "before_delete")
def _prevent_signal_disposition_member_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Signal disposition members are auditable and cannot be deleted.")


@event.listens_for(SignalDispositionMember, "before_insert")
def _prevent_member_insert_into_an_already_sealed_disposition(_mapper, _connection, target) -> None:
    """Review-checkpoint addition: closes a real, reproduced gap where a
    member row could be appended to an already-committed disposition's own
    member set via a plain `session.add(SignalDispositionMember(...))` -
    `before_update`/`before_delete` never fire for a brand-new INSERT, so
    neither pre-existing immutability listener on either table caught this.

    Fails closed by default: a member insert is allowed only when its
    owning `SignalDisposition` (looked up via the current session's own
    identity map - a pure in-memory lookup whenever that disposition was
    already touched earlier in the same flush, never a new query in the
    common case) carries `ACCEPTING_INITIAL_MEMBERS_ATTR` set `True` -
    which only `record_signal_group_disposition()` ever sets, and only for
    the brief window between constructing a brand-new disposition and
    finishing its own initial member batch (see that function's own
    docstring). A disposition loaded fresh from the database - by
    definition, anything not freshly constructed in the current
    operation - never carries this attribute at all, so `getattr(...,
    False)` correctly defaults to blocking it. This is a transient,
    never-mapped, never-persisted plain Python instance attribute, not a
    database column - it cannot be queried, indexed, or inspected outside
    the single Python process that set it, and it is never relied upon as
    a security boundary against a direct raw-SQL write (see the model's
    own module docstring on that trust boundary, identical to every other
    persistence service in this pipeline).
    """
    session = object_session(target)
    disposition = session.get(SignalDisposition, target.disposition_id) if session is not None else None
    if disposition is None or not getattr(disposition, ACCEPTING_INITIAL_MEMBERS_ATTR, False):
        raise ValueError(
            "Signal disposition members can only be created together with a brand-new "
            "SignalDisposition, in the same operation - a member cannot be appended to an "
            "already-persisted disposition afterward; record a superseding disposition instead."
        )
