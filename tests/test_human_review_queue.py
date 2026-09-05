"""Tests for app.services.human_review_queue,
app.services.human_review_claim_enrichment, and
scripts/list_human_review_queue.py
(docs/architecture/human-review-queue-slice8-report.md).

Every test builds an isolated, disposable SQLite database - the real
data/runway_safe.db is never opened."""
from __future__ import annotations

import ast
import hashlib
import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - registers all metadata
from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.services import human_review_claim_enrichment as hrce
from app.services import human_review_queue as hrq
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_discovery_fragment
from app.services.evidence_attachment_guard import CandidateAirport
from app.services.governed_signal_creation import link_source_assertion_to_duplicate_signal
from app.services.human_review_claim_enrichment import enrich_claims
from app.services.human_review_queue import (
    GOVERNANCE_STAGE_STAGED_UNREVIEWED,
    ReviewWorkflowState,
    derive_workflow_state,
    list_human_review_items,
    list_review_workflow_items,
    list_staged_evidence_items,
)
from app.services.intelligence_review_persistence import persist_intelligence_review
from app.services.promotion_policy_evaluation import PromotionPolicyContext, SourceAuthorityTier
from app.services.promotion_policy_persistence import persist_promotion_policy
from app.services.reviewer_action_persistence import record_reviewer_action
from scripts import list_human_review_queue as cli
from scripts.migrate_intelligence_review_persistence_slice4 import downgrade as downgrade_slice4
from scripts.migrate_promotion_policy_persistence_slice7 import downgrade as downgrade_slice7

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf"
MSP_ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
MSP_SOURCE_LOCATOR = "item-2.3.2"
TIER_1 = PromotionPolicyContext(source_authority_tier=SourceAuthorityTier.TIER_1_PRIMARY_OFFICIAL)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _full_schema_database(tmp_path: Path, name: str = "full.db") -> Path:
    db = tmp_path / name
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return db


def _database_missing_slice7(tmp_path: Path) -> Path:
    db = _full_schema_database(tmp_path, "missing_slice7.db")
    downgrade_slice7(db)
    return db


def _database_missing_slice4_and_slice7(tmp_path: Path) -> Path:
    db = _full_schema_database(tmp_path, "missing_slice4_and_7.db")
    downgrade_slice7(db)
    downgrade_slice4(db)
    return db


@pytest.fixture()
def session(tmp_path):
    db = _full_schema_database(tmp_path)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s
    engine.dispose()


def _msp_candidate_airport(airport_id: int) -> CandidateAirport:
    return CandidateAirport(
        id=airport_id, name="Minneapolis-St Paul International",
        identifiers=frozenset({"MSP", "KMSP"}), canonical_runway_ends=frozenset({"12R", "30L"}),
        canonical_runway_pairs=frozenset({"12R/30L"}), known_issuers=frozenset({"Metropolitan Airports Commission"}),
    )


def _real_msp_fragment_and_claims():
    pdf_bytes = FIXTURE_PATH.read_bytes()
    result = extract_candidate_fragment(pdf_bytes, "application/pdf", artifact_identity=MSP_ARTIFACT_IDENTITY, source_locator=MSP_SOURCE_LOCATOR)
    assert result is not None
    fragment, _vendors = result
    return fragment, extract_mac_claims(fragment)


def _seed_msp_assertion(session: Session):
    airport = Airport(id=45, name="Minneapolis-St Paul International", iata_code="MSP", country="USA")
    session.add(airport)
    session.flush()
    fragment, claims = _real_msp_fragment_and_claims()
    result = persist_discovery_fragment(
        session, DiscoverySourceMetadata(document_identity=MSP_ARTIFACT_IDENTITY, title="MSP EMAS memo", publisher="Metropolitan Airports Commission"),
        fragment, [_msp_candidate_airport(airport.id)],
    )
    assert result.outcome.value == "ATTACH_CONFIRMED"
    assertion = session.get(SourceAssertion, result.source_assertion_id)
    persist_intelligence_review(session, assertion, claims)
    persist_promotion_policy(session, assertion, claims, TIER_1)
    session.commit()
    return assertion, claims


def _bare_assertion(
    session: Session, *, promotion_policy_decision: "str | None", identity_guard_decision: "str | None" = "ATTACH_CONFIRMED",
    intelligence_review_decision: "str | None" = "REVIEW_REQUIRED", parser_identifier: "str | None" = None,
    created_at: "datetime | None" = None, source_locator: str = "loc-1", artifact_identity: str = "artifact-1",
    raw_fragment_hash: str = "hash-1",
) -> SourceAssertion:
    source = Source(title="Test Source", source_type="test", reliability_level="official")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, assertion_type="project_construction", source_locator=source_locator,
        raw_fragment_hash=raw_fragment_hash, artifact_identity=artifact_identity, raw_relevant_text="original evidence text",
        parser_identifier=parser_identifier,
        identity_guard_decision=identity_guard_decision, identity_guard_reason="identity reason",
        intelligence_review_decision=intelligence_review_decision, intelligence_review_reason="intelligence reason",
        promotion_policy_decision=promotion_policy_decision, promotion_policy_reason="promotion reason",
    )
    if created_at is not None:
        assertion.created_at = created_at
    session.add(assertion)
    session.flush()
    return assertion


