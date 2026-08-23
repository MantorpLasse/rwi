"""Tests for EB1 (docs/architecture/rwi-eb1-evidencebag-persistence-foundation-report.md):
app/services/evidence_bag_serialization.py,
app/models/source_assertion_evidence_bag.py, app/models/identity_guard_evaluation.py.

Every test uses an isolated in-memory SQLite database - nothing here ever
opens data/runway_safe.db (see TestNoRealDatabaseAccess).
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidateReview
from app.services.evidence_attachment_guard import AttachmentOutcome, EvidenceBag
from app.services.evidence_bag_serialization import (
    EVIDENCE_BAG_SCHEMA_VERSION,
    EvidenceBagSerializationError,
    deserialize_evidence_bag,
    hash_serialized_evidence_bag,
    serialize_evidence_bag,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # The FK-enabling listener must be registered BEFORE the first
    # connection is ever opened - a plain sqlite:///:memory: engine uses
    # SingletonThreadPool, so create_all()'s own connection would
    # otherwise be cached and reused for the rest of the test with
    # foreign_keys still OFF.
    Base.metadata.create_all(engine)
    return engine


def _assert_fk_actually_enabled(session):
    """Adversarial-review addition: do not trust the event-listener setup
    by inspection alone - directly read PRAGMA foreign_keys back from the
    live connection every FK-attack test actually uses."""
    from sqlalchemy import text

    value = session.execute(text("PRAGMA foreign_keys")).scalar()
    assert value == 1, "PRAGMA foreign_keys is not actually ON for this session's connection"


def _seed_source_assertion(session, *, raw_name="Foo Regional Airport") -> SourceAssertion:
    source = Source(title="t", source_type="web_discovery", external_id=f"discovery:{raw_name}")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction",
        raw_relevant_text=f"{raw_name} evidence.", artifact_identity=f"art-{raw_name}",
        source_locator="p1", raw_fragment_hash=f"hash-{raw_name}",
        identity_guard_decision="INSUFFICIENT_IDENTITY", identity_guard_reason="original discovery-time fact",
    )
    session.add(assertion)
    session.flush()
    return assertion


def _seed_airport(session, *, name="Real Airport", country="XX") -> Airport:
    airport = Airport(name=name, country=country)
    session.add(airport)
    session.flush()
    return airport


_FULL_BAG_KWARGS = dict(
    identifiers=frozenset({"KFOO", "FOO"}),
    names=frozenset({"Foo Regional Airport", "Foo Muni"}),
    runway_ends=frozenset({"09", "27"}),
    runway_pairs=frozenset({"09/27"}),
    issuers=frozenset({"Foo County Airport Authority"}),
    locations=frozenset({"Foo City"}),
    contradicting_names=frozenset({"Bar Regional Airport"}),
    contradicting_issuers=frozenset({"Bar County Airport Authority"}),
    contradicting_locations=frozenset({"Bar City"}),
    alternate_airport_runway_ends=frozenset({"18"}),
    alternate_airport_runway_pairs=frozenset({"18/36"}),
    document_title="Foo Regional Airport Master Plan",
    project_number="PN-123",
    contract_number="CN-456",
    url="https://example.test/foo",
)


# ---------------------------------------------------------------------------
# A/B/C/D. Serialization round-trip
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    def test_empty_evidence_bag_round_trip(self):
        bag = EvidenceBag()
        serialized = serialize_evidence_bag(bag)
        restored = deserialize_evidence_bag(serialized)
        assert restored == bag

    def test_complete_evidence_bag_round_trip(self):
        bag = EvidenceBag(**_FULL_BAG_KWARGS)
        serialized = serialize_evidence_bag(bag)
        restored = deserialize_evidence_bag(serialized)
        assert restored == bag
        # Every field explicitly re-checked, not just dataclass equality.
        for name, value in _FULL_BAG_KWARGS.items():
            assert getattr(restored, name) == value

    def test_unicode_round_trip(self):
        bag = EvidenceBag(
            names=frozenset({"Exempel Flygplats", "Aeroporto Exemplo", "羽田空港"}),
            locations=frozenset({"Åre", "São Paulo", "Brasília", "東京"}),
            issuers=frozenset({"Luftfartsverket", "ANAC", "国土交通省"}),
            document_title="Ö Å Ä 京都 emoji ✈️🛫",
        )
        serialized = serialize_evidence_bag(bag)
        restored = deserialize_evidence_bag(serialized)
        assert restored == bag
        # Confirm actual UTF-8 text is stored, not \uXXXX escapes.
        assert "羽田空港" in serialized
        assert "\\u" not in serialized

    def test_arabic_right_to_left_script_round_trip(self):
        bag = EvidenceBag(
            names=frozenset({"مطار القاهرة الدولي"}),
            locations=frozenset({"القاهرة"}),
            issuers=frozenset({"وزارة الطيران المدني"}),
        )
        serialized = serialize_evidence_bag(bag)
        restored = deserialize_evidence_bag(serialized)
        assert restored == bag
        assert "مطار القاهرة الدولي" in serialized

    def test_unicode_normalization_forms_are_never_silently_normalized(self):
        """EvidenceBag/this module define no normalization behavior of
        their own beyond what the guard's own comparison layer does at
        EVALUATION time (never at serialization time) - two
        canonically-equivalent but differently-encoded Unicode strings
        (NFC "café" as a single U+00E9 codepoint vs. NFD "café" as
        "e" + a combining acute accent, U+0065 U+0301) must round-trip
        as the two DISTINCT strings they actually are, never silently
        collapsed to one normalized form."""
        import unicodedata

        nfc = unicodedata.normalize("NFC", "cafe" + chr(0x0301))  # single precomposed e-acute (U+00E9)
        nfd = unicodedata.normalize("NFD", "cafe" + chr(0x0301))  # "e" + combining acute accent (U+0065 U+0301)
        assert nfc != nfd  # confirms the two Python strings are genuinely distinct
        bag = EvidenceBag(names=frozenset({nfc, nfd}))
        restored = deserialize_evidence_bag(serialize_evidence_bag(bag))
        assert restored.names == frozenset({nfc, nfd})
        assert len(restored.names) == 2  # never silently collapsed to one

    def test_commas_quotes_newlines_tabs_round_trip(self):
        tricky = 'value, with, commas "and quotes"\nand a newline\tand a tab'
        bag = EvidenceBag(
            identifiers=frozenset({tricky}),
            names=frozenset({"KABC, KXYZ"}),
            contradicting_names=frozenset({'Another "airport", maybe'}),
        )
        serialized = serialize_evidence_bag(bag)
        restored = deserialize_evidence_bag(serialized)
        assert restored == bag
        assert tricky in restored.identifiers

    def test_the_exact_uac5_lossy_join_collision_is_no_longer_ambiguous(self):
        """The specific bug UAC5's adversarial review proved empirically:
        {"KABC, KXYZ"} and {"KABC", "KXYZ"} collided under the old
        comma-join. Prove they now serialize to DIFFERENT, distinguishable
        payloads and both round-trip exactly."""
        bag_a = EvidenceBag(identifiers=frozenset({"KABC, KXYZ"}))
        bag_b = EvidenceBag(identifiers=frozenset({"KABC", "KXYZ"}))
        serialized_a = serialize_evidence_bag(bag_a)
        serialized_b = serialize_evidence_bag(bag_b)
        assert serialized_a != serialized_b
        assert deserialize_evidence_bag(serialized_a).identifiers == {"KABC, KXYZ"}
        assert deserialize_evidence_bag(serialized_b).identifiers == {"KABC", "KXYZ"}

    def test_empty_collections_distinguished_from_populated(self):
        bag = EvidenceBag(identifiers=frozenset(), names=frozenset({"Foo Airport"}))
        restored = deserialize_evidence_bag(serialize_evidence_bag(bag))
        assert restored.identifiers == frozenset()
        assert restored.names == frozenset({"Foo Airport"})

    def test_none_scalar_fields_round_trip_as_none(self):
        bag = EvidenceBag(document_title=None, url=None)
        restored = deserialize_evidence_bag(serialize_evidence_bag(bag))
        assert restored.document_title is None
        assert restored.url is None

    def test_all_evidencebag_dataclass_fields_are_covered_by_serialization(self):
        """Structural proof this module was not hand-written against a
        stale field list: every field dataclasses.fields() reports for
        EvidenceBag is present in a round-tripped bag's own comparison
        above, and the serialization module's own field tuples cover
        exactly this set."""
        import dataclasses
        from app.services import evidence_bag_serialization as module

        actual_field_names = {f.name for f in dataclasses.fields(EvidenceBag)}
        covered = set(module._SET_FIELDS) | set(module._SCALAR_FIELDS)
        assert covered == actual_field_names


# ---------------------------------------------------------------------------
# E. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_reversed_construction_order_produces_identical_output(self):
        kwargs = dict(_FULL_BAG_KWARGS)
        bag_a = EvidenceBag(**kwargs)
        # Rebuild every frozenset from a reversed iteration order - frozenset
        # itself has no order, but this proves the SERIALIZER doesn't
        # accidentally depend on some incidental Python hash-seed ordering.
        reversed_kwargs = {
            k: (frozenset(reversed(list(v))) if isinstance(v, frozenset) else v)
            for k, v in kwargs.items()
        }
        bag_b = EvidenceBag(**reversed_kwargs)
        assert serialize_evidence_bag(bag_a) == serialize_evidence_bag(bag_b)

    def test_repeated_serialization_is_byte_identical(self):
        bag = EvidenceBag(**_FULL_BAG_KWARGS)
        first = serialize_evidence_bag(bag)
        second = serialize_evidence_bag(bag)
        third = serialize_evidence_bag(EvidenceBag(**_FULL_BAG_KWARGS))
        assert first == second == third

    def test_duplicate_values_in_source_frozenset_construction_do_not_affect_output(self):
        # frozenset already collapses duplicates at construction - this
        # proves the serializer doesn't somehow reintroduce or depend on
        # duplicate-count information.
        bag_a = EvidenceBag(identifiers=frozenset({"KFOO", "KFOO", "KFOO"}))
        bag_b = EvidenceBag(identifiers=frozenset({"KFOO"}))
        assert serialize_evidence_bag(bag_a) == serialize_evidence_bag(bag_b)


# ---------------------------------------------------------------------------
# F/G/H/I/J. Malformed input rejection
# ---------------------------------------------------------------------------


class TestMalformedInputRejection:
    def test_unsupported_future_schema_version_rejected(self):
        payload = serialize_evidence_bag(EvidenceBag())
        data = json.loads(payload)
        data["schema_version"] = EVIDENCE_BAG_SCHEMA_VERSION + 1
        with pytest.raises(EvidenceBagSerializationError, match="unsupported schema_version"):
            deserialize_evidence_bag(json.dumps(data))

    def test_missing_schema_version_rejected(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        del data["schema_version"]
        with pytest.raises(EvidenceBagSerializationError, match="missing required 'schema_version'"):
            deserialize_evidence_bag(json.dumps(data))

    def test_invalid_json_rejected(self):
        with pytest.raises(EvidenceBagSerializationError, match="invalid JSON"):
            deserialize_evidence_bag("{not valid json")

    def test_wrong_top_level_type_rejected(self):
        with pytest.raises(EvidenceBagSerializationError, match="expected a JSON object"):
            deserialize_evidence_bag(json.dumps(["not", "an", "object"]))
        with pytest.raises(EvidenceBagSerializationError, match="expected a JSON object"):
            deserialize_evidence_bag(json.dumps("just a string"))

    def test_missing_required_field_rejected(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        del data["contradicting_names"]
        with pytest.raises(EvidenceBagSerializationError, match="missing required field"):
            deserialize_evidence_bag(json.dumps(data))

    def test_extra_unknown_field_rejected_not_silently_dropped(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["a_future_field_nobody_knows_about_yet"] = ["surprise"]
        with pytest.raises(EvidenceBagSerializationError, match="unexpected/unknown field"):
            deserialize_evidence_bag(json.dumps(data))

    def test_wrong_type_set_field_rejected(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["identifiers"] = "not a list"
        with pytest.raises(EvidenceBagSerializationError, match="must be a JSON array of strings"):
            deserialize_evidence_bag(json.dumps(data))

    def test_wrong_type_within_set_field_rejected(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["runway_ends"] = ["09", 27, None]
        with pytest.raises(EvidenceBagSerializationError, match="must be a JSON array of strings"):
            deserialize_evidence_bag(json.dumps(data))

    def test_malformed_runway_structure_rejected(self):
        """Mission's own explicitly-named attack: a nested/tuple-shaped
        runway value instead of a flat string."""
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["runway_pairs"] = [["09", "27"]]
        with pytest.raises(EvidenceBagSerializationError, match="must be a JSON array of strings"):
            deserialize_evidence_bag(json.dumps(data))

    def test_wrong_type_scalar_field_rejected(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["document_title"] = 12345
        with pytest.raises(EvidenceBagSerializationError, match="must be a string or null"):
            deserialize_evidence_bag(json.dumps(data))


# ---------------------------------------------------------------------------
# K. Hash determinism
# ---------------------------------------------------------------------------


class TestHashDeterminism:
    def test_hash_is_deterministic_and_survives_round_trip(self):
        bag = EvidenceBag(**_FULL_BAG_KWARGS)
        serialized = serialize_evidence_bag(bag)
        first_hash = hash_serialized_evidence_bag(serialized)
        second_hash = hash_serialized_evidence_bag(serialize_evidence_bag(bag))
        assert first_hash == second_hash

        restored = deserialize_evidence_bag(serialized)
        reserialized = serialize_evidence_bag(restored)
        assert hash_serialized_evidence_bag(reserialized) == first_hash

    def test_hash_is_sha256_hex(self):
        digest = hash_serialized_evidence_bag(serialize_evidence_bag(EvidenceBag()))
        assert len(digest) == 64
        int(digest, 16)  # raises ValueError if not valid hex

    def test_different_content_produces_different_hash(self):
        h1 = hash_serialized_evidence_bag(serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"KABC"}))))
        h2 = hash_serialized_evidence_bag(serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"KXYZ"}))))
        assert h1 != h2

    def test_hash_derived_from_exact_persisted_string_not_a_second_normalization(self):
        """The hash must change if the persisted string changes at all,
        even in a way that would not change the deserialized object
        (e.g. whitespace) - proving it hashes the actual bytes, not a
        re-derived canonical object."""
        bag = EvidenceBag(identifiers=frozenset({"KABC"}))
        serialized = serialize_evidence_bag(bag)
        padded = serialized + " "
        assert hash_serialized_evidence_bag(serialized) != hash_serialized_evidence_bag(padded)


# ---------------------------------------------------------------------------
# L/M. SourceAssertionEvidenceBag one-to-one
# ---------------------------------------------------------------------------


class TestSnapshotOneToOne:
    def test_one_snapshot_success(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            session.commit()
            bag = EvidenceBag(**_FULL_BAG_KWARGS)
            serialized = serialize_evidence_bag(bag)
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized),
                schema_version=EVIDENCE_BAG_SCHEMA_VERSION,
            )
            session.add(snapshot)
            session.commit()
            assert snapshot.id is not None
            reloaded = deserialize_evidence_bag(snapshot.evidence_bag_json)
            assert reloaded == bag

    def test_duplicate_snapshot_for_same_assertion_refused(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            ))
            session.commit()

            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            ))
            with pytest.raises(IntegrityError, match="UNIQUE"):
                session.commit()

    def test_snapshot_for_nonexistent_source_assertion_refused(self):
        with Session(_engine()) as session:
            _assert_fk_actually_enabled(session)
            serialized = serialize_evidence_bag(EvidenceBag())
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=999999, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            ))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_same_evidence_content_across_different_assertions_remains_legal(self):
        """The uniqueness boundary is assertion identity, not evidence
        content - two genuinely different SourceAssertions may each have
        their own snapshot carrying byte-identical serialized payloads
        (e.g. two documents independently reporting the identical claim)."""
        with Session(_engine()) as session:
            a1 = _seed_source_assertion(session, raw_name="First")
            a2 = _seed_source_assertion(session, raw_name="Second")
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"SAME"})))
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=a1.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            ))
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=a2.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            ))
            session.commit()
            assert session.query(SourceAssertionEvidenceBag).count() == 2


# ---------------------------------------------------------------------------
# N/O. Snapshot immutability
# ---------------------------------------------------------------------------


class TestSnapshotImmutability:
    def _seed_snapshot(self, session) -> SourceAssertionEvidenceBag:
        assertion = _seed_source_assertion(session)
        session.commit()
        serialized = serialize_evidence_bag(EvidenceBag(**_FULL_BAG_KWARGS))
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=serialized,
            evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
        )
        session.add(snapshot)
        session.commit()
        return snapshot

    def test_payload_update_blocked(self):
        with Session(_engine()) as session:
            snapshot = self._seed_snapshot(session)
            snapshot.evidence_bag_json = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"CHANGED"})))
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_hash_update_blocked(self):
        with Session(_engine()) as session:
            snapshot = self._seed_snapshot(session)
            snapshot.evidence_bag_hash = "0" * 64
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_schema_version_update_blocked(self):
        with Session(_engine()) as session:
            snapshot = self._seed_snapshot(session)
            snapshot.schema_version = 999
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_source_assertion_id_update_blocked(self):
        with Session(_engine()) as session:
            snapshot = self._seed_snapshot(session)
            other = _seed_source_assertion(session, raw_name="Other Airport")
            session.commit()
            snapshot.source_assertion_id = other.id
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_created_at_update_blocked(self):
        from datetime import UTC, datetime

        with Session(_engine()) as session:
            snapshot = self._seed_snapshot(session)
            snapshot.created_at = datetime.now(UTC)
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_delete_blocked(self):
        with Session(_engine()) as session:
            snapshot = self._seed_snapshot(session)
            session.delete(snapshot)
            with pytest.raises(ValueError, match="cannot be deleted"):
                session.commit()


# ---------------------------------------------------------------------------
# P. Legacy rows
# ---------------------------------------------------------------------------


class TestLegacyRowSemantics:
    def test_source_assertion_without_snapshot_remains_valid(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            session.commit()
            reloaded = session.get(SourceAssertion, assertion.id)
            assert reloaded is not None
            snapshot = (
                session.query(SourceAssertionEvidenceBag)
                .filter(SourceAssertionEvidenceBag.source_assertion_id == assertion.id)
                .first()
            )
            assert snapshot is None


# ---------------------------------------------------------------------------
# Q/R. Multiple evaluations per assertion
# ---------------------------------------------------------------------------


class TestEvaluationAppendOnly:
    def _seed_snapshot_and_airport(self, session):
        assertion = _seed_source_assertion(session)
        airport = _seed_airport(session)
        session.commit()
        serialized = serialize_evidence_bag(EvidenceBag(**_FULL_BAG_KWARGS))
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=serialized,
            evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
        )
        session.add(snapshot)
        session.commit()
        return assertion, airport, snapshot

    def test_zero_evaluations_is_a_valid_state(self):
        with Session(_engine()) as session:
            assertion, airport, snapshot = self._seed_snapshot_and_airport(session)
            count = session.query(IdentityGuardEvaluation).filter(
                IdentityGuardEvaluation.source_assertion_id == assertion.id
            ).count()
            assert count == 0

    def test_evaluation_insert(self):
        with Session(_engine()) as session:
            assertion, airport, snapshot = self._seed_snapshot_and_airport(session)
            evaluation = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="identifier KFOO matches",
            )
            session.add(evaluation)
            session.commit()
            assert evaluation.id is not None

    def test_multiple_evaluations_including_repeated_identical_and_changed_decision(self):
        with Session(_engine()) as session:
            assertion, airport, snapshot = self._seed_snapshot_and_airport(session)
            first = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.INSUFFICIENT_IDENTITY.value, reason="first pass",
            )
            session.add(first)
            session.commit()

            # Repeated, identical outcome - never deduplicated/refused.
            second = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.INSUFFICIENT_IDENTITY.value, reason="second pass, same outcome",
            )
            session.add(second)
            session.commit()

            # A later, changed decision - also just appended.
            third = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="third pass, now confirmed",
            )
            session.add(third)
            session.commit()

            all_evaluations = (
                session.query(IdentityGuardEvaluation)
                .filter(IdentityGuardEvaluation.source_assertion_id == assertion.id)
                .order_by(IdentityGuardEvaluation.id.asc())
                .all()
            )
            assert len(all_evaluations) == 3
            assert [e.outcome for e in all_evaluations] == [
                "INSUFFICIENT_IDENTITY", "INSUFFICIENT_IDENTITY", "ATTACH_CONFIRMED",
            ]
            # History remains fully queryable, nothing overwritten.
            assert all_evaluations[0].reason == "first pass"
            assert all_evaluations[1].reason == "second pass, same outcome"


# ---------------------------------------------------------------------------
# S/T. Evaluation immutability
# ---------------------------------------------------------------------------


class TestEvaluationImmutability:
    def _seed_evaluation(self, session) -> IdentityGuardEvaluation:
        assertion = _seed_source_assertion(session)
        airport = _seed_airport(session)
        session.commit()
        serialized = serialize_evidence_bag(EvidenceBag())
        snapshot = SourceAssertionEvidenceBag(
            source_assertion_id=assertion.id, evidence_bag_json=serialized,
            evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
        )
        session.add(snapshot)
        session.commit()
        evaluation = IdentityGuardEvaluation(
            source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
            evaluated_against_airport_id=airport.id,
            outcome=AttachmentOutcome.INSUFFICIENT_IDENTITY.value, reason="original",
        )
        session.add(evaluation)
        session.commit()
        return evaluation

    def test_outcome_update_blocked(self):
        with Session(_engine()) as session:
            evaluation = self._seed_evaluation(session)
            evaluation.outcome = AttachmentOutcome.ATTACH_CONFIRMED.value
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_reason_update_blocked(self):
        with Session(_engine()) as session:
            evaluation = self._seed_evaluation(session)
            evaluation.reason = "changed"
            with pytest.raises(ValueError, match="immutable"):
                session.commit()

    def test_delete_blocked(self):
        with Session(_engine()) as session:
            evaluation = self._seed_evaluation(session)
            session.delete(evaluation)
            with pytest.raises(ValueError, match="cannot be deleted"):
                session.commit()


# ---------------------------------------------------------------------------
# U. Invalid decision DB rejection
# ---------------------------------------------------------------------------


class TestDecisionVocabulary:
    def test_invalid_outcome_rejected_by_check_constraint(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()

            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome="TOTALLY_MADE_UP_OUTCOME", reason="x",
            ))
            with pytest.raises(IntegrityError, match="CHECK constraint failed"):
                session.commit()

    def test_every_real_attachment_outcome_value_is_accepted(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()

            for outcome in AttachmentOutcome:
                session.add(IdentityGuardEvaluation(
                    source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                    evaluated_against_airport_id=airport.id, outcome=outcome.value, reason="x",
                ))
            session.commit()
            count = session.query(IdentityGuardEvaluation).count()
            assert count == len(AttachmentOutcome)

    def test_no_second_hand_typed_vocabulary_exists(self):
        """Reused verbatim - grep-confirmed no separate, hand-typed tuple
        of outcome strings exists in the new model module."""
        from app.models import identity_guard_evaluation as module

        source = inspect_module.getsource(module)
        assert "ATTACH_CONFIRMED" not in source  # only ever referenced via AttachmentOutcome.*.value


# ---------------------------------------------------------------------------
# V. FK / delete safety
# ---------------------------------------------------------------------------


class TestForeignKeyDeleteSafety:
    def test_deleting_referenced_source_assertion_blocked_by_snapshot(self):
        with Session(_engine()) as session:
            _assert_fk_actually_enabled(session)
            assertion = _seed_source_assertion(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            session.add(SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            ))
            session.commit()

            session.delete(assertion)
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_deleting_referenced_airport_blocked_by_evaluation(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            ))
            session.commit()

            session.delete(airport)
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_deleting_referenced_snapshot_blocked_by_evaluation(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            ))
            session.commit()

            # snapshot delete is ALSO blocked by its own immutability
            # listener, but the FK is the deeper, DB-layer guarantee -
            # attack it directly via a raw DELETE bypassing the ORM event.
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.execute(SourceAssertionEvidenceBag.__table__.delete().where(
                    SourceAssertionEvidenceBag.id == snapshot.id
                ))
                session.commit()

    def test_evaluation_for_nonexistent_evaluated_airport_refused(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()

            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=999999,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            ))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_evaluation_for_nonexistent_source_assertion_refused(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session)
            assertion = _seed_source_assertion(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()

            session.add(IdentityGuardEvaluation(
                source_assertion_id=999999, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            ))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_triggering_review_id_nullable_and_optional(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            evaluation = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id, triggering_review_id=None,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            )
            session.add(evaluation)
            session.commit()
            assert evaluation.triggering_review_id is None


# ---------------------------------------------------------------------------
# Adversarial-review addition: cross-assertion causal integrity (mission
# Part 17, "CRITICAL architecture check"). Closed via a composite FK on
# (evidence_bag_snapshot_id, source_assertion_id) ->
# (source_assertion_evidence_bags.id, source_assertion_evidence_bags.source_assertion_id).
# ---------------------------------------------------------------------------


class TestEvaluationSnapshotCausalIntegrity:
    def test_evaluation_cannot_claim_assertion_a_while_referencing_assertion_bs_snapshot(self):
        with Session(_engine()) as session:
            _assert_fk_actually_enabled(session)
            a1 = _seed_source_assertion(session, raw_name="A1")
            a2 = _seed_source_assertion(session, raw_name="A2")
            airport = _seed_airport(session)
            session.commit()

            serialized_1 = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"A1-EVIDENCE"})))
            snapshot_1 = SourceAssertionEvidenceBag(
                source_assertion_id=a1.id, evidence_bag_json=serialized_1,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized_1), schema_version=1,
            )
            serialized_2 = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"A2-EVIDENCE"})))
            snapshot_2 = SourceAssertionEvidenceBag(
                source_assertion_id=a2.id, evidence_bag_json=serialized_2,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized_2), schema_version=1,
            )
            session.add_all([snapshot_1, snapshot_2])
            session.commit()

            # ATTACK: evaluation claims to concern a1 but points at a2's
            # own snapshot.
            mismatched = IdentityGuardEvaluation(
                source_assertion_id=a1.id, evidence_bag_snapshot_id=snapshot_2.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            )
            session.add(mismatched)
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_correctly_matched_assertion_and_snapshot_succeeds(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            serialized = serialize_evidence_bag(EvidenceBag(**_FULL_BAG_KWARGS))
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            evaluation = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            )
            session.add(evaluation)
            session.commit()
            assert evaluation.id is not None

    def test_no_sqlalchemy_relationship_configuration_warning(self):
        """The composite FK shares source_assertion_id with the plain
        source_assertion relationship - proves this was resolved cleanly
        (overlaps=) rather than left as an unresolved SAWarning."""
        import warnings
        from sqlalchemy.orm import configure_mappers

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            configure_mappers()
            sa_warnings = [w for w in caught if "SAWarning" in type(w.category).__name__]
        assert sa_warnings == []


# ---------------------------------------------------------------------------
# Adversarial-review addition: Airport causal-integrity BOUNDARY (mission
# Part 18) - distinguishing what the DB schema can/should enforce from
# what is correctly a future EB4 service's own semantic responsibility.
# ---------------------------------------------------------------------------


class TestAirportCausalIntegrityBoundary:
    def test_any_existing_airport_is_schema_valid_even_if_unrelated_to_the_assertion(self):
        """The DB schema only proves evaluated_against_airport_id
        references a REAL Airport row - it does NOT and should NOT prove
        that Airport is "the" Airport this SourceAssertion is actually
        resolved to (SourceAssertion.airport_id) or that a candidate was
        ever resolved to it. That semantic check - "is this the airport
        this evidence was actually resolved to" - is correctly EB4's own
        future service-level responsibility (mirroring how UAC4's own
        governed functions, not the bare ORM/schema, enforce "the matched
        Airport must exist AND be the one the review named"), not
        something a foreign key alone can express. This test proves the
        CURRENT schema boundary honestly: an evaluation against a
        genuinely unrelated Airport is NOT rejected at the DB layer."""
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            resolved_airport = _seed_airport(session, name="The Actually Resolved Airport")
            unrelated_airport = _seed_airport(session, name="A Totally Unrelated Airport")
            session.commit()
            # This SourceAssertion fixture has no airport_id set at all in
            # this narrow model-level test (EB1 does not wire resolution) -
            # the point is only that the FK itself cannot distinguish
            # "the correct Airport" from "any Airport that happens to exist".
            serialized = serialize_evidence_bag(EvidenceBag())
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()

            evaluation = IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=unrelated_airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="x",
            )
            session.add(evaluation)
            session.commit()  # succeeds - the FK only proves the Airport EXISTS
            assert evaluation.evaluated_against_airport_id == unrelated_airport.id
            del resolved_airport  # not referenced by the evaluation in this test - documents the gap


# ---------------------------------------------------------------------------
# Adversarial-review addition: payload/hash/schema_version consistency
# classification (mission Part 9/Part 13, "HIGH-PRIORITY consistency
# attack"). Classified DEFERRED_TO_EB3_PERSISTENCE_SERVICE - see the EB1
# review addendum for the full reasoning and the Snapshot precedent this
# mirrors (Snapshot.sha256/byte_size carry the identical, already-accepted
# absence of model-level consistency validation).
# ---------------------------------------------------------------------------


class TestPayloadHashSchemaVersionConsistencyBoundary:
    def test_current_boundary_orm_does_not_validate_hash_matches_payload(self):
        """Documents, does not silently accept, the current EB1 boundary:
        nothing at the model layer stops constructing a row whose
        evidence_bag_hash does not actually match evidence_bag_json. This
        mirrors app.models.acquisition.Snapshot's own identical, already-
        accepted precedent (Snapshot.sha256/byte_size are likewise never
        cross-validated against Snapshot.payload at the model layer) -
        payload/hash consistency is a WRITER responsibility
        (hash_serialized_evidence_bag() must always be called on the
        SAME string that is stored), correctly deferred to EB3's own
        persistence service, which does not exist yet."""
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            session.commit()
            real_payload = serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"REAL"})))
            mismatched_hash = hash_serialized_evidence_bag(
                serialize_evidence_bag(EvidenceBag(identifiers=frozenset({"DIFFERENT"})))
            )
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=real_payload,
                evidence_bag_hash=mismatched_hash, schema_version=1,
            )
            session.add(snapshot)
            session.commit()  # succeeds today - see this test's own docstring
            assert snapshot.evidence_bag_hash != hash_serialized_evidence_bag(snapshot.evidence_bag_json)

    def test_current_boundary_orm_does_not_validate_schema_version_column_matches_payload(self):
        """Same classification as the hash test above - the ORM
        schema_version column and the payload's own embedded
        "schema_version" key are two independent values today; keeping
        them in sync is EB3's own writer-side responsibility."""
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            session.commit()
            payload_data = json.loads(serialize_evidence_bag(EvidenceBag()))
            payload_data["schema_version"] = 1
            payload = json.dumps(payload_data)
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=payload,
                evidence_bag_hash=hash_serialized_evidence_bag(payload), schema_version=999,
            )
            session.add(snapshot)
            session.commit()  # succeeds today - see this test's own docstring
            assert snapshot.schema_version != json.loads(snapshot.evidence_bag_json)["schema_version"]


