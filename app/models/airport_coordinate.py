"""Governed, append-only Airport coordinate provenance/history (RWI Mission
#26G, following the architecture recon/approval in Mission #26F and HQ's
explicit #26G clarification).

An `AirportCoordinate` row represents exactly one thing: a human-reviewed
decision that a specific (latitude, longitude) decimal-degree pair is
Airport's accepted coordinate, evidenced by exactly one preserved
SourceAssertion excerpt - never a translation, never an inference from
country/region, never a human's own geography judgment presented as fact.

    SourceAssertion (assertion_type="airport_inventory", already governed
        by Mission #26D/#26E's known-Airport evidence seam)
        -> human coordinate review (exact literal excerpt containing the
           coordinate pair, normalized decimal degrees supplied by the
           reviewer - no DMS parsing happens anywhere in this pipeline)
        -> AirportCoordinate (this table, append-only)
        -> app.services.airport_coordinate's own strictly-gated,
           concurrency-checked write onto Airport.latitude/.longitude
           (the existing, unmodified current-value projection every other
           consumer, including app.static_export.build._airport_location_view(),
           already reads directly)

MODELED DIRECTLY ON app.models.airport_identifier.AirportIdentifier
(Mission #26F's own central precedent finding), with ONE deliberate,
HQ-approved extension: AirportIdentifier's write gate never replaces an
already-populated Airport column (a first-write-only mechanism); this
gate MUST support ordinary coordinate correction - superseding an
already-ADMITTED coordinate with a new, better-evidenced ADMITTED
coordinate. HQ's own explicit #26G clarification: normal replacement is
represented by exactly ONE new ADMITTED row (supersedes_coordinate_id
naming the row it replaces) - never a separate REJECTED/RETIRED
withdrawal row merely because a replacement occurred. REJECTED/RETIRED
remain part of this table's vocabulary (for a future, explicitly
deferred, standalone withdrawal-without-replacement service - see
app.services.airport_coordinate's own module docstring "WITHDRAWAL
DEFERRED") but this mission's write gate never produces either value
itself.

CURRENT-HEAD SEMANTICS (Mission #26F Part H, refined - never merely "the
latest row by created_at"): the effective/current coordinate for an
Airport is the latest row (by created_at, id) for that airport_id, IF AND
ONLY IF that row's own status is "ADMITTED" - a latest row whose status is
REJECTED/RETIRED means the Airport currently has NO accepted coordinate
(a withdrawal-without-immediate-replacement genuinely un-accepts, it does
not fall back to an earlier row). See
app.services.airport_coordinate.get_current_airport_coordinate().

APPEND-ONLY (mirrors AirportIdentifier's before_update/before_delete
event-listener pattern verbatim): no field of an existing row is EVER
updated after creation, including `status` - a changed accepted truth is
always represented by a new, appended row. Old rows are never deleted.

COORDINATE PAIR AS ONE FACT (Mission #26F Part F): `latitude`/`longitude`
are both plain non-nullable columns on the SAME row, written by the SAME
service call from the SAME evidence excerpt - there is no code path
anywhere in this pipeline that can set one without the other, structurally
preventing "latitude from Assertion A, longitude from Assertion B."

DATUM / SEMANTIC TYPE ARE NEVER MANUFACTURED (Mission #26F Part Q):
`datum`/`coordinate_semantic_type` are nullable, free-text, and populated
ONLY when the cited `evidence_excerpt` itself literally states them - the
write gate never infers "WGS84" or "ARP" from context, source reputation,
or modern-aviation-site convention.

NORMALIZATION BOUNDARY (Mission #26F Part P): `SourceAssertion.raw_relevant_text`
keeps the ORIGINAL source notation verbatim (already true, already
governed, unmodified by this mission). This table stores ONLY the
already-reviewed, already-normalized decimal-degree pair the human
caller supplies - no DMS parser, no derivation logic, anywhere in this
module or its write-gate service.

`source_id` is stored REDUNDANTLY alongside `source_assertion_id`
(mirroring AirportIdentifier's own identical redundancy) but is always
DERIVED from `source_assertion.source_id` by the write-gate service,
never independently supplied by a caller - this removes an entire class
of "contradictory source/source_assertion pair" risk structurally, not
merely by validation (Mission #26F Part I / #26G Part I).

`analyst` is plain free-text human attribution, matching every other
governance table in this pipeline (RWI has no auth/user-management
infrastructure) - never a ReviewerAction FK, whose vocabulary
(APPROVE_SIGNAL/REJECT_SIGNAL/...) is Signal-disposition-specific and
structurally the wrong shape for coordinate acceptance.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Same vocabulary as AirportIdentifier, for the same reasons (Mission
# #26F Part D). This mission's write gate (app.services.airport_coordinate)
# only ever produces "ADMITTED" rows - REJECTED/RETIRED remain valid
# CHECK-constraint members for a future, explicitly deferred withdrawal
# service, never fabricated here merely because the vocabulary exists.
AIRPORT_COORDINATE_STATUSES = ("ADMITTED", "REJECTED", "RETIRED")

_LATITUDE_MIN, _LATITUDE_MAX = -90.0, 90.0
_LONGITUDE_MIN, _LONGITUDE_MAX = -180.0, 180.0


class AirportCoordinate(Base):
    """One append-only, immutable record of a human-reviewed, evidence-
    backed coordinate-pair acceptance for one Airport. See module
    docstring for the full design rationale."""

    __tablename__ = "airport_coordinates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ADMITTED', 'REJECTED', 'RETIRED')",
            name="ck_airport_coordinates_status",
        ),
        CheckConstraint(
            f"latitude >= {_LATITUDE_MIN} AND latitude <= {_LATITUDE_MAX}",
            name="ck_airport_coordinates_latitude_range",
        ),
        CheckConstraint(
            f"longitude >= {_LONGITUDE_MIN} AND longitude <= {_LONGITUDE_MAX}",
            name="ck_airport_coordinates_longitude_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Not unique at the DB layer - append-only; "which coordinate is
    # currently effective for this airport" is a business rule derived by
    # recency + status (app.services.airport_coordinate's own read
    # helper), never a DB uniqueness constraint - matching
    # AirportIdentifier's identical posture exactly.
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)

    # Always derived from source_assertion.source_id by the write-gate
    # service, never independently supplied - see module docstring.
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    source_assertion_id: Mapped[int] = mapped_column(ForeignKey("source_assertions.id"), index=True)

    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)

    # Nullable, never inferred - populated only when the cited evidence
    # excerpt itself literally states it (Mission #26F Part Q/M).
    coordinate_semantic_type: Mapped[Optional[str]] = mapped_column(String(50))
    datum: Mapped[Optional[str]] = mapped_column(String(30))

    # The exact, literal excerpt this acceptance is evidenced by - must be
    # a literal substring of source_assertion.raw_relevant_text (mirrors
    # AirportIdentifier.evidence_excerpt's mechanical-containment
    # discipline exactly, enforced in the service, not the model).
    evidence_excerpt: Mapped[str] = mapped_column(Text)

    # Plain free-text identity, matching every other reviewer/analyst
    # field in this pipeline. Governance attribution only - never
    # evidence.
    analyst: Mapped[str] = mapped_column(String(100))

    status: Mapped[str] = mapped_column(String(20), default="ADMITTED")

    # Self-referential. For a first-time acceptance (no prior effective
    # coordinate for this Airport), this is NULL. For an ordinary
    # replacement, this names the row being replaced (HQ's #26G
    # clarification: the replaced row itself is NEVER edited or given a
    # separate RETIRED row - one new ADMITTED row is the entire
    # representation of "this superseded that"). "Current" state is still
    # always derived by recency+status (see module docstring), never by
    # walking this chain.
    supersedes_coordinate_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("airport_coordinates.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # All one-directional (no back_populates) - matches AirportIdentifier
    # and every other append-only evidence/decision table in this
    # pipeline.
    airport: Mapped["Airport"] = relationship(foreign_keys=[airport_id])
    source: Mapped["Source"] = relationship()
    source_assertion: Mapped["SourceAssertion"] = relationship()
    supersedes: Mapped[Optional["AirportCoordinate"]] = relationship(
        remote_side="AirportCoordinate.id", foreign_keys=[supersedes_coordinate_id]
    )


@event.listens_for(AirportCoordinate, "before_update")
def _prevent_airport_coordinate_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "AirportCoordinate rows are immutable; a corrected or withdrawn coordinate is recorded as a new, "
        "appended row naming this one as supersedes_coordinate_id, never an edit of an existing one."
    )


@event.listens_for(AirportCoordinate, "before_delete")
def _prevent_airport_coordinate_delete(_mapper, _connection, _target) -> None:
    raise ValueError("AirportCoordinate rows are auditable and cannot be deleted.")
