"""Stage-only human-KEEP evidence persistence (RWI Mission #25J1, following
the architecture sign-off in Mission #25I).

    Source (create/reuse)
        -> unresolved SourceAssertion (create/reuse)
        -> STOP

A stage-only SourceAssertion means exactly what Mission #25I approved:
"a human reviewed this exact preserved upstream fragment and decided it
is worth further governed processing" - never accepted evidence, never
resolved airport identity, never a claim, Fact, Signal, or publication.

This module creates ONLY Source and SourceAssertion rows. It never calls
the identity guard's own candidate-evaluation entry point, the UAC3
discovery-identity orchestrator, the EvidenceBag-construction adapter, or
the EvidenceBag-schema-readiness check - see
tests/test_stage_only_evidence_persistence_architectural_safety.py, which
enforces this by AST inspection, not just convention, mirroring the exact
discipline app.services.generic_web_fetch's own architectural-safety test
already established for the human-Fetch boundary (Mission #11B).

Field semantics (Mission #25I's own approved contract - see that
mission's decision table for the full repository-evidence trail):

  assertion_type = "project_construction" - the SAME bucket
      app.services.discovery_evidence_persistence's own _ASSERTION_TYPE
      constant already uses for discovery-sourced, identity-unresolved
      evidence - never "historical" (that value is reserved for
      already-identity-resolved, already-reviewed backfill rows with a
      different meaning entirely - see
      scripts/backfill_faa_fact_sheet_evidence.py's own real usage).
  evidence_quality = "unverified_candidate"
  review_state = "unreviewed" - this field means "has RWI's own later
      claim/evidence-reconciliation process examined this row", never
      "did a human choose this fragment at Selection time" - confirmed
      by discovery_evidence_persistence.py's own identical usage for
      rows that already went through full KEEP+guard in production.
  airport_id = NULL
  unknown_airport_candidate_id = NULL
  identity_guard_decision / identity_guard_reason = NULL - deferred,
      never accepted, never bypassed. Nothing downstream (the Signal-
      creation gate in app.services.governed_signal_creation) can act on
      a NULL value - it fails every gate closed.
  intelligence_review_decision / intelligence_review_reason = NULL
  promotion_policy_decision / promotion_policy_reason = NULL

Source reuse mirrors app.services.discovery_evidence_persistence's own
_get_or_create_source() convention exactly: external_id =
f"discovery:{document_identity}" - so a stage-only Source and a later,
separately-authorized full-governed-apply Source for the SAME document
resolve to the SAME row, never a duplicate under a different key scheme.

SourceAssertion reuse mirrors that module's own _get_existing_assertion()
convention exactly: looked up by the existing DB-level UniqueConstraint
tuple (source_id, artifact_identity, source_locator, raw_fragment_hash) -
a replay of the identical fragment returns the existing row, never a
duplicate, never mutated.

Never commits and never imports app.database.SessionLocal - mutates the
caller-supplied Session and flushes only enough to obtain row ids; the
caller owns the transaction boundary entirely, matching every other
persistence service in this pipeline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source, SourceAssertion
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata

__all__ = [
    "PlannedStageOnlyEvidence",
    "StageOnlyPersistenceResult",
    "plan_stage_only_persistence",
    "apply_stage_only_persistence",
]


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_existing_source(session: Session, metadata: DiscoverySourceMetadata) -> "Source | None":
    external_id = f"discovery:{metadata.document_identity}"
    return session.scalar(select(Source).where(Source.external_id == external_id))


def _find_existing_assertion(
    session: Session, source_id: int, *, artifact_identity: str, source_locator: str, raw_fragment_hash: str
) -> "SourceAssertion | None":
    return session.scalar(
        select(SourceAssertion).where(
            SourceAssertion.source_id == source_id,
            SourceAssertion.artifact_identity == artifact_identity,
            SourceAssertion.source_locator == source_locator,
            SourceAssertion.raw_fragment_hash == raw_fragment_hash,
        )
    )


@dataclass(frozen=True)
class PlannedStageOnlyEvidence:
    """Read-only preview of what stage-only apply WOULD do for one KEPT
    fragment - never mutates the session. `raw_text` is the FULL,
    untruncated fragment text (Mission #25J1 Part G: "the fragment shown
    in preview MUST be exactly the fragment persisted")."""

    document_identity: str
    source_locator: str
    raw_fragment_hash: str
    raw_text: str
    source_would_be_created: bool
    source_id_if_existing: "int | None"
    source_assertion_would_be_created: bool
    source_assertion_id_if_existing: "int | None"


@dataclass(frozen=True)
class StageOnlyPersistenceResult:
    """Deterministic, ORM-free summary of what apply_stage_only_persistence()
    did - never exposes ORM instances directly, matching
    DiscoveryPersistenceResult's own established convention."""

    source_id: int
    source_created: bool
    source_assertion_id: int
    source_assertion_created: bool


def plan_stage_only_persistence(
    session: Session, metadata: DiscoverySourceMetadata, *, source_locator: str, raw_text: str
) -> PlannedStageOnlyEvidence:
    """Read-only. Performs ONLY SELECT queries - never session.add()/
    flush()/commit(). Safe to call unconditionally as a preview pass,
    exactly like scripts.capture_mac_discovery.plan_governed_persistence()
    already does for the full-governed path."""
    raw_fragment_hash = _sha256_of_text(raw_text)
    existing_source = _find_existing_source(session, metadata)
    source_id_if_existing = existing_source.id if existing_source is not None else None

    source_assertion_id_if_existing = None
    if existing_source is not None:
        existing_assertion = _find_existing_assertion(
            session,
            existing_source.id,
            artifact_identity=metadata.document_identity,
            source_locator=source_locator,
            raw_fragment_hash=raw_fragment_hash,
        )
        if existing_assertion is not None:
            source_assertion_id_if_existing = existing_assertion.id

    return PlannedStageOnlyEvidence(
        document_identity=metadata.document_identity,
        source_locator=source_locator,
        raw_fragment_hash=raw_fragment_hash,
        raw_text=raw_text,
        source_would_be_created=existing_source is None,
        source_id_if_existing=source_id_if_existing,
        source_assertion_would_be_created=source_assertion_id_if_existing is None,
        source_assertion_id_if_existing=source_assertion_id_if_existing,
    )


def apply_stage_only_persistence(
    session: Session, metadata: DiscoverySourceMetadata, *, source_locator: str, raw_text: str
) -> StageOnlyPersistenceResult:
    """The only write path in this module. Creates/reuses exactly one
    Source row and creates/reuses exactly one unresolved SourceAssertion
    row - nothing else. Never calls the identity guard, UAC orchestration,
    or any EvidenceBag construction. Caller owns commit()."""
    raw_fragment_hash = _sha256_of_text(raw_text)

    source = _find_existing_source(session, metadata)
    source_created = False
    if source is None:
        source = Source(
            title=metadata.title,
            source_type=metadata.source_type,
            publisher=metadata.publisher,
            url=metadata.url,
            published_date=metadata.published_date,
            reliability_level=metadata.reliability_level,
            external_id=f"discovery:{metadata.document_identity}",
        )
        session.add(source)
        session.flush()
        source_created = True

    assertion = _find_existing_assertion(
        session,
        source.id,
        artifact_identity=metadata.document_identity,
        source_locator=source_locator,
        raw_fragment_hash=raw_fragment_hash,
    )
    assertion_created = False
    if assertion is None:
        assertion = SourceAssertion(
            source_id=source.id,
            airport_id=None,
            runway_id=None,
            unknown_airport_candidate_id=None,
            assertion_type="project_construction",
            raw_relevant_text=raw_text,
            source_locator=source_locator,
            raw_fragment_hash=raw_fragment_hash,
            artifact_identity=metadata.document_identity,
            evidence_quality="unverified_candidate",
            review_state="unreviewed",
            identity_guard_decision=None,
            identity_guard_reason=None,
            intelligence_review_decision=None,
            intelligence_review_reason=None,
            promotion_policy_decision=None,
            promotion_policy_reason=None,
        )
        session.add(assertion)
        session.flush()
        assertion_created = True

    return StageOnlyPersistenceResult(
        source_id=source.id,
        source_created=source_created,
        source_assertion_id=assertion.id,
        source_assertion_created=assertion_created,
    )
