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
from app.services.human_review_claim_enrichment import enrich_claims
from app.services.human_review_queue import list_human_review_items
from app.services.intelligence_review_persistence import persist_intelligence_review
from app.services.promotion_policy_evaluation import PromotionPolicyContext, SourceAuthorityTier
from app.services.promotion_policy_persistence import persist_promotion_policy
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