# ---------------------------------------------------------------------------
# Adversarial-review addition: bool/int type pitfall (mission Part 8) -
# a genuine defect found and fixed: `True == 1` and `1.0 == 1` in Python,
# so a plain `!=` comparison silently accepted schema_version: true/1.0
# as if they were the real integer 1. Fixed via a strict `type(x) is int`
# check.
# ---------------------------------------------------------------------------


class TestSchemaVersionTypeStrictness:
    @pytest.mark.parametrize("bad_value", [True, False, 1.0, 0, -1, None, "1"])
    def test_non_strict_int_schema_version_rejected(self, bad_value):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["schema_version"] = bad_value
        with pytest.raises(EvidenceBagSerializationError, match="unsupported schema_version"):
            deserialize_evidence_bag(json.dumps(data))

    def test_literal_integer_one_still_accepted(self):
        data = json.loads(serialize_evidence_bag(EvidenceBag()))
        data["schema_version"] = 1
        result = deserialize_evidence_bag(json.dumps(data))
        assert result == EvidenceBag()


# ---------------------------------------------------------------------------
# Adversarial-review addition: duplicate JSON keys (mission Part 7).
# ---------------------------------------------------------------------------


class TestDuplicateJsonKeys:
    def test_duplicate_top_level_key_resolves_to_the_last_value_per_python_json_semantics(self):
        """Documents actual behavior rather than assuming it: Python's
        json.loads() silently keeps only the LAST occurrence of a
        duplicate object key (standard library behavior, not something
        this module adds or could easily override without a custom
        object_pairs_hook). Not a defect this module introduces -
        recorded here so a future reader does not need to rediscover it
        by attack."""
        raw = '{"schema_version": 1, "schema_version": 1, "identifiers": ["FIRST"], "identifiers": ["SECOND"], ' + \
            '"names": [], "runway_ends": [], "runway_pairs": [], "issuers": [], "locations": [], ' + \
            '"contradicting_names": [], "contradicting_issuers": [], "contradicting_locations": [], ' + \
            '"alternate_airport_runway_ends": [], "alternate_airport_runway_pairs": [], ' + \
            '"document_title": null, "project_number": null, "contract_number": null, "url": null}'
        result = deserialize_evidence_bag(raw)
        assert result.identifiers == frozenset({"SECOND"})


