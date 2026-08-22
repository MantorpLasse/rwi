"""Tests for app/services/unknown_airport_candidate_resolution.py (UAC4,
docs/architecture/rwi-uac4-unknown-airport-resolution-report.md).

Isolated, in-memory (or tmp_path, for the real-migration-chain test)
SQLite databases only - never the real one. Fixtures are entirely
fictional.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Installation, PhysicalInstallationIdentity, ReviewerAction, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_candidate_linked_source_assertion
from app.services.unknown_airport_candidate_persistence import (
    find_or_create_unknown_airport_candidate,
    get_latest_unknown_airport_candidate_review,
    record_unknown_airport_candidate_review,
)
from app.services.unknown_airport_candidate_resolution import (
    AlreadyResolvedError,
    CreateNewAirportResult,
    InconsistentCandidateStateError,
    MatchExistingAirportResult,
    StaleReviewError,
    create_airport_from_approved_candidate,
    resolve_candidate_to_existing_airport,
)
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, *, name="Existing Airport", country="XX", **kwargs) -> Airport:
    airport = Airport(name=name, country=country, **kwargs)
    session.add(airport)
    session.flush()
    return airport


def _seed_candidate_with_n_assertions(session, *, n=1, raw_name="Foo Regional Airport") -> "tuple[UnknownAirportCandidate, list[SourceAssertion]]":
    candidate = find_or_create_unknown_airport_candidate(session, raw_name=raw_name).candidate
    session.flush()
    assertions = []
    for i in range(n):
        fragment = CandidateFragment(
            artifact_identity=f"art-{raw_name}-{i}", source_locator="p1", raw_text=f"{raw_name} evidence {i}.",
        )
        linked = persist_candidate_linked_source_assertion(
            session, DiscoverySourceMetadata(document_identity=f"doc-{raw_name}-{i}", title="t"), fragment,
            unknown_airport_candidate_id=candidate.id,
        )
        assertions.append(session.get(SourceAssertion, linked.source_assertion_id))
    return candidate, assertions


# ---------------------------------------------------------------------------
# A/B. DEFER / REJECT history only - no execution service needed
# ---------------------------------------------------------------------------


class TestDeferAndRejectRequireNoExecutionService:
    def test_defer_history_only_no_canonical_changes(self):
        with Session(_engine()) as session:
            candidate, assertions = _seed_candidate_with_n_assertions(session)
            session.commit()
            record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="need more", reviewer="human:x")
            session.commit()

            session.refresh(candidate)
            assert candidate.resolved_airport_id is None
            assert session.query(Airport).count() == 0
            reloaded = session.get(SourceAssertion, assertions[0].id)
            assert reloaded.unknown_airport_candidate_id == candidate.id
            assert reloaded.airport_id is None

    def test_reject_candidate_history_only_no_canonical_changes(self):
        with Session(_engine()) as session:
            candidate, assertions = _seed_candidate_with_n_assertions(session)
            session.commit()
            record_unknown_airport_candidate_review(session, candidate, action="REJECT_CANDIDATE", reason="hallucinated", reviewer="human:x")
            session.commit()

            session.refresh(candidate)
            assert candidate.resolved_airport_id is None
            assert session.query(Airport).count() == 0
            reloaded = session.get(SourceAssertion, assertions[0].id)
            assert reloaded.unknown_airport_candidate_id == candidate.id


# ---------------------------------------------------------------------------
# C/D/E/F. MATCH_EXISTING_AIRPORT
# ---------------------------------------------------------------------------


class TestMatchExistingAirport:
    def test_match_success_single_assertion(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="same airport", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()

            result = resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            assert isinstance(result, MatchExistingAirportResult)
            assert result.resolved_airport_id == real.id
            assert result.moved_source_assertion_ids == (assertions[0].id,)

            session.refresh(candidate)
            assert candidate.resolved_airport_id == real.id
            reloaded = session.get(SourceAssertion, assertions[0].id)
            assert reloaded.airport_id == real.id
            assert reloaded.unknown_airport_candidate_id is None

    def test_match_missing_airport_fails_closed(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            session.delete(real)
            session.commit()

            with pytest.raises(ValueError, match="does not reference an existing Airport"):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            session.refresh(candidate)
            assert candidate.resolved_airport_id is None

    def test_match_stale_review_refused(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            first = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            # A newer review supersedes it before execution runs.
            record_unknown_airport_candidate_review(
                session, candidate, action="DEFER", reason="changed mind", reviewer="human:y",
                supersedes_review_id=first.id,
            )
            session.commit()

            with pytest.raises(StaleReviewError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=first.id)
            session.refresh(candidate)
            assert candidate.resolved_airport_id is None

    def test_check_constraint_itself_blocks_the_inconsistent_state_at_the_db_layer(self):
        """Attack attempt: fabricate a SourceAssertion with both airport_id
        and unknown_airport_candidate_id set via a raw UPDATE, bypassing the
        ORM. Finding: UAC2B's own
        ck_source_assertions_airport_candidate_mutually_exclusive CHECK
        constraint already refuses this at the database layer - SQLite
        enforces CHECK constraints unconditionally (unlike foreign keys,
        there is no PRAGMA to relax them), so this exact inconsistent state
        cannot be persisted at all via SQL, direct or otherwise. This is a
        genuine, stronger-than-expected defense already in place from UAC2B;
        _require_no_linked_assertion_already_canonical (tested directly,
        below) is defense-in-depth for a state the schema itself already
        forbids."""
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            other = _seed_airport(session, name="Other Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()

            with pytest.raises(IntegrityError, match="ck_source_assertions_airport_candidate_mutually_exclusive"):
                session.execute(
                    SourceAssertion.__table__.update().where(SourceAssertion.id == assertions[0].id).values(airport_id=other.id)
                )
                session.flush()
            session.rollback()

    def test_require_no_linked_assertion_already_canonical_fails_loud_directly(self):
        """Defense-in-depth unit test: exercise the guard function itself
        with a fabricated in-memory object carrying the inconsistent shape
        the CHECK constraint above proves is unreachable through normal
        persistence - proving the guard would still fail loud (never
        silently repair) if this state were ever reached through a future
        code path the CHECK constraint doesn't cover (e.g. a pre-UAC2B
        database, or a backend without CHECK support)."""
        import app.services.unknown_airport_candidate_resolution as resolution_module

        class _FakeAssertion:
            id = 999
            airport_id = 123

        with pytest.raises(InconsistentCandidateStateError):
            resolution_module._require_no_linked_assertion_already_canonical(1, [_FakeAssertion()])


# ---------------------------------------------------------------------------
# G/H/I/J. CREATE_NEW_AIRPORT
# ---------------------------------------------------------------------------


class TestCreateNewAirport:
    def test_create_success_single_assertion(self):
        with Session(_engine()) as session:
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="genuinely new", reviewer="human:x",
            )
            session.commit()

            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id,
                name="Foo Regional Airport", country="Fictionland", city="Fooville",
            )
            assert isinstance(result, CreateNewAirportResult)
            new_airport = session.get(Airport, result.created_airport_id)
            assert new_airport.name == "Foo Regional Airport"
            assert new_airport.country == "Fictionland"
            assert new_airport.city == "Fooville"

            session.refresh(candidate)
            assert candidate.resolved_airport_id == new_airport.id
            reloaded = session.get(SourceAssertion, assertions[0].id)
            assert reloaded.airport_id == new_airport.id
            assert reloaded.unknown_airport_candidate_id is None

    def test_create_deterministic_code_conflict_blocked(self):
        with Session(_engine()) as session:
            existing = _seed_airport(session, name="Existing", icao_code="KABC")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()

            with pytest.raises(ValueError, match="already has icao_code"):
                create_airport_from_approved_candidate(
                    session, candidate_id=candidate.id, review_id=review.id,
                    name="Foo", country="XX", icao_code="KABC",
                )
            session.refresh(candidate)
            assert candidate.resolved_airport_id is None
            assert session.query(Airport).count() == 1

    def test_create_case_variant_code_conflict_blocked(self):
        """Attack: existing Airport has icao_code='KABC'; candidate
        supplies a lowercase variant 'kabc'. Airport.icao_code carries no
        DB-level case constraint (confirmed by direct model inspection),
        and this repository's own established convention for comparing a
        claimed identifier against a canonical one
        (app.services.evidence_attachment_guard._norm_text,
        strip+casefold) is case-insensitive - a byte-exact comparison
        would silently admit an effectively-duplicate Airport differing
        only in case."""
        with Session(_engine()) as session:
            _seed_airport(session, name="Existing", icao_code="KABC")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()

            with pytest.raises(ValueError, match="already has icao_code"):
                create_airport_from_approved_candidate(
                    session, candidate_id=candidate.id, review_id=review.id,
                    name="Foo", country="XX", icao_code="kabc",
                )
            session.refresh(candidate)
            assert candidate.resolved_airport_id is None
            assert session.query(Airport).count() == 1

    def test_create_padded_code_conflict_blocked_and_stored_stripped(self):
        """Attack: candidate supplies a code with incidental leading/
        trailing whitespace ('  KABC  ') that should neither evade the
        duplicate-code defense nor be persisted onto the new Airport
        verbatim (an un-stripped stored code would itself be a latent
        future duplicate-detection/lookup bug)."""
        with Session(_engine()) as session:
            _seed_airport(session, name="Existing", icao_code="KABC")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()

            with pytest.raises(ValueError, match="already has icao_code"):
                create_airport_from_approved_candidate(
                    session, candidate_id=candidate.id, review_id=review.id,
                    name="Foo", country="XX", icao_code="  KABC  ",
                )
            assert session.query(Airport).count() == 1

            # A genuinely distinct, padded code is accepted but stored
            # stripped, not verbatim.
            candidate2, _ = _seed_candidate_with_n_assertions(session, n=1, raw_name="Bar Field")
            session.commit()
            review2 = record_unknown_airport_candidate_review(
                session, candidate2, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate2.id, review_id=review2.id,
                name="Bar Field", country="XX", icao_code="  KDEF  ",
            )
            new_airport = session.get(Airport, result.created_airport_id)
            assert new_airport.icao_code == "KDEF"

    def test_create_similar_name_no_code_conflict_not_blocked(self):
        """Similar/generic names never gate CREATE_NEW_AIRPORT - only
        exact canonical code collisions do."""
        with Session(_engine()) as session:
            _seed_airport(session, name="Foo Regional Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name="Foo Regional Airport", country="XX",
            )
            assert session.query(Airport).count() == 2

    def test_create_stale_review_refused(self):
        with Session(_engine()) as session:
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            first = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            record_unknown_airport_candidate_review(
                session, candidate, action="DEFER", reason="wait", reviewer="human:y", supersedes_review_id=first.id,
            )
            session.commit()

            with pytest.raises(StaleReviewError):
                create_airport_from_approved_candidate(
                    session, candidate_id=candidate.id, review_id=first.id, name="Foo", country="XX",
                )
            assert session.query(Airport).count() == 0

    def test_create_already_resolved_refused(self):
        with Session(_engine()) as session:
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name="Foo", country="XX",
            )
            session.commit()

            with pytest.raises(AlreadyResolvedError):
                create_airport_from_approved_candidate(
                    session, candidate_id=candidate.id, review_id=review.id, name="Foo", country="XX",
                )
            assert session.query(Airport).count() == 1

    def test_missing_name_or_country_rejected(self):
        with Session(_engine()) as session:
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            with pytest.raises(ValueError, match="name is required"):
                create_airport_from_approved_candidate(session, candidate_id=candidate.id, review_id=review.id, name="", country="XX")
            with pytest.raises(ValueError, match="country is required"):
                create_airport_from_approved_candidate(session, candidate_id=candidate.id, review_id=review.id, name="Foo", country="")


# ---------------------------------------------------------------------------
# K. Exact repeat execution
# ---------------------------------------------------------------------------


class TestExactRepeatExecution:
    def test_match_repeat_execution_refused_not_idempotent(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            session.commit()

            with pytest.raises(AlreadyResolvedError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            assert session.query(Airport).count() == 1


# ---------------------------------------------------------------------------
# L/M/N. SourceAssertion transition, 1/3/many assertions, field preservation
# ---------------------------------------------------------------------------


class TestSourceAssertionTransition:
    @pytest.mark.parametrize("count", [1, 3, 7])
    def test_all_linked_assertions_transition(self, count):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=count)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            result = resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            assert len(result.moved_source_assertion_ids) == count
            for a in assertions:
                reloaded = session.get(SourceAssertion, a.id)
                assert reloaded.airport_id == real.id
                assert reloaded.unknown_airport_candidate_id is None

    def test_no_other_field_changes_during_transition(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            before = assertions[0]
            snapshot = (
                before.source_id, before.raw_relevant_text, before.identity_guard_decision,
                before.identity_guard_reason, before.review_state, before.evidence_quality,
                before.assertion_type, before.signal_id, before.source_locator, before.raw_fragment_hash,
                before.artifact_identity, before.created_at,
            )
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)

            after = session.get(SourceAssertion, assertions[0].id)
            after_snapshot = (
                after.source_id, after.raw_relevant_text, after.identity_guard_decision,
                after.identity_guard_reason, after.review_state, after.evidence_quality,
                after.assertion_type, after.signal_id, after.source_locator, after.raw_fragment_hash,
                after.artifact_identity, after.created_at,
            )
            assert snapshot == after_snapshot


# ---------------------------------------------------------------------------
# O/P. Rollback injection - real SQLAlchemy transaction rollback
# ---------------------------------------------------------------------------


class TestFailureAtomicity:
    def test_match_failure_after_partial_transition_rolls_back_completely(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=3)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()

            import app.services.unknown_airport_candidate_resolution as resolution_module

            real_linked = resolution_module._linked_assertions

            def _crash_after_partial_transition(session_arg, candidate_id):
                result = real_linked(session_arg, candidate_id)
                # Simulate a mid-loop crash: mutate the candidate and the
                # first assertion for real, then raise before the rest.
                candidate_obj = session_arg.get(UnknownAirportCandidate, candidate_id)
                candidate_obj.resolved_airport_id = real.id
                result[0].airport_id = real.id
                result[0].unknown_airport_candidate_id = None
                session_arg.flush()
                raise RuntimeError("simulated crash mid-transition")

            resolution_module._linked_assertions = _crash_after_partial_transition
            try:
                with pytest.raises(RuntimeError, match="simulated crash"):
                    resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            finally:
                resolution_module._linked_assertions = real_linked
            session.rollback()

            session.refresh(candidate)
            assert candidate.resolved_airport_id is None
            for a in assertions:
                reloaded = session.get(SourceAssertion, a.id)
                assert reloaded.unknown_airport_candidate_id == candidate.id
                assert reloaded.airport_id is None

    def test_create_failure_after_airport_flush_rolls_back_completely(self):
        with Session(_engine()) as session:
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=2)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            airports_before = session.query(Airport).count()

            import app.services.unknown_airport_candidate_resolution as resolution_module

            real_linked = resolution_module._linked_assertions

            def _crash_after_airport_created(session_arg, candidate_id):
                raise RuntimeError("simulated crash after airport flush")

            resolution_module._linked_assertions = _crash_after_airport_created
            try:
                with pytest.raises(RuntimeError, match="simulated crash"):
                    create_airport_from_approved_candidate(
                        session, candidate_id=candidate.id, review_id=review.id, name="Foo", country="XX",
                    )
            finally:
                resolution_module._linked_assertions = real_linked
            session.rollback()

            assert session.query(Airport).count() == airports_before
            session.refresh(candidate)
            assert candidate.resolved_airport_id is None
            for a in assertions:
                reloaded = session.get(SourceAssertion, a.id)
                assert reloaded.unknown_airport_candidate_id == candidate.id


# ---------------------------------------------------------------------------
# Q/R. Canonical side-effect firewall
# ---------------------------------------------------------------------------


class TestCanonicalSideEffectFirewall:
    def test_create_new_airport_touches_only_airport_count(self):
        with Session(_engine()) as session:
            candidate, _ = _seed_candidate_with_n_assertions(session, n=2)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            before = {
                "runways": session.query(Runway).count(),
                "runway_ends": session.query(RunwayEnd).count(),
                "installations": session.query(Installation).count(),
                "signals": session.query(Signal).count(),
                "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
                "sources": session.query(Source).count(),
            }
            create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name="Foo", country="XX",
            )
            after = {
                "runways": session.query(Runway).count(),
                "runway_ends": session.query(RunwayEnd).count(),
                "installations": session.query(Installation).count(),
                "signals": session.query(Signal).count(),
                "physical_installation_identities": session.query(PhysicalInstallationIdentity).count(),
                "sources": session.query(Source).count(),
            }
            assert before == after

    def test_match_existing_airport_touches_zero_new_airports(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            before = session.query(Airport).count()
            resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            assert session.query(Airport).count() == before

    def test_no_orm_construction_of_runway_installation_signal_in_module_source(self):
        import app.services.unknown_airport_candidate_resolution as resolution_module

        tree = ast.parse(inspect_module.getsource(resolution_module))
        forbidden = {"Runway", "RunwayEnd", "Installation", "Signal", "PhysicalInstallationIdentity"}
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()


class TestDownstreamContinuationIsNotYetReachable:
    """Adversarial-review finding: the original UAC4 implementation
    report's own "downstream continuation" claim ("no second code path
    is needed... the existing pipeline can resume unmodified") is
    OVERSTATED. resolve_candidate_to_existing_airport()/
    create_airport_from_approved_candidate() correctly and safely set
    SourceAssertion.airport_id, but never touch identity_guard_decision -
    and app.services.intelligence_review_persistence._identity_decision_from_assertion()
    treats ANY value other than the literal string "ATTACH_CONFIRMED"
    (which a candidate-linked assertion never carries - it is always
    "INSUFFICIENT_IDENTITY", set once at
    persist_candidate_linked_source_assertion() time and never revisited)
    as IDENTITY_NOT_CONFIRMED, which fails the entire downstream chain
    closed. A resolved assertion is therefore NOT YET structurally
    equivalent to ordinary known-airport evidence for downstream
    promotion/Signal-eligibility purposes - only for direct airport_id
    attribution (static export, direct Airport.source_assertions queries,
    etc.). This is judged non-blocking for THIS mission (design doc §11
    already named "whether the identity guard should be re-run
    post-resolution" as an open, unresolved implementation choice, not a
    UAC4 requirement; re-running the guard is a nontrivial, separately-
    scoped design decision of its own - what should happen if a re-run
    guard produces something other than ATTACH_CONFIRMED even after human
    resolution is exactly the kind of question "do not widen into UAC5"
    forbids deciding here) - but the original report's claim is corrected
    by this test and the review addendum."""

    def test_resolved_assertion_identity_guard_decision_remains_insufficient_identity(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            assert assertions[0].identity_guard_decision == "INSUFFICIENT_IDENTITY"
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)

            reloaded = session.get(SourceAssertion, assertions[0].id)
            assert reloaded.airport_id == real.id
            # The one, single fact this test locks in: resolution alone
            # does NOT make this assertion eligible for the existing
            # governed intelligence-review/promotion/Signal pipeline.
            assert reloaded.identity_guard_decision == "INSUFFICIENT_IDENTITY"

            from app.services.intelligence_review_persistence import _identity_decision_from_assertion
            from app.services.evidence_attachment_guard import AttachmentOutcome

            assert _identity_decision_from_assertion(reloaded) == AttachmentOutcome.INSUFFICIENT_IDENTITY


# ---------------------------------------------------------------------------
# S/T. International / Unicode
# ---------------------------------------------------------------------------


class TestInternationalCreateNewAirport:
    @pytest.mark.parametrize(
        "name,country,city",
        [
            ("Exempel Flygplats", "Sweden", "Exempelstad"),
            ("Aeroporto Exemplo", "Brazil", "Cidade Exemplo"),
            ("羽田空港", "Japan", "東京"),
        ],
    )
    def test_international_airport_creation_no_faa_lid_required(self, name, country, city):
        with Session(_engine()) as session:
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1, raw_name=name)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name=name, country=country, city=city,
            )
            new_airport = session.get(Airport, result.created_airport_id)
            assert new_airport.name == name
            assert new_airport.country == country
            assert new_airport.city == city
            assert new_airport.faa_code is None
            assert new_airport.iata_code is None
            assert new_airport.icao_code is None


# ---------------------------------------------------------------------------
# U. Migration-chain end-to-end
# ---------------------------------------------------------------------------


class TestMigrationChainParity:
    def test_end_to_end_against_genuinely_migrated_schema(self, tmp_path):
        db = tmp_path / "uac4_parity.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_reviews")
        conn.execute("DROP TABLE unknown_airport_candidates")
        replacement = "source_assertions__presetup"
        conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
        quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
        conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
        conn.execute("DROP TABLE source_assertions")
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
        conn.commit()
        conn.close()

        uac2a_migration.upgrade(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=2)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name="Foo Regional Airport", country="Fictionland",
            )
            session.commit()

            new_airport = session.get(Airport, result.created_airport_id)
            assert new_airport.name == "Foo Regional Airport"
            for a in assertions:
                reloaded = session.get(SourceAssertion, a.id)
                assert reloaded.airport_id == new_airport.id
                assert reloaded.unknown_airport_candidate_id is None
        engine.dispose()

    def test_match_existing_airport_end_to_end_against_genuinely_migrated_schema(self, tmp_path):
        """MATCH_EXISTING_AIRPORT's own genuinely-migrated-schema
        counterpart to the CREATE_NEW_AIRPORT test above - the mission's
        own attack list explicitly requires both resolution actions to be
        proven against a real migration chain, not create_all(), and not
        only the CREATE path."""
        db = tmp_path / "uac4_match_parity.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_reviews")
        conn.execute("DROP TABLE unknown_airport_candidates")
        replacement = "source_assertions__presetup"
        conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
        quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
        conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
        conn.execute("DROP TABLE source_assertions")
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
        conn.commit()
        conn.close()

        uac2a_migration.upgrade(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, assertions = _seed_candidate_with_n_assertions(session, n=2, raw_name="Bar Municipal Airport")
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            result = resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            session.commit()

            assert result.resolved_airport_id == real.id
            assert session.query(Airport).count() == 1
            for a in assertions:
                reloaded = session.get(SourceAssertion, a.id)
                assert reloaded.airport_id == real.id
                assert reloaded.unknown_airport_candidate_id is None
        engine.dispose()


# ---------------------------------------------------------------------------
# V. Contradictory later review
# ---------------------------------------------------------------------------


class TestContradictoryLaterReview:
    def test_review_after_resolution_never_triggers_re_resolution(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            session.commit()

            # A human later records a contradictory review - allowed to
            # record (UAC1's own, unmodified persistence never checks
            # resolved_airport_id), but must never itself mutate canonical
            # state, and any attempt to EXECUTE it must refuse.
            other = _seed_airport(session, name="Other Airport")
            later_review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="actually this one", reviewer="human:y",
                matched_airport_id=other.id, supersedes_review_id=review.id,
            )
            session.commit()

            session.refresh(candidate)
            assert candidate.resolved_airport_id == real.id  # unchanged by merely recording the later review

            with pytest.raises(AlreadyResolvedError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=later_review.id)
            session.refresh(candidate)
            assert candidate.resolved_airport_id == real.id  # still unchanged after the refused attempt


# ---------------------------------------------------------------------------
# W. Direct malformed state fails loud
# ---------------------------------------------------------------------------


class TestMalformedStateFailsLoud:
    def test_resolved_airport_id_set_via_raw_orm_blocks_all_execution(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            other = _seed_airport(session, name="Other Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            # Fabricate a resolved_airport_id via direct attribute
            # assignment - immutability guard only blocks OTHER fields,
            # this one is the intentionally mutable field (UAC1 design).
            candidate.resolved_airport_id = other.id
            session.commit()

            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()

            with pytest.raises(AlreadyResolvedError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)

    def test_resolved_with_latest_review_reject_still_blocks_execution(self):
        """Case E: resolved_airport_id is fabricated non-NULL, but the
        candidate's latest review is REJECT_CANDIDATE (a decision that,
        on its own, never carries any canonical consequence). Execution
        must still refuse via AlreadyResolvedError - the resolved marker
        alone is dispositive, regardless of what the latest review says."""
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            candidate.resolved_airport_id = real.id
            session.commit()
            record_unknown_airport_candidate_review(
                session, candidate, action="REJECT_CANDIDATE", reason="x", reviewer="human:x",
            )
            session.commit()

            with pytest.raises(AlreadyResolvedError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=999999)
            with pytest.raises(AlreadyResolvedError):
                create_airport_from_approved_candidate(
                    session, candidate_id=candidate.id, review_id=999999, name="Foo", country="XX",
                )

    def test_resolved_airport_id_referencing_deleted_airport_fails_loud(self):
        """Case F: resolved_airport_id points at an Airport id that no
        longer exists (the Airport was deleted after resolution - the FK
        itself would ordinarily block this under foreign_keys=ON
        enforcement, but this proves the service's OWN logic does not
        depend on that enforcement being active to stay safe: it never
        re-validates resolved_airport_id's target on the already-resolved
        path, since AlreadyResolvedError fires purely off resolved_airport_id
        being non-NULL and never dereferences the dangling id)."""
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate, _ = _seed_candidate_with_n_assertions(session, n=1)
            session.commit()
            candidate.resolved_airport_id = real.id
            session.commit()
            # Delete the Airport out from under the candidate directly via
            # raw SQL, bypassing FK enforcement (this engine has no
            # foreign_keys=ON pragma, matching the rest of this file).
            session.execute(Airport.__table__.delete().where(Airport.id == real.id))
            session.flush()

            with pytest.raises(AlreadyResolvedError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=999999)


class TestZeroAssertionCandidate:
    """Mission-required policy: a candidate is itself sufficient evidence/
    history to resolve - resolution must not require at least one linked
    SourceAssertion (e.g. a manually-entered claim, or one whose evidence
    was never attached)."""

    def test_match_zero_assertions_succeeds(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate = find_or_create_unknown_airport_candidate(session, raw_name="No Evidence Airport").candidate
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()
            result = resolve_candidate_to_existing_airport(session, candidate_id=candidate.id, review_id=review.id)
            assert result.moved_source_assertion_ids == ()
            session.refresh(candidate)
            assert candidate.resolved_airport_id == real.id

    def test_create_zero_assertions_succeeds(self):
        with Session(_engine()) as session:
            candidate = find_or_create_unknown_airport_candidate(session, raw_name="No Evidence Airport").candidate
            session.commit()
            review = record_unknown_airport_candidate_review(
                session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:x",
            )
            session.commit()
            result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id, name="No Evidence Airport", country="XX",
            )
            assert result.moved_source_assertion_ids == ()
            new_airport = session.get(Airport, result.created_airport_id)
            assert new_airport.name == "No Evidence Airport"


class TestCrossCandidateReviewBinding:
    """A review_id genuinely belonging to a DIFFERENT candidate must never
    authorize execution against this candidate - safe by construction,
    since get_latest_unknown_airport_candidate_review() is itself scoped
    to candidate_id, so a foreign review's id can practically never equal
    this candidate's own current review id (globally unique ids)."""

    def test_review_id_from_a_different_candidate_is_refused(self):
        with Session(_engine()) as session:
            real = _seed_airport(session, name="Real Airport")
            candidate_a, _ = _seed_candidate_with_n_assertions(session, n=1, raw_name="Candidate A Airport")
            candidate_b, _ = _seed_candidate_with_n_assertions(session, n=1, raw_name="Candidate B Airport")
            session.commit()
            review_for_b = record_unknown_airport_candidate_review(
                session, candidate_b, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:x",
                matched_airport_id=real.id,
            )
            session.commit()

            # Attempt to resolve candidate A using candidate B's own,
            # genuinely-current review id.
            with pytest.raises(StaleReviewError):
                resolve_candidate_to_existing_airport(session, candidate_id=candidate_a.id, review_id=review_for_b.id)
            session.refresh(candidate_a)
            assert candidate_a.resolved_airport_id is None
            session.refresh(candidate_b)
            assert candidate_b.resolved_airport_id is None


# ---------------------------------------------------------------------------
# X. Real DB no-access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_the_real_database_path_or_sessionlocal(self):
        import app.services.unknown_airport_candidate_resolution as resolution_module

        source = inspect_module.getsource(resolution_module)
        assert "runway_safe.db" not in source
        assert "import SessionLocal" not in source
        assert "SessionLocal()" not in source
        assert ".commit(" not in source


# ---------------------------------------------------------------------------
# Nonexistent candidate
# ---------------------------------------------------------------------------


class TestNonexistentCandidate:
    def test_match_nonexistent_candidate(self):
        with Session(_engine()) as session:
            with pytest.raises(ValueError, match="does not exist"):
                resolve_candidate_to_existing_airport(session, candidate_id=999999, review_id=1)

    def test_create_nonexistent_candidate(self):
        with Session(_engine()) as session:
            with pytest.raises(ValueError, match="does not exist"):
                create_airport_from_approved_candidate(session, candidate_id=999999, review_id=1, name="Foo", country="XX")
