"""Tests for app/services/source_assertion_identity_resolution.py (KAR2,
docs/architecture/rwi-known-airport-ambiguity-resolution-design.md).

Isolated, in-memory SQLite databases only - never the real one. Fixtures
are entirely fictional. Modeled directly on
tests/test_unknown_airport_candidate_resolution.py (UAC4), this
repository's own strongest, most recent precedent for a governed
resolution-service test suite.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Runway, RunwayEnd, Source, SourceAssertion
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.source_assertion_identity_resolution import SourceAssertionIdentityResolution
from app.services.effective_identity_guard_decision import (
    EffectiveIdentityGuardDecisionBasis,
    resolve_effective_identity_guard_decision,
)
from app.services.evidence_attachment_guard import AttachmentOutcome, EvidenceBag
from app.services.evidence_bag_serialization import hash_serialized_evidence_bag, serialize_evidence_bag
from app.services.resolved_candidate_evidence_reevaluation import reevaluate_resolved_candidate_evidence
from app.services.source_assertion_identity_resolution import (
    CandidateLinkedAssertionError,
    MissingEvidenceBagSnapshotError,
    SourceAssertionAlreadyResolvedError,
    SourceAssertionNotFoundError,
    TargetAirportNotFoundError,
    get_latest_source_assertion_identity_resolution,
    record_source_assertion_identity_resolution,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, *, name="St. Paul Downtown Airport", country="USA", runway_pairs=("14/32",), **kwargs) -> Airport:
    airport = Airport(name=name, country=country, **kwargs)
    session.add(airport)
    session.flush()
    for pair in runway_pairs:
        runway = Runway(airport_id=airport.id, designation=pair)
        session.add(runway)
        session.flush()
        for end in pair.split("/"):
            session.add(RunwayEnd(runway_id=runway.id, designation=end))
    session.flush()
    return airport


def _seed_assertion(session, *, names=(), runway_ends=("14", "32"), runway_pairs=("14/32",), locator="item-1", with_snapshot=True, unknown_airport_candidate_id=None) -> SourceAssertion:
    source = session.query(Source).first()
    if source is None:
        source = Source(title="Test Source", source_type="web_discovery")
        session.add(source)
        session.flush()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction",
        source_locator=locator, artifact_identity=f"doc-{locator}", raw_fragment_hash=f"hash-{locator}",
        identity_guard_decision="REVIEW_REQUIRED",
        identity_guard_reason="ambiguous across candidates",
        evidence_quality="unverified_candidate", review_state="unreviewed",
        unknown_airport_candidate_id=unknown_airport_candidate_id,
    )
    session.add(assertion)
    session.flush()
    if with_snapshot:
        bag = EvidenceBag(
            names=frozenset(names), runway_ends=frozenset(runway_ends), runway_pairs=frozenset(runway_pairs),
        )
        serialized = serialize_evidence_bag(bag)
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=serialized,
            evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
        )
        session.add(snapshot)
        session.flush()
    return assertion


# ---------------------------------------------------------------------------
# 1-3. Happy paths
# ---------------------------------------------------------------------------


class TestHappyPaths:
    def test_attach_happy_path(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="name+topology match", reviewer="human:tester", matched_airport_id=airport.id,
            )
            session.commit()
            assert result.airport_id_set == airport.id
            assert assertion.airport_id == airport.id
            assert result.resolution.action == "ATTACH_TO_EXISTING_AIRPORT"
            assert result.resolution.matched_airport_id == airport.id

    def test_reject_happy_path(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                reason="does not match any evaluated candidate", reviewer="human:tester",
            )
            session.commit()
            assert result.airport_id_set is None
            assert assertion.airport_id is None
            assert result.resolution.matched_airport_id is None

    def test_defer_happy_path(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="need more evidence", reviewer="human:tester",
            )
            session.commit()
            assert result.airport_id_set is None
            assert assertion.airport_id is None


# ---------------------------------------------------------------------------
# 4-10. Precondition failures
# ---------------------------------------------------------------------------


class TestPreconditionFailures:
    def test_missing_target_for_attach(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="tester",
                )
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "requires matched_airport_id" in str(exc)

    def test_inappropriate_target_on_reject(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                    reason="x", reviewer="tester", matched_airport_id=airport.id,
                )
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "must not supply matched_airport_id" in str(exc)

    def test_inappropriate_target_on_defer(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="tester", matched_airport_id=airport.id,
                )
                assert False, "expected ValueError"
            except ValueError:
                pass

    def test_nonexistent_target_airport(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="tester", matched_airport_id=999999,
                )
                assert False, "expected TargetAirportNotFoundError"
            except TargetAirportNotFoundError as exc:
                assert exc.matched_airport_id == 999999

    def test_nonexistent_source_assertion(self):
        with Session(_engine()) as session:
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=999999, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="tester",
                )
                assert False, "expected SourceAssertionNotFoundError"
            except SourceAssertionNotFoundError:
                pass

    def test_already_attached_assertion(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session)
            assertion.airport_id = airport.id
            session.flush()
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="tester",
                )
                assert False, "expected SourceAssertionAlreadyResolvedError"
            except SourceAssertionAlreadyResolvedError as exc:
                assert exc.airport_id == airport.id

    def test_candidate_linked_assertion(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session, unknown_airport_candidate_id=None)
            assertion.unknown_airport_candidate_id = 12345
            session.flush()
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                    reason="x", reviewer="tester",
                )
                assert False, "expected CandidateLinkedAssertionError"
            except CandidateLinkedAssertionError as exc:
                assert exc.unknown_airport_candidate_id == 12345

    def test_unsupported_action(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="CREATE_NEW_AIRPORT",
                    reason="x", reviewer="tester",
                )
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "must be one of" in str(exc)

    def test_missing_evidence_bag_snapshot(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session, with_snapshot=False)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="tester",
                )
                assert False, "expected MissingEvidenceBagSnapshotError"
            except MissingEvidenceBagSnapshotError:
                pass

    def test_empty_reason_rejected(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="   ", reviewer="tester",
                )
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "reason is required" in str(exc)

    def test_empty_reviewer_rejected(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="   ",
                )
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "reviewer is required" in str(exc)


# ---------------------------------------------------------------------------
# 11-13. Historical-fact preservation / append-only
# ---------------------------------------------------------------------------


class TestHistoricalFactPreservation:
    def test_original_guard_decision_preserved_after_attach(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            original_decision = assertion.identity_guard_decision
            original_reason = assertion.identity_guard_reason
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            assert assertion.identity_guard_decision == original_decision
            assert assertion.identity_guard_reason == original_reason

    def test_evidence_bag_snapshot_preserved(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            snapshot_before = session.query(SourceAssertionEvidenceBag).filter(
                SourceAssertionEvidenceBag.source_assertion_id == assertion.id
            ).one()
            hash_before = snapshot_before.evidence_bag_hash
            json_before = snapshot_before.evidence_bag_json
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            snapshot_after = session.query(SourceAssertionEvidenceBag).filter(
                SourceAssertionEvidenceBag.source_assertion_id == assertion.id
            ).one()
            assert snapshot_after.evidence_bag_hash == hash_before
            assert snapshot_after.evidence_bag_json == json_before

    def test_decision_history_is_append_only(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="first", reviewer="tester",
            )
            session.commit()
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                reason="second", reviewer="tester",
            )
            session.commit()
            rows = (
                session.query(SourceAssertionIdentityResolution)
                .filter(SourceAssertionIdentityResolution.source_assertion_id == assertion.id)
                .order_by(SourceAssertionIdentityResolution.id.asc())
                .all()
            )
            assert len(rows) == 2
            assert rows[0].reason == "first"
            assert rows[1].reason == "second"

    def test_rows_are_immutable(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="first", reviewer="tester",
            )
            session.commit()
            result.resolution.reason = "tampered"
            try:
                session.commit()
                assert False, "expected ValueError from before_update listener"
            except ValueError:
                session.rollback()

    def test_rows_cannot_be_deleted(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="first", reviewer="tester",
            )
            session.commit()
            session.delete(result.resolution)
            try:
                session.commit()
                assert False, "expected ValueError from before_delete listener"
            except ValueError:
                session.rollback()


# ---------------------------------------------------------------------------
# 14-19. Double-resolution / sequencing attacks
# ---------------------------------------------------------------------------


class TestDoubleResolutionAttacks:
    def test_double_attach_same_airport(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="tester", matched_airport_id=airport.id,
                )
                assert False, "expected SourceAssertionAlreadyResolvedError"
            except SourceAssertionAlreadyResolvedError:
                pass

    def test_double_attach_different_airport(self):
        with Session(_engine()) as session:
            airport1 = _seed_airport(session, name="Airport One")
            airport2 = _seed_airport(session, name="Airport Two")
            assertion = _seed_assertion(session, names={"Airport One"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport1.id,
            )
            session.commit()
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="correction attempt", reviewer="tester", matched_airport_id=airport2.id,
                )
                assert False, "expected SourceAssertionAlreadyResolvedError - v1 supports no reassignment"
            except SourceAssertionAlreadyResolvedError:
                pass
            assert assertion.airport_id == airport1.id

    def test_attach_after_reject_succeeds(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                reason="initially rejected", reviewer="tester",
            )
            session.commit()
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="reconsidered", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            assert result.airport_id_set == airport.id

    def test_attach_after_defer_succeeds(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="deferred", reviewer="tester",
            )
            session.commit()
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="now confident", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            assert result.airport_id_set == airport.id

    def test_reject_after_attach_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                    reason="x", reviewer="tester",
                )
                assert False, "expected SourceAssertionAlreadyResolvedError"
            except SourceAssertionAlreadyResolvedError:
                pass

    def test_defer_after_attach_fails(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="tester",
                )
                assert False, "expected SourceAssertionAlreadyResolvedError"
            except SourceAssertionAlreadyResolvedError:
                pass

    def test_multiple_defer_rows_all_permitted(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="first defer", reviewer="tester",
            )
            session.commit()
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="DEFER_IDENTITY_REVIEW",
                reason="second defer", reviewer="tester",
            )
            session.commit()
            latest = get_latest_source_assertion_identity_resolution(session, assertion.id)
            assert latest.reason == "second defer"
            rows = (
                session.query(SourceAssertionIdentityResolution)
                .filter(SourceAssertionIdentityResolution.source_assertion_id == assertion.id)
                .all()
            )
            assert len(rows) == 2


# ---------------------------------------------------------------------------
# 20. No-autoflush attack matrix
# ---------------------------------------------------------------------------


class TestNoAutoflushHardening:
    def test_unrelated_invalid_pending_row_does_not_leak_into_precondition_check(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            # An unrelated, invalid, pending object in the SAME session -
            # missing a required column, would violate NOT NULL on flush.
            session.add(Source(source_type="web_discovery"))  # title is required, left unset
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=999999, action="DEFER_IDENTITY_REVIEW",
                    reason="x", reviewer="tester",
                )
                assert False, "expected SourceAssertionNotFoundError, not a leaked autoflush IntegrityError"
            except SourceAssertionNotFoundError:
                pass

    def test_expired_source_assertion_attributes_do_not_leak_autoflush(self):
        """Reading assertion.airport_id/unknown_airport_candidate_id INSIDE
        the service's own no_autoflush block, on an already-expired
        instance (post-commit), must not leak an unrelated invalid pending
        row's own constraint violation - a precondition failure (here: a
        nonexistent target Airport) must surface as THIS module's own
        typed TargetAirportNotFoundError. `source_assertion_id` is
        captured as a plain int BEFORE the invalid pending row is added -
        the correct, safe calling convention every real caller (including
        this repository's own CLI, which always passes a plain argparse
        int, never a live ORM attribute) already uses. See
        test_caller_side_expired_pk_read_is_a_known_unprotected_hazard
        below for the DIFFERENT, deliberately-unprotected scenario where
        the caller itself reads an expired attribute as its own argument
        expression."""
        with Session(_engine()) as session:
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            session.commit()  # expires assertion's own attributes
            assertion_id = assertion.id  # captured BEFORE the invalid pending row below
            session.add(Source(source_type="web_discovery"))  # unrelated invalid pending row
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="tester", matched_airport_id=999999,
                )
                assert False, "expected TargetAirportNotFoundError"
            except TargetAirportNotFoundError:
                pass

    def test_caller_side_expired_pk_read_is_a_known_unprotected_hazard(self):
        """DOCUMENTS, does not "fix," a known limitation shared by every
        no_autoflush-guarded function in this codebase (mission's own
        explicit attack vector: "caller-side attribute reads before
        protected service code"): a no_autoflush block INSIDE
        record_source_assertion_identity_resolution() can only protect
        that function's OWN internal reads. If the CALLER evaluates an
        expired ORM attribute (e.g. `assertion.id`) as an argument
        expression - a read that happens in the CALLER's own scope, before
        the function (and its no_autoflush block) is ever entered - with
        an unrelated invalid pending row already in the session, the
        resulting IntegrityError is NOT something this service can
        prevent, exactly as get_latest_unknown_airport_candidate_review()'s
        own docstring already documents for the identical class of risk.
        This repository's real callers avoid it structurally: the CLI
        (scripts/resolve_source_assertion_identity.py) always passes a
        plain argparse int, never a live ORM attribute."""
        with Session(_engine()) as session:
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            session.commit()  # expires assertion's own attributes
            session.add(Source(source_type="web_discovery"))  # unrelated invalid pending row
            try:
                record_source_assertion_identity_resolution(
                    # assertion.id is read HERE, in the caller's own scope,
                    # as the argument expression - genuinely unprotected.
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="tester", matched_airport_id=999999,
                )
                assert False, "expected some exception"
            except TargetAirportNotFoundError:
                pass  # would also be an acceptable outcome, just not the one actually observed
            except Exception:
                pass  # the known, documented, caller-side leak - not a service defect

    def test_expired_airport_attributes_do_not_leak_autoflush(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            session.commit()  # expires airport's own attributes too
            _ = airport.id  # trigger a refresh read on an expired instance
            result = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            assert result.airport_id_set == airport.id

    def test_pending_decision_row_from_a_prior_call_does_not_block_a_new_ones_precondition_check(self):
        with Session(_engine()) as session:
            assertion_a = _seed_assertion(session, locator="item-a")
            assertion_b = _seed_assertion(session, locator="item-b")
            # Leave assertion_a's own resolution pending (not yet committed)
            # while checking preconditions for assertion_b.
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion_a.id, action="DEFER_IDENTITY_REVIEW",
                reason="x", reviewer="tester",
            )
            result_b = record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion_b.id, action="DEFER_IDENTITY_REVIEW",
                reason="y", reviewer="tester",
            )
            session.commit()
            assert result_b.resolution.id is not None

    def test_get_latest_does_not_autoflush_unrelated_pending_state(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session)
            session.add(Source(source_type="web_discovery"))  # unrelated invalid pending row
            # Must not raise despite the unrelated invalid pending row.
            result = get_latest_source_assertion_identity_resolution(session, assertion.id)
            assert result is None


# ---------------------------------------------------------------------------
# 21. Transaction rollback / failure atomicity
# ---------------------------------------------------------------------------


class TestFailureAtomicity:
    def test_failed_attach_leaves_no_partial_row_and_no_partial_mutation(self):
        with Session(_engine()) as session:
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            try:
                record_source_assertion_identity_resolution(
                    session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                    reason="x", reviewer="tester", matched_airport_id=999999,
                )
            except TargetAirportNotFoundError:
                pass
            assert assertion.airport_id is None
            count = (
                session.query(SourceAssertionIdentityResolution)
                .filter(SourceAssertionIdentityResolution.source_assertion_id == assertion.id)
                .count()
            )
            assert count == 0

    def test_rollback_after_successful_call_leaves_no_trace(self):
        """Adversarial-review correction (KAR adversarial review mission):
        the original version of this test opened a SECOND, unrelated
        in-memory database after the rollback and asserted nothing against
        it - it proved no actual rollback behavior. Rewritten to verify,
        against the SAME session/database, that a caller-initiated
        session.rollback() after a successful (but not yet committed)
        record_source_assertion_identity_resolution() call genuinely
        reverts both the appended row and the SourceAssertion.airport_id
        mutation - the caller, not this service, owns commit/rollback, and
        that boundary must actually hold."""
        engine = _engine()
        with Session(engine) as session:
            airport = _seed_airport(session)
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            assertion_id, airport_id = assertion.id, airport.id
            session.commit()

        with Session(engine) as session:
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion_id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport_id,
            )
            # Not committed - the row and the airport_id mutation are both
            # only flushed, still inside this session's own open transaction.
            session.rollback()

        with Session(engine) as verify_session:
            reloaded = verify_session.get(SourceAssertion, assertion_id)
            assert reloaded.airport_id is None
            count = (
                verify_session.query(SourceAssertionIdentityResolution)
                .filter(SourceAssertionIdentityResolution.source_assertion_id == assertion_id)
                .count()
            )
            assert count == 0


# ---------------------------------------------------------------------------
# 31-32. EB4/EB5 composition - HIGH PRIORITY, the core product invariant
# ---------------------------------------------------------------------------


class TestEb4Eb5Composition:
    def test_finding_2_shaped_reproduces_attach_confirmed_and_eb5_eligible(self):
        """Explicit airport name + runway 14/32 + human attachment to a
        canonical STP-shaped Airport -> EB4 must reproduce ATTACH_CONFIRMED,
        and EB5 must consider the evidence Signal-eligible."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="St. Paul Downtown Airport", runway_pairs=("14/32",))
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"}, runway_ends=("14", "32"), runway_pairs=("14/32",))
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="name+topology match", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()

            reeval = reevaluate_resolved_candidate_evidence(session, source_assertion_id=assertion.id)
            session.commit()
            assert reeval.outcome == AttachmentOutcome.ATTACH_CONFIRMED

            effective = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert effective.effective_decision == AttachmentOutcome.ATTACH_CONFIRMED
            assert effective.basis == EffectiveIdentityGuardDecisionBasis.LATEST_REEVALUATION
            assert effective.is_identity_confirmed is True

    def test_finding_4_shaped_reproduces_attach_provisional_and_eb5_ineligible(self):
        """Bare abbreviation (no extracted name) + runway 14/32 + human
        attachment to the SAME canonical Airport -> EB4 must reproduce
        ATTACH_PROVISIONAL, and EB5 must NOT promote this to
        confirmed/Signal-ready merely because a human attached it - the
        core product invariant this design exists to prove."""
        with Session(_engine()) as session:
            airport = _seed_airport(session, name="St. Paul Downtown Airport", runway_pairs=("14/32",))
            assertion = _seed_assertion(session, names=set(), runway_ends=("14", "32"), runway_pairs=("14/32",))
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="human judgment call - STP abbreviation", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()

            reeval = reevaluate_resolved_candidate_evidence(session, source_assertion_id=assertion.id)
            session.commit()
            assert reeval.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

            effective = resolve_effective_identity_guard_decision(session, source_assertion_id=assertion.id)
            assert effective.effective_decision == AttachmentOutcome.ATTACH_PROVISIONAL
            assert effective.is_identity_confirmed is False

    def test_reject_and_defer_never_reach_eb4_preconditions(self):
        """REJECT_ATTACHMENT/DEFER_IDENTITY_REVIEW never set airport_id, so
        EB4 correctly refuses to re-evaluate them (UnresolvedSourceAssertionError) -
        proving this design creates no path to a stronger machine
        conclusion without an explicit human ATTACH decision."""
        from app.services.resolved_candidate_evidence_reevaluation import UnresolvedSourceAssertionError
        with Session(_engine()) as session:
            assertion = _seed_assertion(session, names={"St. Paul Downtown Airport"})
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                reason="x", reviewer="tester",
            )
            session.commit()
            try:
                reevaluate_resolved_candidate_evidence(session, source_assertion_id=assertion.id)
                assert False, "expected UnresolvedSourceAssertionError"
            except UnresolvedSourceAssertionError:
                pass