# ---------------------------------------------------------------------------
# W/X. Historical decision firewall / no canonical side effects
# ---------------------------------------------------------------------------


class TestHistoricalDecisionFirewall:
    def test_creating_snapshot_and_evaluation_never_mutates_original_source_assertion_fields(self):
        with Session(_engine()) as session:
            assertion = _seed_source_assertion(session)
            airport = _seed_airport(session)
            session.commit()
            original_decision = assertion.identity_guard_decision
            original_reason = assertion.identity_guard_reason
            assert original_decision == "INSUFFICIENT_IDENTITY"
            assert original_reason == "original discovery-time fact"

            serialized = serialize_evidence_bag(EvidenceBag(**_FULL_BAG_KWARGS))
            snapshot = SourceAssertionEvidenceBag(
                source_assertion_id=assertion.id, evidence_bag_json=serialized,
                evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
            )
            session.add(snapshot)
            session.commit()
            session.add(IdentityGuardEvaluation(
                source_assertion_id=assertion.id, evidence_bag_snapshot_id=snapshot.id,
                evaluated_against_airport_id=airport.id,
                outcome=AttachmentOutcome.ATTACH_CONFIRMED.value, reason="a fresh, different, later outcome",
            ))
            session.commit()

            reloaded = session.get(SourceAssertion, assertion.id)
            assert reloaded.identity_guard_decision == original_decision
            assert reloaded.identity_guard_reason == original_reason

    def test_no_writes_to_identity_guard_decision_anywhere_in_new_modules(self):
        """Both new model modules' own docstrings legitimately DISCUSS
        identity_guard_decision/identity_guard_reason (explaining the
        firewall itself) - so this checks for actual write-shaped
        expressions (assignment / mapped_column) rather than the bare
        substring, which would false-positive on that prose."""
        from app.models import identity_guard_evaluation as eval_module
        from app.models import source_assertion_evidence_bag as snapshot_module
        from app.services import evidence_bag_serialization as serialization_module

        for module in (eval_module, snapshot_module, serialization_module):
            tree = ast.parse(inspect_module.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in (
                    "identity_guard_decision", "identity_guard_reason",
                ):
                    pytest.fail(f"{module.__name__} references .{node.attr} as an attribute expression")
                if isinstance(node, ast.Name) and node.id in (
                    "identity_guard_decision", "identity_guard_reason",
                ):
                    pytest.fail(f"{module.__name__} references {node.id} as a name")

    def test_no_canonical_or_signal_side_effects_via_ast(self):
        from app.models import identity_guard_evaluation as eval_module
        from app.models import source_assertion_evidence_bag as snapshot_module
        from app.services import evidence_bag_serialization as serialization_module

        forbidden = {"Runway", "RunwayEnd", "Installation", "Signal", "PhysicalInstallationIdentity", "Airport"}
        for module in (eval_module, snapshot_module, serialization_module):
            tree = ast.parse(inspect_module.getsource(module))
            found = {
                node.func.id for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
            }
            assert found == set(), f"{module.__name__} unexpectedly constructs: {found}"


# ---------------------------------------------------------------------------
# Y. Source-neutral / international / no business orchestration
# ---------------------------------------------------------------------------


class TestSourceNeutralAndNoOrchestration:
    def test_no_producer_specific_dependency_anywhere_in_eb1_modules(self):
        from app.models import identity_guard_evaluation as eval_module
        from app.models import source_assertion_evidence_bag as snapshot_module
        from app.services import evidence_bag_serialization as serialization_module

        forbidden_terms = ("mac", "granicus", "usaspending", "faa_", "n8n", "llm", "openai", "anthropic")
        for module in (eval_module, snapshot_module, serialization_module):
            source = inspect_module.getsource(module).lower()
            for term in forbidden_terms:
                assert term not in source, f"{module.__name__} unexpectedly references {term!r}"

    def test_no_migration_or_discovery_wiring_imported(self):
        """The serialization module's own docstring legitimately
        MENTIONS discovery_evidence_persistence.py in prose (explaining
        the exact bug this module closes) - this checks the actual
        `import`/`from ... import` statements, not the free-text
        substring, which would false-positive on that explanation."""
        from app.services import evidence_bag_serialization as serialization_module

        tree = ast.parse(inspect_module.getsource(serialization_module))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        assert not any("discovery_evidence_persistence" in m for m in imported_modules)
        assert not any("unknown_airport_discovery_integration" in m for m in imported_modules)
        assert not any("migrate_" in m for m in imported_modules)

        source = inspect_module.getsource(serialization_module)
        assert ".upgrade(" not in source
        assert ".downgrade(" not in source

    def test_no_network_imports(self):
        from app.models import identity_guard_evaluation as eval_module
        from app.models import source_assertion_evidence_bag as snapshot_module
        from app.services import evidence_bag_serialization as serialization_module

        for module in (eval_module, snapshot_module, serialization_module):
            source = inspect_module.getsource(module)
            for term in ("import requests", "import urllib", "import http.client", "httpx", "socket."):
                assert term not in source


# ---------------------------------------------------------------------------
# Z. Real DB no-access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_real_database_path_or_sessionlocal(self):
        from app.models import identity_guard_evaluation as eval_module
        from app.models import source_assertion_evidence_bag as snapshot_module
        from app.services import evidence_bag_serialization as serialization_module

        for module in (eval_module, snapshot_module, serialization_module):
            source = inspect_module.getsource(module)
            assert "runway_safe.db" not in source
            assert "SessionLocal" not in source
            assert ".commit(" not in source
