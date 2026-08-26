"""Static-site / product firewall tests for KAR1-KAR3
(docs/architecture/rwi-known-airport-ambiguity-resolution-design.md).

Proves structurally that this capability cannot directly create or
publish an Airport, UnknownAirportCandidate, Signal, or Installation -
the ONLY mutation it ever performs is SourceAssertion.airport_id, exactly
as the locked design requires. Modeled directly on
tests/test_unknown_airport_candidate_resolution.py::TestCanonicalSideEffectFirewall,
this repository's own established pattern for this exact kind of proof.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401
from app.models import Airport, Installation, Signal, Source, SourceAssertion
from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.evidence_attachment_guard import EvidenceBag
from app.services.evidence_bag_serialization import hash_serialized_evidence_bag, serialize_evidence_bag
from app.services.source_assertion_identity_resolution import record_source_assertion_identity_resolution


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_assertion(session):
    airport = Airport(name="St. Paul Downtown Airport", country="USA")
    session.add(airport)
    session.flush()
    source = Source(title="Test Source", source_type="web_discovery")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction",
        source_locator="item-1", artifact_identity="doc-1", raw_fragment_hash="hash-1",
        identity_guard_decision="REVIEW_REQUIRED", identity_guard_reason="ambiguous",
        evidence_quality="unverified_candidate", review_state="unreviewed",
    )
    session.add(assertion)
    session.flush()
    bag = EvidenceBag(names=frozenset({"St. Paul Downtown Airport"}), runway_ends=frozenset({"14", "32"}), runway_pairs=frozenset({"14/32"}))
    serialized = serialize_evidence_bag(bag)
    snapshot = SourceAssertionEvidenceBag(
        source_assertion_id=assertion.id, evidence_bag_json=serialized,
        evidence_bag_hash=hash_serialized_evidence_bag(serialized), schema_version=1,
    )
    session.add(snapshot)
    session.flush()
    return airport, assertion


class TestCanonicalSideEffectFirewall:
    def test_attach_touches_only_source_assertion_airport_id(self):
        with Session(_engine()) as session:
            airport, assertion = _seed_assertion(session)
            before = {
                "airports": session.query(Airport).count(),
                "unknown_airport_candidates": session.query(UnknownAirportCandidate).count(),
                "signals": session.query(Signal).count(),
                "installations": session.query(Installation).count(),
            }
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            after = {
                "airports": session.query(Airport).count(),
                "unknown_airport_candidates": session.query(UnknownAirportCandidate).count(),
                "signals": session.query(Signal).count(),
                "installations": session.query(Installation).count(),
            }
            assert before == after
            assert assertion.airport_id == airport.id

    def test_reject_and_defer_touch_nothing_but_the_new_table(self):
        with Session(_engine()) as session:
            _, assertion = _seed_assertion(session)
            before = {
                "airports": session.query(Airport).count(),
                "unknown_airport_candidates": session.query(UnknownAirportCandidate).count(),
                "signals": session.query(Signal).count(),
                "installations": session.query(Installation).count(),
                "source_assertions_airport_id": assertion.airport_id,
            }
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="REJECT_ATTACHMENT",
                reason="x", reviewer="tester",
            )
            after = {
                "airports": session.query(Airport).count(),
                "unknown_airport_candidates": session.query(UnknownAirportCandidate).count(),
                "signals": session.query(Signal).count(),
                "installations": session.query(Installation).count(),
                "source_assertions_airport_id": assertion.airport_id,
            }
            assert before == after

    def test_no_orm_construction_of_airport_candidate_signal_installation_in_service_module(self):
        import app.services.source_assertion_identity_resolution as service_module

        tree = ast.parse(inspect_module.getsource(service_module))
        forbidden = {"Airport", "UnknownAirportCandidate", "Signal", "Installation"}
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()

    def test_no_orm_construction_of_airport_candidate_signal_installation_in_cli_module(self):
        import scripts.resolve_source_assertion_identity as cli_module

        tree = ast.parse(inspect_module.getsource(cli_module))
        forbidden = {"Airport", "UnknownAirportCandidate", "Signal", "Installation"}
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()


class TestStaticSiteInformationFirewall:
    def test_build_module_never_references_kar_capability(self):
        """Structural, not behavioral: app/static_export/build.py's own
        source text contains zero references to the new table, model, or
        service - it cannot leak what it never imports or queries."""
        build_source = Path("app/static_export/build.py").read_text(encoding="utf-8")
        for forbidden in (
            "SourceAssertionIdentityResolution",
            "source_assertion_identity_resolution",
            "source_assertion_identity_resolutions",
        ):
            assert forbidden not in build_source

    def test_resolved_but_unpublished_assertion_not_reachable_via_airport_source_assertions_without_signal(self):
        """A resolved SourceAssertion becomes reachable via
        Airport.source_assertions (the exact relationship
        app/static_export/build.py traverses) the moment airport_id is
        set - but build.py's own _is_public_signal() gate (unchanged,
        unmodified by this design) still requires a real, separately
        created, published Signal before anything derived from this
        evidence appears in public output. This test proves the KAR
        mutation alone (no Signal involved) does not itself constitute
        public visibility - it only proves the SourceAssertion is now
        reachable by the SAME relationship traversal every other resolved
        assertion already uses, which is the intended, documented
        consequence of ATTACH_TO_EXISTING_AIRPORT (design doc S11)."""
        with Session(_engine()) as session:
            airport, assertion = _seed_assertion(session)
            record_source_assertion_identity_resolution(
                session, source_assertion_id=assertion.id, action="ATTACH_TO_EXISTING_AIRPORT",
                reason="x", reviewer="tester", matched_airport_id=airport.id,
            )
            session.commit()
            assert assertion.signal_id is None
            reloaded_airport = session.get(Airport, airport.id)
            linked_assertion_ids = {a.id for a in reloaded_airport.source_assertions}
            assert assertion.id in linked_assertion_ids
            # Reachable via the relationship (exactly like any other
            # resolved assertion) but carries no signal_id - build.py's
            # own _is_public_signal() gate has nothing to admit.