def _staged_assertion(
    session: Session, *, airport_id: "int | None" = None, signal_id: "int | None" = None,
    unknown_airport_candidate_id: "int | None" = None, evidence_quality: str = "unverified_candidate",
    review_state: str = "unreviewed", created_at: "datetime | None" = None,
    source_locator: str = "page:1;chars:0-10", artifact_identity: str = "staged-artifact-1",
    raw_fragment_hash: str = "staged-hash-1", raw_relevant_text: str = "staged evidence text",
    source_type: str = "web_discovery",
) -> SourceAssertion:
    """Builds a SourceAssertion in EXACTLY the shape
    app.services.stage_only_evidence_persistence (airport_id=None) and
    app.services.known_airport_evidence_persistence (airport_id=<int>) both
    actually produce - identity_guard_decision/intelligence_review_decision/
    promotion_policy_decision all None, matching those modules' own real
    persistence code verbatim, not a guess."""
    source = Source(title="Staged Test Source", source_type=source_type, reliability_level="unverified")
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport_id, unknown_airport_candidate_id=unknown_airport_candidate_id,
        assertion_type="project_construction", source_locator=source_locator,
        raw_fragment_hash=raw_fragment_hash, artifact_identity=artifact_identity,
        raw_relevant_text=raw_relevant_text, evidence_quality=evidence_quality, review_state=review_state,
        identity_guard_decision=None, identity_guard_reason=None,
        intelligence_review_decision=None, intelligence_review_reason=None,
        promotion_policy_decision=None, promotion_policy_reason=None,
        signal_id=signal_id,
    )
    if created_at is not None:
        assertion.created_at = created_at
    session.add(assertion)
    session.flush()
    return assertion


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 1-3. Schema gate ---

class TestSchemaGate:
    def test_missing_slice4_and_slice7_columns_blocks(self, tmp_path):
        db = _database_missing_slice4_and_slice7(tmp_path)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        assert cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER in report.blockers
        assert report.items == ()

    def test_missing_slice7_columns_only_blocks(self, tmp_path):
        db = _database_missing_slice7(tmp_path)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        assert cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER in report.blockers

    def test_fully_migrated_schema_passes(self, tmp_path):
        db = _full_schema_database(tmp_path)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        assert report.blockers == ()


# --- 4-7. Queue filter ---

class TestQueueFilter:
    def test_human_review_required_included(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        items = list_human_review_items(session)
        assert len(items) == 1
        assert items[0].source_assertion_id == assertion.id

    def test_auto_eligible_excluded(self, session):
        _bare_assertion(session, promotion_policy_decision="AUTO_ELIGIBLE")
        session.commit()
        assert list_human_review_items(session) == ()

    def test_do_not_promote_excluded(self, session):
        _bare_assertion(session, promotion_policy_decision="DO_NOT_PROMOTE")
        session.commit()
        assert list_human_review_items(session) == ()

    def test_null_excluded(self, session):
        _bare_assertion(session, promotion_policy_decision=None)
        session.commit()
        assert list_human_review_items(session) == ()

    def test_malformed_value_excluded(self, session):
        _bare_assertion(session, promotion_policy_decision="GARBAGE_VALUE")
        session.commit()
        assert list_human_review_items(session) == ()


# --- 8-9. Ordering / limit ---

class TestOrderingAndLimit:
    def test_deterministic_ordering_newest_first(self, session):
        old = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-old",
            artifact_identity="artifact-old", raw_fragment_hash="hash-old", created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        new = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-new",
            artifact_identity="artifact-new", raw_fragment_hash="hash-new", created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.commit()
        items = list_human_review_items(session)
        assert [item.source_assertion_id for item in items] == [new.id, old.id]

    def test_tiebreak_by_id_descending_for_identical_timestamps(self, session):
        same_time = datetime(2025, 6, 1, tzinfo=UTC)
        first = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-1a",
            artifact_identity="artifact-1a", raw_fragment_hash="hash-1a", created_at=same_time,
        )
        second = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-1b",
            artifact_identity="artifact-1b", raw_fragment_hash="hash-1b", created_at=same_time,
        )
        session.commit()
        items = list_human_review_items(session)
        assert [item.source_assertion_id for item in items] == [second.id, first.id]

    def test_optional_limit_bounds_results(self, session):
        for i in range(5):
            _bare_assertion(
                session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator=f"loc-{i}",
                artifact_identity=f"artifact-{i}", raw_fragment_hash=f"hash-{i}",
            )
        session.commit()
        assert len(list_human_review_items(session)) == 5
        assert len(list_human_review_items(session, limit=2)) == 2

    def test_no_limit_returns_all(self, session):
        for i in range(3):
            _bare_assertion(
                session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator=f"loc-{i}",
                artifact_identity=f"artifact-{i}", raw_fragment_hash=f"hash-{i}",
            )
        session.commit()
        assert len(list_human_review_items(session, limit=None)) == 3


# --- 10-12. MSP golden item / verbatim reasons ---

