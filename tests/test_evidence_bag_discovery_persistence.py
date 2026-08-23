"""Tests for EB3 - lossless EvidenceBag snapshot persistence wired into
modern discovery SourceAssertion creation
(docs/architecture/rwi-eb3-evidencebag-discovery-persistence-report.md,
Slice 3 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

Isolated, in-memory (or tmp_path, for the real-migration-chain tests)
SQLite databases only - never the real one. Fixtures are entirely
fictional.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport
from app.services.evidence_bag_serialization import (
    EVIDENCE_BAG_SCHEMA_VERSION,
    deserialize_evidence_bag,
    hash_serialized_evidence_bag,
    serialize_evidence_bag,
)
import app.services.discovery_evidence_persistence as dep_module
from app.services.discovery_evidence_persistence import (
    ConflictingEvidenceBagReplayError,
    DiscoverySourceMetadata,
    EvidenceBagSchemaRequiredError,
    persist_candidate_linked_source_assertion,
    persist_discovery_fragment,
)
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_discovery_integration import (
    DiscoveryIdentityOutcome,
    resolve_or_persist_discovery_identity,
)
import scripts.migrate_evidence_bag_persistence_eb2 as eb2_migration
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _engine_without_eb_schema():
    """A schema with everything EXCEPT the EB1/EB2 tables - simulates a
    database that has never run the EB2 migration."""
    engine = create_engine("sqlite:///:memory:")
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name not in ("source_assertion_evidence_bags", "identity_guard_evaluations")
    ]
    Base.metadata.create_all(engine, tables=tables)
    return engine


def _seed_airport(session, *, faa_code=None, name, city=None, country="USA") -> Airport:
    airport = Airport(name=name, faa_code=faa_code, country=country, city=city)
    session.add(airport)
    session.flush()
    return airport


def _candidate(airport: Airport, **kwargs) -> CandidateAirport:
    return CandidateAirport(id=airport.id, name=airport.name, **kwargs)


def _artifact(name: str) -> str:
    return f"eb3-test-artifact:{name}"


def _meta(document_identity: str, title: str = "Test document") -> DiscoverySourceMetadata:
    return DiscoverySourceMetadata(document_identity=document_identity, title=title)


def _snapshot_for(session, source_assertion_id: int) -> SourceAssertionEvidenceBag | None:
    return session.query(SourceAssertionEvidenceBag).filter_by(source_assertion_id=source_assertion_id).one_or_none()


# ---------------------------------------------------------------------------
# A. Known-canonical path + snapshot
# ---------------------------------------------------------------------------


class TestKnownCanonicalSnapshot:
    def test_known_airport_attachment_creates_exactly_one_matching_snapshot(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("known"), source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            evidence = candidate_fragment_to_evidence_bag(fragment)
            result = persist_discovery_fragment(session, _meta("known-doc"), fragment, [candidate_airport])

            assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
            assert result.attached_airport_id == airport.id
            assert result.attached_unknown_airport_candidate_id is None
            assert result.attached_evidence_bag_snapshot_id is not None

            snapshot = session.get(SourceAssertionEvidenceBag, result.attached_evidence_bag_snapshot_id)
            assert snapshot.source_assertion_id == result.source_assertion_id
            assert snapshot.evidence_bag_json == serialize_evidence_bag(evidence)
            assert session.query(UnknownAirportCandidate).count() == 0


# ---------------------------------------------------------------------------
# B. Unknown-candidate path + snapshot
# ---------------------------------------------------------------------------


class TestUnknownCandidateSnapshot:
    def test_strong_unknown_identity_creates_exactly_one_matching_snapshot(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("unknown"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            evidence = candidate_fragment_to_evidence_bag(fragment)
            result = resolve_or_persist_discovery_identity(session, _meta("unknown-doc"), fragment, [])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.attached_airport_id is None
            assert result.evidence_bag_snapshot_id is not None

            snapshot = session.get(SourceAssertionEvidenceBag, result.evidence_bag_snapshot_id)
            assert snapshot.source_assertion_id == result.source_assertion_id
            assert snapshot.evidence_bag_json == serialize_evidence_bag(evidence)
            assert session.query(Airport).count() == 0


# ---------------------------------------------------------------------------
# C. Unresolved / ambiguous path snapshotting
# ---------------------------------------------------------------------------


class TestUnresolvedAndAmbiguousSnapshot:
    def test_unresolved_identity_still_gets_a_snapshot(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("unresolved"), source_locator="p1", raw_text="Vague EMAS mention only.",
            )
            result = resolve_or_persist_discovery_identity(session, _meta("unresolved-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert result.evidence_bag_snapshot_id is not None
            snapshot = session.get(SourceAssertionEvidenceBag, result.evidence_bag_snapshot_id)
            assert snapshot.source_assertion_id == result.source_assertion_id

    def test_reject_cross_airport_still_gets_a_snapshot(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("reject"), source_locator="p1", raw_text="ORH runway work, not BOS.",
                airport_identifiers=frozenset({"ORH"}),
            )
            result = persist_discovery_fragment(session, _meta("reject-doc"), fragment, [candidate_airport])
            assert result.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert result.attached_evidence_bag_snapshot_id is not None

    def test_ambiguous_known_identity_still_gets_a_snapshot(self):
        with Session(_engine()) as session:
            bos = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            orh = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
            bos_c = _candidate(bos, known_issuers=frozenset({"Massport"}))
            orh_c = _candidate(orh, known_issuers=frozenset({"Massport"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("ambiguous"), source_locator="p1",
                raw_text="Massport bill covering Logan and Worcester Regional.", issuers=frozenset({"Massport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("ambiguous-doc"), fragment, [bos_c, orh_c])
            assert result.outcome == DiscoveryIdentityOutcome.AMBIGUOUS_KNOWN_IDENTITY
            assert result.evidence_bag_snapshot_id is not None


# ---------------------------------------------------------------------------
# D/E/F. Payload / hash / schema-version consistency
# ---------------------------------------------------------------------------


class TestWriteTimeConsistency:
    def test_stored_payload_hash_and_schema_version_are_internally_consistent(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("consistency"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}), issuers=frozenset({"FAA"}),
            )
            evidence = candidate_fragment_to_evidence_bag(fragment)
            result = resolve_or_persist_discovery_identity(session, _meta("consistency-doc"), fragment, [])
            snapshot = session.get(SourceAssertionEvidenceBag, result.evidence_bag_snapshot_id)

            assert snapshot.evidence_bag_json == serialize_evidence_bag(evidence)
            assert snapshot.evidence_bag_hash == hash_serialized_evidence_bag(snapshot.evidence_bag_json)
            assert snapshot.schema_version == EVIDENCE_BAG_SCHEMA_VERSION
            round_tripped = deserialize_evidence_bag(snapshot.evidence_bag_json)
            assert round_tripped == evidence

    def test_caller_cannot_supply_payload_hash_or_schema_version_independently(self):
        """API-shape proof (§4): the only public entry points are
        persist_discovery_fragment()/persist_candidate_linked_source_assertion(),
        neither of which accepts a payload/hash/schema_version parameter -
        the internal helper signature itself only accepts an EvidenceBag."""
        sig = inspect_module.signature(dep_module._attach_evidence_bag_snapshot)
        param_names = set(sig.parameters.keys())
        assert "evidence_bag" in param_names
        assert not ({"serialized_payload", "payload", "evidence_bag_hash", "schema_version"} & param_names)


# ---------------------------------------------------------------------------
# G. Exactly one snapshot per assertion
# ---------------------------------------------------------------------------


class TestExactlyOneSnapshot:
    def test_db_uniqueness_rejects_a_second_snapshot_for_the_same_assertion(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("unique"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            evidence = candidate_fragment_to_evidence_bag(fragment)
            result = resolve_or_persist_discovery_identity(session, _meta("unique-doc"), fragment, [])
            session.commit()

            with pytest.raises(IntegrityError):
                dep_module._attach_evidence_bag_snapshot(
                    session, source_assertion_id=result.source_assertion_id, evidence_bag=evidence,
                )
            session.rollback()
            assert session.query(SourceAssertionEvidenceBag).filter_by(
                source_assertion_id=result.source_assertion_id
            ).count() == 1

    def test_replay_never_attempts_a_second_snapshot(self):
        with Session(_engine()) as session:
            fragment_factory = lambda: CandidateFragment(
                artifact_identity=_artifact("replay-count"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            r1 = resolve_or_persist_discovery_identity(session, _meta("replay-count-doc"), fragment_factory(), [])
            r2 = resolve_or_persist_discovery_identity(session, _meta("replay-count-doc"), fragment_factory(), [])
            assert r1.source_assertion_id == r2.source_assertion_id
            assert r1.evidence_bag_snapshot_id == r2.evidence_bag_snapshot_id
            assert session.query(SourceAssertionEvidenceBag).count() == 1


# ---------------------------------------------------------------------------
# H/I. Replay identical vs. conflicting evidence
# ---------------------------------------------------------------------------


class TestReplaySemantics:
    def test_identical_replay_reuses_snapshot_silently(self):
        with Session(_engine()) as session:
            fragment_factory = lambda: CandidateFragment(
                artifact_identity=_artifact("identical-replay"), source_locator="p1",
                raw_text="Foo Regional Airport memo.", airport_names=frozenset({"Foo Regional Airport"}),
            )
            r1 = resolve_or_persist_discovery_identity(session, _meta("identical-replay-doc"), fragment_factory(), [])
            r2 = resolve_or_persist_discovery_identity(session, _meta("identical-replay-doc"), fragment_factory(), [])
            assert r1.evidence_bag_snapshot_id == r2.evidence_bag_snapshot_id

    def test_conflicting_replay_on_known_path_fails_loud_end_to_end(self):
        """Real, reachable attack through the PUBLIC API (never a direct
        call to the private reconciliation helper): raw_fragment_hash is
        SHA-256 of raw_text ONLY (CandidateFragment.fragment_hash) - it is
        NOT a hash of the fully-structured extracted fields, so two
        genuinely different CandidateFragments (e.g. a later extraction
        run that changed which identifiers it found) can share the exact
        same fragment identity while producing different EvidenceBags.
        §14/§16 both demand this be attacked end-to-end, not merely at the
        private-helper level."""
        same_raw_text = "Foo Regional Airport EMAS memo, generic phrasing."
        with Session(_engine()) as session:
            meta = _meta("e2e-conflict-doc")
            frag1 = CandidateFragment(
                artifact_identity=_artifact("e2e-conflict"), source_locator="p1", raw_text=same_raw_text,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"XYZ"}),
            )
            r1 = persist_discovery_fragment(session, meta, frag1, [])
            session.commit()
            original_snapshot = session.get(SourceAssertionEvidenceBag, r1.attached_evidence_bag_snapshot_id)
            original_payload = original_snapshot.evidence_bag_json

            frag2 = CandidateFragment(
                artifact_identity=_artifact("e2e-conflict"), source_locator="p1", raw_text=same_raw_text,
                airport_names=frozenset({"Foo Regional Airport"}), airport_identifiers=frozenset({"DIFFERENT"}),
            )
            assert frag1.fragment_hash == frag2.fragment_hash  # same fragment identity, by construction

            with pytest.raises(ConflictingEvidenceBagReplayError):
                persist_discovery_fragment(session, meta, frag2, [])

            # no dirty/pending state was left behind by the failed call
            assert not session.new
            assert not session.dirty
            session.rollback()

            # the original SourceAssertion and its snapshot are byte-for-byte
            # untouched; no second snapshot, no second assertion.
            assert session.query(SourceAssertion).count() == 1
            assert session.query(SourceAssertionEvidenceBag).count() == 1
            reloaded = session.get(SourceAssertionEvidenceBag, r1.attached_evidence_bag_snapshot_id)
            assert reloaded.evidence_bag_json == original_payload


# ---------------------------------------------------------------------------
# J/K. Transaction atomicity - new assertion
# ---------------------------------------------------------------------------


class TestNewAssertionAtomicity:
    def test_snapshot_failure_after_assertion_flush_rolls_back_both(self, monkeypatch):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("snap-fail"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )

            def _boom(*args, **kwargs):
                raise RuntimeError("simulated snapshot persistence failure")

            monkeypatch.setattr(dep_module, "_attach_evidence_bag_snapshot", _boom)
            with pytest.raises(RuntimeError, match="simulated snapshot persistence failure"):
                persist_candidate_linked_source_assertion(
                    session, _meta("snap-fail-doc"), fragment,
                    unknown_airport_candidate_id=find_or_create_unknown_airport_candidate(
                        session, raw_name="Foo Regional Airport", raw_runway_designation=None,
                        evidence_source_locator="p1", evidence_artifact_identity=_artifact("snap-fail"),
                    ).candidate.id,
                )
            session.rollback()
            assert session.query(SourceAssertion).count() == 0
            assert session.query(SourceAssertionEvidenceBag).count() == 0

    def test_assertion_failure_never_leaves_an_orphan_snapshot(self, monkeypatch):
        """Failure BEFORE the assertion flush (e.g. a Source-creation bug)
        must leave no snapshot either, since none can exist without a
        source_assertion_id to reference."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("assert-fail"), source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))

            original_flush = session.flush
            call_count = {"n": 0}

            def _flush_boom(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("simulated assertion flush failure")
                return original_flush(*args, **kwargs)

            monkeypatch.setattr(session, "flush", _flush_boom)
            with pytest.raises(RuntimeError, match="simulated assertion flush failure"):
                persist_discovery_fragment(session, _meta("assert-fail-doc"), fragment, [candidate_airport])
            monkeypatch.undo()
            session.rollback()
            assert session.query(SourceAssertion).count() == 0
            assert session.query(SourceAssertionEvidenceBag).count() == 0


# ---------------------------------------------------------------------------
# L/M. Transaction atomicity - unknown-candidate path (new vs. existing candidate)
# ---------------------------------------------------------------------------


class TestUnknownCandidateAtomicity:
    def test_new_candidate_removed_on_rollback_when_snapshot_fails(self, monkeypatch):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("new-cand-fail"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )

            def _boom(*args, **kwargs):
                raise RuntimeError("simulated snapshot failure")

            monkeypatch.setattr(dep_module, "_attach_evidence_bag_snapshot", _boom)
            with pytest.raises(RuntimeError, match="simulated snapshot failure"):
                resolve_or_persist_discovery_identity(session, _meta("new-cand-fail-doc"), fragment, [])
            session.rollback()
            assert session.query(UnknownAirportCandidate).count() == 0
            assert session.query(SourceAssertion).count() == 0

    def test_preexisting_candidate_survives_rollback_of_a_failed_second_assertion(self, monkeypatch):
        with Session(_engine()) as session:
            frag1 = CandidateFragment(
                artifact_identity=_artifact("existing-cand-a"), source_locator="p1", raw_text="Foo Regional Airport memo A.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            first = resolve_or_persist_discovery_identity(session, _meta("existing-cand-doc-a"), frag1, [])
            session.commit()
            candidate_id = first.unknown_airport_candidate_id

            frag2 = CandidateFragment(
                artifact_identity=_artifact("existing-cand-b"), source_locator="p1", raw_text="Foo Regional Airport memo B.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )

            def _boom(*args, **kwargs):
                raise RuntimeError("simulated second-linkage snapshot failure")

            monkeypatch.setattr(dep_module, "_attach_evidence_bag_snapshot", _boom)
            with pytest.raises(RuntimeError, match="simulated second-linkage snapshot failure"):
                resolve_or_persist_discovery_identity(session, _meta("existing-cand-doc-b"), frag2, [])
            session.rollback()

            candidate = session.get(UnknownAirportCandidate, candidate_id)
            assert candidate is not None
            assert candidate.raw_name == "Foo Regional Airport"
            assert session.query(SourceAssertion).count() == 1


# ---------------------------------------------------------------------------
# N. Legacy missing-snapshot behavior - never backfilled
# ---------------------------------------------------------------------------


class TestLegacyMissingSnapshotNeverBackfilled:
    def test_pre_eb3_assertion_replay_does_not_gain_a_snapshot(self):
        with Session(_engine()) as session:
            source = Source(title="t", source_type="web_discovery", external_id="discovery:legacy-doc")
            session.add(source)
            session.flush()
            fragment = CandidateFragment(
                artifact_identity=_artifact("legacy"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            legacy_assertion = SourceAssertion(
                source_id=source.id, airport_id=None, assertion_type="project_construction",
                raw_relevant_text=fragment.raw_text, source_locator=fragment.source_locator,
                raw_fragment_hash=fragment.fragment_hash, artifact_identity=fragment.artifact_identity,
                evidence_quality="unverified_candidate", review_state="unreviewed",
                identity_guard_decision=AttachmentOutcome.INSUFFICIENT_IDENTITY.value,
                identity_guard_reason="pre-EB3 legacy row",
            )
            session.add(legacy_assertion)
            session.flush()
            assert _snapshot_for(session, legacy_assertion.id) is None

            result = persist_discovery_fragment(session, _meta("legacy-doc"), fragment, [])
            assert result.source_assertion_id == legacy_assertion.id
            assert result.source_assertion_created is False
            assert result.attached_evidence_bag_snapshot_id is None
            assert _snapshot_for(session, legacy_assertion.id) is None


# ---------------------------------------------------------------------------
# O. Missing EB schema - fail loud, no auto-migration, no silent fallback
# ---------------------------------------------------------------------------


class TestMissingSchemaFailsLoud:
    def test_known_path_fails_loud_without_eb_schema(self):
        with Session(_engine_without_eb_schema()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("no-schema-known"), source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            with pytest.raises(EvidenceBagSchemaRequiredError):
                persist_discovery_fragment(session, _meta("no-schema-known-doc"), fragment, [])
            assert session.query(SourceAssertion).count() == 0

    def test_unknown_path_fails_loud_without_eb_schema(self):
        engine = _engine_without_eb_schema()
        with Session(engine) as session:
            candidate_result = find_or_create_unknown_airport_candidate(
                session, raw_name="Foo Regional Airport", raw_runway_designation=None,
                evidence_source_locator="p1", evidence_artifact_identity=_artifact("no-schema-unknown"),
            )
            session.commit()
            fragment = CandidateFragment(
                artifact_identity=_artifact("no-schema-unknown"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            with pytest.raises(EvidenceBagSchemaRequiredError):
                persist_candidate_linked_source_assertion(
                    session, _meta("no-schema-unknown-doc"), fragment,
                    unknown_airport_candidate_id=candidate_result.candidate.id,
                )
            assert session.query(SourceAssertion).count() == 0

    def test_malformed_present_snapshot_table_fails_loud_at_insert_not_silently(self):
        """§9: a snapshot table that EXISTS but is missing a column the ORM
        model expects (here, evidence_bag_hash) is deliberately NOT caught
        by _verify_evidence_bag_schema_ready() (an existence-only check,
        never a duplicate of EB2's own deep structural comparison - see
        that function's own docstring). This must fail loud with a real,
        informative SQLAlchemy error at INSERT time - never silently
        succeed with corrupted/partial data and never silently produce a
        SourceAssertion with no usable snapshot."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE source_assertion_evidence_bags"))
            conn.execute(text(
                "CREATE TABLE source_assertion_evidence_bags ("
                "id INTEGER PRIMARY KEY, source_assertion_id INTEGER, "
                "evidence_bag_json TEXT, schema_version INTEGER, created_at DATETIME)"
            ))
            conn.commit()
        with Session(engine) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("malformed-schema"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            from sqlalchemy.exc import OperationalError

            with pytest.raises(OperationalError, match="evidence_bag_hash"):
                resolve_or_persist_discovery_identity(session, _meta("malformed-schema-doc"), fragment, [])
            session.rollback()
            # the malformed table itself is left completely empty - no
            # partial/corrupted row was ever committed.
            assert session.execute(text("SELECT COUNT(*) FROM source_assertion_evidence_bags")).scalar() == 0

    def test_schema_readiness_check_never_opens_a_second_connection(self, tmp_path):
        """§7 permanent regression coverage for the exact defect class
        found and fixed during EB3's own implementation: the FIRST version
        of _verify_evidence_bag_schema_ready() used
        sqlalchemy.inspect(session.get_bind()), which opened a second,
        independent Connection against the bound Engine and, for an
        in-memory SQLite database (SingletonThreadPool), silently
        corrupted the Session's own open transaction (observed as
        duplicate-primary-key reuse across unrelated rows). Proven here by
        directly asserting the underlying DBAPI connection identity is
        unchanged before/after the check, for BOTH an in-memory engine and
        a file-backed one - not merely inferred from the outer test suite
        passing."""
        for engine in (
            create_engine("sqlite:///:memory:"),
            create_engine(f"sqlite:///{tmp_path / 'conn_identity.db'}"),
        ):
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                dbapi_connection_before = session.connection().connection
                dep_module._verify_evidence_bag_schema_ready(session)
                dbapi_connection_after = session.connection().connection
                assert dbapi_connection_before is dbapi_connection_after
            engine.dispose()

    def test_known_path_does_not_require_uac_schema(self):
        """§21: the known-airport path never touches UnknownAirportCandidate
        - it must not spuriously require UAC schema presence, only EB
        schema. Build a DB with EB tables but WITHOUT the UAC tables and
        confirm the known path still works."""
        engine = create_engine("sqlite:///:memory:")
        tables = [t for t in Base.metadata.sorted_tables if t.name not in ("unknown_airport_candidates", "unknown_airport_candidate_reviews")]
        Base.metadata.create_all(engine, tables=tables)
        with Session(engine) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("no-uac-known"), source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            result = persist_discovery_fragment(session, _meta("no-uac-known-doc"), fragment, [candidate_airport])
            assert result.attached_evidence_bag_snapshot_id is not None


# ---------------------------------------------------------------------------
# Q/R. Migration-chain parity - real migrations, not create_all()
# ---------------------------------------------------------------------------


class TestMigrationChainParity:
    def _migrated_db(self, tmp_path, name: str):
        db = tmp_path / name
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_reviews")
        conn.execute("DROP TABLE unknown_airport_candidates")
        conn.execute("DROP TABLE source_assertion_evidence_bags")
        conn.execute("DROP TABLE identity_guard_evaluations")
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
        eb2_migration.upgrade(db)
        return db

    def test_known_path_against_genuinely_migrated_schema(self, tmp_path):
        db = self._migrated_db(tmp_path, "eb3_parity_known.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("migrated-known"), source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            result = persist_discovery_fragment(session, _meta("migrated-known-doc"), fragment, [candidate_airport])
            session.commit()
            assert result.attached_evidence_bag_snapshot_id is not None
            snapshot = session.get(SourceAssertionEvidenceBag, result.attached_evidence_bag_snapshot_id)
            assert snapshot is not None
        engine.dispose()

    def test_unknown_path_against_genuinely_migrated_schema(self, tmp_path):
        db = self._migrated_db(tmp_path, "eb3_parity_unknown.db")
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("migrated-unknown"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("migrated-unknown-doc"), fragment, [])
            session.commit()
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.evidence_bag_snapshot_id is not None
            assert session.query(UnknownAirportCandidate).count() == 1
        engine.dispose()


# ---------------------------------------------------------------------------
# S. Unicode / comma / newline lossless round trip through a committed,
# reopened database
# ---------------------------------------------------------------------------


class TestUnicodeLosslessRoundTrip:
    def test_full_unicode_and_delimiter_content_round_trips_through_reopened_db(self, tmp_path):
        db = tmp_path / "eb3_unicode.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        tricky_name = 'Åäö, "Aéroport" \n\t\\ 羽田空港 مطار São Paulo emoji ✈️ NFC-é NFD-é'
        with Session(engine) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("unicode-lossless"), source_locator="p1",
                raw_text=f"{tricky_name} EMAS memo.", airport_names=frozenset({tricky_name}),
                issuers=frozenset({"Comma, Quoted \"Authority\"\nNewline"}),
            )
            evidence = candidate_fragment_to_evidence_bag(fragment)
            result = resolve_or_persist_discovery_identity(session, _meta("unicode-lossless-doc"), fragment, [])
            session.commit()
            snapshot_id = result.evidence_bag_snapshot_id
        engine.dispose()

        reopened = create_engine(f"sqlite:///{db}")
        with Session(reopened) as session:
            snapshot = session.get(SourceAssertionEvidenceBag, snapshot_id)
            round_tripped = deserialize_evidence_bag(snapshot.evidence_bag_json)
            assert round_tripped == evidence
            assert round_tripped.names == frozenset({tricky_name})
            assert round_tripped.issuers == frozenset({"Comma, Quoted \"Authority\"\nNewline"})
        reopened.dispose()


# ---------------------------------------------------------------------------
# T. Multiple assertions, identical EvidenceBag content - allowed, hash not
# globally unique
# ---------------------------------------------------------------------------


class TestMultipleAssertionsSameContent:
    def test_two_distinct_assertions_may_share_identical_evidence_bag_content(self):
        with Session(_engine()) as session:
            frag_a = CandidateFragment(
                artifact_identity=_artifact("dup-content-a"), source_locator="p1", raw_text="Foo Regional Airport memo A.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            frag_b = CandidateFragment(
                artifact_identity=_artifact("dup-content-b"), source_locator="p1", raw_text="Foo Regional Airport memo B.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            r_a = resolve_or_persist_discovery_identity(session, _meta("dup-content-doc-a"), frag_a, [])
            r_b = resolve_or_persist_discovery_identity(session, _meta("dup-content-doc-b"), frag_b, [])

            snap_a = session.get(SourceAssertionEvidenceBag, r_a.evidence_bag_snapshot_id)
            snap_b = session.get(SourceAssertionEvidenceBag, r_b.evidence_bag_snapshot_id)
            assert snap_a.id != snap_b.id
            assert snap_a.evidence_bag_hash == snap_b.evidence_bag_hash
            assert session.query(SourceAssertionEvidenceBag).count() == 2


# ---------------------------------------------------------------------------
# U/V. Historical guard firewall - fields unchanged, zero evaluation rows
# ---------------------------------------------------------------------------


class TestHistoricalGuardFirewall:
    def test_identity_guard_fields_unchanged_by_snapshot_creation(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("guard-fields"), source_locator="p1", raw_text="BOS EMAS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            result = persist_discovery_fragment(session, _meta("guard-fields-doc"), fragment, [candidate_airport])
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.identity_guard_decision == AttachmentOutcome.ATTACH_CONFIRMED.value
            assert assertion.identity_guard_reason == result.reason

    def test_no_identity_guard_evaluation_rows_ever_created(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("zero-eval"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("zero-eval-doc"), fragment, [])
            assert session.query(IdentityGuardEvaluation).count() == 0


# ---------------------------------------------------------------------------
# W. No canonical / Signal side effects
# ---------------------------------------------------------------------------


class TestNoCanonicalOrSignalSideEffects:
    def test_unknown_path_creates_no_airport_or_signal(self):
        from app.models import Signal

        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("no-side-effects"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("no-side-effects-doc"), fragment, [])
            assert session.query(Airport).count() == 0
            assert session.query(Signal).count() == 0


# ---------------------------------------------------------------------------
# X. Source-neutrality - no vendor/geography/language-specific dependency
# ---------------------------------------------------------------------------


class TestSourceNeutrality:
    def test_no_vendor_or_geography_specific_imports(self):
        source = inspect_module.getsource(dep_module)
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        forbidden_terms = ("granicus", "usaspending", "faa_nasr", "n8n", "openai", "requests", "urllib", "httpx")
        for module_name in imported_modules:
            lowered = module_name.lower()
            assert not any(term in lowered for term in forbidden_terms), module_name

    def test_no_network_imports(self):
        source = inspect_module.getsource(dep_module)
        for term in ("import requests", "import urllib", "import http.client", "httpx", "socket."):
            assert term not in source


# ---------------------------------------------------------------------------
# Y. Real database never touched
# ---------------------------------------------------------------------------


class TestRealDatabaseNeverTouched:
    def test_module_never_imports_session_local_or_real_db_path(self):
        source = inspect_module.getsource(dep_module)
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
        assert "SessionLocal" not in imported_names
        assert "runway_safe.db" not in source