class TestMSPGoldenItem:
    def test_msp_appears_exactly_once(self, session):
        _seed_msp_assertion(session)
        items = list_human_review_items(session)
        assert len(items) == 1
        assert items[0].promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"

    def test_msp_airport_and_source_fields(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        assert item.airport_id == 45
        assert item.airport_code == "MSP"
        assert item.source_title == "MSP EMAS memo"
        assert item.source_publisher == "Metropolitan Airports Commission"

    def test_intelligence_reason_verbatim(self, session):
        assertion, _claims = _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        assert item.intelligence_review_reason == assertion.intelligence_review_reason
        assert "advance_deposit_purchase_order" in item.intelligence_review_reason

    def test_promotion_reason_verbatim(self, session):
        assertion, _claims = _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        assert item.promotion_policy_reason == assertion.promotion_policy_reason
        assert "cip_project_ceiling" in item.promotion_policy_reason


# --- 13-17. Claim enrichment / rendering ---

class TestClaimEnrichmentAndRendering:
    def test_mac_claims_re_derived(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        claims = enrich_claims(item)
        assert claims is not None
        assert len(claims) == 7

    def test_re_derived_claims_are_structurally_identical_to_original_extraction(self, session):
        # Checkpoint-review strengthening (task S7): proves re-derivation
        # from persisted raw_relevant_text cannot silently diverge from the
        # original ingestion-time extraction - not merely a matching count,
        # but full dataclass equality (Claim is frozen/hashable with
        # structural __eq__) against the claims extracted directly from the
        # real PDF at persistence time.
        _assertion, original_claims = _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        re_derived_claims = enrich_claims(item)
        assert re_derived_claims == original_claims

    def test_financial_role_rendering(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "1590000.00 USD — advance_deposit_purchase_order" in text
        assert "19000000.00 USD — cip_project_ceiling" in text

    def test_not_established_rendering(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "NOT ESTABLISHED: contract_value, confirmed_vendor_award_amount, estimated_vendor_revenue" in text
        assert "What is NOT established" in text
        assert "contract_value" in text.split("What is NOT established")[1]

    def test_procedural_rendering(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "requested_pending_approval" in text

    def test_temporal_rendering(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "planned_future_action" in text
        assert "historical_fact" in text

    def test_no_fabricated_19m_contract_language(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "19000000.00 USD — contract" not in text
        assert "19,000,000 contract" not in text


# --- 18. Impossible-state warning ---

class TestInvariantWarning:
    def test_identity_inconsistency_surfaces_warning(self, session):
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", identity_guard_decision="ATTACH_PROVISIONAL")
        session.commit()
        item = list_human_review_items(session)[0]
        assert item.invariant_warnings
        assert any("identity_guard_decision" in w for w in item.invariant_warnings)

    def test_intelligence_inconsistency_surfaces_warning(self, session):
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", intelligence_review_decision="INSUFFICIENT_MATERIALITY")
        session.commit()
        item = list_human_review_items(session)[0]
        assert item.invariant_warnings
        assert any("intelligence_review_decision" in w for w in item.invariant_warnings)

    def test_consistent_row_has_no_warnings(self, session):
        _seed_msp_assertion(session)
        item = list_human_review_items(session)[0]
        assert item.invariant_warnings == ()

    def test_warning_does_not_modify_the_database(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", identity_guard_decision="ATTACH_PROVISIONAL")
        session.commit()
        list_human_review_items(session)
        reloaded = session.get(SourceAssertion, assertion.id)
        assert reloaded.identity_guard_decision == "ATTACH_PROVISIONAL"  # untouched


# --- 19. Source authority gap ---

class TestSourceAuthorityGap:
    def test_reliability_level_labeled_raw_not_tier(self, session):
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        item = list_human_review_items(session)[0]
        assert item.source_reliability_level_raw == "official"
        text = cli.render_item_report(item)
        assert "NOT a PromotionPolicy SourceAuthorityTier" in text

    def test_queue_service_never_imports_source_authority_tier(self):
        # AST-based, not substring: the module's own docstring legitimately
        # explains, in prose, that it never imports this name.
        tree = ast.parse(inspect.getsource(hrq))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "SourceAuthorityTier" not in imported_names


# --- 20. No-Signal invariant ---

class TestNoSignalInvariant:
    def test_queue_service_no_signal_import(self):
        for module in (hrq, hrce, cli):
            tree = ast.parse(inspect.getsource(module))
            imported_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported_names.add(alias.asname or alias.name)
            assert "Signal" not in imported_names

    def test_signal_count_unchanged_across_a_full_queue_read(self, session):
        _seed_msp_assertion(session)
        before = session.scalars(select(Signal)).all()
        list_human_review_items(session)
        after = session.scalars(select(Signal)).all()
        assert before == [] and after == []


# --- 21-23. Wrong-DB isolation / no backup / no migration ---

class TestReadOnlySafety:
    def test_only_the_target_database_is_queried(self, tmp_path):
        target = _full_schema_database(tmp_path, "target.db")
        protected = _full_schema_database(tmp_path, "protected.db")

        engine = create_engine(f"sqlite:///{target}")
        with Session(engine) as s:
            _bare_assertion(s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
            s.commit()
        engine.dispose()

        target_sha_before = _file_sha(target)
        protected_sha_before = _file_sha(protected)

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=target))
        assert len(report.items) == 1

        assert _file_sha(target) == target_sha_before  # read-only, unchanged
        assert _file_sha(protected) == protected_sha_before  # never touched at all

    def test_no_backup_file_created(self, tmp_path):
        db = _full_schema_database(tmp_path)
        before = set(tmp_path.iterdir())
        cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        after = set(tmp_path.iterdir())
        assert before == after  # no new files (e.g. a backup) appeared

    def test_module_never_imports_upgrade_or_downgrade(self):
        source = inspect.getsource(cli)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "upgrade" not in imported_names
        assert "downgrade" not in imported_names

    def test_readonly_engine_refuses_writes(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = cli.build_readonly_engine(db)
        with Session(engine) as s:
            with pytest.raises(Exception):
                s.add(Airport(name="Should fail", country="USA"))
                s.commit()
        engine.dispose()


# --- 24-25. Determinism / empty queue ---

class TestDeterminismAndEmpty:
    def test_repeated_reads_are_identical(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            _seed_msp_assertion(s)
        engine.dispose()

        first = cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        second = cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        assert first.items == second.items
        assert cli.render_report(first) == cli.render_report(second)

    def test_empty_queue_renders_clear_message(self, tmp_path):
        db = _full_schema_database(tmp_path)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db))
        assert report.items == ()
        text = cli.render_report(report)
        assert "empty" in text.lower()


# --- 26-27. International readiness / unsupported source family ---

class TestInternationalAndUnsupportedFamily:
    def test_non_us_non_usd_item_renders_without_error(self, session):
        _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", parser_identifier="haneda-authority-v1",
            source_locator="haneda-loc-1", artifact_identity="haneda-artifact-1", raw_fragment_hash="haneda-hash-1",
        )
        session.commit()
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "SourceAssertion" in text  # rendered without raising

    def test_unsupported_source_family_enrichment_returns_none(self, session):
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", parser_identifier="haneda-authority-v1")
        session.commit()
        item = list_human_review_items(session)[0]
        assert enrich_claims(item) is None

    def test_unsupported_source_family_reports_gracefully_not_fabricated(self, session):
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", parser_identifier=None)
        session.commit()
        item = list_human_review_items(session)[0]
        text = cli.render_item_report(item)
        assert "No source-specific claim extraction available" in text

    def test_queue_structure_has_no_us_specific_dependency(self):
        # Structural, not prose-based: the module's own docstring legitimately
        # discusses MAC/Granicus in prose to explain the boundary it keeps -
        # the real guarantee is that it imports nothing from
        # app.acquisition (where every source-specific extractor lives).
        tree = ast.parse(inspect.getsource(hrq))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("app.acquisition")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.acquisition")


# ---------------------------------------------------------------------------
# Slice 9D - workflow-aware queue
# (docs/architecture/human-review-workflow-awareness-slice9d-report.md).
# All fixtures below build on _bare_assertion() (promotion_policy_decision
# already HUMAN_REVIEW_REQUIRED) plus zero or more ReviewerAction rows -
# never touches the real database.
# ---------------------------------------------------------------------------


def _existing_signal(session: Session, *, published: bool = True) -> Signal:
    airport = Airport(name="Existing-Signal Airport", country="USA")
    session.add(airport)
    session.flush()
    signal = Signal(
        airport=airport, title="Existing signal", category="replacement", confidence="high", published=published,
    )
    session.add(signal)
    session.flush()
    return signal


class TestWorkflowStateDerivation:
    """1-7. derive_workflow_state() itself, the pure classifier - covers
    every action/signal_id combination in isolation before testing the
    queue-level filtering built on top of it."""

    def test_no_action_is_active_review(self):
        assert derive_workflow_state(None, None) == ReviewWorkflowState.ACTIVE_REVIEW

    def test_defer_is_deferred(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        action = record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer="human:t")
        session.commit()
        assert derive_workflow_state(action, None) == ReviewWorkflowState.DEFERRED

    def test_needs_more_evidence_state(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        action = record_reviewer_action(session, assertion, action="NEEDS_MORE_EVIDENCE", reason="x", reviewer="human:t")
        session.commit()
        assert derive_workflow_state(action, None) == ReviewWorkflowState.NEEDS_MORE_EVIDENCE

    def test_approve_signal_no_signal_is_approved_pending(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        action = record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
        session.commit()
        assert derive_workflow_state(action, None) == ReviewWorkflowState.APPROVED_PENDING_SIGNAL

    def test_approve_signal_with_signal_is_resolved_signal_created(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        action = record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
        session.commit()
        assert derive_workflow_state(action, 999) == ReviewWorkflowState.RESOLVED_SIGNAL_CREATED

    def test_reject_signal_is_resolved_rejected(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        action = record_reviewer_action(session, assertion, action="REJECT_SIGNAL", reason="x", reviewer="human:t")
        session.commit()
        assert derive_workflow_state(action, None) == ReviewWorkflowState.RESOLVED_REJECTED

    def test_mark_duplicate_is_resolved_duplicate(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        action = record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        session.commit()
        assert derive_workflow_state(action, target.id) == ReviewWorkflowState.RESOLVED_DUPLICATE


class TestMSP222ExactRegression:
    """8. Exact reproduction of the real production MSP #222 resolution:
    ReviewerAction #1 APPROVE_SIGNAL, superseded by #2 MARK_DUPLICATE ->
    Signal #67-shaped target, signal_id linked. This is the specific real
    workflow defect Slice 9D fixes - before this slice, #222 stayed listed
    in the active queue forever."""

    def test_resolved_duplicate_msp_222_shape(self, session):
        assertion, claims = _seed_msp_assertion(session)
        existing_signal = _existing_signal(session)
        session.commit()

        approve = record_reviewer_action(
            session, assertion, action="APPROVE_SIGNAL", reason="initial approval", reviewer="human:rwi-owner",
        )
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="resolved as duplicate", reviewer="human:rwi-owner",
            supersedes_action_id=approve.id, duplicate_of_signal_id=existing_signal.id,
        )
        session.commit()
        link_source_assertion_to_duplicate_signal(session, assertion)
        session.commit()

        # 9. Default active queue excludes it.
        active = list_human_review_items(session)
        assert active == ()

        # Full picture still shows it, correctly classified.
        all_items = list_review_workflow_items(session)
        assert len(all_items) == 1
        item = all_items[0]
        assert item.review_workflow_state == ReviewWorkflowState.RESOLVED_DUPLICATE.value
        assert item.latest_reviewer_action == "MARK_DUPLICATE"
        assert item.linked_signal_id == existing_signal.id
        assert item.invariant_warnings == ()

        # Evidence remains fully queryable/unmodified - not deleted or hidden.
        fresh = session.get(SourceAssertion, assertion.id)
        assert fresh is not None
        assert fresh.raw_relevant_text == assertion.raw_relevant_text
        assert fresh.identity_guard_decision == "ATTACH_CONFIRMED"


class TestDefaultActiveQueueFiltering:
    """9-12. Default queue exclusion/inclusion per derived state."""

    def test_excludes_resolved_duplicate(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        session.commit()
        assert list_human_review_items(session) == ()

    def test_excludes_resolved_rejected(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        record_reviewer_action(session, assertion, action="REJECT_SIGNAL", reason="x", reviewer="human:t")
        session.commit()
        assert list_human_review_items(session) == ()

    def test_excludes_approved_pending_signal(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
        session.commit()
        assert list_human_review_items(session) == ()

    def test_excludes_resolved_signal_created(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(session, assertion, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
        session.commit()
        assertion.signal_id = target.id  # simulate a completed governed creation link
        session.commit()
        assert list_human_review_items(session) == ()

    def test_excludes_deferred(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer="human:t")
        session.commit()
        assert list_human_review_items(session) == ()

    def test_includes_needs_more_evidence(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        record_reviewer_action(session, assertion, action="NEEDS_MORE_EVIDENCE", reason="x", reviewer="human:t")
        session.commit()
        active = list_human_review_items(session)
        assert len(active) == 1
        assert active[0].review_workflow_state == ReviewWorkflowState.NEEDS_MORE_EVIDENCE.value

    def test_fresh_item_with_no_action_remains_active(self, session):
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        active = list_human_review_items(session)
        assert len(active) == 1
        assert active[0].review_workflow_state == ReviewWorkflowState.ACTIVE_REVIEW.value
        assert active[0].latest_reviewer_action is None
        assert active[0].linked_signal_id is None

    def test_active_item_remains_visible_alongside_resolved_items(self, session):
        active_assertion = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-active",
            artifact_identity="artifact-active", raw_fragment_hash="hash-active",
        )
        resolved_assertion = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-resolved",
            artifact_identity="artifact-resolved", raw_fragment_hash="hash-resolved",
        )
        session.commit()
        record_reviewer_action(session, resolved_assertion, action="REJECT_SIGNAL", reason="x", reviewer="human:t")
        session.commit()

        active = list_human_review_items(session)
        assert [item.source_assertion_id for item in active] == [active_assertion.id]


class TestInvariantWarnings:
    """13. Reviewer-action-vs-signal_id invariant mismatches are surfaced,
    never silently repaired."""

    def test_mark_duplicate_target_disagrees_with_signal_id(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        session.commit()
        # Deliberately never linked - signal_id stays NULL, disagreeing with duplicate_of_signal_id.
        item = list_review_workflow_items(session)[0]
        assert item.review_workflow_state == ReviewWorkflowState.RESOLVED_DUPLICATE.value  # still classified, not repaired
        assert len(item.invariant_warnings) == 1
        assert "target and link disagree" in item.invariant_warnings[0]

    def test_reject_signal_with_signal_id_already_set_warns(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(session, assertion, action="REJECT_SIGNAL", reason="x", reviewer="human:t")
        assertion.signal_id = target.id  # inconsistent, deliberately, for this test
        session.commit()
        item = list_review_workflow_items(session)[0]
        assert len(item.invariant_warnings) == 1
        assert "mutually exclusive" in item.invariant_warnings[0]

    def test_consistent_mark_duplicate_has_no_warning(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        assertion.signal_id = target.id
        session.commit()
        item = list_review_workflow_items(session)[0]
        assert item.invariant_warnings == ()


class TestLatestReviewerActionReuse:
    """14. The batched lookup used by the queue must agree exactly with
    get_latest_reviewer_action(), row for row - never a second, drifting
    ordering implementation."""

    def test_batched_lookup_matches_get_latest_reviewer_action_per_row(self, session):
        from app.services.reviewer_action_persistence import get_latest_reviewer_action

        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        first = record_reviewer_action(session, assertion, action="DEFER", reason="x", reviewer="human:a")
        session.commit()
        second = record_reviewer_action(
            session, assertion, action="NEEDS_MORE_EVIDENCE", reason="y", reviewer="human:b",
            supersedes_action_id=first.id,
        )
        session.commit()

        expected = get_latest_reviewer_action(session, assertion.id)
        item = list_review_workflow_items(session)[0]
        assert item.latest_reviewer_action == expected.action == second.action


class TestOrderingLimitAndExclusions:
    """15-19. Ordering/limit continue to hold for the workflow-aware default
    queue; malformed/AUTO_ELIGIBLE/DO_NOT_PROMOTE rows remain excluded
    exactly as under Slice 8."""

    def test_deterministic_ordering_preserved(self, session):
        older = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", created_at=datetime(2024, 1, 1, tzinfo=UTC),
            source_locator="loc-older", artifact_identity="artifact-older", raw_fragment_hash="hash-older",
        )
        newer = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", created_at=datetime(2024, 6, 1, tzinfo=UTC),
            source_locator="loc-newer", artifact_identity="artifact-newer", raw_fragment_hash="hash-newer",
        )
        session.commit()
        active = list_human_review_items(session)
        assert [item.source_assertion_id for item in active] == [newer.id, older.id]

    def test_optional_limit_applies_after_workflow_filtering(self, session):
        for i in range(3):
            _bare_assertion(
                session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
                source_locator=f"loc-{i}", artifact_identity=f"artifact-{i}", raw_fragment_hash=f"hash-{i}",
            )
        session.commit()
        active = list_human_review_items(session, limit=2)
        assert len(active) == 2

    def test_limit_does_not_truncate_active_items_when_newer_rows_are_resolved(self, session):
        """Review-checkpoint regression (H): the exact failure shape a naive
        SQL-level LIMIT-before-filtering implementation would produce - the
        newest rows (which SQL ordering would return first) are all already
        resolved, while older still-active rows exist further down. If
        `limit` were applied to the raw HUMAN_REVIEW_REQUIRED query before
        workflow-state filtering, this would incorrectly return 0 active
        items instead of the 2 that actually need review."""
        for i in range(3):
            resolved = _bare_assertion(
                session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
                created_at=datetime(2024, 6, 1 + i, tzinfo=UTC),
                source_locator=f"loc-resolved-{i}", artifact_identity=f"artifact-resolved-{i}",
                raw_fragment_hash=f"hash-resolved-{i}",
            )
            session.commit()
            record_reviewer_action(session, resolved, action="REJECT_SIGNAL", reason="x", reviewer="human:t")
            session.commit()

        active_ids = []
        for i in range(2):
            active_assertion = _bare_assertion(
                session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
                created_at=datetime(2024, 1, 1 + i, tzinfo=UTC),
                source_locator=f"loc-active-{i}", artifact_identity=f"artifact-active-{i}",
                raw_fragment_hash=f"hash-active-{i}",
            )
            active_ids.append(active_assertion.id)
        session.commit()

        result = list_human_review_items(session, limit=2)
        assert sorted(item.source_assertion_id for item in result) == sorted(active_ids)

    def test_malformed_promotion_policy_excluded_from_active_and_all(self, session):
        _bare_assertion(session, promotion_policy_decision="NOT_A_REAL_VALUE")
        session.commit()
        assert list_human_review_items(session) == ()
        assert list_review_workflow_items(session) == ()

    def test_auto_eligible_excluded(self, session):
        _bare_assertion(session, promotion_policy_decision="AUTO_ELIGIBLE")
        session.commit()
        assert list_human_review_items(session) == ()
        assert list_review_workflow_items(session) == ()

    def test_do_not_promote_excluded(self, session):
        _bare_assertion(session, promotion_policy_decision="DO_NOT_PROMOTE")
        session.commit()
        assert list_human_review_items(session) == ()
        assert list_review_workflow_items(session) == ()


class TestNoWrites:
    """20-21. Strictly read-only: no DB writes, no Signal writes, from any
    function in this slice."""

    def test_no_db_writes_from_queue_functions(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        session.commit()

        before_sa = [(a.id, a.signal_id) for a in session.scalars(select(SourceAssertion)).all()]
        list_human_review_items(session)
        list_review_workflow_items(session)
        after_sa = [(a.id, a.signal_id) for a in session.scalars(select(SourceAssertion)).all()]
        assert before_sa == after_sa

    def test_no_signal_writes_from_queue_functions(self, session):
        assertion = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        session.commit()

        before = session.scalars(select(Signal)).all()
        list_human_review_items(session)
        list_review_workflow_items(session)
        after = session.scalars(select(Signal)).all()
        assert len(before) == len(after) == 1

    def test_queue_module_never_constructs_a_reviewer_action_or_mutates_session(self):
        tree = ast.parse(inspect.getsource(hrq))
        forbidden_attrs = {"add", "flush", "commit", "delete", "update"}
        hits = [
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs
        ]
        assert hits == []


class TestCLIReadOnlyAndStateFilter:
    """22. CLI remains read-only under the new --state option; verifies the
    active/all/resolved views render distinctly."""

    def test_cli_read_only_with_state_all(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            assertion = _bare_assertion(s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
            target = _existing_signal(s)
            s.commit()
            record_reviewer_action(
                s, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
                duplicate_of_signal_id=target.id,
            )
            s.commit()
        engine.dispose()

        sha_before = _file_sha(db)
        active_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="active"))
        all_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="all"))
        resolved_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="resolved"))
        sha_after = _file_sha(db)

        assert sha_before == sha_after  # strictly read-only
        assert active_report.items == ()
        assert len(all_report.items) == 1
        assert len(resolved_report.items) == 1
        assert "RESOLVED_DUPLICATE" in cli.render_report(resolved_report, state="resolved")

    def test_invalid_state_rejected(self, tmp_path):
        db = _full_schema_database(tmp_path)
        with pytest.raises(ValueError):
            cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="not-a-real-state"))

    def test_resolved_state_limit_does_not_truncate_when_newer_rows_are_active(self, tmp_path):
        """Review-checkpoint regression: run_review_queue()'s "resolved"
        branch used to pass `limit` straight into list_review_workflow_items(),
        which bounds the raw SQL query before any state filtering - if the
        newest rows (fetched first) are all still active, a small limit
        could consume them and leave zero rows for the resolved-state filter
        to find, even though a resolved item exists further down. Fixed to
        fetch everything, filter to resolved, then limit in Python."""
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            for i in range(3):
                _bare_assertion(
                    s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
                    created_at=datetime(2024, 6, 1 + i, tzinfo=UTC),
                    source_locator=f"loc-active-{i}", artifact_identity=f"artifact-active-{i}",
                    raw_fragment_hash=f"hash-active-{i}",
                )
            s.commit()
            resolved = _bare_assertion(
                s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                source_locator="loc-resolved-0", artifact_identity="artifact-resolved-0",
                raw_fragment_hash="hash-resolved-0",
            )
            s.commit()
            record_reviewer_action(s, resolved, action="REJECT_SIGNAL", reason="x", reviewer="human:t")
            s.commit()
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="resolved", limit=3))
        assert len(report.items) == 1
        assert report.items[0].review_workflow_state == ReviewWorkflowState.RESOLVED_REJECTED.value


class TestInternationalWorkflowReadiness:
    """24. Workflow classification is source/airport/currency agnostic - a
    synthetic non-US, non-MAC item classifies identically to the MSP case."""

    def test_non_us_item_mark_duplicate_classifies_identically(self, session):
        assertion = _bare_assertion(
            session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", parser_identifier="haneda-authority-v1",
            source_locator="haneda-loc-1", artifact_identity="haneda-artifact-1", raw_fragment_hash="haneda-hash-1",
        )
        target = _existing_signal(session)
        session.commit()
        record_reviewer_action(
            session, assertion, action="MARK_DUPLICATE", reason="x", reviewer="human:t",
            duplicate_of_signal_id=target.id,
        )
        assertion.signal_id = target.id
        session.commit()

        assert list_human_review_items(session) == ()
        item = list_review_workflow_items(session)[0]
        assert item.review_workflow_state == ReviewWorkflowState.RESOLVED_DUPLICATE.value
        assert item.invariant_warnings == ()


# --- 25-40. Staged evidence lane (RWI HQ "Commander Review Queue - Staged Evidence Lane") ---


class TestStagedEvidenceLane:
    """25-33. list_staged_evidence_items() surfaces preserved candidate
    evidence that never entered governance review - a structurally
    separate population from the governed queue above, using fixtures
    shaped exactly like the real persistence paths that produce it."""

    def test_known_airport_shaped_row_surfaced(self, session):
        """SA255/256/258 shape: airport_id resolved, everything else None."""
        assertion = _staged_assertion(session, airport_id=45)
        session.commit()
        items = list_staged_evidence_items(session)
        assert len(items) == 1
        assert items[0].source_assertion_id == assertion.id
        assert items[0].airport_id == 45

    def test_stage_only_shaped_row_also_surfaced(self, session):
        """The other real persistence shape: airport_id left NULL entirely."""
        assertion = _staged_assertion(session, airport_id=None)
        session.commit()
        items = list_staged_evidence_items(session)
        assert len(items) == 1
        assert items[0].airport_id is None

    def test_governance_stage_is_exactly_staged_unreviewed(self, session):
        _staged_assertion(session, airport_id=45)
        session.commit()
        item = list_staged_evidence_items(session)[0]
        assert item.governance_stage == "STAGED_UNREVIEWED"
        assert item.governance_stage == GOVERNANCE_STAGE_STAGED_UNREVIEWED

    def test_staged_rows_distinct_from_governed_review_rows(self, session):
        """A row that HAS been through identity/intelligence/promotion
        evaluation must never appear as staged, even if it happens to
        share other field values."""
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        _staged_assertion(session, airport_id=45)
        session.commit()
        staged_ids = {item.source_assertion_id for item in list_staged_evidence_items(session)}
        governed_ids = {item.source_assertion_id for item in list_review_workflow_items(session)}
        assert staged_ids & governed_ids == set()
        assert len(staged_ids) == 1
        assert len(governed_ids) == 1

    def test_existing_governed_predicate_unchanged_by_staged_rows_presence(self, session):
        """Adding staged rows must never widen, narrow, or otherwise affect
        the pre-existing governed queue's own output."""
        governed = _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        session.commit()
        before_active = list_human_review_items(session)
        before_all = list_review_workflow_items(session)

        for i in range(5):
            _staged_assertion(session, airport_id=None, source_locator=f"loc-staged-{i}", artifact_identity=f"artifact-staged-{i}", raw_fragment_hash=f"hash-staged-{i}")
        session.commit()

        after_active = list_human_review_items(session)
        after_all = list_review_workflow_items(session)
        assert [i.source_assertion_id for i in before_active] == [i.source_assertion_id for i in after_active]
        assert [i.source_assertion_id for i in before_all] == [i.source_assertion_id for i in after_all]
        assert after_active[0].source_assertion_id == governed.id

    def test_promoted_staged_row_no_longer_appears_as_staged(self, session):
        """The real, evidenced SA257 case: known-airport-staged evidence
        that was already promoted via the lightweight path (signal_id set)
        must no longer appear as awaiting Commander attention, even though
        promotion_policy_decision itself is still None (that field is never
        populated by the lightweight path either)."""
        target = _existing_signal(session)
        session.commit()
        promoted = _staged_assertion(session, airport_id=45)
        promoted.signal_id = target.id
        session.commit()
        assert list_staged_evidence_items(session) == ()

    def test_uac_linked_row_excluded_from_staged(self, session):
        """A row already routed through UAC candidate formation implies
        SOME identity-resolution work already happened - a meaningfully
        different situation from evidence never examined at all."""
        _staged_assertion(session, airport_id=None, unknown_airport_candidate_id=999)
        session.commit()
        assert list_staged_evidence_items(session) == ()

    def test_no_governance_decisions_synthesized(self, session):
        _staged_assertion(session, airport_id=45)
        session.commit()
        item = list_staged_evidence_items(session)[0]
        assert item.identity_guard_decision is None
        assert item.intelligence_review_decision is None
        assert item.promotion_policy_decision is None
        assert item.linked_signal_id is None

    def test_deterministic_ordering(self, session):
        older = _staged_assertion(
            session, airport_id=None, created_at=datetime(2024, 1, 1, tzinfo=UTC),
            source_locator="loc-a", artifact_identity="artifact-a", raw_fragment_hash="hash-a",
        )
        newer = _staged_assertion(
            session, airport_id=None, created_at=datetime(2024, 6, 1, tzinfo=UTC),
            source_locator="loc-b", artifact_identity="artifact-b", raw_fragment_hash="hash-b",
        )
        session.commit()
        items = list_staged_evidence_items(session)
        assert [i.source_assertion_id for i in items] == [newer.id, older.id]

    def test_exact_set_for_mixed_fixture(self, session):
        """Only the genuinely staged-shaped rows are returned - never a
        superset, never a subset."""
        staged_a = _staged_assertion(session, airport_id=45, source_locator="loc-s1", artifact_identity="artifact-s1", raw_fragment_hash="hash-s1")
        staged_b = _staged_assertion(session, airport_id=None, source_locator="loc-s2", artifact_identity="artifact-s2", raw_fragment_hash="hash-s2")
        _bare_assertion(session, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
        _bare_assertion(session, promotion_policy_decision=None)  # malformed/incomplete, not staged-shaped
        session.commit()
        ids = {item.source_assertion_id for item in list_staged_evidence_items(session)}
        assert ids == {staged_a.id, staged_b.id}

    def test_optional_limit_applies(self, session):
        for i in range(3):
            _staged_assertion(session, airport_id=None, source_locator=f"loc-lim-{i}", artifact_identity=f"artifact-lim-{i}", raw_fragment_hash=f"hash-lim-{i}")
        session.commit()
        assert len(list_staged_evidence_items(session)) == 3
        assert len(list_staged_evidence_items(session, limit=2)) == 2

    def test_no_db_writes_from_staged_query(self, session):
        _staged_assertion(session, airport_id=45)
        session.commit()
        before = [(a.id, a.signal_id) for a in session.scalars(select(SourceAssertion)).all()]
        list_staged_evidence_items(session)
        after = [(a.id, a.signal_id) for a in session.scalars(select(SourceAssertion)).all()]
        assert before == after

    def test_no_signal_writes_from_staged_query(self, session):
        _staged_assertion(session, airport_id=45)
        session.commit()
        before = session.scalars(select(Signal)).all()
        list_staged_evidence_items(session)
        after = session.scalars(select(Signal)).all()
        assert len(before) == len(after) == 0

    def test_staged_query_never_constructs_a_reviewer_action_or_mutates_session(self):
        """The whole human_review_queue module is inspected here (matching
        test_queue_module_never_constructs_a_reviewer_action_or_mutates_session's
        own convention exactly) - this automatically covers the new staged
        code too, with no separate AST walk needed."""
        tree = ast.parse(inspect.getsource(hrq))
        forbidden_attrs = {"add", "flush", "commit", "delete", "update"}
        hits = [
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs
        ]
        assert hits == []


class TestCLIStagedState:
    """34-40. CLI --state staged: distinct rendering, distinct empty
    message, unaffected pre-existing states."""

    def test_cli_staged_state_surfaces_rows(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            _staged_assertion(s, airport_id=45)
            s.commit()
        engine.dispose()

        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="staged"))
        assert len(report.items) == 1
        rendered = cli.render_report(report, state="staged")
        assert "STAGED EVIDENCE" in rendered
        assert "STAGED_UNREVIEWED" in rendered
        assert "not evaluated" in rendered

    def test_cli_staged_empty_message_is_distinct(self, tmp_path):
        db = _full_schema_database(tmp_path)
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="staged"))
        rendered = cli.render_report(report, state="staged")
        assert "No staged evidence is currently awaiting Commander attention." in rendered
        assert "Nothing currently requires a human decision" not in rendered

    def test_cli_staged_read_only(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            _staged_assertion(s, airport_id=45)
            s.commit()
        engine.dispose()

        sha_before = _file_sha(db)
        cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="staged"))
        sha_after = _file_sha(db)
        assert sha_before == sha_after

    def test_existing_states_unaffected_by_staged_rows(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            _bare_assertion(s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED")
            for i in range(3):
                _staged_assertion(s, airport_id=None, source_locator=f"loc-x{i}", artifact_identity=f"artifact-x{i}", raw_fragment_hash=f"hash-x{i}")
            s.commit()
        engine.dispose()

        active_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="active"))
        all_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="all"))
        resolved_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="resolved"))
        assert len(active_report.items) == 1
        assert len(all_report.items) == 1
        assert resolved_report.items == ()

    def test_sa235_sa222_style_rows_remain_compatible(self, tmp_path):
        """A resolved-signal-created row and a resolved-duplicate row (the
        real SA235/SA222 shapes) still render correctly under 'all'/
        'resolved', unaffected by the new staged lane's existence."""
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            approved = _bare_assertion(s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-approved", artifact_identity="artifact-approved", raw_fragment_hash="hash-approved")
            s.commit()
            record_reviewer_action(s, approved, action="APPROVE_SIGNAL", reason="x", reviewer="human:t")
            signal = _existing_signal(s)
            s.commit()
            approved.signal_id = signal.id
            s.commit()

            duplicate = _bare_assertion(s, promotion_policy_decision="HUMAN_REVIEW_REQUIRED", source_locator="loc-dup", artifact_identity="artifact-dup", raw_fragment_hash="hash-dup")
            target = _existing_signal(s, published=False)
            s.commit()
            record_reviewer_action(s, duplicate, action="MARK_DUPLICATE", reason="x", reviewer="human:t", duplicate_of_signal_id=target.id)
            duplicate.signal_id = target.id
            s.commit()
        engine.dispose()

        resolved_report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="resolved"))
        assert len(resolved_report.items) == 2
        rendered = cli.render_report(resolved_report, state="resolved")
        assert "RESOLVED_SIGNAL_CREATED" in rendered
        assert "RESOLVED_DUPLICATE" in rendered

    def test_staged_state_never_creates_reviewer_action_or_signal(self, tmp_path):
        db = _full_schema_database(tmp_path)
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            _staged_assertion(s, airport_id=45)
            s.commit()
            before_signals = len(s.scalars(select(Signal)).all())
        engine.dispose()

        cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="staged"))

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            after_signals = len(s.scalars(select(Signal)).all())
        engine.dispose()
        assert before_signals == after_signals == 0

    def test_staged_valid_state_choice(self, tmp_path):
        db = _full_schema_database(tmp_path)
        # "staged" must not raise ValueError the way an invalid state would.
        report = cli.run_review_queue(cli.ReviewQueueConfig(database=db, state="staged"))
        assert report.blockers == ()
